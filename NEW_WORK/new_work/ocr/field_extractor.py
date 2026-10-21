from __future__ import annotations

from datetime import date
import re
import unicodedata

from new_work.domain import OcrLine


_RRN = re.compile(r"(?<!\d)(\d{6})\D{0,3}(\d{7})(?!\d)")
_LICENSE = re.compile(r"(?<!\d)(\d{2})\D?(\d{2})\D?(\d{6})\D?(\d{2})(?!\d)")
_LICENSE_LABEL = re.compile(r"운전\s*면허|면허\s*번호")
_DATE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년|[./,:-])?\s*(\d{1,2})\s*(?:월|[./,:-])?\s*(\d{1,2})\s*일?(?!\d)"
)
_DATE_PREFIX = re.compile(r"(?<!\d)(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]?\s*$")
_DATE_DAY_ONLY = re.compile(r"^\s*(\d{1,2})\s*[.]?\s*$")
_NAME = re.compile(r"[가-힣]{2,4}")
_ISSUER = re.compile(r"(?:시장|구청장|군수|읍장|면장|동장|경찰청장|도로교통공단|시청|구청)\s*$")
_ISSUER_LABEL = re.compile(r"발급\s*(?:기관|처|처명|자)")
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

    license_numbers: set[str] = set()
    for line in lines:
        match = _LICENSE.search(line.text)
        if match:
            license_numbers.add("".join(match.groups()))

    # PaddleOCR can split the printed label and number into adjacent rows.
    # Join digit groups only in a short window containing a license label and
    # only when the combined value has the known 2-2-6-2 digit structure.
    for start in range(len(lines)):
        for end in range(start + 1, min(len(lines), start + 4) + 1):
            window = lines[start:end]
            if not any(_LICENSE_LABEL.search(line.text) for line in window):
                continue
            digits = "".join(re.findall(r"\d", " ".join(line.text for line in window)))
            if len(digits) == 12:
                license_numbers.add(digits)
    if len(license_numbers) == 1:
        values["drvLicenIsueNum"] = license_numbers.pop()

    date_values: set[str] = set()
    issuer_date_values: set[str] = set()
    for line in lines:
        line_dates = _dates_in_text(line.text)
        date_values.update(line_dates)
        if _ISSUER.search(line.text):
            issuer_date_values.update(line_dates)
    # OCR can split a printed date over adjacent text boxes (for example,
    # "2023.01." followed by "15"). Re-scan short adjacent groups as one line.
    for start in range(len(lines)):
        for end in range(start + 2, min(len(lines), start + 3) + 1):
            date_values.update(_dates_in_text(" ".join(line.text for line in lines[start:end])))
    for index, line in enumerate(lines[:-1]):
        prefix = _DATE_PREFIX.search(line.text)
        day_match = _DATE_DAY_ONLY.fullmatch(lines[index + 1].text)
        if not prefix or not day_match:
            continue
        year, month = prefix.groups()
        day = day_match.group(1)
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            date_values.add(f"{year}{int(month):02d}{int(day):02d}")
    preferred_dates = issuer_date_values if issuer_date_values else date_values
    if len(preferred_dates) == 1:
        values["isueDt"] = preferred_dates.pop()

    issuers = [
        _strip_issue_date(line.text)
        for line in lines
        if _ISSUER.search(line.text) and _strip_issue_date(line.text)
    ]
    for index, line in enumerate(lines):
        if _ISSUER_LABEL.search(line.text):
            candidate = re.sub(r"^발급\s*(?:기관|처|처명|자)\s*[:：]?\s*", "", line.text).strip()
            if candidate:
                candidate = _strip_issue_date(candidate)
                if candidate:
                    issuers.append(candidate)
            elif index + 1 < len(lines) and lines[index + 1].text.strip():
                issuers.append(_strip_issue_date(lines[index + 1].text.strip()))
    unique_issuers = list(dict.fromkeys(issuers))
    if len(unique_issuers) == 1:
        values["isuePlc"] = re.sub(r"^발급\s*(?:기관|처|처명|자)\s*[:：]?\s*", "", unique_issuers[0])

    values["addr"] = _extract_address(lines)
    values["custNm"] = _extract_name(lines, numbered[0][0] if len(numbered) == 1 else None)
    return values


def _dates_in_text(text: str) -> set[str]:
    """Read valid YYYYMMDD dates with OCR-friendly punctuation or no separators."""
    values: set[str] = set()
    text = text.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}))
    matches = list(_DATE.finditer(text))
    matches.extend(re.finditer(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", text))
    for match in matches:
        year, month, day = map(int, match.groups())
        try:
            parsed = date(year, month, day)
        except ValueError:
            continue
        values.add(parsed.strftime("%Y%m%d"))
    return values


def _strip_issue_date(text: str) -> str:
    """Keep an issue agency when OCR joins it to the date on the same line."""
    return _DATE.sub("", text, count=1).strip(" .,:;-/")


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
    candidates: list[str] = []
    # Start from the road/building line as OCR may prepend hidden characters or
    # merge the province/city into an unrecognized token on the preceding row.
    for index, line in enumerate(lines):
        text = _clean_address_line(line.text)
        if _DATE.search(text):
            continue
        has_road_number = bool(re.search(r"(?:로|길)\s*\d", text))
        has_address_number = bool(re.search(r"\d+\s*(?:번지|동|호|층)", text))
        if not (has_road_number or has_address_number):
            continue
        address_parts: list[str] = []
        for previous in reversed(lines[max(0, index - 3):index]):
            prior = re.sub(r"^[^가-힣]+", "", _clean_address_line(previous.text))
            if not prior or _DATE.search(prior) or _ISSUER.search(prior):
                break
            if _ADDRESS_PREFIX.search(prior) or re.search(r"(?:특별시|광역시|도|시|군|구|읍|면|동)", prior):
                address_parts.insert(0, prior)
            else:
                break
        address_parts.append(text)
        candidates.append(" ".join(address_parts))

    for index, line in enumerate(lines):
        first = _clean_address_line(line.text)
        # A road address can contain the syllables "시장" (for example,
        # "화성시 장안면"). Trust a regional prefix and the street/number
        # pattern below instead of classifying this row as an issuer first.
        first = re.sub(r"^[^가-힣]+", "", first)
        if not _ADDRESS_PREFIX.search(first):
            continue
        parts = [first]
        # OCR may return the region, street, and building number on separate rows.
        for following in lines[index + 1:index + 4]:
            text = _clean_address_line(following.text)
            if not text or _DATE.search(text):
                break
            has_road_number = bool(re.search(r"(?:로|길)\s*\d", text))
            has_address_number = bool(re.search(r"\d+\s*(?:번지|동|호|층)", text))
            if _ISSUER.search(text) and not (has_road_number or has_address_number):
                break
            if _ADDRESS_PREFIX.search(text) or has_road_number or has_address_number or re.search(r"\d", text):
                parts.append(text)
            else:
                break
        candidate = " ".join(parts)
        if re.search(r"(?:시|도|군|구|읍|면|동|로|길)\s*\d", candidate) or _ADDRESS_DETAIL.search(candidate):
            candidates.append(candidate)
    if not candidates:
        return ""
    # Prefer the candidate that contains a road/building number. Adjacent OCR
    # boxes can create overlapping candidates for the same address; choosing
    # the longest complete candidate avoids blanking an otherwise clear value.
    detailed = [
        candidate for candidate in candidates
        if re.search(r"(?:로|길)\s*\d", candidate) or _ADDRESS_DETAIL.search(candidate)
    ]
    return max(detailed or candidates, key=len)


def _clean_address_line(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text))
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", value).strip()
