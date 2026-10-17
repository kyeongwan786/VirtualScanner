import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from new_work.image.legacy_crop import find_latest_id_crop


class LegacyImageCropTests(unittest.TestCase):
    def test_newest_m_image_is_selected_with_original_code_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "older_m.jpg"
            newer = root / "newer_m.jpeg"
            ignored = root / "newest.jpg"
            Image.new("RGB", (500, 600), "red").save(older, format="JPEG")
            Image.new("RGB", (500, 600), "blue").save(newer, format="JPEG")
            Image.new("RGB", (800, 800), "green").save(ignored, format="JPEG")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            os.utime(ignored, (300, 300))

            crop = find_latest_id_crop(root, "1001")

            self.assertEqual(crop, root / "cropped_idcrop_image.jpg")
            with Image.open(crop) as image:
                self.assertEqual(image.size, (340, 406))
                self.assertGreater(image.getpixel((170, 203))[2], image.getpixel((170, 203))[0])

    def test_non_1001_code_uses_default_320_by_420_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (500, 600), "white").save(root / "input_m.jpg")

            crop = find_latest_id_crop(root, "1401")

            with Image.open(crop) as image:
                self.assertEqual(image.size, (320, 420))


if __name__ == "__main__":
    unittest.main()
