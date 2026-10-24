from __future__ import annotations

import os
import secrets
import sqlite3
import string
import sys
from datetime import datetime
from pathlib import Path
from typing import Mapping

from new_work.image.face_crop import crop_id_portrait_jpeg
from new_work.database.reader import KT_PAPER_FIELDS, fetch_latest_kt_data
from new_work.domain import OcrResult
from new_work.image.legacy_crop import reencode_jpeg
from new_work.ocr.id_types import IdTypeDetection


KT_DB_FIELDS = KT_PAPER_FIELDS
KT_OCR_METADATA_FIELDS = ("FORM_CODE", "APPLF_SEQ", "SDOC_CD", "DOC_TYP_CD")
KT_REVIEW_TO_DB = {
    "CTZ_CORP_NUM": "ctzCorpNum",
    "CUST_NM": "custNm",
    "ISUE_DT": "isueDt",
    "DRV_LICEN_ISUE_NUM": "drvLicenIsueNum",
    "ISUE_PLC": "isuePlc",
    "ADDR": "addr",
}


def kt_store_destinations(
    store_root: Path,
    database: Path,
    original_image: Path,
    crop_image: Path,
) -> list[tuple[Path, Path]]:
    """Map com_kt result artifacts to the store's legacy IMAGE folder."""
    image_directory = Path(store_root).expanduser() / "IMAGE"
    return [
        (Path(database), image_directory / "myCustomer.db"),
        (Path(original_image), image_directory / "ORG.jpg"),
        (Path(crop_image), image_directory / "CR.jpg"),
    ]


def get_latest_kt_recog_sub_cd(source_database: Path) -> str:
    """Return com_kt.fetch_data's RECOG_SUB_CD from the latest paper row."""
    row = fetch_latest_kt_data(source_database)
    if row is None:
        raise ValueError("KT 원본 DB에서 T_SCAN_PAPER 행을 찾지 못했습니다.")
    value = row["RECOG_SUB_CD"]
    return "" if value is None else str(value)


def update_kt_database_copy(
    source_database: Path,
    output_database: Path,
    reviewed_values: dict[str, str],
) -> tuple[Path, int]:
    """Apply the recovered com_kt latest-SCAN_DT UPDATE to a disposable copy.

    The supplied source database is only read/copied. The UPDATE predicate and
    ten fields match the recovered source strings. If SCAN_DT is tied, SQLite
    updates all rows sharing the maximum timestamp, matching the original SQL.
    """
    source = Path(source_database).expanduser().resolve(strict=True)
    output = Path(output_database).expanduser()
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.resolve() == source:
        raise ValueError("KT UPDATE 대상은 원본 DB와 다른 복사본이어야 합니다.")
    missing = [field for field in KT_REVIEW_TO_DB.values() if field not in reviewed_values]
    if missing:
        raise ValueError(f"KT 검토 필드가 없습니다: {', '.join(missing)}")
    invalid = [
        field for field in KT_REVIEW_TO_DB.values()
        if not isinstance(reviewed_values[field], str)
    ]
    if invalid:
        raise ValueError(f"KT 검토 필드는 문자열이어야 합니다: {', '.join(invalid)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    assignments = ", ".join(f"{field} = ?" for field in KT_DB_FIELDS)
    sql = (
        f"UPDATE T_SCAN_PAPER SET {assignments} "
        "WHERE SCAN_DT = (SELECT MAX(SCAN_DT) FROM T_SCAN_PAPER)"
    )
    try:
        source_uri = f"{source.as_uri()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True, timeout=5.0) as original, sqlite3.connect(
            output, timeout=5.0
        ) as connection:
            original.backup(connection)
            selected = connection.execute(
                f"SELECT {', '.join(KT_DB_FIELDS)} FROM T_SCAN_PAPER "
                "ORDER BY SCAN_DT DESC LIMIT 1"
            ).fetchone()
            if selected is None:
                raise ValueError("KT 복사 DB에서 T_SCAN_PAPER 행을 찾지 못했습니다.")
            values_by_field = dict(zip(KT_DB_FIELDS, selected))
            for field, review_key in KT_REVIEW_TO_DB.items():
                values_by_field[field] = reviewed_values[review_key].strip()
            values = [values_by_field[field] for field in KT_DB_FIELDS]
            cursor = connection.execute(sql, values)
            count = cursor.rowcount
            if count < 1:
                raise ValueError("KT 복사 DB에서 갱신 대상 T_SCAN_PAPER 행을 찾지 못했습니다.")
            connection.commit()
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return output, count


def create_kt_images(
    source_image: Path,
    output_directory: Path,
    _recog_sub_cd: str,
) -> tuple[Path, Path]:
    """Overwrite ORG.jpg and CR.jpg with the source card and its portrait crop."""
    source = Path(source_image).expanduser().resolve(strict=True)
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    original_output = output_dir / "ORG.jpg"
    crop_output = output_dir / "CR.jpg"

    crop_bytes = crop_id_portrait_jpeg(
        source,
        recog_sub_cd=_recog_sub_cd,
        require_face=True,
    )
    if crop_bytes is None:
        raise ValueError("얼굴 사진 영역을 찾지 못해 KT 저장을 중단했습니다.")

    original_staged = output_dir / ".ORG.jpg.tmp"
    crop_staged = output_dir / ".CR.jpg.tmp"
    try:
        reencode_jpeg(source, original_staged, quality=100)
        crop_staged.write_bytes(crop_bytes)
        os.replace(original_staged, original_output)
        os.replace(crop_staged, crop_output)
    finally:
        original_staged.unlink(missing_ok=True)
        crop_staged.unlink(missing_ok=True)
    return original_output, crop_output


def default_kt_image_directory() -> Path:
    """Use the fixed Windows C:\\IMAGE directory; keep source runs in-project."""
    if os.name == "nt":
        return Path(r"C:\IMAGE")
    return Path(__file__).resolve().parents[2] / "IMAGE"


KT_ID_TYPE_CODES = {
    "1001": "R",
    "1101": "D",
    "1102": "D",
    "1201": "D",
    "1301": "S",
    "1303": "S",
    "1302": "N",
    "1401": "F",
    "2001": "P",
    "2002": "T",
    "3001": "K",
}


def _legacy_unique(salt: str) -> str:
    """Create a U.Scan-style key: local timestamp + 2 base36 chars + salt."""
    alphabet = string.digits + string.ascii_uppercase
    suffix = "".join(secrets.choice(alphabet) for _ in range(2))
    return f"{datetime.now().strftime('%Y%m%d%H%M%S')}{suffix}{salt}"


def _new_element_id(connection: sqlite3.Connection, send_org_id: str) -> str:
    """Generate a collision-free legacy TMP_ELEMENT_ID for an existing row."""
    salt = send_org_id[:6]
    if not salt:
        raise ValueError("기존 문서의 SEND_ORG_ID가 비어 있어 TMP_ELEMENT_ID를 만들 수 없습니다.")
    for _ in range(100):
        candidate = _legacy_unique(salt)
        exists = connection.execute(
            "SELECT 1 FROM T_SCAN_PAPER WHERE TMP_ELEMENT_ID = ? "
            "UNION ALL SELECT 1 FROM T_SCAN_RESOURCES WHERE TMP_ELEMENT_ID = ? "
            "UNION ALL SELECT 1 FROM T_SCAN_TEMP WHERE TMP_ELEMENT_ID = ? "
            "UNION ALL SELECT 1 FROM T_SCAN_UNKNOWN WHERE TMP_ELEMENT_ID = ? "
            "LIMIT 1",
            (candidate, candidate, candidate, candidate),
        ).fetchone()
        if exists is None:
            return candidate
    raise RuntimeError("중복되지 않는 TMP_ELEMENT_ID를 생성하지 못했습니다.")


def default_kt_database_path() -> Path:
    """Use the existing Windows DB in C:\\IMAGE or the project DB in development."""
    if os.name == "nt":
        return Path(r"C:\IMAGE\myCustomer.db")
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "myCustomer.db"
    return Path(__file__).resolve().parents[2] / "myCustomer.db"


def save_kt_submission(
    result: OcrResult,
    detection: IdTypeDetection,
    values: Mapping[str, str] | None = None,
    *,
    database_path: Path | None = None,
) -> tuple[Path, str]:
    """Update the latest existing U.Scan paper row with reviewed OCR values."""
    target = Path(database_path) if database_path is not None else default_kt_database_path()
    target = target.expanduser().resolve(strict=True)
    provided = dict(result.values if values is None else values)

    def clean(value: object) -> str:
        return str(value or "").strip()

    def digits(value: object) -> str:
        return "".join(char for char in clean(value) if char.isdecimal())

    with sqlite3.connect(target, timeout=5.0) as connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("BEGIN IMMEDIATE")
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'T_SCAN_PAPER'"
        ).fetchone()
        if exists is None:
            raise ValueError("test.db에 T_SCAN_PAPER 테이블이 없습니다.")

        fields = ", ".join(
            ("TMP_ELEMENT_ID", "SEND_ORG_ID", *KT_PAPER_FIELDS, *KT_OCR_METADATA_FIELDS)
        )
        existing = connection.execute(
            f"SELECT {fields} FROM T_SCAN_PAPER "
            "ORDER BY SCAN_DT DESC LIMIT 1"
        ).fetchone()
        if existing is None:
            raise ValueError("업데이트할 기존 T_SCAN_PAPER 문서가 없습니다.")

        old_element_id = clean(existing[0])
        send_org_id = clean(existing[1])
        if not old_element_id:
            raise ValueError("최신 T_SCAN_PAPER 행의 TMP_ELEMENT_ID가 비어 있습니다.")
        reviewed = dict(zip((*KT_PAPER_FIELDS, *KT_OCR_METADATA_FIELDS), existing[2:]))

        # Keep existing values for fields absent from this OCR/UI payload.
        # This mirrors com_kt's read-modify-update flow without creating rows.
        id_code = clean(provided.get("kaitIdType")) or clean(detection.code)
        if not id_code:
            raise ValueError("신분증 종류를 선택하거나 확인한 뒤 저장하세요.")
        if id_code not in KT_ID_TYPE_CODES:
            raise ValueError(f"지원하지 않는 신분증 코드입니다: {id_code}")

        type_metadata = connection.execute(
            "SELECT FORM_CD, APPLF_SEQ, SDOC_CD, DOC_TYP_CD FROM T_CODE_OCR "
            "WHERE OCR_CD = ? AND COALESCE(DEL_YN, 'N') = 'N'",
            (id_code,),
        ).fetchone()
        if type_metadata is None:
            raise ValueError(f"T_CODE_OCR에 사용 가능한 신분증 코드가 없습니다: {id_code}")

        reviewed["RECOG_SUB_CD"] = id_code
        reviewed["ID_TYPE"] = KT_ID_TYPE_CODES[id_code]
        for field, value in zip(KT_OCR_METADATA_FIELDS, type_metadata):
            reviewed[field] = value

        if "ctzCorpNum" in provided:
            number = clean(provided["ctzCorpNum"])
            if id_code not in {"2001", "2002"}:
                number = digits(number)
            reviewed["CTZ_CORP_NUM"] = number
        if "custNm" in provided:
            reviewed["CUST_NM"] = clean(provided["custNm"])
        if "isueDt" in provided:
            reviewed["ISUE_DT"] = digits(provided["isueDt"])
        if "drvLicenIsueNum" in provided:
            reviewed["DRV_LICEN_ISUE_NUM"] = digits(provided["drvLicenIsueNum"])
        if "isuePlc" in provided:
            reviewed["ISUE_PLC"] = clean(provided["isuePlc"])
        if "addr" in provided:
            reviewed["ADDR"] = clean(provided["addr"])
        if "mskPos" in provided:
            reviewed["MSK_POS"] = clean(provided["mskPos"])
        reviewed["NATION_CL_CD"] = "F" if id_code == "1401" else "I"

        # TMP_ELEMENT_ID is the primary key of both PAPER and RESOURCES. Move
        # both sides together so the existing image remains attached to the
        # same document after assigning its new legacy key.
        resource = connection.execute(
            "SELECT 1 FROM T_SCAN_RESOURCES WHERE TMP_ELEMENT_ID = ?",
            (old_element_id,),
        ).fetchone()
        if resource is None:
            raise ValueError("기존 문서에 연결된 T_SCAN_RESOURCES 이미지 행이 없습니다.")

        create_kt_images(
            result.image_path,
            default_kt_image_directory(),
            id_code,
        )
        new_element_id = _new_element_id(connection, send_org_id)

        resource_cursor = connection.execute(
            "UPDATE T_SCAN_RESOURCES SET TMP_ELEMENT_ID = ? WHERE TMP_ELEMENT_ID = ?",
            (new_element_id, old_element_id),
        )
        if resource_cursor.rowcount != 1:
            raise ValueError("기존 T_SCAN_RESOURCES의 TMP_ELEMENT_ID 갱신에 실패했습니다.")

        update_fields = (*KT_PAPER_FIELDS, *KT_OCR_METADATA_FIELDS)
        assignments = ", ".join(f"{field} = ?" for field in update_fields)
        cursor = connection.execute(
            f"UPDATE T_SCAN_PAPER SET TMP_ELEMENT_ID = ?, {assignments} "
            "WHERE TMP_ELEMENT_ID = ?",
            (new_element_id,)
            + tuple(reviewed[field] for field in update_fields)
            + (old_element_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError("기존 T_SCAN_PAPER OCR 결과 업데이트에 실패했습니다.")
        connection.commit()
    return target, new_element_id
