"""
Tests for tools.build_html._slugify — heading anchor generation.

Two properties matter here and they pull in opposite directions:

  1. Pure-ASCII headings must slug exactly as they always have. Anchors for
     English content are already published and linked to; changing them
     silently breaks every deep link and TOC entry.

  2. Non-Latin headings must produce a real slug. They used to collapse to the
     empty string, and the Markdown TOC extension's `unique()` then fell back
     to positional ids (`_1`, `_2`, ...) which carry no meaning and shift
     whenever the document is edited.

The trap worth guarding: Python's `\\w` does NOT match Unicode combining marks
(categories Mc/Mn), which Indic scripts use to build syllables. A "simplifying"
rewrite to `re.sub(r"[\\W_]+", "-", value)` looks correct and passes a casual
eyeball check, but turns "भारत" into "भ-रत". test_combining_marks_are_not_separators
is what catches that.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

import markdown
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.build_html import _slugify  # noqa: E402


def _legacy_slugify(value: str, separator: str = "-") -> str:
    """The pre-fix implementation, kept as the ASCII parity oracle."""
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[^a-z0-9]+", separator, value.lower())
    return value.strip(separator)


class TestAsciiBehaviorUnchanged:
    """Pure-ASCII headings must slug byte-identically to the old implementation."""

    @pytest.mark.parametrize("heading", [
        "1.3 The 2-d Cartesian Coordinate System",
        "Think and Reflect",
        "Exercise Set 1.1",
        "Orienting Yourself: The Use of Coordinates",
        "Chap 5 - 79-99",
        "  leading and trailing  ",
        "UPPERCASE HEADING",
        "snake_case_heading",
        "multiple   internal   spaces",
        "punctuation!@#$%^&*()heavy",
        "trailing dashes ---",
        "2.2.1 How do we classify plants?",
        "",
        "---",
        "123",
    ])
    def test_matches_legacy(self, heading):
        assert _slugify(heading) == _legacy_slugify(heading)

    def test_html_tags_stripped(self):
        assert _slugify("<span>Hello</span> World") == "hello-world"

    def test_custom_separator(self):
        assert _slugify("Hello World", "_") == "hello_world"


class TestNonLatinHeadings:
    """Non-Latin headings must produce real, non-empty slugs."""

    @pytest.mark.parametrize("heading,expected", [
        ("यतींद्र मिश्र", "यतींद्र-मिश्र"),
        ("ऐसी भी बातें होती हैं", "ऐसी-भी-बातें-होती-हैं"),
        ("अभ्यास", "अभ्यास"),
        ("साक्षात्कार की पड़ताल", "साक्षात्कार-की-पड़ताल"),
    ])
    def test_devanagari(self, heading, expected):
        assert _slugify(heading) == expected

    def test_legacy_produced_nothing_for_these(self):
        """Confirms these are genuinely the broken cases, not already working."""
        for heading in ("यतींद्र मिश्र", "अभ्यास", "வணக்கம்"):
            assert _legacy_slugify(heading) == ""
            assert _slugify(heading) != ""

    def test_tamil(self):
        assert _slugify("வணக்கம்") == "வணக்கம்"

    def test_mixed_script_keeps_digits_and_text(self):
        # Urdu heading numbered "2.1" — the old code kept only "2-1", so every
        # section numbered 2.1 in a document collided on the same anchor.
        slug = _slugify("2.1 ہمارے ارد گرد پودوں اور جانوروں میں تنوع")
        assert slug.startswith("2-1-")
        assert "ہمارے" in slug

    def test_combining_marks_are_not_separators(self):
        """
        The regression guard. Devanagari matras/virama are categories Mc/Mn and
        are NOT matched by `\\w`, so a `[\\W_]+` implementation fragments words.
        """
        assert _slugify("भारत") == "भारत"
        assert _slugify("क्रिया") == "क्रिया"
        # naive `\W`-based approach, shown failing, so intent is unambiguous
        assert re.sub(r"[\W_]+", "-", "भारत".lower()).strip("-") == "भ-रत"

    def test_no_separator_runs(self):
        """Consecutive non-slug characters collapse to a single separator."""
        assert _slugify("अभ्यास  ---  प्रश्न") == "अभ्यास-प्रश्न"

    def test_nfc_normalization_makes_anchors_stable(self):
        """
        Canonically-equivalent spellings must yield the same anchor, so the same
        heading text can't produce two different ids depending on how the
        extractor happened to encode it.
        """
        composed = unicodedata.normalize("NFC", "café")
        decomposed = unicodedata.normalize("NFD", "café")
        assert composed != decomposed          # genuinely different codepoints
        assert _slugify(composed) == _slugify(decomposed) == "café"

    def test_devanagari_nukta_forms_are_consistent(self):
        """
        Devanagari nukta letters such as क़ (U+0958) are Unicode composition
        exclusions: NFC deliberately leaves them decomposed, so both spellings
        already agree. Pinned because it looks like a normalization bug
        otherwise, and because the decomposed form relies on the nukta
        (category Mn) being kept rather than treated as a separator.
        """
        composed = unicodedata.normalize("NFC", "क़")
        decomposed = unicodedata.normalize("NFD", "क़")
        assert composed == decomposed
        assert _slugify(composed) == _slugify(decomposed) != ""
        assert "-" not in _slugify(composed)


class TestThroughMarkdownPipeline:
    """End-to-end: the ids the TOC extension actually emits."""

    @staticmethod
    def _render(src: str) -> str:
        md = markdown.Markdown(
            extensions=["tables", "fenced_code", "toc", "sane_lists"],
            extension_configs={"toc": {"permalink": False, "slugify": _slugify}},
        )
        return md.convert(src)

    def test_devanagari_heading_gets_no_positional_fallback(self):
        html = self._render("## अभ्यास\n\ntext\n")
        assert 'id="अभ्यास"' in html
        assert not re.search(r'id="_\d+"', html)

    def test_english_ids_and_collision_suffix_preserved(self):
        html = self._render(
            "## Think and Reflect\n\na\n\n## Think and Reflect\n\nb\n"
        )
        assert 'id="think-and-reflect"' in html
        assert 'id="think-and-reflect_1"' in html

    def test_mixed_document(self):
        html = self._render(
            "## 1.3 The 2-d Cartesian Coordinate System\n\na\n\n## यतींद्र मिश्र\n\nb\n"
        )
        assert 'id="1-3-the-2-d-cartesian-coordinate-system"' in html
        assert 'id="यतींद्र-मिश्र"' in html
