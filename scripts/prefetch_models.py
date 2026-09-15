#!/usr/bin/env python3
"""
Pre-fetch ML models at image build time so worker pods start warm.

Docling (layout/table/OCR) and RapidOCR download model weights on first use.
Running a tiny conversion / model init here forces those downloads into the
image layer. Best-effort: any failure is non-fatal (the worker will fetch on
first use), so a transient network issue at build time doesn't break the image.

Invoked from Dockerfile.worker.
"""

from __future__ import annotations

import sys


def _prefetch_docling() -> None:
    """Initialize Docling's converter so its models are downloaded/cached."""
    try:
        from docling.document_converter import DocumentConverter
        # Constructing the converter triggers model resolution/caching.
        DocumentConverter()
        print("[prefetch] docling converter initialized")
    except Exception as exc:  # noqa: BLE001 - best-effort
        print(f"[prefetch] docling init skipped: {exc}")


def _prefetch_whisper() -> None:
    """Download the faster-whisper 'tiny' model used for captions."""
    try:
        from faster_whisper import WhisperModel
        WhisperModel("tiny", compute_type="int8")
        print("[prefetch] faster-whisper 'tiny' cached")
    except Exception as exc:  # noqa: BLE001 - best-effort
        print(f"[prefetch] whisper prefetch skipped: {exc}")


def main() -> int:
    print("[prefetch] starting model prefetch…")
    _prefetch_docling()
    _prefetch_whisper()
    print("[prefetch] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
