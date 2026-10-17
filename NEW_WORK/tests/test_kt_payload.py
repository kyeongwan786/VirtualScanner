import base64
import unittest

from new_work.carrier.kt_payload import KtPayloadError, parse_kt_image_fragment


class KtPayloadTests(unittest.TestCase):
    def test_parses_both_observed_property_names_and_trailing_brace(self):
        image = b"synthetic-jpeg-bytes"
        encoded = base64.b64encode(image).decode("ascii")

        id_crop = parse_kt_image_fragment(
            f',"image":"{encoded}","imageSize":{len(image)}'
        )
        saved = parse_kt_image_fragment(
            f',"imageData":"{encoded}","imageDataSize":{len(image)}}}'
        )

        self.assertEqual((id_crop.image_key, id_crop.size_key), ("image", "imageSize"))
        self.assertEqual((saved.image_key, saved.size_key), ("imageData", "imageDataSize"))
        self.assertEqual(id_crop.image_bytes, image)
        self.assertEqual(saved.image_bytes, image)

    def test_rejects_size_mismatch_or_missing_required_field(self):
        encoded = base64.b64encode(b"synthetic").decode("ascii")
        with self.assertRaises(KtPayloadError):
            parse_kt_image_fragment(f',"image":"{encoded}","imageSize":1')
        with self.assertRaises(KtPayloadError):
            parse_kt_image_fragment(f',"image":"{encoded}"')


if __name__ == "__main__":
    unittest.main()
