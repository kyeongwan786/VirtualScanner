from __future__ import annotations

from importlib import metadata, util
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import uuid4

from new_work.domain import OcrLine, OcrResult
from new_work.ocr.mask_position import mask_position_from_box

OCR_BUILD_ID = "20261022-name-crop-2"


class OcrUnavailableError(RuntimeError):
    pass


class OcrAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def status(self) -> str: ...

    def recognize(self, image_path: Path) -> OcrResult: ...


class PaddleOcrAdapter:
    """Korean JPG OCR using a configurable lightweight PaddleOCR pipeline."""

    def __init__(
        self,
        device: str = "cpu",
        ocr_version: str = "PP-OCRv5",
        engine_factory: Callable[..., Any] | None = None,
    ) -> None:
        if device not in {"cpu", "gpu"}:
            raise ValueError("device must be 'cpu' or 'gpu'")
        if ocr_version != "PP-OCRv5":
            raise ValueError("이 프로젝트는 포함된 PP-OCRv5 모델을 사용합니다")
        self.device = device
        self.ocr_version = ocr_version
        self._engine_factory = engine_factory
        self._engine: Any | None = None
        self._english_engine: Any | None = None
        self._recognizers: dict[str, Any] = {}
        self._lock = Lock()

    @property
    def available(self) -> bool:
        return self._engine_factory is not None or (
            util.find_spec("paddleocr") is not None
            and util.find_spec("paddle") is not None
        )

    @property
    def status(self) -> str:
        if self.available:
            return (
                f"PaddleOCR 한국어 {self.ocr_version} 모바일 ({self.device}) "
                f"[{OCR_BUILD_ID}]"
            )
        return "PaddleOCR 설치 필요 (현재 Python 환경에 설치)"

    def _create_engine(self, language: str = "korean") -> Any:
        factory = self._engine_factory
        model_paths = _ocr_model_paths(self.ocr_version)
        if factory is None:
            if not self.available:
                raise OcrUnavailableError(
                    "PaddleOCR/PaddlePaddle이 설치되지 않았습니다. "
                    "NEW_WORK/requirements.txt를 현재 Python 환경에 설치하세요."
                )
            try:
                from paddleocr import PaddleOCR
            except Exception as exc:  # native runtime/import errors vary by platform
                raise OcrUnavailableError(
                    f"PaddleOCR를 불러오지 못했습니다: {exc}"
                ) from exc
            factory = PaddleOCR

        try:
            model_options = (
                {
                    "text_detection_model_name": f"{self.ocr_version}_mobile_det",
                    "text_detection_model_dir": str(model_paths["det"]),
                    "text_recognition_model_name": (
                        f"{'korean' if language == 'korean' else 'en'}_"
                        f"{self.ocr_version}_mobile_rec"
                    ),
                    "text_recognition_model_dir": str(model_paths[language]),
                }
                if self._engine_factory is None
                else {
                    "text_detection_model_name": f"{self.ocr_version}_mobile_det",
                    "text_recognition_model_name": (
                        f"{'korean' if language == 'korean' else 'en'}_"
                        f"{self.ocr_version}_mobile_rec"
                    ),
                }
            )
            return factory(
                **model_options,
                device=self.device,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_det_limit_side_len=640,
            )
        except Exception as exc:
            raise OcrUnavailableError(
                "PaddleOCR 엔진 초기화에 실패했습니다. CPU/GPU 런타임과 "
                f"모델 다운로드 상태를 확인하세요: {exc}"
            ) from exc

    def _get_engine(self) -> Any:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    self._engine = self._create_engine()
        return self._engine

    def _get_english_engine(self) -> Any:
        if self._english_engine is None:
            with self._lock:
                if self._english_engine is None:
                    self._english_engine = self._create_recognizer("en")
        return self._english_engine

    def _create_recognizer(self, language: str) -> Any:
        try:
            factory = self._engine_factory
            model_paths = _ocr_model_paths(self.ocr_version)
            if factory is None:
                from paddleocr import TextRecognition

                factory = TextRecognition
            model_options = (
                {
                    "model_name": (
                        f"{'korean' if language == 'korean' else 'en'}_"
                        f"{self.ocr_version}_mobile_rec"
                    ),
                    "model_dir": str(model_paths[language]),
                }
                if self._engine_factory is None
                else {
                    "model_name": (
                        f"{'korean' if language == 'korean' else 'en'}_"
                        f"{self.ocr_version}_mobile_rec"
                    )
                }
            )
            return factory(
                **model_options,
                device=self.device,
            )
        except Exception as exc:
            raise OcrUnavailableError(
                f"PaddleOCR 문자 인식 모델 초기화에 실패했습니다: {exc}"
            ) from exc

    def _get_number_recognizer(self) -> Any:
        if "korean" not in self._recognizers:
            with self._lock:
                if "korean" not in self._recognizers:
                    self._recognizers["korean"] = self._create_recognizer("korean")
        return self._recognizers["korean"]

    def warmup(self) -> None:
        """Load OCR models before the operator starts a document review."""
        self._get_engine()
        self._get_english_engine()
        self._get_number_recognizer()

    def recognize_card_heading(self, image_path: Path) -> tuple[OcrLine, ...]:
        """Re-read the small upper-left document heading when full OCR is unsure."""
        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        path = Path(image_path)
        if not path.is_file():
            raise OcrUnavailableError(f"이미지 파일을 찾을 수 없습니다: {path}")
        with Image.open(path) as source:
            rgb = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
        height, width = rgb.shape[:2]
        crop = rgb[int(height * 0.05):int(height * 0.20),
                   int(width * 0.05):int(width * 0.40)]
        crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        with TemporaryDirectory(prefix="new_work_heading_") as temp_dir:
            crop_path = Path(temp_dir) / "heading.png"
            if not cv2.imwrite(str(crop_path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)):
                return ()
            lines = self._read_lines(
                self._get_number_recognizer().predict(input=str(crop_path))
            )
        return tuple(
            OcrLine(line.text, line.confidence, None, "korean_focus")
            for line in lines
        )

    def recognize(self, image_path: Path) -> OcrResult:
        path = Path(image_path)
        if not path.is_file():
            raise OcrUnavailableError(f"이미지 파일을 찾을 수 없습니다: {path}")

        started = perf_counter()
        try:
            outputs = self._get_engine().predict(input=str(path))
            lines = tuple(
                OcrLine(line.text, line.confidence, line.box, "korean")
                for line in self._read_lines(outputs)
            )
        except OcrUnavailableError:
            raise
        except Exception as exc:
            raise OcrUnavailableError(f"PaddleOCR 인식 실패: {exc}") from exc

        try:
            version = metadata.version("paddleocr")
        except metadata.PackageNotFoundError:
            version = "installed"
        return OcrResult(
            job_id=str(uuid4()),
            image_path=path,
            lines=lines,
            engine_name=(
                f"PaddleOCR {self.ocr_version} Korean Mobile [{OCR_BUILD_ID}]"
            ),
            engine_version=version,
            elapsed_seconds=perf_counter() - started,
        )

    def recognize_foreigner_reg_number(self, image_path: Path) -> str:
        """Re-read the printed number area at high resolution on alien cards."""
        number, _ = self.recognize_foreigner_reg_number_details(image_path)
        return number

    def recognize_foreigner_reg_number_details(
        self, image_path: Path
    ) -> tuple[str, str]:
        """Return the foreign registration number and its six-digit mask box."""
        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(image_path) as source:
            image = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
            height, width = image.shape[:2]
            # Determine the card layout once and read only that number band.
            # TextRecognition skips the detector because the crop is already
            # a single known text row.
            if _face_on_right(image):
                crop_left, crop_top, crop_right, crop_bottom = (
                    int(width * 0.10), int(height * 0.22),
                    int(width * 0.63), int(height * 0.39),
                )
            else:
                crop_left, crop_top, crop_right, crop_bottom = (
                    int(width * 0.35), int(height * 0.17),
                    int(width * 0.78), int(height * 0.30),
                )
            crop = image[crop_top:crop_bottom, crop_left:crop_right]
            gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
            denoised = cv2.medianBlur(gray, 3)
            variants = [cv2.resize(
                crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC
            )]

        with TemporaryDirectory(prefix="new_work_foreigner_num_") as temp_dir:
            engine = self._get_number_recognizer()
            for index, variant in enumerate(variants):
                crop_path = Path(temp_dir) / f"registration_number_{index}.png"
                if not cv2.imwrite(str(crop_path), variant):
                    continue
                outputs = engine.predict(input=str(crop_path))
                lines = self._read_lines(outputs)
                number = _number_from_focused_lines(lines)
                if not number and index == 0:
                    thresholded = cv2.threshold(
                        denoised, 0, 255,
                        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
                    )[1]
                    variants.append(cv2.resize(
                        thresholded, None, fx=3, fy=3,
                        interpolation=cv2.INTER_CUBIC,
                    ))
                    continue
                if number:
                    # This recognition-only model does not return text boxes.
                    # The crop bounds approximate the line bounds for mskPos.
                    position = mask_position_from_box((
                        float(crop_left), float(crop_top),
                        float(crop_right), float(crop_bottom),
                    )) or ""
                    return number, position
        return "", ""

    def recognize_english(self, image_path: Path) -> tuple[OcrLine, ...]:
        """Run English OCR on the likely printed name area only."""
        path = Path(image_path)
        if not path.is_file():
            raise OcrUnavailableError(f"이미지 파일을 찾을 수 없습니다: {path}")
        try:
            engine = self._get_english_engine()
            # The Korean full-card pass already reads the remaining fields.
            # Avoid spending a second full-card detector pass for English.
            import cv2
            import numpy as np
            from PIL import Image, ImageOps

            with Image.open(path) as source:
                rgb = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
            height, width = rgb.shape[:2]
            face_on_right = _face_on_right(rgb)
            if face_on_right:
                # The name is left of the portrait on this card layout.
                x_left, x_right = 0.17, 0.48
            else:
                # On the opposite layout, names start just right of the
                # Korean name label and can extend close to the card edge.
                x_left, x_right = 0.44, 0.96
            # The printed name can occupy two separate rows. Recognize each
            # row on its own; TextRecognition does not detect or separate
            # multiple lines when they are passed as one crop.
            row_bands = ((0.36, 0.435), (0.425, 0.50))
            with TemporaryDirectory(prefix="new_work_foreigner_name_") as temp_dir:
                focused_lines = []
                for row_index, (top_ratio, bottom_ratio) in enumerate(row_bands):
                    left = int(width * x_left)
                    right = int(width * x_right)
                    top = int(height * top_ratio)
                    bottom = int(height * bottom_ratio)
                    crop_rgb = rgb[top:bottom, left:right]
                    # The vertical security pattern interferes with narrow
                    # Latin strokes in both portrait layouts. A 3x3 median
                    # filter suppresses stripes while preserving character edges.
                    crop_rgb = cv2.medianBlur(crop_rgb, 3)
                    scale = min(5.0, 220.0 / max(1, crop_rgb.shape[0]))
                    crop = cv2.resize(
                        crop_rgb, None, fx=scale, fy=scale,
                        interpolation=cv2.INTER_CUBIC,
                    )
                    crop_path = Path(temp_dir) / f"name_row_{row_index}.png"
                    cv2.imwrite(
                        str(crop_path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
                    )
                    focused_outputs = engine.predict(input=str(crop_path))
                    for line in self._read_lines(focused_outputs):
                        mapped_box = (float(left), float(top), float(right), float(bottom))
                        text = _name_with_visual_spaces(line.text, rgb, mapped_box)
                        focused_lines.append(
                            OcrLine(text, line.confidence, mapped_box, "english_focus")
                        )
            return tuple(focused_lines)
        except OcrUnavailableError:
            raise
        except Exception as exc:
            raise OcrUnavailableError(f"영문 PaddleOCR 인식 실패: {exc}") from exc

    @staticmethod
    def _read_lines(outputs: Any) -> list[OcrLine]:
        """Read PaddleOCR 3.x prediction objects without depending on their class."""
        lines: list[OcrLine] = []
        for output in outputs or ():
            payload = getattr(output, "json", output)
            if callable(payload):
                payload = payload()
            if not isinstance(payload, dict):
                continue
            result = payload.get("res", payload)
            if not isinstance(result, dict):
                continue
            texts = result.get("rec_texts")
            scores = result.get("rec_scores")
            boxes = result.get("rec_polys")
            if texts is None and result.get("rec_text") is not None:
                texts = [result["rec_text"]]
                scores = [result.get("rec_score")]
            if boxes is None:
                boxes = result.get("dt_polys")
            texts = [] if texts is None else texts
            scores = [] if scores is None else scores
            boxes = [] if boxes is None else boxes
            for index, text in enumerate(texts):
                if text is None or not str(text).strip():
                    continue
                score = None
                if index < len(scores) and scores[index] is not None:
                    try:
                        score = float(scores[index])
                    except (TypeError, ValueError):
                        score = None
                box = PaddleOcrAdapter._read_box(
                    boxes[index] if index < len(boxes) else None
                )
                lines.append(OcrLine(text=str(text), confidence=score, box=box))
        return lines

    @staticmethod
    def _read_box(points: Any) -> tuple[float, float, float, float] | None:
        """Convert a PaddleOCR polygon into source-image left, top, right, bottom."""
        try:
            coordinates = points.tolist() if hasattr(points, "tolist") else points
            flattened = [point for point in coordinates if point is not None]
            xs = [float(point[0]) for point in flattened]
            ys = [float(point[1]) for point in flattened]
            if not xs or not ys:
                return None
            left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
            if right <= left or bottom <= top:
                return None
            return left, top, right, bottom
        except (TypeError, ValueError, IndexError):
            return None


def _number_from_focused_lines(lines: list[OcrLine]) -> str:
    """Accept a clear 13-digit number from the tightly cropped number field."""
    candidates: set[str] = set()
    numeric_rows: list[str] = []
    for line in lines:
        digits = "".join(char for char in line.text if char.isdigit())
        if len(digits) >= 13:
            for offset in range(len(digits) - 12):
                candidate = digits[offset:offset + 13]
                if candidate[6] in "5678":
                    candidates.add(candidate)
        elif digits and all(char.isdigit() or char in " -‐‑–—." for char in line.text.strip()):
            numeric_rows.append(digits)
    joined = "".join(numeric_rows)
    if len(joined) == 13 and joined[6] in "5678":
        candidates.add(joined)
    return next(iter(candidates)) if len(candidates) == 1 else ""


def _ocr_model_paths(ocr_version: str) -> dict[str, Path]:
    """Resolve the exact bundled OCR weights for source and frozen runs."""
    if ocr_version != "PP-OCRv5":
        raise OcrUnavailableError(
            "이 빌드에는 PP-OCRv5 모델만 포함되어 있습니다."
        )
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    model_root = root / "ocr_models"
    paths = {
        "det": model_root / "PP-OCRv5_mobile_det",
        "korean": model_root / "korean_PP-OCRv5_mobile_rec",
        "en": model_root / "en_PP-OCRv5_mobile_rec",
    }
    missing = [
        str(path) for path in paths.values()
        if not (path / "inference.pdiparams").is_file()
        or not (path / "inference.yml").is_file()
    ]
    if missing:
        raise OcrUnavailableError(
            "포함된 OCR 모델 파일을 찾지 못했습니다: " + ", ".join(missing)
        )
    return paths


def _face_on_right(image_rgb: Any) -> bool:
    """Return whether the card portrait is on the right half, when detected."""
    import cv2

    height, width = image_rgb.shape[:2]
    cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    if cascade.empty():
        return False
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=1.08,
        minNeighbors=4,
        minSize=(max(24, width // 25), max(24, height // 14)),
    )
    return bool(len(faces)) and max(
        x + face_width / 2 for x, _, face_width, _ in faces
    ) > width / 2


def _name_from_focused_lines(lines: list[OcrLine]) -> str:
    """Parse Latin name text, excluding common card labels and nationality."""
    stopwords = {
        "CARD", "KOR", "RESIDENCE", "NAME", "FULL", "SEX", "GENDER",
        "NATIONALITY", "ADDRESS", "DATE", "BIRTH", "ISSUE", "EXPIRY",
        "PERIOD", "STATUS", "SRI", "LANKA",
    }
    words: list[str] = []
    for line in lines:
        for raw in re.findall(r"[A-Za-z][A-Za-z'-]*", line.text):
            word = raw.strip("'-")
            if not word or word.upper() in stopwords:
                continue
            if len(word) == 1 and not raw.endswith("-"):
                continue
            words.append(word.upper())
    return " ".join(words)


def _name_with_visual_spaces(
    text: str,
    image_rgb: Any,
    box: tuple[float, float, float, float] | None,
) -> str:
    """Restore word boundaries the recognizer drops in a compact name line."""
    if box is None:
        return text
    compact = "".join(re.findall(r"[A-Za-z'-]", text))
    if len(compact) < 8 or len(text.split()) != 1:
        return text
    try:
        import cv2
        import numpy as np

        height, width = image_rgb.shape[:2]
        left, top, right, bottom = box
        x1, y1 = max(0, int(left)), max(0, int(top))
        x2, y2 = min(width, int(right)), min(height, int(bottom))
        if x2 <= x1 or y2 <= y1:
            return text
        gray = cv2.cvtColor(image_rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        if gray.size == 0:
            return text
        # A fixed dark-ink threshold suppresses most of the colored security
        # background while retaining the large inter-word gap.
        binary = cv2.threshold(gray, 70, 255, cv2.THRESH_BINARY_INV)[1]
        projection = (binary > 0).sum(axis=0)
        active = projection > 1
        minimum_gap = max(8, int(gray.shape[0] * 0.22))
        gaps = []
        index = 0
        while index < len(active):
            if active[index]:
                index += 1
                continue
            start = index
            while index < len(active) and not active[index]:
                index += 1
            end = index
            if (
                end - start >= minimum_gap
                and start > len(active) * 0.04
                and end < len(active) * 0.96
            ):
                boundary = round(((start + end) / 2) / len(active) * len(compact))
                if 0 < boundary < len(compact):
                    gaps.append(boundary)
        # The source text already contains some spaces. Map those to the same
        # compact character offsets before adding the visually measured gaps.
        existing = set()
        offset = 0
        for char in text:
            if char.isspace():
                if 0 < offset < len(compact):
                    existing.add(offset)
            elif re.match(r"[A-Za-z'-]", char):
                offset += 1
        boundaries = sorted(existing | set(gaps))
        if not boundaries:
            return text
        pieces = []
        start = 0
        for boundary in boundaries:
            if boundary > start:
                pieces.append(compact[start:boundary])
                start = boundary
        pieces.append(compact[start:])
        return " ".join(piece for piece in pieces if piece)
    except Exception:
        return text


class UnconnectedOcrAdapter:
    """Explicit fallback used only when the OCR runtime cannot be imported."""

    @property
    def available(self) -> bool:
        return False

    @property
    def status(self) -> str:
        return "PaddleOCR 미연결 (설치 필요)"

    def recognize(self, image_path: Path) -> OcrResult:
        raise OcrUnavailableError(
            "PaddleOCR/PaddlePaddle 런타임이 설치되지 않아 OCR을 실행할 수 없습니다."
        )
