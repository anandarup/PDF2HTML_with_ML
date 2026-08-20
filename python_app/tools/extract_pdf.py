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
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

logging.basicConfig(level=logging.WARNING)
_log = logging.getLogger(__name__)

# Resolution scale for extracted images (2.0 = ~144 DPI)
IMAGE_RESOLUTION_SCALE = 2.0


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

    # Configure Docling pipeline for rich extraction
    pipeline_options = PdfPipelineOptions()
    pipeline_options.images_scale = IMAGE_RESOLUTION_SCALE
    pipeline_options.generate_page_images = True
    pipeline_options.generate_picture_images = True

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

    return ExtractionResult(
        markdown=markdown_content,
        image_paths=image_paths,
        image_directory=str(image_dir),
        page_count=page_count,
        source_path=str(resolved_path),
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
