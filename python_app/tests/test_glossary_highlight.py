"""
Regression tests for glossary term matching in Indic scripts.

Bug: the glossary term "निवासी" was never highlighted, while other Hindi terms
such as "भारत" were. Both highlighters used \\b as the word boundary:

  * Python's \\w does not include combining marks (Unicode Mn/Mc), so a term
    ending in a vowel sign (matra) like "ी" has no \\b after it.
  * JavaScript's \\b is ASCII-only, so in the browser no Devanagari term matched.

The server tests exercise glossary_highlight directly. The browser tests run
the exact highlighter scripts shipped in the published page (s3_publish),
the editor template and static/glossary-client.js in headless Chromium, and
are skipped when Playwright or its browser is not installed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from glossary_highlight import highlight_glossary_terms  # noqa: E402

DEF = "definition"

# Terms whose last (or first) character is a combining mark: the failing class.
MARK_EDGE_TERMS = ["निवासी", "नदी", "सीमा", "हिंदी", "संस्कृति", "पाणी"]


def _dfn_terms(html: str) -> list[str]:
    return re.findall(r'<dfn [^>]*class="glossary-term"[^>]*>(.*?)</dfn>', html)


class TestServerHighlighter:
    @pytest.mark.parametrize("term", MARK_EDGE_TERMS)
    def test_term_ending_in_vowel_sign_is_highlighted(self, term):
        html = f"<p>यहाँ {term} शब्द है।</p>"
        out = highlight_glossary_terms(html, [{"term": term, "definition": DEF}])
        assert _dfn_terms(out) == [term]

    def test_reported_case(self):
        # Mirrors the published CHAP 10 markup where the bug was observed.
        html = "<p><strong>निवासी</strong><br/>वे लोग जो एक स्थान विशेष में रहते हैं।</p>"
        out = highlight_glossary_terms(html, [{"term": "निवासी", "definition": DEF}])
        assert _dfn_terms(out) == ["निवासी"]

    def test_consonant_ending_term_still_works(self):
        out = highlight_glossary_terms("<p>भारत एक देश है।</p>", [{"term": "भारत", "definition": DEF}])
        assert _dfn_terms(out) == ["भारत"]

    @pytest.mark.parametrize("text", [
        "<p>भारतीय लोग</p>",       # term followed by a matra-bearing syllable
        "<p>निवासियों के नाम</p>",  # inflected form is a different word
    ])
    def test_no_partial_word_matches(self, text):
        glossary = [{"term": "भारत", "definition": DEF}, {"term": "निवासी", "definition": DEF}]
        assert _dfn_terms(highlight_glossary_terms(text, glossary)) == []

    def test_term_not_matched_when_followed_by_matra(self):
        # "नद" must not match inside "नदी" — the trailing ी is part of the word.
        out = highlight_glossary_terms("<p>नदी</p>", [{"term": "नद", "definition": DEF}])
        assert _dfn_terms(out) == []

    def test_english_behaviour_unchanged(self):
        glossary = [{"term": "carbon", "definition": DEF}, {"term": "carbon dioxide", "definition": DEF}]
        out = highlight_glossary_terms(
            "<p>Carbon, carbonate and carbon dioxide; snake_carbon.</p>", glossary
        )
        assert _dfn_terms(out) == ["Carbon", "carbon dioxide"]

    def test_hindi_punctuation_is_a_boundary(self):
        out = highlight_glossary_terms("<p>(निवासी), निवासी।</p>", [{"term": "निवासी", "definition": DEF}])
        assert _dfn_terms(out) == ["निवासी", "निवासी"]


# --------------------------------------------------------------------------
# Browser-side highlighters
# --------------------------------------------------------------------------

def _js_regex_sources() -> dict[str, str]:
    """Extract each shipped JS glossary RegExp constructor, keyed by origin."""
    s3 = (APP_DIR / "s3_publish.py").read_text(encoding="utf-8")
    client = (APP_DIR / "static" / "glossary-client.js").read_text(encoding="utf-8")
    tpl = (APP_DIR / "templates" / "document.html").read_text(encoding="utf-8")
    found = {}
    # Greedy [^;]* then \) = up to the constructor's closing paren before ';'.
    for i, m in enumerate(re.findall(r"new RegExp\([^;]*esc\.join\('\|'\)[^;]*\)", s3)):
        found[f"s3_publish#{i}"] = m.replace("esc.join('|')", "TERMS")
    m = re.search(r"new RegExp\([^;]*escaped\.join\('\|'\)[^;]*\)", client)
    found["glossary-client"] = m.group(0).replace("escaped.join('|')", "TERMS")
    m = re.search(r"new RegExp\([^;]*\+ esc \+[^;]*\)", tpl)
    found["template.termExists"] = re.sub(r"\.test\(docText\)$", "", m.group(0)).replace("esc", "TERMS")
    return found


def test_no_ascii_word_boundary_left_in_js():
    for origin, src in _js_regex_sources().items():
        assert "\\\\b" not in src, f"{origin} still uses ASCII-only \\b: {src}"


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        pw = sync_api.sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as exc:  # browser binary missing
        pytest.skip(f"Chromium unavailable: {exc}")
    pg = browser.new_page()
    yield pg
    browser.close()
    pw.stop()


@pytest.mark.parametrize("origin", sorted(_js_regex_sources()))
def test_js_highlighters_match_indic_terms(page, origin):
    src = _js_regex_sources()[origin]
    cases = [
        ("निवासी", "यहाँ निवासी शब्द", True),
        ("नदी", "(नदी)", True),
        ("भारत", "भारत एक देश", True),
        ("भारत", "भारतीय लोग", False),
        ("नद", "नदी", False),
        ("carbon", "Carbon dioxide", True),
        ("carbon", "carbonate", False),
    ]
    for term, text, expected in cases:
        got = page.evaluate(
            "([src, term, text]) => { const TERMS = term; return eval(src).test(text); }",
            [src, term, text],
        )
        assert got is expected, f"{origin}: {term!r} in {text!r} -> {got}"
