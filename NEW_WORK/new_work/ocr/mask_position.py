from __future__ import annotations

import re
from urllib.parse import quote_plus

from new_work.domain import OcrLine


def detect_mask_position(lines: tuple[OcrLine, ...]) -> str | None:
    """Encode the bounding rectangle of the seven RRN digits after the hyphen.

    PaddleOCR supplies a polygon around the full resident number. Since its six
    front digits, separator, and seven rear digits are printed in a single line,
    the rear region is estimated proportionally within that line's text box.
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

        # Six full-width digits plus the half-width hyphen precede the seven
        # rear digits: 6.5 units out of a total 13.5 character-width units.
        rear_left = round(left + width * (6.5 / 13.5))
        rear_right = round(right)
        bounds = f"{rear_left},{round(top)},{rear_right},{round(bottom)}"
        matches.append(quote_plus(bounds))

    if len(matches) != 1:
        return None
    return matches[0]
