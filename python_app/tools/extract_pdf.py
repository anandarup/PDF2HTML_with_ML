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

    Returns:
        ExtractionResult with markdown content, image paths, and metadata.

    Raises:
        FileNotFoundError: If the PDF file does not exist.
        ValueError: If the PDF file is empty or invalid.
        RuntimeError: If extraction fails.
    """
    resolved_path = Path(pdf_file_path).resolve()
    image_dir = Path(image_output_dir).resolve()

    if not resolved_path.exists():
        raise FileNotFoundError(f"PDF file not found: {resolved_path}")

    if resolved_path.stat().st_size == 0:
        raise ValueError(f"PDF file is empty: {resolved_path}")

    # Ensure image output directory exists
    image_dir.mkdir(parents=True, exist_ok=True)

    # Detect if PDF is predominantly scanned/image-based
    is_scanned = _is_scanned_pdf(str(resolved_path))

    # Configure Docling pipeline for rich extraction with RapidOCR
    ocr_options = RapidOcrOptions(
        # Force full-page OCR for scanned PDFs; selective for text-based PDFs
        force_full_page_ocr=is_scanned,
        bitmap_area_threshold=OCR_BITMAP_AREA_THRESHOLD,
        text_score=OCR_TEXT_SCORE_THRESHOLD,
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

    # Convert the document
    conv_result = doc_converter.convert(resolved_path)
    document = conv_result.document

    # Get page count
    page_count = len(document.pages)

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

        elif isinstance(element, TableItem):
            table_counter += 1
            filename = f"{doc_stem}-table-{table_counter}.png"
            filepath = image_dir / filename
            img = element.get_image(document)
            if img is not None:
                img.save(str(filepath), format="PNG")
                image_paths.append(str(filepath.resolve()))

    # Export Markdown with referenced images
    md_filename = image_dir / f"{doc_stem}.md"
    document.save_as_markdown(md_filename, image_mode=ImageRefMode.REFERENCED)
    markdown_content = md_filename.read_text(encoding="utf-8")

    # Also save page images for completeness
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
