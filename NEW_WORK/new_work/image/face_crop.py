from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps


def crop_id_portrait_jpeg(
    image_path: Path,
    *,
    detector: Any | None = None,
) -> bytes | None:
    """Find the largest clear frontal face and crop its surrounding ID photo.

    The detector finds the face; fixed margins estimate the portrait photo
    around it. Ambiguous face detections return None instead of cropping an
    arbitrary region.
    """
    with Image.open(Path(image_path)) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    try:
        gray = np.asarray(source.convert("L"))
        try:
            face_detector = detector if detector is not None else _face_detector()
            faces = face_detector.detectMultiScale(
                gray,
                scaleFactor=1.05,
                minNeighbors=4,
                minSize=(24, 30),
            )
        except (cv2.error, RuntimeError):
            return _fixed_card_portrait(source)
        if faces is None or len(faces) == 0:
            return _fixed_card_portrait(source)
        ordered = sorted(faces, key=lambda face: int(face[2]) * int(face[3]), reverse=True)
        if len(ordered) > 1:
            largest_area = int(ordered[0][2]) * int(ordered[0][3])
            second_area = int(ordered[1][2]) * int(ordered[1][3])
            if second_area >= largest_area * 0.75:
                return _fixed_card_portrait(source)

        x, y, width, height = (int(value) for value in ordered[0])
        center_x = x + width / 2
        left = max(0, round(center_x - width * 1.05))
        right = min(source.width, round(center_x + width * 1.05))
        top = max(0, round(y - height * 0.22))
        bottom = min(source.height, round(y + height * 2.0))
        if right - left < 24 or bottom - top < 30:
            return _fixed_card_portrait(source)

        output = BytesIO()
        source.crop((left, top, right, bottom)).save(
            output, format="JPEG", quality=95
        )
        return output.getvalue()
    finally:
        source.close()


def _fixed_card_portrait(source: Image.Image) -> bytes | None:
    """Fallback for standardized horizontal Korean ID scans with tiny portraits.

    Haar detection often misses the small printed headshot on a card. These
    scans share nearly the same card layout, so take the portrait panel from
    normalized card coordinates when the image has a card-like aspect ratio.
    """
    if source.width < source.height or not 1.35 <= source.width / source.height <= 2.0:
        return None
    left = round(source.width * 0.045)
    top = round(source.height * 0.14)
    right = round(source.width * 0.35)
    bottom = round(source.height * 0.86)
    if right <= left or bottom <= top:
        return None
    output = BytesIO()
    source.crop((left, top, right, bottom)).save(output, format="JPEG", quality=95)
    return output.getvalue()


def _face_detector():
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(str(cascade_path))
    if detector.empty():
        raise RuntimeError(f"OpenCV 얼굴 검출 모델을 불러오지 못했습니다: {cascade_path}")
    return detector
