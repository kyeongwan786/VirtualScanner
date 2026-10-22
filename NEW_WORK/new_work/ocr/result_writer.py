from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Mapping
from urllib.parse import quote_plus

from new_work.domain import OcrResult
from new_work.image.face_crop import crop_id_portrait_jpeg
from new_work.ocr.id_types import IdTypeDetection


RESULT_FIELD_ORDER = (
    "kaitIdType",
    "recogSubCd",
    "idType",
    "ctzCorpNum",
    "custNm",
    "isueDt",
    "drvLicenIsueNum",
    "isuePlc",
    "nationClCd",
    "addr",
    "mskPos",
    "base64DATA",
    "idCrop",
)
URL_ENCODED_FIELDS = frozenset(("custNm", *RESULT_FIELD_ORDER[7:]))


def execution_directory() -> Path:
    """Folder beside the source project or frozen executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def default_result_path() -> Path:
    """Use the legacy Windows consumer path; keep source runs portable."""
    if os.name == "nt":
        return Path(r"C:\SKT-UScan\agent\WEB-INF\classes\sk_report.txt")
    return execution_directory() / "sk_report.txt"


def write_ocr_result(
    result: OcrResult,
    detection: IdTypeDetection,
    values: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> Path:
    target = Path(path) if path is not None else default_result_path()
    provided = dict(result.values if values is None else values)
    selected_code = provided.get("kaitIdType", "").strip()
    if not selected_code and detection.code:
        selected_code = detection.code

    output_values = {
        "kaitIdType": selected_code,
        "recogSubCd": provided.get("recogSubCd", "").strip() or selected_code,
        "idType": provided.get("idType", "").strip() or detection.type_code or "",
        "ctzCorpNum": "".join(
            char for char in provided.get("ctzCorpNum", "") if char.isdecimal()
        ),
        "custNm": provided.get("custNm", ""),
        "isueDt": "".join(
            char for char in provided.get("isueDt", "") if char.isdecimal()
        ),
        # The UI displays separators; the legacy report stores only digits.
        "drvLicenIsueNum": "".join(
            char for char in provided.get("drvLicenIsueNum", "") if char.isdecimal()
        ),
        "isuePlc": provided.get("isuePlc", ""),
        "nationClCd": provided.get("nationClCd", "").strip() or "I",
        "addr": provided.get("addr", ""),
        "mskPos": provided.get("mskPos", "").strip(),
    }

    output_lines: list[str] = []
    for field in RESULT_FIELD_ORDER:
        if field == "base64DATA":
            value = base64.b64encode(result.image_path.read_bytes()).decode("ascii")
        elif field == "idCrop":
            cropped = crop_id_portrait_jpeg(result.image_path)
            value = base64.b64encode(cropped).decode("ascii") if cropped else ""
        else:
            value = str(output_values[field]).replace("\r", " ").replace("\n", " ").strip()
        if field == "drvLicenIsueNum" and not value:
            continue
        if field in URL_ENCODED_FIELDS:
            value = quote_plus(value, encoding="utf-8")
        output_lines.append(f"{field}={value}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return target
