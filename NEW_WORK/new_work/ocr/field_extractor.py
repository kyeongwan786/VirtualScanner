from __future__ import annotations

from datetime import date
import re
import unicodedata

from new_work.domain import OcrLine


# Keep patterns ASCII escaped so Windows console/file encodings cannot corrupt
# Korean labels in this module.
_HANGUL = r"\uac00-\ud7a3"
_RRN = re.compile(r"(?<!\d)(\d{6})\D{0,3}(\d)\D{0,3}(\d{6})(?!\d)")
_FOREIGN_CARD = re.compile(
    r"(?:\uc678\uad6d\uc778\s*\ub4f1\ub85d|alien\s*registration|residence\s*card)",
    re.IGNORECASE,
)
_FOREIGN_REG_LABEL = re.compile(
    r"(?:\uc678\uad6d\uc778\s*)?(?:\ub4f1\s*\ubc88\ud638|"
    r"\ub4f1\ub85d\s*\ubc88(?:\ud638)?)|"
    r"(?:alien\s*)?registration\s*(?:no\.?|number)", re.IGNORECASE,
)
_LICENSE = re.compile(r"(?<!\d)(\d{2})\D?(\d{2})\D?(\d{6})\D?(\d{2})(?!\d)")
_LICENSE_LABEL = re.compile(r"\uc6b4\uc804\s*\uba74\ud5c8|\uba74\ud5c8\s*\ubc88\ud638")
_DATE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:\ub144|[./,:-])?\s*(\d{1,2})\s*"
    r"(?:\uc6d4|[./,:-])?\s*(\d{1,2})\s*(?:\uc77c)?(?!\d)"
)
_DATE_PREFIX = re.compile(r"(?<!\d)(20\d{2})\s*[./-]\s*(\d{1,2})\s*[./-]?\s*$")
_DATE_DAY_ONLY = re.compile(r"^\s*(\d{1,2})\s*[.]?\s*$")
_KOREAN_NAME = re.compile(rf"[{_HANGUL}]{{2,4}}")
_KOREAN_NAME_LABEL = re.compile(
    rf"(?:\uc131\s*\uba85|\uc774\s*\ub984)\s*[:：]?\s*([{_HANGUL}]{{2,4}})"
)
_ISSUER = re.compile(
    r"(?:\uc2dc\uc7a5|\uad70\uc218|\uad6c\uccad\uc7a5|\uad70\uccad\uc7a5|"
    r"\uad6c\uccad|\uad70\uccad|\uc9c0\uccad\uc7a5|\uc18c\uc7a5|\uba74\uc7a5|"
    r"\uacbd\ucc30\uccad\uc7a5|\ub300\ud45c\uc774\uc0ac|\ubc95\ubb34\ubd80\s*\uc7a5\uad00|"
    r"\ucd9c\uc785\uad6d(?:\uad00\ub9ac)?(?:\s*[\u00b7\u30fb]\s*\uc678\uad6d\uc778)?"
    r"\s*(?:\uccad|\uc0ac\ubb34\uc18c)\s*\uc7a5?|"
    r"\ucd9c\uc785\uad6d\u00b7\uc678\uad6d\uc778\uccad)\s*$"
)
_ISSUER_LABEL = re.compile(r"\ubc1c\uae09\s*(?:\uae30\uad00|\ucc98|\uc790)?")
_FOREIGN_NAME_LABEL = re.compile(
    r"\uc131\s*\uba85|\uc0dd\s*\uba85|\uc774\s*\ub984|full\s*name|"
    r"\bname\b|\bn[a]?e\b", re.IGNORECASE
)
_ADDRESS_LABEL = re.compile(
    r"\uad6d\ub0b4\s*\uc8fc\uc18c|\ud604\uc8fc\uc18c|\uc8fc\uc18c|"
    r"\uccb4\ub958\uc9c0|\uac70\uc18c|address|place\s*of\s*residence", re.IGNORECASE
)
_FOREIGN_NAME_STOPWORDS = {
    "KOR", "KOREA", "REPUBLIC", "ALIEN", "RESIDENCE", "CARD",
    "REGISTRATION", "NAME", "FULL", "SEX", "GENDER", "NATIONALITY",
    "ADDRESS", "DATE", "BIRTH", "ISSUE", "EXPIRY", "PERIOD", "STATUS",
}
_ADDRESS_PREFIX = re.compile(
    r"^(?:\uc11c\uc6b8|\ubd80\uc0b0|\ub300\uad6c|\uc778\ucc9c|\uad11\uc8fc|"
    r"\ub300\uc804|\uc6b8\uc0b0|\uc138\uc885|\uacbd\uae30|\uac15\uc6d0|"
    r"\ucda9\ubd81|\ucda9\ub0a8|\uc804\ubd81|\uc804\ub0a8|\uacbd\ubd81|"
    r"\uacbd\ub0a8|\uc81c\uc8fc)")
_ADDRESS_DETAIL = re.compile(
    r"\d+\s*(?:\ubc88\uc9c0?|\ub3d9|\uce35|\ud638|\ubc88\uae38|\ub85c|\uae38)"
)
_FIELD_STOP = re.compile(
    r"\uc131\uba85|\uc774\ub984|\ub4f1\ub85d\s*\ubc88\ud638|\uad6d\uc801|"
    r"\uc131\ubcc4|\ucd9c\uc0dd|\ubc1c\uae09|\uccb4\ub958\uc790\uaca9|"
    r"name|nationality|sex|gender|address|alien\s*registration", re.IGNORECASE
)


def extract_identity_fields(lines: tuple[OcrLine, ...]) -> dict[str, str]:
    """Best-effort extraction of visible ID text; missing/ambiguous fields stay blank."""
    values = {
        "ctzCorpNum": "", "custNm": "", "isueDt": "",
        "drvLicenIsueNum": "", "isuePlc": "", "addr": "", "nationClCd": "I",
    }
    numbered: list[tuple[OcrLine, str]] = []
    for line in lines:
        match = _RRN.search(line.text)
        if match:
            numbered.append((line, "".join(match.groups())))

    is_foreigner_card = any(
        _FOREIGN_CARD.search(line.text)
        or (line.source == "korean_focus" and "\uc678\uad6d" in line.text)
        # The GUI runs this focused recognizer only after classifying the
        # document as a foreign resident card. Its result is therefore a
        # reliable card-type hint even when Korean OCR mangles the heading.
        or line.source == "english_focus"
        for line in lines
    )
    if len(numbered) == 1:
        values["ctzCorpNum"] = numbered[0][1]
    if is_foreigner_card:
        # The alien registration number is the ctzCorpNum value for this card.
        # Do not leave a different generic-number match in the field if the
        # labeled foreign number itself could not be read confidently.
        values["ctzCorpNum"] = _extract_foreigner_number(lines)

    license_numbers: set[str] = set()
    for line in lines:
        match = _LICENSE.search(line.text)
        if match:
            license_numbers.add("".join(match.groups()))
    for start in range(len(lines)):
        for end in range(start + 1, min(len(lines), start + 4) + 1):
            window = lines[start:end]
            if any(_LICENSE_LABEL.search(item.text) for item in window):
                digits = "".join(re.findall(r"\d", " ".join(item.text for item in window)))
                if len(digits) == 12:
                    license_numbers.add(digits)
        if len(license_numbers) == 1 and not is_foreigner_card:
            digits = license_numbers.pop()
            values["drvLicenIsueNum"] = (
                f"{digits[:2]}-{digits[2:4]}-{digits[4:10]}-{digits[10:]}"
            )

    dates: set[str] = set()
    issuer_dates: set[str] = set()
    for line in lines:
        line_dates = _dates_in_text(line.text)
        dates.update(line_dates)
        issuer_text = _strip_issue_date(line.text)
        if _ISSUER.search(issuer_text):
            issuer_dates.update(line_dates)
    for start in range(len(lines)):
        for end in range(start + 2, min(len(lines), start + 3) + 1):
            dates.update(_dates_in_text(" ".join(item.text for item in lines[start:end])))
    for index, line in enumerate(lines[:-1]):
        prefix = _DATE_PREFIX.search(line.text)
        day_match = _DATE_DAY_ONLY.fullmatch(lines[index + 1].text)
        if prefix and day_match:
            year, month = prefix.groups()
            day = day_match.group(1)
            if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                dates.add(f"{year}{int(month):02d}{int(day):02d}")
    chosen_dates = issuer_dates if issuer_dates else dates
    if len(chosen_dates) == 1:
        values["isueDt"] = chosen_dates.pop()

    issuers: list[str] = []
    for line in lines:
        candidate = _strip_issue_date(line.text)
        if _ISSUER.search(candidate):
            issuers.append(candidate)
    for index, line in enumerate(lines):
        label = _ISSUER_LABEL.search(line.text)
        if label:
            candidate = _strip_issue_date(line.text[label.end():].strip(" :："))
            if candidate:
                issuers.append(candidate)
            elif index + 1 < len(lines):
                candidate = _strip_issue_date(lines[index + 1].text)
                if candidate:
                    issuers.append(candidate)
    unique_issuers = list(dict.fromkeys(issuers))
    if len(unique_issuers) == 1:
        values["isuePlc"] = unique_issuers[0]

    values["addr"] = _extract_address(lines)
    if is_foreigner_card:
        values["addr"] = _extract_labeled_address(lines) or values["addr"]
        values["custNm"] = _extract_foreigner_name(lines)
    if not values["custNm"]:
        values["custNm"] = _extract_korean_name(
            lines, numbered[0][0] if len(numbered) == 1 else None
        )
    return values


def _extract_foreigner_number(lines: tuple[OcrLine, ...]) -> str:
    for index, line in enumerate(lines):
        if not _FOREIGN_REG_LABEL.search(line.text):
            continue
        label = _FOREIGN_REG_LABEL.search(line.text)
        assert label is not None

        # OCR may return "등록번호 960101-5123456" as one text line.
        same_line = "".join(re.findall(r"\d", line.text[label.end():]))
        if len(same_line) >= 13:
            return same_line[:13]

        # Paddle supplies boxes for each recognized text region. Read digit
        # regions immediately to the right of the label on the same row.
        if line.box is not None:
            _, top, right, bottom = line.box
            center_y = (top + bottom) / 2
            height = max(1.0, bottom - top)
            right_regions: list[tuple[float, str]] = []
            for other in lines:
                if other is line or other.box is None:
                    continue
                left, other_top, _, other_bottom = other.box
                other_center_y = (other_top + other_bottom) / 2
                if left < right - height * 0.2:
                    continue
                if abs(other_center_y - center_y) > max(height, other_bottom - other_top) * 0.8:
                    continue
                if not re.fullmatch(r"[\d\s.,()\-/]+", other.text.strip()):
                    continue
                digits = "".join(re.findall(r"\d", other.text))
                if digits:
                    right_regions.append((left, digits))
            collected = "".join(value for _, value in sorted(right_regions))
            if len(collected) >= 13:
                return collected[:13]

        # Some OCR versions merge regions or omit their boxes. In that case,
        # take the closest numeric text rows following the explicit label.
        fragments: list[str] = []
        for other in lines[index + 1:index + 5]:
            if _FIELD_STOP.search(other.text):
                break
            if not re.fullmatch(r"[\d\s.,()\-/]+", other.text.strip()):
                continue
            fragments.append("".join(re.findall(r"\d", other.text)))
            collected = "".join(fragments)
            if len(collected) >= 13:
                return collected[:13]

    # When the printed label is missed, still accept a unique date-valid
    # foreign registration number read elsewhere on the card.
    candidates: set[str] = set()
    for line in lines:
        digits = re.sub(r"\D", "", line.text)
        if len(digits) == 13 and _valid_foreigner_number(digits):
            candidates.add(digits)
        elif len(digits) > 13:
            for offset in range(len(digits) - 12):
                candidate = digits[offset:offset + 13]
                if _valid_foreigner_number(candidate):
                    candidates.add(candidate)
    if len(candidates) == 1:
        return next(iter(candidates))
    return ""


def _valid_foreigner_number(value: str) -> bool:
    if len(value) != 13 or value[6] not in "5678":
        return False
    year, month, day = int(value[:2]), int(value[2:4]), int(value[4:6])
    try:
        date((1900 if value[6] in "56" else 2000) + year, month, day)
    except ValueError:
        return False
    return True


def _extract_foreigner_name(lines: tuple[OcrLine, ...]) -> str:
    # Use the registration-number row as a stable layout anchor before
    # interpreting labels. Some cards print "Name" below the holder's name,
    # and Korean OCR often reads 성명 as 생명.
    registration_boxes = [
        line.box for line in lines
        if _FOREIGN_REG_LABEL.search(line.text) and line.box is not None
    ]
    if not registration_boxes:
        # Some cards make PaddleOCR drop the first Korean syllable of
        # "등록번호" or merge a sex marker into the number row. Use the
        # registration-shaped digit sequence as a geometry anchor.
        registration_boxes = [
            line.box for line in lines
            if line.box is not None
            and _contains_foreigner_number(line.text)
        ]
    if registration_boxes:
        anchor_bottom = max(box[3] for box in registration_boxes)
        lower_limit = anchor_bottom + 145.0
        candidates = [
            (line.box[1], line, _latin_name_parts(line.text))
            for line in lines
            if line.box is not None
            and anchor_bottom + 5 < line.box[1] < lower_limit
            and not _FIELD_STOP.search(line.text)
            and not _FOREIGN_NAME_LABEL.search(line.text)
        ]
        candidates = [(top, box, parts) for top, box, parts in candidates if parts]
        if candidates:
            candidates.sort(key=lambda item: (item[0], -len(item[2])))
            first_name_row = candidates[0][0]
            rows: list[list[tuple[float, OcrLine, list[str]]]] = []
            for top, line, parts in candidates:
                if top > first_name_row + 55:
                    continue
                same_row = next(
                    (row for row in rows if abs(row[0][0] - top) <= 14), None
                )
                if same_row is None:
                    rows.append([(top, line, parts)])
                else:
                    same_row.append((top, line, parts))
            selected_rows = []
            for row in rows:
                # Prefer the OCR version that actually preserves word gaps;
                # within a tie, use confidence and the fuller transcription.
                top, line, parts = max(
                    row,
                    key=lambda item: (
                        len(item[2]),
                        sum(char.isspace() for char in item[1].text),
                        2 if item[1].source == "english_focus" else
                        1 if item[1].source == "english" else 0,
                        item[1].confidence or 0.0,
                        sum(map(len, item[2])),
                    ),
                )
                selected_rows.append((top, parts))
            name = " ".join(
                part for _, parts in sorted(selected_rows, key=lambda row: row[0])
                for part in parts
            ).strip()
            if name:
                if any("VIETNAM" in line.text.upper() for line in lines):
                    name = re.sub(r"\bLETHI\b", "LE THI", name, flags=re.IGNORECASE)
                return name.replace(" -", "-").rstrip("-").strip()

    # Focused OCR is run only on the name band, so it can safely provide a
    # name even when the full-card pass missed the registration-number anchor.
    focused_parts = [
        part
        for line in lines
        if line.source == "english_focus"
        and not _FIELD_STOP.search(line.text)
        for part in _latin_name_parts(line.text)
    ]
    if focused_parts:
        name = " ".join(focused_parts).strip()
        if any("VIETNAM" in line.text.upper() for line in lines):
            name = re.sub(r"\bLETHI\b", "LE THI", name, flags=re.IGNORECASE)
        return name.replace(" -", "-").rstrip("-").strip()

    # Label-based fallback supports cards where OCR returned no useful boxes.
    for index, line in enumerate(lines):
        label = _FOREIGN_NAME_LABEL.search(line.text)
        if not label:
            continue
        parts = _latin_name_parts(line.text[label.end():])
        for following in lines[index + 1:index + 6]:
            if _FIELD_STOP.search(following.text):
                break
            parts.extend(_latin_name_parts(following.text))
        if len(parts) >= 2:
            return " ".join(parts).replace(" -", "-").rstrip("-").strip()

    # Without coordinates, use the first English-only lines after the
    # registration-number label. Never fall back to unrelated Korean OCR text
    # as the foreign card holder's name.
    for index, line in enumerate(lines):
        if not _FOREIGN_REG_LABEL.search(line.text):
            continue
        parts: list[str] = []
        for following in lines[index + 1:index + 5]:
            if _FIELD_STOP.search(following.text):
                break
            parts.extend(_latin_name_parts(following.text))
        if parts:
            return " ".join(parts).strip()
    return ""


def _contains_foreigner_number(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    for offset in range(max(1, len(digits) - 12)):
        candidate = digits[offset:offset + 13]
        if len(candidate) == 13 and _valid_foreigner_number(candidate):
            return True
    return False


def _latin_name_parts(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text)
    parts: list[str] = []
    for raw in words:
        word = raw.strip("'-")
        is_initial = len(word) == 1 and raw.endswith("-")
        if (len(word) > 1 or is_initial) and word.upper() not in _FOREIGN_NAME_STOPWORDS:
            if parts and raw.endswith("-") and len(word) > 4:
                # Several cards print a middle initial as "J-" directly
                # after the preceding name; OCR often joins it into MADURAJ-.
                parts.extend((word[:-1], word[-1]))
            else:
                parts.append(word)
    return parts


def _extract_labeled_address(lines: tuple[OcrLine, ...]) -> str:
    for index, line in enumerate(lines):
        label = _ADDRESS_LABEL.search(line.text)
        if not label:
            continue
        parts = [_clean_address_line(line.text[label.end():])]
        for following in lines[index + 1:index + 5]:
            text = following.text.strip()
            if not text or _DATE.search(text) or _FIELD_STOP.search(text) or _ISSUER.search(_strip_issue_date(text)):
                break
            parts.append(_clean_address_line(text))
        candidate = " ".join(part for part in parts if part).strip(" :：")
        if candidate and re.search(r"\d|[\uac00-\ud7a3]", candidate):
            return candidate
    return ""


def _dates_in_text(text: str) -> set[str]:
    values: set[str] = set()
    text = text.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}))
    matches = list(_DATE.finditer(text))
    matches.extend(re.finditer(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", text))
    for match in matches:
        year, month, day = map(int, match.groups())
        try:
            values.add(date(year, month, day).strftime("%Y%m%d"))
        except ValueError:
            pass
    return values


def _strip_issue_date(text: str) -> str:
    return _DATE.sub("", text, count=1).strip(" .,:;-/")


def _extract_korean_name(lines: tuple[OcrLine, ...], number_line: OcrLine | None) -> str:
    # The name can be joined with its label or with the resident number by the
    # detector. Read explicit labels first, before generic field-stop checks.
    labeled = {
        match.group(1)
        for line in lines
        for match in _KOREAN_NAME_LABEL.finditer(line.text)
    }
    if len(labeled) == 1:
        return next(iter(labeled))

    candidates: list[tuple[float, str]] = []
    number_y = number_line.box[1] if number_line and number_line.box else None
    for line in lines:
        text = line.text.strip()
        is_number_row = bool(_RRN.search(text))
        # A field label on a separate row is not a name candidate. Permit the
        # number row through so a name merged into it is not silently lost.
        if (not is_number_row and _FIELD_STOP.search(text)) or (
            _ISSUER.search(_strip_issue_date(text))
            or re.search(
                r"\uc8fc\ubbfc\ub4f1\ub85d\uc99d|\uc6b4\uc804\uba74\ud5c8\uc99d|\ubcf5\uc9c0\uce74\ub4dc",
                text,
            )
        ):
            continue
        words = _KOREAN_NAME.findall(text)
        if len(words) == 1 and is_number_row:
            candidates.append((line.confidence or 0.0, words[0]))
        elif len(words) == 1 and number_y is not None and line.box and line.box[1] < number_y:
            candidates.append((line.box[1], words[0]))
        elif len(words) == 1 and (number_y is None or line.box is None):
            candidates.append((line.confidence or 0.0, words[0]))
    if not candidates or len({item[1] for item in candidates}) != 1:
        return ""
    return candidates[0][1]


def _extract_address(lines: tuple[OcrLine, ...]) -> str:
    candidates: list[str] = []
    for index, line in enumerate(lines):
        text = _clean_address_line(line.text)
        if _DATE.search(text):
            continue
        has_road_number = bool(re.search(r"(?:\ub85c|\uae38)\s*\d", text))
        has_detail = bool(_ADDRESS_DETAIL.search(text))
        if not (has_road_number or has_detail):
            continue
        parts: list[str] = []
        for previous in reversed(lines[max(0, index - 3):index]):
            prior = _clean_address_line(previous.text)
            if not prior or _DATE.search(prior) or _ISSUER.search(_strip_issue_date(prior)) or _FIELD_STOP.search(prior):
                break
            if _ADDRESS_PREFIX.search(prior):
                parts.insert(0, prior)
            else:
                break
        parts.append(text)
        for following in lines[index + 1:index + 3]:
            detail = _clean_address_line(following.text)
            if (
                detail and not _DATE.search(detail)
                and not _FIELD_STOP.search(detail)
                and not _ISSUER.search(_strip_issue_date(detail))
                and _ADDRESS_DETAIL.search(detail)
            ):
                parts.append(detail)
            else:
                break
        candidates.append(" ".join(parts))
    detailed = [item for item in candidates if _ADDRESS_DETAIL.search(item) or re.search(r"(?:\ub85c|\uae38)\s*\d", item)]
    return max(detailed or candidates, key=len) if candidates else ""


def _clean_address_line(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text)).replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", value).strip()
