# PDF2HTML — Technical Architecture Document

**Application:** PDF → Interactive HTML converter (PDF2WebView)
**Deployed at:** `https://poc-interactivetxtbk.diksha.gov.in`
**Source of truth:** `/opt/pdf2html/app` (repo `PDF2HTML_with_ML`)
**Date:** 2026-09-09

> This document describes the system **as it is deployed on the VM**. It is a factual reflection of the code, not a proposal. No code was changed to produce it.

---

## 1. Overview

PDF2WebView is a Flask web application that converts PDF documents (typically textbook chapters) into styled, interactive HTML. It provides:

- A drag-and-drop upload page.
- An AI-driven conversion pipeline (Docling) that produces Markdown + extracted images.
- An HTML builder that renders the Markdown into an interactive, editable document.
- An in-browser editor (Apple-style toolbar) for teachers/editors.
- A "Publish for Learners" workflow that pushes a clean, learner-facing HTML to OCI Object Storage.
- CMS integration endpoints (Strapi / WordPress) and a `refId`-based lookup API for a parent application.

---

## 2. Runtime Topology

```
Browser (editor / learner)
      │  HTTPS
      ▼
Nginx  (server_name poc-interactivetxtbk.diksha.gov.in)
  - client_max_body_size 1200M
  - proxy_read_timeout / proxy_send_timeout 600s
  - proxy_pass → 127.0.0.1:8501
      │
      ▼
Gunicorn  (systemd unit: pdf2html.service)
  - gunicorn --workers 1 --threads 4 --timeout 600
  - --bind 127.0.0.1:8501 --max-requests 200 --max-requests-jitter 30
  - app:app   (Flask application object)
      │
      ├── conversion runs in a background threading.Thread
      │
      ▼
OCI Object Storage  (Instance Principals, region ap-hyderabad-1)
  - poc-interactivetxtbk1               (rendered HTML)
  - poc-interactivetxt-media-src-bucket (images / media)
  - poc-interactivetxt-media-dst-bucket (streaming video)
```

**Host facts (as observed):** Ubuntu 22.04, 4 vCPU, 15 GB RAM, no GPU. The Python virtualenv at `/opt/pdf2html/venv` is ~7.3 GB (ML dependencies); ML model caches live under the service user's home.

---

## 3. Component Map

| Layer | File(s) | Responsibility |
|---|---|---|
| Web / API | `python_app/app.py` | Flask routes (~18), job orchestration, media upload, publish, CMS export |
| Conversion orchestration | `python_app/convert.py` | Runs extract → build, detects title, uploads results to OCI |
| PDF extraction | `python_app/tools/extract_pdf.py` | Docling pipeline (layout, tables, OCR), image export, Markdown |
| HTML building | `python_app/tools/build_html.py` | Markdown→HTML, artifact cleanup, figure wrapping, TOC |
| Document template | `python_app/templates/document.html` | Jinja2 template + full CSS/JS (editor + reader) |
| Upload UI | `python_app/web_templates/index.html` | Drag-and-drop upload page |
| Publish | `python_app/s3_publish.py` | Strip editor UI, inject learner runtime, upload to OCI buckets |
| Object storage | `python_app/oci_storage.py` | OCI SDK upload helpers (Instance Principals) |
| Glossary | `python_app/glossary_highlight.py` | Wrap glossary terms in accessible `<dfn>` tooltips |
| Diagram labels | `python_app/diagram_interactive.py` | Detect labels on diagram images |
| CMS export | `python_app/strapi_export.py` | Push chapters/sections to DIKSHA Strapi |
| Progress | `python_app/progress_tracker.py` | Video playback progress via TinyDB |

---

## 4. Conversion Pipeline (end to end)

The flow when an editor uploads a PDF:

1. **`POST /convert`** (`app.py`) receives the `pdf` file (and optional `ref_id`), saves it to `uploads/`, writes an initial job record, and starts a **background thread**. It returns `202 {job_id}` immediately.
2. **`convert_pdf_to_html`** (`convert.py`) orchestrates two steps and reports progress via a callback (`extracting` → `building` → `done`).
3. **`extract_pdf_content`** (`tools/extract_pdf.py`) runs the **Docling** pipeline: layout analysis, table recognition, OCR, image export, and Markdown export. It also detects whether the PDF is scanned (PyMuPDF sampling) to decide on full-page OCR.
4. **`build_interactive_html`** (`tools/build_html.py`) converts the Markdown to HTML (Python `markdown` with `tables`, `fenced_code`, `toc`, `sane_lists`), cleans PDF artifacts, removes QR codes (OpenCV), wraps images as figures, builds a table of contents, and renders the `document.html` Jinja2 template.
5. Back in `convert.py`, extracted **images are uploaded** to `poc-interactivetxt-media-src-bucket` and the local `images/...` references in the HTML are rewritten to bucket URLs. The **HTML file is uploaded** to `poc-interactivetxtbk1`.
6. The job record is updated to `status="done"` with an `edit_url`.

```
/convert (thread)
   └─ convert_pdf_to_html()
        ├─ extract_pdf_content()   [Docling]  → markdown + images + page_count
        ├─ build_interactive_html()[markdown+Jinja2] → document HTML
        ├─ oci_storage.upload_directory(images) → rewrite <img> paths to bucket URLs
        └─ oci_storage.upload_html_to_bucket(html)
```

Conversion is CPU-bound (no GPU); per the code's progress messaging it may take from ~15s up to a couple of minutes for longer documents.

---

## 5. Job State Model

Job state is **file-based**, persisted as one JSON file per job under `python_app/jobs/<job_id>.json`.

- Writes are **atomic**: the record is written to a `.tmp` file and then `replace()`d (POSIX-atomic).
- A module-level `threading.Lock` (`CONVERSION_JOBS_LOCK`) guards read/modify/write.
- The design comment notes this deliberately survives Gunicorn worker recycling (`--max-requests 200`).

A job record contains:

```json
{
  "status": "processing | done | error | published",
  "stage": "queued | extracting | building | done | error",
  "detail": "human-readable progress message",
  "result": { "title": "...", "html_url": "...", "page_count": 0, "image_count": 0, "file_size": 0 },
  "error": null,
  "ref_id": "external UUID or null",
  "edit_url": "/output/<job_dir>/<file>.html | null",
  "render_url": "https://objectstorage.../... | null"
}
```

**`refId` lookup:** `GET /api/lookup/<refId>` scans `jobs/*.json` to find the job whose `ref_id` matches, then maps the internal `status` to the external states `converting | ready-for-edit | published | error`.

---

## 6. Editing & Persistence

- Rendered documents are served by **`GET /output/<job_dir>/<filename>`** (`send_from_directory`).
- The in-browser editor saves edits via **`PUT /output/<job_dir>/<filename>`**, which replaces the content inside `<article class="document-body">...</article>` and rebuilds the TOC (`_rebuild_toc_in_html`).
- Media added in the editor is uploaded via **`POST /upload-media/<job_dir>`** into the document's `media/` folder, with per-type size limits enforced server-side.
- Path-traversal is guarded by `..` checks and `Path.resolve().relative_to(OUTPUT_DIR)`.

---

## 7. Publish for Learners

**`POST /publish`** → `s3_publish.publish_document(job_dir, filename)`:

1. Reads the edited HTML.
2. **Strips editor-only UI** (`_strip_editor_ui`): removes Edit/Export/Publish buttons, the edit toolbar, modals, and editor JS.
3. **Injects the learner runtime**: media popups, flip cards, accordions, tabs, animated headings, back-to-top, learner notes (localStorage), and a reader shell/theme.
4. **Pre-highlights glossary** terms server-side for the learner view.
5. Uploads the HTML to `poc-interactivetxtbk1`, media files to `poc-interactivetxt-media-src-bucket`, and streaming video to `poc-interactivetxt-media-dst-bucket`.
6. Updates the job record with `render_url` and `status="published"`.

The returned `url` is the public OCI Object Storage URL for the learner-facing document.

---

## 8. Media Handling

| Type | Limit (from `UPLOAD_LIMITS`) | Special handling |
|---|---|---|
| Video | 1.2 GB | MP4 `moov` atom moved to front for progressive streaming (`ffmpeg -movflags +faststart`) |
| Audio | 50 MB | — |
| PPTX | 30 MB | — |
| H5P | 400 MB | ZIP archive extracted for the `h5p-standalone` player |
| PDF | 100 MB | Source for conversion |
| Image | 1 MB | Restricted to `.png`, `.jpg`, `.jpeg` |

The global Flask `MAX_CONTENT_LENGTH` is 1200 MB, matching Nginx's `client_max_body_size 1200M`.

---

## 9. Auxiliary Services

- **Captions:** `POST /api/generate-captions/<video_path>` runs `faster-whisper` (`WhisperModel("tiny", compute_type="int8")`) and returns/saves WebVTT.
- **Diagram interactivity:** `POST /api/make-interactive` detects labels on a diagram image.
- **Label info:** `GET /api/label-info/<term>` fetches a short summary from the Wikipedia REST API.
- **Glossary:** `POST /api/glossary-highlight` wraps terms in `<dfn>` tooltips.
- **Sections/TOC:** `GET /api/sections/<refId>` and `/api/sections-by-path/...` return the heading hierarchy for building a CMS sidebar.
- **Progress:** `POST /api/progress` + `GET /api/progress/<learner_id>/<video_src>` store/read video playback progress in TinyDB (`learner_progress.json`), marking a video complete at ≥ 90%.

---

## 10. External Integrations

- **OCI Object Storage** via `InstancePrincipalsSecurityTokenSigner` (no keys on disk), region `ap-hyderabad-1`, three buckets (HTML, media, video).
- **DIKSHA Strapi** via `strapi_export.py` — creates a chapter and its sections (dynamic zones), uploading media to the Strapi media library first.
- **WordPress** via the REST `posts` endpoint (draft).
- **Wikipedia REST API** for diagram label descriptions.
- **Parent CMS** via `refId` — see `DOCLING-INTEGRATION-REPORT.md`.

---

## 11. Key Characteristics & Constraints (as-built)

- **Single process, background threads.** Conversion runs inside the web worker via `threading.Thread`. `app.run(..., threaded=True)` in dev; Gunicorn `--threads 4` in production.
- **Local disk state.** `jobs/`, `uploads/`, and `output/` live on the VM's local disk; rendered output is also mirrored to OCI on publish.
- **ML-heavy dependencies.** Docling, RapidOCR (via Docling), OpenCV, faster-whisper, PyMuPDF.
- **Stateless OCI auth.** Instance Principals; the VM's identity grants bucket access.

---

## 12. Directory Layout (relevant parts)

```
/opt/pdf2html/
├── venv/                         # Python virtualenv (~7.3 GB)
└── app/
    ├── docs/                     # architecture & deployment docs
    ├── output/                   # rendered documents (served via /output)
    └── python_app/
        ├── app.py                # Flask routes
        ├── convert.py            # orchestration
        ├── oci_storage.py        # OCI upload helpers
        ├── s3_publish.py         # publish workflow
        ├── strapi_export.py      # Strapi/WordPress export
        ├── glossary_highlight.py # <dfn> glossary tooltips
        ├── progress_tracker.py   # TinyDB video progress
        ├── diagram_interactive.py
        ├── jobs/                 # per-job JSON state files
        ├── uploads/              # temporary uploaded PDFs
        ├── templates/document.html      # document template (editor + reader)
        ├── web_templates/index.html     # upload page
        └── tools/
            ├── extract_pdf.py    # Docling extraction
            └── build_html.py     # Markdown → HTML
```
