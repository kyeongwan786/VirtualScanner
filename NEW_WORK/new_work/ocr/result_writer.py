from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

from new_work.domain import OcrResult
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
)
DEFAULT_MSK_POS = "339%2C257%2C491%2C300"


def execution_directory() -> Path:
    """Folder beside the source project or frozen executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def write_ocr_result(
    result: OcrResult,
    detection: IdTypeDetection,
    values: Mapping[str, str] | None = None,
    path: Path | None = None,
) -> Path:
    target = Path(path) if path is not None else execution_directory() / "result.txt"
    provided = dict(result.values if values is None else values)
    selected_code = provided.get("kaitIdType", "").strip()
    if not selected_code and detection.code:
        selected_code = detection.code

    output_values = {
        "kaitIdType": selected_code,
        "recogSubCd": provided.get("recogSubCd", "").strip() or selected_code,
        "idType": provided.get("idType", "").strip() or detection.type_code or "",
        "ctzCorpNum": provided.get("ctzCorpNum", ""),
        "custNm": provided.get("custNm", ""),
        "isueDt": provided.get("isueDt", ""),
        "drvLicenIsueNum": provided.get("drvLicenIsueNum", ""),
        "isuePlc": provided.get("isuePlc", ""),
        "nationClCd": provided.get("nationClCd", "").strip() or "I",
        "addr": provided.get("addr", ""),
        "mskPos": provided.get("mskPos", "").strip() or DEFAULT_MSK_POS,
    }

    output_lines: list[str] = []
    for field in RESULT_FIELD_ORDER:
        value = str(output_values[field]).replace("\r", " ").replace("\n", " ").strip()
        if field == "drvLicenIsueNum" and not value:
            continue
        output_lines.append(f"{field}={value}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return target
