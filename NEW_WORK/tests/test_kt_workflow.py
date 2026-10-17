import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from new_work.carrier.kt import (
    KT_DB_FIELDS,
    create_kt_images,
    get_latest_kt_recog_sub_cd,
    kt_store_destinations,
    update_kt_database_copy,
)

KT_REVIEW_FIELDS = (
    "ctzCorpNum", "custNm", "isueDt", "drvLicenIsueNum", "isuePlc", "addr"
)


class KtWorkflowTests(unittest.TestCase):
    def test_update_targets_latest_scan_date_on_a_copy_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            output = root / "job" / "myCustomer.db"
            columns = ", ".join(f"{field} TEXT" for field in KT_DB_FIELDS)
            with sqlite3.connect(source) as connection:
                connection.execute(
                    f"CREATE TABLE T_SCAN_PAPER (SCAN_DT TEXT, {columns}, EXTRA TEXT)"
                )
                names = ", ".join(("SCAN_DT", *KT_DB_FIELDS))
                placeholders = ", ".join("?" for _ in range(len(KT_DB_FIELDS) + 1))
                connection.execute(
                    f"INSERT INTO T_SCAN_PAPER ({names}, EXTRA) VALUES ({placeholders}, ?)",
                    ["20260101000000", *[f"old-{field}" for field in KT_DB_FIELDS], "keep-old"],
                )
                connection.execute(
                    f"INSERT INTO T_SCAN_PAPER ({names}, EXTRA) VALUES ({placeholders}, ?)",
                    ["20260102000000", *[f"latest-{field}" for field in KT_DB_FIELDS], "keep-latest"],
                )
            reviewed = {field: f"reviewed-{field}" for field in KT_REVIEW_FIELDS}
            with sqlite3.connect(source) as connection:
                connection.execute(
                    "UPDATE T_SCAN_PAPER SET RECOG_SUB_CD = '1001' "
                    "WHERE SCAN_DT = '20260102000000'"
                )

            self.assertEqual(get_latest_kt_recog_sub_cd(source), "1001")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

            updated_path, count = update_kt_database_copy(source, output, reviewed)

            self.assertEqual(count, 1)
            self.assertEqual(updated_path, output)
            with sqlite3.connect(source) as connection:
                source_latest = connection.execute(
                    "SELECT CUST_NM FROM T_SCAN_PAPER ORDER BY SCAN_DT DESC LIMIT 1"
                ).fetchone()[0]
            self.assertEqual(source_latest, "latest-CUST_NM")
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)
            with sqlite3.connect(output) as connection:
                rows = connection.execute(
                    "SELECT * FROM T_SCAN_PAPER ORDER BY SCAN_DT DESC"
                ).fetchall()
            self.assertEqual(rows[0][KT_DB_FIELDS.index("CUST_NM") + 1], "reviewed-custNm")
            self.assertEqual(rows[1][KT_DB_FIELDS.index("CUST_NM") + 1], "old-CUST_NM")
            with sqlite3.connect(output) as connection:
                latest = connection.execute(
                    "SELECT RECOG_SUB_CD, ID_TYPE, NATION_CL_CD, MSK_POS "
                    "FROM T_SCAN_PAPER ORDER BY SCAN_DT DESC LIMIT 1"
                ).fetchone()
            self.assertEqual(latest, ("1001", "latest-ID_TYPE",
                                      "latest-NATION_CL_CD", "latest-MSK_POS"))
            self.assertEqual(rows[0][-1], "keep-latest")
            self.assertEqual(rows[1][-1], "keep-old")
            with sqlite3.connect(source) as original, sqlite3.connect(output) as copied:
                original_schema = original.execute(
                    "SELECT type, name, sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                ).fetchall()
                copied_schema = copied.execute(
                    "SELECT type, name, sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                ).fetchall()
            self.assertEqual(copied_schema, original_schema)

    def test_matching_maximum_scan_dates_update_all_legacy_target_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tied.db"
            output = root / "copy.db"
            columns = ", ".join(f"{field} TEXT" for field in KT_DB_FIELDS)
            names = ", ".join(("SCAN_DT", *KT_DB_FIELDS))
            placeholders = ", ".join("?" for _ in range(len(KT_DB_FIELDS) + 1))
            with sqlite3.connect(source) as connection:
                connection.execute(f"CREATE TABLE T_SCAN_PAPER (SCAN_DT TEXT, {columns})")
                for index in range(2):
                    connection.execute(
                        f"INSERT INTO T_SCAN_PAPER ({names}) VALUES ({placeholders})",
                        ["20261017090000", *[f"row-{index}-{field}" for field in KT_DB_FIELDS]],
                    )
            reviewed = {field: f"reviewed-{field}" for field in KT_REVIEW_FIELDS}

            _, updated_rows = update_kt_database_copy(source, output, reviewed)

            self.assertEqual(updated_rows, 2)
            with sqlite3.connect(output) as connection:
                rows = connection.execute(
                    "SELECT CUST_NM FROM T_SCAN_PAPER ORDER BY rowid"
                ).fetchall()
            self.assertEqual(rows, [("reviewed-custNm",), ("reviewed-custNm",)])

    def test_kt_images_use_original_center_crop_and_jpeg_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jpg"
            Image.new("RGB", (500, 600), "white").save(source, format="JPEG")

            original_output, crop_output = create_kt_images(source, root / "job", "1001")

            self.assertEqual(original_output.name, "ORG.jpg")
            self.assertEqual(crop_output.name, "CR.jpg")
            with Image.open(original_output) as image:
                self.assertEqual(image.size, (500, 600))
                self.assertEqual(image.format, "JPEG")
            with Image.open(crop_output) as image:
                self.assertEqual(image.size, (340, 406))
                self.assertEqual(image.format, "JPEG")
            self.assertNotEqual(original_output.read_bytes(), source.read_bytes())
            self.assertNotEqual(crop_output.read_bytes(), source.read_bytes())

    def test_kt_artifacts_map_to_legacy_store_image_directory(self):
        root = Path("/store/share")
        database = Path("/local/job/myCustomer.db")
        original = Path("/local/job/ORG.jpg")
        crop = Path("/local/job/CR.jpg")

        targets = kt_store_destinations(root, database, original, crop)

        self.assertEqual(
            targets,
            [
                (database, root / "IMAGE" / "myCustomer.db"),
                (original, root / "IMAGE" / "ORG.jpg"),
                (crop, root / "IMAGE" / "CR.jpg"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
