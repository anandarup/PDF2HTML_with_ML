#!/usr/bin/env python3
"""
One-off migration: import existing file-based job records into the
ServiceJobStore (Redis / shared state).

Idempotent and re-runnable:
  - Reads every jobs/*.json (the current FileJobStore data).
  - Writes each into the ServiceJobStore, which rebuilds the ref_id index.
  - By default SKIPS jobs that already exist in the service store (so re-running
    is safe and won't clobber newer state). Pass --overwrite to force.

Requires STATE_BACKEND=service and REDIS_URL to be configured in the
environment (the destination), while the source is always the local jobs/ dir.

Usage:
    STATE_BACKEND=service REDIS_URL=redis://host:6379/0 \
        python migrate_jobs_to_service.py [--overwrite] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

import config
from state.job_store import FileJobStore, ServiceJobStore, _build_redis_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate file job records into the service store.")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite jobs that already exist in the service store.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be migrated without writing.")
    args = parser.parse_args()

    if config.STATE_BACKEND != "service":
        print("Refusing to run: set STATE_BACKEND=service (destination) before migrating.",
              file=sys.stderr)
        return 2
    if not config.REDIS_URL:
        print("Refusing to run: REDIS_URL is not set.", file=sys.stderr)
        return 2

    source = FileJobStore(config.JOBS_DIR)
    dest = ServiceJobStore(_build_redis_client(config))

    total = migrated = skipped = errors = 0
    for job_file in sorted(config.JOBS_DIR.glob("*.json")):
        job_id = job_file.stem
        total += 1
        record = source.get_job(job_id)
        if record is None:
            errors += 1
            print(f"  ! unreadable: {job_id}")
            continue

        existing = dest.get_job(job_id)
        if existing is not None and not args.overwrite:
            skipped += 1
            continue

        if args.dry_run:
            migrated += 1
            print(f"  would migrate: {job_id}"
                  f"{' (overwrite)' if existing else ''}")
            continue

        dest.update_job(job_id, record)  # idempotent; rebuilds ref_id index
        migrated += 1
        print(f"  migrated: {job_id}")

    print(f"\nDone. total={total} migrated={migrated} "
          f"skipped(existing)={skipped} errors={errors} "
          f"{'[DRY RUN]' if args.dry_run else ''}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
