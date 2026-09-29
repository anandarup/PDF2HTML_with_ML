"""
Application logging + infra snapshots.

Why this exists
----------------
Before this module, every part of the app (convert.py, conversion_job.py,
s3_publish.py, app.py) wrote plain `print(...)` lines. Those only ever reached
stdout, which systemd/journald captured with no time-based retention -- so a
failure from a few days ago could already be gone by the time someone went
looking for it, and there was no way to search "everything that happened for
job_id X" without hand-grepping.

This module gives every part of the app:

1. A single, shared `logging.Logger` ("pdf2webview") that writes to both
   stdout (so `journalctl -u pdf2html` still works exactly as before) AND a
   rotating file under LOG_DIR/app.log, retained for LOG_RETENTION_DAYS.
2. A `job_context(job_id=..., ref_id=..., stage=...)` context manager that
   attaches those fields to every log line emitted inside it, so a single job
   can be traced end-to-end with `grep '"job_id": "abc123"' app.log`.
3. `capture_infra_snapshot(reason, **fields)` -- a point-in-time dump of the
   machine's vitals (CPU/mem/disk/load/process list/network) plus whatever
   job-specific fields the caller passes in. Called automatically on
   conversion/publish failure (see conversion_job.py and s3_publish.py), so
   an intermittent failure leaves a forensic trail instead of just
   "it worked on retry."

Log format
----------
Every line is a single JSON object (one per line -- "JSON Lines"), so it's
both human-greppable and machine-parseable:

    {"ts": "2026-09-28T10:58:49.123Z", "level": "ERROR", "logger": "pdf2webview",
     "msg": "Conversion failed", "job_id": "dc8b5753", "ref_id": null,
     "pid": 1203017, "exc": "..."}

Usage
-----
    from app_logging import get_logger, job_context, capture_infra_snapshot

    log = get_logger(__name__)

    with job_context(job_id=job_id, ref_id=ref_id):
        log.info("Starting conversion", extra={"pdf_stem": pdf_stem})
        ...
        log.exception("Conversion failed")
        capture_infra_snapshot("conversion_failed", job_id=job_id)
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import platform
import socket
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

_LOGGER_NAME = "pdf2webview"
_configured = False
_configure_lock = threading.Lock()

# Per-"logical task" context (job_id, ref_id, stage, ...), propagated across
# the background conversion thread via job_context(). contextvars is used
# (rather than a plain global/thread-local) because it correctly follows
# asyncio/thread boundaries the same way either would need manual copying.
_context_var: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "pdf2webview_log_context", default={}
)


class _ContextFilter(logging.Filter):
    """Injects the current job_context() fields into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _context_var.get()
        for key, value in ctx.items():
            setattr(record, key, value)
        record.pid = os.getpid()
        return True


# Names that are meaningful attributes on a stdlib LogRecord already (see
# logging.LogRecord.__init__). Passing e.g. extra={"filename": ...} raises
# `KeyError: Attempt to overwrite 'filename' in LogRecord` deep inside
# logging.Logger.makeRecord() -- which would otherwise crash the *caller*
# (a Flask request handler, a conversion job) over a mere logging call.
# Call sites should avoid these names, but this is enforced here too as a
# safety net: colliding keys are logged under a `ctx_<name>` alias instead
# of being dropped or crashing the log call itself.
_RESERVED_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}


def _safe_extra(extra: dict | None) -> dict | None:
    """Rename any key in `extra` that collides with a reserved LogRecord
    attribute, so a call site typo (e.g. `filename`) can never raise from
    inside the logging call itself."""
    if not extra:
        return extra
    safe = {}
    for key, value in extra.items():
        safe[f"ctx_{key}" if key in _RESERVED_RECORD_ATTRS else key] = value
    return safe


class _JsonFormatter(logging.Formatter):
    """One JSON object per line -- greppable by field, parseable by tooling."""

    # Reserved keys already meaningful on a LogRecord; anything else in
    # __dict__ came from `extra={...}` or the context filter and gets
    # merged into the output verbatim.
    _RESERVED = frozenset(logging.LogRecord(
        "", 0, "", 0, "", (), None
    ).__dict__.keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._RESERVED:
                continue
            try:
                json.dumps(value)  # skip anything non-serializable
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value
        if record.exc_info:
            payload["exc"] = "".join(
                traceback.format_exception(*record.exc_info)
            )
        return json.dumps(payload, ensure_ascii=False)


class _SafeExtraLogger(logging.Logger):
    """A Logger that renames `extra` keys colliding with reserved LogRecord
    attributes (e.g. `filename`, `module`) instead of raising a KeyError out
    of the logging call itself -- see `_safe_extra` for why this matters.

    `extra` arrives here as a positional argument (Logger._log calls
    makeRecord(name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)
    positionally, not by keyword), so it must be intercepted positionally --
    a kwargs-only override would silently never fire.
    """

    def makeRecord(
        self, name, level, fn, lno, msg, args, exc_info,
        func=None, extra=None, sinfo=None,
    ):
        return super().makeRecord(
            name, level, fn, lno, msg, args, exc_info,
            func=func, extra=_safe_extra(extra), sinfo=sinfo,
        )


def _configure() -> None:
    global _configured
    with _configure_lock:
        if _configured:
            return

        config.LOG_DIR.mkdir(parents=True, exist_ok=True)

        # setLoggerClass affects future getLogger() calls app-wide, which is
        # fine here: this app has exactly one real logger name
        # (_LOGGER_NAME) and get_logger() is the only supported entry point.
        logging.setLoggerClass(_SafeExtraLogger)
        logger = logging.getLogger(_LOGGER_NAME)
        logging.setLoggerClass(logging.Logger)  # restore default for anything else
        logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.INFO))
        logger.propagate = False  # don't double-log via the root logger

        json_formatter = _JsonFormatter()
        context_filter = _ContextFilter()

        # File handler: rotates by size as a safety net between logrotate
        # runs (logrotate is the primary retention mechanism, see
        # deploy/logrotate/pdf2html); each rotated file is also gzipped by
        # logrotate, not here.
        file_handler = logging.handlers.RotatingFileHandler(
            config.LOG_DIR / "app.log",
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(json_formatter)
        file_handler.addFilter(context_filter)
        logger.addHandler(file_handler)

        # Stream handler: preserves the existing behavior of everything
        # being visible via `journalctl -u pdf2html` / `docker logs` / etc.
        # Uses a compact human-readable format there (journald already adds
        # its own timestamp), while the file gets full structured JSON.
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(
            logging.Formatter("[pdf2webview] %(levelname)s %(message)s")
        )
        stream_handler.addFilter(context_filter)
        logger.addHandler(stream_handler)

        _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the shared app logger (configuring handlers on first call).

    `name` is accepted for call-site clarity (`get_logger(__name__)`) but all
    loggers returned share the same handlers/config -- there's exactly one
    real logger ("pdf2webview"); this just makes call sites self-documenting.
    """
    _configure()
    return logging.getLogger(_LOGGER_NAME)


class job_context:
    """Context manager: attach fields (job_id, ref_id, stage, ...) to every
    log record emitted while inside the `with` block, including from code
    called deeper in the stack (e.g. convert.py functions called from
    conversion_job.py). Nests: entering a second job_context() merges onto
    the current one and restores it on exit.

    Example:
        with job_context(job_id=job_id, ref_id=ref_id):
            log.info("Starting conversion")   # includes job_id, ref_id
            convert_pdf_to_html(...)            # logs from in here too
    """

    def __init__(self, **fields: Any):
        self._fields = fields
        self._token: contextvars.Token | None = None

    def __enter__(self) -> "job_context":
        current = dict(_context_var.get())
        current.update(self._fields)
        self._token = _context_var.set(current)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._token is not None:
            _context_var.reset(self._token)


def current_context() -> dict:
    """Return a copy of the active job_context() fields (job_id, ref_id, ...)."""
    return dict(_context_var.get())


# ---------------------------------------------------------------------------
# Infra snapshots
# ---------------------------------------------------------------------------

def _safe_psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _disk_usage(paths: list[Path]) -> dict:
    out = {}
    seen_mounts = set()
    for p in paths:
        try:
            usage = os.statvfs(str(p))
            total = usage.f_frsize * usage.f_blocks
            free = usage.f_frsize * usage.f_bavail
            key = str(p)
            # Avoid duplicate entries when two paths share a filesystem.
            mount_id = (usage.f_fsid,)
            if mount_id in seen_mounts:
                continue
            seen_mounts.add(mount_id)
            out[key] = {
                "total_gb": round(total / (1024 ** 3), 2),
                "free_gb": round(free / (1024 ** 3), 2),
                "used_pct": round(100 * (1 - free / total), 1) if total else None,
            }
        except OSError as e:
            out[str(p)] = {"error": str(e)}
    return out


def _top_processes(psutil_mod, limit: int = 8) -> list[dict]:
    """Top processes by RSS memory -- cheap, no per-process CPU sampling
    delay (psutil.cpu_percent() per-process needs an interval to be
    meaningful, which would slow down every snapshot; system-wide CPU is
    captured separately instead)."""
    procs = []
    for p in psutil_mod.process_iter(
        ["pid", "name", "username", "memory_info", "cmdline"]
    ):
        try:
            info = p.info
            rss = info["memory_info"].rss if info.get("memory_info") else 0
            cmdline = " ".join(info.get("cmdline") or [])[:160]
            procs.append({
                "pid": info["pid"],
                "name": info.get("name"),
                "user": info.get("username"),
                "rss_mb": round(rss / (1024 ** 2), 1),
                "cmdline": cmdline,
            })
        except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
            continue
    procs.sort(key=lambda x: x["rss_mb"], reverse=True)
    return procs[:limit]


def build_infra_snapshot(reason: str, **fields: Any) -> dict:
    """Build (without writing) a point-in-time snapshot dict. Exposed
    separately from capture_infra_snapshot() so callers/tests can inspect
    the payload without touching disk."""
    snapshot: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "reason": reason,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "python": platform.python_version(),
    }
    snapshot.update(current_context())
    snapshot.update(fields)

    psutil_mod = _safe_psutil()
    if psutil_mod is not None:
        try:
            vm = psutil_mod.virtual_memory()
            swap = psutil_mod.swap_memory()
            snapshot["cpu_pct"] = psutil_mod.cpu_percent(interval=0.2)
            snapshot["cpu_count"] = psutil_mod.cpu_count()
            snapshot["load_avg"] = list(os.getloadavg())
            snapshot["mem"] = {
                "total_mb": round(vm.total / (1024 ** 2), 1),
                "available_mb": round(vm.available / (1024 ** 2), 1),
                "used_pct": vm.percent,
            }
            snapshot["swap"] = {
                "total_mb": round(swap.total / (1024 ** 2), 1),
                "used_pct": swap.percent,
            }
            snapshot["top_processes"] = _top_processes(psutil_mod)
            try:
                net = psutil_mod.net_io_counters()
                snapshot["net"] = {
                    "bytes_sent": net.bytes_sent,
                    "bytes_recv": net.bytes_recv,
                    "errin": net.errin,
                    "errout": net.errout,
                    "dropin": net.dropin,
                    "dropout": net.dropout,
                }
            except Exception:
                pass
        except Exception as e:
            snapshot["psutil_error"] = str(e)
    else:
        # Fallback with zero extra dependencies if psutil is ever missing.
        try:
            snapshot["load_avg"] = list(os.getloadavg())
        except (AttributeError, OSError):
            pass

    snapshot["disk"] = _disk_usage([
        config.OUTPUT_DIR, config.UPLOAD_DIR, config.LOG_DIR,
    ])

    return snapshot


def capture_infra_snapshot(reason: str, **fields: Any) -> Path | None:
    """Build an infra snapshot and append it (as one JSON line) to
    LOG_DIR/snapshots/infra-YYYY-MM-DD.jsonl. Never raises -- a failure to
    capture diagnostics must not itself break the caller's error handling.

    Returns the path written to, or None if writing failed.
    """
    try:
        snapshot = build_infra_snapshot(reason, **fields)
        config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = config.SNAPSHOT_DIR / f"infra-{day}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

        log = get_logger(__name__)
        log.warning(
            "Infra snapshot captured (%s)", reason,
            extra={"snapshot_path": str(path), "snapshot_reason": reason},
        )
        return path
    except Exception as e:
        # Best-effort: log to stderr directly, bypassing our own logger in
        # case the failure is disk-related (same disk the logger writes to).
        print(
            f"[pdf2webview] WARNING: failed to capture infra snapshot: {e}",
            file=sys.stderr,
        )
        return None


def prune_old_files(directory: Path, pattern: str, retention_days: int) -> int:
    """Delete files under `directory` matching `pattern` whose mtime is older
    than retention_days. Used as a safety net alongside logrotate/journald
    time-based retention. Returns the number of files removed."""
    if not directory.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for f in directory.glob(pattern):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    return removed
