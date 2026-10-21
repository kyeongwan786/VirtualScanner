import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from new_work.image.face_crop import crop_id_portrait_jpeg


class OneFaceDetector:
    def detectMultiScale(self, _gray, **_kwargs):
        return [(40, 30, 20, 30)]


class AmbiguousFaceDetector:
    def detectMultiScale(self, _gray, **_kwargs):
        return [(40, 30, 30, 40), (100, 40, 29, 39)]


class NoFaceDetector:
    def detectMultiScale(self, _gray, **_kwargs):
        return ()


class FaceCropTests(unittest.TestCase):
    def test_crops_and_encodes_face_photo_region_as_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.jpg"
            Image.new("RGB", (200, 150), "white").save(source, format="JPEG")

            output = crop_id_portrait_jpeg(source, detector=OneFaceDetector())

        self.assertIsNotNone(output)
        with Image.open(BytesIO(output)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (42, 67))

    def test_no_or_ambiguous_face_does_not_crop_an_arbitrary_region(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.jpg"
            Image.new("RGB", (200, 150), "white").save(source, format="JPEG")

            self.assertIsNone(crop_id_portrait_jpeg(source, detector=NoFaceDetector()))
            self.assertIsNone(crop_id_portrait_jpeg(source, detector=AmbiguousFaceDetector()))


if __name__ == "__main__":
    unittest.main()
