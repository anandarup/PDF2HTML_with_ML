"""
Real conversion analytics, derived from the JSON-lines app log.

The developer analytics dashboard (web_templates/t-dashboard.html) originally
ran on client-side mock data. This module reads the actual app log
(config.LOG_DIR/app.log, one JSON object per line -- see app_logging.py) and
reconstructs per-conversion "executions" plus the aggregates the dashboard
renders: KPIs, a status-over-time series, an error-type breakdown, and a
recent-executions table.

How executions are reconstructed
--------------------------------
A single conversion emits two paired log lines sharing a `job_id`:

    {"msg": "Starting conversion",  "job_id": ..., "pdf_stem": ..., "attempt": ...}
    {"msg": "Conversion finished",  "job_id": ..., "elapsed_seconds": ...,
     "page_count": ..., "image_count": ..., "html_file_size": ...}

On failure the terminal line is instead (ERROR level, with a traceback in
`exc`):

    {"msg": "Conversion failed", "level": "ERROR", "job_id": ...,
     "elapsed_seconds": ...}

We index terminal lines by job_id and join them onto their "Starting
conversion" line. A start with no terminal line (within the log window) is
reported as status "running" -- which is exactly the abandoned-worker failure
mode the codebase already worries about, so surfacing it is useful.

This is deliberately dependency-free (stdlib json only) and read-only: it never
writes, and tolerates malformed/rotated lines by skipping them.
"""

from __future__ import annotations

import json
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

# Terminal messages we recognise, mapped to a status.
_FINISHED_MSG = "Conversion finished"
_FAILED_MSG = "Conversion failed"
_START_MSG = "Starting conversion"


def _parse_ts(ts: str | None) -> datetime | None:
    """Parse the ISO-8601 timestamps app_logging writes (e.g.
    '2026-09-28T13:00:05.480+00:00'). Returns None on anything unparseable."""
    if not ts:
        return None
    try:
        # Python's fromisoformat handles the +00:00 offset directly.
        return datetime.fromisoformat(ts)
    except ValueError:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None


def _classify_error(record: dict) -> str:
    """Derive a coarse error code from a failed conversion's log record.

    The app log doesn't (yet) carry a structured error_code, so we infer one
    from the exception text when present. This mirrors the dashboard's
    bottleneck categories. Falls back to a generic bucket.
    """
    exc = (record.get("exc") or "") + " " + (record.get("msg") or "")
    exc_l = exc.lower()
    if "memoryerror" in exc_l or "memory" in exc_l and "limit" in exc_l:
        return "ERR_TIMEOUT"          # memory/timeout family (matches dashboard)
    if "font" in exc_l:
        return "ERR_MISSING_FONT"
    if "xref" in exc_l or "corrupt" in exc_l:
        return "ERR_XREF_CORRUPT"
    if "ocr" in exc_l:
        return "ERR_OCR_FAILURE"
    if "timeout" in exc_l or "timed out" in exc_l:
        return "ERR_TIMEOUT"
    if "permission" in exc_l or "no such file" in exc_l or "ioerror" in exc_l or "storage" in exc_l:
        return "ERR_STORAGE"
    return "ERR_UNSUPPORTED"


def _iter_log_records(log_path: Path):
    """Yield parsed JSON objects from the JSON-lines log, skipping bad lines."""
    if not log_path.exists():
        return
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (ValueError, TypeError):
                continue


def build_executions(log_path: Path | None = None, limit: int = 200) -> list[dict]:
    """Reconstruct per-conversion executions from the log.

    Returns a list (most-recent first) of dicts shaped for the dashboard:
        job_id, ref_id, filename, file(html)_size_bytes, page_count,
        duration_ms, status ("success"|"error"|"running"), error_code, ts (iso).
    """
    log_path = log_path or (config.LOG_DIR / "app.log")

    starts: "OrderedDict[str, dict]" = OrderedDict()
    terminals: dict[str, dict] = {}

    for rec in _iter_log_records(log_path):
        msg = rec.get("msg", "")
        job_id = rec.get("job_id")
        if not job_id:
            continue
        if msg == _START_MSG:
            starts[job_id] = rec
        elif msg == _FINISHED_MSG:
            terminals[job_id] = {"status": "success", "rec": rec}
        elif msg == _FAILED_MSG:
            terminals[job_id] = {"status": "error", "rec": rec}

    executions: list[dict] = []
    for job_id, srec in starts.items():
        term = terminals.get(job_id)
        if term is None:
            status = "running"
            trec = {}
        else:
            status = term["status"]
            trec = term["rec"]

        elapsed = trec.get("elapsed_seconds")
        duration_ms = round(float(elapsed) * 1000) if elapsed is not None else None

        pdf_stem = srec.get("pdf_stem") or trec.get("pdf_stem") or job_id
        # Terminal line timestamp is the completion moment; fall back to start.
        ts = _parse_ts(trec.get("ts")) or _parse_ts(srec.get("ts"))

        executions.append({
            "job_id": job_id,
            "ref_id": srec.get("ref_id") or trec.get("ref_id"),
            "filename": f"{pdf_stem}.pdf" if not str(pdf_stem).endswith(".pdf") else str(pdf_stem),
            "pdf_stem": pdf_stem,
            "html_file_size_bytes": trec.get("html_file_size"),
            "page_count": trec.get("page_count"),
            "image_count": trec.get("image_count"),
            "duration_ms": duration_ms,
            "status": status,
            "error_code": _classify_error(trec) if status == "error" else None,
            "attempt": srec.get("attempt"),
            "ts": ts.isoformat() if ts else None,
            "_ts_sort": ts.timestamp() if ts else 0.0,
        })

    executions.sort(key=lambda e: e["_ts_sort"], reverse=True)
    for e in executions:
        e.pop("_ts_sort", None)
    return executions[:limit]


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, int((pct / 100.0) * len(s)))
    return s[idx]


def build_dashboard_payload(log_path: Path | None = None) -> dict[str, Any]:
    """Assemble the full payload the dashboard consumes: kpis, time series,
    bottlenecks (top error codes), and the executions table."""
    executions = build_executions(log_path)

    total = len(executions)
    errors = [e for e in executions if e["status"] == "error"]
    durations = [e["duration_ms"] for e in executions if e["duration_ms"] is not None]
    # pages/sec only for completed runs with both numbers present
    pps_values = [
        (e["page_count"] / (e["duration_ms"] / 1000.0))
        for e in executions
        if e["status"] == "success" and e["page_count"] and e["duration_ms"]
    ]

    kpis = {
        "total_processed": total,
        "error_rate_pct": round((len(errors) / total) * 100, 1) if total else 0.0,
        "p95_latency_ms": _percentile(durations, 95),
        "p99_latency_ms": _percentile(durations, 99),
        "avg_pages_per_sec": round(sum(pps_values) / len(pps_values), 2) if pps_values else 0.0,
    }

    # Time series: bucket by hour-of-day of the timestamp, chronological.
    buckets: "OrderedDict[str, dict]" = OrderedDict()
    for e in reversed(executions):  # oldest first for a natural left-to-right axis
        if not e["ts"]:
            continue
        dt = _parse_ts(e["ts"])
        if not dt:
            continue
        key = dt.strftime("%m-%d %H:00")
        b = buckets.setdefault(key, {"label": key, "success": 0, "warning": 0, "error": 0})
        status = e["status"]
        if status == "running":
            # count in-flight jobs as warnings so they're visible but distinct
            b["warning"] += 1
        elif status in b:
            b[status] += 1
    time_series = list(buckets.values())

    # Bottleneck analysis: top error codes.
    code_counts = Counter(e["error_code"] for e in errors if e["error_code"])
    bottlenecks = [{"code": code, "count": n} for code, n in code_counts.most_common(6)]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "app.log",
        "kpis": kpis,
        "time_series": time_series,
        "bottlenecks": bottlenecks,
        "executions": executions,
    }
