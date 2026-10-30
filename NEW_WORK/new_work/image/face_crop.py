from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps


# (crop width/height, saved width/height, face side, face-top offset)
# Keep the visible selection and the legacy output dimensions identical. The
# legacy crop contract uses 340x406 for 1001 and 320x420 for other ID codes.
_ID_CROP_SPECS: dict[str, tuple[int, int, int, int, str | None, float]] = {
    "1001": (340, 406, 340, 406, "right", 0.45),
    "1101": (320, 420, 320, 420, "left", 0.45),
    "1102": (320, 420, 320, 420, "left", 0.45),
    "1201": (320, 420, 320, 420, "left", 0.45),
    "1301": (320, 420, 320, 420, None, 0.45),
    "1302": (320, 420, 320, 420, "left", 0.45),
    "1303": (320, 420, 320, 420, None, 0.45),
    "1401": (320, 420, 320, 420, "left", 0.45),
    "2001": (320, 420, 320, 420, None, 0.45),
    "2002": (320, 420, 320, 420, None, 0.45),
    "3001": (320, 420, 320, 420, None, 0.45),
}


def crop_id_portrait_jpeg(
    image_path: Path,
    *,
    detector: Any | None = None,
    recog_sub_cd: str | None = None,
    require_face: bool = False,
    crop_bounds: tuple[int, int, int, int] | None = None,
) -> bytes | None:
    """Crop the complete printed portrait panel around the primary face.

    The selected code determines the source photo window, saved pixel size,
    expected face side, and layout-specific face anchor.
    ``require_face`` is retained for caller compatibility; this function never
    substitutes a guessed crop when face detection fails.
    """
    with Image.open(Path(image_path)) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGB")
    try:
        bounds = crop_bounds or id_portrait_bounds(
            source,
            detector=detector,
            recog_sub_cd=recog_sub_cd,
            require_face=require_face,
        )
        if bounds is None:
            return None
        left, top, right, bottom = (int(value) for value in bounds)
        if (
            left < 0 or top < 0 or right > source.width or bottom > source.height
            or right - left < 20 or bottom - top < 20
        ):
            raise ValueError("직접 지정한 크롭 영역이 원본 이미지 범위를 벗어났습니다.")
        portrait = source.crop((left, top, right, bottom))
        output_size = _ID_CROP_SPECS.get(
            recog_sub_cd or "", (320, 420, 320, 420, None, 0.45)
        )[2:4]
        if portrait.size != output_size:
            portrait = portrait.resize(output_size, Image.Resampling.LANCZOS)
        output = BytesIO()
        portrait.save(output, format="JPEG", quality=95)
        portrait.close()
        return output.getvalue()
    finally:
        source.close()


def parse_crop_bounds(value: object) -> tuple[int, int, int, int] | None:
    """Parse the private crop-coordinate field carried in a work package."""
    if not value:
        return None
    try:
        bounds = tuple(int(part.strip()) for part in str(value).split(","))
    except (TypeError, ValueError):
        raise ValueError("크롭 영역 좌표 형식이 올바르지 않습니다.") from None
    if len(bounds) != 4:
        raise ValueError("크롭 영역 좌표는 왼쪽, 위, 오른쪽, 아래 순서여야 합니다.")
    left, top, right, bottom = bounds
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise ValueError("크롭 영역 좌표의 크기가 올바르지 않습니다.")
    return left, top, right, bottom


def id_portrait_size(
    recog_sub_cd: str | None,
    source_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Return the source window, scaled from a standard 1008x640 card image."""
    spec = _ID_CROP_SPECS.get(recog_sub_cd or "")
    crop_width, crop_height = (spec[:2] if spec is not None else (320, 420))
    if source_size is None:
        return crop_width, crop_height
    source_width, source_height = source_size
    scale = max(source_width / 1008, source_height / 640, 1.0)
    return round(crop_width * scale), round(crop_height * scale)


def id_portrait_fallback_bounds(
    source: Image.Image,
    *,
    recog_sub_cd: str | None = None,
) -> tuple[int, int, int, int] | None:
    """Return a visible, resolution-scaled starting box when face detection misses.

    This is only a UI placement hint. Callers must not silently save it as an
    automatically detected portrait; the operator should confirm its position.
    """
    spec = _ID_CROP_SPECS.get(recog_sub_cd or "")
    crop_width, crop_height = id_portrait_size(recog_sub_cd, source.size)
    crop_width = min(crop_width, source.width)
    crop_height = min(crop_height, source.height)
    if crop_width < 20 or crop_height < 20:
        return None

    expected_side = spec[4] if spec is not None else None
    center_x_ratio = 0.75 if expected_side == "right" else (
        0.25 if expected_side == "left" else 0.5
    )
    center_x = round(source.width * center_x_ratio)
    center_y = round(source.height * 0.52)
    left = min(max(center_x - crop_width // 2, 0), source.width - crop_width)
    top = min(max(center_y - crop_height // 2, 0), source.height - crop_height)
    return left, top, left + crop_width, top + crop_height


def id_portrait_bounds(
    source: Image.Image,
    *,
    detector: Any | None = None,
    recog_sub_cd: str | None = None,
    require_face: bool = False,
) -> tuple[int, int, int, int] | None:
    """Return the exact source-image rectangle used for the idCrop portrait."""
    try:
        gray = np.asarray(source.convert("L"))
        face_detector = detector if detector is not None else _face_detector()
        detection_scale = max(source.width / 1008, source.height / 640, 1.0)
        if detection_scale > 1.0:
            detection_width = max(1, round(source.width / detection_scale))
            detection_height = max(1, round(source.height / detection_scale))
            gray = cv2.resize(
                gray, (detection_width, detection_height),
                interpolation=cv2.INTER_AREA,
            )
            scale_x = source.width / detection_width
            scale_y = source.height / detection_height
        else:
            scale_x = scale_y = 1.0
        faces = face_detector.detectMultiScale(
            gray,
            scaleFactor=1.05,
            minNeighbors=4,
            minSize=(24, 30),
        )
    except (cv2.error, RuntimeError):
        return None
    if faces is None or len(faces) == 0:
        return None
    if scale_x != 1.0 or scale_y != 1.0:
        faces = np.asarray(faces, dtype=np.float64)
        faces[:, 0] *= scale_x
        faces[:, 1] *= scale_y
        faces[:, 2] *= scale_x
        faces[:, 3] *= scale_y
        faces = np.rint(faces).astype(np.int32)
    spec = _ID_CROP_SPECS.get(recog_sub_cd or "")
    if spec is None:
        # Preserve the pre-type-selection helper behavior for callers that do
        # not provide a document code. The application always supplies one.
        if recog_sub_cd:
            spec = (320, 420, 320, 420, None, 0.45)
        else:
            x, y, width, height = map(int, max(faces, key=lambda face: int(face[2]) * int(face[3])))
            center_x = x + width / 2
            left = round(center_x - width * 1.05)
            right = round(center_x + width * 1.05)
            top = round(y - height * 0.22)
            bottom = round(y + height * 2.0)
            left = max(0, left)
            top = max(0, top)
            right = min(source.width, right)
            bottom = min(source.height, bottom)
            return (left, top, right, bottom) if right > left and bottom > top else None
    expected_side = spec[4]
    if expected_side:
        if expected_side == "right":
            side_faces = [face for face in faces if int(face[0]) + int(face[2]) / 2 >= source.width * 0.52]
        else:
            # Driver cards have a large portrait panel on the far left and a
            # small security portrait on the far right. A 48% cutoff admitted
            # false detections in overprinted text near the card center.
            left_limit = 0.42 if recog_sub_cd in {"1101", "1102", "1201"} else 0.48
            side_faces = [
                face for face in faces
                if int(face[0]) + int(face[2]) / 2 <= source.width * left_limit
            ]
        if side_faces:
            faces = side_faces
        else:
            return None
    ordered = sorted(faces, key=lambda face: int(face[2]) * int(face[3]), reverse=True)
    if len(ordered) > 1:
        largest_area = int(ordered[0][2]) * int(ordered[0][3])
        second_area = int(ordered[1][2]) * int(ordered[1][3])
        if second_area >= largest_area * 0.75:
            return None

    x, y, width, height = (int(value) for value in ordered[0])
    crop_width, crop_height = id_portrait_size(recog_sub_cd, source.size)
    face_top_offset = spec[5]
    if source.width < crop_width or source.height < crop_height:
        return None

    # The detector positions a reference-size source window around the portrait.
    # Large photos scale that window up; saving still uses the legacy output size.
    face_center_x = x + width / 2
    left = round(face_center_x - crop_width / 2)
    top = round(y - height * face_top_offset)
    left = min(max(0, left), source.width - crop_width)
    top = min(max(0, top), source.height - crop_height)
    right = left + crop_width
    bottom = top + crop_height
    return left, top, right, bottom


def _face_detector():
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(str(cascade_path))
    if detector.empty():
        raise RuntimeError(f"OpenCV 얼굴 검출 모델을 불러오지 못했습니다: {cascade_path}")
    return detector
