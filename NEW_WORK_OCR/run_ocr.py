from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog

from new_work.domain import OcrResult
from new_work.ocr.adapter import PaddleOcrAdapter
from new_work.ocr.field_extractor import extract_identity_fields
from new_work.ocr.id_types import detect_id_type
from new_work.ocr.mask_position import detect_mask_position
from new_work.ocr.result_writer import write_ocr_result


def choose_image() -> Path | None:
    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askopenfilename(
            title="OCR할 신분증 JPG 선택",
            filetypes=(("JPEG 이미지", "*.jpg *.jpeg"), ("모든 파일", "*.*")),
        )
    finally:
        root.destroy()
    return Path(selected) if selected else None


def main() -> int:
    parser = argparse.ArgumentParser(description="JPG 신분증 OCR 후 result.txt 생성")
    parser.add_argument("image", nargs="?", help="OCR할 JPG 경로; 생략하면 파일 선택창")
    parser.add_argument("output", nargs="?", help="결과 경로; 기본값은 프로그램 폴더/result.txt")
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve() if args.image else choose_image()
    if image_path is None:
        print("이미지를 선택하지 않았습니다.")
        return 1
    if not image_path.is_file():
        print(f"이미지를 찾지 못했습니다: {image_path}")
        return 2

    try:
        adapter = PaddleOcrAdapter()
        ocr_result = adapter.recognize(image_path)
        detection = detect_id_type(ocr_result.lines)
        focused_values = {}
        english_ocr_used = False
        if detection.code == "1401":
            english_reader = getattr(adapter, "recognize_english", None)
            if callable(english_reader):
                try:
                    english_lines = english_reader(image_path)
                except Exception:
                    english_lines = ()
                if english_lines:
                    english_ocr_used = True
                    ocr_result = OcrResult(
                        job_id=ocr_result.job_id,
                        image_path=ocr_result.image_path,
                        values=ocr_result.values,
                        lines=tuple((*ocr_result.lines, *english_lines)),
                        engine_name=ocr_result.engine_name,
                        engine_version=ocr_result.engine_version,
                        elapsed_seconds=ocr_result.elapsed_seconds,
                    )
            details_reader = getattr(adapter, "recognize_foreigner_reg_number_details", None)
            if callable(details_reader):
                try:
                    number, mask_position = details_reader(image_path)
                except Exception:
                    number, mask_position = "", ""
                if number:
                    focused_values["ctzCorpNum"] = number
                if mask_position:
                    focused_values["mskPos"] = mask_position
        values = {
            **extract_identity_fields(ocr_result.lines),
            **ocr_result.values,
            **focused_values,
        }
        if detection.code:
            values.update(
                kaitIdType=detection.code,
                recogSubCd=detection.code,
                idType=detection.type_code or "",
            )
        elif detection.type_code:
            values["idType"] = detection.type_code
        values["nationClCd"] = "I"
        mask_position = values.get("mskPos") or detect_mask_position(ocr_result.lines)
        if mask_position:
            values["mskPos"] = mask_position

        result = OcrResult(
            job_id=ocr_result.job_id,
            image_path=ocr_result.image_path,
            values=values,
            lines=ocr_result.lines,
            engine_name=ocr_result.engine_name,
            engine_version=ocr_result.engine_version,
            elapsed_seconds=ocr_result.elapsed_seconds,
        )
        output_path = Path(args.output).expanduser() if args.output else None
        written = write_ocr_result(result, detection, values=values, path=output_path)
    except Exception as exc:
        print(f"OCR 또는 결과 저장 실패: {exc}")
        return 3

    print(f"OCR 완료: {len(ocr_result.lines)}줄 인식")
    print(f"문서 종류: {detection.name or '판별 안 됨'}")
    if detection.code:
        print(f"kaitIdType={detection.code}, idType={detection.type_code or ''}")
    if detection.code == "1401" and not english_ocr_used:
        print("영문 OCR을 사용할 수 없어 기본 한국어 OCR 결과를 표시합니다.")
    if detection.candidate_codes and not detection.code:
        print(f"문서 코드 후보: {', '.join(detection.candidate_codes)} (수동 선택 필요)")
    print(f"결과 파일: {written.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
