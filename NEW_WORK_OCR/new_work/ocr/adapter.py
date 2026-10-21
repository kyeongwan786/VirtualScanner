from __future__ import annotations

from importlib import metadata, util
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import uuid4

from new_work.domain import OcrLine, OcrResult
from new_work.ocr.mask_position import mask_position_from_box


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
        if ocr_version not in {"PP-OCRv3", "PP-OCRv4", "PP-OCRv5"}:
            raise ValueError("ocr_version must be PP-OCRv3, PP-OCRv4, or PP-OCRv5")
        self.device = device
        self.ocr_version = ocr_version
        self._engine_factory = engine_factory
        self._engine: Any | None = None
        self._english_engine: Any | None = None
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
            return f"PaddleOCR 한국어 {self.ocr_version} 모바일 ({self.device})"
        return "PaddleOCR 설치 필요 (현재 Python 환경에 설치)"

    def _create_engine(self, language: str = "korean") -> Any:
        factory = self._engine_factory
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
            return factory(
                lang=language,
                ocr_version=self.ocr_version,
                device=self.device,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
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
                    self._english_engine = self._create_engine("en")
        return self._english_engine

    def recognize(self, image_path: Path) -> OcrResult:
        path = Path(image_path)
        if not path.is_file():
            raise OcrUnavailableError(f"이미지 파일을 찾을 수 없습니다: {path}")

        started = perf_counter()
        try:
            outputs = self._get_engine().predict(input=str(path))
            lines = tuple(self._read_lines(outputs))
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
            engine_name=f"PaddleOCR {self.ocr_version} Korean Mobile",
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
            # On the standard landscape foreign resident card, the number is
            # printed to the right of the two-line registration label.
            crop_left = int(width * 0.46)
            crop_top = int(height * 0.215)
            crop = image[
                crop_top:int(height * 0.285),
                crop_left:int(width * 0.755),
            ]
            gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
            denoised = cv2.medianBlur(gray, 3)
            variants = [
                cv2.resize(denoised, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC),
                cv2.resize(
                    cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
                    None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC,
                ),
            ]

        with TemporaryDirectory(prefix="new_work_foreigner_num_") as temp_dir:
            engine = self._get_engine()
            for index, variant in enumerate(variants):
                crop_path = Path(temp_dir) / f"registration_number_{index}.png"
                if not cv2.imwrite(str(crop_path), variant):
                    continue
                outputs = engine.predict(input=str(crop_path))
                lines = self._read_lines(outputs)
                number = _number_from_focused_lines(lines)
                if number:
                    position = ""
                    number_lines = []
                    for line in lines:
                        digits = "".join(char for char in line.text if char.isdigit())
                        if not digits or line.box is None:
                            continue
                        number_lines.append((digits, line.box))
                    matching = [box for digits, box in number_lines if digits == number]
                    if not matching:
                        ordered = sorted(number_lines, key=lambda row: row[1][0])
                        if "".join(digits for digits, _ in ordered) == number:
                            boxes = [box for _, box in ordered]
                            heights = [box[3] - box[1] for box in boxes]
                            centers = [(box[1] + box[3]) / 2 for box in boxes]
                            if max(centers) - min(centers) <= max(heights, default=0):
                                matching = [(
                                    min(box[0] for box in boxes),
                                    min(box[1] for box in boxes),
                                    max(box[2] for box in boxes),
                                    max(box[3] for box in boxes),
                                )]
                    if matching:
                        # Map the OCR box from the 5x number crop back to the
                        # EXIF-corrected source JPG coordinate system.
                        left, top, right, bottom = matching[0]
                        source_box = (
                            crop_left + left / 5,
                            crop_top + top / 5,
                            crop_left + right / 5,
                            crop_top + bottom / 5,
                        )
                        position = mask_position_from_box(source_box) or ""
                    return number, position
        return "", ""

    def recognize_english(self, image_path: Path) -> tuple[OcrLine, ...]:
        """Run the English PP-OCRv5 pipeline across the entire card image."""
        path = Path(image_path)
        if not path.is_file():
            raise OcrUnavailableError(f"이미지 파일을 찾을 수 없습니다: {path}")
        try:
            outputs = self._get_english_engine().predict(input=str(path))
            return tuple(self._read_lines(outputs))
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
