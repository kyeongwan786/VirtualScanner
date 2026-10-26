from __future__ import annotations

from pathlib import Path

from new_work.carrier.sk_report import render_sk_report
from new_work.domain import ReviewDraft
from new_work.image.face_crop import crop_id_portrait_jpeg


def create_sk_report(
    draft: ReviewDraft,
    output_path: Path,
) -> Path:
    if not draft.reviewed:
        raise ValueError("검토 완료한 OCR 값을 먼저 확정하세요.")
    full_path = draft.result.image_path.expanduser().resolve(strict=True)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    crop_bytes = crop_id_portrait_jpeg(
        full_path,
        recog_sub_cd=draft.reviewed_values.get("kaitIdType", ""),
    )
    report = render_sk_report(
        draft.reviewed_values,
        full_path.read_bytes(),
        crop_bytes,
    )
    output_path.write_bytes(report)
    return output_path
