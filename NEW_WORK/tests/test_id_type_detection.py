import base64
from io import BytesIO
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from new_work.domain import OcrLine, OcrResult
from new_work.ocr.id_types import detect_id_type
from new_work.ocr.field_extractor import extract_identity_fields
from new_work.ocr.mask_position import detect_mask_position
from new_work.ocr.result_writer import write_ocr_result


def synthetic_jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (80, 60), "white").save(output, format="JPEG")
    return output.getvalue()


class IdTypeDetectionTests(unittest.TestCase):
    def test_extracts_clear_identity_fields_from_ocr_lines(self):
        values = extract_identity_fields(
            (
                OcrLine("주민등록증"),
                OcrLine("홍길동"),
                OcrLine("900101-1234567", box=(20, 100, 290, 140)),
                OcrLine("서울특별시 종로구 세종대로 1"),
                OcrLine("101동 202호"),
                OcrLine("2020. 3. 26."),
                OcrLine("서울특별시 종로구청장"),
            )
        )

        self.assertEqual(values["ctzCorpNum"], "9001011234567")
        self.assertEqual(values["custNm"], "홍길동")
        self.assertEqual(values["addr"], "서울특별시 종로구 세종대로 1 101동 202호")
        self.assertEqual(values["isueDt"], "20200326")
        self.assertEqual(values["isuePlc"], "서울특별시 종로구청장")

    def test_extracts_license_number_when_unambiguous(self):
        values = extract_identity_fields((OcrLine("11-22-123456-78"),))

        self.assertEqual(values["drvLicenIsueNum"], "11-22-123456-78")

    def test_joins_issue_date_split_across_adjacent_ocr_lines(self):
        values = extract_identity_fields((OcrLine("2020.3"), OcrLine("26")))

        self.assertEqual(values["isueDt"], "20200326")

    def test_pads_single_digit_month_and_day_to_yyyymmdd(self):
        values = extract_identity_fields((OcrLine("2024. 5. 1."),))

        self.assertEqual(values["isueDt"], "20240501")

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

    def test_writes_key_value_result_and_omits_empty_driver_license_number(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.txt"
            image = Path(directory) / "identity.jpg"
            image_bytes = synthetic_jpeg()
            image.write_bytes(image_bytes)
            result = OcrResult(
                job_id="synthetic-job",
                image_path=image,
                lines=(OcrLine("주민등록증", 0.95), OcrLine("가나다 123", 0.81)),
                engine_name="synthetic OCR",
                engine_version="test",
            )

            written = write_ocr_result(
                result,
                detect_id_type(result.lines),
                values={
                    "ctzCorpNum": "900101-1234567",
                    "custNm": "홍길동",
                    "isueDt": "2020. 3. 26.",
                    "isuePlc": "대구광역시 서구청장",
                    "addr": "대구광역시 서구",
                },
                path=output,
            )

            self.assertEqual(written, output)
            content = output.read_text(encoding="utf-8")
            self.assertEqual(
                content.splitlines(),
                [
                    "kaitIdType=1001",
                    "recogSubCd=1001",
                    "idType=R",
                    "ctzCorpNum=900101-1234567",
                    "custNm=홍길동",
                    "isueDt=2020. 3. 26.",
                    "isuePlc=대구광역시 서구청장",
                    "nationClCd=I",
                    "addr=대구광역시 서구",
                    "mskPos=",
                    f"base64DATA={base64.b64encode(image_bytes).decode('ascii')}",
                    "idCrop=",
                ],
            )

    def test_manual_type_code_overrides_detected_code_and_keeps_driver_number(self):
        detected = detect_id_type(("주민등록증",))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.txt"
            image = Path(directory) / "identity.jpg"
            image.write_bytes(synthetic_jpeg())
            write_ocr_result(
                OcrResult(
                    job_id="synthetic-job",
                    image_path=image,
                    lines=(OcrLine("주민등록증"),),
                    engine_name="synthetic OCR",
                    engine_version="test",
                ),
                detected,
                values={
                    "kaitIdType": "1102",
                    "recogSubCd": "1102",
                    "idType": "D",
                    "drvLicenIsueNum": "11-22-333333-44",
                    "nationClCd": "I",
                    "mskPos": "",
                },
                path=output,
            )

            content = output.read_text(encoding="utf-8")

        self.assertIn("kaitIdType=1102", content)
        self.assertIn("recogSubCd=1102", content)
        self.assertIn("drvLicenIsueNum=11-22-333333-44", content)
        self.assertTrue(content.endswith("idCrop=\n"))

    def test_mask_position_is_derived_from_resident_number_box_and_url_encoded(self):
        detected = detect_mask_position(
            (OcrLine("900101-1234567", box=(100, 200, 370, 240)),)
        )

        self.assertEqual(detected, "250%2C200%2C370%2C240")

    def test_mask_position_is_unset_when_number_or_location_is_ambiguous(self):
        self.assertIsNone(
            detect_mask_position((OcrLine("900101-1234567"),))
        )
        self.assertIsNone(
            detect_mask_position(
                (
                    OcrLine("900101-1234567", box=(0, 0, 270, 40)),
                    OcrLine("900101-1234567", box=(0, 50, 270, 90)),
                )
            )
        )

    def test_unknown_text_stays_unclassified(self):
        detected = detect_id_type(("synthetic unrelated text",))

        self.assertIsNone(detected.name)
        self.assertEqual(detected.candidate_codes, ())


if __name__ == "__main__":
    unittest.main()
