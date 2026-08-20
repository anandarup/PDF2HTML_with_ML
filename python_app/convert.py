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
    document_title = title or pdf_stem

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
        "markdown": extraction.markdown,
    }


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
