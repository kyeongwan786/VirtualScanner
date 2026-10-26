from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from new_work.domain import OcrLine


ID_TYPES: dict[str, dict[str, str]] = {
    "1001": {"type": "R", "name": "주민등록증"},
    "1101": {"type": "D", "name": "운전면허증"},
    "1102": {"type": "D", "name": "운전면허증"},
    "1201": {"type": "D", "name": "운전면허증"},
    "1301": {"type": "S", "name": "복지카드"},
    "1303": {"type": "S", "name": "복지카드"},
    "1302": {"type": "N", "name": "국가유공자증"},
    "1401": {"type": "F", "name": "\uc678\uad6d\uc778\ub4f1\ub85d\uc99d"},
    "2001": {"type": "P", "name": "여권"},
    "2002": {"type": "T", "name": "외국인 여권"},
    "3001": {"type": "K", "name": "주민등록 발급확인서"},
}


@dataclass(frozen=True)
class IdTypeDetection:
    name: str | None
    type_code: str | None
    candidate_codes: tuple[str, ...] = ()
    matched_text: str | None = None
    preferred_code: str | None = None

    @property
    def code(self) -> str | None:
        if self.preferred_code in self.candidate_codes:
            return self.preferred_code
        return self.candidate_codes[0] if len(self.candidate_codes) == 1 else None


# Specific phrases precede broader ones so passport / issue-confirmation labels
# are not swallowed by the generic passport / resident-card rules.
_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("주민등록증발급확인서", "주민등록발급확인서", "주민등록증발급신청확인서"), ("3001",)),
    (("외국인여권", "여권외국인"), ("2002",)),
    (("외국인등록증", "외국인등록"), ("1401",)),
    (("국가유공자증", "국가유공자", "보훈보상대상자증"), ("1302",)),
    (("장애인복지카드", "복지카드"), ("1301", "1303")),
    (("자동차운전면허증", "운전면허증", "운전면허"), ("1101", "1102", "1201")),
    (("여권",), ("2001",)),
    (("주민등록증",), ("1001",)),
)


def _normalize(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).casefold()


def detect_id_type(lines: Iterable[OcrLine | str]) -> IdTypeDetection:
    """Classify a document family from OCR text without claiming verification.

    When multiple legacy codes share the same visible name, all candidates are
    returned. The legacy UI default (1102 for driver licenses, 1301 for welfare
    cards) is selected automatically; the GUI still lets the operator choose
    another code from the table.
    """
    source_lines = tuple(lines)
    raw_lines = tuple(
        line.text if isinstance(line, OcrLine) else str(line)
        for line in source_lines
    )
    # Recognize common Korean and English headings on foreign resident cards.
    foreign_text = "".join(
        re.sub(r"[^0-9A-Za-z\uac00-\ud7a3]", "", text).casefold()
        for text in raw_lines
    )
    if any(marker in foreign_text for marker in (
        "\uc678\uad6d\uc778\ub4f1\ub85d", "alienregistration", "residencecard",
    )):
        return IdTypeDetection(
            name="\uc678\uad6d\uc778\ub4f1\ub85d\uc99d", type_code="F",
            candidate_codes=("1401",), matched_text="\uc678\uad6d\uc778\ub4f1\ub85d\uc99d",
        )
    partial_foreign_heading = any(
        isinstance(line, OcrLine)
        and line.source == "korean_focus"
        and "\uc678\uad6d" in _normalize(line.text)
        for line in source_lines
    )
    if (
        ("\uc678\uad6d\uc778" in foreign_text or partial_foreign_heading)
        and "\uc5ec\uad8c" not in foreign_text
    ):
        # Low-resolution cards may yield only the first word from the heading.
        # Keep explicit foreign-passport text out of this fallback.
        return IdTypeDetection(
            name="\uc678\uad6d\uc778\ub4f1\ub85d\uc99d", type_code="F",
            candidate_codes=("1401",), matched_text="\uc678\uad6d\uc778",
        )
    normalized = "".join(_normalize(text) for text in raw_lines)
    for phrases, codes in _RULES:
        for phrase in phrases:
            if _normalize(phrase) in normalized:
                first = ID_TYPES[codes[0]]
                # Keep the user-provided display name for exact type codes.
                name = first["name"]
                return IdTypeDetection(
                    name=name,
                    type_code=first["type"],
                    candidate_codes=codes,
                    matched_text=phrase,
                    preferred_code=(
                        "1102" if "1102" in codes else
                        "1301" if "1301" in codes else None
                    ),
                )
    # A tightly cropped card-heading OCR pass can still substitute a couple
    # of Hangul syllables (for example, 주민등록증 -> 주만능록증). Use fuzzy
    # matching only on that isolated heading crop, and only when one card type
    # is a clear best match; this avoids guessing from arbitrary card text.
    heading_texts = [
        _normalize(line.text)
        for line in source_lines
        if isinstance(line, OcrLine) and line.source == "korean_focus"
    ]
    heading_matches: dict[tuple[str, ...], tuple[float, str]] = {}
    for phrases, codes in _RULES:
        for phrase in phrases:
            target = _normalize(phrase)
            if len(target) < 4:
                continue
            for heading in heading_texts:
                for width in range(max(4, len(target) - 1), len(target) + 2):
                    for start in range(max(0, len(heading) - width + 1)):
                        candidate = heading[start:start + width]
                        score = SequenceMatcher(None, target, candidate).ratio()
                        if score > heading_matches.get(codes, (0.0, ""))[0]:
                            heading_matches[codes] = (score, phrase)
    if heading_matches:
        ranked = sorted(
            ((score, codes, phrase)
             for codes, (score, phrase) in heading_matches.items()),
            key=lambda item: item[0],
            reverse=True,
        )
        if (
            ranked[0][0] >= 0.60
            and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.10)
        ):
            score, codes, phrase = ranked[0]
            first = ID_TYPES[codes[0]]
            return IdTypeDetection(
                name=first["name"],
                type_code=first["type"],
                candidate_codes=codes,
                matched_text=phrase,
                preferred_code=(
                    "1102" if "1102" in codes else
                    "1301" if "1301" in codes else None
                ),
            )
    return IdTypeDetection(name=None, type_code=None)
