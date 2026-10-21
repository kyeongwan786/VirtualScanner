import tempfile
import unittest
from pathlib import Path

from new_work.ocr.adapter import OcrUnavailableError, PaddleOcrAdapter


class FakePrediction:
    json = {
        "res": {
            "rec_texts": ["서울시청", "12345", ""],
            "rec_scores": [0.97, 0.88, 0.2],
        }
    }


class FakeEngine:
    def __init__(self):
        self.paths = []

    def predict(self, input):
        self.paths.append(input)
        return [FakePrediction()]


class PaddleOcrAdapterTests(unittest.TestCase):
    def test_korean_mobile_pipeline_returns_raw_lines_and_confidence(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        image_path = Path(tempdir.name) / "synthetic.jpg"
        image_path.write_bytes(b"synthetic fixture, fake engine does not decode image")
        engine = FakeEngine()
        constructor_options = {}

        def factory(**kwargs):
            constructor_options.update(kwargs)
            return engine

        adapter = PaddleOcrAdapter(engine_factory=factory)
        result = adapter.recognize(image_path)

        self.assertEqual(constructor_options["lang"], "korean")
        self.assertEqual(constructor_options["ocr_version"], "PP-OCRv5")
        self.assertFalse(constructor_options["use_doc_orientation_classify"])
        self.assertFalse(constructor_options["use_doc_unwarping"])
        self.assertFalse(constructor_options["use_textline_orientation"])
        self.assertEqual([line.text for line in result.lines], ["서울시청", "12345"])
        self.assertEqual(result.lines[0].confidence, 0.97)
        self.assertEqual(result.values, {})
        self.assertIn("PaddleOCR PP-OCRv5 Korean Mobile", result.engine_name)
        self.assertEqual(engine.paths, [str(image_path)])

    def test_engine_initializes_once_and_file_must_exist(self):
        calls = []
        engine = FakeEngine()
        adapter = PaddleOcrAdapter(engine_factory=lambda **kwargs: calls.append(kwargs) or engine)

        with self.assertRaises(OcrUnavailableError):
            adapter.recognize(Path("missing-synthetic.jpg"))
        self.assertEqual(calls, [])

        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        image_path = Path(tempdir.name) / "fixture.jpg"
        image_path.write_bytes(b"fixture")
        adapter.recognize(image_path)
        adapter.recognize(image_path)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
