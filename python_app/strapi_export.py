"""
Strapi DIKSHA CMS Export Module.

Exports converted HTML content to the DIKSHA Strapi CMS following the
content-manager API structure:
  Textbook (existing) → Chapter → Sections → Content Blocks (dynamic zone)

API: /content-manager/collection-types/api::<type>.<type>
Auth: Bearer JWT (admin login token)
Relations: linked via documentId (string)
Media: uploaded via POST /upload, referenced by numeric id
"""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Strapi content-manager base path
CM_PATH = "/content-manager/collection-types"


def export_to_strapi_diksha(
    base_url: str,
    jwt_token: str,
    textbook_document_id: str,
    chapter_title: str,
    chapter_order: int,
    body_html: str,
    local_dir: Path | None,
    http_client: Any,
) -> dict:
    """
    Export a converted document to DIKSHA Strapi CMS.

    Flow:
    1. Upload all local media files → get Strapi media IDs
    2. Create Chapter linked to the Textbook documentId
    3. Split HTML into sections by headings
    4. For each section, parse content into content_blocks (dynamic zone)
    5. Create Section entries linked to the Chapter documentId

    Args:
        base_url: Strapi base URL (no trailing slash)
        jwt_token: Admin JWT token from /admin/login
        textbook_document_id: documentId of the parent Textbook
        chapter_title: Title for the new Chapter
        chapter_order: Order number for the Chapter
        body_html: Full HTML content of the document body
        local_dir: Local directory containing media files (images/, media/)
        http_client: requests-compatible HTTP client

    Returns:
        Dict with success status, chapter documentId, section count
    """
    base_url = base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
    }

    # Strip editor-only UI elements that shouldn't go to the CMS
    body_html = _strip_editor_ui(body_html)

    # Step 1: Upload media files and build path→id mapping
    media_map: dict[str, int] = {}
    if local_dir and local_dir.exists():
        media_map = _upload_all_media(base_url, jwt_token, local_dir, http_client)

    # Step 2: Create Chapter linked to Textbook
    chapter_payload = {
        "title": chapter_title,
        "order": chapter_order,
        "textbook": textbook_document_id,
    }

    ch_resp = http_client.post(
        f"{base_url}{CM_PATH}/api::chapter.chapter",
        json=chapter_payload,
        headers=headers,
        timeout=30,
    )

    if ch_resp.status_code not in (200, 201):
        return {"success": False, "error": f"Failed to create chapter: {ch_resp.text[:300]}"}

    ch_data = ch_resp.json()
    chapter_doc_id = ch_data.get("documentId", ch_data.get("data", {}).get("documentId", ""))

    if not chapter_doc_id:
        return {"success": False, "error": "Chapter created but no documentId returned"}

    # Step 3: Split HTML into sections
    sections = _split_html_into_sections(body_html)

    # Step 4: Create each Section with content_blocks
    created_sections = 0
    for idx, section in enumerate(sections):
        content_blocks = _html_to_content_blocks(section["content"], media_map, base_url)

        section_payload = {
            "title": section["title"],
            "order": idx + 1,
            "chapter": chapter_doc_id,
            "content_blocks": content_blocks,
        }

        sec_resp = http_client.post(
            f"{base_url}{CM_PATH}/api::section.section",
            json=section_payload,
            headers=headers,
            timeout=30,
        )

        if sec_resp.status_code in (200, 201):
            created_sections += 1
        else:
            _log.warning(f"Failed to create section '{section['title']}': {sec_resp.text[:200]}")

    return {
        "success": True,
        "chapter_document_id": chapter_doc_id,
        "sections_created": created_sections,
        "sections_total": len(sections),
        "media_uploaded": len(media_map),
    }


def _upload_all_media(
    base_url: str, jwt_token: str, local_dir: Path, http_client: Any
) -> dict[str, int]:
    """Upload all media files from images/ and media/ directories. Returns path→id map."""
    media_map: dict[str, int] = {}
    headers = {"Authorization": f"Bearer {jwt_token}"}

    for subdir in ("images", "media"):
        media_dir = local_dir / subdir
        if not media_dir.exists():
            continue
        for file_path in media_dir.rglob("*"):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue
            # Skip markdown files and very small files (likely metadata)
            if file_path.suffix in (".md", ".json") or file_path.stat().st_size < 100:
                continue

            relative_path = f"{subdir}/{file_path.relative_to(media_dir)}"
            try:
                with open(file_path, "rb") as f:
                    files = {"files": (file_path.name, f, _guess_mime(file_path))}
                    resp = http_client.post(
                        f"{base_url}/upload",
                        headers=headers,
                        files=files,
                        timeout=120,
                    )
                if resp.status_code in (200, 201):
                    resp_data = resp.json()
                    if isinstance(resp_data, list) and len(resp_data) > 0:
                        media_id = resp_data[0].get("id")
                        media_url = resp_data[0].get("url", "")
                        if media_id:
                            media_map[relative_path] = media_id
                            # Also map the full URL for reference
                            if media_url:
                                full_url = media_url if media_url.startswith("http") else base_url + media_url
                                media_map[f"url:{relative_path}"] = full_url
            except Exception as exc:
                _log.warning(f"Failed to upload {relative_path}: {exc}")

    return media_map


def _split_html_into_sections(html: str) -> list[dict[str, str]]:
    """Split HTML by h1/h2/h3 headings into sections with title and content."""
    parts = re.split(r"(<h[1-3][^>]*>.*?</h[1-3]>)", html, flags=re.IGNORECASE | re.DOTALL)

    sections: list[dict[str, str]] = []
    current_title = "Introduction"
    current_content = ""

    for part in parts:
        heading_match = re.match(r"<h[1-3][^>]*>(.*?)</h[1-3]>", part, flags=re.IGNORECASE | re.DOTALL)
        if heading_match:
            if current_content.strip():
                sections.append({"title": current_title, "content": current_content.strip()})
            current_title = re.sub(r"<[^>]+>", "", heading_match.group(1)).strip()
            current_content = ""
        else:
            current_content += part

    if current_content.strip():
        sections.append({"title": current_title, "content": current_content.strip()})

    if not sections:
        sections.append({"title": "Content", "content": html})

    return sections


def _html_to_content_blocks(
    html: str, media_map: dict[str, int], base_url: str
) -> list[dict]:
    """
    Convert an HTML section into Strapi content_blocks (dynamic zone array).

    Detects:
    - Images → blocks.image-block
    - Videos → blocks.video-block
    - Flip cards → blocks.flashcard-set
    - H5P containers → blocks.h5p-block
    - Everything else → blocks.text-block
    """
    blocks: list[dict] = []

    # Split HTML into meaningful chunks
    # Pattern: split around images, videos, flip-card-decks, h5p containers, media icons
    splitter = re.compile(
        r"("
        r"<figure[^>]*>.*?</figure>"
        r"|<video[^>]*>.*?</video>"
        r"|<div[^>]*class=\"flip-card-deck\"[^>]*>.*?</div>\s*</div>\s*</div>"
        r"|<div[^>]*class=\"h5p-inline-container\"[^>]*>.*?</div>"
        r"|<div[^>]*class=\"section-media\"[^>]*>.*?</div>"
        r"|<button[^>]*class=\"[^\"]*media-icon[^\"]*has-content[^\"]*\"[^>]*>.*?</button>"
        r"|<img[^>]*/?>"
        r")",
        re.IGNORECASE | re.DOTALL,
    )

    parts = splitter.split(html)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Media icon buttons with content → convert to proper Strapi blocks
        media_btn_match = re.search(
            r'data-media-type="([^"]*)"[^>]*data-media-src="([^"]*)"',
            part, re.IGNORECASE
        )
        if media_btn_match:
            media_type = media_btn_match.group(1)
            media_src = media_btn_match.group(2)
            if media_src:
                block = _media_icon_to_block(media_type, media_src, media_map, base_url)
                if block:
                    blocks.append(block)
            continue

        # Section-media div (contains multiple media icons) → extract each
        if "section-media" in part:
            icon_matches = re.finditer(
                r'data-media-type="([^"]*)"[^>]*data-media-src="([^"]+)"',
                part, re.IGNORECASE
            )
            for m in icon_matches:
                block = _media_icon_to_block(m.group(1), m.group(2), media_map, base_url)
                if block:
                    blocks.append(block)
            continue

        # Image block
        img_match = re.search(r'<img[^>]*src="([^"]*)"[^>]*/?>',  part, re.IGNORECASE)
        if img_match and ("<figure" in part or part.startswith("<img")):
            src = img_match.group(1)
            caption_match = re.search(r"<figcaption>(.*?)</figcaption>", part, re.IGNORECASE)
            caption = re.sub(r"<[^>]+>", "", caption_match.group(1)) if caption_match else ""
            alt_match = re.search(r'alt="([^"]*)"', part, re.IGNORECASE)
            alt_text = alt_match.group(1) if alt_match else ""

            block: dict = {
                "__component": "blocks.image-block",
                "caption": caption,
                "alt_text": alt_text,
                "alignment": "center",
                "size": "large",
            }
            # Try to resolve local image to Strapi media ID
            media_id = media_map.get(src)
            if media_id:
                block["image"] = media_id
            blocks.append(block)
            continue

        # Video block
        if "<video" in part or re.search(r'class="[^"]*video', part, re.IGNORECASE):
            video_src = re.search(r'src="([^"]*)"', part, re.IGNORECASE)
            video_url = video_src.group(1) if video_src else ""
            blocks.append({
                "__component": "blocks.video-block",
                "video_url": video_url,
                "caption": "",
            })
            continue

        # Flip card deck → flashcard-set
        if "flip-card-deck" in part or "flip-card" in part:
            cards = _extract_flashcards(part)
            if cards:
                blocks.append({
                    "__component": "blocks.flashcard-set",
                    "title": "Flash Cards",
                    "cards": cards,
                })
            continue

        # H5P block
        if "h5p-inline-container" in part:
            h5p_src = re.search(r'data-h5p-src="([^"]*)"', part, re.IGNORECASE)
            blocks.append({
                "__component": "blocks.h5p-block",
                "title": "Interactive Content",
                "h5p_url": h5p_src.group(1) if h5p_src else "",
                "source": "custom",
            })
            continue

        # Default: text block (skip if empty/whitespace only)
        text_content = part.strip()
        if text_content and text_content != "<br>" and len(re.sub(r"<[^>]+>", "", text_content).strip()) > 0:
            blocks.append({
                "__component": "blocks.text-block",
                "callout_type": "none",
                "body": text_content,
            })

    # Ensure at least one block
    if not blocks:
        blocks.append({
            "__component": "blocks.text-block",
            "callout_type": "none",
            "body": html,
        })

    return blocks


def _extract_flashcards(html: str) -> list[dict[str, str]]:
    """Extract front/back card pairs from a flip-card-deck HTML."""
    cards: list[dict[str, str]] = []
    card_pattern = re.compile(
        r'class="flip-card-front"[^>]*>(.*?)</div>.*?class="flip-card-back"[^>]*>(.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in card_pattern.finditer(html):
        front = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        back = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        # Remove "Click to flip" hints
        front = re.sub(r"Click to flip\s*(back)?", "", front).strip()
        back = re.sub(r"Click to flip\s*(back)?", "", back).strip()
        if front or back:
            cards.append({"front": front, "back": back, "hint": ""})
    return cards


def _guess_mime(file_path: Path) -> str:
    """Guess MIME type for a file."""
    mime = mimetypes.guess_type(str(file_path))[0]
    return mime or "application/octet-stream"


def _strip_editor_ui(html: str) -> str:
    """
    Remove editor-only UI attributes and elements from HTML before CMS export.

    Removes:
    - Block manipulation buttons (move up/down, delete)
    - draggable and contenteditable attributes
    - Empty media-icon buttons (no content attached)
    """
    # Remove block move/delete buttons
    html = re.sub(
        r'<button[^>]*class="[^"]*block-(?:move-up|move-down|delete)[^"]*"[^>]*>.*?</button>',
        '',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove empty media-icon buttons (data-media-src="") — keep ones with URLs
    html = re.sub(
        r'<button[^>]*data-media-src=""[^>]*>.*?</button>',
        '',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove draggable attributes
    html = re.sub(r'\s*draggable="true"', '', html)

    # Remove contenteditable attributes
    html = re.sub(r'\s*contenteditable="[^"]*"', '', html)

    # Clean up multiple consecutive blank lines
    html = re.sub(r'\n{3,}', '\n\n', html)

    return html.strip()


def _media_icon_to_block(
    media_type: str, media_src: str, media_map: dict[str, int], base_url: str
) -> dict | None:
    """
    Convert a media icon button into the appropriate Strapi content block.

    Mapping:
    - video → blocks.video-block
    - audio → blocks.audio-block
    - pptx  → blocks.file-upload-block
    - h5p   → blocks.h5p-block
    - url   → blocks.video-block (if YouTube/Vimeo) or blocks.media-block
    - glossary → blocks.text-block (formatted as definition)
    """
    if not media_src:
        return None

    if media_type == "video":
        return {
            "__component": "blocks.video-block",
            "video_url": media_src,
            "caption": "",
        }

    elif media_type == "audio":
        block: dict = {
            "__component": "blocks.audio-block",
            "title": "Audio",
            "duration": "",
        }
        # Check if file was uploaded to Strapi
        media_id = media_map.get(media_src)
        if media_id:
            block["audioFile"] = media_id
        return block

    elif media_type == "pptx":
        block = {
            "__component": "blocks.file-upload-block",
            "title": "Presentation",
            "fileType": "ppt",
        }
        media_id = media_map.get(media_src)
        if media_id:
            block["file"] = media_id
        return block

    elif media_type == "h5p":
        return {
            "__component": "blocks.h5p-block",
            "title": "Interactive Content",
            "h5p_url": media_src,
            "source": "custom",
        }

    elif media_type == "url":
        # Check if it's a video URL (YouTube/Vimeo)
        yt_match = re.search(r"(?:youtube\.com|youtu\.be)", media_src, re.IGNORECASE)
        vimeo_match = re.search(r"vimeo\.com", media_src, re.IGNORECASE)
        if yt_match or vimeo_match:
            return {
                "__component": "blocks.video-block",
                "video_url": media_src,
                "caption": "",
            }
        else:
            return {
                "__component": "blocks.media-block",
                "media_url": media_src,
                "mimeType": "text/html",
            }

    elif media_type == "glossary":
        parts = media_src.split("|")
        term = parts[0].strip() if parts else media_src
        definition = parts[1].strip() if len(parts) > 1 else ""
        return {
            "__component": "blocks.text-block",
            "callout_type": "info",
            "body": f"<p><strong>{term}</strong>: {definition}</p>",
        }

    return None
