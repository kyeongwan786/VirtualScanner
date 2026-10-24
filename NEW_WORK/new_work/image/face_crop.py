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
    recog_sub_cd: str | None = None,
    require_face: bool = False,
) -> bytes | None:
    """Crop the portrait panel around the primary face, excluding nearby fields.

    Code-specific KT crops are returned at the legacy dimensions. When
    ``require_face`` is true, a failed or ambiguous detection returns None
    instead of guessing a fixed card region.
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
            if require_face:
                return None
            return _fixed_card_portrait(source)
        if faces is None or len(faces) == 0:
            if require_face:
                return None
            return _fixed_card_portrait(source)
        ordered = sorted(faces, key=lambda face: int(face[2]) * int(face[3]), reverse=True)
        if len(ordered) > 1:
            largest_area = int(ordered[0][2]) * int(ordered[0][3])
            second_area = int(ordered[1][2]) * int(ordered[1][3])
            if second_area >= largest_area * 0.75:
                if require_face:
                    return None
                return _fixed_card_portrait(source)

        x, y, width, height = (int(value) for value in ordered[0])
        target_size = _target_crop_size(recog_sub_cd)
        if target_size:
            aspect_ratio = target_size[0] / target_size[1]
            # Include the printed portrait panel around the detected face,
            # while excluding the adjacent ID number and bottom card fields.
            crop_height = height * 1.63
            top = round(y - height * 0.20)
            bottom = round(top + crop_height)
            crop_width = crop_height * aspect_ratio
            center_x = x + width / 2
            left = round(center_x - crop_width / 2)
            right = round(center_x + crop_width / 2)
        else:
            # Keep the legacy SK crop when no document code was provided.
            center_x = x + width / 2
            left = round(center_x - width * 1.05)
            right = round(center_x + width * 1.05)
            top = round(y - height * 0.22)
            bottom = round(y + height * 2.0)
        # Clamp while preserving the selected face inside the crop.
        if left < 0:
            right = min(source.width, right - left)
            left = 0
        if right > source.width:
            left = max(0, left - (right - source.width))
            right = source.width
        if top < 0:
            bottom = min(source.height, bottom - top)
            top = 0
        if bottom > source.height:
            top = max(0, top - (bottom - source.height))
            bottom = source.height
        if right - left < 24 or bottom - top < 30:
            if require_face:
                return None
            return _fixed_card_portrait(source)

        portrait = source.crop((left, top, right, bottom))
        if target_size:
            portrait = portrait.resize(target_size, Image.Resampling.LANCZOS)
        output = BytesIO()
        portrait.save(output, format="JPEG", quality=95)
        portrait.close()
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


def _target_crop_size(recog_sub_cd: str | None) -> tuple[int, int] | None:
    if not recog_sub_cd:
        return None
    return (340, 406) if recog_sub_cd == "1001" else (320, 420)


def _face_detector():
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(str(cascade_path))
    if detector.empty():
        raise RuntimeError(f"OpenCV 얼굴 검출 모델을 불러오지 못했습니다: {cascade_path}")
    return detector
