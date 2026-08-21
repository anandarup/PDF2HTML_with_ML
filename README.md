# PDF2HTML with ML

An AI-powered document conversion application that transforms PDF files into interactive, styled HTML documents. Uses IBM's Docling for intelligent layout analysis and RapidOCR for text extraction from scanned content.

## Features

- **AI-Powered Extraction** — Docling's DocLayNet model detects document structure (headings, paragraphs, tables, figures, lists) with high accuracy
- **OCR Support** — RapidOCR (via ONNX Runtime) handles scanned/image-based PDFs with automatic detection of when full-page OCR is needed
- **Responsive HTML Output** — Generated HTML includes dark mode, table of contents sidebar, floating textbook-style figures, and print styles
- **QR Code Filtering** — Automatically detects and removes QR codes from the output using OpenCV
- **Smart Title Detection** — Extracts the chapter/document title from the first page text rather than using the filename
- **PDF Artifact Cleanup** — Strips page numbers, running headers/footers, glyph artifacts (`/square6`), and deduplicates repeated heading text
- **MCQ List Restructuring** — Detects multiple-choice options `(a)-(d)` and nests them properly under their parent questions
- **Web Frontend** — Drag-and-drop interface for uploading PDFs and viewing converted HTML in the browser

## Architecture

```
python_app/
├── app.py                  # Flask web server (drag-and-drop frontend)
├── convert.py              # Orchestrator: PDF → Markdown → HTML pipeline
├── tools/
│   ├── extract_pdf.py      # Docling + RapidOCR extraction tool
│   └── build_html.py       # Markdown → HTML with Jinja2 + cleanup
├── templates/
│   └── document.html       # Jinja2 template for output HTML
├── web_templates/
│   └── index.html          # Frontend upload page
└── requirements.txt
```

**Data Flow:**

```
PDF File
  → Docling (layout analysis + table recognition + image extraction)
  → RapidOCR (text from image regions)
  → Markdown (structured content with image references)
  → Artifact Cleanup (page numbers, headers, QR codes, glyph names)
  → Python Markdown Library (MD → HTML conversion)
  → Jinja2 Template (styled responsive HTML with TOC)
  → Output HTML + Images
```

## Prerequisites

- Python 3.9+
- macOS, Linux, or Windows
- Homebrew (macOS) for `zbar` if using pyzbar (optional — OpenCV QR detection is used by default)

## Installation

```bash
git clone https://github.com/anandarup/PDF2HTML_with_ML.git
cd PDF2HTML_with_ML/python_app
pip install -r requirements.txt
```

The first run will download Docling's AI models (~500 MB) automatically from HuggingFace.

## Usage

### Web Frontend (Recommended)

```bash
cd python_app
python3 app.py
```

Open **http://localhost:8501** in your browser. Drag and drop a PDF to convert it.

### Command Line

```bash
cd python_app
python3 convert.py <path-to-pdf> [output-dir]
```

**Examples:**

```bash
# Convert with auto-detected output directory
python3 convert.py "../testFiles/How do Organisms.pdf"

# Specify output directory
python3 convert.py "../testFiles/Methods of Enquiry.pdf" ../output/psychology
```

### Programmatic

```python
from convert import convert_pdf_to_html

result = convert_pdf_to_html(
    pdf_path="./document.pdf",
    output_dir="./output/my_doc",
)

print(result["html_path"])       # Path to generated HTML
print(result["chapter_title"])   # Detected chapter title
print(result["page_count"])      # Number of pages
print(result["image_count"])     # Number of extracted images
```

## Output Structure

```
output/<document_name>/
├── <document_name>.html        # The interactive HTML file
└── images/
    ├── <name>-figure-1.png     # Extracted figures
    ├── <name>-figure-2.png
    ├── <name>-page-1.png       # Full page renders
    └── <name>_artifacts/       # Docling inline images
        ├── image_000000_<hash>.png
        └── image_000001_<hash>.png
```

## Configuration

Key constants in `tools/extract_pdf.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `IMAGE_RESOLUTION_SCALE` | `2.0` | DPI multiplier for extracted images (~144 DPI) |
| `OCR_TEXT_SCORE_THRESHOLD` | `0.4` | Minimum confidence for OCR text acceptance |
| `OCR_BITMAP_AREA_THRESHOLD` | `0.02` | Minimum image area (% of page) to trigger OCR |

Key constants in `tools/build_html.py`:

| Behaviour | Rule |
|-----------|------|
| Running header detection | Lines appearing 3+ times are stripped |
| Page number patterns | Standalone 1-4 digit numbers removed |
| QR code detection | OpenCV QRCodeDetector + edge-density heuristic |
| MCQ option nesting | Lines matching `(a)`-`(d)` are indented under parent |

## Dependencies

| Package | Purpose |
|---------|---------|
| `docling` | AI-powered PDF layout analysis (DocLayNet + TableFormer) |
| `docling-core` | Document data types and export formats |
| `onnxruntime` | Inference engine for RapidOCR models |
| `markdown` | Markdown → HTML conversion |
| `Jinja2` | HTML template rendering |
| `flask` | Web server for the upload frontend |
| `opencv-python` | QR code detection |
| `Pillow` | Image processing |

## How It Works

1. **PDF Ingestion** — Docling parses the PDF using its AI layout model, identifying document elements (text blocks, tables, figures, headings) and their reading order.

2. **OCR Layer** — RapidOCR processes image regions where no embedded text exists. For fully scanned PDFs (detected by low text-per-page ratio), full-page OCR is enabled automatically.

3. **Markdown Export** — Docling exports the structured document as Markdown with image references. Figures are saved as individual PNG files.

4. **Cleanup Pipeline** — The build tool runs several sanity passes:
   - Remove QR codes (OpenCV detection on absolute image paths)
   - Rewrite image paths to relative (for HTML portability)
   - Strip PDF artifacts (page numbers, repeated headers, glyph names)
   - Fix numbered list structure (MCQ options as nested bullets)
   - Deduplicate repeated heading text

5. **HTML Generation** — The cleaned Markdown is converted to HTML using Python's `markdown` library, then injected into a Jinja2 template with responsive CSS, dark mode, TOC sidebar, and floating figures.

6. **Title Detection** — The chapter title is extracted from the first page's raw text (via PyMuPDF), falling back to cleaned heading text or the filename.

## Limitations

- **Scanned PDFs** — OCR quality depends on scan resolution. Very low-quality scans may produce incomplete text.
- **Complex Vector Diagrams** — Diagrams rendered as pure vector paths may not be captured as images by Docling (they appear as text layout elements).
- **Right-to-Left Languages** — Currently configured for English. Change `lang` in `RapidOcrOptions` for other languages.
- **Processing Time** — First conversion downloads AI models. Subsequent conversions take 15-90 seconds depending on page count and image density.

## License

MIT
