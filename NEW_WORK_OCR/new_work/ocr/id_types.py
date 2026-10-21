from __future__ import annotations

import re
from dataclasses import dataclass
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
    "1401": {"type": "F", "name": "외국인등록증"},
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

    @property
    def code(self) -> str | None:
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
    returned and `code` stays None until a person chooses the exact code.
    """
    normalized = "".join(
        _normalize(line.text if isinstance(line, OcrLine) else str(line))
        for line in lines
    )
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
                )
    return IdTypeDetection(name=None, type_code=None)
