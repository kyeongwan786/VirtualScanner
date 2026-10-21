import tempfile
import unittest
from pathlib import Path

from new_work.ocr.adapter import (
    OcrUnavailableError,
    PaddleOcrAdapter,
    _name_from_focused_lines,
)
from new_work.domain import OcrLine


class FakePrediction:
    json = {
        "res": {
            "rec_texts": ["서울시청", "12345", ""],
            "rec_scores": [0.97, 0.88, 0.2],
            "rec_polys": [
                [[1, 2], [31, 2], [31, 12], [1, 12]],
                [[4, 20], [44, 20], [44, 30], [4, 30]],
                [[0, 0], [0, 0], [0, 0], [0, 0]],
            ],
        }
    }


class FakeEngine:
    def __init__(self):
        self.paths = []

    def predict(self, input):
        self.paths.append(input)
        return [FakePrediction()]


class PaddleOcrAdapterTests(unittest.TestCase):
    def test_reads_text_recognition_pipeline_output(self):
        lines = PaddleOcrAdapter._read_lines(
            [{"res": {"rec_text": "SAMPLE HOLDER", "rec_score": 0.93}}]
        )

        self.assertEqual(lines, [OcrLine("SAMPLE HOLDER", 0.93, None)])

    def test_focused_english_name_ocr_discards_card_labels_and_keeps_initials(self):
        value = _name_from_focused_lines(
            [
                OcrLine("성"),
                OcrLine("WAL AKULUGE"),
                OcrLine("MADURA J-"),
                OcrLine("ANAKA"),
                OcrLine("SUBHASHITHA"),
                OcrLine("SRI LANKA"),
            ]
        )

        self.assertEqual(value, "WAL AKULUGE MADURA J ANAKA SUBHASHITHA")

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

        self.assertEqual(constructor_options["text_detection_model_name"], "PP-OCRv5_mobile_det")
        self.assertEqual(
            constructor_options["text_recognition_model_name"],
            "korean_PP-OCRv5_mobile_rec",
        )
        self.assertEqual(constructor_options["text_det_limit_side_len"], 640)
        self.assertFalse(constructor_options["use_doc_orientation_classify"])
        self.assertFalse(constructor_options["use_doc_unwarping"])
        self.assertFalse(constructor_options["use_textline_orientation"])
        self.assertEqual([line.text for line in result.lines], ["서울시청", "12345"])
        self.assertEqual(result.lines[0].confidence, 0.97)
        self.assertEqual(result.lines[0].box, (1.0, 2.0, 31.0, 12.0))
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
