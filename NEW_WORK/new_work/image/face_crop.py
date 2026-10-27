from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps


# (source window width/height, saved width/height, face side, face-top offset)
# Source windows follow the printed photo panel; saved dimensions follow the
# legacy idCrop/CR.jpg contract. Driver layouts have a small and large variant.
_ID_CROP_SPECS: dict[str, tuple[int, int, int, int, str | None, float]] = {
    "1001": (340, 406, 340, 406, "right", 0.45),
    "1101": (332, 384, 320, 420, "left", 0.45),
    "1102": (332, 384, 320, 420, "left", 0.45),
    "1201": (332, 384, 320, 420, "left", 0.45),
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


def id_portrait_size(recog_sub_cd: str | None) -> tuple[int, int]:
    """Return the fixed source-window size used for this identity type."""
    spec = _ID_CROP_SPECS.get(recog_sub_cd or "")
    if spec is None:
        return 320, 420
    return spec[0], spec[1]


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
    crop_width, crop_height = spec[:2]
    face_top_offset = spec[5]
    # On the newer driver-license layouts, Haar's face box starts well below
    # the top of the printed portrait panel. Anchor the panel higher than the
    # face; using the face box's shoulders as the panel reference pushed the
    # crop into the card's bottom edge on samples such as 8.jpg.
    if recog_sub_cd in {"1101", "1102", "1201"} and height / source.height >= 0.35:
        crop_width, crop_height = 338, 412
        face_top_offset = 0.41
    if source.width < crop_width or source.height < crop_height:
        return None

    # The detector positions a fixed source window around the printed portrait.
    # crop_id_portrait_jpeg then converts that window to the legacy output size.
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
