import base64
import unittest
from urllib.parse import unquote_plus

from new_work.carrier.sk_report import (
    SK_REPORT_KEYS,
    SK_REPORT_TEXT_KEYS,
    SkReportInputError,
    render_sk_report,
)


class SkReportTests(unittest.TestCase):
    def setUp(self):
        self.fields = {key: f"test {key} + 한글" for key in SK_REPORT_TEXT_KEYS}
        self.fields["kaitIdType"] = "1001"
        self.fields["drvLicenIsueNum"] = ""
        self.full_image = b"synthetic-full-image"
        self.id_crop = b"synthetic-cropped-image"

    def test_report_has_observed_order_encoding_and_empty_value(self):
        output = render_sk_report(self.fields, self.full_image, self.id_crop)
        lines = output.decode("utf-8").splitlines()

        self.assertEqual(tuple(line.split("=", 1)[0] for line in lines), SK_REPORT_KEYS)
        self.assertEqual(lines[6], "drvLicenIsueNum=")
        self.assertIn(b"\r\n", output)
        self.assertNotIn(b"\n", output.replace(b"\r\n", b""))
        parsed = {
            key: unquote_plus(value)
            for key, value in (line.split("=", 1) for line in lines)
        }
        self.assertEqual(parsed["custNm"], self.fields["custNm"])
        self.assertEqual(
            base64.b64decode(parsed["base64DATA"]), self.full_image
        )
        self.assertEqual(base64.b64decode(parsed["idCrop"]), self.id_crop)

    def test_missing_or_null_fields_are_not_silently_coerced(self):
        with self.assertRaises(SkReportInputError):
            render_sk_report({}, self.full_image, self.id_crop)
        fields = dict(self.fields)
        fields["custNm"] = None
        with self.assertRaises(SkReportInputError):
            render_sk_report(fields, self.full_image, self.id_crop)

    def test_id_crop_is_emitted_only_for_legacy_supported_codes(self):
        fields = dict(self.fields)
        fields["kaitIdType"] = "1401"
        output = render_sk_report(fields, self.full_image, self.id_crop)
        names = tuple(line.split("=", 1)[0] for line in output.decode("utf-8").splitlines())
        self.assertEqual(names, (*SK_REPORT_TEXT_KEYS, "base64DATA"))

    def test_synthetic_nonpersonal_report_matches_exact_bytes(self):
        fields = {key: "" for key in SK_REPORT_TEXT_KEYS}
        fields.update(kaitIdType="1001", recogSubCd="1001", idType="R")
        expected = (
            "kaitIdType=1001\r\n"
            "recogSubCd=1001\r\n"
            "idType=R\r\n"
            "ctzCorpNum=\r\n"
            "custNm=\r\n"
            "isueDt=\r\n"
            "drvLicenIsueNum=\r\n"
            "isuePlc=\r\n"
            "nationClCd=\r\n"
            "addr=\r\n"
            "mskPos=\r\n"
            "base64DATA=aW1hZ2U%3D\r\n"
            "idCrop=Y3JvcA%3D%3D\r\n"
        ).encode("utf-8")

        self.assertEqual(render_sk_report(fields, b"image", b"crop"), expected)


if __name__ == "__main__":
    unittest.main()
