"""
Learner progress tracking with TinyDB.

Stores video playback progress (last position, completion percentage)
in a local TinyDB JSON file. Data is also synced to Strapi on export.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tinydb import TinyDB, Query

# Database file stored alongside the app
DB_PATH = Path(__file__).parent / "learner_progress.json"
_db = TinyDB(str(DB_PATH))
_progress_table = _db.table("video_progress")

Progress = Query()


def save_progress(
    learner_id: str,
    video_src: str,
    current_time: float,
    duration: float,
    completed: bool = False,
) -> dict:
    """
    Save or update video playback progress for a learner.

    Args:
        learner_id: Unique learner identifier (from localStorage)
        video_src: Video source URL/path
        current_time: Current playback position in seconds
        duration: Total video duration in seconds
        completed: Whether the video was watched to completion (>90%)

    Returns:
        The saved progress record
    """
    percentage = (current_time / duration * 100) if duration > 0 else 0
    if percentage >= 90:
        completed = True

    record = {
        "learner_id": learner_id,
        "video_src": video_src,
        "current_time": current_time,
        "duration": duration,
        "percentage": round(percentage, 1),
        "completed": completed,
    }

    existing = _progress_table.get(
        (Progress.learner_id == learner_id) & (Progress.video_src == video_src)
    )

    if existing:
        _progress_table.update(record, doc_ids=[existing.doc_id])
    else:
        _progress_table.insert(record)

    return record


def get_progress(learner_id: str, video_src: str) -> dict | None:
    """Get saved progress for a specific learner and video."""
    return _progress_table.get(
        (Progress.learner_id == learner_id) & (Progress.video_src == video_src)
    )


def get_all_progress(learner_id: str) -> list[dict]:
    """Get all video progress records for a learner."""
    return _progress_table.search(Progress.learner_id == learner_id)


def export_progress_for_strapi(learner_id: str) -> list[dict]:
    """
    Export progress data formatted for Strapi sync.

    Returns a list of progress records suitable for pushing to
    a Strapi 'progress' content type.
    """
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
