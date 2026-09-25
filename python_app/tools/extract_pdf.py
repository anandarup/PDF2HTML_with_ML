"""
extract_pdf_content tool.

Reads a PDF file and extracts its content as Markdown, saving embedded
images to a specified directory. Uses Docling (IBM) for AI-powered layout
analysis, table recognition, and structured Markdown extraction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from docling_core.types.doc import ImageRefMode, PictureItem, TableItem

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    RapidOcrOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

logging.basicConfig(level=logging.WARNING)
_log = logging.getLogger(__name__)

# Resolution scale for extracted images (2.0 = ~144 DPI)
IMAGE_RESOLUTION_SCALE = 2.0

# OCR confidence threshold — text below this score is discarded
OCR_TEXT_SCORE_THRESHOLD = 0.4

# Minimum bitmap area (fraction of page) to trigger OCR on an image region
OCR_BITMAP_AREA_THRESHOLD = 0.02


@dataclass
class ExtractionResult:
    """Result of PDF content extraction."""

    markdown: str
    """The Markdown representation of the PDF content."""

    image_paths: List[str] = field(default_factory=list)
    """Absolute paths to extracted image files."""

    image_directory: str = ""
    """Directory where images were saved."""

    page_count: int = 0
    """Number of pages in the source PDF."""

    source_path: str = ""
    """Resolved absolute path of the source PDF."""

    first_page_text: str = ""
    """Raw text from the first page (useful for title detection)."""
    """Number of pages in the source PDF."""

    source_path: str = ""
    """Resolved absolute path of the source PDF."""


def extract_pdf_content(
    pdf_file_path: str,
    image_output_dir: str,
    progress_callback=None,
) -> ExtractionResult:
    """
    Extract structured Markdown and embedded images from a PDF file.

    Uses Docling's AI-powered pipeline for:
    - Layout analysis (DocLayNet model)
    - Table structure recognition (TableFormer model)
    - Image/figure extraction
    - Reading order detection
    - Structured Markdown export with referenced images

    Args:
        pdf_file_path: Absolute or relative path to the PDF file.
        image_output_dir: Directory to save extracted images.
        progress_callback: Optional callable invoked as
            progress_callback(stage: str, detail: str, extra: dict) at each
            real step of extraction, so callers can surface live progress
            (e.g. an image counter). `extra` carries structured fields like
            {"image_count": N, "page_count": N}. Ignored if None.

    Returns:
        ExtractionResult with markdown content, image paths, and metadata.

    Raises:
        FileNotFoundError: If the PDF file does not exist.
        ValueError: If the PDF file is empty or invalid.
        RuntimeError: If extraction fails.
    """
    def _report(stage: str, detail: str, **extra) -> None:
        if progress_callback:
            try:
                progress_callback(stage, detail, extra)
            except TypeError:
                # Back-compat: callback that only accepts (stage, detail).
                progress_callback(stage, detail)
            except Exception:
                pass  # progress reporting must never break conversion

    resolved_path = Path(pdf_file_path).resolve()
    image_dir = Path(image_output_dir).resolve()

    if not resolved_path.exists():
        raise FileNotFoundError(f"PDF file not found: {resolved_path}")

    if resolved_path.stat().st_size == 0:
        raise ValueError(f"PDF file is empty: {resolved_path}")

    # Ensure image output directory exists
    image_dir.mkdir(parents=True, exist_ok=True)

    # --- OCR engine selection ------------------------------------------------
    # Default ("rapidocr") falls through to the in-process Docling pipeline
    # below, unchanged. "surya" routes the whole conversion to the hosted
    # Datalab/Surya API and adapts its response into the same ExtractionResult.
    try:
        import config as _config
        _ocr_engine = _config.OCR_ENGINE
    except Exception:
        _ocr_engine = "rapidocr"

    if _ocr_engine == "surya":
        return _extract_via_surya(
            resolved_path=resolved_path,
            image_dir=image_dir,
            report=_report,
        )

    # Detect if PDF is predominantly scanned/image-based
    is_scanned = _is_scanned_pdf(str(resolved_path))

    # Configure Docling pipeline for rich extraction with RapidOCR
    ocr_options = RapidOcrOptions(
        force_full_page_ocr=is_scanned,
        lang=["english"],
    )

    if is_scanned:
        _log.info("Detected scanned/image-based PDF — enabling full-page OCR")

    pipeline_options = PdfPipelineOptions()
    pipeline_options.images_scale = IMAGE_RESOLUTION_SCALE
    pipeline_options.generate_page_images = True
    pipeline_options.generate_picture_images = True
    pipeline_options.do_ocr = True
    pipeline_options.ocr_options = ocr_options

    doc_converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    # Convert the document (Docling: layout analysis, OCR, table recognition).
    _report(
        "analyzing",
        "Analyzing document layout, reading order and tables"
        + (" (running OCR on scanned pages)" if is_scanned else "")
        + "…",
    )
    conv_result = doc_converter.convert(resolved_path)
    document = conv_result.document

    # Get page count
    page_count = len(document.pages)
    _report(
        "extracting_images",
        f"Layout analyzed across {page_count} page"
        f"{'s' if page_count != 1 else ''}. Extracting figures and tables…",
        page_count=page_count,
    )

    # Save figure and table images
    image_paths: List[str] = []
    doc_stem = resolved_path.stem

    picture_counter = 0
    table_counter = 0

    for element, _level in document.iterate_items():
        if isinstance(element, PictureItem):
            picture_counter += 1
            filename = f"{doc_stem}-figure-{picture_counter}.png"
            filepath = image_dir / filename
            img = element.get_image(document)
            if img is not None:
                img.save(str(filepath), format="PNG")
                image_paths.append(str(filepath.resolve()))
            _report(
                "extracting_images",
                f"Extracting images… {picture_counter} figure"
                f"{'s' if picture_counter != 1 else ''}"
                + (f", {table_counter} table{'s' if table_counter != 1 else ''}"
                   if table_counter else "")
                + " found",
                page_count=page_count,
                figure_count=picture_counter,
                table_count=table_counter,
                image_count=picture_counter + table_counter,
            )

        elif isinstance(element, TableItem):
            table_counter += 1
            filename = f"{doc_stem}-table-{table_counter}.png"
            filepath = image_dir / filename
            img = element.get_image(document)
            if img is not None:
                img.save(str(filepath), format="PNG")
                image_paths.append(str(filepath.resolve()))
            _report(
                "extracting_images",
                f"Extracting images… {picture_counter} figure"
                f"{'s' if picture_counter != 1 else ''}, "
                f"{table_counter} table{'s' if table_counter != 1 else ''} found",
                page_count=page_count,
                figure_count=picture_counter,
                table_count=table_counter,
                image_count=picture_counter + table_counter,
            )

    # Export Markdown with referenced images
    _report(
        "extracting_text",
        f"Found {picture_counter + table_counter} image"
        f"{'s' if (picture_counter + table_counter) != 1 else ''}. "
        "Extracting and structuring text…",
        page_count=page_count,
        image_count=picture_counter + table_counter,
    )
    md_filename = image_dir / f"{doc_stem}.md"
    document.save_as_markdown(md_filename, image_mode=ImageRefMode.REFERENCED)
    markdown_content = md_filename.read_text(encoding="utf-8")

    # Also save page images for completeness
    _report(
        "saving_pages",
        f"Saving {page_count} page image{'s' if page_count != 1 else ''}…",
        page_count=page_count,
        image_count=picture_counter + table_counter,
    )
    for page_no, page in document.pages.items():
        if page.image and page.image.pil_image:
            page_filename = f"{doc_stem}-page-{page_no}.png"
            page_filepath = image_dir / page_filename
            page.image.pil_image.save(str(page_filepath), format="PNG")

    if not markdown_content or not markdown_content.strip():
        _log.warning(
            f"No text content extracted from {resolved_path}. "
            "The PDF may be scanned/image-only."
        )

    # Re-collect all image files (includes figures, tables, pages)
    image_paths = _collect_image_paths(image_dir)

    # Extract first page raw text for title detection
    first_page_text = _get_first_page_text(str(resolved_path))

    return ExtractionResult(
        markdown=markdown_content,
        image_paths=image_paths,
        image_directory=str(image_dir),
        page_count=page_count,
        source_path=str(resolved_path),
        first_page_text=first_page_text,
    )


def _should_disable_image_captions(pdf_path: str, cfg) -> bool:
    """
    Decide whether to ask the backend for picture descriptions.

    The backend writes them in English whatever the document's language, so an
    English caption on a Hindi chapter is worse than no caption — the editor can
    write one in the document view. Captions are therefore requested only when
    the PDF reads as English.

    DATALAB_IMAGE_CAPTIONS overrides the decision ("always" / "never").
    """
    policy = getattr(cfg, "DATALAB_IMAGE_CAPTIONS", "auto")

    if policy == "always":
        _log.info("Image captions: on (DATALAB_IMAGE_CAPTIONS=always)")
        return False
    if policy == "never":
        _log.info("Image captions: off (DATALAB_IMAGE_CAPTIONS=never)")
        return True

    from tools.lang_detect import pdf_looks_english

    signals = pdf_looks_english(pdf_path)
    if signals is None:
        # No readable text layer, so the language is unknown. Leave captions to
        # the editor rather than risk English text on a non-English chapter.
        _log.info("Image captions: off (no text layer to identify the language)")
        return True

    if signals["is_english"]:
        _log.info(
            "Image captions: on (reads as English: non-Latin letters %.1f%%, "
            "English function words %.1f%%)",
            signals["non_latin_ratio"] * 100, signals["function_word_ratio"] * 100,
        )
        return False

    _log.info(
        "Image captions: off (not English: non-Latin letters %.1f%%, "
        "English function words %.1f%%) — the editor writes these captions",
        signals["non_latin_ratio"] * 100, signals["function_word_ratio"] * 100,
    )
    return True


def _extract_via_surya(resolved_path: Path, image_dir: Path, report) -> ExtractionResult:
    """Extract PDF content using the hosted Datalab/Surya API.

    Produces the same ExtractionResult contract as the Docling path:
    markdown, image_paths, image_directory, page_count, first_page_text.

    Raises RuntimeError on failure so the caller/job surfaces a clear error
    (the default RapidOCR path is unaffected).
    """
    import base64
    import binascii

    import config as _config
    from tools.surya_ocr_client import convert_pdf, SuryaOcrError

    report(
        "analyzing",
        "Reading your PDF and identifying text, images, and layout. "
        "This may take a bit longer for complex documents…",
    )

    disable_captions = _should_disable_image_captions(str(resolved_path), _config)

    try:
        result = convert_pdf(
            str(resolved_path),
            api_key=_config.DATALAB_API_KEY,
            base_url=_config.DATALAB_API_BASE_URL,
            mode=_config.DATALAB_MODE,
            processing_location=_config.DATALAB_PROCESSING_LOCATION,
            timeout_seconds=_config.DATALAB_TIMEOUT_SECONDS,
            poll_interval=_config.DATALAB_POLL_INTERVAL_SECONDS,
            disable_image_captions=disable_captions,
        )
    except SuryaOcrError as exc:
        # Editor-facing, key-free, brand-neutral message.
        raise RuntimeError(f"Text extraction failed: {exc}") from exc

    markdown = result.markdown or ""
    doc_stem = resolved_path.stem

    # Save returned images ({filename: base64}) into image_dir, and rewrite any
    # references in the markdown to point at the saved files. Datalab returns
    # its own filenames; we keep them so markdown links stay consistent.
    report(
        "extracting_images",
        f"Saving {len(result.images)} extracted image"
        f"{'s' if len(result.images) != 1 else ''}…",
        page_count=result.page_count,
    )
    saved_names: List[str] = []
    for fname, b64 in (result.images or {}).items():
        safe_name = Path(fname).name  # never allow path traversal from API data
        if not safe_name:
            continue
        try:
            raw = base64.b64decode(b64, validate=False)
        except (binascii.Error, ValueError):
            _log.warning("Skipping undecodable image from Surya: %s", safe_name)
            continue
        out_path = image_dir / safe_name
        try:
            out_path.write_bytes(raw)
            saved_names.append(safe_name)
        except OSError as exc:
            _log.warning("Could not write image %s: %s", safe_name, exc)

    # If markdown references images as "![](name)" or "(name)", normalize the
    # references so build_html can resolve them under the images/ directory.
    # We only rewrite bare references to files we actually saved.
    for name in saved_names:
        markdown = markdown.replace(f"]({name})", f"](images/{name})")

    report(
        "extracting_text",
        "Structuring extracted text…",
        page_count=result.page_count,
        image_count=len(saved_names),
    )

    # Persist the markdown next to images, mirroring the Docling path's on-disk
    # artifact (some downstream tooling expects a .md alongside images).
    try:
        (image_dir / f"{doc_stem}.md").write_text(markdown, encoding="utf-8")
    except OSError:
        pass

    image_paths = _collect_image_paths(image_dir)
    first_page_text = _get_first_page_text(str(resolved_path))

    # page_count from the API can be 0 for odd inputs; fall back to PyMuPDF.
    page_count = result.page_count
    if page_count <= 0:
        try:
            import pymupdf
            doc = pymupdf.open(str(resolved_path))
            page_count = doc.page_count
            doc.close()
        except Exception:
            page_count = 0

    report(
        "saving_pages",
        f"Saving {page_count} page image{'s' if page_count != 1 else ''}…",
        page_count=page_count,
        image_count=len(saved_names),
    )
    # Page rasters for the editor's split-view fallback tier. Deliberately
    # after _collect_image_paths() above: they are a viewer asset, not extracted
    # content, so they must not inflate image_paths or the "N images" shown in
    # the document header. build_html discovers them by scanning images/.
    if getattr(_config, "SPLIT_VIEW_PAGE_RASTERS", True):
        _render_page_images(str(resolved_path), image_dir, doc_stem)

    return ExtractionResult(
        markdown=markdown,
        image_paths=image_paths,
        image_directory=str(image_dir),
        page_count=page_count,
        source_path=str(resolved_path),
        first_page_text=first_page_text,
    )


def _collect_image_paths(image_dir: Path) -> List[str]:
    """Collect all image files from the output directory, sorted by name."""
    image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    paths: List[str] = []

    if not image_dir.exists():
        return paths

    for entry in sorted(image_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() in image_extensions:
            paths.append(str(entry.resolve()))

    return paths


# Render scale for split-view page rasters. 1.0 == 72 DPI, so 1.75 ≈ 126 DPI:
# legible for side-by-side proofreading without bloating the output directory.
_PAGE_RASTER_ZOOM = 1.75


def _render_page_images(pdf_path: str, image_dir: Path, doc_stem: str) -> int:
    """Rasterise each PDF page to images/<doc_stem>-page-<N>.png (1-based).

    Mirrors the filenames the Docling path produces (see the page-image loop in
    _extract_via_docling), which the editor's split view uses as its fallback
    tier when the embedded PDF viewer can't render.

    Idempotent: pages whose file already exists are counted and skipped, so this
    is cheap to re-run over an existing output directory.

    Best-effort by design: returns the number of pages available and never
    raises. A document without rasters must still convert — the split view
    falls through to the preserved PDF, then to its "not available" message.
    """
    import pymupdf

    written = 0
    doc = None
    try:
        image_dir = Path(image_dir)
        image_dir.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open(pdf_path)
        matrix = pymupdf.Matrix(_PAGE_RASTER_ZOOM, _PAGE_RASTER_ZOOM)
        for index, page in enumerate(doc, start=1):
            out_path = image_dir / f"{doc_stem}-page-{index}.png"
            if out_path.exists():
                written += 1
                continue
            try:
                page.get_pixmap(matrix=matrix).save(str(out_path))
                written += 1
            except Exception as exc:
                _log.warning(
                    "Could not rasterise page %d of %s: %s", index, pdf_path, exc
                )
    except Exception as exc:
        _log.warning("Page rasterisation unavailable for %s: %s", pdf_path, exc)
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    return written


def _get_first_page_text(pdf_path: str) -> str:
    """Extract raw text from the first page of a PDF using PyMuPDF."""
    import pymupdf

    try:
        doc = pymupdf.open(pdf_path)
        if doc.page_count > 0:
            text = doc[0].get_text()
            doc.close()
            return text
        doc.close()
    except Exception:
        pass
    return ""


# Threshold: if average text per page is below this, the PDF is likely scanned
_SCANNED_TEXT_THRESHOLD = 50  # characters per page


def _is_scanned_pdf(pdf_path: str) -> bool:
    """
    Detect whether a PDF is predominantly scanned/image-based.

    Samples the first few pages and checks if embedded text content is
    below a threshold. Scanned PDFs have minimal or no extractable text
    from the PDF stream, requiring full-page OCR.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        True if the PDF appears to be scanned (needs full-page OCR).
    """
    import pymupdf

    try:
        doc = pymupdf.open(pdf_path)
        pages_to_check = min(doc.page_count, 5)
        total_text_len = 0

        for i in range(pages_to_check):
            page = doc[i]
            text = page.get_text().strip()
            total_text_len += len(text)

        doc.close()

        avg_text_per_page = total_text_len / max(pages_to_check, 1)
        return avg_text_per_page < _SCANNED_TEXT_THRESHOLD

    except Exception as exc:
        _log.warning(f"Could not detect PDF type: {exc}")
        return False


class PDFSuitabilityError(Exception):
    """
    Raised when a PDF cannot be converted into clean, editable HTML.

    Currently this covers scanned / image-only PDFs that have no extractable
    text layer — running OCR on them yields a noisy, misordered dump rather
    than clean HTML. The message is written to be shown directly to the editor.

    Note: watermark-based rejection was intentionally NOT enabled. In this
    deployment essentially all source material carries a publisher watermark
    (e.g. NCERT "not to be republished"), so blocking on watermarks would stop
    all legitimate conversions.
    """


# Below this average extractable text per page the PDF has effectively no real
# text layer (scanned / image-only).
_SUITABILITY_MIN_AVG_CHARS_PER_PAGE = 25
# A page with fewer than this many characters is treated as "text-empty".
_SUITABILITY_EMPTY_PAGE_CHARS = 10
# If at least this fraction of sampled pages are text-empty, the document is
# predominantly image-based even if a few pages carry text.
_SUITABILITY_MAX_EMPTY_PAGE_RATIO = 0.6


def check_pdf_suitability(pdf_path: str) -> None:
    """
    Validate that a PDF is suitable for clean HTML conversion.

    Rejects (via PDFSuitabilityError) scanned / image-only PDFs that have no
    real extractable text layer, because OCR of those produces a dirty,
    unusable HTML document rather than clean, editable content.

    Fast, pre-conversion gate using PyMuPDF only (no Docling/OCR). It errs
    toward letting borderline documents through — a genuinely text-based PDF is
    never rejected.

    Raises:
        PDFSuitabilityError: If the PDF is unsuitable. Message is editor-safe.
    """
    import pymupdf

    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        raise PDFSuitabilityError(
            "This file could not be opened as a valid PDF. Please re-export "
            "the document and upload it again."
        ) from exc

    try:
        page_count = doc.page_count
        if page_count == 0:
            raise PDFSuitabilityError(
                "This PDF has no pages. Please upload a valid document."
            )

        sample_limit = min(page_count, 30)
        if page_count <= sample_limit:
            sample_indices = list(range(page_count))
        else:
            step = page_count / float(sample_limit)
            sample_indices = sorted({int(i * step) for i in range(sample_limit)})

        total_chars = 0
        empty_pages = 0
        for idx in sample_indices:
            text = (doc[idx].get_text() or "").strip()
            total_chars += len(text)
            if len(text) < _SUITABILITY_EMPTY_PAGE_CHARS:
                empty_pages += 1

        sampled = len(sample_indices)
        avg_chars = total_chars / max(sampled, 1)
        empty_ratio = empty_pages / max(sampled, 1)

        if (avg_chars < _SUITABILITY_MIN_AVG_CHARS_PER_PAGE
                or empty_ratio >= _SUITABILITY_MAX_EMPTY_PAGE_RATIO):
            raise PDFSuitabilityError(
                "This PDF appears to be scanned or image-only — it has no "
                "selectable text layer, so it can't be converted into clean, "
                "editable HTML. Please upload a text-based PDF (one where you "
                "can select and copy the text)."
            )
    finally:
        doc.close()
