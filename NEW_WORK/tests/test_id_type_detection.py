import base64
from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote_plus

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

    def test_extracts_resident_name_merged_with_registration_number_row(self):
        values = extract_identity_fields(
            (
                OcrLine("주민등록증"),
                OcrLine("홍길동 900101-1234567", box=(20, 100, 290, 140)),
            )
        )

        self.assertEqual(values["custNm"], "홍길동")

    def test_extracts_resident_name_after_same_line_name_label(self):
        values = extract_identity_fields(
            (
                OcrLine("이름: 홍길동", box=(20, 70, 150, 100)),
                OcrLine("900101-1234567", box=(20, 110, 290, 145)),
            )
        )

        self.assertEqual(values["custNm"], "홍길동")

    def test_extracts_license_number_when_unambiguous(self):
        values = extract_identity_fields((OcrLine("11-22-123456-78"),))

        self.assertEqual(values["drvLicenIsueNum"], "11-22-123456-78")

    def test_extracts_license_number_split_across_labeled_ocr_lines(self):
        values = extract_identity_fields(
            (
                OcrLine("운전면허번호"),
                OcrLine("11-22-1234"),
                OcrLine("56-78"),
            )
        )

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

    def test_detects_foreign_resident_card_from_a_partial_heading(self):
        detected = detect_id_type((OcrLine("외국인"),))

        self.assertEqual(detected.code, "1401")

    def test_detects_foreign_resident_card_from_focused_partial_heading(self):
        detected = detect_id_type((OcrLine("외국", source="korean_focus"),))

        self.assertEqual(detected.code, "1401")

    def test_prioritizes_resident_issue_confirmation_over_resident_card(self):
        detected = detect_id_type(("주민등록증 발급확인서",))

        self.assertEqual(detected.candidate_codes, ("3001",))
        self.assertEqual(detected.type_code, "K")

    def test_foreign_passport_is_not_classified_as_generic_passport(self):
        detected = detect_id_type(("외국인", " 여 권 "))

        self.assertEqual(detected.candidate_codes, ("2002",))
        self.assertEqual(detected.type_code, "T")

    def test_driver_license_uses_legacy_default_and_retains_all_code_choices(self):
        detected = detect_id_type(("자동차 운전면허증",))

        self.assertEqual(detected.name, "운전면허증")
        self.assertEqual(detected.type_code, "D")
        self.assertEqual(detected.candidate_codes, ("1101", "1102", "1201"))
        self.assertEqual(detected.code, "1102")

    def test_table_code_to_type_mapping_covers_every_user_provided_code(self):
        from new_work.ocr.id_types import ID_TYPES

        self.assertEqual(
            {code: data["type"] for code, data in ID_TYPES.items()},
            {
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
            },
        )

    def test_each_document_family_detects_its_numeric_kait_code_and_letter_id_type(self):
        cases = (
            ("주민등록증", "1001", "R"),
            ("운전면허증", "1102", "D"),
            ("복지카드", "1301", "S"),
            ("국가유공자증", "1302", "N"),
            ("외국인등록증", "1401", "F"),
            ("여권", "2001", "P"),
            ("외국인 여권", "2002", "T"),
            ("주민등록증 발급확인서", "3001", "K"),
        )

        for label, expected_kait_id, expected_id_type in cases:
            with self.subTest(label=label):
                detected = detect_id_type((label,))
                self.assertEqual(detected.code, expected_kait_id)
                self.assertEqual(detected.type_code, expected_id_type)

    def test_extracts_english_name_after_spaced_foreigner_name_label(self):
        values = extract_identity_fields(
            (
                OcrLine("외국인등록증"),
                OcrLine("성 명"),
                OcrLine("WALAKULUGE MADURA J-"),
                OcrLine("ANAKA SUBHASHITHA"),
                OcrLine("국적 SRI LANKA"),
            )
        )

        self.assertEqual(values["custNm"], "WALAKULUGE MADURA J ANAKA SUBHASHITHA")

    def test_uses_english_name_rows_when_foreigner_label_is_misrecognized(self):
        values = extract_identity_fields(
            (
                OcrLine("외국인등록증", box=(59, 53, 342, 111)),
                OcrLine("월국입 011211-7240203 성", box=(356, 136, 925, 181)),
                OcrLine("등록번호", box=(360, 165, 454, 194)),
                OcrLine("NALAKULUGEMADURA-", box=(357, 237, 910, 278)),
                OcrLine("ANAKA SUBHASHTHA", box=(469, 275, 839, 312)),
                OcrLine("체류자격 비전문취업(E-9)", box=(357, 406, 800, 449)),
                OcrLine("CHIEF CHANGWON IMMIGRATION OFFICE", box=(469, 561, 916, 587)),
            )
        )

        self.assertEqual(values["custNm"], "NALAKULUGEMADURA ANAKA SUBHASHTHA")

    def test_uses_name_row_before_label_on_reverse_layout_foreigner_card(self):
        values = extract_identity_fields(
            (
                OcrLine("외국인등록증", box=(220, 38, 464, 90)),
                OcrLine("Registration No.", box=(28, 200, 178, 224), source="english"),
                OcrLine("SAMPLE PERSON", 0.91, (204, 236, 413, 267), "english_focus"),
                OcrLine("Name", 0.94, (118, 261, 183, 289), "english"),
                OcrLine("INDONESIA", 0.95, (199, 350, 388, 384), "english"),
            )
        )

        self.assertEqual(values["custNm"], "SAMPLE PERSON")

    def test_uses_focused_english_name_when_full_card_number_anchor_is_missing(self):
        values = extract_identity_fields(
            (
                OcrLine("외국인등록증"),
                OcrLine("SAMPLE HOLDER", 0.94, (25, 40, 230, 75), "english_focus"),
            )
        )

        self.assertEqual(values["custNm"], "SAMPLE HOLDER")

    def test_focused_partial_heading_enables_foreign_name_extraction(self):
        values = extract_identity_fields(
            (
                OcrLine("외국", source="korean_focus"),
                OcrLine("SAMPLE HOLDER", 0.94, None, "english_focus"),
            )
        )

        self.assertEqual(values["custNm"], "SAMPLE HOLDER")

    def test_extracts_foreign_registration_number_without_ocr_label(self):
        values = extract_identity_fields(
            (
                OcrLine("외국인등록증"),
                OcrLine("900101-5123456", box=(20, 100, 290, 140)),
            )
        )

        self.assertEqual(values["ctzCorpNum"], "9001015123456")

    def test_driver_detection_writes_numeric_kait_fields_and_letter_id_type(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "identity.jpg"
            image.write_bytes(synthetic_jpeg())
            detection = detect_id_type(("운전면허증",))
            output = write_ocr_result(
                OcrResult(
                    job_id="synthetic-job",
                    image_path=image,
                    lines=(OcrLine("운전면허증"),),
                    engine_name="synthetic OCR",
                    engine_version="test",
                ),
                detection,
                path=Path(directory) / "result.txt",
            )

            first_fields = output.read_text(encoding="utf-8").splitlines()[:3]

        self.assertEqual(first_fields, [
            "kaitIdType=1102",
            "recogSubCd=1102",
            "idType=D",
        ])

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
                    "custNm=%ED%99%8D%EA%B8%B8%EB%8F%99",
                    "isueDt=2020. 3. 26.",
                    "isuePlc=%EB%8C%80%EA%B5%AC%EA%B4%91%EC%97%AD%EC%8B%9C+%EC%84%9C%EA%B5%AC%EC%B2%AD%EC%9E%A5",
                    "nationClCd=I",
                    "addr=%EB%8C%80%EA%B5%AC%EA%B4%91%EC%97%AD%EC%8B%9C+%EC%84%9C%EA%B5%AC",
                    "mskPos=",
                    f"base64DATA={quote_plus(base64.b64encode(image_bytes).decode('ascii'))}",
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
        self.assertIn("drvLicenIsueNum=112233333344", content)
        self.assertTrue(content.endswith("idCrop=\n"))

    def test_mask_position_is_derived_from_resident_number_box_and_url_encoded(self):
        detected = detect_mask_position(
            (OcrLine("900101-1234567", box=(100, 200, 370, 240)),)
        )

        self.assertEqual(detected, "250,200,370,240")

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
