"""
Glossary Term Highlighting Module

Takes raw HTML and a list of glossary terms, then wraps matched terms
in accessible <dfn> tooltip tags — traversing only text nodes to avoid
breaking existing markup, links, or attributes.

Usage:
    from glossary_highlight import highlight_glossary_terms

    html = "<p>Photosynthesis occurs in the chloroplast.</p>"
    glossary = [
        {"term": "Photosynthesis", "definition": "The process by which plants convert light into energy"},
        {"term": "chloroplast", "definition": "An organelle found in plant cells where photosynthesis takes place"}
    ]
    result = highlight_glossary_terms(html, glossary)
    # result: <p><dfn class="glossary-term" ...>Photosynthesis</dfn> occurs in the <dfn ...>chloroplast</dfn>.</p>

Performance:
    - Compiles a single regex from all terms (O(1) pattern matching per text node)
    - Skips elements that should never be highlighted (code, pre, script, style, dfn, a)
    - First-occurrence-only option to avoid over-highlighting
    - Case-insensitive matching with original case preservation
"""

from __future__ import annotations

import re
import logging
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

_log = logging.getLogger(__name__)

# Elements whose text content should NOT be highlighted
SKIP_ELEMENTS = frozenset([
    'script', 'style', 'code', 'pre', 'kbd', 'var', 'samp',
    'dfn', 'a', 'button', 'input', 'textarea', 'select',
    'h1', 'h2', 'h3',  # Don't highlight inside headings
    'figcaption',
])


def highlight_glossary_terms(
    html: str,
    glossary: list[dict[str, str]],
    first_occurrence_only: bool = False,
    max_highlights_per_term: int = 0,
) -> str:
    """
    Process HTML and wrap glossary terms in <dfn> tooltip tags.

    Args:
        html: Raw HTML string to process
        glossary: List of dicts with 'term' and 'definition' keys
            Example: [{"term": "Photosynthesis", "definition": "..."}]
        first_occurrence_only: If True, only highlight first occurrence of each term
        max_highlights_per_term: Max times a term can be highlighted (if not first_only)

    Returns:
        Modified HTML string with glossary terms wrapped in <dfn> tags
    """
    if not html or not glossary:
        return html

    # Filter valid glossary entries
    valid_terms = [
        g for g in glossary
        if g.get('term') and g.get('definition') and len(g['term'].strip()) >= 2
    ]

    if not valid_terms:
        return html

    # Build a single compiled regex pattern for all terms (efficient)
    # Sort by length descending so longer terms match first ("carbon dioxide" before "carbon")
    valid_terms.sort(key=lambda g: len(g['term']), reverse=True)

    # Escape regex special characters in terms
    escaped_terms = [re.escape(g['term'].strip()) for g in valid_terms]
    # Use word boundaries to avoid partial matches
    pattern = re.compile(
        r'\b(' + '|'.join(escaped_terms) + r')\b',
        re.IGNORECASE
    )

    # Build a lookup map: lowercase term -> definition
    term_map: dict[str, dict[str, str]] = {}
    for g in valid_terms:
        term_map[g['term'].strip().lower()] = {
            'term': g['term'].strip(),
            'definition': g['definition'].strip(),
        }

    # Track occurrences per term (for first_occurrence_only / max_highlights)
    occurrence_count: dict[str, int] = {t.lower(): 0 for t in term_map}

    # Parse HTML
    soup = BeautifulSoup(html, 'html.parser')

    # Collect text nodes to process (avoid modifying tree while iterating)
    text_nodes: list[NavigableString] = []
    for text_node in soup.find_all(string=True):
        # Skip if inside a protected element
        if _is_inside_skip_element(text_node):
            continue
        # Skip if text is too short or whitespace-only
        if not text_node.strip() or len(text_node.strip()) < 2:
            continue
        text_nodes.append(text_node)

    # Process each text node
    for text_node in text_nodes:
        original_text = str(text_node)

        # Check if any glossary term exists in this text (quick check before regex)
        if not pattern.search(original_text):
            continue

        # Split text by matches and rebuild with <dfn> wrappers
        parts = []
        last_end = 0

        for match in pattern.finditer(original_text):
            matched_text = match.group(0)
            term_lower = matched_text.lower()

            # Check occurrence limit
            if first_occurrence_only and occurrence_count.get(term_lower, 0) >= 1:
                continue
            if max_highlights_per_term and max_highlights_per_term > 0 and occurrence_count.get(term_lower, 0) >= max_highlights_per_term:
                continue

            occurrence_count[term_lower] = occurrence_count.get(term_lower, 0) + 1

            # Add text before match
            if match.start() > last_end:
                parts.append(original_text[last_end:match.start()])

            # Build the <dfn> element
            term_info = term_map.get(term_lower, {})
            definition = term_info.get('definition', '')
            # Escape definition for use in attribute
            safe_def = definition.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')

            dfn_html = (
                f'<dfn class="glossary-term" '
                f'tabindex="0" '
                f'role="term" '
                f'aria-label="{matched_text}: {safe_def}" '
                f'data-definition="{safe_def}" '
                f'title="{safe_def}">'
                f'{matched_text}'
                f'</dfn>'
            )
            parts.append(dfn_html)
            last_end = match.end()

        # If no replacements were made, skip
        if not parts:
            continue

        # Add remaining text after last match
        if last_end < len(original_text):
            parts.append(original_text[last_end:])

        # Replace the text node with the new HTML
        new_html = ''.join(parts)
        new_soup = BeautifulSoup(new_html, 'html.parser')
        text_node.replace_with(new_soup)

    return str(soup)


def _is_inside_skip_element(node: NavigableString) -> bool:
    """Check if a text node is inside an element that should not be highlighted."""
    parent = node.parent
    while parent:
        if isinstance(parent, Tag):
            if parent.name in SKIP_ELEMENTS:
                return True
            # Also skip if parent already has a glossary-term class
            if parent.get('class') and 'glossary-term' in parent.get('class', []):
                return True
        parent = parent.parent
    return False


def remove_glossary_highlights(html: str) -> str:
    """
    Remove all glossary highlights from HTML, restoring original text.

    Useful when glossary terms change and you need to re-process.
    """
    if not html:
        return html

    soup = BeautifulSoup(html, 'html.parser')

    for dfn in soup.find_all('dfn', class_='glossary-term'):
        # Replace <dfn> with its text content
        dfn.replace_with(dfn.get_text())

    return str(soup)


# --- CSS for the glossary tooltips (inject into document <style>) ---
GLOSSARY_CSS = """
/* Glossary term highlighting */
.glossary-term {
    font-style: normal;
    border-bottom: 2px dotted #3282b8;
    cursor: help;
    position: relative;
    display: inline;
}

.glossary-term:hover,
.glossary-term:focus {
    background: #eff6ff;
    border-bottom-color: #0f4c75;
    outline: none;
}

.glossary-term:hover::after,
.glossary-term:focus::after {
    content: attr(data-definition);
    position: absolute;
    bottom: calc(100% + 8px);
    left: 0;
    min-width: 200px;
    max-width: 300px;
    padding: 0.6rem 0.8rem;
    background: #1a1a2e;
    color: #f0f0f0;
    border-radius: 8px;
    font-size: 0.8rem;
    font-weight: normal;
    line-height: 1.4;
    white-space: normal;
    z-index: 100;
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
    pointer-events: none;
}

.glossary-term:hover::before,
.glossary-term:focus::before {
    content: '';
    position: absolute;
    bottom: calc(100% + 2px);
    left: 16px;
    border: 6px solid transparent;
    border-top-color: #1a1a2e;
    z-index: 101;
}

@media (max-width: 600px) {
    .glossary-term:hover::after,
    .glossary-term:focus::after {
        min-width: 160px;
        max-width: 220px;
        font-size: 0.75rem;
    }
}
"""
