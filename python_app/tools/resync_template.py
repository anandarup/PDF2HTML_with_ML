#!/usr/bin/env python3
"""
Re-apply the current templates/document.html chrome (styles, toolbar,
scripts, header) to an already-converted output file, without touching
the document's actual content.

--- Why this exists ---

build_html.py bakes templates/document.html into a static HTML file
*once*, at conversion time (see build_interactive_html()). Editing the
template afterward — a new toolbar, a CSS fix, a JS behavior change —
has zero effect on files that were already generated; they're frozen
snapshots on disk, not re-rendered on every view. Editing the saved
document (the in-browser "Edit" mode) only ever replaces the
<article class="document-body"> content, never the surrounding chrome.

If you've changed document.html and want an already-converted file to
pick up the new chrome, run this script against it. Converting a fresh
PDF does NOT need this — build_html.py always reads the current
template from disk on every new conversion.

--- What this does ---

  1. Reads the existing output file.
  2. Extracts its <article class="document-body"> content unchanged, and
     strips out editor-only UI chrome that should never have been
     persisted in the first place (block-controls, heading-level-ctrl,
     make-interactive-btn — see EDITOR_CHROME below).
  3. Recovers title / page_count / image_count from the existing header,
     and rebuilds the table of contents from the (now-cleaned) headings.
  4. Re-renders the current document.html template with that content and
     overwrites the file — after writing a .bak backup alongside it.

Refuses to touch (and reports, rather than silently skipping) any file
whose structure it doesn't recognize, so a malformed or unrelated HTML
file can't be corrupted.

--- Usage ---

    python tools/resync_template.py <path-to-output.html>
    python tools/resync_template.py --all              # every output/*/*.html
    python tools/resync_template.py --all --dry-run    # preview only, no writes
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TOOLS_DIR = Path(__file__).resolve().parent
_PYTHON_APP_DIR = _TOOLS_DIR.parent
_TEMPLATE_DIR = _PYTHON_APP_DIR / "templates"
_OUTPUT_DIR = _PYTHON_APP_DIR.parent / "output"

_BODY_RE = re.compile(r'<article class="document-body">(.*?)</article>', re.DOTALL)
_TITLE_RE = re.compile(r'<h1 class="document-title">(.*?)</h1>', re.DOTALL)
_META_RE = re.compile(r"(\d+)\s*pages(?:\s*&middot;\s*(\d+)\s*images)?", re.IGNORECASE)
_HEADING_RE = re.compile(r'<h([1-3])[^>]*\bid="([^"]*)"[^>]*>(.*?)</h\1>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# Editor-only UI chrome that must never be persisted in a saved document.
# Keep this in sync with getCleanBodyHtml()'s strip list in document.html —
# these are exactly the injected-widget classes that function is meant to
# remove before saving, plus make-interactive-btn (which that function
# currently misses, per the last UX audit's finding D).
_EDITOR_CHROME = (
    ("span", "block-controls"),
    ("span", "heading-level-ctrl"),
    ("button", "make-interactive-btn"),
)


class ResyncError(Exception):
    """Raised when a file can't be safely resynced. Never partially written."""


def _strip_editor_chrome(body_html: str) -> str:
    for tag, cls in _EDITOR_CHROME:
        # Non-greedy match of a single, non-nested <tag class="...cls...">
        # ... </tag> block. Safe here because none of these widgets ever
        # contain a nested element of their own tag+class.
        pattern = re.compile(
            rf'<{tag}\b[^>]*\bclass="[^"]*\b{re.escape(cls)}\b[^"]*"[^>]*>.*?</{tag}>',
            re.DOTALL,
        )
        body_html = pattern.sub("", body_html)
    return body_html


def _extract_toc(body_html: str) -> list[dict]:
    toc = []
    for match in _HEADING_RE.finditer(body_html):
        level = int(match.group(1))
        heading_id = match.group(2)
        text = _TAG_RE.sub("", match.group(3)).strip()
        if text:
            toc.append({"level": level, "id": heading_id, "text": text})
    return toc


def resync_file(html_path: Path, *, dry_run: bool = False) -> None:
    if not html_path.exists():
        raise ResyncError(f"File not found: {html_path}")

    original = html_path.read_text(encoding="utf-8")

    body_match = _BODY_RE.search(original)
    if not body_match:
        raise ResyncError(
            f'Could not find <article class="document-body"> in {html_path} '
            "— refusing to touch a file whose structure isn't recognized."
        )
    body_html = _strip_editor_chrome(body_match.group(1))

    title_match = _TITLE_RE.search(original)
    title = _TAG_RE.sub("", title_match.group(1)).strip() if title_match else html_path.stem

    meta_match = _META_RE.search(original)
    page_count = int(meta_match.group(1)) if meta_match else 0
    image_count = int(meta_match.group(2)) if meta_match and meta_match.group(2) else 0

    toc_entries = _extract_toc(body_html)

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)
    template = env.get_template("document.html")
    rendered = template.render(
        title=title,
        body_html=body_html,
        toc=toc_entries,
        page_count=page_count,
        image_count=image_count,
    )

    if dry_run:
        print(f"[dry-run] Would rewrite {html_path} ({len(rendered)} bytes, was {len(original)})")
        return

    backup_path = html_path.with_suffix(html_path.suffix + ".bak")
    if not backup_path.exists():
        backup_path.write_text(original, encoding="utf-8")
    html_path.write_text(rendered, encoding="utf-8")
    print(f"Resynced {html_path} (original preserved at {backup_path})")


def find_all_output_html() -> list[Path]:
    if not _OUTPUT_DIR.exists():
        return []
    return sorted(
        p for p in _OUTPUT_DIR.glob("*/*.html")
        if not p.name.startswith("._") and not p.name.endswith(".bak")
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-apply the current document.html template chrome to already-converted output files.",
    )
    parser.add_argument("path", nargs="?", help="Path to a single output HTML file")
    parser.add_argument("--all", action="store_true", help="Resync every output/*/*.html file")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    args = parser.parse_args()

    if not args.path and not args.all:
        parser.error("Provide a file path, or use --all to resync every output file")

    targets = find_all_output_html() if args.all else [Path(args.path)]
    if not targets:
        print("No output HTML files found under output/.")
        return 0

    failures = 0
    for target in targets:
        try:
            resync_file(target, dry_run=args.dry_run)
        except ResyncError as e:
            print(f"SKIPPED: {e}", file=sys.stderr)
            failures += 1

    if failures:
        print(f"\n{failures} file(s) skipped — see messages above.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
