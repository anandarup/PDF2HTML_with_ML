#!/usr/bin/env python3
"""
Lifecycle / cleanup job (Phase 7).

Runs as an OKE CronJob. Enforces retention so local disk / object storage and
the job store don't grow unbounded:

  1. Transient uploads whose job has reached a terminal state are removed.
  2. Job records and their UNPUBLISHED output older than RETENTION_TTL_DAYS
     are removed.
  3. PUBLISHED learner content is NEVER deleted by this job (invariant I8):
     it consults the `published` flag / render_url on the job record before
     touching any artifact.

Best-effort and idempotent: safe to run repeatedly. Read-only unless artifacts
genuinely qualify for deletion.

Usage:
    RETENTION_TTL_DAYS=30 python cleanup_job.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import config
from state.job_store import build_job_store

TERMINAL_STATES = {"done", "published", "error"}


def _is_published(record: dict) -> bool:
    return bool(record.get("published") or record.get("render_url")
                or record.get("status") == "published")


def _job_age_days(record: dict) -> float:
    ts = record.get("updated_at") or record.get("created_at")
    if not ts:
        return 0.0  # unknown age -> treat as fresh (never delete on uncertainty)
    try:
        # Accept epoch seconds or ISO-ish; be lenient.
        if isinstance(ts, (int, float)):
            epoch = float(ts)
        else:
            import datetime as _dt
            epoch = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        return max(0.0, (time.time() - epoch) / 86400.0)
    except Exception:
        return 0.0


def cleanup(dry_run: bool = False) -> dict:
    ttl_days = config.RETENTION_TTL_DAYS
    uploads_removed = jobs_expired = output_removed = published_skipped = 0

    # 1) Transient uploads for terminal jobs.
    uploads = config.UPLOAD_DIR
    if uploads.exists():
        for f in uploads.glob("*"):
            if not f.is_file():
                continue
            job_id = f.name.split("_", 1)[0]
            rec = None
            try:
                rec = build_job_store(config).get_job(job_id)
            except Exception:
                rec = None
            # If the job is terminal (or unknown/gone), the upload is disposable.
            if rec is None or rec.get("status") in TERMINAL_STATES:
                if dry_run:
                    print(f"[dry-run] would remove upload {f.name}")
                else:
                    try:
                        f.unlink()
                    except OSError:
                        pass
                uploads_removed += 1

    # 2) Expire old job records + their UNPUBLISHED output.
    # (File backend enumerates jobs/*.json; the service backend would enumerate
    #  the `jobs` set. Here we handle the file layout used by cleanup on-VM.)
    jobs_dir = config.JOBS_DIR
    output_root = config.OUTPUT_DIR
    if jobs_dir.exists():
        import json as _json
        for jf in jobs_dir.glob("*.json"):
            try:
                rec = _json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if _is_published(rec):
                published_skipped += 1
                continue  # NEVER delete published content
            if _job_age_days(rec) < ttl_days:
                continue
            # Old + unpublished -> expire the record and its output dir.
            job_id = jf.stem
            # find output dir(s) prefixed by job_id
            if output_root.exists():
                for d in output_root.glob(f"{job_id}_*"):
                    if d.is_dir():
                        if dry_run:
                            print(f"[dry-run] would remove output dir {d.name}")
                        else:
                            _rmtree(d)
                        output_removed += 1
            if dry_run:
                print(f"[dry-run] would expire job record {job_id}")
            else:
                try:
                    jf.unlink()
                except OSError:
                    pass
            jobs_expired += 1

    summary = {
        "uploads_removed": uploads_removed,
        "jobs_expired": jobs_expired,
        "output_dirs_removed": output_removed,
        "published_skipped": published_skipped,
        "ttl_days": ttl_days,
        "dry_run": dry_run,
    }
    print(f"[cleanup] {summary}")
    return summary


def _rmtree(path: Path) -> None:
    import shutil
    try:
        shutil.rmtree(str(path))
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="TTL cleanup of uploads/jobs/unpublished output.")
    ap.add_argument("--dry-run", action="store_true", help="Report only; delete nothing.")
    args = ap.parse_args()
    cleanup(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
