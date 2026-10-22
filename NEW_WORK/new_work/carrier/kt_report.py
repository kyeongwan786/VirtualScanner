from __future__ import annotations

import base64
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Mapping
from urllib.parse import quote_plus

from new_work.domain import OcrResult
from new_work.image.face_crop import crop_id_portrait_jpeg
from new_work.ocr.id_types import IdTypeDetection


KT_ID_TYPES: dict[str, tuple[str, str, str]] = {
    "1001": ("주민등록증", "REGID", "주민등록증"),
    "1101": ("운전면허증", "DRIVE", "운전면허증"),
    "1102": ("운전면허증", "DRIVE", "운전면허증"),
    "1201": ("운전면허증", "DRIVE", "운전면허증"),
    "1301": ("복지카드", "HANDI", "복지카드"),
    "1303": ("복지카드", "HANDI", "복지카드"),
    "1302": ("유공자증", "MERIT", "유공자증"),
    "1401": ("외국인 등록증", "FORGN", "외국인 등록증"),
    "2001": ("여권", "NAMEC", "여권"),
    "2002": ("여권", "NAMEC", "여권"),
    "3001": ("주민등록증 발급신청서", "REGID", "주민등록증 발급신청서"),
    **{
        code: ("종류별 상이", "OTHER", "종류별 상이")
        for code in ("1501", "1502", "1601", "1701", "1702", "1704", "1801", "1901")
    },
}
KT_ID_CARD_TYPES = {
    "1001": "1001",
    "1101": "1102",
    "1102": "1102",
    "1201": "1102",
    "1301": "1301",
    "1303": "1301",
    "1302": "1302",
    "1401": "1401",
    "2001": "2001",
    "2002": "2001",
}


def default_kt_report_path() -> Path:
    """Use report.txt beside the existing SK report location."""
    if os.name == "nt":
        return Path(r"C:\SKT-UScan\agent\WEB-INF\classes\report.txt")
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "report.txt"
    return Path(__file__).resolve().parents[2] / "report.txt"


def default_kt_image_directory() -> Path:
    """Use the requested Windows C:\\IMAGE folder and a local source folder elsewhere."""
    if os.name == "nt":
        return Path(r"C:\IMAGE")
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "IMAGE"
    return Path(__file__).resolve().parents[2] / "IMAGE"


def kt_image_output_paths(directory: Path | None = None) -> tuple[Path, Path]:
    image_directory = Path(directory) if directory is not None else default_kt_image_directory()
    return image_directory / "ORG.jpg", image_directory / "CR.jpg"


def write_kt_images(
    source_image: Path,
    directory: Path | None = None,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    """Save the selected original JPG and the report's face crop as ORG/CR."""
    source = Path(source_image).resolve(strict=True)
    original_path, crop_path = kt_image_output_paths(directory)
    if not overwrite:
        for output in (original_path, crop_path):
            if output.exists() and output.resolve() != source:
                raise FileExistsError(f"이미 파일이 있습니다: {output}")

    portrait = crop_id_portrait_jpeg(source)
    if portrait is None:
        raise ValueError("얼굴 사진 영역을 찾지 못해 CR.jpg를 만들지 못했습니다.")
    original_bytes = source.read_bytes()
    original_path.parent.mkdir(parents=True, exist_ok=True)
    if original_path.resolve() != source:
        original_path.write_bytes(original_bytes)
    crop_path.write_bytes(portrait)
    return original_path, crop_path


def render_kt_report(
    result: OcrResult,
    detection: IdTypeDetection,
    values: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> str:
    provided = dict(result.values if values is None else values)
    code = provided.get("kaitIdType", "").strip() or (detection.code or "")
    id_card_type_name, id_type, id_type_name = KT_ID_TYPES.get(
        code, ("", "", "")
    )
    id_card_type = KT_ID_CARD_TYPES.get(code, "")

    def digits(value: str) -> str:
        return "".join(char for char in value if char.isdecimal())

    passport_number = ""
    if code in {"1101", "1102", "1201"}:
        passport_number = digits(provided.get("drvLicenIsueNum", ""))
    elif code in {"2001", "2002"}:
        passport_number = provided.get("ctzCorpNum", "").strip()

    portrait = crop_id_portrait_jpeg(result.image_path)
    portrait_base64 = base64.b64encode(portrait).decode("ascii") if portrait else ""
    timestamp = (now or datetime.now()).strftime("%Y%m%d%H%M%S")
    fields = (
        ("address", provided.get("addr", "")),
        ("agencyIssued", provided.get("isuePlc", "")),
        ("base64corbImg", portrait_base64),
        ("blackMarkPos", provided.get("mskPos", "").strip()),
        ("cdNm", "신분증k"),
        ("dateIssued", digits(provided.get("isueDt", ""))),
        ("documentType", "A0002"),
        ("idCardTypeName", id_card_type_name),
        ("idCardType", id_card_type),
        ("idType", id_type),
        ("idTypeNm", id_type_name),
        ("juminNum", digits(provided.get("ctzCorpNum", ""))),
        ("passportNum", passport_number),
        ("elementId", timestamp),
        ("name", provided.get("custNm", "")),
    )
    lines = []
    for key, value in fields:
        normalized = str(value).replace("\r", " ").replace("\n", " ").strip()
        lines.append(f"{key}={quote_plus(normalized, encoding='utf-8')}")
    return "\n".join(lines) + "\n"


def write_kt_report(
    result: OcrResult,
    detection: IdTypeDetection,
    values: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> Path:
    target = Path(path) if path is not None else default_kt_report_path()
    report = render_kt_report(result, detection, values)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8", newline="")
    return target
