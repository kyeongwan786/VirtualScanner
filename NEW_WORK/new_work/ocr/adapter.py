from __future__ import annotations

from importlib import metadata, util
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import uuid4

from new_work.domain import OcrLine, OcrResult


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
        ocr_version: str = "PP-OCRv3",
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
        return "PaddleOCR 설치 필요 (Windows 빌드 환경에서 설치)"

    def _create_engine(self) -> Any:
        factory = self._engine_factory
        if factory is None:
            if not self.available:
                raise OcrUnavailableError(
                    "PaddleOCR/PaddlePaddle이 설치되지 않았습니다. "
                    "NEW_WORK Windows 설치 안내에 따라 OCR 런타임을 설치하세요."
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
                lang="korean",
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
            texts = result.get("rec_texts") or []
            scores = result.get("rec_scores") or []
            for index, text in enumerate(texts):
                if text is None or not str(text).strip():
                    continue
                score = None
                if index < len(scores) and scores[index] is not None:
                    try:
                        score = float(scores[index])
                    except (TypeError, ValueError):
                        score = None
                lines.append(OcrLine(text=str(text), confidence=score))
        return lines


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
