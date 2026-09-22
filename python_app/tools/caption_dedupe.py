"""
Remove the duplicated image description that sits beside a figure.

The OCR backend describes each picture and emits that description twice: once as
the Markdown image's alt text, which becomes the <figcaption>, and again as an
ordinary paragraph next to the image. The reader then sees the same sentence
twice — once as body text at full size, once as the caption.

This drops the paragraph and keeps the caption, which is the copy the editor can
style and edit.

The implementation is byte-surgical: only the duplicated <p> blocks are cut, so
every other byte of an already-published document is preserved. It runs both at
conversion time and over existing documents during a template resync.
"""

from __future__ import annotations

import html as _html
import re
from typing import Tuple

__all__ = ["remove_duplicate_captions"]

_FIGURE_RE = re.compile(r'<figure\b[^>]*>.*?</figure>', re.IGNORECASE | re.DOTALL)
_FIGCAPTION_RE = re.compile(r'<figcaption\b[^>]*>(.*?)</figcaption>', re.IGNORECASE | re.DOTALL)
_PARAGRAPH_RE = re.compile(r'\s*<p\b[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'<[^>]+>')

# Ignore very short captions: a one-word caption ("Fig 5.2") can legitimately
# appear as body text, and removing that would delete real content.
_MIN_LENGTH = 25


def _plain(fragment: str) -> str:
    """Visible text of an HTML fragment, normalised for comparison."""
    text = _html.unescape(_TAG_RE.sub(" ", fragment))
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(" .:;!?").lower()


def remove_duplicate_captions(html_content: str) -> Tuple[str, int]:
    """
    Remove paragraphs that merely repeat the caption of the figure above them.

    Returns:
        (cleaned_html, number_of_paragraphs_removed)
    """
    if not html_content or "<figcaption" not in html_content.lower():
        return html_content, 0

    pieces = []
    position = 0
    removed = 0

    for figure in _FIGURE_RE.finditer(html_content):
        if figure.start() < position:
            continue

        caption_match = _FIGCAPTION_RE.search(figure.group(0))
        if not caption_match:
            continue
        caption = _plain(caption_match.group(1))
        if len(caption) < _MIN_LENGTH:
            continue

        # Walk the paragraphs immediately after the figure, dropping each one
        # that repeats the caption (the backend sometimes emits it twice).
        cursor = figure.end()
        cut_from = cursor
        while True:
            paragraph = _PARAGRAPH_RE.match(html_content, cursor)
            if not paragraph or _plain(paragraph.group(1)) != caption:
                break
            cursor = paragraph.end()
            removed += 1

        if cursor == cut_from:
            continue

        pieces.append(html_content[position:cut_from])
        position = cursor

    if not removed:
        return html_content, 0

    pieces.append(html_content[position:])
    return "".join(pieces), removed
