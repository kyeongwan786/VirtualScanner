from __future__ import annotations

import re
from urllib.parse import quote_plus

from new_work.domain import OcrLine


def detect_mask_position(lines: tuple[OcrLine, ...]) -> str | None:
    """Encode the bounding rectangle of the last six RRN digits.

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
        left, top, right, bottom = line.box
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            continue

        # Six front digits, the half-width hyphen, and the first rear digit
        # precede the target six digits: 7.5 units out of 13.5 total units.
        rear_left = round(left + width * (7.5 / 13.5))
        rear_right = round(right)
        bounds = f"{rear_left},{round(top)},{rear_right},{round(bottom)}"
        matches.append(quote_plus(bounds))

    if len(matches) != 1:
        return None
    return matches[0]
