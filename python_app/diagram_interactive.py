"""
Interactive Diagram Module.

Detects text labels in diagram images using RapidOCR (ONNX Runtime backend),
then generates an interactive HTML overlay with hoverable/clickable hotspots.
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

    Uses RapidOCR to detect text labels and their bounding box positions.
    Returns a data structure suitable for rendering interactive hotspots.
    """
    import cv2
    from rapidocr import RapidOCR

    if not Path(image_path).exists():
        return {"success": False, "error": "Image file not found"}

    img = cv2.imread(image_path)
    if img is None:
        return {"success": False, "error": "Cannot read image"}

    img_height, img_width = img.shape[:2]

    # Run RapidOCR
    ocr = RapidOCR()
    result = ocr(image_path)

    # RapidOCR v3.9+ returns RapidOCROutput with .boxes, .txts, .scores
    if result.boxes is None or len(result.boxes) == 0:
        return {"success": False, "error": "No text labels detected"}

    labels: list[dict[str, Any]] = []

    for i in range(len(result.boxes)):
        bbox = result.boxes[i]   # numpy array [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        text = result.txts[i].strip() if i < len(result.txts) else ""
        confidence = result.scores[i] if i < len(result.scores) else 0

        if confidence < 0.3 or len(text) < 2:
            continue

        # Skip watermarks
        if text.lower() in ("ps", "\u00a9", "\u00ae", "tm", "p", "s"):
            continue

        # Skip figure captions
        if re.match(r"^\([a-z]\)", text):
            continue

        # Skip long text (captions, not labels)
        if len(text) > 35:
            continue

        x_coords = [p[0] for p in bbox]
        y_coords = [p[1] for p in bbox]
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

    # Merge adjacent labels
    labels = _merge_adjacent_labels(labels, img_width, img_height)

    if not labels:
        return {"success": False, "error": "No readable labels found"}

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


def _merge_adjacent_labels(labels, img_width, img_height):
    if not labels:
        return labels

    sorted_labels = sorted(labels, key=lambda l: (l["y_min"], l["x_min"]))
    merged = []
    used = set()

    for i, label in enumerate(sorted_labels):
        if i in used:
            continue
        current = dict(label)
        used.add(i)

        changed = True
        while changed:
            changed = False
            for j in range(len(sorted_labels)):
                if j in used:
                    continue
                other = sorted_labels[j]

                current_cy = (current["y_min"] + current["y_max"]) / 2
                other_cy = (other["y_min"] + other["y_max"]) / 2
                current_h = current["y_max"] - current["y_min"]
                other_h = other["y_max"] - other["y_min"]

                is_same_line = abs(current_cy - other_cy) < max(current_h, other_h) * 0.7
                h_gap = other["x_min"] - current["x_max"]
                h_adjacent = -10 < h_gap < 40

                x_overlap = min(current["x_max"], other["x_max"]) - max(current["x_min"], other["x_min"])
                v_gap = other["y_min"] - current["y_max"]
                is_vertically_stacked = x_overlap > 0 and 0 < v_gap < max(current_h, other_h) * 1.5

                if (is_same_line and h_adjacent) or is_vertically_stacked:
                    current["text"] = current["text"] + " " + other["text"]
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

        if len(current["text"]) <= 40:
            merged.append(current)

    return merged
