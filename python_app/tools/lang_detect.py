"""
Decide whether a PDF is in English, to gate automatic image captions.

The OCR backend writes picture descriptions in English regardless of the
document's language, so a Hindi or Urdu textbook ends up with English captions.
Captions are therefore requested only for English documents; for everything else
the editor writes them (the caption is editable in the document view).

Detection is deliberately dependency-free and based on the extracted text:

1. Script — the share of letters that are not Latin. Devanagari, Bengali,
   Gurmukhi, Tamil, Telugu, Arabic and friends settle this immediately.
2. English function words — needed because some books in this corpus are
   Devanagari *transliterated into Latin letters* ("osQ fo|qr~ ½.kkRedrk"),
   which passes a script test but is not English.

Both must agree before a document is treated as English, so the failure mode is
"no automatic caption" rather than "caption in the wrong language".
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

__all__ = ["looks_english", "describe_text", "pdf_looks_english"]

# Common English function words. Short, closed-class and frequent enough that a
# page of real English contains several, while transliterated text contains
# almost none.
_ENGLISH_FUNCTION_WORDS = frozenset("""
    the of and to in is that it for as are was with be by on not this or
    from at have has an we you they which but their there if can when what
    all been would one more also such into other than its these those about
""".split())

# Latin letters must dominate, and enough English function words must appear.
_MAX_NON_LATIN_LETTER_RATIO = 0.10
_MIN_FUNCTION_WORD_RATIO = 0.06
_MIN_WORDS = 40

_WORD_RE = re.compile(r"[A-Za-z']+")


def _is_latin_letter(char: str) -> bool:
    try:
        return "LATIN" in unicodedata.name(char)
    except ValueError:
        return False


def describe_text(text: str) -> dict:
    """
    Measure the signals used to decide whether text is English.

    Returns a dict with letters, non_latin_ratio, words, function_word_ratio and
    is_english, so callers can log exactly why a document was classified.
    """
    letters = [c for c in text if c.isalpha()]
    non_latin = sum(1 for c in letters if not _is_latin_letter(c))
    non_latin_ratio = (non_latin / len(letters)) if letters else 1.0

    words = _WORD_RE.findall(text.lower())
    hits = sum(1 for w in words if w in _ENGLISH_FUNCTION_WORDS)
    function_word_ratio = (hits / len(words)) if words else 0.0

    enough_text = len(words) >= _MIN_WORDS
    is_english = (
        enough_text
        and non_latin_ratio <= _MAX_NON_LATIN_LETTER_RATIO
        and function_word_ratio >= _MIN_FUNCTION_WORD_RATIO
    )

    return {
        "letters": len(letters),
        "non_latin_ratio": round(non_latin_ratio, 4),
        "words": len(words),
        "function_word_ratio": round(function_word_ratio, 4),
        "enough_text": enough_text,
        "is_english": is_english,
    }


def looks_english(text: str) -> bool:
    """True when the text is confidently English."""
    if not text:
        return False
    return describe_text(text)["is_english"]


def pdf_looks_english(pdf_path: str, max_pages: int = 6) -> Optional[dict]:
    """
    Sample a PDF's text layer and decide whether the document is English.

    Samples several pages rather than only the first, because a first page is
    often a title or a picture with almost no words.

    Returns:
        The describe_text() dict, or None when no text could be read (a scanned
        file, or PyMuPDF unavailable) so the caller can pick its own default.
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - environment dependent
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return None

    chunks = []
    try:
        doc = pymupdf.open(pdf_path)
        try:
            for index in range(min(max_pages, doc.page_count)):
                chunks.append(doc.load_page(index).get_text())
        finally:
            doc.close()
    except Exception:  # unreadable/encrypted: let the caller decide
        return None

    text = "\n".join(chunks).strip()
    if not text:
        return None
    return describe_text(text)
