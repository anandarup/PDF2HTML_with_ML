# PDF2HTML with ML

An AI-powered document conversion application that transforms PDF files into interactive, styled HTML documents with a rich content editor and CMS export capabilities. Uses IBM's Docling for intelligent layout analysis and RapidOCR for text extraction.

## Features

### PDF Conversion
- **AI-Powered Extraction** — Docling's DocLayNet model detects document structure (headings, paragraphs, tables, figures, lists)
- **OCR Support** — RapidOCR handles scanned/image-based PDFs with automatic detection
- **QR Code Filtering** — Detects and removes QR codes using OpenCV
- **Smart Title Detection** — Extracts chapter title from the first page text
- **PDF Artifact Cleanup** — Strips page numbers, running headers/footers, glyph artifacts, deduplicates headings
- **MCQ List Restructuring** — Nests multiple-choice options properly under parent questions

### Rich Text Editor
- **Inline Editing** — Click "Edit" to make content editable directly in the browser
- **Formatting Toolbar** — Bold, Italic, Underline, Strikethrough, Sub/Superscript, Headings (H1-H3), Blockquote, Lists
- **LaTeX Formulas** — Insert math expressions with live preview via MathJax 3
- **Symbol Picker** — 64 math/science/logic symbols
- **Font & Background Color** — Native color pickers
- **Insert Image/Video** — Embed images and YouTube/Vimeo/direct video inline
- **Flip Cards** — Multi-card decks with rich text, images, and formulas on each face
- **H5P Content** — Insert interactive H5P packages inline
- **Drag & Drop Reordering** — Reposition any block element in edit mode
- **Section Delete** — Remove blocks with confirmation (× button on hover)

### Media Attachments
- **6 Content Types** — Video, Audio, Presentation (PPTX), H5P, Glossary, URL
- **Icon Bar** — Appears after each heading, visible in edit mode (all icons) or learner mode (only with content)
- **Popup Playback** — Clicking icons in learner mode opens content in a modal popup
- **File Upload** — Upload or enter URL via a proper dialog (not browser prompt)
- **Video Optimization** — Uploaded MP4s auto-optimized with ffmpeg faststart for streaming
- **H5P Extraction** — .h5p files extracted and served via h5p-standalone player

### CMS Export
- **Strapi ** — Full integration with content-manager API:
  - Creates Chapter linked to an existing Textbook (via documentId)
  - Splits content into Sections by headings
  - Each section contains `content_blocks` dynamic zone with proper block types
  - Uploads all media to Strapi media library
  - Maps to native blocks: text-block, image-block, video-block, audio-block, flashcard-set, h5p-block, file-upload-block, media-block
  - Includes `designLayout` CSS for each block component
  - Strips editor-only UI (drag handles, delete buttons) from export
- **WordPress** — Creates draft posts via REST API with full HTML content and uploaded media

### HTML Output
- Responsive layout with dark mode support
- Table of contents sidebar with scroll tracking
- Textbook-style floating figures
- Print-friendly styles
- Video.js player for uploaded videos
- Back-to-top button

## Architecture

```
python_app/
├── app.py                  # Flask web server + API endpoints
├── convert.py              # Orchestrator: PDF → Markdown → HTML pipeline
├── strapi_export.py        # Strapi CMS export module
├── tools/
│   ├── extract_pdf.py      # Docling + RapidOCR extraction
│   └── build_html.py       # Markdown → HTML with Jinja2 + cleanup
├── templates/
│   └── document.html       # Jinja2 template (output HTML with editor)
├── web_templates/
│   └── index.html          # Frontend upload page
└── requirements.txt
```

## Prerequisites

- Python 3.9+
- macOS / Linux / Windows
- ffmpeg (for video optimization): `brew install ffmpeg`

## Installation

```bash
git clone https://github.com/anandarup/PDF2HTML_with_ML.git
cd PDF2HTML_with_ML/python_app
pip install -r requirements.txt
```

First run downloads Docling AI models (~500 MB) from HuggingFace.

## Usage

### Web Frontend

```bash
cd python_app
python3 app.py
```

Open **http://localhost:8501** — drag and drop a PDF to convert.

### Command Line

```bash
cd python_app
python3 convert.py <path-to-pdf> [output-dir]
```

### Programmatic

```python
from convert import convert_pdf_to_html

result = convert_pdf_to_html(pdf_path="./document.pdf")
print(result["html_path"])
print(result["chapter_title"])
```

## CMS Export

### Strapi

1. Click **Export** button on any converted document
2. Select **Strapi** platform
3. Enter:
   - CMS Base URL: `https://xyz.com`
   - Admin JWT Token (from `POST /admin/login`)
   - Textbook Document ID (parent textbook's `documentId`)
   - Chapter Order number
4. Click Export

The system will:
- Upload all images/media to Strapi's media library
- Create a Chapter entry linked to the Textbook
- Split content into Sections with `content_blocks` dynamic zone
- Map each element to the correct Strapi block type with `designLayout` CSS

### WordPress

1. Click **Export** → select **WordPress**
2. Enter site URL, username, and Application Password
3. Creates a draft post with full content and uploaded media

## Configuration

### Upload Limits

| Type | Max Size |
|------|----------|
| Video | 1.2 GB |
| H5P | 400 MB |
| PDF | 100 MB |
| Audio | 50 MB |
| PPT | 30 MB |

### Extraction Options

| Constant | Default | Description |
|----------|---------|-------------|
| `IMAGE_RESOLUTION_SCALE` | `2.0` | DPI multiplier for extracted images |
| `OCR_TEXT_SCORE_THRESHOLD` | `0.4` | Minimum OCR confidence |
| `OCR_BITMAP_AREA_THRESHOLD` | `0.02` | Min image area to trigger OCR |

## Dependencies

| Package | Purpose |
|---------|---------|
| `docling` | AI-powered PDF layout analysis |
| `onnxruntime` | RapidOCR inference engine |
| `markdown` | Markdown → HTML conversion |
| `Jinja2` | HTML template rendering |
| `flask` | Web server |
| `opencv-python` | QR code detection |
| `requests` | CMS API calls |
| `Pillow` | Image processing |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Upload page |
| POST | `/convert` | Convert uploaded PDF |
| GET | `/output/<dir>/<file>` | Serve converted files |
| PUT | `/output/<dir>/<file>` | Save edited content |
| POST | `/upload-media/<dir>` | Upload media file |
| POST | `/export-cms` | Export to Strapi/WordPress |

## License

MIT
