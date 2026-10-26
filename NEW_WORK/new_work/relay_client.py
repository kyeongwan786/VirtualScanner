from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import quote

from new_work.transfer import TransferError


MAX_PAGE_BYTES = 40 * 1024 * 1024
MAX_PAGES = 100


class DeliveryPendingError(TransferError):
    def __init__(self, job_id: str, message: str):
        super().__init__(message)
        self.job_id = job_id


def _request(url: str, token: str, *, method: str = "GET",
             body: bytes | None = None, content_type: str | None = None,
             response_limit: int = 64 * 1024) -> tuple[int, dict | bytes]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(response_limit + 1)
            if len(data) > response_limit:
                raise TransferError("Relay 응답 크기 제한을 초과했습니다.")
            if response.headers.get_content_type() == "application/json":
                return response.status, json.loads(data.decode("utf-8")) if data else {}
            return response.status, data
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(64 * 1024).decode("utf-8", errors="replace")
        except OSError:
            detail = ""
        raise TransferError(f"Relay HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise TransferError(f"Relay 연결 실패: {exc}") from exc


def _package_metadata(archive: zipfile.ZipFile, station_id: str) -> tuple[dict, list[str]]:
    try:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferError(f"작업 패키지 manifest를 읽을 수 없습니다: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("station_id") != station_id:
        raise TransferError("작업 패키지의 접수 자리 ID가 선택한 PC와 다릅니다.")

    identity_path = manifest.get("identity_image")
    documents = manifest.get("documents")
    if not isinstance(identity_path, str) or not isinstance(documents, list):
        raise TransferError("작업 패키지에 신분증 이미지 또는 서류 목록이 없습니다.")
    page_paths = [identity_path]
    document_entries = []
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("pages"), list):
            raise TransferError("PDF 페이지 목록 형식이 올바르지 않습니다.")
        numbers = []
        for relative in document["pages"]:
            if not isinstance(relative, str):
                raise TransferError("PDF 페이지 경로 형식이 올바르지 않습니다.")
            safe = PurePosixPath(relative)
            if safe.is_absolute() or any(part in {"", ".", ".."} for part in safe.parts):
                raise TransferError("PDF 페이지 경로가 허용 범위를 벗어났습니다.")
            page_paths.append(relative)
            numbers.append(len(page_paths))
        document_entries.append({
            "document_id": str(document.get("document_id", "")),
            "label": str(document.get("label", "서류"))[:255],
            "source_name": str(document.get("source_name", ""))[:255],
            "order": len(document_entries) + 1,
            "page_count": len(numbers),
            "page_numbers": numbers,
        })
    if len(page_paths) > MAX_PAGES:
        raise TransferError(f"신분증을 포함한 총 이미지가 {MAX_PAGES}장을 초과했습니다.")
    for path in page_paths:
        try:
            info = archive.getinfo(path)
        except KeyError as exc:
            raise TransferError(f"패키지 이미지가 없습니다: {path}") from exc
        if info.file_size > MAX_PAGE_BYTES:
            raise TransferError(f"이미지가 Relay의 페이지 제한 40 MiB를 초과했습니다: {path}")

    work_package = {
        "schema_version": 1,
        "carrier": manifest.get("carrier"),
        "id_type_code": manifest.get("id_type_code"),
        "id_type_detection": manifest.get("id_type_detection", {}),
        "reviewed_values": manifest.get("reviewed_values", {}),
        "identity_page": 1,
        "identity_filename": PurePosixPath(identity_path).name,
        "documents": document_entries,
    }
    return work_package, page_paths


def send_work_package(
    relay_config: dict[str, str],
    station_id: str,
    package_path,
    progress: Callable[[str], None] | None = None,
    *,
    wait_seconds: int = 300,
) -> str:
    """Upload the ZIP's JPEG pages to the existing encrypted relay protocol."""
    relay_url = relay_config["relay_url"].rstrip("/")
    token = relay_config["central_token"]
    report = progress or (lambda _message: None)
    health_status, health = _request(f"{relay_url}/health", token)
    if (health_status != 200 or not isinstance(health, dict)
            or "new-work-v1" not in health.get("features", [])):
        raise TransferError(
            "Render Relay가 NEW_WORK 패키지 API를 지원하지 않습니다. "
            "저장소의 KyeongwanReceiver/relay_server.py 변경을 Render에 배포한 뒤 다시 시도하세요."
        )
    with zipfile.ZipFile(package_path, "r") as archive:
        work_package, page_paths = _package_metadata(archive, station_id)
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        job_id = str(manifest["job_id"])
        source_name = f"{work_package['carrier']} {len(work_package['documents'])}개 서류"
        metadata = {
            "job_id": job_id,
            "station_id": station_id,
            "page_count": len(page_paths),
            "dpi": 175,
            "source_name": source_name,
            "payload_type": "new-work-v1",
            "work_package": work_package,
        }
        created = False
        completion_started = False
        try:
            status, result = _request(
                f"{relay_url}/v1/jobs", token, method="POST",
                body=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
                content_type="application/json",
            )
            if status != 201:
                raise TransferError(f"Relay가 작업을 만들지 못했습니다: {result}")
            created = True
            for number, name in enumerate(page_paths, start=1):
                content = archive.read(name)
                if (not content.startswith(b"\xff\xd8\xff")
                        or not content.endswith(b"\xff\xd9")):
                    raise TransferError(f"JPEG 이미지 형식이 올바르지 않습니다: {name}")
                status, result = _request(
                    f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/pages/{number}",
                    token, method="PUT", body=content, content_type="image/jpeg",
                )
                if status not in (200, 201):
                    raise TransferError(f"Relay가 {number}페이지를 받지 못했습니다: {result}")
                report(f"Relay 전송 중 · {number}/{len(page_paths)}장")

            completion_started = True
            status, result = _request(
                f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/complete",
                token, method="POST", body=b"", content_type="application/json",
            )
            if status not in (200, 201):
                raise TransferError(f"Relay가 작업 완료를 확인하지 못했습니다: {result}")
        except Exception as exc:
            if completion_started:
                raise DeliveryPendingError(
                    job_id,
                    f"Relay 작업 완료 요청 결과를 확인하지 못했습니다. 중복 전송하지 말고 "
                    f"Receiver와 Render 상태를 확인하세요. 작업 ID: {job_id}",
                ) from exc
            if created:
                try:
                    _request(f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/cancel",
                             token, method="DELETE")
                except TransferError:
                    pass
            raise

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                status, result = _request(
                    f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/status", token,
                )
            except TransferError as exc:
                raise DeliveryPendingError(
                    job_id,
                    f"작업 {job_id}은 Relay에 접수됐지만 완료 상태를 확인할 수 없습니다. "
                    "중복 전송하지 말고 Receiver와 Render 상태를 확인하세요.",
                ) from exc
            if result.get("status") == "delivered":
                return job_id
            report("Receiver 적용 대기 중…")
            time.sleep(2)
    raise DeliveryPendingError(
        job_id,
        f"Relay에 전송했지만 Receiver 적용 확인을 {wait_seconds}초 안에 받지 못했습니다. "
        "중복 전송하지 말고 접수 PC Receiver 상태를 확인하세요. "
        f"작업 ID: {job_id}"
    )


def get_work_package_status(relay_config: dict[str, str], job_id: str) -> str:
    relay_url = relay_config["relay_url"].rstrip("/")
    _, result = _request(
        f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/status",
        relay_config["central_token"],
    )
    status = result.get("status") if isinstance(result, dict) else None
    if status not in {"uploading", "queued", "delivered", "cancelled"}:
        raise TransferError("Relay가 알 수 없는 작업 상태를 돌려줬습니다.")
    return status


def receive_work_package(relay_config: dict[str, str], station_id: str,
                         work_path: Path) -> tuple[Path, dict] | None:
    """Pull one assigned package, stage all JPEGs locally, and return its manifest."""
    relay_url = relay_config["relay_url"].rstrip("/")
    token = relay_config["token"]
    station = quote(station_id, safe="")
    status, response = _request(
        f"{relay_url}/v1/stations/{station}/jobs/next?payload_type=new-work-v1", token,
        response_limit=2 * 1024 * 1024,
    )
    if status == 204:
        return None
    if status != 200 or not isinstance(response, dict):
        raise TransferError(f"Relay가 올바른 작업 응답을 주지 않았습니다: {response}")
    if response.get("payload_type") != "new-work-v1":
        raise TransferError("이 작업은 NEW_WORK Receiver용 패키지가 아닙니다.")
    job_id = response.get("job_id")
    page_count = response.get("page_count")
    package = response.get("work_package")
    if (not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id)
            or not isinstance(page_count, int) or isinstance(page_count, bool)
            or not 1 <= page_count <= MAX_PAGES or not isinstance(package, dict)):
        raise TransferError("Relay 작업 정보가 올바르지 않습니다.")
    if package.get("carrier") not in {"SK", "KT"}:
        raise TransferError("지원하지 않는 포탈 작업입니다.")
    values = package.get("reviewed_values")
    documents = package.get("documents")
    detection = package.get("id_type_detection")
    identity_page = package.get("identity_page")
    if (not isinstance(values, dict) or not isinstance(documents, list)
            or not isinstance(detection, dict) or identity_page != 1):
        raise TransferError("Relay OCR 또는 서류 메타데이터가 올바르지 않습니다.")

    job_root = Path(work_path).expanduser() / "jobs"
    job_dir = job_root / job_id
    staging = job_root / f".{job_id}.incoming"
    if job_dir.exists():
        try:
            saved = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileExistsError(f"Receiver 작업 폴더가 올바르지 않습니다: {job_dir}") from exc
        if saved.get("station_id") != station_id or saved.get("job_id") != job_id:
            raise FileExistsError(f"기존 Receiver 작업이 다른 자리/작업입니다: {job_dir}")
        return job_dir, saved

    job_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    identity_name = package.get("identity_filename", "identity.jpg")
    if not isinstance(identity_name, str) or Path(identity_name).suffix.casefold() not in {".jpg", ".jpeg"}:
        raise TransferError("신분증 이미지 파일명이 올바르지 않습니다.")
    identity_relative = f"identity/identity{Path(identity_name).suffix.lower()}"
    page_map: dict[int, Path] = {1: Path(identity_relative)}
    normalized_documents: list[dict] = []
    seen_page_numbers = {1}
    for index, document in enumerate(documents, start=1):
        if not isinstance(document, dict):
            raise TransferError("서류 항목 형식이 올바르지 않습니다.")
        document_id = str(document.get("document_id", index))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", document_id):
            raise TransferError("서류 작업 ID 형식이 올바르지 않습니다.")
        numbers = document.get("page_numbers")
        if not isinstance(numbers, list) or not numbers:
            raise TransferError("서류 페이지 번호가 비어 있습니다.")
        relative_pages = []
        for page_index, raw_number in enumerate(numbers, start=1):
            if (not isinstance(raw_number, int) or isinstance(raw_number, bool)
                    or not 2 <= raw_number <= page_count or raw_number in seen_page_numbers):
                raise TransferError("Relay 서류 페이지 번호가 중복되거나 범위를 벗어났습니다.")
            seen_page_numbers.add(raw_number)
            relative = Path("documents") / document_id / f"{page_index:03d}.jpg"
            page_map[raw_number] = relative
            relative_pages.append(relative.as_posix())
        normalized_documents.append({
            "document_id": document_id,
            "label": str(document.get("label", "서류"))[:255],
            "source_name": str(document.get("source_name", ""))[:255],
            "order": index,
            "page_count": len(relative_pages),
            "pages": relative_pages,
        })
    if set(page_map) != set(range(1, page_count + 1)):
        raise TransferError("Relay 작업의 이미지 페이지 구성이 완전하지 않습니다.")

    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "station_id": station_id,
        "carrier": package["carrier"],
        "id_type_code": package.get("id_type_code"),
        "id_type_detection": detection,
        "reviewed_values": values,
        "identity_image": identity_relative,
        "documents": normalized_documents,
    }
    station_path = quote(station_id, safe="")
    for number in range(1, page_count + 1):
        page_url = (f"{relay_url}/v1/stations/{station_path}/jobs/"
                    f"{quote(job_id, safe='')}/pages/{number}")
        status, content = _request(page_url, token, response_limit=MAX_PAGE_BYTES)
        if status != 200 or not isinstance(content, bytes):
            raise TransferError(f"Relay에서 {number}페이지를 받지 못했습니다.")
        if not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9"):
            raise TransferError(f"Relay의 {number}페이지가 올바른 JPG가 아닙니다.")
        target = staging / page_map[number]
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".page-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        if number == 1:
            manifest["identity_sha256"] = hashlib.sha256(content).hexdigest()
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    try:
        os.replace(staging, job_dir)
    except OSError:
        if job_dir.exists():
            saved = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
            if saved.get("job_id") == job_id and saved.get("station_id") == station_id:
                return job_dir, saved
        raise
    return job_dir, manifest


def acknowledge_work_package(relay_config: dict[str, str], station_id: str,
                             job_id: str) -> None:
    relay_url = relay_config["relay_url"].rstrip("/")
    station = quote(station_id, safe="")
    _request(
        f"{relay_url}/v1/stations/{station}/jobs/{quote(job_id, safe='')}/ack",
        relay_config["token"], method="POST", body=b"",
        content_type="application/json",
    )
