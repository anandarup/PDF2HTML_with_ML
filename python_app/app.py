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

    # For H5P uploads, extract the zip archive for browser playback
    h5p_folder = ""
    if media_type == "h5p" and target_path.suffix.lower() == ".h5p":
        h5p_folder = _extract_h5p(target_path)

    # Return the relative URL from the HTML file's perspective
    relative_url = f"media/{target_path.name}"
    if h5p_folder:
        relative_url = f"media/{h5p_folder}"

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
            return _export_to_strapi(data, base_url, title, body_html, http_client)
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


def _export_to_strapi(data: dict, base_url: str, title: str, body_html: str, http_client) -> tuple:
    """Push content to Strapi v4/v5 REST API, splitting into Chapter + Sections."""
    import re as _re

    api_token = data.get("api_token", "")
    content_type = data.get("content_type", "chapters")

    base_url = base_url.rstrip("/")
    content_type = content_type.strip().strip("/")

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    # Split HTML into sections by h1/h2 headings
    sections = _split_html_into_sections(body_html)

    # Step 1: Create Section entries
    section_ids: list = []
    for idx, section in enumerate(sections):
        section_payload = {
            "data": {
                "title": section["title"],
                "order": idx + 1,
            }
        }
        sec_resp = http_client.post(
            f"{base_url}/api/sections",
            json=section_payload,
            headers=headers,
            timeout=30,
        )
        if sec_resp.status_code in (200, 201):
            sec_data = sec_resp.json()
            sec_id = sec_data.get("data", {}).get("id")
            if sec_id:
                section_ids.append(sec_id)
        else:
            error_msg = sec_resp.text[:200]
            return jsonify({
                "error": f"Failed creating section '{section['title']}': {error_msg}"
            }), sec_resp.status_code

    # Step 2: Create Chapter entry with linked sections
    chapter_payload = {
        "data": {
            "title": title,
            "order": 1,
            "description": "",
            "sections": section_ids,
        }
    }

    resp = http_client.post(
        f"{base_url}/api/{content_type}",
        json=chapter_payload,
        headers=headers,
        timeout=30,
    )

    if resp.status_code in (200, 201):
        resp_data = resp.json()
        entry_id = resp_data.get("data", {}).get("id", "")
        return jsonify({
            "success": True,
            "url": f"{base_url}/api/{content_type}/{entry_id}",
            "message": f"Created chapter with {len(section_ids)} sections",
        }), 200
    elif resp.status_code == 405:
        return jsonify({
            "error": (
                "Method Not Allowed (405). Check Strapi configuration:\n"
                "1. Ensure the content type API ID is correct\n"
                "2. Enable 'create' permission for this content type"
            )
        }), 405
    elif resp.status_code == 403:
        return jsonify({
            "error": "Forbidden (403). The API token doesn't have 'create' permission."
        }), 403
    else:
        error_msg = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
        return jsonify({"error": f"Strapi error ({resp.status_code}): {error_msg}"}), resp.status_code


def _split_html_into_sections(html: str) -> list:
    """
    Split HTML content into sections based on h1/h2 headings.

    Each section has a title (from the heading) and content (HTML until
    the next heading of same or higher level).
    """
    import re as _re

    # Split on h1 or h2 tags
    parts = _re.split(r'(<h[12][^>]*>.*?</h[12]>)', html, flags=_re.IGNORECASE | _re.DOTALL)

    sections: list = []
    current_title = "Introduction"
    current_content = ""

    for part in parts:
        heading_match = _re.match(r'<h[12][^>]*>(.*?)</h[12]>', part, flags=_re.IGNORECASE | _re.DOTALL)
        if heading_match:
            # Save previous section if it has content
            if current_content.strip():
                sections.append({"title": current_title, "content": current_content.strip()})
            # Start new section
            current_title = _re.sub(r'<[^>]+>', '', heading_match.group(1)).strip()
            current_content = ""
        else:
            current_content += part

    # Don't forget the last section
    if current_content.strip():
        sections.append({"title": current_title, "content": current_content.strip()})

    # If no sections were created, treat the whole thing as one
    if not sections:
        sections.append({"title": "Content", "content": html})

    return sections


def _export_to_wordpress(data: dict, base_url: str, title: str, body_html: str, http_client) -> tuple:
    """Push content to WordPress REST API (posts endpoint)."""
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
    They must be extracted to be served to the h5p-standalone player.

    Returns:
        The folder name (relative to media/) where content was extracted.
    """
    import zipfile

    folder_name = h5p_path.stem
    extract_dir = h5p_path.parent / folder_name

    try:
        with zipfile.ZipFile(str(h5p_path), "r") as zf:
            zf.extractall(str(extract_dir))
        return folder_name
    except (zipfile.BadZipFile, OSError):
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


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8501)
