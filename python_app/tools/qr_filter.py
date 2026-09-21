"""
QR code filtering for converted documents.

Textbook PDFs embed QR codes (publisher links, "scan for digital resources")
that add nothing to an HTML reading experience. Depending on the extraction
backend, a single QR code shows up in the Markdown as up to three separate
pieces:

    ![QR code linking to the content.](images/30a26f2d…_img.jpg)   <- the image
    QR code linking to the content.                                <- caption
    0531CH01                                                       <- code value

This module strips all three: the image (detected by alt text *or* by actually
looking at the pixels with OpenCV), the descriptive caption, and the short
alphanumeric code value that follows it. It also provides an HTML-level sweep
for anything that slips past the Markdown pass (e.g. images inside tables).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Tuple

__all__ = [
    "strip_qr_content",
    "strip_qr_from_html",
    "is_qr_related_text",
    "is_qr_code_value",
    "image_contains_qr_code",
]


# --- Text patterns -----------------------------------------------------------

# "QR code", "QR-code", "QRcode", "qr codes"
_QR_PHRASE_RE = re.compile(r"\bQR[\s\-_]?codes?\b", re.IGNORECASE)
# Bare "QR" — case-sensitive (lowercase "qr" occurs inside transliterated
# Devanagari text such as "fo|qr~") and only trusted together with one of the
# corroborating words below
_QR_LOOSE_RE = re.compile(r"\bQR\b")
_QR_SUPPORT_RE = re.compile(
    r"\b(scan|code|link(?:ing|s)?|url|https?://|www\.|digital|resource)",
    re.IGNORECASE,
)

# Markdown image: ![alt](path)
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# Leading Markdown decoration to ignore when judging a line's text
_DECORATION_RE = re.compile(r"^(?:[#>\-*+]+\s*|\d+\.\s+)")

# A QR "value" printed next to the code, e.g. 0531CH01, 0906CH04, 0873, 1064CH10
_QR_VALUE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-]{2,15}$")

# Longest line still considered a QR caption rather than body text. Real
# captions are short; a full sentence that happens to mention a QR code
# (e.g. "The audio version … can be found in the QR code of the chapter")
# is body content and must survive.
_MAX_CAPTION_LEN = 100

# List items and blockquotes are authored content, never picture captions
_LIST_ITEM_RE = re.compile(r"^(?:[-*+]\s|\d+[.)]\s|>)")


def is_qr_related_text(text: str) -> bool:
    """
    Return True if a line of text is a QR code caption / description.

    Matches captions like "QR code linking to the content." or
    "A square QR code located in the top right corner of the page.",
    while leaving long body paragraphs that merely mention a QR code alone.
    """
    if not text:
        return False

    candidate = _DECORATION_RE.sub("", text.strip()).strip(" *_`")
    if not candidate or len(candidate) > _MAX_CAPTION_LEN:
        return False

    if _QR_PHRASE_RE.search(candidate):
        return True

    if _QR_LOOSE_RE.search(candidate) and _QR_SUPPORT_RE.search(candidate):
        return True

    return False


def is_qr_code_value(text: str) -> bool:
    """
    Return True if a line looks like the alphanumeric value printed with a
    QR code (e.g. "0531CH01", "0906CH04", "0873").

    Deliberately strict — uppercase/digits only, no spaces, at least two
    digits — and only ever applied to lines directly following removed QR
    content, so ordinary text and headings are never matched.
    """
    candidate = text.strip().strip(" *_`")
    if not candidate or not _QR_VALUE_RE.match(candidate):
        return False
    if sum(char.isdigit() for char in candidate) < 2:
        return False
    return True


# --- Image detection ---------------------------------------------------------

def _load_detector():
    """Return an OpenCV QR detector, or None if OpenCV is unavailable."""
    try:
        import cv2
    except ImportError:  # pragma: no cover - environment dependent
        return None
    try:
        return cv2.QRCodeDetector()
    except Exception:  # pragma: no cover - defensive
        return None


def _resolve_image_path(img_path: str, base_dir: Optional[str]) -> Optional[str]:
    """Resolve a Markdown image reference to a readable file path."""
    if not img_path:
        return None

    # Ignore remote references — nothing to inspect locally
    if img_path.startswith(("http://", "https://", "data:")):
        return None

    if os.path.isabs(img_path):
        return img_path if os.path.isfile(img_path) else None

    if base_dir:
        candidate = Path(base_dir) / img_path
        if candidate.is_file():
            return str(candidate)

    return img_path if os.path.isfile(img_path) else None


def image_contains_qr_code(image_path: str, detector=None) -> bool:
    """
    Check whether an image file actually contains a QR code.

    Detection requires a successful *decode*, which is proof. OpenCV's
    detect-without-decode, and black/white density heuristics, both fire on
    ordinary textbook line drawings (a rooster sketch and several children's
    illustrations in the sample corpus), so they are deliberately not used:
    silently deleting real artwork is worse than leaving an undecodable code,
    and captions/alt text catch those anyway.
    """
    try:
        import cv2
    except ImportError:  # pragma: no cover - environment dependent
        return False

    if detector is None:
        detector = _load_detector()
        if detector is None:
            return False

    try:
        if not os.path.isfile(image_path):
            return False

        img = cv2.imread(image_path)
        if img is None:
            return False

        height, width = img.shape[:2]

        # Very large images are page scans / photos, not a bare QR code
        if width > 1000 and height > 1000:
            return False

        # A decoded payload is conclusive
        data, _points, _ = detector.detectAndDecode(img)
        if data:
            return True
    except (OSError, ValueError, cv2.error):
        pass

    return False


# --- Markdown pass -----------------------------------------------------------

def _strip_qr_images_from_line(
    line: str, detector, base_dir: Optional[str]
) -> Tuple[str, bool]:
    """
    Remove QR code image references from a single Markdown line.

    Returns the rewritten line and whether anything was removed.
    """
    removed = False

    def replace(match: "re.Match") -> str:  # type: ignore[type-arg]
        nonlocal removed
        alt_text = match.group(1)
        img_path = match.group(2).strip()

        if is_qr_related_text(alt_text):
            removed = True
            return ""

        resolved = _resolve_image_path(img_path, base_dir)
        if resolved and image_contains_qr_code(resolved, detector):
            removed = True
            return ""

        return match.group(0)

    new_line = _IMG_RE.sub(replace, line)

    if removed and new_line.strip():
        # Tidy the gap left mid-sentence, without touching the trailing
        # double-space that Markdown uses as a line break
        new_line = re.sub(r"(\S)[ \t]{2,}(\S)", r"\1 \2", new_line)

    return new_line, removed


def strip_qr_content(markdown_text: str, base_dir: Optional[str] = None) -> str:
    """
    Remove QR codes, their captions, and their printed code values from Markdown.

    Args:
        markdown_text: Markdown to clean.
        base_dir: Directory used to resolve relative image paths so the
            pixel-level QR detection can inspect them. Optional.

    Returns:
        Cleaned Markdown. Table rows are never dropped (that would break the
        table); QR images inside them are stripped in place instead.
    """
    if not markdown_text:
        return markdown_text

    detector = _load_detector()
    cleaned: list[str] = []
    # True while the preceding non-empty content was QR related, which is the
    # only situation where a bare code value like "0531CH01" is removed.
    qr_context = False

    for line in markdown_text.split("\n"):
        stripped = line.strip()

        if not stripped:
            cleaned.append(line)
            continue

        if _IMG_RE.search(line):
            new_line, removed = _strip_qr_images_from_line(line, detector, base_dir)
            if removed:
                qr_context = True
                # Keep the line only if it carried other content besides the QR image
                if new_line.strip():
                    cleaned.append(new_line)
                continue
            qr_context = False
            cleaned.append(line)
            continue

        # Table rows, list items and quotes are structural/authored content:
        # keep them (their QR images were already stripped above)
        if stripped.startswith("|") or _LIST_ITEM_RE.match(stripped):
            qr_context = False
            cleaned.append(line)
            continue

        if is_qr_related_text(stripped):
            qr_context = True
            continue

        if qr_context and is_qr_code_value(stripped):
            continue

        qr_context = False
        cleaned.append(line)

    result = "\n".join(cleaned)
    # Collapse the blank-line gaps left behind by removed blocks
    return re.sub(r"\n{3,}", "\n\n", result)


# --- HTML pass ---------------------------------------------------------------

_FIGURE_RE = re.compile(r"<figure\b[^>]*>.*?</figure>", re.IGNORECASE | re.DOTALL)
_PARAGRAPH_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ALT_RE = re.compile(r'alt\s*=\s*"([^"]*)"', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _html_text(fragment: str) -> str:
    """Strip tags and collapse whitespace to get a fragment's visible text."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", fragment)).strip()


def strip_qr_from_html(html: str) -> str:
    """
    Safety net that removes QR code leftovers from rendered HTML.

    Drops figures and paragraphs whose visible text (or image alt text) is a
    QR code description, and any remaining <img> whose alt text describes a
    QR code.
    """
    if not html:
        return html

    def drop_figure(match: "re.Match") -> str:  # type: ignore[type-arg]
        block = match.group(0)
        alt_values = _ALT_RE.findall(block)
        if any(is_qr_related_text(alt) for alt in alt_values):
            return ""
        if is_qr_related_text(_html_text(block)):
            return ""
        return block

    html = _FIGURE_RE.sub(drop_figure, html)

    def drop_paragraph(match: "re.Match") -> str:  # type: ignore[type-arg]
        text = _html_text(match.group(1))
        if is_qr_related_text(text):
            return ""
        return match.group(0)

    html = _PARAGRAPH_RE.sub(drop_paragraph, html)

    def drop_img(match: "re.Match") -> str:  # type: ignore[type-arg]
        alt_match = _ALT_RE.search(match.group(0))
        if alt_match and is_qr_related_text(alt_match.group(1)):
            return ""
        return match.group(0)

    html = _IMG_TAG_RE.sub(drop_img, html)

    # Clean up now-empty wrappers left behind
    html = re.sub(r"<p\b[^>]*>\s*</p>", "", html, flags=re.IGNORECASE)
    html = re.sub(
        r"<figure\b[^>]*>\s*(?:<figcaption\b[^>]*>.*?</figcaption>)?\s*</figure>",
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return html
