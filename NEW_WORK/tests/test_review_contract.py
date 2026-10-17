import unittest
from pathlib import Path

from new_work.domain import OcrResult, REVIEW_TEXT_FIELDS, ReviewDraft
from new_work.ocr.adapter import OcrUnavailableError, UnconnectedOcrAdapter


class ReviewContractTests(unittest.TestCase):
    def test_raw_values_are_preserved_when_review_copy_changes(self):
        raw = {field: f"raw-{field}" for field in REVIEW_TEXT_FIELDS}
        result = OcrResult(
            job_id="synthetic-job-1",
            image_path=Path("synthetic.jpg"),
            values=raw,
            engine_name="fixture-engine",
            engine_version="0",
        )
        draft = ReviewDraft.from_result(result)
        edited = dict(draft.reviewed_values)
        edited["custNm"] = "synthetic-correction"

        draft.mark_reviewed(edited)

        self.assertEqual(result.values["custNm"], "raw-custNm")
        self.assertEqual(draft.reviewed_values["custNm"], "synthetic-correction")
        self.assertEqual(draft.changed_fields, ("custNm",))
        self.assertTrue(draft.reviewed)

    def test_missing_engine_is_explicit_and_never_returns_fake_text(self):
        adapter = UnconnectedOcrAdapter()

        self.assertFalse(adapter.available)
        self.assertIn("미연결", adapter.status)
        with self.assertRaises(OcrUnavailableError):
            adapter.recognize(Path("synthetic.jpg"))


if __name__ == "__main__":
    unittest.main()
