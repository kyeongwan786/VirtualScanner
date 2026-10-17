from __future__ import annotations

import os
from pathlib import Path

from PIL import Image


def find_latest_id_crop(
    image_directory: Path,
    recog_sub_cd: str,
    *,
    output_name: str = "cropped_idcrop_image.jpg",
) -> Path | None:
    """Port of com_sk/com_kt find_latest_image, including its file filter."""
    directory = Path(image_directory)
    candidates = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name.lower().endswith(("m.png", "m.jpg", "m.jpeg"))
    ]
    if not candidates:
        return None
    selected = max(candidates, key=os.path.getmtime)

    crop_width, crop_height = (320, 420)
    if recog_sub_cd == "1001":
        crop_width, crop_height = (340, 406)
    elif recog_sub_cd == "1102":
        crop_width, crop_height = (320, 420)

    with Image.open(selected) as image:
        width, height = image.size
        left = (width - crop_width) // 2
        top = (height - crop_height) // 2
        cropped = image.crop((left, top, left + crop_width, top + crop_height)).convert("RGB")
        output = selected.parent / output_name
        cropped.save(output, format="JPEG", quality=95)
    return output


def reencode_jpeg(source: Path, destination: Path, *, quality: int = 100) -> Path:
    """Port of com_kt save_image: Pillow JPEG encode at the requested quality."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.save(destination, format="JPEG", quality=quality)
    return destination
