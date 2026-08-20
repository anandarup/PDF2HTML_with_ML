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
    """
    lines = markdown_text.split("\n")

    # First pass: detect repeated short lines (likely headers/footers)
    line_counts: dict = {}
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) < 60 and not stripped.startswith(("#", "!", "-", "|", ">")):
            line_counts[stripped] = line_counts.get(stripped, 0) + 1

    # Lines appearing 5+ times are almost certainly running headers/footers
    repeated_headers = {
        text for text, count in line_counts.items() if count >= 5
    }

    # Second pass: filter out artifacts
    cleaned: list = []
    for line in lines:
        stripped = line.strip()

        # Skip standalone page numbers
        if stripped and re.match(r"^\d{1,4}$", stripped):
            continue

        # Skip detected running headers/footers
        if stripped in repeated_headers:
            continue

        cleaned.append(line)

    return "\n".join(cleaned)


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
