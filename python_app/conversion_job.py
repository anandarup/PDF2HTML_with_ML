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

from pathlib import Path
from urllib.parse import quote

import config
from convert import convert_pdf_to_html


def run_conversion_job(message: dict, job_store) -> None:
    """Execute one conversion job and persist its terminal state.

    Never raises for ordinary conversion failures — it records them as the
    job's error state (so the ThreadQueue thread and the queue consumer behave
    identically). It re-raises only truly unexpected errors so an out-of-process
    consumer can decide to retry via the queue's visibility timeout.
    """
    job_id = message["job_id"]
    upload_ref = message["upload_ref"]
    ref_id = message.get("ref_id")
    pdf_stem = message.get("pdf_stem") or Path(upload_ref).stem
    upload_path = Path(upload_ref)

    def on_progress(stage: str, detail: str, extra: dict | None = None) -> None:
        job_store.set_progress(job_id, stage, detail, extra)

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
        # Ordinary conversion failure (incl. PDFSuitabilityError): record and
        # do NOT re-raise — this is a terminal, non-retryable outcome.
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
