#!/usr/bin/env python3
"""
Conversion worker (scaled deployment).

Consumes conversion jobs from the queue and runs the Docling/OCR pipeline off
the API request path. This is the process that the OKE worker deployment runs
(`python worker.py`), scaled independently on queue depth.

It uses the SAME run_conversion_job() as the in-process ThreadQueue, so the
conversion behavior is identical whether it ran inline (single-VM default) or
here (horizontally scaled). Job state and output go to the shared backends
selected by config (STATE_BACKEND=service, OUTPUT_BACKEND=oci, etc.).

Run:
    QUEUE_BACKEND=queue STATE_BACKEND=service \
    REDIS_URL=... QUEUE_OCID=... QUEUE_ENDPOINT=... \
    python worker.py

With QUEUE_BACKEND=thread this exits immediately (there is nothing to consume —
work runs in the web process), which is the correct behavior for local/dev.
"""

from __future__ import annotations

import logging
import sys

import config
from state.job_store import build_job_store
from queue_backend.job_queue import build_job_queue
from conversion_job import run_conversion_job

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("worker")


def main() -> int:
    problems = config.validate()
    if problems:
        _log.error("Configuration problems: %s", problems)
        return 2

    if config.QUEUE_BACKEND != "queue":
        _log.info(
            "QUEUE_BACKEND=%s — no out-of-process queue to consume. "
            "Conversions run in the web process. Worker exiting.",
            config.QUEUE_BACKEND,
        )
        return 0

    job_store = build_job_store(config)

    def handler(message: dict) -> None:
        _log.info("Processing job %s (attempt %s)",
                  message.get("job_id"), message.get("attempt"))
        run_conversion_job(message, job_store)

    queue = build_job_queue(config, handler)
    _log.info("Worker started. Consuming queue '%s'…", config.QUEUE_NAME)
    try:
        queue.consume(handler)
    except KeyboardInterrupt:
        _log.info("Worker stopping (interrupt).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
