#!/usr/bin/env python3
"""
PDF2WebView — Convert PDFs to interactive, styled HTML.

Usage:
    python convert.py <path-to-pdf> [output-dir]

Examples:
    python convert.py ./testFiles/sample.pdf
    python convert.py "./testFiles/Visual Arts.pdf" ./output/visual_arts
"""

from __future__ import annotations

import sys
import time
import re
from pathlib import Path

from tools.extract_pdf import extract_pdf_content
from tools.build_html import build_interactive_html


def convert_pdf_to_html(
    pdf_path: str,
    output_dir: str | None = None,
    title: str | None = None,
) -> dict:
    """
    Orchestrate the full PDF → Markdown → HTML conversion pipeline.

    Steps:
        1. extract_pdf_content: Parse PDF into Markdown, extract images.
        2. build_interactive_html: Convert Markdown + images into styled HTML5.

    Args:
        pdf_path: Path to the source PDF file.
        output_dir: Directory for output files. Defaults to ./output/<pdf_stem>/.
        title: HTML document title. Defaults to the PDF filename stem.

    Returns:
        Dict with html_path, image_count, page_count, html_file_size, markdown.

    Raises:
        FileNotFoundError: If the PDF does not exist.
        ValueError: If the PDF is empty or invalid.
        RuntimeError: If extraction or generation fails.
    """
    resolved_pdf = Path(pdf_path).resolve()
    pdf_stem = resolved_pdf.stem
    document_title = title or _clean_fallback_title(pdf_stem)

    # Determine output directory
    if output_dir:
        resolved_output = Path(output_dir).resolve()
    else:
        resolved_output = Path("output").resolve() / pdf_stem

    resolved_output.mkdir(parents=True, exist_ok=True)
    image_dir = str(resolved_output / "images")

    print(f"[pdf2webview] Starting conversion: {resolved_pdf}")
    print(f"[pdf2webview] Output directory: {resolved_output}")

    # --- Step 1: Extract PDF content ---
    print("[pdf2webview] Step 1/2: Extracting PDF content...")
    start = time.time()

    extraction = extract_pdf_content(
        pdf_file_path=str(resolved_pdf),
        image_output_dir=image_dir,
    )

    elapsed_extract = time.time() - start
    print(f"[pdf2webview]   - Extracted {extraction.page_count} pages ({elapsed_extract:.1f}s)")
    print(f"[pdf2webview]   - Found {len(extraction.image_paths)} images")
    print(f"[pdf2webview]   - Markdown length: {len(extraction.markdown)} chars")

    if not extraction.markdown.strip():
        print(
            "[pdf2webview]   ⚠ Warning: No text content extracted. "
            "The PDF may be scanned/image-only."
        )

    # Detect chapter title from extracted content (if not explicitly provided)
    if not title:
        document_title = _extract_chapter_title(
            extraction.markdown, extraction.first_page_text, pdf_stem
        )
        print(f"[pdf2webview]   - Chapter title: {document_title}")

    # --- Step 2: Build interactive HTML ---
    print("[pdf2webview] Step 2/2: Building interactive HTML...")
    start = time.time()

    # Use fallback markdown if extraction produced nothing
    md_content = extraction.markdown
    if not md_content.strip():
        md_content = (
            f"# {document_title}\n\n"
            "*This document appears to be image-based. "
            "Text content could not be extracted.*\n"
        )

    html_result = build_interactive_html(
        markdown_content=md_content,
        image_paths=extraction.image_paths,
        output_dir=str(resolved_output),
        output_filename=f"{pdf_stem}.html",
        title=document_title,
        page_count=extraction.page_count,
    )

    elapsed_html = time.time() - start
    print(f"[pdf2webview]   - HTML file: {html_result.html_path}")
    print(f"[pdf2webview]   - File size: {_format_bytes(html_result.file_size)} ({elapsed_html:.1f}s)")
    print("[pdf2webview] ✓ Conversion complete.")

    return {
        "html_path": html_result.html_path,
        "image_directory": extraction.image_directory,
        "image_count": html_result.image_count,
        "page_count": extraction.page_count,
        "html_file_size": html_result.file_size,
        "chapter_title": document_title,
        "markdown": extraction.markdown,
    }


def _extract_chapter_title(
    markdown_content: str | None, first_page_text: str, fallback: str
) -> str:
    """
    Extract the chapter/document title from PDF content.

    Strategy:
    1. Use the first page raw text — in textbooks, the chapter title is
       typically the first few lines on the first page (before body text).
    2. Look for clean headings in the Markdown.
    3. Fall back to a cleaned-up version of the filename.
    """
    # Strategy 1: Extract title from first page raw text
    if first_page_text:
        title = _title_from_first_page(first_page_text)
        if title:
            return title

    if not markdown_content:
        return _clean_fallback_title(fallback)

    lines = markdown_content.strip().split("\n")

    # Strategy 2: Look at first few non-empty, non-image lines for a title
    candidate_lines: list = []
    for line in lines[:20]:
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        if stripped.startswith("#"):
            break
        if len(stripped) < 80:
            candidate_lines.append(stripped)
        else:
            break

    title_candidates = [
        l for l in candidate_lines
        if not re.match(r"^\d+$", l)
        and not re.match(r"^(CHAPTER|Chapter)\s*$", l)
        and len(l) > 3
    ]

    if title_candidates:
        combined = " ".join(title_candidates)
        combined = re.sub(r"\s+\d{1,2}\s*$", "", combined)
        combined = re.sub(r"^\d{1,2}\s+", "", combined)
        if combined and len(combined) > 3:
            return combined

    # Strategy 3: Look for clean ## headings in the Markdown
    heading_pattern = re.compile(r"^(#{1,2})\s+(.+)$", re.MULTILINE)

    for match in heading_pattern.finditer(markdown_content):
        title_text = match.group(2).strip()
        title_text = re.sub(r"\s+", " ", title_text)

        # Skip garbled/duplicated headings
        words = title_text.split()
        if len(words) >= 4:
            half = len(words) // 2
            if words[:half] == words[half:]:
                title_text = " ".join(words[:half])

        if len(title_text) < 4:
            continue
        if re.match(r"^\d+\.\d+", title_text):
            continue
        if re.match(r"^([A-Z] ){3,}", title_text):
            continue

        return title_text

    return _clean_fallback_title(fallback)


def _title_from_first_page(text: str) -> str | None:
    """
    Extract a chapter title from the first page raw text.

    Textbooks typically start with a chapter title in the first few lines,
    often followed by "CHAPTER" and a number. We extract the title text
    that appears before the main body paragraph begins.
    """
    lines = text.strip().split("\n")
    title_parts: list = []

    for line in lines[:10]:
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            continue

        # Skip standalone numbers (chapter numbers, page numbers)
        if re.match(r"^\d{1,3}$", stripped):
            continue

        # Skip "CHAPTER" labels (standalone, without a title)
        if re.match(r"^(CHAPTER|Chapter)\s*$", stripped, re.IGNORECASE):
            continue

        # If line contains a bullet separator, it's likely "Chapter X • Title"
        if " • " in stripped:
            title_parts.append(stripped)
            break

        # If line is short-ish and looks like a title, collect it
        if len(stripped) < 50:
            title_parts.append(stripped)
            # If this looks like a complete title (ends with punctuation or
            # is a recognized chapter pattern), stop collecting
            if stripped.endswith("?") or stripped.endswith("!"):
                break
        else:
            # Long line = body text, stop collecting
            break

    if title_parts:
        combined = " ".join(title_parts)
        # Clean up extra whitespace
        combined = re.sub(r"\s+", " ", combined).strip()
        # Remove trailing single characters (drop-cap artifacts)
        combined = re.sub(r"\s+[A-Z]$", "", combined)
        # If there's a bullet/dot separator, take the full title after it
        if " • " in combined:
            parts = combined.split(" • ")
            # The meaningful title is usually after "Chapter X •"
            if len(parts) >= 2 and re.match(r"^(Chapter|CHAPTER)\s+\d+", parts[0], re.IGNORECASE):
                combined = parts[1].strip()
            else:
                combined = parts[0].strip()
        # Deduplicate: if a phrase repeats, keep only the first occurrence
        words = combined.split()
        if len(words) >= 6:
            # Try finding where the text starts repeating
            for split in range(3, len(words) // 2 + 1):
                first = " ".join(words[:split]).lower()
                rest = " ".join(words[split:]).lower()
                if rest.startswith(first):
                    combined = " ".join(words[:split])
                    break
        if combined and len(combined) > 3:
            return combined

    return None


def _clean_fallback_title(filename_stem: str) -> str:
    """Clean up a filename stem into a presentable title."""
    # Replace hyphens/underscores with spaces
    title = filename_stem.replace("-", " ").replace("_", " ")
    # Title case if all lowercase
    if title == title.lower():
        title = title.title()
    return title


def _format_bytes(size: int) -> str:
    """Format bytes into a human-readable string."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def main() -> None:
    """CLI entry point."""
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print("Usage: python convert.py <path-to-pdf> [output-dir]")
        print()
        print("Arguments:")
        print("  path-to-pdf   Path to the PDF file to convert")
        print("  output-dir    (Optional) Directory for output files")
        sys.exit(0 if args else 1)

    pdf_path = args[0]
    output_dir = args[1] if len(args) > 1 else None

    try:
        result = convert_pdf_to_html(pdf_path=pdf_path, output_dir=output_dir)

        print()
        print("=== Conversion Summary ===")
        print(f"  HTML file:    {result['html_path']}")
        print(f"  Pages:        {result['page_count']}")
        print(f"  Images:       {result['image_count']}")
        print(f"  HTML size:    {_format_bytes(result['html_file_size'])}")
        print(f"  Image dir:    {result['image_directory']}")
        print()

    except FileNotFoundError as e:
        print(f"[pdf2webview] Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"[pdf2webview] Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[pdf2webview] Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
