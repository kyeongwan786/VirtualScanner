from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


REFERENCE_RELATIONS = {
    "subindex_without_index": (
        "SELECT COUNT(*) FROM T_SCAN_SUBINDEX s "
        "LEFT JOIN T_SCAN_INDEX i ON i.INDEX_KEY = s.INDEX_KEY "
        "WHERE i.INDEX_KEY IS NULL"
    ),
    "paper_without_subindex": (
        "SELECT COUNT(*) FROM T_SCAN_PAPER p "
        "LEFT JOIN T_SCAN_SUBINDEX s ON s.SUB_INDEX_KEY = p.SUB_INDEX_KEY "
        "WHERE s.SUB_INDEX_KEY IS NULL"
    ),
    "resource_without_paper": (
        "SELECT COUNT(*) FROM T_SCAN_RESOURCES r "
        "LEFT JOIN T_SCAN_PAPER p ON p.TMP_ELEMENT_ID = r.TMP_ELEMENT_ID "
        "WHERE p.TMP_ELEMENT_ID IS NULL"
    ),
    "paper_without_resource": (
        "SELECT COUNT(*) FROM T_SCAN_PAPER p "
        "LEFT JOIN T_SCAN_RESOURCES r ON r.TMP_ELEMENT_ID = p.TMP_ELEMENT_ID "
        "WHERE r.TMP_ELEMENT_ID IS NULL"
    ),
}


@dataclass(frozen=True)
class ReferenceDatabaseAudit:
    table_count: int
    schema: tuple[tuple[str, str, str | None], ...]
    table_rows: dict[str, int]
    column_counts: dict[str, int]
    orphan_counts: dict[str, int]


def audit_reference_database(database_path: Path) -> ReferenceDatabaseAudit:
    """Read schema/count/key-integrity metadata without returning row values."""
    path = Path(database_path).expanduser().resolve(strict=True)
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0) as connection:
        connection.execute("PRAGMA query_only = ON")
        schema = tuple(connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall())
        tables = [name for kind, name, _sql in schema if kind == "table"]
        table_rows: dict[str, int] = {}
        column_counts: dict[str, int] = {}
        for table in tables:
            # Table names come from sqlite_master, not user input.
            quoted = '"' + table.replace('"', '""') + '"'
            table_rows[table] = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            column_counts[table] = len(connection.execute(f"PRAGMA table_info({quoted})").fetchall())
        orphan_counts = {
            label: int(connection.execute(query).fetchone()[0])
            for label, query in REFERENCE_RELATIONS.items()
        }
    return ReferenceDatabaseAudit(
        table_count=len(tables),
        schema=schema,
        table_rows=table_rows,
        column_counts=column_counts,
        orphan_counts=orphan_counts,
    )


def compare_reference_databases(
    source_database: Path, result_database: Path
) -> tuple[ReferenceDatabaseAudit, ReferenceDatabaseAudit, bool]:
    source = audit_reference_database(source_database)
    result = audit_reference_database(result_database)
    return source, result, source.schema == result.schema
