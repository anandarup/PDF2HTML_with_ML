"""
Job state abstraction.

Phase 1 of the scalable-deployment plan introduces a `JobStore` seam so the rest
of the app never touches job persistence directly. The default `FileJobStore`
reproduces the current behavior exactly (one JSON file per job under jobs/,
atomic writes, a process-level lock), and additionally maintains a
`ref_id -> job_id` index so `find_by_ref_id` is O(1) instead of scanning every
job file. Later phases add a `ServiceJobStore` (Redis + JSON DB) behind the
same interface, selected via `config.STATE_BACKEND`.

The record shape is unchanged from what app.py writes today, e.g.:
    {
      "status": "processing|done|published|error",
      "stage": "...", "detail": "...",
      "progress": {"stage": "...", "page_count": N, "image_count": M, ...},
      "result": {...} | None,
      "error": str | None,
      "ref_id": str | None,
      "edit_url": str | None,
      "render_url": str | None,
    }
"""

from __future__ import annotations

import abc
import json
import threading
from pathlib import Path
from typing import Optional


class JobStore(abc.ABC):
    """Interface every job-state backend implements."""

    @abc.abstractmethod
    def create_job(self, job_id: str, record: dict) -> None:
        """Create/overwrite a job's full record."""

    @abc.abstractmethod
    def get_job(self, job_id: str) -> Optional[dict]:
        """Return the job record, or None if it doesn't exist."""

    @abc.abstractmethod
    def update_job(self, job_id: str, record: dict) -> None:
        """Overwrite a job's full record (used for terminal states)."""

    @abc.abstractmethod
    def set_progress(self, job_id: str, stage: str, detail: str,
                     extra: Optional[dict] = None) -> None:
        """Merge a live progress update into the job (stage/detail + counters).

        Safe no-op if the job no longer exists.
        """

    @abc.abstractmethod
    def find_by_ref_id(self, ref_id: str) -> Optional[dict]:
        """Return the job record for an external ref_id, or None."""

    @abc.abstractmethod
    def delete_job(self, job_id: str) -> bool:
        """Remove a job's record entirely (and any ref_id index entry for it).

        Returns True if a record existed and was removed, False if there was
        nothing to delete. Safe to call on an unknown job_id.
        """


class FileJobStore(JobStore):
    """
    File-backed job store — behavior-identical to app.py's original
    _read_job/_write_job, plus a maintained ref_id index.

    - Each job is `jobs/<job_id>.json`, written atomically (.tmp then replace,
      POSIX-atomic), guarded by an internal re-entrant-safe lock.
    - A `ref_id -> job_id` index is kept in memory and lazily built from the
      existing job files on first use, so the ~100 pre-existing jobs are found
      without any migration step.
    """

    def __init__(self, jobs_dir: Path):
        self._jobs_dir = Path(jobs_dir)
        self._jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ref_index: dict[str, str] = {}
        self._index_built = False

    # --- low-level file I/O (matches the original implementation) ----------
    def _job_path(self, job_id: str) -> Path:
        return self._jobs_dir / f"{job_id}.json"

    def _read_file(self, job_id: str) -> Optional[dict]:
        path = self._job_path(job_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _write_file(self, job_id: str, data: dict) -> None:
        path = self._job_path(job_id)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data), encoding="utf-8")
        tmp_path.replace(path)  # atomic on POSIX

    def _index_ref(self, record: dict, job_id: str) -> None:
        ref = record.get("ref_id")
        if ref:
            self._ref_index[ref] = job_id

    def _ensure_index(self) -> None:
        """Lazily build the ref_id index from existing job files (once)."""
        if self._index_built:
            return
        for job_file in self._jobs_dir.glob("*.json"):
            rec = self._read_file(job_file.stem)
            if rec:
                self._index_ref(rec, job_file.stem)
        self._index_built = True

    # --- JobStore interface -------------------------------------------------
    def create_job(self, job_id: str, record: dict) -> None:
        with self._lock:
            self._write_file(job_id, record)
            self._ensure_index()
            self._index_ref(record, job_id)

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._lock:
            return self._read_file(job_id)

    def update_job(self, job_id: str, record: dict) -> None:
        with self._lock:
            self._write_file(job_id, record)
            self._ensure_index()
            self._index_ref(record, job_id)

    def set_progress(self, job_id: str, stage: str, detail: str,
                     extra: Optional[dict] = None) -> None:
        with self._lock:
            job = self._read_file(job_id)
            if job is None:
                return
            job["stage"] = stage
            job["detail"] = detail
            prog = job.get("progress") or {}
            if extra:
                prog.update(extra)
            prog["stage"] = stage
            job["progress"] = prog
            self._write_file(job_id, job)

    def find_by_ref_id(self, ref_id: str) -> Optional[dict]:
        with self._lock:
            self._ensure_index()
            job_id = self._ref_index.get(ref_id)
            if job_id:
                rec = self._read_file(job_id)
                # Guard against a stale index entry (e.g. ref_id changed).
                if rec and rec.get("ref_id") == ref_id:
                    return rec
            # Fallback: index miss (or stale) — scan once to be correct, and
            # repair the index. This keeps correctness identical to the old
            # scan while making the common path O(1).
            for job_file in self._jobs_dir.glob("*.json"):
                rec = self._read_file(job_file.stem)
                if rec and rec.get("ref_id") == ref_id:
                    self._ref_index[ref_id] = job_file.stem
                    return rec
            return None

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            rec = self._read_file(job_id)
            path = self._job_path(job_id)
            try:
                path.unlink()
                existed = True
            except FileNotFoundError:
                existed = False
            if rec:
                ref = rec.get("ref_id")
                if ref and self._ref_index.get(ref) == job_id:
                    del self._ref_index[ref]
            return existed


class ServiceJobStore(JobStore):
    """
    Shared-store job backend for horizontal scale (Phase 2).

    Backed by Redis today; structured so a durable JSON/document DB (OCI
    Autonomous JSON DB) can be layered underneath later without changing the
    interface or callers.

    Key layout (all keys optionally namespaced by `prefix`):
      - `job:<job_id>`      -> JSON string of the full record (source of truth)
      - `ref:<ref_id>`      -> job_id  (the ref_id index; O(1) lookup, REQ-2.2)
      - `jobs` (a set)      -> all job_ids (for migration/enumeration)

    Concurrency (REQ-2.4 / I4): reads-modify-writes for progress use a
    WATCH/MULTI optimistic transaction with bounded retries, so a worker's
    progress update and an API terminal write never clobber each other's fields.
    All writes are idempotent by `job_id` (DI-2): re-applying the same terminal
    record yields the same state.

    An optional TTL bounds how long *hot* records live in Redis. In production
    the durable copy would live in the JSON DB and Redis would just be the cache;
    here we set no TTL by default so Redis alone is durable enough for the store.
    """

    def __init__(self, redis_client, prefix: str = "", hot_ttl_seconds: int = 0,
                 max_retries: int = 5):
        self._r = redis_client
        self._p = prefix
        self._ttl = hot_ttl_seconds if hot_ttl_seconds and hot_ttl_seconds > 0 else None
        self._max_retries = max_retries

    # --- key helpers --------------------------------------------------------
    def _job_key(self, job_id: str) -> str:
        return f"{self._p}job:{job_id}"

    def _ref_key(self, ref_id: str) -> str:
        return f"{self._p}ref:{ref_id}"

    def _jobs_set_key(self) -> str:
        return f"{self._p}jobs"

    @staticmethod
    def _loads(raw):
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    def _persist(self, pipe_or_client, job_id: str, record: dict) -> None:
        """Write the record + maintain ref index + jobs set on a pipeline/client."""
        key = self._job_key(job_id)
        payload = json.dumps(record)
        if self._ttl:
            pipe_or_client.set(key, payload, ex=self._ttl)
        else:
            pipe_or_client.set(key, payload)
        pipe_or_client.sadd(self._jobs_set_key(), job_id)
        ref = record.get("ref_id")
        if ref:
            if self._ttl:
                pipe_or_client.set(self._ref_key(ref), job_id, ex=self._ttl)
            else:
                pipe_or_client.set(self._ref_key(ref), job_id)

    # --- JobStore interface -------------------------------------------------
    def create_job(self, job_id: str, record: dict) -> None:
        self._persist(self._r, job_id, record)

    def get_job(self, job_id: str) -> Optional[dict]:
        return self._loads(self._r.get(self._job_key(job_id)))

    def update_job(self, job_id: str, record: dict) -> None:
        # Idempotent full-record overwrite (terminal states carry the whole record).
        self._persist(self._r, job_id, record)

    def set_progress(self, job_id: str, stage: str, detail: str,
                     extra: Optional[dict] = None) -> None:
        key = self._job_key(job_id)

        def _apply(job: dict) -> dict:
            job["stage"] = stage
            job["detail"] = detail
            prog = job.get("progress") or {}
            if extra:
                prog.update(extra)
            prog["stage"] = stage
            job["progress"] = prog
            return job

        # Optimistic, field-level merge under WATCH so a concurrent terminal
        # write (done/error) isn't clobbered by a late progress update, and
        # vice-versa. Bounded retries on contention.
        for _ in range(self._max_retries):
            try:
                with self._r.pipeline() as pipe:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    job = self._loads(raw)
                    if job is None:
                        pipe.unwatch()
                        return  # job gone — safe no-op (matches FileJobStore)
                    job = _apply(job)
                    pipe.multi()
                    payload = json.dumps(job)
                    if self._ttl:
                        pipe.set(key, payload, ex=self._ttl)
                    else:
                        pipe.set(key, payload)
                    pipe.execute()
                    return
            except Exception as exc:  # redis.WatchError and transient errors
                # WatchError -> retry; other errors -> one more attempt then give up
                if exc.__class__.__name__ == "WatchError":
                    continue
                # Non-contention error: best-effort, don't break conversion
                return
        # Exhausted retries under heavy contention: drop this progress tick
        # rather than block; the next update will carry the latest state.
        return

    def find_by_ref_id(self, ref_id: str) -> Optional[dict]:
        job_id = self._r.get(self._ref_key(ref_id))
        if job_id is None:
            return None
        if isinstance(job_id, bytes):
            job_id = job_id.decode("utf-8")
        rec = self.get_job(job_id)
        # Guard against a stale index entry.
        if rec and rec.get("ref_id") == ref_id:
            return rec
        return None

    def delete_job(self, job_id: str) -> bool:
        rec = self.get_job(job_id)
        existed = self._r.delete(self._job_key(job_id)) > 0
        self._r.srem(self._jobs_set_key(), job_id)
        if rec:
            ref = rec.get("ref_id")
            if ref:
                self._r.delete(self._ref_key(ref))
        return existed


def _build_redis_client(config_module):
    """Create a redis client from config.REDIS_URL. Imported lazily so the
    dependency is only needed when STATE_BACKEND=service."""
    import redis  # redis-py
    url = getattr(config_module, "REDIS_URL", "")
    if not url:
        raise ValueError("STATE_BACKEND=service requires REDIS_URL to be set")
    # decode_responses=True so we work with str, not bytes.
    return redis.Redis.from_url(url, decode_responses=True)


def build_job_store(config_module) -> JobStore:
    """Factory: pick the job-store backend from config.STATE_BACKEND.

    Defaults to FileJobStore (current single-VM behavior). `service` selects the
    Redis-backed ServiceJobStore (Phase 2) and requires REDIS_URL.
    """
    backend = getattr(config_module, "STATE_BACKEND", "file")
    if backend == "file":
        return FileJobStore(config_module.JOBS_DIR)
    if backend == "service":
        client = _build_redis_client(config_module)
        # Until a durable JSON DB is layered underneath, Redis IS the source of
        # truth for job records, so default to NO expiry (durable). A TTL is
        # only applied when a separate durable store is configured — signalled
        # by JSONDB_URL being set — at which point Redis becomes a hot cache.
        durable_db_configured = bool(getattr(config_module, "JSONDB_URL", ""))
        ttl = (getattr(config_module, "STATE_HOT_TTL_SECONDS", 0) or 0) if durable_db_configured else 0
        return ServiceJobStore(client, hot_ttl_seconds=ttl)
    raise ValueError(f"Unknown STATE_BACKEND: {backend!r}")
