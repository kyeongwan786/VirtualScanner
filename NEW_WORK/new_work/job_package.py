from __future__ import annotations

import json
import shutil
from threading import Event
from pathlib import Path
from typing import Mapping, Sequence

from new_work.domain import OcrResult
from new_work.ocr.id_types import IdTypeDetection

MAX_PACKAGE_FILES = 1000
MAX_PACKAGE_BYTES = 512 * 1024 * 1024


def _jpeg_payload(path: Path) -> bytes:
    """Return a JPEG without harmless zero padding after its EOI marker."""
    content = Path(path).read_bytes()
    if len(content) < 4 or not content.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"신분증 이미지가 올바른 JPG가 아닙니다: {path}")
    end_marker = content.rfind(b"\xff\xd9")
    if end_marker < 2 or any(content[end_marker + 2:]):
        raise ValueError(f"신분증 이미지가 올바른 JPG가 아닙니다: {path}")
    return content[:end_marker + 2]


def create_work_package(
    output_path: Path,
    *,
    job_id: str,
    station_id: str,
    carrier: str,
    result: OcrResult | None,
    detection: IdTypeDetection,
    reviewed_values: Mapping[str, str],
    document_jobs: Sequence[Mapping[str, object]],
    cancel_event: Event | None = None,
) -> Path:
    """Prepare a manifest/JPG directory for direct Relay page transfer."""
    def check_cancelled():
        if cancel_event is not None and cancel_event.is_set():
            from new_work.relay_client import TransferCancelledError
            raise TransferCancelledError("전송 자료 준비를 취소했습니다.")

    check_cancelled()
    if carrier not in {"SK", "KT"}:
        raise ValueError("포탈은 SK 또는 KT여야 합니다.")
    if not station_id:
        raise ValueError("대상 접수 PC가 지정되지 않았습니다.")
    if any(job.get("status") != "변환 완료" for job in document_jobs):
        raise ValueError("모든 서류 파일 준비가 끝난 뒤 전송할 수 있습니다.")
    if result is None and not document_jobs:
        raise ValueError("신분증 또는 PDF 서류가 하나 이상 있어야 합니다.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    package_root = output_path.parent / f"{output_path.stem}_payload"
    if output_path.exists() or package_root.exists():
        raise FileExistsError(output_path if output_path.exists() else package_root)
    package_root.mkdir(parents=True, exist_ok=False)
    identity_relative: str | None = None
    if result is not None:
        # OCR accepts PNG as an input, but the relay page contract is JPEG-only.
        identity_path = package_root / "identity" / "identity.jpg"
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        identity_path.write_bytes(_jpeg_payload(result.image_path))
        identity_relative = identity_path.relative_to(package_root).as_posix()

    document_entries: list[dict[str, object]] = []
    for index, item in enumerate(document_jobs, start=1):
        check_cancelled()
        source = Path(item["source"])
        source_directory = Path(item["output"])
        page_paths = sorted(source_directory.glob("*.jpg"))
        if not page_paths:
            raise ValueError(f"변환 결과 페이지가 없습니다: {source.name}")
        document_id = str(item["key"])
        relative_directory = Path("documents") / document_id
        copied_pages: list[str] = []
        for page_path in page_paths:
            check_cancelled()
            relative_page = relative_directory / page_path.name
            destination = package_root / relative_page
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(page_path, destination)
            copied_pages.append(relative_page.as_posix())
        document_entries.append({
            "document_id": document_id,
            "label": source.stem,
            "source_name": source.name,
            "order": index,
            "page_count": len(copied_pages),
            "pages": copied_pages,
        })

    check_cancelled()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "job_id": job_id,
        "station_id": station_id,
        "carrier": carrier,
        "id_type_code": str(reviewed_values.get("kaitIdType", "")),
        "id_type_detection": {
            "name": detection.name,
            "type_code": detection.type_code,
            "candidate_codes": list(detection.candidate_codes),
            "preferred_code": detection.preferred_code,
        },
        "reviewed_values": dict(reviewed_values),
        "identity_image": identity_relative,
        "documents": document_entries,
    }
    (package_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    members = [item for item in package_root.rglob("*") if item.is_file()]
    if len(members) > MAX_PACKAGE_FILES:
        raise ValueError(f"작업 패키지가 파일 수 제한 {MAX_PACKAGE_FILES}개를 초과합니다.")
    if sum(item.stat().st_size for item in members) > MAX_PACKAGE_BYTES:
        raise ValueError("작업 패키지가 512 MiB 제한을 초과합니다.")

    return package_root
