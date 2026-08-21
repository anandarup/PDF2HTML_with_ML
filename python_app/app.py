"""
PDF2WebView — Web Frontend

A Flask application providing a drag-and-drop interface to upload PDFs
and convert them to interactive HTML documents.

Usage:
    python app.py

Then open http://localhost:5000 in your browser.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_from_directory,
    url_for,
)

from convert import convert_pdf_to_html

app = Flask(__name__, static_folder="static", template_folder="web_templates")

# Configuration
UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("../output")

# Global max set to the largest allowed type (video: 1.2 GB)
MAX_CONTENT_LENGTH = 1200 * 1024 * 1024

# Per-type upload limits (bytes)
UPLOAD_LIMITS: dict = {
    "video": 1200 * 1024 * 1024,  # 1.2 GB
    "audio": 50 * 1024 * 1024,    # 50 MB
    "pptx": 30 * 1024 * 1024,     # 30 MB
    "h5p": 400 * 1024 * 1024,     # 400 MB
    "pdf": 100 * 1024 * 1024,     # 100 MB
}

app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# Ensure directories exist
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@app.route("/")
def index():
    """Serve the main drag-and-drop upload page."""
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert_pdf():
    """
    Handle PDF upload and conversion.

    Accepts a multipart form upload with a 'pdf' file field.
    Returns JSON with the path to the converted HTML.
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

    # Save uploaded file
    upload_path = UPLOAD_DIR / f"{job_id}_{file.filename}"
    file.save(str(upload_path))

    try:
        # Run conversion pipeline
        output_dir = str(OUTPUT_DIR.resolve() / f"{job_id}_{pdf_stem}")

        result = convert_pdf_to_html(
            pdf_path=str(upload_path),
            output_dir=output_dir,
        )

        # Build URL to serve the converted HTML
        html_path = Path(result["html_path"])
        relative_output = html_path.parent.name
        html_filename = html_path.name

        return jsonify({
            "success": True,
            "title": result.get("chapter_title", pdf_stem),
            "html_url": url_for(
                "serve_output",
                job_dir=relative_output,
                filename=html_filename,
            ),
            "page_count": result["page_count"],
            "image_count": result["image_count"],
            "file_size": result["html_file_size"],
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        # Clean up uploaded file
        if upload_path.exists():
            upload_path.unlink()


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

    # Return the relative URL from the HTML file's perspective
    relative_url = f"media/{target_path.name}"

    return jsonify({
        "success": True,
        "url": relative_url,
        "filename": target_path.name,
    }), 201


@app.route("/output/<path:job_dir>/<path:filename>")
def serve_output(job_dir: str, filename: str):
    """Serve converted HTML and associated assets (images)."""
    directory = OUTPUT_DIR.resolve() / job_dir
    return send_from_directory(str(directory), filename)


@app.route("/output/<path:job_dir>/<path:filename>", methods=["PUT"])
def save_output(job_dir: str, filename: str):
    """
    Save edited HTML content back to the output file.

    Accepts JSON body with { "body_html": "<updated content>" }.
    Replaces the article content in the saved HTML file.
    """
    if not filename.lower().endswith(".html"):
        return jsonify({"error": "Only HTML files can be edited"}), 400

    data = request.get_json()
    if not data or "body_html" not in data:
        return jsonify({"error": "Missing body_html in request"}), 400

    file_path = OUTPUT_DIR.resolve() / job_dir / filename
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404

    # Security: ensure the resolved path is within OUTPUT_DIR
    try:
        file_path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        return jsonify({"error": "Invalid path"}), 403

    try:
        html_content = file_path.read_text(encoding="utf-8")

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

        file_path.write_text(updated_html, encoding="utf-8")

        return jsonify({"success": True, "message": "Content saved"}), 200

    except OSError as e:
        return jsonify({"error": f"File write failed: {e}"}), 500


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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8501)
