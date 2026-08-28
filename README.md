# PDF2HTML with ML

An AI-powered document conversion platform that transforms static PDFs into interactive, styled HTML documents. Features a rich WYSIWYG editor with 15+ interactive element types, publish-to-cloud workflow, learner-facing features (notes, bookmarks, glossary), and CMS integration via iframe embedding.

Built for Indian K-12 educational content — converts NCERT/DIKSHA textbook PDFs into enriched digital learning experiences.

## Features

### PDF Conversion (AI-Powered)
- **Docling Extraction** — IBM's DocLayNet model detects headings, paragraphs, tables, figures, lists
- **RapidOCR** — Handles scanned/image-based PDFs with automatic detection
- **QR Code Filtering** — Detects and removes QR codes using OpenCV
- **Smart Title Detection** — Extracts chapter title from first page
- **PDF Artifact Cleanup** — Strips page numbers, running headers/footers, deduplicates headings
- **MCQ Restructuring** — Nests multiple-choice options properly under parent questions
- **Auto-upload to OCI** — Images uploaded to Object Storage, HTML paths rewritten to public URLs

### Rich Text Editor (WYSIWYG)
- **Inline Editing** — Click "Edit" to make content editable
- **Grouped Toolbar** — Labeled sections (Text, Block, List, Align, Insert, Colors, Media & Elements, History, Actions)
- **44px Touch Targets** — WCAG-compliant button sizing
- **Formatting** — Bold, Italic, Underline, Strikethrough, Sub/Superscript, Headings (H1-H3), Blockquote, Lists, Text Alignment (L/C/R)
- **LaTeX Formulas** — Insert with live MathJax 3 preview + template library
- **Symbol Picker** — 64 math/science/logic symbols
- **Highlighted Text** — Background markers (5 colors) + text gradients (5 variants) via inline popover
- **Font & Background Color** — Native color pickers
- **Undo/Redo** — Custom history stack (5 levels)
- **Keyboard Shortcuts** — Ctrl+B/I/U/S/Z/Y

### Interactive Elements (Insert via Toolbar)
| Element | Description |
|---------|-------------|
| **Image** | Insert from URL, resize (S/M/L/Full), 4-corner drag handles, replace, delete |
| **Video** | YouTube/Vimeo/direct URL embed (responsive 16:9), file upload with streaming optimization |
| **H5P** | Interactive H5P packages (iframe or h5p-standalone) |
| **Flip Cards** | Multi-card decks with rich text, images, LaTeX on each face |
| **Content Box** | Styled callout containers (Info/Success/Warning/Tip/Highlight) with icon + optional image |
| **Vertical Tabs** | Left-nav tabs with content panels (ArrowUp/Down keyboard nav) |
| **Horizontal Tabs** | Top-row tabs with content panels (ArrowLeft/Right keyboard nav) |
| **Animated Heading** | Static text + rotating colored words with fade animation |
| **Accordion** | Collapsible panels with CSS Grid animation (keyboard accessible) |
| **Shape Divider** | Animated SVG wave/zigzag/curve separators (3 shapes, 3 speeds, 3 heights) |
| **Glossary** | Chapter-level term definitions with auto-highlighting in learner view |

All elements support **double-click to edit** in-place after insertion.

### Media Attachments (Per-Section)
- **5 Content Types** — Video, Audio, Presentation (PPTX/PDF), H5P, URL
- **Icon Bar** — Appears after each heading in edit mode
- **Popup Playback** — Opens content in modal with Video.js player, iframe embeds, or download links
- **YouTube URL Support** — watch, shorts, embed, live, youtu.be formats all auto-convert to embed

### Publish for Learners
- **One-Click Publish** — Uploads HTML + media to OCI Object Storage
- **3 Buckets** — HTML → `poc-interactivetxtbk1`, media → `poc-interactivetxt-media-src-bucket`, videos → `poc-interactivetxt-media-dst-bucket`
- **Editor UI Stripped** — Published HTML has no toolbar, modals, or edit buttons
- **Learner Runtime** — Injected script handles: flip card nav, accordion toggle, tab switching, animated headings, media popups, bookmarks, notes
- **Re-publish** — Overwrites same URL (stable learner links)

### Learner-Facing Features (in Published View)
- **TOC Sidebar** — Left-column section hierarchy with anchor links
- **Notes** — Click heading to add/edit/delete personal notes (localStorage, works offline)
- **Bookmarks** — Save/resume reading position (auto-saves after 10s, auto-scrolls on return)
- **Glossary Tooltips** — Dotted underline on terms, hover/focus shows definition tooltip
- **Back-to-Top** — Floating button after 300px scroll
- **Dark Mode** — Auto via `prefers-color-scheme`
- **Responsive** — Breakpoints at 900px/600px

### Make Interactive (AI Diagrams)
- **RapidOCR Label Detection** — Scans diagram images for text labels
- **Hotspot Overlay** — Clickable/focusable regions positioned at each detected label
- **Wikipedia Tooltips** — Hover/focus shows live definition fetched from Wikipedia API
- **Keyboard Accessible** — `role="button"`, `tabindex="0"`, `:focus` tooltip trigger

### CMS Integration (Strapi/iframe)
- **Iframe Embedding** — Open via `?refId=<uuid>` for tracking
- **Lookup API** — `GET /api/lookup/<refId>` returns status + edit/render URLs
- **Sections API** — `GET /api/sections/<refId>` returns heading hierarchy for CMS sidebar
- **PostMessage** — `scrollToSection`, `applyGlossary`, `sectionPositions` handlers
- **Export to Strapi** — Creates chapters with dynamic zone content blocks
- **Export to WordPress** — Creates draft posts via REST API

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10, Flask |
| PDF Extraction | IBM Docling, RapidOCR, OpenCV |
| HTML Generation | Markdown, Jinja2 |
| Frontend | Vanilla JS (contenteditable), CSS custom properties |
| Math | MathJax 3 |
| Video | Video.js 8.10 |
| Interactive | H5P Standalone 3.8 |
| Storage | OCI Object Storage (Instance Principals) |
| Deployment | Gunicorn, Nginx |

---

## Quick Start

### Web Frontend
```bash
cd python_app
pip install -r requirements.txt
python3 app.py
```
Open **http://localhost:8501** — drag and drop a PDF.

### Command Line
```bash
python3 convert.py <path-to-pdf> [output-dir]
```

### Programmatic
```python
from convert import convert_pdf_to_html

result = convert_pdf_to_html(pdf_path="./document.pdf")
print(result["html_path"])
print(result["chapter_title"])
```

---

## API Endpoints

### Core
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Upload page |
| POST | `/convert` | Upload PDF + start conversion (accepts `ref_id` field) |
| GET | `/convert-status/<job_id>` | Poll conversion progress |
| GET | `/output/<dir>/<file>` | Serve converted files |
| PUT | `/output/<dir>/<file>` | Save edited content |
| POST | `/upload-media/<dir>` | Upload media file (video/audio/h5p/pptx/pdf) |
| POST | `/publish` | Publish to OCI for learners |

### Integration
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/lookup/<refId>` | Look up job by external reference ID |
| GET | `/api/sections/<refId>` | Get heading hierarchy by refId |
| GET | `/api/sections-by-path/<dir>/<file>` | Get heading hierarchy by path |
| POST | `/api/glossary-highlight` | Server-side glossary term highlighting |
| POST | `/api/make-interactive` | AI diagram label detection |
| GET | `/api/label-info/<term>` | Wikipedia summary for a term |

### CMS Export
| Method | Path | Description |
|--------|------|-------------|
| POST | `/export-cms` | Export to Strapi or WordPress |

---

## Configuration

### Upload Limits
| Type | Max Size |
|------|----------|
| Video | 1.2 GB |
| H5P | 400 MB |
| PDF | 100 MB |
| Audio | 50 MB |
| PPT | 30 MB |

### OCI Storage
| Bucket | Purpose |
|--------|---------|
| `poc-interactivetxtbk1` | Published HTML |
| `poc-interactivetxt-media-src-bucket` | Images + media files |
| `poc-interactivetxt-media-dst-bucket` | Streaming video |

### Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `ap-south-1` | (Legacy, unused — OCI uses Instance Principals) |

---

## File Structure

```
python_app/
├── app.py                    # Flask routes (all endpoints)
├── convert.py                # PDF→Markdown→HTML orchestrator
├── s3_publish.py             # OCI publish + learner runtime injection
├── glossary_highlight.py     # Server-side BeautifulSoup term highlighter
├── oci_storage.py            # OCI Object Storage client
├── diagram_interactive.py    # RapidOCR diagram label detection
├── strapi_export.py          # Strapi DIKSHA CMS export
├── progress_tracker.py       # Video playback progress (TinyDB)
├── requirements.txt          # Python dependencies
├── templates/
│   └── document.html         # Main document template (editor + viewer)
├── web_templates/
│   └── index.html            # Upload/landing page
├── static/
│   └── glossary-client.js    # Standalone client-side glossary highlighter
└── tools/
    ├── extract_pdf.py        # Docling extraction
    └── build_html.py         # Markdown→HTML + Jinja2 rendering
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `docling` | AI PDF layout analysis |
| `rapidocr` | Text detection in images |
| `onnxruntime` | ML inference engine |
| `markdown` | Markdown → HTML |
| `Jinja2` | Template rendering |
| `flask` | Web server |
| `opencv-python` | QR code detection, image processing |
| `boto3` | AWS S3 client (available but OCI used) |
| `oci` | Oracle Cloud Infrastructure SDK |
| `beautifulsoup4` | HTML parsing (glossary highlighting) |
| `requests` | HTTP client for CMS APIs |
| `Pillow` | Image processing |
| `faster-whisper` | Video caption generation |
| `paddleocr` | OCR (alternative backend) |

---

## Accessibility

- `role="toolbar"` with grouped labeled sections
- `aria-modal`, `aria-labelledby`, `aria-expanded` on all modals
- `aria-live="polite"` status region for screen reader announcements
- `tabindex="0"` + keyboard handlers on interactive elements
- `prefers-reduced-motion` support (freezes animations)
- `prefers-color-scheme: dark` support
- 44×44px minimum touch targets
- `:focus-visible` outlines on all interactive controls

---

## License

MIT
