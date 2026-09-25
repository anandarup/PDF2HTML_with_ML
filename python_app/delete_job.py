"""
Full, explicit deletion of a job's document and everything derived from it.

Distinct from cleanup_job.py (the retention CronJob): that script only ever
removes UNPUBLISHED, TTL-expired content and explicitly never touches a
published document (invariant I8). This module is the opposite case -- an
operator or an integrated CMS asking, by job_dir, to delete a specific
document right now, published or not. It is used by the DELETE
/api/documents/<job_dir> route in app.py; see docs/03-DELETE-WEBHOOK.md.

What gets removed, best-effort and in this order:
    1. The local output directory (HTML, images/, media/, the preserved
       -source.pdf) via the active OutputStore.
    2. Any leftover transient upload under uploads/<job_id>_* -- normally
       already gone (conversion deletes it on success), but confirmed to
       still exist for some older/crashed jobs (see the earlier repo
       investigation), so this is swept defensively.
    3. Every object under <job_dir>/ in all three OCI buckets (HTML, media,
       video), if OCI is reachable -- regardless of OUTPUT_BACKEND, because
       uploads happen during conversion independent of that flag (see
       config.py's OCI_UPLOADS_ENABLED comment).
    4. The job record itself, via JobStore.delete_job.

Each step is independent and failures don't abort the rest: a delete that
removes everything it safely can is more useful than one that stops at the
first problem and leaves the rest untouched. The full per-step outcome is
returned so the caller (the route) can report exactly what happened rather
than a bare success/failure.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import config
from state.job_store import JobStore
from storage.output_store import OutputStore

_log = logging.getLogger(__name__)


def job_id_from_dir(job_dir: str) -> str:
    """job_dir is "{job_id}_{pdf_stem}"; job_id is everything before the
    first underscore. Matches the convention already used in app.py's
    publish_for_learners and _sync_job_record_title -- kept identical here
    rather than introduced as a new one."""
    return job_dir.split("_", 1)[0] if "_" in job_dir else job_dir[:8]


def _delete_upload(job_id: str) -> int:
    """Remove any leftover transient upload(s) for this job. Normally a
    no-op -- conversion_job.py deletes the upload right after conversion --
    but see the module docstring: some do survive a crashed/killed worker."""
    removed = 0
    if not config.UPLOAD_DIR.exists():
        return removed
    for f in config.UPLOAD_DIR.glob(f"{job_id}_*"):
        if not f.is_file():
            continue
        try:
            f.unlink()
            removed += 1
        except OSError as exc:
            _log.warning(f"Could not remove upload {f.name}: {exc}")
    return removed


def _delete_oci_objects(job_dir: str) -> dict:
    """Delete every object under job_dir/ in all three buckets. Best-effort:
    an unreachable OCI (off-VM, no IAM policy -- the same condition
    is_available() already guards elsewhere) is not an error here, since a
    local/on-VM-only deployment has nothing there to begin with."""
    result = {"attempted": False, "buckets": {}}
    try:
        import oci_storage
    except Exception as exc:  # pragma: no cover -- import-time failure only
        _log.warning(f"oci_storage unavailable, skipping bucket cleanup: {exc}")
        return result

    if not oci_storage.is_available():
        return result

    result["attempted"] = True
    prefix = f"{job_dir}/"
    for label, bucket in (
        ("html", oci_storage.HTML_BUCKET_NAME),
        ("media", oci_storage.BUCKET_NAME),
        ("video", oci_storage.VIDEO_BUCKET_NAME),
    ):
        try:
            result["buckets"][label] = oci_storage.delete_prefix(prefix, bucket=bucket)
        except Exception as exc:
            _log.warning(f"Bucket cleanup failed for {label} ({bucket}): {exc}")
            result["buckets"][label] = {"deleted": 0, "failed": [], "error": str(exc)}
    return result


def delete_job_artifacts(
    job_dir: str,
    job_store: JobStore,
    output_store: OutputStore,
    *,
    job_id: Optional[str] = None,
) -> dict:
    """Delete everything associated with job_dir. See module docstring for
    the exact steps and ordering. Never raises -- every step is independent
    and best-effort; the returned dict is the complete, honest record of
    what actually happened."""
    jid = job_id or job_id_from_dir(job_dir)

    outcome = {
        "job_dir": job_dir,
        "job_id": jid,
        "output_dir_removed": False,
        "uploads_removed": 0,
        "oci": {"attempted": False, "buckets": {}},
        "job_record_removed": False,
    }

    try:
        outcome["output_dir_removed"] = output_store.delete(job_dir)
    except Exception as exc:
        _log.warning(f"Failed to remove output dir for {job_dir}: {exc}")

    try:
        outcome["uploads_removed"] = _delete_upload(jid)
    except Exception as exc:
        _log.warning(f"Failed to sweep uploads for {jid}: {exc}")

    outcome["oci"] = _delete_oci_objects(job_dir)

    try:
        outcome["job_record_removed"] = job_store.delete_job(jid)
    except Exception as exc:
        _log.warning(f"Failed to remove job record {jid}: {exc}")

    return outcome
