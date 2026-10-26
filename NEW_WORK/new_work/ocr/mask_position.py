from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from new_work.domain import OcrLine


_ID_NUMBER = re.compile(
    r"(?<!\d)(\d{6})([^\d]{0,3})(\d)([^\d]{0,3})(\d{6})(?!\d)"
)


def detect_mask_position(
    lines: tuple[OcrLine, ...], image_path: Path | None = None
) -> str | None:
    """Find the last six digits using the OCR row and, when available, its pixels."""
    image_rgb = _load_rgb(image_path) if image_path is not None else None
    matches: list[str] = []
    for line in lines:
        if len(re.sub(r"\D", "", line.text)) != 13 or line.box is None:
            continue
        bounds = (
            _mask_position_for_number_row(image_rgb, line)
            if image_rgb is not None
            else ""
        )
        if not bounds:
            bounds = mask_position_from_box(line.box, line.text)
        if bounds:
            matches.append(bounds)

    if len(matches) != 1:
        return None
    return matches[0]


def mask_position_from_box(
    box: tuple[float, float, float, float], text: str = ""
) -> str | None:
    """Estimate the rear-six bounds, accounting for the OCR separator glyphs."""
    left, top, right, bottom = box
    width = right - left
    if width <= 0 or bottom <= top:
        return None

    match = _ID_NUMBER.search(text)
    if match:
        # The text detector's row width includes what OCR saw between the two
        # number groups. Driver cards can yield multiple separator glyphs over
        # the security print; counting them moves the mask past rear digit one.
        front_separator = len(match.group(2))
        rear_separator = len(match.group(4))
        total_units = 13 + front_separator + rear_separator
        before_target_units = 7 + front_separator
        ratio = before_target_units / total_units
    else:
        # Keep a conservative fallback when OCR recognizes no separator.
        ratio = 7.5 / 13.5

    rear_left = round(left + width * ratio)
    return f"{rear_left},{round(top)},{round(right)},{round(bottom)}"


def _load_rgb(image_path: Path) -> Any | None:
    try:
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(image_path) as source:
            return np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
    except Exception:
        return None


def _mask_position_for_number_row(image_rgb: Any, line: OcrLine) -> str:
    """Return tight image bounds around only the final six printed digits."""
    if line.box is None:
        return ""
    left, top, right, bottom = line.box
    try:
        import cv2
        import numpy as np

        image_height, image_width = image_rgb.shape[:2]
        x1, x2 = max(0, int(left)), min(image_width, int(right))
        y1, y2 = max(0, int(top)), min(image_height, int(bottom))
        if x2 <= x1 or y2 <= y1:
            return ""
        gray = cv2.cvtColor(image_rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        digit_groups = _registration_digit_groups(gray)
        if digit_groups is None:
            return ""
        _, rear = digit_groups
        target_left = x1 + rear[1][0]
        target_right = x1 + rear[-1][1]
        average_digit_width = sum(right - left for left, right in rear) / len(rear)
        gap_before_mask = max(0, rear[1][0] - rear[0][1])
        horizontal_pad = min(
            max(1, round(average_digit_width * 0.08)),
            gap_before_mask // 2,
        )
        target_left -= horizontal_pad
        target_right = min(x2, target_right + max(1, round(average_digit_width * 0.08)))
        ink = gray < 70
        target_pixels = ink[
            :, max(0, target_left - x1):max(1, target_right - x1)
        ]
        row_counts = target_pixels.sum(axis=1)
        active_rows = np.flatnonzero(
            row_counts > max(2, round(target_pixels.shape[1] * 0.07))
        )
        if len(active_rows):
            vertical_pad = max(1, round((y2 - y1) * 0.04))
            tight_top = max(y1, y1 + int(active_rows[0]) - vertical_pad)
            tight_bottom = min(y2, y1 + int(active_rows[-1]) + 1 + vertical_pad)
        else:
            tight_top, tight_bottom = round(top), round(bottom)
        return f"{target_left},{tight_top},{target_right},{tight_bottom}"
    except Exception:
        return ""


def _registration_digit_groups(
    gray: Any,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]] | None:
    """Find the six front and seven rear digit glyphs inside an OCR box."""
    import cv2
    import numpy as np

    height = gray.shape[0]
    for threshold in (50, 60, 70, 80):
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            np.uint8(gray < threshold), connectivity=8
        )
        components: list[tuple[int, int]] = []
        for x, _, width, char_height, area in stats[1:count]:
            if (
                char_height >= round(height * 0.38)
                and 3 <= width <= round(height * 0.8)
                and area >= 10
            ):
                components.append((int(x), int(x + width)))
        components.sort()

        groups: list[list[tuple[int, int]]] = []
        max_character_gap = round(height * 0.62)
        for component in components:
            if (
                groups
                and component[0] - groups[-1][-1][1] <= max_character_gap
            ):
                groups[-1].append(component)
            else:
                groups.append([component])

        for group in groups:
            if len(group) == 13:
                return group[:6], group[6:]

        matches = [
            (groups[index - 1], groups[index])
            for index in range(1, len(groups))
            if len(groups[index - 1]) == 6 and len(groups[index]) == 7
        ]
        if len(matches) == 1:
            return matches[0]
    return None
