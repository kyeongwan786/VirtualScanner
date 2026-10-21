from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


REVIEW_TEXT_FIELDS = (
    "kaitIdType",
    "recogSubCd",
    "idType",
    "ctzCorpNum",
    "custNm",
    "isueDt",
    "drvLicenIsueNum",
    "isuePlc",
    "nationClCd",
    "addr",
    "mskPos",
)


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float | None = None
    box: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class OcrResult:
    """Raw OCR output. Values are kept apart from later human edits."""

    job_id: str
    image_path: Path
    values: dict[str, str] = field(default_factory=dict)
    lines: tuple[OcrLine, ...] = ()
    engine_name: str = ""
    engine_version: str = ""
    elapsed_seconds: float | None = None

    @classmethod
    def empty(cls, image_path: Path) -> "OcrResult":
        return cls(job_id=str(uuid4()), image_path=image_path)


@dataclass
class ReviewDraft:
    """An in-memory review copy; the original OCR strings remain unchanged."""

    result: OcrResult
    reviewed_values: dict[str, str]
    reviewed: bool = False

    @classmethod
    def from_result(cls, result: OcrResult) -> "ReviewDraft":
        raw = {key: result.values.get(key, "") for key in REVIEW_TEXT_FIELDS}
        return cls(result=result, reviewed_values=dict(raw))

    def mark_reviewed(self, values: dict[str, str]) -> None:
        self.reviewed_values = {
            key: str(values.get(key, "")) for key in REVIEW_TEXT_FIELDS
        }
        self.reviewed = True

    @property
    def changed_fields(self) -> tuple[str, ...]:
        return tuple(
            key
            for key in REVIEW_TEXT_FIELDS
            if self.result.values.get(key, "") != self.reviewed_values.get(key, "")
        )
