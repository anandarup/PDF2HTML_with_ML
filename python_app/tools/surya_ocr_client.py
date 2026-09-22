"""
Datalab / Surya OCR client.

Thin wrapper over the hosted Datalab document-conversion API
(https://documentation.datalab.to). Used only when OCR_ENGINE=surya; the
default RapidOCR path does not touch this module.

Flow (per Datalab docs):
    1. POST /api/v1/convert with the file          -> {request_id, request_check_url}
    2. GET request_check_url until status=="complete"
    3. If the response carries a signed `result_url` (regional/EU downloads),
       fetch it and merge; then check `success` before reading output.

Design notes / honesty:
    - This is a network, whole-document service, NOT a Docling-pluggable local
      recognizer. It returns markdown/HTML + base64 images for the whole file.
    - `success` can be False on a completed request (e.g. the page-concurrency
      limit is enforced during processing, not at submit). We surface that as
      an error rather than silently returning empty output.
    - The API key is a secret: it is sent only in the X-API-Key header and is
      never logged or included in exception messages.

Nothing here mutates the running app's behavior unless OCR_ENGINE=surya.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import requests

_log = logging.getLogger(__name__)

_CONVERT_PATH = "/api/v1/convert"
# Statuses that mean "stop polling".
_TERMINAL_STATUSES = {"complete", "failed"}


class SuryaOcrError(RuntimeError):
    """Raised when the Datalab/Surya conversion cannot be completed.

    Messages are safe to surface and never contain the API key.
    """


@dataclass
class SuryaOcrResult:
    """Normalized result of a Datalab conversion."""

    markdown: str = ""
    html: str = ""
    images: Dict[str, str] = field(default_factory=dict)  # {filename: base64}
    page_count: int = 0
    parse_quality_score: Optional[float] = None
    request_id: str = ""


def _headers(api_key: str) -> dict:
    # X-API-Key is the only place the secret is used.
    return {"X-API-Key": api_key}


def _submit(
    session: requests.Session,
    base_url: str,
    api_key: str,
    pdf_path: str,
    mode: str,
    processing_location: str,
    max_retries: int = 3,
    disable_image_captions: bool = False,
) -> str:
    """Submit the PDF for conversion. Returns request_check_url.

    processing_location, when set, requires file_url / datalab:// per the API —
    multipart upload is not allowed with it. To keep multipart upload (simplest
    for a local file) we only send processing_location when it does not conflict;
    US is the default region, so we omit the param for "us" and rely on the
    account/default region, and only pass it explicitly for "eu" via file_url
    (unsupported here) -> we therefore reject eu+multipart early.
    """
    url = base_url.rstrip("/") + _CONVERT_PATH
    data = {
        "output_format": "markdown",
        "mode": mode,
        "paginate": "false",
        # Picture descriptions are always written in English by the backend, so
        # the caller disables them for documents that are not in English.
        "disable_image_captions": "true" if disable_image_captions else "false",
    }
    # The API rejects multipart upload combined with processing_location. Since
    # the app uploads a local file (multipart), we can only honor "us" (the
    # default region) this way. "eu" would need a pre-uploaded datalab:// ref.
    if processing_location and processing_location != "us":
        raise SuryaOcrError(
            f"DATALAB_PROCESSING_LOCATION='{processing_location}' requires a "
            "pre-uploaded file reference; only 'us' is supported with direct "
            "upload. Set DATALAB_PROCESSING_LOCATION=us."
        )

    last_status = None
    for attempt in range(max_retries):
        with open(pdf_path, "rb") as fh:
            files = {"file": (_basename(pdf_path), fh, "application/pdf")}
            resp = session.post(
                url,
                headers=_headers(api_key),
                data=data,
                files=files,
                timeout=120,
            )
        last_status = resp.status_code
        if resp.status_code == 429:
            # Rate limited on submission. Back off and retry.
            time.sleep(min(2 ** attempt * 5, 60))
            continue
        if resp.status_code == 401:
            raise SuryaOcrError(
                "OCR service authentication failed (401). Check DATALAB_API_KEY."
            )
        if resp.status_code == 402:
            raise SuryaOcrError(
                "OCR service spend cap reached (402). Increase the key's limit."
            )
        if resp.status_code >= 400:
            raise SuryaOcrError(
                f"OCR service submit failed with HTTP {resp.status_code}."
            )
        body = _json(resp)
        check_url = body.get("request_check_url")
        if not check_url:
            raise SuryaOcrError("OCR service submit response missing request_check_url.")
        return check_url

    raise SuryaOcrError(
        f"OCR service submit rate-limited after {max_retries} retries "
        f"(last HTTP {last_status})."
    )


def _poll(
    session: requests.Session,
    api_key: str,
    check_url: str,
    timeout_seconds: int,
    poll_interval: int,
) -> dict:
    """Poll until the job is terminal or the deadline passes. Returns the
    completed result payload (with result_url merged if present)."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        if time.monotonic() > deadline:
            raise SuryaOcrError(
                f"OCR service conversion timed out after {timeout_seconds}s."
            )
        resp = session.get(check_url, headers=_headers(api_key), timeout=60)
        if resp.status_code == 429:
            time.sleep(min(poll_interval * 2, 30))
            continue
        if resp.status_code >= 400:
            raise SuryaOcrError(
                f"OCR service status check failed with HTTP {resp.status_code}."
            )
        result = _json(resp)
        status = result.get("status")

        # A completed job may still have failed; check success + error.
        if result.get("success") is False or status == "failed":
            raise SuryaOcrError(
                "OCR service conversion failed: "
                + str(result.get("error") or "unknown error")
            )

        if status == "complete":
            # Regional results are delivered via a signed result_url. The URL
            # authorizes itself; do NOT send the API key when downloading it.
            result_url = result.get("result_url")
            if result_url:
                dl = session.get(result_url, timeout=120)
                if dl.status_code >= 400:
                    raise SuryaOcrError(
                        f"OCR service result download failed HTTP {dl.status_code}."
                    )
                downloaded = _json(dl)
                # Keep non-null polling fields (billing/quality can update).
                merged = {**downloaded,
                          **{k: v for k, v in result.items() if v is not None}}
                result = merged
                if result.get("success") is False:
                    raise SuryaOcrError(
                        "OCR service conversion failed: "
                        + str(result.get("error") or "unknown error")
                    )
            return result

        # status == "processing" (or anything non-terminal): keep waiting.
        time.sleep(poll_interval)


def convert_pdf(
    pdf_path: str,
    *,
    api_key: str,
    base_url: str,
    mode: str = "balanced",
    processing_location: str = "us",
    timeout_seconds: int = 600,
    poll_interval: int = 2,
    disable_image_captions: bool = False,
) -> SuryaOcrResult:
    """Convert a PDF via the Datalab/Surya API and return normalized output.

    Set disable_image_captions to skip the backend's picture descriptions; they
    are produced in English regardless of the document's language.

    Raises SuryaOcrError on any failure (auth, timeout, API error). Never logs
    or embeds the API key.
    """
    if not api_key:
        raise SuryaOcrError("OCR service API key (DATALAB_API_KEY) is not set.")

    session = requests.Session()
    _log.info(
        "Submitting PDF to Datalab (mode=%s, region=%s, image_captions=%s)",
        mode, processing_location, "off" if disable_image_captions else "on",
    )
    check_url = _submit(
        session, base_url, api_key, pdf_path, mode, processing_location,
        disable_image_captions=disable_image_captions,
    )
    result = _poll(session, api_key, check_url, timeout_seconds, poll_interval)

    return SuryaOcrResult(
        markdown=result.get("markdown") or "",
        html=result.get("html") or "",
        images=result.get("images") or {},
        page_count=int(result.get("page_count") or 0),
        parse_quality_score=result.get("parse_quality_score"),
        request_id=str(result.get("request_id") or ""),
    )


def _json(resp: requests.Response) -> dict:
    try:
        data = resp.json()
    except ValueError as exc:  # untrusted external content
        raise SuryaOcrError("OCR service returned a non-JSON response.") from exc
    if not isinstance(data, dict):
        raise SuryaOcrError("OCR service returned an unexpected response shape.")
    return data


def _basename(path: str) -> str:
    import os
    return os.path.basename(path) or "document.pdf"
