"""
Interactive Diagram Module.

Detects text labels in diagram images using PaddleOCR, then generates
an interactive HTML overlay with hoverable/clickable hotspots.
"""

from __future__ import annotations

import logging
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

    # Run PaddleOCR (detection + recognition)
    ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    results = ocr.ocr(image_path, cls=True)

    if not results or not results[0]:
        return {"success": False, "error": "No text labels detected in this image"}

    labels: list[dict[str, Any]] = []

    for line in results[0]:
        # line format: [bbox_points, (text, confidence)]
        bbox_points = line[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        text_info = line[1]    # (text_string, confidence)

        text = text_info[0].strip()
        confidence = text_info[1]

        # Skip low confidence or very short text
        if confidence < 0.5 or len(text) < 2:
            continue

        # Calculate bounding box in percentage (for responsive overlay)
        x_coords = [p[0] for p in bbox_points]
        y_coords = [p[1] for p in bbox_points]

        x_min = min(x_coords)
        y_min = min(y_coords)
        x_max = max(x_coords)
        y_max = max(y_coords)

        labels.append({
            "text": text,
            "x_pct": round(x_min / img_width * 100, 2),
            "y_pct": round(y_min / img_height * 100, 2),
            "w_pct": round((x_max - x_min) / img_width * 100, 2),
            "h_pct": round((y_max - y_min) / img_height * 100, 2),
            "confidence": round(confidence, 3),
        })

    if not labels:
        return {"success": False, "error": "No readable labels found"}

    return {
        "success": True,
        "labels": labels,
        "image_width": img_width,
        "image_height": img_height,
        "label_count": len(labels),
    }
