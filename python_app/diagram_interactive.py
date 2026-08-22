"""
Interactive Diagram Module.

Detects text labels in diagram images using PaddleOCR, then generates
an interactive HTML overlay with hoverable/clickable hotspots.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


def make_diagram_interactive(image_path: str) -> dict:
    """
    Analyze a diagram image and extract labeled hotspots.

    Uses PaddleOCR to detect text labels and their bounding box positions.
    Returns a data structure suitable for rendering interactive hotspots
    over the image in the browser.

    Args:
        image_path: Absolute path to the diagram image file.

    Returns:
        Dict with:
        - success: bool
        - labels: list of {text, x, y, width, height} (in percentage of image)
        - image_width: original image width
        - image_height: original image height
    """
    import cv2
    from paddleocr import PaddleOCR

    if not Path(image_path).exists():
        return {"success": False, "error": "Image file not found"}

    # Read image dimensions
    img = cv2.imread(image_path)
    if img is None:
        return {"success": False, "error": "Cannot read image"}

    img_height, img_width = img.shape[:2]

    # Run PaddleOCR (detection + recognition) — v3.7 API
    # Lower box_thresh to detect smaller/lighter text labels like "Pupil"
    ocr = PaddleOCR(text_det_box_thresh=0.4, text_det_limit_side_len=960)
    results = list(ocr.predict(image_path))

    if not results:
        return {"success": False, "error": "No text labels detected in this image"}

    r = results[0]
    rec_texts = r.get("rec_texts", [])
    rec_scores = r.get("rec_scores", [])
    rec_polys = r.get("rec_polys", [])

    if not rec_texts:
        return {"success": False, "error": "No text labels detected in this image"}

    labels: list[dict[str, Any]] = []

    for i, text in enumerate(rec_texts):
        text = text.strip()
        confidence = rec_scores[i] if i < len(rec_scores) else 0

        # Skip low confidence or very short text
        if confidence < 0.3 or len(text) < 2:
            continue

        # Skip common watermarks/artifacts
        if text.lower() in ("ps", "©", "®", "tm", "p", "s"):
            continue

        # Skip figure captions like "(a) Near point of..."
        if re.match(r"^\([a-z]\)", text):
            continue

        # Skip very long text (captions, not labels)
        if len(text) > 35:
            continue

        # Get bounding polygon
        poly = rec_polys[i] if i < len(rec_polys) else None
        if poly is None:
            continue

        x_coords = [p[0] for p in poly]
        y_coords = [p[1] for p in poly]

        x_min = min(x_coords)
        y_min = min(y_coords)
        x_max = max(x_coords)
        y_max = max(y_coords)

        labels.append({
            "text": text,
            "x_pct": round(float(x_min) / img_width * 100, 2),
            "y_pct": round(float(y_min) / img_height * 100, 2),
            "w_pct": round(float(x_max - x_min) / img_width * 100, 2),
            "h_pct": round(float(y_max - y_min) / img_height * 100, 2),
            "x_min": float(x_min),
            "y_min": float(y_min),
            "x_max": float(x_max),
            "y_max": float(y_max),
            "confidence": round(float(confidence), 3),
        })

    # Merge fragmented labels on the same line (within 15px vertical, adjacent horizontal)
    labels = _merge_adjacent_labels(labels, img_width, img_height)

    if not labels:
        return {"success": False, "error": "No readable labels found"}

    # Remove internal coordinate fields before returning
    for label in labels:
        label.pop("x_min", None)
        label.pop("y_min", None)
        label.pop("x_max", None)
        label.pop("y_max", None)

    return {
        "success": True,
        "labels": labels,
        "image_width": img_width,
        "image_height": img_height,
        "label_count": len(labels),
    }


def _merge_adjacent_labels(
    labels: list[dict[str, Any]], img_width: int, img_height: int
) -> list[dict[str, Any]]:
    """
    Merge text fragments that are adjacent (horizontally or vertically stacked).

    Handles cases like:
    - "Crysta" + "line lens" → "Crystalline lens" (horizontal)
    - "Apparent" / "star position" → "Apparent star position" (vertical stack)
    - "Refractive index" / "increasing" → "Refractive index increasing" (vertical)
    """
    if not labels:
        return labels

    sorted_labels = sorted(labels, key=lambda l: (l["y_min"], l["x_min"]))
    merged: list[dict[str, Any]] = []
    used: set = set()

    for i, label in enumerate(sorted_labels):
        if i in used:
            continue

        current = dict(label)
        used.add(i)

        # Multiple passes to catch chains (A+B then AB+C)
        changed = True
        while changed:
            changed = False
            for j in range(len(sorted_labels)):
                if j in used:
                    continue
                other = sorted_labels[j]

                # Horizontal merge: same line (centers within 20px), gap < 40px
                current_cy = (current["y_min"] + current["y_max"]) / 2
                other_cy = (other["y_min"] + other["y_max"]) / 2
                current_h = current["y_max"] - current["y_min"]
                other_h = other["y_max"] - other["y_min"]

                is_same_line = abs(current_cy - other_cy) < max(current_h, other_h) * 0.7
                h_gap = other["x_min"] - current["x_max"]
                h_adjacent = -10 < h_gap < 40

                # Vertical merge: overlapping x range, vertical gap < line height
                x_overlap = (
                    min(current["x_max"], other["x_max"]) -
                    max(current["x_min"], other["x_min"])
                )
                v_gap = other["y_min"] - current["y_max"]
                is_vertically_stacked = x_overlap > 0 and 0 < v_gap < max(current_h, other_h) * 1.5

                if (is_same_line and h_adjacent) or is_vertically_stacked:
                    # Merge
                    separator = " " if is_same_line and h_adjacent else " "
                    current["text"] = current["text"] + separator + other["text"]
                    current["x_min"] = min(current["x_min"], other["x_min"])
                    current["y_min"] = min(current["y_min"], other["y_min"])
                    current["x_max"] = max(current["x_max"], other["x_max"])
                    current["y_max"] = max(current["y_max"], other["y_max"])
                    current["x_pct"] = round(current["x_min"] / img_width * 100, 2)
                    current["y_pct"] = round(current["y_min"] / img_height * 100, 2)
                    current["w_pct"] = round((current["x_max"] - current["x_min"]) / img_width * 100, 2)
                    current["h_pct"] = round((current["y_max"] - current["y_min"]) / img_height * 100, 2)
                    current["confidence"] = max(current["confidence"], other["confidence"])
                    used.add(j)
                    changed = True

        # Post-merge: skip if result is too long (became a caption after merging)
        if len(current["text"]) > 40:
            continue

        merged.append(current)

    return merged
