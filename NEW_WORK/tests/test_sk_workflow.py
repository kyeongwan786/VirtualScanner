import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from new_work.carrier.sk import create_sk_report
from new_work.domain import OcrResult, REVIEW_TEXT_FIELDS, ReviewDraft
from new_work.carrier.sk_report import SK_REPORT_KEYS


class SkWorkflowTests(unittest.TestCase):
    def test_reviewed_values_and_selected_jpegs_create_report_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full.jpg"
            Image.new("RGB", (40, 30), "white").save(full, format="JPEG")
            values = {field: f"synthetic-{field}" for field in REVIEW_TEXT_FIELDS}
            values["kaitIdType"] = "1001"
            result = OcrResult(job_id="synthetic-job", image_path=full)
            draft = ReviewDraft.from_result(result)
            draft.mark_reviewed(values)

            output = create_sk_report(draft, root / "out" / "sk_report.txt")

            rows = output.read_text(encoding="utf-8").splitlines()
            self.assertEqual(tuple(row.split("=", 1)[0] for row in rows), SK_REPORT_KEYS)
            self.assertEqual(output.name, "sk_report.txt")
            self.assertTrue(rows[-1].startswith("idCrop="))

    def test_sk_report_works_without_a_database_or_temp_select(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = root / "full.jpg"
            Image.new("RGB", (500, 600), "white").save(full, format="JPEG")
            values = {field: "" for field in REVIEW_TEXT_FIELDS}
            values.update(kaitIdType="1001", recogSubCd="1001", idType="R")
            draft = ReviewDraft.from_result(OcrResult(job_id="no-db", image_path=full))
            draft.mark_reviewed(values)

            with patch("sqlite3.connect", side_effect=AssertionError("SK must not access SQLite")):
                result = create_sk_report(draft, root / "sk_report.txt")

            self.assertTrue(result.is_file())


if __name__ == "__main__":
    unittest.main()
