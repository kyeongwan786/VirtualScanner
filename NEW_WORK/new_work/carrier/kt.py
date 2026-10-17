from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from new_work.database.reader import KT_PAPER_FIELDS, fetch_latest_kt_data
from new_work.image.legacy_crop import find_latest_id_crop, reencode_jpeg


KT_DB_FIELDS = KT_PAPER_FIELDS
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
    recog_sub_cd: str,
) -> tuple[Path, Path]:
    """Create ORG.jpg/CR.jpg using the recovered com_kt image algorithm."""
    source = Path(source_image).expanduser().resolve(strict=True)
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    original_output = output_dir / "ORG.jpg"
    crop_output = output_dir / "CR.jpg"
    reencode_jpeg(source, original_output, quality=100)

    # The legacy code searches recursively for the newest *m.jpg/m.jpeg/m.png.
    # Isolate the chosen JPG in this job directory so another saved card image
    # cannot accidentally win its mtime comparison.
    data_directory = output_dir / "data"
    data_directory.mkdir(exist_ok=True)
    selected_input = data_directory / f"{source.stem}_m{source.suffix.lower()}"
    shutil.copyfile(source, selected_input)
    cropped_path = find_latest_id_crop(
        data_directory, recog_sub_cd, output_name="CR.jpg"
    )
    if cropped_path is None:
        raise FileNotFoundError("KT 원본 크롭 알고리즘이 대상 JPG를 찾지 못했습니다.")
    # com_kt first creates its crop at q95, then save_data re-encodes CR.jpg at q100.
    reencode_jpeg(cropped_path, crop_output, quality=100)
    return original_output, crop_output
