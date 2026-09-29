"""
The conversion job runner — the single unit of work shared by the in-process
ThreadQueue (today's behavior) and the standalone worker.py (scaled deployment).

`run_conversion_job(message, job_store)` takes a queue message and performs the
full pipeline: run convert_pdf_to_html, then record the terminal state in the
job store. It is idempotent by job_id (re-running yields the same terminal
record), so a redelivered queue message is safe.

Message shape: {job_id, upload_ref, ref_id, pdf_stem, attempt}
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import quote

import config
from app_logging import capture_infra_snapshot, get_logger, job_context
from convert import convert_pdf_to_html

log = get_logger(__name__)

# A conversion that's genuinely still running updates job_store.set_progress()
# roughly every few seconds (see convert.py's progress_callback calls). A job
# stuck at "processing" for longer than this was, with overwhelming
# probability, abandoned by a process that died mid-conversion (the gunicorn
# --max-requests worker-recycle failure mode -- see module docstring above).
# Chosen well above any real single-stage duration (large scanned PDFs can
# spend minutes in OCR) to avoid ever flagging a conversion that is simply
# slow and still genuinely alive.
STUCK_JOB_THRESHOLD_SECONDS = 20 * 60


def run_conversion_job(message: dict, job_store) -> None:
    """Execute one conversion job and persist its terminal state.

    Never raises for ordinary conversion failures — it records them as the
    job's error state (so the ThreadQueue thread and the queue consumer behave
    identically). It re-raises only truly unexpected errors so an out-of-process
    consumer can decide to retry via the queue's visibility timeout.

    NOTE on silent worker-restart failures: when this runs under
    QUEUE_BACKEND=thread (the single-VM default), it executes in a daemon
    thread INSIDE the gunicorn worker process. If gunicorn recycles that
    worker mid-conversion (e.g. --max-requests), the thread is killed with the
    process -- the `except Exception` below never runs, nothing is logged, and
    the job silently never reaches a terminal state. That failure mode is NOT
    catchable from inside this function; look for a "Starting conversion" log
    line with no matching "Conversion finished"/"Conversion failed" line
    close after it, cross-referenced with a gunicorn "Booting worker" line in
    the same window, as the signature of this specific failure mode.
    """
    job_id = message["job_id"]
    upload_ref = message["upload_ref"]
    ref_id = message.get("ref_id")
    pdf_stem = message.get("pdf_stem") or Path(upload_ref).stem
    upload_path = Path(upload_ref)
    started_at = time.monotonic()

    def on_progress(stage: str, detail: str, extra: dict | None = None) -> None:
        log.info("Progress: %s", detail, extra={"stage": stage, "progress_extra": extra})
        job_store.set_progress(job_id, stage, detail, extra)

    with job_context(job_id=job_id, ref_id=ref_id, pdf_stem=pdf_stem):
        log.info(
            "Starting conversion",
            extra={"upload_ref": str(upload_path), "attempt": message.get("attempt", 1)},
        )
        try:
            output_dir = str(config.OUTPUT_DIR.resolve() / f"{job_id}_{pdf_stem}")

            result = convert_pdf_to_html(
                pdf_path=str(upload_path),
                output_dir=output_dir,
                progress_callback=on_progress,
            )

            html_path = Path(result["html_path"])
            relative_output = html_path.parent.name
            html_filename = html_path.name

            # Hand-built URL (no request context in the worker/thread).
            html_url = "/output/{}/{}".format(
                quote(relative_output, safe=""), quote(html_filename, safe="")
            )

            elapsed = round(time.monotonic() - started_at, 1)
            log.info(
                "Conversion finished",
                extra={
                    "elapsed_seconds": elapsed,
                    "page_count": result["page_count"],
                    "image_count": result["image_count"],
                    "html_file_size": result["html_file_size"],
                },
            )

            job_store.update_job(job_id, {
                "status": "done",
                "stage": "done",
                "detail": "Conversion complete.",
                "error": None,
                "ref_id": ref_id,
                "edit_url": html_url,
                "render_url": None,
                "result": {
                    "success": True,
                    "title": result.get("chapter_title", pdf_stem),
                    "html_url": html_url,
                    "page_count": result["page_count"],
                    "image_count": result["image_count"],
                    "file_size": result["html_file_size"],
                },
            })

        except Exception as e:
            # Ordinary conversion failure (incl. PDFSuitabilityError): record
            # and do NOT re-raise — this is a terminal, non-retryable outcome.
            elapsed = round(time.monotonic() - started_at, 1)
            log.exception(
                "Conversion failed",
                extra={"elapsed_seconds": elapsed},
            )
            capture_infra_snapshot(
                "conversion_failed",
                job_id=job_id, ref_id=ref_id, pdf_stem=pdf_stem,
                error=str(e), elapsed_seconds=elapsed,
            )
            job_store.update_job(job_id, {
                "status": "error",
                "stage": "error",
                "detail": str(e),
                "result": None,
                "error": str(e),
                "ref_id": ref_id,
                "edit_url": None,
                "render_url": None,
            })

        finally:
            # Clean up the transient upload (best-effort).
            try:
                if upload_path.exists():
                    upload_path.unlink()
            except OSError:
                pass


def reconcile_stuck_jobs(job_store, threshold_seconds: float = STUCK_JOB_THRESHOLD_SECONDS) -> int:
    """Find jobs abandoned by a process that died mid-conversion and mark
    them as failed, instead of leaving them at status="processing" forever.

    This directly targets the root cause documented at the top of this file:
    under QUEUE_BACKEND=thread, a conversion runs in a daemon thread inside
    the gunicorn worker process. If that worker is killed (e.g. recycled by
    --max-requests) while the thread is running, the thread dies with it --
    run_conversion_job()'s own except/finally never execute, because the
    interpreter itself is torn down, not raising an exception. Nothing
    catches that from *inside* a single job's run. The only place that CAN
    catch it is a fresh process starting up afterward, noticing a job that
    claims to be "processing" but hasn't been touched in far longer than any
    real conversion stage takes.

    Called once at application startup (see app.py), which is exactly when a
    fresh gunicorn worker boots after a recycle -- the natural place to sweep
    up whatever the previous worker abandoned.

    Deliberately conservative: only touches jobs whose `updated_at` is older
    than `threshold_seconds`, so a conversion that is still genuinely running
    (just slow -- large scanned PDFs can spend minutes in OCR) is never
    touched. Idempotent and safe to call from multiple worker boots
    concurrently landing on the same stale record (each just overwrites the
    same terminal state).

    Returns the number of jobs reconciled.
    """
    now = time.time()
    reconciled = 0
    try:
        job_ids = job_store.list_job_ids()
    except Exception:
        log.exception("Reconciliation sweep: failed to list jobs")
        return 0

    for job_id in job_ids:
        try:
            job = job_store.get_job(job_id)
        except Exception:
            continue
        if not job or job.get("status") != "processing":
            continue

        updated_at = job.get("updated_at")
        if not updated_at:
            # Pre-dates the created_at/updated_at stamping added alongside
            # this sweep -- age is unknown. Treat as stale rather than
            # skipping forever: a legitimately-running job always has a
            # progress update within the last few seconds via
            # set_progress(), so an unstamped "processing" record can only be
            # debris from before this fix existed.
            age = threshold_seconds + 1
        else:
            age = now - updated_at
        if age < threshold_seconds:
            continue  # still plausibly running -- leave it alone

        with job_context(job_id=job_id, ref_id=job.get("ref_id")):
            log.warning(
                "Reconciling stuck job (abandoned mid-conversion by a prior "
                "process, likely a worker recycle)",
                extra={"age_seconds": round(age, 1), "last_stage": job.get("stage")},
            )
        job_store.update_job(job_id, {
            **job,
            "status": "error",
            "stage": "error",
            "detail": (
                "Conversion did not finish (the server process restarted "
                "mid-conversion). Please try uploading again."
            ),
            "error": "worker_recycled_mid_conversion",
            "result": None,
        })
        reconciled += 1

    if reconciled:
        log.warning(
            "Reconciliation sweep complete", extra={"jobs_reconciled": reconciled}
        )
        capture_infra_snapshot("stuck_jobs_reconciled", jobs_reconciled=reconciled)
    return reconciled
