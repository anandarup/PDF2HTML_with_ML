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
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB max upload

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


@app.route("/output/<path:job_dir>/<path:filename>")
def serve_output(job_dir: str, filename: str):
    """Serve converted HTML and associated assets (images)."""
    directory = OUTPUT_DIR.resolve() / job_dir
    return send_from_directory(str(directory), filename)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8501)
