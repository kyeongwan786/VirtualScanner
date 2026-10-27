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
from new_work.ocr.mask_position import _mask_position_for_number_row

OCR_BUILD_ID = "20261026-issuer-date-descreen-1"


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
        self._detail_engine: Any | None = None
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

    def _create_engine(
        self, language: str = "korean", *, detection_limit: int = 640
    ) -> Any:
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
                text_det_limit_side_len=detection_limit,
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

    def _get_detail_engine(self) -> Any:
        """Load a larger-input detector only when the first pass misses fields."""
        if self._detail_engine is None:
            with self._lock:
                if self._detail_engine is None:
                    self._detail_engine = self._create_engine(detection_limit=1024)
        return self._detail_engine

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
        """Load the common Korean full-card pipeline before review starts.

        Focused Korean-recognition and English-recognition models are loaded
        on demand. Initializing all three at launch makes startup much slower
        while most documents only use the full-card Korean pipeline.
        """
        self._get_engine()

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
        # The title can span half the card width on older resident cards.
        # Keep the whole heading in view instead of truncating its last syllables.
        crop = rgb[int(height * 0.04):int(height * 0.22),
                   int(width * 0.04):int(width * 0.62)]
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

    def recognize_korean_name_near_number(
        self,
        image_path: Path,
        source_lines: tuple[OcrLine, ...],
        id_type_code: str | None = None,
    ) -> str:
        """Focus on the name row positioned next to a detected ID number.

        Korean resident cards commonly place the holder name immediately
        above the resident number; driver's licenses often put it below the
        license number. The OCR number box anchors the crop, so card size and
        resolution can vary without per-image coordinates.
        """
        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        from new_work.ocr.field_extractor import _LICENSE, _RRN

        path = Path(image_path)
        with Image.open(path) as source:
            rgb = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
        height, width = rgb.shape[:2]

        license_line = next((
            line for line in source_lines
            if line.box is not None and _LICENSE.search(line.text)
        ), None)
        number_line = license_line
        name_above = False
        if number_line is None and id_type_code not in {"1101", "1102", "1201"}:
            number_line = next((
                line for line in source_lines
                if line.box is not None and _RRN.search(line.text)
            ), None)
            name_above = True
        if number_line is None or number_line.box is None:
            return ""

        left, top, right, bottom = number_line.box
        row_height = max(1.0, bottom - top)
        # Keep the crop width close to the detected row. The old generous
        # margin forced the recognition model to shrink the actual name and
        # include more security-print noise than useful text.
        pad_x = row_height * 0.12
        crop_left = max(0, int(left - pad_x))
        crop_right = min(width, int(right + pad_x))
        if name_above:
            crop_top = max(0, int(top - row_height * 1.9))
            crop_bottom = max(crop_top + 1, int(top - row_height * 0.6))
        else:
            crop_top = min(height - 1, int(bottom + row_height * 0.35))
            crop_bottom = min(height, int(bottom + row_height * 1.25))
        crop = rgb[crop_top:crop_bottom, crop_left:crop_right]
        if crop.size == 0:
            return ""
        crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)

        with TemporaryDirectory(prefix="new_work_name_focus_") as temp_dir:
            crop_path = Path(temp_dir) / "name.png"
            if not cv2.imwrite(
                str(crop_path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
            ):
                return ""
            focused_lines = self._read_lines(
                self._get_number_recognizer().predict(input=str(crop_path))
            )
        from new_work.ocr.field_extractor import extract_identity_fields

        focused = tuple(
            OcrLine(line.text, line.confidence, None, "korean_name_focus")
            for line in focused_lines
        )
        return extract_identity_fields(focused, id_type_code).get("custNm", "")

    def recognize_numeric_fields(
        self,
        image_path: Path,
        source_lines: tuple[OcrLine, ...],
        id_type_code: str | None,
    ) -> dict[str, str]:
        """Re-read detected ID-number rows at high resolution and require consensus."""
        from collections import Counter

        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        from new_work.ocr.field_extractor import (
            _LICENSE,
            _NUMERIC_OCR_TRANSLATION,
            _RRN,
            _structured_rrn_candidates,
            is_valid_resident_number,
        )

        rrn_codes = {"1001", "1101", "1102", "1201", "1301", "1302", "1303"}
        license_codes = {"1101", "1102", "1201"}
        jobs: list[tuple[str, OcrLine]] = []

        def ranked_row(field: str, pattern: Any, expected_digits: int) -> OcrLine | None:
            candidates: list[tuple[tuple[float, ...], OcrLine]] = []
            for line in source_lines:
                if line.box is None:
                    continue
                normalized = line.text.translate(_NUMERIC_OCR_TRANSLATION)
                digits = "".join(char for char in normalized if char.isdecimal())
                if len(digits) < 9:
                    continue
                left, top, right, bottom = line.box
                width = right - left
                height = max(1.0, bottom - top)
                if width < height * 2.2:
                    continue
                matched = bool(pattern.search(normalized))
                if field == "ctzCorpNum" and not matched:
                    matched = bool(_structured_rrn_candidates(normalized))
                if not matched:
                    continue
                confidence = line.confidence if line.confidence is not None else 0.0
                rank = (float(matched), -float(abs(len(digits) - expected_digits)), confidence)
                candidates.append((rank, line))
            if not candidates:
                return None
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]

        if id_type_code in rrn_codes:
            row = ranked_row("ctzCorpNum", _RRN, 13)
            if row is not None:
                jobs.append(("ctzCorpNum", row))
        if id_type_code in license_codes:
            row = ranked_row("drvLicenIsueNum", _LICENSE, 12)
            if row is not None:
                jobs.append(("drvLicenIsueNum", row))
        if not jobs:
            return {}

        with Image.open(Path(image_path)) as source:
            rgb = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
        image_height, image_width = rgb.shape[:2]
        recognized: dict[str, list[str]] = {field: [] for field, _ in jobs}
        with TemporaryDirectory(prefix="new_work_numeric_focus_") as temp_dir:
            engine = self._get_number_recognizer()
            for job_index, (field, line) in enumerate(jobs):
                assert line.box is not None
                left, top, right, bottom = line.box
                row_height = max(1.0, bottom - top)
                pad_x = max(6, round(row_height * 0.30))
                pad_y = max(3, round(row_height * 0.28))
                crop = rgb[
                    max(0, int(top - pad_y)):min(image_height, int(bottom + pad_y)),
                    max(0, int(left - pad_x)):min(image_width, int(right + pad_x)),
                ]
                if crop.size == 0:
                    continue
                scale = min(6.0, max(3.0, 120.0 / max(1, crop.shape[0])))
                color_variant = cv2.resize(
                    crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
                )
                gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
                contrast = cv2.createCLAHE(
                    clipLimit=2.0, tileGridSize=(8, 8)
                ).apply(gray)
                contrast_variant = cv2.resize(
                    contrast, None, fx=scale, fy=scale,
                    interpolation=cv2.INTER_CUBIC,
                )
                for variant_index, variant in enumerate(
                    (color_variant, contrast_variant)
                ):
                    crop_path = Path(temp_dir) / f"{job_index}_{variant_index}.png"
                    if variant.ndim == 3:
                        variant = cv2.cvtColor(variant, cv2.COLOR_RGB2BGR)
                    if not cv2.imwrite(str(crop_path), variant):
                        continue
                    outputs = engine.predict(input=str(crop_path))
                    variant_candidates: set[str] = set()
                    for recognized_line in self._read_lines(outputs):
                        normalized = recognized_line.text.translate(
                            _NUMERIC_OCR_TRANSLATION
                        )
                        if field == "ctzCorpNum":
                            match = _RRN.search(normalized)
                            if match:
                                variant_candidates.add("".join(match.groups()))
                            variant_candidates.update(
                                _structured_rrn_candidates(normalized)
                            )
                        else:
                            match = _LICENSE.search(normalized)
                            if match:
                                variant_candidates.add("".join(match.groups()))
                            else:
                                digits = "".join(
                                    char for char in normalized if char.isdecimal()
                                )
                                if len(digits) == 12:
                                    variant_candidates.add(digits)
                    recognized[field].extend(variant_candidates)

        focused: dict[str, str] = {}
        for field, candidates in recognized.items():
            counts = Counter(candidates)
            if field == "ctzCorpNum":
                checksum_valid = {
                    value for value in counts if is_valid_resident_number(value)
                }
                if len(checksum_valid) == 1:
                    focused[field] = next(iter(checksum_valid))
                # Repeated recognition must not reintroduce an invalid
                # checksum when no valid candidate was found.
                continue
            if counts:
                value, count = counts.most_common(1)[0]
                if count >= 2:
                    focused[field] = value
        return focused

    def recognize_issuer(
        self, image_path: Path, recog_sub_cd: str | None = None
    ) -> str:
        """Read the small issuer row separately without changing other fields."""
        return self.recognize_issuer_fields(image_path, recog_sub_cd).get("isuePlc", "")

    def recognize_issuer_fields(
        self,
        image_path: Path,
        recog_sub_cd: str | None = None,
        source_lines: tuple[OcrLine, ...] = (),
    ) -> dict[str, str]:
        """Re-read the issuing row with contrast variants and vote on fields."""
        from collections import Counter

        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(Path(image_path)) as source:
            rgb = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
        height, width = rgb.shape[:2]
        if recog_sub_cd == "1001":
            box = (0.20, 0.78, 0.97, 0.96)
        elif recog_sub_cd in {"1101", "1102", "1201"}:
            box = (0.27, 0.80, 0.96, 0.95)
        else:
            # Foreign cards place the issuing office beside the issue date at
            # the lower right. This crop avoids the portrait and number row.
            box = (0.27, 0.78, 0.93, 0.91)
        left, top, right, bottom = box
        crop = rgb[
            int(height * top):int(height * bottom),
            int(width * left):int(width * right),
        ]
        from new_work.ocr.field_extractor import (
            _ISSUER,
            _dates_in_text,
            _strip_issue_date,
            extract_identity_fields,
        )

        field_votes: dict[str, list[str]] = {"isueDt": [], "isuePlc": []}
        original = extract_identity_fields(source_lines, id_type_code=recog_sub_cd)
        for field in field_votes:
            if original.get(field):
                field_votes[field].append(original[field])

        # When full-card OCR has already linked an issue date to the issuing
        # authority, crop that detected text row instead of the whole lower
        # band. The broad fallback band contains expiry dates and security
        # print that can turn a printed 11 into 01.
        issuer_rows = [
            line for line in source_lines
            if line.box is not None and _ISSUER.search(_strip_issue_date(line.text))
        ]
        date_row_crop = False
        if issuer_rows:
            issuer_row = max(
                issuer_rows,
                key=lambda line: line.confidence if line.confidence is not None else 0.0,
            )
            left, top, right, bottom = issuer_row.box
            row_height = max(1.0, bottom - top)
            row_center = (top + bottom) / 2
            related_rows = [issuer_row]
            for line in source_lines:
                if line is issuer_row or line.box is None or not _dates_in_text(line.text):
                    continue
                date_left, date_top, date_right, date_bottom = line.box
                date_center = (date_top + date_bottom) / 2
                date_height = max(1.0, date_bottom - date_top)
                # Some ID layouts put the issue date on a separate line just
                # above the issuer. Include that adjacent row in focused OCR.
                if abs(date_center - row_center) <= max(row_height, date_height) * 1.2:
                    related_rows.append(line)
            row_left = min(line.box[0] for line in related_rows if line.box)
            row_top = min(line.box[1] for line in related_rows if line.box)
            row_right = max(line.box[2] for line in related_rows if line.box)
            row_bottom = max(line.box[3] for line in related_rows if line.box)
            pad_x = max(3, round(row_height * 0.35))
            pad_y = max(2, round(row_height * 0.30))
            crop = rgb[
                max(0, int(row_top - pad_y)):min(height, int(row_bottom + pad_y)),
                max(0, int(row_left - pad_x)):min(width, int(row_right + pad_x)),
            ]
        elif recog_sub_cd == "1401":
            # Foreign resident cards print the explicit issue date on its own
            # row above the issuing office. Use that detected row instead of
            # the whole lower card band, which can include patterned security
            # print that turns 08 into 10 during OCR.
            date_rows = [
                line for line in source_lines
                if line.box is not None and _dates_in_text(line.text)
                and line.box[1] >= height * 0.65
            ]
            if date_rows:
                date_row = max(date_rows, key=lambda line: line.box[1])
                left, top, right, bottom = date_row.box
                row_height = max(1.0, bottom - top)
                pad_x = max(8, round(row_height * 0.5))
                pad_y = max(5, round(row_height * 0.3))
                crop = rgb[
                    max(0, int(top - pad_y)):min(height, int(bottom + pad_y)),
                    max(0, int(left - pad_x)):min(width, int(right + pad_x)),
                ]
                date_row_crop = True
        if crop.size == 0:
            return {}
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        contrast = cv2.createCLAHE(
            clipLimit=2.0, tileGridSize=(8, 8)
        ).apply(gray)
        if date_row_crop:
            # Fine vertical security lines on foreign cards confuse digit
            # recognition. Median filtering suppresses that pattern while
            # retaining the printed digit strokes.
            variants = [
                cv2.medianBlur(gray, 3),
                cv2.medianBlur(gray, 5),
                cv2.medianBlur(contrast, 3),
            ]
        else:
            variants = [
                crop,
                contrast,
                cv2.threshold(
                    contrast, 0, 255,
                    cv2.THRESH_BINARY + cv2.THRESH_OTSU,
                )[1],
            ]
        variants = [
            cv2.resize(variant, None, fx=5 if date_row_crop else 4,
                       fy=5 if date_row_crop else 4,
                       interpolation=cv2.INTER_CUBIC)
            for variant in variants
        ]
        with TemporaryDirectory(prefix="new_work_issuer_") as temp_dir:
            engine = self._get_number_recognizer()
            for index, variant in enumerate(variants):
                crop_path = Path(temp_dir) / f"issuer_{index}.png"
                if variant.ndim == 3:
                    variant = cv2.cvtColor(variant, cv2.COLOR_RGB2BGR)
                if not cv2.imwrite(str(crop_path), variant):
                    continue
                focused_lines = self._read_lines(
                    engine.predict(input=str(crop_path))
                )
                fields = extract_identity_fields(
                    tuple(
                        OcrLine(line.text, line.confidence, None, "issuer_focus")
                        for line in focused_lines
                    ),
                    id_type_code=recog_sub_cd,
                )
                for field in field_votes:
                    if fields.get(field):
                        field_votes[field].append(fields[field])

        selected: dict[str, str] = {}
        for field, candidates in field_votes.items():
            if not candidates:
                continue
            counts = Counter(candidates)
            value, count = counts.most_common(1)[0]
            # A single focused read can supply a missing value; when reads
            # disagree, require a majority rather than taking a lucky parse.
            if count >= 2 or len(candidates) == 1:
                selected[field] = value
                if field == "isueDt" and count >= 2:
                    selected["_isueDt_consensus"] = "1"
        return selected

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

        # Small print on card photos can disappear when the detector scales a
        # full-resolution image down to its fast default input size. Only then
        # run the same bundled OCR models on a larger detector canvas. Keep the
        # pass whose OCR actually extracts more useful fields; this adds no
        # latency to images already read well by the first pass.
        detail_used = False
        try:
            from new_work.ocr.field_extractor import (
                extract_identity_fields,
                is_valid_resident_number,
            )
            from new_work.ocr.id_types import detect_id_type

            primary_type = detect_id_type(lines)
            primary_fields = extract_identity_fields(lines, primary_type.code)
            def review_fields(code: str | None) -> tuple[str, ...]:
                fields = ("ctzCorpNum", "isueDt")
                if code != "1401":
                    fields = ("custNm", *fields)
                if code in {"1101", "1102", "1201"}:
                    fields = (*fields, "drvLicenIsueNum")
                if code == "1302":
                    fields = (*fields, "veteranNum")
                return fields

            required_fields = review_fields(primary_type.code)
            primary_number = "".join(
                char for char in primary_fields.get("ctzCorpNum", "")
                if char.isdecimal()
            )
            primary_license = "".join(
                char for char in primary_fields.get("drvLicenIsueNum", "")
                if char.isdecimal()
            )
            needs_numeric_retry = (
                primary_type.code in {"1001", "1101", "1102", "1201", "1301", "1302", "1303"}
                and bool(primary_number)
                and not is_valid_resident_number(primary_number)
            ) or (
                primary_type.code in {"1101", "1102", "1201"}
                and bool(primary_license)
                and len(primary_license) != 12
            )
            needs_low_confidence_retry = any(
                line.confidence is not None
                and line.confidence < 0.88
                and sum(char.isdecimal() for char in line.text) >= 6
                for line in lines
            )
            if (
                primary_type.code is None
                or any(not primary_fields.get(field) for field in required_fields)
                or needs_numeric_retry
                or needs_low_confidence_retry
            ):
                detail_outputs = self._get_detail_engine().predict(input=str(path))
                detail_lines = tuple(
                    OcrLine(line.text, line.confidence, line.box, "korean_detail")
                    for line in self._read_lines(detail_outputs)
                )
                detail_type = detect_id_type(detail_lines)
                detail_fields = extract_identity_fields(
                    detail_lines, detail_type.code
                )
                def quality_score(
                    fields: dict[str, str], code: str | None,
                    source_lines: tuple[OcrLine, ...],
                ) -> float:
                    score = float(
                        sum(bool(fields.get(field)) for field in review_fields(code))
                    )
                    number = "".join(
                        char for char in fields.get("ctzCorpNum", "")
                        if char.isdecimal()
                    )
                    if code in {"1001", "1101", "1102", "1201", "1301", "1302", "1303"}:
                        score += 2.0 if is_valid_resident_number(number) else -1.0
                    if code in {"1101", "1102", "1201"}:
                        license_digits = "".join(
                            char for char in fields.get("drvLicenIsueNum", "")
                            if char.isdecimal()
                        )
                        score += 1.0 if len(license_digits) == 12 else -0.5
                    numeric_confidence = [
                        line.confidence for line in source_lines
                        if line.confidence is not None
                        and any(char.isdecimal() for char in line.text)
                    ]
                    if numeric_confidence:
                        score += sum(numeric_confidence) / len(numeric_confidence)
                    return score

                primary_score = quality_score(
                    primary_fields, primary_type.code, lines
                )
                detail_score = quality_score(
                    detail_fields, detail_type.code, detail_lines
                )
                if (
                    detail_score > primary_score
                    or (primary_type.code is None and detail_type.code is not None)
                ):
                    lines = detail_lines
                    detail_used = True
        except Exception:
            # The high-detail pass is a recovery path. A failed optional pass
            # must not discard a usable result from the normal OCR pass.
            pass

        try:
            version = metadata.version("paddleocr")
        except metadata.PackageNotFoundError:
            version = "installed"
        return OcrResult(
            job_id=str(uuid4()),
            image_path=path,
            lines=lines,
            engine_name=(
                f"PaddleOCR {self.ocr_version} Korean Mobile"
                f"{' + high-detail retry' if detail_used else ''} [{OCR_BUILD_ID}]"
            ),
            engine_version=version,
            elapsed_seconds=perf_counter() - started,
        )

    def recognize_foreigner_reg_number(self, image_path: Path) -> str:
        """Re-read the printed number area at high resolution on alien cards."""
        number, _ = self.recognize_foreigner_reg_number_details(image_path)
        return number

    def recognize_foreigner_reg_number_details(
        self, image_path: Path, source_lines: tuple[OcrLine, ...] = ()
    ) -> tuple[str, str]:
        """Return the foreign registration number and its six-digit mask box."""
        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(image_path) as source:
            image = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
            height, width = image.shape[:2]
            number_row = _find_registration_number_row(source_lines)
            if number_row is not None and number_row.box is not None:
                row_left, row_top, row_right, row_bottom = number_row.box
                pad_x = max(8, round((row_right - row_left) * 0.045))
                pad_y = max(12, round((row_bottom - row_top) * 0.32))
                crop_left = max(0, int(row_left) - pad_x)
                crop_top = max(0, int(row_top) - pad_y)
                crop_right = min(width, int(row_right) + pad_x)
                crop_bottom = min(height, int(row_bottom) + pad_y)
            else:
                # Keep the historical crop only as a recognition fallback;
                # never use its broad bounds as a mask position.
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
                    # TextRecognition has no detector boxes. Use the linked
                    # full-card number-row detection and refine its bounds to
                    # the printed glyphs, independent of portrait placement.
                    position = (
                        _mask_position_for_number_row(image, number_row)
                        if number_row is not None
                        else ""
                    )
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


def _find_registration_number_row(
    lines: tuple[OcrLine, ...],
) -> OcrLine | None:
    """Pair a numeric OCR row with its nearby registration-number label."""
    candidates: list[tuple[float, OcrLine]] = []
    labels: list[OcrLine] = []
    for line in lines:
        if line.box is None:
            continue
        normalized = re.sub(r"\s+", "", line.text).casefold()
        if any(word in normalized for word in ("등록", "registration", "번의", "번호")):
            labels.append(line)
        digits = "".join(char for char in line.text if char.isdigit())
        if len(digits) < 5:
            continue
        left, top, right, bottom = line.box
        if right - left < (bottom - top) * 2.5:
            continue
        candidates.append((len(digits), line))

    if not candidates:
        return None

    linked: list[tuple[float, OcrLine]] = []
    for digit_count, candidate in candidates:
        assert candidate.box is not None
        left, top, right, bottom = candidate.box
        for label in labels:
            assert label.box is not None
            label_left, label_top, label_right, label_bottom = label.box
            # A registration label is normally immediately left of the
            # number and on the same row, even when the card layout changes.
            horizontal_gap = max(0.0, left - label_right)
            vertical_gap = max(0.0, top - label_bottom, label_top - bottom)
            if label_left > left or horizontal_gap > (right - left) * 0.18:
                continue
            if vertical_gap > (bottom - top) * 0.45:
                continue
            score = digit_count * 10 - horizontal_gap - vertical_gap
            linked.append((score, candidate))

    if not linked:
        return None
    linked.sort(key=lambda item: item[0], reverse=True)
    if len(linked) > 1 and abs(linked[0][0] - linked[1][0]) < 0.01:
        return None
    return linked[0][1]


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
