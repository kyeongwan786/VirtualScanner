from __future__ import annotations

import re

from new_work.domain import OcrLine


_RRN = re.compile(r"(?<!\d)(\d{6})\D{0,3}(\d{7})(?!\d)")
_LICENSE = re.compile(r"(?<!\d)(\d{2})\D?(\d{2})\D?(\d{6})\D?(\d{2})(?!\d)")
_DATE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년|[./-])\s*(\d{1,2})\s*(?:월|[./-])\s*(\d{1,2})\s*일?"
)
_DATE_PREFIX = re.compile(r"(?<!\d)(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]?\s*$")
_DATE_DAY_ONLY = re.compile(r"^\s*(\d{1,2})\s*[.]?\s*$")
_NAME = re.compile(r"[가-힣]{2,4}")
_ISSUER = re.compile(r"(?:시장|구청장|군수|읍장|면장|동장|경찰청장|도로교통공단)")
_ADDRESS_PREFIX = re.compile(
    r"^(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
)
_ADDRESS_DETAIL = re.compile(r"\d+\s*(?:번지|동|호|층)|아파트|빌라|\([^)]*동[^)]*\)")
_NON_NAME_WORDS = {
    "주민등록증", "운전면허증", "외국인등록증", "복지카드", "국가유공자증",
    "발급확인서", "대한민국", "주민등록", "운전면허", "대구광역시",
}


def extract_identity_fields(lines: tuple[OcrLine, ...]) -> dict[str, str]:
    """Best-effort extraction of visible ID text; missing/ambiguous fields stay blank."""
    values = {
        "ctzCorpNum": "",
        "custNm": "",
        "isueDt": "",
        "drvLicenIsueNum": "",
        "isuePlc": "",
        "addr": "",
        "nationClCd": "I",
    }
    numbered: list[tuple[OcrLine, str]] = []
    for line in lines:
        match = _RRN.search(line.text)
        if match:
            numbered.append((line, "".join(match.groups())))
    if len(numbered) == 1:
        values["ctzCorpNum"] = numbered[0][1]

    license_numbers: list[str] = []
    for line in lines:
        match = _LICENSE.search(line.text)
        if match:
            license_numbers.append("-".join(match.groups()))
    if len(set(license_numbers)) == 1 and license_numbers:
        values["drvLicenIsueNum"] = license_numbers[0]

    date_values: set[str] = set()
    for line in lines:
        match = _DATE.search(line.text)
        if match:
            year, month, day = match.groups()
            if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                date_values.add(f"{year}. {int(month)}. {int(day)}.")
    for index, line in enumerate(lines[:-1]):
        prefix = _DATE_PREFIX.search(line.text)
        day_match = _DATE_DAY_ONLY.fullmatch(lines[index + 1].text)
        if not prefix or not day_match:
            continue
        year, month = prefix.groups()
        day = day_match.group(1)
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            date_values.add(f"{year}. {int(month)}. {int(day)}.")
    if len(date_values) == 1:
        values["isueDt"] = date_values.pop()

    issuers = [line.text.strip() for line in lines if _ISSUER.search(line.text)]
    if len(issuers) == 1:
        values["isuePlc"] = issuers[0]

    values["addr"] = _extract_address(lines)
    values["custNm"] = _extract_name(lines, numbered[0][0] if len(numbered) == 1 else None)
    return values


def _extract_name(lines: tuple[OcrLine, ...], number_line: OcrLine | None) -> str:
    candidates: list[tuple[int, float, str]] = []
    number_y = number_line.box[1] if number_line and number_line.box else None
    for index, line in enumerate(lines):
        text = line.text.strip()
        if any(word in text for word in _NON_NAME_WORDS) or _ISSUER.search(text):
            continue
        words = _NAME.findall(text)
        if not words:
            continue
        name = words[0]
        if len(words) > 1 or not 2 <= len(name) <= 4:
            continue
        if number_y is not None and line.box is not None:
            y = line.box[1]
            if y >= number_y:
                continue
            candidates.append((1, y, name))
        else:
            confidence = line.confidence if line.confidence is not None else 0.0
            candidates.append((0, confidence, name))
    if not candidates:
        return ""
    # Prefer the nearest recognized name line above a resident-number line.
    if number_y is not None and any(item[0] for item in candidates):
        return max((item for item in candidates if item[0]), key=lambda item: item[1])[2]
    if len({item[2] for item in candidates}) != 1:
        return ""
    return candidates[0][2]


def _extract_address(lines: tuple[OcrLine, ...]) -> str:
    candidates = [
        index for index, line in enumerate(lines)
        if _ADDRESS_PREFIX.search(line.text.strip())
        and not _ISSUER.search(line.text)
        and re.search(r"(?:시|도|군|구|읍|면|동|로|길)", line.text)
    ]
    if len(candidates) != 1:
        return ""
    index = candidates[0]
    address = lines[index].text.strip()
    if index + 1 < len(lines):
        following = lines[index + 1].text.strip()
        if _ADDRESS_DETAIL.search(following):
            address = f"{address} {following}"
    return address
