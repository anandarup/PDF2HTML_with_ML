"""
Learner progress tracking.

Video playback progress (last position, completion %) is stored per learner.
Phase 2 of the scalable-deployment plan puts this behind a backend abstraction
selected by `config.STATE_BACKEND`:

    file    -> TinyDB JSON file (original single-VM behavior; DEFAULT)
    service -> shared Redis store (concurrency-safe across workers/replicas)

The public functions — save_progress, get_progress, get_all_progress,
export_progress_for_strapi — keep their exact signatures and the >=90%
completion rule, so callers (app.py) are unchanged.
"""

from __future__ import annotations

import abc
import json
from pathlib import Path
from typing import Optional

import config


COMPLETION_THRESHOLD_PCT = 90.0


def _percentage(current_time: float, duration: float) -> float:
    return (current_time / duration * 100) if duration and duration > 0 else 0.0


class ProgressStore(abc.ABC):
    @abc.abstractmethod
    def save(self, learner_id: str, video_src: str, current_time: float,
             duration: float, completed: bool = False) -> dict: ...

    @abc.abstractmethod
    def get(self, learner_id: str, video_src: str) -> Optional[dict]: ...

    @abc.abstractmethod
    def get_all(self, learner_id: str) -> list[dict]: ...


class TinyDBProgressStore(ProgressStore):
    """Original TinyDB-backed implementation (local JSON file)."""

    def __init__(self, db_path: Path):
        from tinydb import TinyDB, Query
        self._Query = Query
        self._db = TinyDB(str(db_path))
        self._table = self._db.table("video_progress")

    def save(self, learner_id, video_src, current_time, duration, completed=False):
        pct = _percentage(current_time, duration)
        if pct >= COMPLETION_THRESHOLD_PCT:
            completed = True
        record = {
            "learner_id": learner_id,
            "video_src": video_src,
            "current_time": current_time,
            "duration": duration,
            "percentage": round(pct, 1),
            "completed": completed,
        }
        Q = self._Query()
        existing = self._table.get((Q.learner_id == learner_id) & (Q.video_src == video_src))
        if existing:
            self._table.update(record, doc_ids=[existing.doc_id])
        else:
            self._table.insert(record)
        return record

    def get(self, learner_id, video_src):
        Q = self._Query()
        return self._table.get((Q.learner_id == learner_id) & (Q.video_src == video_src))

    def get_all(self, learner_id):
        Q = self._Query()
        return self._table.search(Q.learner_id == learner_id)


class RedisProgressStore(ProgressStore):
    """Shared Redis-backed implementation (concurrency-safe across workers).

    Layout:
      - hash `progress:<learner_id>` with field `<video_src>` -> JSON record
    A per-learner hash makes get_all O(1) and keeps each learner's data together.
    """

    def __init__(self, redis_client, prefix: str = ""):
        self._r = redis_client
        self._p = prefix

    def _key(self, learner_id: str) -> str:
        return f"{self._p}progress:{learner_id}"

    def save(self, learner_id, video_src, current_time, duration, completed=False):
        pct = _percentage(current_time, duration)
        if pct >= COMPLETION_THRESHOLD_PCT:
            completed = True
        record = {
            "learner_id": learner_id,
            "video_src": video_src,
            "current_time": current_time,
            "duration": duration,
            "percentage": round(pct, 1),
            "completed": completed,
        }
        self._r.hset(self._key(learner_id), video_src, json.dumps(record))
        return record

    def get(self, learner_id, video_src):
        raw = self._r.hget(self._key(learner_id), video_src)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def get_all(self, learner_id):
        out = []
        for raw in (self._r.hvals(self._key(learner_id)) or []):
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return out


def _build_store() -> ProgressStore:
    if getattr(config, "STATE_BACKEND", "file") == "service":
        import redis
        url = getattr(config, "REDIS_URL", "")
        if not url:
            raise ValueError("STATE_BACKEND=service requires REDIS_URL for progress")
        client = redis.Redis.from_url(url, decode_responses=True)
        return RedisProgressStore(client)
    # default: TinyDB, same file path as before
    db_path = Path(__file__).parent / "learner_progress.json"
    return TinyDBProgressStore(db_path)


# Lazily-initialized singleton so importing this module is cheap and doesn't
# require Redis unless STATE_BACKEND=service.
_store: Optional[ProgressStore] = None


def _get_store() -> ProgressStore:
    global _store
    if _store is None:
        _store = _build_store()
    return _store


# --- Public API (unchanged signatures) -------------------------------------

def save_progress(learner_id: str, video_src: str, current_time: float,
                  duration: float, completed: bool = False) -> dict:
    """Save or update video playback progress for a learner."""
    return _get_store().save(learner_id, video_src, current_time, duration, completed)


def get_progress(learner_id: str, video_src: str) -> dict | None:
    """Get saved progress for a specific learner and video."""
    return _get_store().get(learner_id, video_src)


def get_all_progress(learner_id: str) -> list[dict]:
    """Get all video progress records for a learner."""
    return _get_store().get_all(learner_id)


def export_progress_for_strapi(learner_id: str) -> list[dict]:
    """Export progress data formatted for Strapi sync."""
    records = get_all_progress(learner_id)
    return [
        {
            "video_src": r["video_src"],
            "current_time": r["current_time"],
            "duration": r["duration"],
            "percentage": r["percentage"],
            "completed": r["completed"],
        }
        for r in records
    ]
