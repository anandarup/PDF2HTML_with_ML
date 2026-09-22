"""
Remove persisted H5P player markup from saved documents.

The editor mounts an H5P activity into
`<div class="h5p-inline-container" data-h5p-src="...">`. h5p-standalone fills
that container with its own DOM — a wrapper div holding an `about:blank`
iframe sized to the activity. Saves made before the editor started clearing
that markup stored it in the document, where it is inert: reloading the page
leaves a dead iframe, and an initialiser that skips containers which already
hold an iframe will never rebuild the player. The reader sees an empty box.

Only `data-h5p-src` needs to persist; the player is rebuilt from it on load.

The implementation is deliberately byte-surgical: every byte outside a
container's interior is preserved exactly, because this runs over hundreds of
already-published documents where re-serialising the HTML would risk changing
unrelated markup.
"""

from __future__ import annotations

import re
from typing import Tuple

__all__ = ["empty_h5p_containers"]

_CONTAINER_OPEN_RE = re.compile(
    r'<div\b[^>]*\bclass="[^"]*\bh5p-inline-container\b[^"]*"[^>]*>',
    re.IGNORECASE,
)
_DIV_TOKEN_RE = re.compile(r'<div\b|</div\s*>', re.IGNORECASE)


def empty_h5p_containers(html: str) -> Tuple[str, int]:
    """
    Empty the interior of every .h5p-inline-container in `html`.

    Returns:
        (cleaned_html, number_of_containers_emptied). Containers that are
        already empty, and containers whose closing tag cannot be found, are
        left exactly as they are.
    """
    if not html or "h5p-inline-container" not in html:
        return html, 0

    pieces = []
    position = 0
    emptied = 0

    for match in _CONTAINER_OPEN_RE.finditer(html):
        # Skip matches that fall inside a container already handled.
        if match.start() < position:
            continue

        depth = 1
        close_start = None
        for token in _DIV_TOKEN_RE.finditer(html, match.end()):
            if token.group(0).lower().startswith("<div"):
                depth += 1
                continue
            depth -= 1
            if depth == 0:
                close_start = token.start()
                break

        if close_start is None:
            continue  # unbalanced markup: leave it alone rather than guess

        if not html[match.end():close_start].strip():
            continue  # already empty

        pieces.append(html[position:match.end()])
        position = close_start
        emptied += 1

    if not emptied:
        return html, 0

    pieces.append(html[position:])
    return "".join(pieces), emptied
