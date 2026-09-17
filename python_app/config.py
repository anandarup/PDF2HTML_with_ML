"""
Central configuration for PDF2WebView.

All backend endpoints and behavior toggles are read from environment variables
here, in one place, so the app can be pointed at different backends (local disk
vs OCI, in-process thread vs queue, file state vs a state service) without code
changes elsewhere.

IMPORTANT — backward compatibility:
The DEFAULTS below reproduce the current single-VM behavior exactly:
    STATE_BACKEND  = "file"    -> job state in jobs/*.json (today's behavior)
    OUTPUT_BACKEND = "local"   -> output served from ../output on local disk
    QUEUE_BACKEND  = "thread"  -> conversion runs in an in-process thread
So importing and using this module changes nothing until an operator flips a
flag via the environment. Later phases (state service, object storage, queue)
add new backend values behind these same flags.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    """Read an env var, falling back to a default. Empty string -> default."""
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    try:
        return int(val) if val not in (None, "") else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Feature flags — select which backend implementation is active.
# Defaults preserve the current single-VM behavior.
# ---------------------------------------------------------------------------
STATE_BACKEND = _env("STATE_BACKEND", "file").lower()        # file | service
OUTPUT_BACKEND = _env("OUTPUT_BACKEND", "local").lower()     # local | oci
QUEUE_BACKEND = _env("QUEUE_BACKEND", "thread").lower()      # thread | queue

VALID_STATE_BACKENDS = {"file", "service"}
VALID_OUTPUT_BACKENDS = {"local", "oci"}
VALID_QUEUE_BACKENDS = {"thread", "queue"}


# ---------------------------------------------------------------------------
# Filesystem paths (still used by the "file"/"local"/"thread" defaults).
# Resolved relative to this file's directory so behavior matches app.py today.
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = Path(_env("UPLOAD_DIR", str(_BASE_DIR / "uploads")))
OUTPUT_DIR = Path(_env("OUTPUT_DIR", str(_BASE_DIR.parent / "output")))
JOBS_DIR = Path(_env("JOBS_DIR", str(_BASE_DIR / "jobs")))


# ---------------------------------------------------------------------------
# Upload limits (bytes) — unchanged from app.py.
# ---------------------------------------------------------------------------
MAX_CONTENT_LENGTH = _env_int("MAX_CONTENT_LENGTH", 1200 * 1024 * 1024)
UPLOAD_LIMITS: dict = {
    "video": 1200 * 1024 * 1024,  # 1.2 GB
    "audio": 50 * 1024 * 1024,    # 50 MB
    "pptx": 30 * 1024 * 1024,     # 30 MB
    "h5p": 400 * 1024 * 1024,     # 400 MB
    "pdf": 100 * 1024 * 1024,     # 100 MB
    "image": 1 * 1024 * 1024,     # 1 MB
}


# ---------------------------------------------------------------------------
# OCI Object Storage — buckets + region.
# Defaults match the values currently hardcoded in oci_storage.py / s3_publish.py.
# ---------------------------------------------------------------------------
OCI_REGION = _env("OCI_REGION", "ap-hyderabad-1")
OCI_NAMESPACE = _env("OCI_NAMESPACE", "")  # empty -> resolve at runtime via SDK
BUCKET_HTML = _env("BUCKET_HTML", "poc-interactivetxtbk1")
BUCKET_MEDIA = _env("BUCKET_MEDIA", "poc-interactivetxt-media-src-bucket")
BUCKET_VIDEO = _env("BUCKET_VIDEO", "poc-interactivetxt-media-dst-bucket")


# ---------------------------------------------------------------------------
# State Service (Phase 2) — Redis (hot) + JSON DB (durable, refId-indexed).
# Unused while STATE_BACKEND == "file".
# ---------------------------------------------------------------------------
REDIS_URL = _env("REDIS_URL", "")          # e.g. redis://host:6379/0
JSONDB_URL = _env("JSONDB_URL", "")        # connection string / ADB URL
JSONDB_COLLECTION = _env("JSONDB_COLLECTION", "jobs")
STATE_HOT_TTL_SECONDS = _env_int("STATE_HOT_TTL_SECONDS", 3600)


# ---------------------------------------------------------------------------
# Job Queue (Phase 4). Unused while QUEUE_BACKEND == "thread".
# ---------------------------------------------------------------------------
QUEUE_NAME = _env("QUEUE_NAME", "pdf-convert")
QUEUE_ENDPOINT = _env("QUEUE_ENDPOINT", "")        # OCI Queue/Streaming endpoint
QUEUE_OCID = _env("QUEUE_OCID", "")
QUEUE_VISIBILITY_TIMEOUT = _env_int("QUEUE_VISIBILITY_TIMEOUT", 900)  # seconds
QUEUE_MAX_ATTEMPTS = _env_int("QUEUE_MAX_ATTEMPTS", 3)


# ---------------------------------------------------------------------------
# OCR engine selection.
# Defaults to "rapidocr" — the current in-process Docling+RapidOCR path, so
# behavior is unchanged until an operator opts in. "surya" routes OCR to the
# hosted Datalab/Surya API (network call, best run via the job queue).
#
# NOTE ON DATA EGRESS: with OCR_ENGINE=surya, page content is sent to Datalab's
# servers for processing (results are deleted there ~1h after completion).
# DATALAB_PROCESSING_LOCATION pins the region ("us" by default here).
# ---------------------------------------------------------------------------
OCR_ENGINE = _env("OCR_ENGINE", "rapidocr").lower()          # rapidocr | surya
VALID_OCR_ENGINES = {"rapidocr", "surya"}

# Datalab/Surya API. DATALAB_API_KEY is a secret and is read from the
# environment only — it is NEVER echoed by summary() or logged.
DATALAB_API_KEY = _env("DATALAB_API_KEY", "")
DATALAB_API_BASE_URL = _env("DATALAB_API_BASE_URL", "https://www.datalab.to")
DATALAB_MODE = _env("DATALAB_MODE", "balanced").lower()      # fast|balanced|accurate
DATALAB_PROCESSING_LOCATION = _env("DATALAB_PROCESSING_LOCATION", "us").lower()  # us|eu
# End-to-end ceiling for a single conversion round-trip (submit + poll).
DATALAB_TIMEOUT_SECONDS = _env_int("DATALAB_TIMEOUT_SECONDS", 600)
DATALAB_POLL_INTERVAL_SECONDS = _env_int("DATALAB_POLL_INTERVAL_SECONDS", 2)

VALID_DATALAB_MODES = {"fast", "balanced", "accurate"}
VALID_DATALAB_LOCATIONS = {"us", "eu"}


# ---------------------------------------------------------------------------
# App / runtime
# ---------------------------------------------------------------------------
FLASK_ENV = _env("FLASK_ENV", "production")
APP_PORT = _env_int("APP_PORT", 8501)
RETENTION_TTL_DAYS = _env_int("RETENTION_TTL_DAYS", 30)  # Phase 7 cleanup


def summary() -> dict:
    """Return a non-secret snapshot of the active configuration (for /readyz,
    logging, and debugging). Never includes credentials."""
    return {
        "state_backend": STATE_BACKEND,
        "output_backend": OUTPUT_BACKEND,
        "queue_backend": QUEUE_BACKEND,
        "oci_region": OCI_REGION,
        "buckets": {
            "html": BUCKET_HTML,
            "media": BUCKET_MEDIA,
            "video": BUCKET_VIDEO,
        },
        "redis_configured": bool(REDIS_URL),
        "jsondb_configured": bool(JSONDB_URL),
        "queue_name": QUEUE_NAME,
        "queue_configured": bool(QUEUE_ENDPOINT or QUEUE_OCID),
        "ocr_engine": OCR_ENGINE,
        # Report only whether a key is present, never the key itself.
        "datalab_api_key_configured": bool(DATALAB_API_KEY),
        "datalab_mode": DATALAB_MODE,
        "datalab_processing_location": DATALAB_PROCESSING_LOCATION,
    }


def validate() -> list[str]:
    """Return a list of configuration problems (empty = OK).

    Only flags combinations that would actually break at runtime: e.g. a
    non-file state backend selected without an endpoint configured. The
    default (all-local) configuration always validates clean.
    """
    problems: list[str] = []
    if STATE_BACKEND not in VALID_STATE_BACKENDS:
        problems.append(f"STATE_BACKEND '{STATE_BACKEND}' not in {VALID_STATE_BACKENDS}")
    if OUTPUT_BACKEND not in VALID_OUTPUT_BACKENDS:
        problems.append(f"OUTPUT_BACKEND '{OUTPUT_BACKEND}' not in {VALID_OUTPUT_BACKENDS}")
    if QUEUE_BACKEND not in VALID_QUEUE_BACKENDS:
        problems.append(f"QUEUE_BACKEND '{QUEUE_BACKEND}' not in {VALID_QUEUE_BACKENDS}")

    if STATE_BACKEND == "service" and not (REDIS_URL or JSONDB_URL):
        problems.append("STATE_BACKEND=service requires REDIS_URL and/or JSONDB_URL")
    if QUEUE_BACKEND == "queue" and not (QUEUE_ENDPOINT or QUEUE_OCID):
        problems.append("QUEUE_BACKEND=queue requires QUEUE_ENDPOINT or QUEUE_OCID")

    if OCR_ENGINE not in VALID_OCR_ENGINES:
        problems.append(f"OCR_ENGINE '{OCR_ENGINE}' not in {VALID_OCR_ENGINES}")
    if OCR_ENGINE == "surya" and not DATALAB_API_KEY:
        problems.append("OCR_ENGINE=surya requires DATALAB_API_KEY")
    if OCR_ENGINE == "surya" and DATALAB_MODE not in VALID_DATALAB_MODES:
        problems.append(f"DATALAB_MODE '{DATALAB_MODE}' not in {VALID_DATALAB_MODES}")
    if OCR_ENGINE == "surya" and DATALAB_PROCESSING_LOCATION not in VALID_DATALAB_LOCATIONS:
        problems.append(
            f"DATALAB_PROCESSING_LOCATION '{DATALAB_PROCESSING_LOCATION}' "
            f"not in {VALID_DATALAB_LOCATIONS}"
        )
    return problems
