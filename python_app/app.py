"""
PDF2WebView — Web Frontend

A Flask application providing a drag-and-drop interface to upload PDFs
and convert them to interactive HTML documents.

Usage:
    python app.py

Then open http://localhost:5000 in your browser.
"""

from __future__ import annotations

import json
import os
import threading
import traceback
import uuid
from pathlib import Path
from urllib.parse import quote

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
)

from flask import redirect

import config
from state.job_store import build_job_store
from storage.output_store import build_output_store
from queue_backend.job_queue import build_job_queue
# NOTE: `conversion_job` (which imports the heavy ML stack via convert.py) is
# imported LAZILY inside the queue handler, not at module load. This lets the
# API container ship WITHOUT the ML dependencies (docling/paddle/whisper): under
# QUEUE_BACKEND=queue the API only enqueues, and a separate worker image runs
# the conversion. Under the QUEUE_BACKEND=thread default (single-VM), the import
# happens on the first enqueue — where the ML stack is present anyway.

app = Flask(__name__, static_folder="static", template_folder="web_templates")

# Configuration — sourced from config.py (env-driven, with defaults that
# preserve the current single-VM behavior). Kept as module-level names below
# so the rest of app.py is unchanged.
UPLOAD_DIR = config.UPLOAD_DIR
OUTPUT_DIR = config.OUTPUT_DIR

# Global max set to the largest allowed type (video: 1.2 GB)
MAX_CONTENT_LENGTH = config.MAX_CONTENT_LENGTH

# Per-type upload limits (bytes)
UPLOAD_LIMITS: dict = config.UPLOAD_LIMITS

app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# Ensure directories exist
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Job state — accessed through the JobStore abstraction (Phase 1). The default
# FileJobStore reproduces the original jobs/*.json behavior exactly and adds a
# ref_id index; STATE_BACKEND selects the implementation (Phase 2 adds Redis+DB).
JOBS_DIR = config.JOBS_DIR
JOBS_DIR.mkdir(parents=True, exist_ok=True)
CONVERSION_JOBS_LOCK = threading.Lock()  # retained for compatibility (unused by store)

job_store = build_job_store(config)
output_store = build_output_store(config)

# Conversion is dispatched through a queue seam. The default ThreadQueue runs
# the job in a daemon thread in-process (identical to the original behavior);
# QUEUE_BACKEND=queue publishes to OCI Queue and a standalone worker.py consumes.
def _conversion_handler(msg):
    # Lazy import so the API image doesn't require the ML stack at load time.
    from conversion_job import run_conversion_job
    run_conversion_job(msg, job_store)


job_queue = build_job_queue(config, _conversion_handler)


def _job_path(job_id: str) -> Path:
    """Return the filesystem path for a job's state file (compat shim)."""
    return JOBS_DIR / f"{job_id}.json"


def _read_job(job_id: str) -> dict | None:
    """Compat shim → job_store.get_job. Kept so existing callers/tests work."""
    return job_store.get_job(job_id)


def _write_job(job_id: str, data: dict) -> None:
    """Compat shim → job_store.update_job. Kept so existing callers/tests work."""
    job_store.update_job(job_id, data)


@app.route("/")
def index():
    """Serve the main drag-and-drop upload page."""
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    """Liveness probe: the process is up and serving. No dependency checks."""
    return jsonify({"status": "ok"}), 200


@app.route("/readyz")
def readyz():
    """
    Readiness probe: the app is configured correctly and its active backends
    are reachable. With the default (file/local/thread) backends this only
    validates configuration and local paths. When the state/queue backends are
    switched on (later phases), this checks their reachability too, so an
    orchestrator won't route traffic to a replica that can't reach its store.
    """
    checks: dict = {}
    ok = True

    # Configuration validity (always checked).
    cfg_problems = config.validate()
    checks["config"] = "ok" if not cfg_problems else cfg_problems
    if cfg_problems:
        ok = False

    # Local paths used by the default backends.
    if config.STATE_BACKEND == "file":
        writable = os.access(str(JOBS_DIR), os.W_OK)
        checks["job_store"] = "ok" if writable else "jobs dir not writable"
        ok = ok and writable
    else:
        # Phase 2: probe the state service (Redis/JSON DB). Not yet wired, so
        # we report "not_configured" rather than falsely claiming healthy.
        checks["job_store"] = "service backend not yet implemented"

    if config.OUTPUT_BACKEND == "local":
        writable = os.access(str(OUTPUT_DIR), os.W_OK)
        checks["output_store"] = "ok" if writable else "output dir not writable"
        ok = ok and writable
    else:
        checks["output_store"] = "oci backend selected"

    checks["queue_backend"] = config.QUEUE_BACKEND
    checks["config_summary"] = config.summary()

    return jsonify({"status": "ok" if ok else "not_ready", "checks": checks}), (200 if ok else 503)


@app.route("/metrics/queue-depth")
def metrics_queue_depth():
    """
    Report the number of pending conversion jobs, for the worker autoscaler
    (KEDA metrics-api trigger). Under QUEUE_BACKEND=queue this reflects the
    queue's visible-message count; otherwise it derives a best-effort count of
    jobs still in a non-terminal state from the state store. Always 200 so the
    scaler treats an unreachable backend as "no load" rather than erroring.
    """
    depth = 0
    try:
        depth = job_queue.depth()
    except Exception:
        depth = 0
    return jsonify({"queue_depth": depth}), 200


@app.route("/metrics")
def metrics():
    """Lightweight operational metrics snapshot (JSON). A Prometheus exporter
    can be layered later; this keeps the app dependency-free."""
    try:
        depth = job_queue.depth()
    except Exception:
        depth = None
    return jsonify({
        "queue_depth": depth,
        "backends": config.summary(),
    }), 200


@app.route("/convert", methods=["POST"])
def convert_pdf():
    """
    Handle PDF upload and kick off conversion in the background.

    Accepts a multipart form upload with a 'pdf' file field. The upload
    itself is handled synchronously (so the browser's own upload-progress
    events stay meaningful), but the actual Docling extraction + HTML
    generation runs in a background thread — this request returns
    immediately with a job_id; the frontend polls GET /convert-status/<id>
    for real stage-by-stage progress and the final result.
    """
    if "pdf" not in request.files:
        return jsonify({"error": "No PDF file provided"}), 400

    file = request.files["pdf"]

    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted"}), 400

    # Generate unique ID for this conversion
    job_id = str(uuid.uuid4())[:8]
    pdf_stem = Path(file.filename).stem

    # Accept optional external reference ID (for iframe integration)
    ref_id = request.form.get("ref_id", "").strip() or None

    # Save uploaded file via the output store (local disk today; an uploads/
    # prefix under OUTPUT_BACKEND=oci). Returns a reference the worker reads.
    upload_path = Path(output_store.put_upload(f"{job_id}_{file.filename}", file))

    job_store.create_job(job_id, {
        "status": "processing",
        "stage": "queued",
        "detail": "Upload complete, starting conversion...",
        "progress": {"stage": "queued"},
        "result": None,
        "error": None,
        "ref_id": ref_id,
        "edit_url": None,
        "render_url": None,
    })

    # Dispatch the conversion through the queue seam. The default ThreadQueue
    # runs run_conversion_job() in a daemon thread now (identical to before);
    # under QUEUE_BACKEND=queue this publishes a message and a separate
    # worker.py consumes it — off the request path entirely.
    job_queue.enqueue({
        "job_id": job_id,
        "upload_ref": str(upload_path),
        "ref_id": ref_id,
        "pdf_stem": pdf_stem,
        "attempt": 0,
    })

    return jsonify({"job_id": job_id}), 202


@app.route("/convert-status/<job_id>")
def convert_status(job_id: str):
    """Poll the status/progress of a background conversion job."""
    job = job_store.get_job(job_id)

    if job is None:
        return jsonify({"error": "Unknown job_id"}), 404

    return jsonify(job)


@app.route("/api/lookup/<ref_id>")
def lookup_by_ref_id(ref_id: str):
    """
    Look up a job by its external reference ID (refId).

    Used by the parent application's backend to poll job status
    and retrieve edit/render URLs without needing to know our internal job_id.

    Returns:
        200: { status, editUrl, renderUrl, title, pageCount, imageCount, updatedAt }
        404: if refId is unknown
    """
    import re as _re

    # Validate refId shape (UUID-like, to prevent directory traversal / abuse)
    if not ref_id or not _re.match(r'^[a-zA-Z0-9\-]{8,64}$', ref_id):
        return jsonify({"error": "Invalid refId format"}), 400

    # Indexed lookup by ref_id (O(1) via the job store's ref_id index).
    matched_job = job_store.find_by_ref_id(ref_id)

    if matched_job is None:
        return jsonify({"error": "Unknown refId"}), 404

    # Map internal status to the integration's expected states
    internal_status = matched_job.get("status", "processing")
    if matched_job.get("render_url"):
        ext_status = "published"
    elif internal_status == "done":
        ext_status = "ready-for-edit"
    elif internal_status == "error":
        ext_status = "error"
    else:
        ext_status = "converting"

    # Extract metadata from result if available
    result = matched_job.get("result") or {}
    title = result.get("title", "")
    page_count = result.get("page_count", 0)
    image_count = result.get("image_count", 0)

    return jsonify({
        "status": ext_status,
        "editUrl": matched_job.get("edit_url"),
        "renderUrl": matched_job.get("render_url"),
        "title": title,
        "pageCount": page_count,
        "imageCount": image_count,
    }), 200


@app.route("/api/sections/<ref_id>")
def get_sections_by_ref_id(ref_id: str):
    """
    Return the heading/section structure for a document, keyed by refId.

    Parses the HTML file's headings (h1-h3) and returns a hierarchical
    list suitable for building a sidebar/TOC in the parent CMS.

    Returns:
        200: { title, sections: [{ level, id, title, subsections: [...] }] }
        404: if refId is unknown or document not found
    """
    import re as _re
    from urllib.parse import unquote

    # Validate refId format
    if not ref_id or not _re.match(r'^[a-zA-Z0-9\-]{8,64}$', ref_id):
        return jsonify({"error": "Invalid refId format"}), 400

    # Find job by ref_id (indexed lookup via the job store).
    matched_job = job_store.find_by_ref_id(ref_id)

    if matched_job is None:
        return jsonify({"error": "Unknown refId"}), 404

    # Get the edit_url to find the HTML file
    edit_url = matched_job.get("edit_url")
    if not edit_url:
        return jsonify({"error": "Document not ready yet"}), 404

    # Parse the edit_url to get the file path: /output/<job_dir>/<filename>
    # edit_url is like: /output/a88b7d1e_Chap%205/a88b7d1e_Chap%205.html
    url_parts = edit_url.strip("/").split("/")
    if len(url_parts) < 3 or url_parts[0] != "output":
        return jsonify({"error": "Invalid edit URL format"}), 500

    job_dir = unquote(url_parts[1])
    filename = unquote(url_parts[2])
    html_path = OUTPUT_DIR.resolve() / job_dir / filename

    if not html_path.exists():
        return jsonify({"error": "HTML file not found"}), 404

    # Parse headings from the HTML
    html_content = html_path.read_text(encoding="utf-8")

    # Extract content between <article class="document-body">...</article>
    body_match = _re.search(
        r'<article class="document-body">(.*?)</article>',
        html_content, _re.DOTALL
    )
    body_html = body_match.group(1) if body_match else html_content

    # Extract all h1-h3 headings with their IDs and text
    heading_pattern = _re.compile(
        r'<h([1-3])([^>]*)>(.*?)</h\1>',
        _re.IGNORECASE | _re.DOTALL
    )

    sections = []
    for match in heading_pattern.finditer(body_html):
        level = int(match.group(1))
        attrs = match.group(2)
        raw_text = match.group(3)

        # Extract id from attributes
        id_match = _re.search(r'id="([^"]*)"', attrs)
        heading_id = id_match.group(1) if id_match else ""

        # Strip HTML tags from heading text (remove block-controls, etc.)
        text = _re.sub(r'<[^>]+>', '', raw_text).strip()

        if not text:
            continue

        # Generate an ID if missing
        if not heading_id:
            heading_id = _re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')

        sections.append({
            "level": level,
            "id": heading_id,
            "title": text,
        })

    # Build hierarchical structure (h2 = section, h3 = subsection under previous h2)
    hierarchy = []
    current_section = None

    for s in sections:
        if s["level"] <= 2:
            current_section = {
                "level": s["level"],
                "id": s["id"],
                "title": s["title"],
                "subsections": [],
            }
            hierarchy.append(current_section)
        elif s["level"] == 3 and current_section:
            current_section["subsections"].append({
                "level": s["level"],
                "id": s["id"],
                "title": s["title"],
            })
        else:
            # h3 with no parent h2 — treat as top-level
            hierarchy.append({
                "level": s["level"],
                "id": s["id"],
                "title": s["title"],
                "subsections": [],
            })

    # Get document title
    result = matched_job.get("result") or {}
    doc_title = result.get("title", "")

    return jsonify({
        "title": doc_title,
        "sectionCount": len(hierarchy),
        "totalHeadings": len(sections),
        "sections": hierarchy,
    }), 200


@app.route("/api/sections-by-path/<path:job_dir>/<path:filename>")
def get_sections_by_path(job_dir: str, filename: str):
    """
    Return heading structure for a document by its file path.

    Alternative to /api/sections/<refId> — works without a refId,
    using the same job_dir/filename from the edit or render URL.
    """
    import re as _re
    from urllib.parse import unquote

    job_dir = unquote(job_dir)
    filename = unquote(filename)

    if not filename.lower().endswith(".html"):
        return jsonify({"error": "Only HTML files supported"}), 400
    if ".." in job_dir or ".." in filename:
        return jsonify({"error": "Invalid path"}), 403

    html_path = OUTPUT_DIR.resolve() / job_dir / filename
    if not html_path.exists():
        return jsonify({"error": "File not found"}), 404

    html_content = html_path.read_text(encoding="utf-8")

    # Extract title
    title_match = _re.search(r'<h1 class="document-title">(.*?)</h1>', html_content, _re.DOTALL)
    doc_title = _re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""

    # Extract body content
    body_match = _re.search(
        r'<article class="document-body">(.*?)</article>',
        html_content, _re.DOTALL
    )
    body_html = body_match.group(1) if body_match else html_content

    # Parse headings
    heading_pattern = _re.compile(
        r'<h([1-3])([^>]*)>(.*?)</h\1>',
        _re.IGNORECASE | _re.DOTALL
    )

    sections = []
    for match in heading_pattern.finditer(body_html):
        level = int(match.group(1))
        attrs = match.group(2)
        raw_text = match.group(3)

        id_match = _re.search(r'id="([^"]*)"', attrs)
        heading_id = id_match.group(1) if id_match else ""
        text = _re.sub(r'<[^>]+>', '', raw_text).strip()
        if not text:
            continue
        if not heading_id:
            heading_id = _re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')

        sections.append({"level": level, "id": heading_id, "title": text})

    # Build hierarchy
    hierarchy = []
    current_section = None
    for s in sections:
        if s["level"] <= 2:
            current_section = {"level": s["level"], "id": s["id"], "title": s["title"], "subsections": []}
            hierarchy.append(current_section)
        elif s["level"] == 3 and current_section:
            current_section["subsections"].append({"level": s["level"], "id": s["id"], "title": s["title"]})
        else:
            hierarchy.append({"level": s["level"], "id": s["id"], "title": s["title"], "subsections": []})

    return jsonify({
        "title": doc_title,
        "sectionCount": len(hierarchy),
        "totalHeadings": len(sections),
        "sections": hierarchy,
    }), 200


@app.route("/api/glossary-highlight", methods=["POST"])
def apply_glossary_highlight():
    """
    Apply glossary term highlighting to an HTML string.

    Accepts JSON with:
    - html: raw HTML string to process
    - glossary: array of { term, definition } objects
    - first_occurrence_only: boolean (default true)

    Returns the HTML with glossary terms wrapped in accessible <dfn> tags.
    """
    from glossary_highlight import highlight_glossary_terms, GLOSSARY_CSS

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    html = data.get("html", "")
    glossary = data.get("glossary", [])
    first_only = data.get("first_occurrence_only", False)
    max_highlights = data.get("max_highlights_per_term", 0)

    if not html:
        return jsonify({"error": "html field is required"}), 400
    if not glossary:
        return jsonify({"error": "glossary array is required"}), 400

    try:
        result_html = highlight_glossary_terms(
            html, glossary, first_occurrence_only=first_only, max_highlights_per_term=max_highlights
        )
        return jsonify({
            "success": True,
            "html": result_html,
            "css": GLOSSARY_CSS,
            "terms_count": len(glossary),
        }), 200
    except Exception as e:
        return jsonify({"error": f"Processing failed: {str(e)}"}), 500


@app.route("/upload-media/<path:job_dir>", methods=["POST"])
def upload_media(job_dir: str):
    """
    Handle media file uploads for a converted document.

    Saves the uploaded file to the document's media/ subdirectory
    and returns the relative URL for embedding.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    # Enforce per-type size limit
    media_type = request.form.get("type", "video")
    size_limit = UPLOAD_LIMITS.get(media_type, UPLOAD_LIMITS["video"])

    # Check file size by reading content length or seeking
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)     # Reset to start

    if file_size > size_limit:
        limit_mb = size_limit // (1024 * 1024)
        return jsonify({
            "error": f"File too large. Maximum for {media_type} is {limit_mb} MB."
        }), 413

    # Validate file extension for image uploads
    if media_type == "image":
        allowed_extensions = (".png", ".jpg", ".jpeg")
        if not file.filename.lower().endswith(allowed_extensions):
            return jsonify({
                "error": "Only PNG and JPG images are allowed."
            }), 400

    # H5P packages are ZIP archives; anything else cannot be unpacked or played.
    # (.html is still accepted here because the per-section media chip allows
    # pointing at a single pre-built H5P page.)
    if media_type == "h5p":
        allowed_extensions = (".h5p", ".zip", ".html", ".htm")
        if not file.filename.lower().endswith(allowed_extensions):
            return jsonify({
                "error": "Choose a .h5p package (a .zip export also works)."
            }), 400

    # A Virtual Lab is a static site build, uploaded as a .zip.
    if media_type == "vlab" and not file.filename.lower().endswith(".zip"):
        return jsonify({
            "error": "Choose the Virtual Lab bundle as a .zip file."
        }), 400

    # Validate the target directory exists and is within OUTPUT_DIR
    # URL-decode the job_dir to handle double-encoding from browser JS
    from urllib.parse import unquote
    decoded_job_dir = unquote(job_dir)
    target_dir = OUTPUT_DIR.resolve() / decoded_job_dir / "media"
    try:
        target_dir.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return jsonify({"error": "Invalid path"}), 403

    target_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename: keep only safe characters
    import re as _re
    safe_name = _re.sub(r"[^\w\-.]", "_", file.filename)
    if not safe_name:
        safe_name = "upload"

    # Avoid overwrites by appending a short suffix if needed
    target_path = target_dir / safe_name
    if target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        counter = 1
        while target_path.exists():
            target_path = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    file.save(str(target_path))

    # For video uploads, optimize for web streaming by moving the moov atom
    # to the beginning of the file (enables progressive playback without
    # downloading the entire file first)
    if media_type == "video" and target_path.suffix.lower() in (".mp4", ".m4v", ".mov"):
        _optimize_video_for_streaming(target_path)

    # For H5P uploads, extract the archive — the player needs the unpacked
    # folder (it fetches <folder>/h5p.json), not the archive itself.
    h5p_folder = ""
    if media_type == "h5p" and target_path.suffix.lower() in (".h5p", ".zip"):
        try:
            h5p_folder = _extract_h5p(target_path)
        except ValueError as exc:
            # Unusable archive: drop it rather than leaving a file the player
            # cannot load, and tell the editor why.
            target_path.unlink(missing_ok=True)
            return jsonify({"error": str(exc)}), 400

    # A Virtual Lab is embedded in an iframe pointed at its index.html, so the
    # bundle has to be unpacked and its entry point located.
    vlab_entry = ""
    vlab_warning = ""
    if media_type == "vlab" and target_path.suffix.lower() == ".zip":
        try:
            result = _extract_vlab(target_path)
            vlab_entry = result["entry"]
            vlab_warning = result["warning"]
        except ValueError as exc:
            target_path.unlink(missing_ok=True)
            return jsonify({"error": str(exc)}), 400

    # Return the relative URL from the HTML file's perspective
    relative_url = f"media/{target_path.name}"
    if h5p_folder:
        relative_url = f"media/{h5p_folder}"
    elif vlab_entry:
        relative_url = f"media/{vlab_entry}"

    payload = {
        "success": True,
        "url": relative_url,
        "filename": target_path.name,
    }
    if vlab_warning:
        payload["warning"] = vlab_warning

    return jsonify(payload), 201


@app.route("/output/<path:job_dir>/<path:filename>")
def serve_output(job_dir: str, filename: str):
    """Serve converted HTML and associated assets (images).

    Routed through the output store: local disk (send_from_directory) or, under
    OUTPUT_BACKEND=oci, a 302 redirect to the bucket/CDN URL.
    """
    action = output_store.serve(job_dir, filename)
    if action.kind == "redirect":
        return redirect(action.url, code=302)
    if action.kind == "missing":
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(action.directory, action.filename)


def sanitize_document_title(raw_title) -> str:
    """
    Reduce a client-supplied chapter title to safe, single-line plain text.

    The title is rendered as element text in both <h1 class="document-title">
    and <head><title>, so markup is never legitimate here: any tags are
    stripped, and the remaining characters are collapsed onto one line. The
    return value is NOT yet HTML-escaped — callers must escape before
    inserting it into markup (see _apply_document_title).

    Returns "" for a value that is empty, whitespace-only, or markup-only;
    callers treat that as "reject, keep the previous title".
    """
    import re

    if raw_title is None:
        return ""
    text = str(raw_title)
    # Drop comments and anything tag-shaped rather than escaping it into visible
    # junk. The pattern deliberately requires a letter (or !/?) after "<" so a
    # legitimate "<0>" or "a < b" in a title is preserved as text — it is
    # HTML-escaped later, so leaving it in is safe.
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'</?[a-zA-Z][^>]*>|<[!?][^>]*>', '', text, flags=re.DOTALL)
    # Collapse newlines/tabs/NBSP (contenteditable loves to insert these).
    text = text.replace('\u00a0', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    # Defensive cap — a title is a heading, not a document.
    return text[:300]


def _apply_document_title(html_content: str, clean_title: str) -> tuple[str, int]:
    """
    Write `clean_title` into the document's <h1 class="document-title"> and
    <head><title>, HTML-escaping it exactly once.

    The replacements use callable replacers, so backslashes and \\g<...> in the
    title are never interpreted as re.sub group references — a title such as
    'Tom & Jerry \\1 \\g<0>' round-trips as literal text.

    Returns (updated_html, headings_replaced). headings_replaced == 0 means the
    document-title heading was not found and nothing was changed.
    """
    import html as _html
    import re

    escaped = _html.escape(clean_title, quote=True)

    updated, h1_count = re.subn(
        r'(<h1[^>]*class="document-title"[^>]*>)(.*?)(</h1>)',
        lambda m: m.group(1) + escaped + m.group(3),
        html_content, count=1, flags=re.DOTALL,
    )
    if h1_count == 0:
        return html_content, 0

    # Keep the browser tab / downstream metadata consistent with the heading.
    updated = re.sub(
        r'(<title[^>]*>)(.*?)(</title>)',
        lambda m: m.group(1) + escaped + m.group(3),
        updated, count=1, flags=re.DOTALL | re.IGNORECASE,
    )
    return updated, h1_count


def _sync_job_record_title(job_dir: str, clean_title: str) -> None:
    """
    Mirror an edited title into the job record's result.title.

    /api/status/<refId> and /api/sections/<refId> serve result.title to the
    embedding CMS, so leaving it at the auto-generated value would show a stale
    title in listings after an edit. Best-effort: a missing/legacy job record is
    not an error for the save itself.
    """
    try:
        job_id = job_dir.split("_")[0] if "_" in job_dir else job_dir[:8]
        job = _read_job(job_id)
        if not job:
            return
        result = job.get("result")
        if not isinstance(result, dict):
            return
        if result.get("title") == clean_title:
            return
        result["title"] = clean_title
        job["result"] = result
        _write_job(job_id, job)
    except Exception:  # noqa: BLE001 — never fail a content save on bookkeeping
        traceback.print_exc()


@app.route("/output/<path:job_dir>/<path:filename>", methods=["PUT"])
def save_output(job_dir: str, filename: str):
    """
    Save edited HTML content back to the output file.

    Accepts JSON body with { "body_html": "<updated content>" } and an optional
    { "title": "<chapter title>" }. Replaces the article content in the saved
    HTML file, and — when a title is supplied — the document heading and
    <head><title> too.
    """
    if not filename.lower().endswith(".html"):
        return jsonify({"error": "Only HTML files can be edited"}), 400

    data = request.get_json()
    if not data or "body_html" not in data:
        return jsonify({"error": "Missing body_html in request"}), 400

    # Read current HTML via the output store (local disk or OCI mirror/bucket).
    # A path-traversal / missing-file returns None.
    if ".." in job_dir or ".." in filename:
        return jsonify({"error": "Invalid path"}), 403

    html_content = output_store.read_html(job_dir, filename)
    if html_content is None:
        return jsonify({"error": "File not found"}), 404

    # An edited chapter title is optional; when present it must survive the
    # save, so validate it BEFORE any file write (all-or-nothing).
    clean_title = None
    if "title" in data:
        clean_title = sanitize_document_title(data.get("title"))
        if not clean_title:
            return jsonify({"error": "Title cannot be empty"}), 400

    try:
        # Replace the article body content between the markers
        import re
        new_body = data["body_html"]

        # Match the <article class="document-body">...</article> section
        pattern = r'(<article class="document-body">)(.*?)(</article>)'
        replacement = r'\g<1>' + new_body.replace('\\', '\\\\') + r'\g<3>'

        updated_html, count = re.subn(
            pattern, replacement, html_content, count=1, flags=re.DOTALL
        )

        if count == 0:
            return jsonify({"error": "Could not locate content section"}), 500

        # Rebuild the TOC from the new headings
        updated_html = _rebuild_toc_in_html(updated_html, new_body)

        # Apply the edited chapter title (heading + <head><title>).
        title_saved = None
        if clean_title:
            updated_html, h1_count = _apply_document_title(updated_html, clean_title)
            if h1_count == 0:
                return jsonify({"error": "Could not locate document title"}), 500
            title_saved = clean_title

        # Persist via the output store (local disk, or write-back to the bucket).
        output_store.write_html(job_dir, filename, updated_html)

        if title_saved:
            _sync_job_record_title(job_dir, title_saved)

        payload = {"success": True, "message": "Content saved"}
        if title_saved:
            payload["title"] = title_saved
        return jsonify(payload), 200

    except (OSError, ValueError) as e:
        return jsonify({"error": f"File write failed: {e}"}), 500


@app.route("/publish", methods=["POST"])
def publish_for_learners():
    """
    Publish a document to S3 for learner access.

    Uploads HTML to poc-interactivetxtbk1, media files to
    poc-interactivetxt-media-src-bucket, and streaming videos to
    poc-interactivetxt-media-dst-bucket. Returns the public learner URL.
    """
    from urllib.parse import unquote

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    job_dir = unquote(data.get("job_dir", ""))
    filename = unquote(data.get("filename", ""))

    if not job_dir or not filename:
        return jsonify({"error": "job_dir and filename are required"}), 400

    if not filename.lower().endswith(".html"):
        return jsonify({"error": "Only HTML files can be published"}), 400

    # Security: prevent path traversal
    if ".." in job_dir or ".." in filename or "/" in filename:
        return jsonify({"error": "Invalid path"}), 403

    try:
        from s3_publish import publish_document

        result = publish_document(job_dir, filename)

        # Record render_url in the job state for lookup-by-refId
        render_url = result["html_url"]
        job_id_from_dir = job_dir.split("_")[0] if "_" in job_dir else job_dir[:8]
        with CONVERSION_JOBS_LOCK:
            job = _read_job(job_id_from_dir)
            if job:
                job["render_url"] = render_url
                job["status"] = "published"
                _write_job(job_id_from_dir, job)

        return jsonify({
            "success": True,
            "url": render_url,
            "media_count": result["media_uploaded"],
            "video_count": result["videos_uploaded"],
        }), 200

    except FileNotFoundError:
        # The exception text carries the absolute server path — keep that in
        # the log, not in the editor's error dialog.
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": "This document could not be found on the server.",
        }), 404
    except Exception:
        # Storage-layer failures name the backend and its buckets (e.g. an OCI
        # /S3 "NoSuchBucket" or a credentials error). That is operator
        # information, not something to put in front of an editor, so the
        # detail goes to the server log and the UI gets a generic message.
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": (
                "Couldn't generate the HTML for learners. "
                "Please try again — the server log has the details."
            ),
        }), 500


@app.route("/export-cms", methods=["POST"])
def export_cms():
    """
    Export document content to a CMS (Strapi or WordPress).

    Accepts JSON with platform config and HTML content.
    Uploads media files to the CMS, replaces local paths with CMS URLs,
    then pushes the content via the CMS REST API.
    """
    import requests as http_client

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    platform = data.get("platform")
    base_url = data.get("base_url", "").rstrip("/")
    title = data.get("title", "Untitled")
    body_html = data.get("body_html", "")
    job_dir = data.get("job_dir", "")

    if not base_url:
        return jsonify({"error": "CMS base URL is required"}), 400

    # Upload media files and replace local paths with CMS URLs
    if job_dir:
        from urllib.parse import unquote
        decoded_dir = unquote(job_dir)
        local_dir = OUTPUT_DIR.resolve() / decoded_dir
        body_html = _upload_media_to_cms(
            body_html, local_dir, base_url, data, platform, http_client
        )

    try:
        if platform == "strapi":
            return _export_to_strapi_diksha(data, base_url, title, body_html, job_dir, http_client)
        elif platform == "wordpress":
            return _export_to_wordpress(data, base_url, title, body_html, http_client)
        else:
            return jsonify({"error": f"Unsupported platform: {platform}"}), 400
    except http_client.exceptions.ConnectionError:
        return jsonify({"error": "Cannot connect to CMS. Check the URL."}), 502
    except http_client.exceptions.Timeout:
        return jsonify({"error": "CMS request timed out."}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _upload_media_to_cms(
    body_html: str,
    local_dir: Path,
    base_url: str,
    data: dict,
    platform: str,
    http_client,
) -> str:
    """
    Find all local media references in HTML, upload them to the CMS media
    library, and replace local paths with CMS-hosted URLs.

    Handles: images (src), videos (src), audio (src), data-media-src attributes.
    """
    import re as _re

    # Pattern to find local file references (relative paths)
    # Matches src="images/..." or src="media/..." or data-media-src="media/..."
    local_path_pattern = _re.compile(
        r'((?:src|data-media-src)\s*=\s*")((?:images|media)/[^"]+)(")',
        _re.IGNORECASE,
    )

    uploaded_cache: dict = {}

    def replace_with_cms_url(match: _re.Match) -> str:
        prefix = match.group(1)
        relative_path = match.group(2)
        suffix = match.group(3)

        # Skip external URLs and data URIs
        if relative_path.startswith(("http://", "https://", "data:")):
            return match.group(0)

        # Check cache
        if relative_path in uploaded_cache:
            return prefix + uploaded_cache[relative_path] + suffix

        # Resolve to absolute local path
        local_file = local_dir / relative_path
        if not local_file.exists() or not local_file.is_file():
            return match.group(0)

        # Upload to CMS
        cms_url = _upload_single_file(local_file, base_url, data, platform, http_client)
        if cms_url:
            uploaded_cache[relative_path] = cms_url
            return prefix + cms_url + suffix

        return match.group(0)

    return local_path_pattern.sub(replace_with_cms_url, body_html)


def _upload_single_file(
    file_path: Path, base_url: str, data: dict, platform: str, http_client
) -> str:
    """Upload a single file to the CMS media library. Returns the public URL or empty string."""
    try:
        if platform == "strapi":
            api_token = data.get("api_token", "")
            headers = {"Authorization": f"Bearer {api_token}"}
            with open(file_path, "rb") as f:
                files = {"files": (file_path.name, f)}
                resp = http_client.post(
                    f"{base_url}/api/upload",
                    headers=headers,
                    files=files,
                    timeout=120,
                )
            if resp.status_code in (200, 201):
                resp_data = resp.json()
                if isinstance(resp_data, list) and len(resp_data) > 0:
                    url = resp_data[0].get("url", "")
                    # Strapi returns relative URLs — prepend base
                    if url and not url.startswith("http"):
                        url = base_url + url
                    return url
        elif platform == "wordpress":
            username = data.get("username", "")
            password = data.get("password", "")
            import mimetypes
            mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            headers = {
                "Content-Disposition": f'attachment; filename="{file_path.name}"',
                "Content-Type": mime_type,
            }
            with open(file_path, "rb") as f:
                resp = http_client.post(
                    f"{base_url}/wp-json/wp/v2/media",
                    headers=headers,
                    data=f,
                    auth=(username, password),
                    timeout=120,
                )
            if resp.status_code in (200, 201):
                resp_data = resp.json()
                return resp_data.get("source_url", "")
    except Exception:
        pass  # Best-effort: if upload fails, keep the local path

    return ""


def _export_to_strapi_diksha(data: dict, base_url: str, title: str, body_html: str, job_dir: str, http_client) -> tuple:
    """Export to DIKSHA Strapi CMS using content-manager API with dynamic zones."""
    from urllib.parse import unquote
    from strapi_export import export_to_strapi_diksha

    jwt_token = data.get("api_token", "")
    textbook_id = data.get("textbook_id", "")
    chapter_order = data.get("chapter_order", 1)

    if not jwt_token:
        return jsonify({"error": "JWT token is required"}), 400
    if not textbook_id:
        return jsonify({"error": "Textbook Document ID is required"}), 400

    # Resolve local directory for media uploads
    local_dir = None
    if job_dir:
        decoded_dir = unquote(job_dir)
        local_dir = OUTPUT_DIR.resolve() / decoded_dir

    result = export_to_strapi_diksha(
        base_url=base_url,
        jwt_token=jwt_token,
        textbook_document_id=textbook_id,
        chapter_title=title,
        chapter_order=chapter_order,
        body_html=body_html,
        local_dir=local_dir,
        http_client=http_client,
    )

    if result.get("success"):
        return jsonify({
            "success": True,
            "message": f"Created chapter with {result['sections_created']}/{result['sections_total']} sections, {result['media_uploaded']} media files uploaded",
            "url": f"{base_url}/admin/content-manager/collection-types/api::chapter.chapter/{result['chapter_document_id']}",
        }), 200
    else:
        return jsonify({"error": result.get("error", "Export failed")}), 400


def _export_to_wordpress(data: dict, base_url: str, title: str, body_html: str, http_client) -> tuple:
    """Push content to WordPress REST API (posts endpoint)."""
    from strapi_export import _strip_editor_ui
    body_html = _strip_editor_ui(body_html)

    username = data.get("username", "")
    password = data.get("password", "")

    endpoint = f"{base_url}/wp-json/wp/v2/posts"
    payload = {
        "title": title,
        "content": body_html,
        "status": "draft",
    }

    resp = http_client.post(
        endpoint,
        json=payload,
        auth=(username, password),
        timeout=30,
    )

    if resp.status_code in (200, 201):
        resp_data = resp.json()
        post_id = resp_data.get("id", "")
        post_link = resp_data.get("link", "")
        return jsonify({
            "success": True,
            "url": post_link,
            "message": f"Created WordPress draft post #{post_id}",
        }), 200
    else:
        error_msg = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
        return jsonify({"error": f"WordPress error: {error_msg}"}), resp.status_code


def _extract_h5p(h5p_path: Path) -> str:
    """
    Extract an H5P file (ZIP archive) into a folder for browser playback.

    H5P files are ZIP archives containing HTML5 interactive content.
    They must be extracted to be served to the h5p-standalone player, which
    fetches <folder>/h5p.json.

    The archive is uploaded by an editor, so it is treated as untrusted input:
    members that would escape the extraction directory are rejected (Zip
    Slip), and the uncompressed size and entry count are capped so a small
    archive cannot fill the disk.

    Returns:
        The folder name (relative to media/) where content was extracted.

    Raises:
        ValueError: If the archive is not a usable H5P package. The message is
            safe to show to the user.
    """
    folder_name = h5p_path.stem
    extract_dir = (h5p_path.parent / folder_name).resolve()

    names = _extract_archive_safely(
        h5p_path, extract_dir, kind="H5P package", marker="h5p.json"
    )

    # The player is pointed at the directory holding h5p.json. That is normally
    # the archive root, but a hand-zipped package often nests everything one
    # level down, so accept that shape too.
    inner_dir = _package_inner_dir(names, "h5p.json")
    return f"{folder_name}/{inner_dir}" if inner_dir else folder_name


def _extract_vlab(zip_path: Path) -> dict:
    """
    Extract a Virtual Lab bundle (ZIP archive) for embedding.

    A Virtual Lab is a self-contained static build of a single-page app
    (typically Create React App: index.html plus hashed bundles under
    static/js and static/css). It is embedded in an iframe pointed at its
    index.html, so extraction must find that entry point.

    Returns:
        dict with:
            entry:   path to index.html, relative to media/
            warning: user-facing note if the build cannot work from a
                     subdirectory, or "" when it is fine.

    Raises:
        ValueError: If the archive is not a usable Virtual Lab bundle. The
            message is safe to show to the user.
    """
    folder_name = zip_path.stem
    extract_dir = (zip_path.parent / folder_name).resolve()

    names = _extract_archive_safely(
        zip_path, extract_dir, kind="Virtual Lab bundle", marker="index.html"
    )

    inner_dir = _package_inner_dir(names, "index.html")
    lab_root = extract_dir / inner_dir if inner_dir else extract_dir
    warning = _make_static_bundle_relative(lab_root)

    entry = f"{folder_name}/{inner_dir}/index.html" if inner_dir else f"{folder_name}/index.html"
    return {"entry": entry, "warning": warning}


def _extract_archive_safely(
    archive_path: Path, extract_dir: Path, *, kind: str, marker: str
) -> set:
    """
    Extract an uploaded ZIP archive, treating its contents as untrusted.

    Members that would land outside extract_dir are rejected (Zip Slip), and
    the entry count and total uncompressed size are capped so a small archive
    cannot fill the disk. The archive must contain `marker` somewhere at the
    root or one level down, which is what identifies it as the expected kind.

    Args:
        archive_path: The uploaded .zip/.h5p file.
        extract_dir: Directory to extract into (created if needed).
        kind: Human-readable package kind, used in error messages.
        marker: File that must be present (e.g. "h5p.json", "index.html").

    Returns:
        The set of member names in the archive.

    Raises:
        ValueError: With a message safe to show to the user.
    """
    import zipfile

    MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
    MAX_ENTRIES = 20000

    try:
        with zipfile.ZipFile(str(archive_path), "r") as zf:
            entries = zf.infolist()

            if len(entries) > MAX_ENTRIES:
                raise ValueError(
                    f"This {kind} contains too many files to unpack safely."
                )

            total = sum(entry.file_size for entry in entries)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError(
                    f"This {kind} expands to more than 2 GB and was not unpacked."
                )

            for entry in entries:
                # Reject absolute paths, drive letters and ../ traversal by
                # checking where the member would actually land.
                destination = (extract_dir / entry.filename).resolve()
                try:
                    destination.relative_to(extract_dir)
                except ValueError:
                    raise ValueError(
                        f"This {kind} contains unsafe file paths and was not unpacked."
                    ) from None

            names = {entry.filename for entry in entries}
            if _package_inner_dir(names, marker) is None:
                raise ValueError(
                    f"This does not look like a {kind} (no {marker} inside)."
                )

            extract_dir.mkdir(parents=True, exist_ok=True)
            zf.extractall(str(extract_dir))
            return names
    except zipfile.BadZipFile:
        raise ValueError(
            f"That file isn't a valid {kind} (unreadable archive)."
        ) from None
    except OSError:
        raise ValueError(f"The {kind} could not be unpacked on the server.") from None


def _package_inner_dir(names: set, marker: str):
    """
    Locate the directory holding `marker` within an archive's member names.

    Returns "" when the marker sits at the archive root, the single top-level
    directory name when everything is nested one level down, or None when the
    marker cannot be found unambiguously.
    """
    if marker in names:
        return ""
    nested = [
        name for name in names
        if name.endswith(f"/{marker}") and name.count("/") == 1
    ]
    if len(nested) == 1:
        return nested[0].rsplit("/", 1)[0]
    return None


def _make_static_bundle_relative(lab_root: Path) -> str:
    """
    Make a static SPA build loadable from a subdirectory.

    Create React App builds with the default configuration reference their
    assets from the site root ("/static/js/main.<hash>.js"). A Virtual Lab is
    served from output/<job>/media/<lab>/, so those absolute paths resolve
    against the wrong place and the lab renders blank. Rewriting them in
    index.html to "./static/..." fixes the initial load.

    Chunks requested at runtime use the publicPath baked into the bundle, which
    cannot be rewritten reliably. If any remains, the lab is reported as needing
    a rebuild with PUBLIC_URL="." rather than silently half-working.

    Returns:
        A user-facing warning, or "" if the bundle is fine.
    """
    import re as _re

    index_path = lab_root / "index.html"
    if not index_path.is_file():
        return ""

    try:
        html = index_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    # Only rewrite root-absolute references to files that exist in the bundle,
    # so links to genuinely external resources are left alone.
    def to_relative(match) -> str:
        attr, quote_char, path = match.group(1), match.group(2), match.group(3)
        if (lab_root / path.lstrip("/")).exists():
            return f'{attr}={quote_char}./{path.lstrip("/")}'
        return match.group(0)

    rewritten = _re.sub(
        r'\b(src|href)=(["\'])(/(?:static|manifest\.json|favicon\.ico|logo[^"\']*|asset[^"\']*)[^"\']*)',
        to_relative,
        html,
    )

    if rewritten != html:
        try:
            index_path.write_text(rewritten, encoding="utf-8")
        except OSError:
            return (
                "The lab was uploaded, but its index.html could not be adjusted "
                "for serving from a subfolder."
            )

    # Look for a baked-in absolute publicPath in the main bundles. CRA emits
    # something like n.p="/" in the webpack runtime.
    for script in sorted((lab_root / "static" / "js").glob("*.js")) if (lab_root / "static" / "js").is_dir() else []:
        try:
            text = script.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _re.search(r'\.p\s*=\s*["\']/["\']', text):
            return (
                "This lab was built for the site root, so parts loaded on demand "
                "may not appear. Ask for a rebuild with PUBLIC_URL=\"./\" "
                "(or homepage \".\") for full support."
            )

    return ""


def _optimize_video_for_streaming(video_path: Path) -> None:
    """
    Move the MP4 moov atom to the beginning of the file for progressive playback.

    Without this, browsers must download the entire file before they can
    determine duration/seek/play. Uses ffmpeg's -movflags +faststart.
    """
    import subprocess
    import shutil

    if not shutil.which("ffmpeg"):
        return  # Best-effort: skip if ffmpeg not available

    tmp_path = video_path.with_suffix(".faststart.mp4")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-c", "copy", "-movflags", "+faststart",
                str(tmp_path),
            ],
            capture_output=True,
            timeout=300,
        )
        if result.returncode == 0 and tmp_path.exists():
            tmp_path.replace(video_path)
        else:
            # Clean up failed attempt
            if tmp_path.exists():
                tmp_path.unlink()
    except (subprocess.TimeoutExpired, OSError):
        if tmp_path.exists():
            tmp_path.unlink()


def _rebuild_toc_in_html(full_html: str, body_html: str) -> str:
    """
    Rebuild the TOC sidebar in the full HTML based on current headings.

    Extracts h1-h3 headings from body_html and regenerates the
    <ul class="toc-list"> contents in the full document.
    """
    import re

    # Extract headings from the body
    heading_pattern = re.compile(
        r'<h([1-3])[^>]*id="([^"]*)"[^>]*>(.*?)</h\1>',
        re.IGNORECASE | re.DOTALL,
    )

    toc_items: list = []
    for match in heading_pattern.finditer(body_html):
        level = int(match.group(1))
        heading_id = match.group(2)
        text = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        if text:
            indent = (level - 1) * 12
            toc_items.append(
                f'<li class="toc-item toc-level-{level}" '
                f'style="padding-left:{indent}px">'
                f'<a href="#{heading_id}">{text}</a></li>'
            )

    if not toc_items:
        return full_html

    new_toc = "\n          ".join(toc_items)
    toc_ul = f'<ul class="toc-list">\n          {new_toc}\n        </ul>'

    # Replace existing toc-list
    toc_pattern = r'<ul class="toc-list">.*?</ul>'
    updated, count = re.subn(toc_pattern, toc_ul, full_html, count=1, flags=re.DOTALL)

    if count == 0:
        # If no toc-list exists, try replacing toc-empty
        empty_pattern = r'<p class="toc-empty">.*?</p>'
        updated, _ = re.subn(empty_pattern, toc_ul, full_html, count=1, flags=re.DOTALL)

    return updated


@app.route("/api/label-info/<path:term>", methods=["GET"])
def get_label_info(term: str):
    """
    Fetch a short description for a diagram label term from Wikipedia.

    Uses the Wikipedia REST API to get a summary extract.
    """
    import requests as http_client
    from urllib.parse import unquote

    decoded_term = unquote(term).strip()
    if not decoded_term or len(decoded_term) < 2:
        return jsonify({"error": "Term too short"}), 400

    try:
        # Clean up the term for better Wikipedia match
        import re as _re
        # Remove leading/trailing punctuation and partial words
        clean_term = _re.sub(r'^[^a-zA-Z]+|[^a-zA-Z]+$', '', decoded_term)
        # Try the cleaned term
        search_term = clean_term if clean_term else decoded_term

        wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{search_term}"
        resp = http_client.get(wiki_url, timeout=5, headers={"User-Agent": "PDF2WebView/1.0"})

        if resp.status_code == 200:
            data = resp.json()
            return jsonify({
                "success": True,
                "title": data.get("title", decoded_term),
                "description": data.get("description", ""),
                "extract": data.get("extract", "No information available."),
                "thumbnail": data.get("thumbnail", {}).get("source", ""),
            }), 200
        else:
            return jsonify({
                "success": True,
                "title": decoded_term,
                "description": "",
                "extract": f"No Wikipedia article found for '{decoded_term}'.",
                "thumbnail": "",
            }), 200
    except Exception:
        return jsonify({
            "success": True,
            "title": decoded_term,
            "extract": "Could not fetch information.",
            "description": "",
            "thumbnail": "",
        }), 200


@app.route("/api/make-interactive", methods=["POST"])
def make_interactive():
    """
    Analyze a diagram image and return detected labels with positions.

    Accepts JSON with {image_url: "..."}. Handles both:
    - Local relative paths (resolved against OUTPUT_DIR)
    - Full URLs (downloaded to temp file for analysis)
    """
    import tempfile
    import requests as http_req
    from urllib.parse import unquote
    from diagram_interactive import make_diagram_interactive

    data = request.get_json()
    if not data or "image_url" not in data:
        return jsonify({"error": "image_url required"}), 400

    image_url = data["image_url"]

    # If it's a full URL (OCI bucket), download it first
    if image_url.startswith("http://") or image_url.startswith("https://"):
        try:
            resp = http_req.get(image_url, timeout=30)
            if resp.status_code != 200:
                return jsonify({"error": f"Cannot fetch image: HTTP {resp.status_code}"}), 400
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.write(resp.content)
            tmp.close()
            result = make_diagram_interactive(tmp.name)
            import os
            os.unlink(tmp.name)
        except Exception as e:
            return jsonify({"error": f"Failed to fetch image: {str(e)}"}), 500
    else:
        # Local relative path
        decoded_path = unquote(image_url)
        full_path = OUTPUT_DIR.resolve() / decoded_path
        if not full_path.exists():
            return jsonify({"error": "Image not found"}), 404
        try:
            full_path.resolve().relative_to(OUTPUT_DIR.resolve())
        except ValueError:
            return jsonify({"error": "Invalid path"}), 403
        result = make_diagram_interactive(str(full_path))

    if result.get("success"):
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@app.route("/api/progress", methods=["POST"])
def save_video_progress():
    """Save video playback progress from the learner's browser."""
    from progress_tracker import save_progress

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    learner_id = data.get("learner_id", "")
    video_src = data.get("video_src", "")
    current_time = data.get("current_time", 0)
    duration = data.get("duration", 0)

    if not learner_id or not video_src:
        return jsonify({"error": "learner_id and video_src required"}), 400

    record = save_progress(learner_id, video_src, current_time, duration)
    return jsonify({"success": True, "progress": record}), 200


@app.route("/api/progress/<learner_id>/<path:video_src>", methods=["GET"])
def get_video_progress(learner_id: str, video_src: str):
    """Get saved progress for a specific video."""
    from progress_tracker import get_progress

    record = get_progress(learner_id, video_src)
    if record:
        return jsonify({"progress": record}), 200
    return jsonify({"progress": None}), 200


@app.route("/api/generate-captions/<path:video_path>", methods=["POST"])
def generate_captions(video_path: str):
    """
    Generate VTT captions for a video using Whisper speech-to-text.

    Runs faster-whisper on the video file and returns VTT subtitle content.
    The VTT file is also saved alongside the video.
    """
    from urllib.parse import unquote

    decoded_path = unquote(video_path)
    video_file = OUTPUT_DIR.resolve() / decoded_path

    if not video_file.exists():
        return jsonify({"error": "Video file not found"}), 404

    try:
        vtt_content = _generate_vtt_captions(video_file)
        # Save VTT file alongside the video
        vtt_path = video_file.with_suffix(".vtt")
        vtt_path.write_text(vtt_content, encoding="utf-8")

        # Return the relative URL to the VTT file
        vtt_relative = str(vtt_path.relative_to(OUTPUT_DIR.resolve()))
        return jsonify({
            "success": True,
            "vtt_url": f"/output/{vtt_relative}",
            "vtt_content": vtt_content,
        }), 200
    except Exception as e:
        return jsonify({"error": f"Caption generation failed: {str(e)}"}), 500


def _generate_vtt_captions(video_path: Path) -> str:
    """Generate WebVTT captions from a video file using faster-whisper."""
    from faster_whisper import WhisperModel

    model = WhisperModel("tiny", compute_type="int8")
    segments, _ = model.transcribe(str(video_path), language="en")

    vtt_lines = ["WEBVTT", ""]
    for segment in segments:
        start = _format_vtt_time(segment.start)
        end = _format_vtt_time(segment.end)
        vtt_lines.append(f"{start} --> {end}")
        vtt_lines.append(segment.text.strip())
        vtt_lines.append("")

    return "\n".join(vtt_lines)


def _format_vtt_time(seconds: float) -> str:
    """Format seconds to VTT timestamp HH:MM:SS.mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


if __name__ == "__main__":
    # threaded=True so status-polling requests are served while a
    # background conversion thread is running (see /convert-status/<job_id>).
    app.run(debug=True, host="0.0.0.0", port=8501, threaded=True)
