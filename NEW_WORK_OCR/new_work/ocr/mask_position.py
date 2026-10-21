from __future__ import annotations

import re

from new_work.domain import OcrLine


def detect_mask_position(lines: tuple[OcrLine, ...]) -> str | None:
    """Return raw comma-separated bounds for the last six RRN digits.

    PaddleOCR supplies a polygon around the full resident number. Since its six
    front digits, separator, and seven rear digits are printed in a single line,
    the last-six-digit region is estimated proportionally within that line's
    text box. The first digit after the hyphen is intentionally excluded.
    Return None when the number or its location is absent or ambiguous.
    """
    matches: list[str] = []
    for line in lines:
        digits = re.sub(r"\D", "", line.text)
        if len(digits) != 13 or line.box is None:
            continue
        bounds = mask_position_from_box(line.box)
        if bounds:
            matches.append(bounds)

    if len(matches) != 1:
        return None
    return matches[0]


def mask_position_from_box(
    box: tuple[float, float, float, float],
) -> str | None:
    """Estimate the last six number digits within a single 13-digit text box."""
    left, top, right, bottom = box
    width = right - left
    if width <= 0 or bottom <= top:
        return None

    # Six front digits, the half-width hyphen, and the first rear digit
    # precede the target six digits: 7.5 units out of 13.5 total units.
    rear_left = round(left + width * (7.5 / 13.5))
    return f"{rear_left},{round(top)},{round(right)},{round(bottom)}"
