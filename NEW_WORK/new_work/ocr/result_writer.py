from __future__ import annotations

import sys
from pathlib import Path

from new_work.domain import OcrResult
from new_work.ocr.id_types import IdTypeDetection


def execution_directory() -> Path:
    """Folder beside the source project or frozen executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def write_ocr_result(
    result: OcrResult,
    detection: IdTypeDetection,
    path: Path | None = None,
) -> Path:
    target = Path(path) if path is not None else execution_directory() / "result.txt"
    codes = ", ".join(detection.candidate_codes) or "판별 안 됨"
    if detection.code:
        code_status = f"확정 후보 {detection.code}"
    elif detection.candidate_codes:
        code_status = "세부 코드 수동 선택 필요"
    else:
        code_status = "문서 종류 수동 확인 필요"

    lines = [
        "NEW_WORK OCR 결과",
        f"작업 ID: {result.job_id}",
        f"이미지 파일명: {result.image_path.name}",
        f"OCR 엔진: {result.engine_name} {result.engine_version}".strip(),
        f"문서 종류: {detection.name or '판별 안 됨'}",
        f"문자 유형: {detection.type_code or '판별 안 됨'}",
        f"ID_TYPES 코드 후보: {codes}",
        f"코드 상태: {code_status}",
        "주의: OCR 기반 문서 종류 추정이며 신분증 진위·인증 결과가 아닙니다.",
        "",
        "인식 줄:",
    ]
    for index, line in enumerate(result.lines, start=1):
        score = "" if line.confidence is None else f"\tconfidence={line.confidence:.4f}"
        text = line.text.replace("\r", " ").replace("\n", " ")
        lines.append(f"{index:02d}. {text}{score}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
