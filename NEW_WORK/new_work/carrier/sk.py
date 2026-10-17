from __future__ import annotations

from pathlib import Path

from new_work.carrier.sk_report import render_sk_report
from new_work.domain import ReviewDraft
from new_work.image.legacy_crop import find_latest_id_crop


def create_sk_report(
    draft: ReviewDraft,
    output_path: Path,
) -> Path:
    if not draft.reviewed:
        raise ValueError("검토 완료한 OCR 값을 먼저 확정하세요.")
    full_path = draft.result.image_path.expanduser().resolve(strict=True)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_directory = output_path.parent / "data"
    data_directory.mkdir(exist_ok=True)
    # Reproduce the legacy newest-*m.jpg finder in a job-local directory.
    selected_input = data_directory / f"{draft.result.job_id}_m{full_path.suffix.lower()}"
    selected_input.write_bytes(full_path.read_bytes())
    crop_path = find_latest_id_crop(
        data_directory,
        draft.reviewed_values.get("kaitIdType", ""),
    )
    report = render_sk_report(
        draft.reviewed_values,
        full_path.read_bytes(),
        crop_path.read_bytes() if crop_path and crop_path.is_file() else None,
    )
    output_path.write_bytes(report)
    return output_path
