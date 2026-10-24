from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


KT_PAPER_FIELDS = (
    "RECOG_SUB_CD",
    "ID_TYPE",
    "CTZ_CORP_NUM",
    "CUST_NM",
    "ISUE_DT",
    "DRV_LICEN_ISUE_NUM",
    "ISUE_PLC",
    "NATION_CL_CD",
    "ADDR",
    "MSK_POS",
    "WLF_NUM",
)


def connect_read_only(database_path: Path) -> sqlite3.Connection:
    """Open a configured reference DB read-only for the existing KT SELECT."""
    resolved = database_path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def fetch_latest_kt_data(database_path: Path) -> dict[str, Any] | None:
    """Read KT OCR fields from the global latest paper row."""
    fields = ", ".join(KT_PAPER_FIELDS)
    with connect_read_only(database_path) as connection:
        row = connection.execute(
            f"SELECT {fields} FROM T_SCAN_PAPER "
            "ORDER BY SCAN_DT DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None
