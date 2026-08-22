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

    # Run PaddleOCR (detection + recognition) — v3.7 API
    ocr = PaddleOCR()
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
        if confidence < 0.5 or len(text) < 2:
            continue

        # Get bounding polygon
        poly = rec_polys[i] if i < len(rec_polys) else None
        if poly is None:
            continue

        # poly is a numpy array of points [[x1,y1],[x2,y2],...]
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
            "confidence": round(float(confidence), 3),
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
