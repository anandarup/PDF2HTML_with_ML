"""
build_interactive_html tool.

Compiles Markdown content and image paths into a styled, interactive HTML
document using the Python `markdown` library and Jinja2 templating.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

import markdown
from jinja2 import Environment, FileSystemLoader


# Resolve template directory relative to this file
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


@dataclass
class BuildHtmlResult:
    """Result of HTML generation."""

    html_path: str
    """Absolute path to the generated HTML file."""

    image_count: int
    """Number of images referenced in the document."""

    file_size: int
    """Byte size of the generated HTML file."""


@dataclass
class TocEntry:
    """A single table-of-contents entry."""

    level: int
    text: str
    id: str


def build_interactive_html(
    markdown_content: str,
    image_paths: List[str],
    output_dir: str,
    output_filename: str = "output.html",
    title: str = "Converted Document",
    page_count: int = 0,
) -> BuildHtmlResult:
    """
    Convert Markdown content and associated images into a responsive HTML5 document.

    Converts Markdown to HTML using the `markdown` library with extensions for
    tables, fenced code, and table of contents. Injects the result into a Jinja2
    template with responsive styling, dark mode, and a navigation sidebar.

    Image paths in the Markdown are rewritten to be relative to the output HTML
    file for portability.

    Args:
        markdown_content: Raw Markdown string to convert.
        image_paths: List of absolute paths to extracted images.
        output_dir: Directory where the HTML file will be written.
        output_filename: Name of the output HTML file.
        title: Document title for the HTML page.
        page_count: Number of pages in the source PDF (for metadata display).

    Returns:
        BuildHtmlResult with the output path and metadata.

    Raises:
        ValueError: If markdown_content is empty.
        OSError: If the output directory cannot be created or written to.
    """
    if not markdown_content or not markdown_content.strip():
        raise ValueError("Cannot build HTML from empty Markdown content.")

    resolved_output_dir = Path(output_dir).resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    output_path = resolved_output_dir / output_filename

    # Rewrite absolute image paths in markdown to be relative to output HTML
    processed_markdown = _rewrite_image_paths(
        markdown_content, resolved_output_dir
    )

    # Clean up common PDF artifacts (page numbers, repeated headers/footers)
    processed_markdown = _clean_pdf_artifacts(processed_markdown)

    # Fix numbered list structure (MCQ options as sub-items)
    processed_markdown = _fix_numbered_lists(processed_markdown)

    # Remove QR code images from the Markdown
    processed_markdown = _remove_qr_code_images(processed_markdown)

    # Convert Markdown to HTML
    md = markdown.Markdown(
        extensions=[
            "tables",
            "fenced_code",
            "toc",
            "sane_lists",
        ],
        extension_configs={
            "toc": {
                "permalink": False,
                "slugify": _slugify,
            },
        },
    )
    body_html = md.convert(processed_markdown)

    # Convert inline images to floating figures for textbook-style layout
    body_html = _wrap_images_as_figures(body_html)

    # Extract TOC entries from headings in the rendered HTML
    toc_entries = _extract_toc(body_html)

    # Render the Jinja2 template
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=False,  # We handle escaping in the template with | safe
    )
    template = env.get_template("document.html")

    full_html = template.render(
        title=title,
        body_html=body_html,
        toc=toc_entries,
        page_count=page_count,
        image_count=len(image_paths),
    )

    # Write output
    output_path.write_text(full_html, encoding="utf-8")
    file_size = output_path.stat().st_size

    return BuildHtmlResult(
        html_path=str(output_path),
        image_count=len(image_paths),
        file_size=file_size,
    )


def _remove_qr_code_images(markdown_text: str) -> str:
    """
    Detect and remove QR code image references from Markdown.

    Uses OpenCV's QRCodeDetector to check each referenced image file.
    If a QR code is detected, the entire Markdown image line is removed.
    This prevents QR codes (publisher links, digital resource codes)
    from cluttering the converted HTML output.
    """
    import cv2
    import numpy as np
    from PIL import Image as PILImage

    img_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
    qr_detector = cv2.QRCodeDetector()

    lines = markdown_text.split("\n")
    cleaned: list = []

    for line in lines:
        match = img_pattern.search(line)
        if match:
            img_path = match.group(1)
            if _is_qr_code(img_path, qr_detector):
                continue
        cleaned.append(line)

    return "\n".join(cleaned)


def _is_qr_code(image_path: str, detector: "cv2.QRCodeDetector") -> bool:
    """
    Check whether an image file contains a QR code.

    Uses OpenCV QRCodeDetector for reliable detection. Also applies
    heuristics: QR codes are typically small, square images.

    Args:
        image_path: Path to the image file.
        detector: Pre-initialized cv2.QRCodeDetector instance.

    Returns:
        True if the image contains a QR code.
    """
    import cv2

    try:
        if not os.path.isfile(image_path):
            return False

        img = cv2.imread(image_path)
        if img is None:
            return False

        # QR codes are typically square-ish and small
        height, width = img.shape[:2]
        aspect_ratio = min(width, height) / max(width, height)

        # If image is not roughly square (ratio < 0.6), skip detection
        if aspect_ratio < 0.6:
            return False

        # Attempt QR code detection
        data, points, _ = detector.detectAndDecode(img)

        # If data was decoded, it's definitely a QR code
        if data:
            return True

        # Also check with just detection (some QR codes decode fails but detect works)
        retval, points = detector.detect(img)
        if retval and points is not None:
            return True

    except (OSError, cv2.error):
        # Best-effort: if detection fails, assume it's not a QR code
        pass

    return False


def _wrap_images_as_figures(html: str) -> str:
    """
    Convert standalone images into floating figure elements for textbook layout.

    Transforms patterns like:
        <p><img alt="..." src="..." /></p>
    Into:
        <figure class="figure-wrap"><img ... /><figcaption>...</figcaption></figure>

    Images already inside other elements (tables, lists) are left untouched.
    """
    # Match <p> tags that contain only an <img> (possibly with whitespace)
    img_in_p_pattern = re.compile(
        r'<p>\s*(<img\s+[^>]*/>)\s*</p>',
        re.IGNORECASE,
    )

    figure_index = 0

    def replace_with_figure(match: re.Match) -> str:  # type: ignore[type-arg]
        nonlocal figure_index
        img_tag = match.group(1)

        # Extract alt text for caption
        alt_match = re.search(r'alt="([^"]*)"', img_tag)
        alt_text = alt_match.group(1) if alt_match else ""

        # Build caption from alt text or figure number
        caption = alt_text if alt_text else f"Figure {figure_index + 1}"

        figure_html = (
            f'<figure class="figure-wrap">'
            f'{img_tag}'
            f'<figcaption>{caption}</figcaption>'
            f'</figure>'
        )
        figure_index += 1
        return figure_html

    return img_in_p_pattern.sub(replace_with_figure, html)


def _clean_pdf_artifacts(markdown_text: str) -> str:
    """
    Remove common PDF artifacts from extracted Markdown.

    Strips:
    - Standalone page numbers (lines that are just a number)
    - Repeated short lines that appear as running headers/footers
      (any short line appearing 5+ times is likely a page header)
    - Duplicated heading text (e.g., "## Activity 7.2 Activity 7.2" → "## Activity 7.2")
    - PDF glyph name artifacts (e.g., /square6, /bullet, /circle6, etc.)
    """
    # Remove PDF glyph name artifacts (PostScript character references)
    # These are patterns like /square6, /bullet, /circle6, /a]xx, etc.
    # that leak through when PDF text extraction doesn't resolve glyph names
    markdown_text = re.sub(
        r"/(?:square[0-9]*|bullet|circle[0-9]*|diamond[0-9]*|triangle[0-9]*"
        r"|asterisk[0-9]*|dagger[0-9]*|section[0-9]*|paragraph[0-9]*"
        r"|numbersign|percent|ampersand|hyphen|endash|emdash"
        r"|quoteleft|quoteright|quotedblleft|quotedblright"
        r"|fi|fl|ff|ffi|ffl)\b",
        "",
        markdown_text,
    )

    lines = markdown_text.split("\n")

    # First pass: detect repeated short lines (likely headers/footers)
    line_counts: dict = {}
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) < 80 and not stripped.startswith(("#", "!", "-", "|", ">")):
            line_counts[stripped] = line_counts.get(stripped, 0) + 1

    # Lines appearing 3+ times are almost certainly running headers/footers
    repeated_headers = {
        text for text, count in line_counts.items() if count >= 3
    }

    # Also detect common header/footer patterns
    header_footer_patterns = re.compile(
        r"^("
        r"Chapter\s+\d+\s*[·•\-–—].*"     # "Chapter 2 · Title" or "Chapter 2 •"
        r"|.+\s*[·•\-–—]\s*Chapter\s+\d+"  # "Title · Chapter 2"
        r"|\d+\s+Chapter\s+\d+"            # "19 Chapter 2"
        r"|Chapter\s+\d+\s*$"              # Standalone "Chapter 2"
        r"|©.*\d{4}"                        # Copyright lines
        r"|All rights reserved"             # Rights notices
        r"|Downloaded from"                 # Download notices
        r")$",
        re.IGNORECASE,
    )

    # Second pass: filter out artifacts and deduplicate headings
    cleaned: list = []
    for line in lines:
        stripped = line.strip()

        # Skip standalone page numbers (single number, or number-dash-number)
        if stripped and re.match(r"^\d{1,4}$", stripped):
            continue
        if stripped and re.match(r"^\d{1,3}\s*[-–—]\s*\d{1,3}$", stripped):
            continue

        # Skip detected running headers/footers
        if stripped in repeated_headers:
            continue

        # Skip lines matching common header/footer patterns
        if stripped and header_footer_patterns.match(stripped):
            continue
        if stripped in repeated_headers:
            continue

        # Deduplicate repeated heading text
        # Matches patterns like "## Activity 7.2  Activity 7.2" or "## 7.1 DO ORGANISMS 7.1 DO ORGANISMS"
        line = _deduplicate_heading(line)

        cleaned.append(line)

    return "\n".join(cleaned)


def _fix_numbered_lists(markdown_text: str) -> str:
    """
    Fix numbered list structure for MCQ-style content.

    Docling often extracts multiple-choice questions as flat numbered lists:
        1. Question text
        2. (a) Option A
        3. (b) Option B
        4. (c) Option C
        5. (d) Option D

    This function restructures them so options become indented sub-items
    under the parent question, maintaining proper Markdown list nesting.
    """
    lines = markdown_text.split("\n")
    result: list = []

    # Pattern for a numbered list item: "1. text" or "  1. text"
    numbered_pattern = re.compile(r"^(\s*)\d+\.\s+(.*)$")
    # Pattern for MCQ options: starts with (a), (b), (c), (d), (i), (ii), etc.
    option_pattern = re.compile(
        r"^\(([a-d]|[iv]+)\)\s+",
        re.IGNORECASE,
    )

    i = 0
    while i < len(lines):
        line = lines[i]
        match = numbered_pattern.match(line)

        if match:
            indent = match.group(1)
            content = match.group(2)

            # Check if this numbered item's content looks like an MCQ option
            if option_pattern.match(content):
                # Convert to indented bullet under the parent list item
                # Use 4-space indent (required for nesting under numbered lists)
                result.append(f"{indent}    - {content}")
            else:
                result.append(line)
        else:
            result.append(line)

        i += 1

    return "\n".join(result)


def _deduplicate_heading(line: str) -> str:
    """
    Remove duplicated text in Markdown headings.

    Docling sometimes produces headings like:
        ## Activity 7.2 Activity 7.2
        ## 7.1  DO  ORGANISMS  CREA ANISMS  CREATE  EXA TE  EXACT  COPIES  OF CT  COPIES  OF THEMSEL THEMSELVES? VES?

    This function detects when the heading text is repeated (possibly with
    whitespace differences) and keeps only one occurrence.
    """
    # Only process heading lines
    heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
    if not heading_match:
        return line

    prefix = heading_match.group(1)
    text = heading_match.group(2).strip()

    # Normalize whitespace for comparison
    normalized = re.sub(r"\s+", " ", text)

    # Try to find a repeated substring
    # Strategy: check if the second half is a repeat of the first half
    length = len(normalized)
    for split_pos in range(length // 3, (length * 2) // 3 + 1):
        first_half = normalized[:split_pos].strip()
        second_half = normalized[split_pos:].strip()

        # Normalize both halves and compare
        first_clean = re.sub(r"\s+", " ", first_half).strip().lower()
        second_clean = re.sub(r"\s+", " ", second_half).strip().lower()

        if first_clean and second_clean and first_clean == second_clean:
            # The text is duplicated — keep the first half with original casing
            return f"{prefix} {first_half}"

    # Also handle the exact duplicate pattern: "Text Text" where both halves match
    words = normalized.split()
    if len(words) >= 2 and len(words) % 2 == 0:
        half = len(words) // 2
        first_words = words[:half]
        second_words = words[half:]
        if first_words == second_words:
            return f"{prefix} {' '.join(first_words)}"

    return line


def _rewrite_image_paths(markdown_text: str, output_dir: Path) -> str:
    """
    Rewrite absolute image paths in Markdown to be relative to the output directory.

    pymupdf4llm generates Markdown with absolute image paths like:
        ![](/absolute/path/to/image.png)

    We convert these to relative paths so the HTML is portable:
        ![](images/image.png)
    """

    def replace_path(match: re.Match) -> str:  # type: ignore[type-arg]
        prefix = match.group(1)  # ![alt](
        img_path = match.group(2)
        suffix = match.group(3)  # )

        # Only rewrite absolute paths
        if os.path.isabs(img_path):
            try:
                relative = os.path.relpath(img_path, str(output_dir))
                return f"{prefix}{relative}{suffix}"
            except ValueError:
                # On Windows, relpath fails across drives
                return match.group(0)

        return match.group(0)

    # Match markdown image syntax: ![alt](path)
    pattern = r"(!\[[^\]]*\]\()([^)]+)(\))"
    return re.sub(pattern, replace_path, markdown_text)


def _extract_toc(html: str) -> List[TocEntry]:
    """Extract heading elements from rendered HTML to build table of contents."""
    heading_pattern = re.compile(
        r'<h([1-3])[^>]*id="([^"]*)"[^>]*>(.*?)</h\1>',
        re.IGNORECASE | re.DOTALL,
    )
    entries: List[TocEntry] = []

    for match in heading_pattern.finditer(html):
        level = int(match.group(1))
        heading_id = match.group(2)
        # Strip inner HTML tags to get plain text
        raw_text = re.sub(r"<[^>]+>", "", match.group(3)).strip()

        if raw_text:
            entries.append(TocEntry(level=level, text=raw_text, id=heading_id))

    return entries


def _slugify(value: str, separator: str = "-") -> str:
    """
    Generate a URL-friendly slug from a heading string.
    Matches the behavior expected by the TOC extension.
    """
    # Remove HTML tags
    value = re.sub(r"<[^>]+>", "", value)
    # Convert to lowercase and replace non-alphanumeric with separator
    value = re.sub(r"[^a-z0-9]+", separator, value.lower())
    # Strip leading/trailing separators
    value = value.strip(separator)
    return value
