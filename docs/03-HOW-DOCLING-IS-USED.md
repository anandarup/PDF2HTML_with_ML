# How Docling Is Used in PDF2HTML

**Primary file:** `python_app/tools/extract_pdf.py`
**Called from:** `python_app/convert.py` → `extract_pdf_content(...)`
**Date:** 2026-09-09

> This describes exactly how the deployed code uses [Docling](https://github.com/docling-project/docling) (IBM's document-conversion library) to turn a PDF into structured Markdown plus extracted images. Content is drawn directly from the source; nothing was changed.

---

## 1. Role of Docling

Docling is the **PDF understanding engine**. It performs AI-powered:

- **Layout analysis** (reading order, structure) — the code's docstring attributes this to the DocLayNet model.
- **Table structure recognition** — attributed to the TableFormer model.
- **OCR** for scanned/image content — via **RapidOCR** (configured through Docling).
- **Image/figure extraction** — pictures and table crops are exported as PNGs.
- **Structured Markdown export** with referenced images.

Everything downstream (HTML building, editor, publish) consumes the Markdown + images that Docling produces.

> **Accuracy note:** although `requirements.txt` lists `paddleocr`/`paddlepaddle`, the extraction code configures Docling with **`RapidOcrOptions`** — so RapidOCR is the OCR engine actually wired into the pipeline. `PyMuPDF` (`pymupdf`) is used separately for scanned-PDF detection and first-page text.

---

## 2. Pipeline Configuration

The converter is built in `extract_pdf_content` with these Docling options:

```python
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat

ocr_options = RapidOcrOptions(
    force_full_page_ocr=is_scanned,   # decided per document (see §3)
    lang=["english"],
)

pipeline_options = PdfPipelineOptions()
pipeline_options.images_scale = 2.0          # IMAGE_RESOLUTION_SCALE (~144 DPI)
pipeline_options.generate_page_images = True
pipeline_options.generate_picture_images = True
pipeline_options.do_ocr = True
pipeline_options.ocr_options = ocr_options

doc_converter = DocumentConverter(
    format_options={ InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options) }
)

conv_result = doc_converter.convert(resolved_path)
document = conv_result.document
```

**Constants that tune the pipeline** (defined at module level):

| Constant | Value | Meaning |
|---|---|---|
| `IMAGE_RESOLUTION_SCALE` | `2.0` | Render/extract images at ~144 DPI |
| `OCR_TEXT_SCORE_THRESHOLD` | `0.4` | OCR text below this confidence is discarded |
| `OCR_BITMAP_AREA_THRESHOLD` | `0.02` | Min bitmap area (fraction of page) to OCR a region |
| `_SCANNED_TEXT_THRESHOLD` | `50` | Below this avg chars/page → treat PDF as scanned |

---

## 3. Scanned vs. Digital PDF Detection

Before running Docling, the code decides whether to force **full-page OCR**:

```python
is_scanned = _is_scanned_pdf(str(resolved_path))
```

`_is_scanned_pdf` opens the PDF with **PyMuPDF**, samples up to the first **5 pages**, sums extractable text, and computes average characters per page. If that average is **below 50**, the PDF is considered scanned and `force_full_page_ocr=True` is passed to `RapidOcrOptions`. Digital PDFs keep normal OCR (only where needed).

This avoids running expensive full-page OCR on documents that already have a real text layer, while still handling image-only scans.

---

## 4. Extracting Images and Tables

After conversion, the code iterates the document tree and exports images:

```python
for element, _level in document.iterate_items():
    if isinstance(element, PictureItem):
        # saved as "<stem>-figure-N.png"
    elif isinstance(element, TableItem):
        # saved as "<stem>-table-N.png"
```

- **Pictures** → `<pdf_stem>-figure-<n>.png`
- **Tables** → `<pdf_stem>-table-<n>.png` (the table is captured as an image crop)
- Each `element.get_image(document)` PIL image is saved as PNG into the image output directory.

Then **page images** are also saved for completeness:

```python
for page_no, page in document.pages.items():
    if page.image and page.image.pil_image:
        # saved as "<stem>-page-<page_no>.png"
```

---

## 5. Markdown Export

Docling exports the whole document to Markdown with **referenced** (not embedded) images:

```python
document.save_as_markdown(md_filename, image_mode=ImageRefMode.REFERENCED)
markdown_content = md_filename.read_text(encoding="utf-8")
```

Referenced mode keeps images as separate files (linked from the Markdown), which is what the downstream HTML builder expects so it can rewrite paths to OCI bucket URLs later.

The number of pages is taken from `len(document.pages)`.

---

## 6. Output of the Docling Step

`extract_pdf_content` returns an `ExtractionResult` dataclass:

| Field | Description |
|---|---|
| `markdown` | Structured Markdown from Docling |
| `image_paths` | All extracted image files (figures, tables, pages), collected and sorted |
| `image_directory` | Directory containing the images |
| `page_count` | Number of pages |
| `source_path` | Resolved PDF path |
| `first_page_text` | Raw first-page text (via PyMuPDF) used for title detection |

If Docling produces no text (e.g. a pure image scan where OCR still yields nothing), the caller substitutes a fallback Markdown stub noting the document appears image-based.

---

## 7. What Happens to Docling's Output Next

Docling's Markdown is **not** shown as-is. `tools/build_html.py` post-processes it to clean up the artifacts that PDF extraction commonly introduces:

| Step | Function | Purpose |
|---|---|---|
| Remove QR codes | `_remove_qr_code_images` | OpenCV `QRCodeDetector` drops publisher/QR images |
| Rewrite image paths | `_rewrite_image_paths` | Absolute → relative (`images/…`) for portability |
| Clean artifacts | `_clean_pdf_artifacts` | Strip page numbers, running headers/footers (repeated 3+ times), PostScript glyph names |
| Fix numbered lists | `_fix_numbered_lists` | Turn flat MCQ options `(a)(b)(c)` into nested list items |
| Deduplicate headings | `_deduplicate_heading` | Fix Docling's occasional doubled heading text |
| Wrap figures | `_wrap_images_as_figures` | `<p><img></p>` → `<figure><figcaption>` |
| Build TOC | `_extract_toc` / `_slugify` | Heading hierarchy for the sidebar |

The cleaned Markdown is then converted to HTML with the Python `markdown` library (extensions: `tables`, `fenced_code`, `toc`, `sane_lists`) and rendered into the `document.html` Jinja2 template.

---

## 8. Title Detection (uses Docling + PyMuPDF)

`convert.py` derives a chapter title with a layered strategy:

1. **First-page raw text** (from PyMuPDF `first_page_text`) — textbooks usually put the chapter title in the first lines.
2. **Markdown headings** produced by Docling — first clean `#`/`##` heading.
3. **Filename fallback** — cleaned-up stem.

It also removes duplicated/garbled title fragments (a known PDF-extraction artifact).

---

## 9. Performance Characteristics

- Docling runs on **CPU only** on this VM (no GPU). Layout, table, and OCR models are loaded per process; caches are stored under the service user's home.
- Cost scales with page count and whether full-page OCR is triggered. Scanned documents (full-page OCR) are the most expensive.
- Image extraction at `images_scale = 2.0` yields readable figures/tables while keeping file sizes reasonable.

---

## 10. Summary

Docling is the single source of document structure in this system: it turns a PDF into Markdown + images with layout and table awareness, uses RapidOCR for scanned content, and hands off clean, referenced Markdown to the HTML builder. PyMuPDF complements it for scanned-detection and title extraction, and OpenCV cleans QR/barcode noise from the results.
