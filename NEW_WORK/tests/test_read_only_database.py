import sqlite3
import tempfile
import unittest
from pathlib import Path

from new_work.database.reader import (
    KT_PAPER_FIELDS,
    connect_read_only,
    fetch_latest_kt_data,
)


class ReadOnlyDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "synthetic.db"
        with sqlite3.connect(self.database_path) as connection:
            fields_sql = ", ".join(f"{field} TEXT" for field in KT_PAPER_FIELDS)
            connection.execute(f"CREATE TABLE T_SCAN_PAPER (SCAN_DT TEXT, {fields_sql})")
            columns = ", ".join(("SCAN_DT", *KT_PAPER_FIELDS))
            placeholders = ", ".join("?" for _ in range(len(KT_PAPER_FIELDS) + 1))
            connection.execute(
                f"INSERT INTO T_SCAN_PAPER ({columns}) VALUES ({placeholders})",
                ["20260101000000", *[f"old-{field}" for field in KT_PAPER_FIELDS]],
            )
            latest = [f"new-{field}" for field in KT_PAPER_FIELDS]
            latest[KT_PAPER_FIELDS.index("ISUE_DT")] = None
            latest[KT_PAPER_FIELDS.index("DRV_LICEN_ISUE_NUM")] = ""
            connection.execute(
                f"INSERT INTO T_SCAN_PAPER ({columns}) VALUES ({placeholders})",
                ["20260102000000", *latest],
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_kt_select_returns_exact_latest_ten_fields_preserving_null_and_empty(self):
        row = fetch_latest_kt_data(self.database_path)
        self.assertEqual(len(row), 10)
        self.assertEqual(row["CUST_NM"], "new-CUST_NM")
        self.assertIsNone(row["ISUE_DT"])
        self.assertEqual(row["DRV_LICEN_ISUE_NUM"], "")

    def test_connection_rejects_writes(self):
        with connect_read_only(self.database_path) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM T_SCAN_PAPER")


if __name__ == "__main__":
    unittest.main()
