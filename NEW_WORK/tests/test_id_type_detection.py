import tempfile
import unittest
from pathlib import Path

from new_work.domain import OcrLine, OcrResult
from new_work.ocr.id_types import detect_id_type
from new_work.ocr.result_writer import write_ocr_result


class IdTypeDetectionTests(unittest.TestCase):
    def test_detects_unambiguous_resident_card(self):
        detected = detect_id_type((OcrLine("대한민국 주민등록증"),))

        self.assertEqual(detected.name, "주민등록증")
        self.assertEqual(detected.type_code, "R")
        self.assertEqual(detected.candidate_codes, ("1001",))
        self.assertEqual(detected.code, "1001")

    def test_prioritizes_resident_issue_confirmation_over_resident_card(self):
        detected = detect_id_type(("주민등록증 발급확인서",))

        self.assertEqual(detected.candidate_codes, ("3001",))
        self.assertEqual(detected.type_code, "K")

    def test_foreign_passport_is_not_classified_as_generic_passport(self):
        detected = detect_id_type(("외국인", " 여 권 "))

        self.assertEqual(detected.candidate_codes, ("2002",))
        self.assertEqual(detected.type_code, "T")

    def test_shared_legacy_names_return_candidates_instead_of_guessing_code(self):
        detected = detect_id_type(("자동차 운전면허증",))

        self.assertEqual(detected.name, "운전면허증")
        self.assertEqual(detected.type_code, "D")
        self.assertEqual(detected.candidate_codes, ("1101", "1102", "1201"))
        self.assertIsNone(detected.code)

    def test_writes_plain_text_result_to_requested_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.txt"
            result = OcrResult(
                job_id="synthetic-job",
                image_path=Path("synthetic.jpg"),
                lines=(OcrLine("주민등록증", 0.95), OcrLine("가나다 123", 0.81)),
                engine_name="synthetic OCR",
                engine_version="test",
            )

            written = write_ocr_result(
                result, detect_id_type(result.lines), output
            )

            self.assertEqual(written, output)
            content = output.read_text(encoding="utf-8")
            self.assertIn("문서 종류: 주민등록증", content)
            self.assertIn("ID_TYPES 코드 후보: 1001", content)
            self.assertIn("01. 주민등록증\tconfidence=0.9500", content)
            self.assertIn("신분증 진위·인증 결과가 아닙니다", content)

    def test_unknown_text_stays_unclassified(self):
        detected = detect_id_type(("synthetic unrelated text",))

        self.assertIsNone(detected.name)
        self.assertEqual(detected.candidate_codes, ())


if __name__ == "__main__":
    unittest.main()
