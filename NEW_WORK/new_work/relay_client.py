from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import tempfile
import time
import stat
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Event
from typing import Callable
from urllib.parse import quote

from new_work.transfer import TransferError


MAX_PAGE_BYTES = 40 * 1024 * 1024
MAX_PAGES = 100
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


def _trim_jpeg_zero_padding(path: Path) -> None:
    """Drop zero fill after JPEG EOI while preserving the encoded image bytes."""
    with Path(path).open("r+b") as image:
        image.seek(0, os.SEEK_END)
        size = image.tell()
        if size < 4:
            raise TransferError("Relay ZIP 이미지가 올바른 JPG가 아닙니다.")
        image.seek(0)
        if image.read(3) != b"\xff\xd8\xff":
            raise TransferError("Relay ZIP 이미지가 올바른 JPG가 아닙니다.")

        position = size
        last_nonzero = -1
        while position:
            start = max(0, position - 65536)
            image.seek(start)
            chunk = image.read(position - start)
            index = len(chunk) - 1
            while index >= 0 and chunk[index] == 0:
                index -= 1
            if index >= 0:
                last_nonzero = start + index
                break
            position = start

        if last_nonzero < 3:
            raise TransferError("Relay ZIP 이미지가 올바른 JPG가 아닙니다.")
        image.seek(last_nonzero - 1)
        if image.read(2) != b"\xff\xd9":
            raise TransferError("Relay ZIP 이미지가 올바른 JPG가 아닙니다.")
        if last_nonzero + 1 < size:
            image.truncate(last_nonzero + 1)


class DeliveryPendingError(TransferError):
    def __init__(self, job_id: str, message: str):
        super().__init__(message)
        self.job_id = job_id


class TransferCancelledError(TransferError):
    """Raised when the sender requests cancellation of a Relay job."""


def _request(url: str, token: str, *, method: str = "GET",
             body: bytes | None = None, content_type: str | None = None,
             response_limit: int = 64 * 1024,
             on_download_chunk: Callable[[int], None] | None = None) -> tuple[int, dict | bytes]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if on_download_chunk is None:
                data = response.read(response_limit + 1)
                if len(data) > response_limit:
                    raise TransferError("Relay 응답 크기 제한을 초과했습니다.")
            else:
                received = bytearray()
                while True:
                    block = response.read(min(256 * 1024, response_limit + 1 - len(received)))
                    if not block:
                        break
                    received.extend(block)
                    on_download_chunk(len(block))
                    if len(received) > response_limit:
                        raise TransferError("Relay 응답 크기 제한을 초과했습니다.")
                data = bytes(received)
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
    if identity_path is not None and not isinstance(identity_path, str):
        raise TransferError("작업 패키지 신분증 경로가 올바르지 않습니다.")
    if not isinstance(documents, list):
        raise TransferError("작업 패키지 서류 목록이 올바르지 않습니다.")
    page_paths = [identity_path] if identity_path is not None else []
    document_entries = []
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("pages"), list):
            raise TransferError("PDF 페이지 목록 형식이 올바르지 않습니다.")
        numbers = []
        if not document["pages"]:
            raise TransferError("PDF 변환 페이지가 비어 있습니다.")
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
    if not page_paths:
        raise TransferError("전송할 신분증 JPG 또는 PDF 페이지가 없습니다.")
    if len(page_paths) > MAX_PAGES:
        raise TransferError(f"신분증을 포함한 총 이미지가 {MAX_PAGES}장을 초과했습니다.")
    for path in page_paths:
        try:
            info = archive.getinfo(path)
        except KeyError as exc:
            raise TransferError(f"패키지 이미지가 없습니다: {path}") from exc
        if info.file_size > MAX_PAGE_BYTES:
            raise TransferError(f"이미지가 Relay의 페이지 제한 40 MiB를 초과했습니다: {path}")
        if info.file_size < 4 or Path(path).suffix.casefold() not in {".jpg", ".jpeg"}:
            raise TransferError(f"패키지 페이지가 JPG가 아닙니다: {path}")
        with archive.open(path, "r") as image:
            start = image.read(3)
            image.seek(info.file_size - 2)
            end = image.read(2)
        if start != b"\xff\xd8\xff" or end != b"\xff\xd9":
            raise TransferError(f"패키지 이미지가 올바른 JPG가 아닙니다: {path}")

    work_package = {
        "schema_version": 1,
        "carrier": manifest.get("carrier"),
        "id_type_code": manifest.get("id_type_code"),
        "id_type_detection": manifest.get("id_type_detection", {}),
        "reviewed_values": manifest.get("reviewed_values", {}),
        "identity_page": 1 if identity_path is not None else None,
        "identity_filename": (
            PurePosixPath(identity_path).name if identity_path is not None else None
        ),
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
    cancel_event: Event | None = None,
) -> str:
    """Upload the complete work ZIP in bounded encrypted chunks."""
    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise TransferCancelledError("송신기에서 전송 취소를 요청했습니다.")

    def cancel_remote_job(job_id: str) -> str:
        try:
            _request(
                f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/cancel",
                token, method="DELETE",
            )
            return "cancelled"
        except TransferError as cancel_error:
            try:
                status = get_work_package_status(relay_config, job_id)
            except TransferError:
                raise TransferCancelledError(
                    "취소 요청을 보냈지만 Relay 상태를 확인하지 못했습니다. "
                    f"Receiver 작업을 확인하세요. 작업 ID: {job_id}"
                ) from cancel_error
            if status in {"delivered", "cancelled"}:
                return status
            raise TransferCancelledError(
                "취소 요청 결과를 확인하지 못했습니다. "
                f"Relay 상태: {status} · 작업 ID: {job_id}"
            ) from cancel_error

    check_cancelled()
    relay_url = relay_config["relay_url"].rstrip("/")
    token = relay_config["central_token"]
    report = progress or (lambda _message: None)
    health_status, health = _request(f"{relay_url}/health", token)
    check_cancelled()
    if (health_status != 200 or not isinstance(health, dict)
            or "new-work-zip-v1" not in health.get("features", [])):
        raise TransferError(
            "Render Relay가 NEW_WORK ZIP 전송 API를 지원하지 않습니다. "
            "저장소의 KyeongwanReceiver/relay_server.py 변경을 Render에 배포한 뒤 다시 시도하세요."
        )
    with zipfile.ZipFile(package_path, "r") as archive:
        work_package, page_paths = _package_metadata(archive, station_id)
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        job_id = str(manifest["job_id"])
        archive_path = Path(package_path)
        archive_size = archive_path.stat().st_size
        if not 1 <= archive_size <= MAX_ARCHIVE_BYTES:
            raise TransferError("작업 ZIP이 Relay의 512 MiB 제한을 초과했습니다.")
        archive_sha256 = hashlib.sha256()
        with archive_path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                check_cancelled()
                archive_sha256.update(chunk)
        archive_sha256_hex = archive_sha256.hexdigest()
        chunk_count = (archive_size + MAX_PAGE_BYTES - 1) // MAX_PAGE_BYTES
        source_name = f"{work_package['carrier']} {len(work_package['documents'])}개 서류"
        metadata = {
            "job_id": job_id,
            "station_id": station_id,
            "page_count": chunk_count,
            "dpi": 100,
            "source_name": source_name,
            "payload_type": "new-work-zip-v1",
            "work_package": work_package,
            "archive_size": archive_size,
            "archive_sha256": archive_sha256_hex,
        }
        created = False
        completion_started = False
        try:
            check_cancelled()
            status, result = _request(
                f"{relay_url}/v1/jobs", token, method="POST",
                body=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
                content_type="application/json",
            )
            if status != 201:
                raise TransferError(f"Relay가 작업을 만들지 못했습니다: {result}")
            created = True
            upload_digest = hashlib.sha256()
            upload_size = 0
            with archive_path.open("rb") as source:
                for number in range(1, chunk_count + 1):
                    check_cancelled()
                    content = source.read(MAX_PAGE_BYTES)
                    if not content:
                        raise TransferError("작업 ZIP 읽기가 중간에 끝났습니다.")
                    upload_digest.update(content)
                    upload_size += len(content)
                    status, result = _request(
                        f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/pages/{number}",
                        token, method="PUT", body=content,
                        content_type="application/octet-stream",
                    )
                    if status not in (200, 201):
                        raise TransferError(f"Relay가 ZIP 조각 {number}를 받지 못했습니다: {result}")
                    report(f"Relay ZIP 전송 중 · {number}/{chunk_count}")
                    check_cancelled()
            if upload_size != archive_size or upload_digest.hexdigest() != archive_sha256_hex:
                raise TransferError("전송 중 작업 ZIP이 변경되었습니다. Relay 작업을 취소합니다.")

            check_cancelled()
            completion_started = True
            status, result = _request(
                f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/complete",
                token, method="POST", body=b"", content_type="application/json",
            )
            if status not in (200, 201):
                raise TransferError(f"Relay가 작업 완료를 확인하지 못했습니다: {result}")
            check_cancelled()
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                if created:
                    remote_status = cancel_remote_job(job_id)
                    if remote_status == "delivered":
                        raise TransferCancelledError(
                            "취소 전에 Receiver 적용이 완료됐습니다. "
                            f"송신기 자료만 초기화합니다. 작업 ID: {job_id}"
                        ) from exc
                raise TransferCancelledError(
                    f"Relay 전송을 취소했습니다. 작업 ID: {job_id}"
                ) from exc
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
            if cancel_event is not None and cancel_event.is_set():
                remote_status = cancel_remote_job(job_id)
                if remote_status == "delivered":
                    raise TransferCancelledError(
                        "취소 전에 Receiver 적용이 완료됐습니다. "
                        f"송신기 자료만 초기화합니다. 작업 ID: {job_id}"
                    )
                raise TransferCancelledError(
                    f"Receiver 대기 중 Relay 작업을 취소했습니다. 작업 ID: {job_id}"
                )
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
            if cancel_event is not None:
                cancel_event.wait(1)
            else:
                time.sleep(1)
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


def cancel_work_package(relay_config: dict[str, str], job_id: str) -> str:
    """Cancel an uploading or queued Relay job and report its final state."""
    relay_url = relay_config["relay_url"].rstrip("/")
    try:
        _, result = _request(
            f"{relay_url}/v1/jobs/{quote(job_id, safe='')}/cancel",
            relay_config["central_token"], method="DELETE",
        )
        status = result.get("status") if isinstance(result, dict) else None
        if status == "cancelled":
            return status
    except TransferError:
        pass
    status = get_work_package_status(relay_config, job_id)
    if status in {"cancelled", "delivered"}:
        return status
    raise TransferError(
        f"Relay 작업을 취소하지 못했습니다. 현재 상태: {status} · 작업 ID: {job_id}"
    )


def _receive_work_archive(
    relay_config: dict[str, str],
    station_id: str,
    work_path: Path,
    *,
    job_id: str,
    chunk_count: int,
    archive_size: int,
    archive_sha256: str,
    work_package: dict,
    cancel_event: Event | None,
    progress: Callable[[int, int, str], None] | None,
) -> tuple[Path, dict]:
    relay_url = relay_config["relay_url"].rstrip("/")
    token = relay_config["token"]
    station = quote(station_id, safe="")
    job_root = Path(work_path).expanduser() / "jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    staging = job_root / f".{job_id}.incoming"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{job_id}.", suffix=".zip.partial", dir=job_root)
    temporary_archive = Path(temporary_name)

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            shutil.rmtree(staging, ignore_errors=True)
            temporary_archive.unlink(missing_ok=True)
            raise TransferError("자료 초기화 요청으로 Relay 수신을 취소했습니다.")

    digest = hashlib.sha256()
    downloaded = 0
    last_reported = 0
    last_report_time = time.monotonic()
    if progress:
        progress(0, archive_size, "bytes")
    try:
        with os.fdopen(fd, "wb") as output:
            for number in range(1, chunk_count + 1):
                check_cancelled()
                url = (f"{relay_url}/v1/stations/{station}/jobs/"
                       f"{quote(job_id, safe='')}/pages/{number}")
                def on_download_chunk(size: int) -> None:
                    nonlocal last_reported, last_report_time
                    now = time.monotonic()
                    current = downloaded + size_in_chunk[0] + size
                    size_in_chunk[0] += size
                    if (progress and (current == archive_size
                                      or current - last_reported >= 256 * 1024
                                      and now - last_report_time >= 0.2)):
                        progress(current, archive_size, "bytes")
                        last_reported = current
                        last_report_time = now

                size_in_chunk = [0]
                status, content = _request(
                    url, token, response_limit=MAX_PAGE_BYTES,
                    on_download_chunk=on_download_chunk,
                )
                check_cancelled()
                expected_length = min(MAX_PAGE_BYTES, archive_size - downloaded)
                if (status != 200 or not isinstance(content, bytes)
                        or len(content) != expected_length):
                    raise TransferError(f"Relay에서 ZIP 조각 {number}를 받지 못했습니다.")
                output.write(content)
                digest.update(content)
                downloaded += len(content)
                if progress and downloaded > last_reported:
                    progress(downloaded, archive_size, "bytes")
                    last_reported = downloaded
            output.flush()
            os.fsync(output.fileno())
        if downloaded != archive_size or digest.hexdigest() != archive_sha256:
            raise TransferError("Relay ZIP 크기 또는 SHA-256 검증에 실패했습니다.")
        check_cancelled()
        manifest = _extract_work_archive(
            temporary_archive,
            staging,
            job_id=job_id,
            station_id=station_id,
            work_package=work_package,
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        check_cancelled()
        job_dir = job_root / job_id
        try:
            os.replace(staging, job_dir)
        except OSError:
            if job_dir.exists():
                saved = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
                if saved.get("job_id") == job_id and saved.get("station_id") == station_id:
                    shutil.rmtree(staging, ignore_errors=True)
                    return job_dir, saved
            raise
        return job_dir, manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        temporary_archive.unlink(missing_ok=True)


def receive_work_package(relay_config: dict[str, str], station_id: str,
                         work_path: Path,
                         cancel_event: Event | None = None,
                         progress: Callable[[int, int, str], None] | None = None
                         ) -> tuple[Path, dict] | None:
    """Pull one assigned ZIP package, with compatibility for older page-wise jobs."""
    relay_url = relay_config["relay_url"].rstrip("/")
    token = relay_config["token"]
    station = quote(station_id, safe="")
    status, response = _request(
        f"{relay_url}/v1/stations/{station}/jobs/next?payload_type=new-work-zip-v1", token,
        response_limit=2 * 1024 * 1024,
    )
    if status == 204:
        status, response = _request(
            f"{relay_url}/v1/stations/{station}/jobs/next?payload_type=new-work-v1", token,
            response_limit=2 * 1024 * 1024,
        )
        if status == 204:
            return None
    if status != 200 or not isinstance(response, dict):
        raise TransferError(f"Relay가 올바른 작업 응답을 주지 않았습니다: {response}")
    payload_type = response.get("payload_type")
    if payload_type not in {"new-work-v1", "new-work-zip-v1"}:
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
            or not isinstance(detection, dict)
            or (identity_page is not None and (
                not isinstance(identity_page, int)
                or isinstance(identity_page, bool)
                or identity_page != 1
            ))
            or (identity_page is None and not documents)):
        raise TransferError("Relay OCR 또는 서류 메타데이터가 올바르지 않습니다.")

    job_root = Path(work_path).expanduser() / "jobs"
    job_dir = job_root / job_id
    staging = job_root / f".{job_id}.incoming"

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            shutil.rmtree(staging, ignore_errors=True)
            raise TransferError("자료 초기화 요청으로 Relay 수신을 취소했습니다.")

    check_cancelled()
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
    if payload_type == "new-work-zip-v1":
        archive_size = response.get("archive_size")
        archive_sha256 = response.get("archive_sha256")
        if (not isinstance(archive_size, int) or isinstance(archive_size, bool)
                or not 1 <= archive_size <= MAX_ARCHIVE_BYTES
                or not isinstance(archive_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", archive_sha256)
                or page_count != (archive_size + MAX_PAGE_BYTES - 1) // MAX_PAGE_BYTES):
            raise TransferError("Relay ZIP 크기, SHA-256 또는 조각 수가 올바르지 않습니다.")
        return _receive_work_archive(
            relay_config, station_id, work_path,
            job_id=job_id,
            chunk_count=page_count,
            archive_size=archive_size,
            archive_sha256=archive_sha256,
            work_package=package,
            cancel_event=cancel_event,
            progress=progress,
        )
    identity_name = package.get("identity_filename")
    identity_relative: str | None = None
    page_map: dict[int, Path] = {}
    next_document_page = 1
    if identity_page == 1:
        if (not isinstance(identity_name, str)
                or Path(identity_name).suffix.casefold() not in {".jpg", ".jpeg"}):
            raise TransferError("신분증 이미지 파일명이 올바르지 않습니다.")
        identity_relative = f"identity/identity{Path(identity_name).suffix.lower()}"
        page_map[1] = Path(identity_relative)
        next_document_page = 2
    normalized_documents: list[dict] = []
    seen_page_numbers = {1} if identity_page == 1 else set()
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
                    or not next_document_page <= raw_number <= page_count
                    or raw_number in seen_page_numbers):
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
    if progress:
        progress(0, page_count, "pages")
    for number in range(1, page_count + 1):
        check_cancelled()
        page_url = (f"{relay_url}/v1/stations/{station_path}/jobs/"
                    f"{quote(job_id, safe='')}/pages/{number}")
        status, content = _request(page_url, token, response_limit=MAX_PAGE_BYTES)
        check_cancelled()
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
        if number == 1 and identity_relative is not None:
            manifest["identity_sha256"] = hashlib.sha256(content).hexdigest()
        if progress:
            progress(number, page_count, "pages")
    check_cancelled()
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    check_cancelled()
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


def _extract_work_archive(
    archive_path: Path,
    staging: Path,
    *,
    job_id: str,
    station_id: str,
    work_package: dict,
) -> dict:
    """Validate and safely unpack one complete central work-package ZIP."""
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise TransferError(f"Relay ZIP을 열 수 없습니다: {exc}") from exc

    with archive:
        infos = archive.infolist()
        if not 1 <= len(infos) <= 1000:
            raise TransferError("Relay ZIP 파일 수가 허용 범위를 벗어났습니다.")
        names: set[str] = set()
        total_uncompressed = 0
        for info in infos:
            path = PurePosixPath(info.filename)
            raw_name = info.filename[:-1] if info.is_dir() and info.filename.endswith("/") else info.filename
            if (not info.filename or "\\" in info.filename
                    or path.as_posix() != raw_name
                    or path.is_absolute() or PureWindowsPath(info.filename).is_absolute()
                    or PureWindowsPath(info.filename).drive
                    or any(part in {"", ".", ".."} for part in path.parts)):
                raise TransferError("Relay ZIP에 허용되지 않는 경로가 있습니다.")
            normalized = path.as_posix()
            if normalized in names:
                raise TransferError("Relay ZIP에 중복된 파일 경로가 있습니다.")
            names.add(normalized)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or info.flag_bits & 0x1:
                raise TransferError("Relay ZIP에 바로가기 또는 암호화된 항목이 있습니다.")
            if not info.is_dir():
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ARCHIVE_BYTES:
                    raise TransferError("Relay ZIP 압축 해제 크기가 512 MiB를 초과합니다.")

        if "manifest.json" not in names:
            raise TransferError("Relay ZIP에 manifest.json이 없습니다.")
        try:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
            raise TransferError(f"Relay ZIP manifest를 읽을 수 없습니다: {exc}") from exc
        if (not isinstance(manifest, dict) or manifest.get("job_id") != job_id
                or manifest.get("station_id") != station_id
                or manifest.get("carrier") != work_package.get("carrier")
                or manifest.get("carrier") not in {"SK", "KT"}
                or not isinstance(manifest.get("reviewed_values"), dict)
                or not isinstance(manifest.get("id_type_detection"), dict)
                or not isinstance(manifest.get("documents"), list)):
            raise TransferError("Relay ZIP manifest가 작업 메타데이터와 일치하지 않습니다.")

        expected_images: list[str] = []
        identity = manifest.get("identity_image")
        if identity is not None:
            if not isinstance(identity, str):
                raise TransferError("Relay ZIP 신분증 이미지 경로가 올바르지 않습니다.")
            expected_images.append(identity)
        if work_package.get("identity_page") != (1 if identity is not None else None):
            raise TransferError("Relay ZIP 신분증 페이지 메타데이터가 일치하지 않습니다.")
        if work_package.get("identity_filename") != (
            PurePosixPath(identity).name if identity is not None else None
        ):
            raise TransferError("Relay ZIP 신분증 파일명 메타데이터가 일치하지 않습니다.")
        for document in manifest["documents"]:
            if (not isinstance(document, dict)
                    or not isinstance(document.get("pages"), list)
                    or not document["pages"]):
                raise TransferError("Relay ZIP 서류 목록 형식이 올바르지 않습니다.")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(document.get("document_id", ""))):
                raise TransferError("Relay ZIP 서류 작업 ID가 올바르지 않습니다.")
            for relative in document["pages"]:
                if not isinstance(relative, str):
                    raise TransferError("Relay ZIP 서류 페이지 경로가 올바르지 않습니다.")
                expected_images.append(relative)
        if not expected_images or len(expected_images) > MAX_PAGES:
            raise TransferError(f"Relay ZIP 이미지 수가 {MAX_PAGES}장을 초과했습니다.")
        if len(set(expected_images)) != len(expected_images):
            raise TransferError("Relay ZIP manifest에서 이미지 경로가 중복되었습니다.")
        metadata_documents = work_package.get("documents")
        if (manifest.get("reviewed_values") != work_package.get("reviewed_values")
                or manifest.get("id_type_detection") != work_package.get("id_type_detection")
                or not isinstance(metadata_documents, list)
                or len(metadata_documents) != len(manifest["documents"])):
            raise TransferError("Relay ZIP 검토값이 작업 메타데이터와 일치하지 않습니다.")
        for archive_document, relay_document in zip(manifest["documents"], metadata_documents):
            if (archive_document.get("document_id") != relay_document.get("document_id")
                    or len(archive_document.get("pages", [])) != relay_document.get("page_count")):
                raise TransferError("Relay ZIP 서류 순서가 작업 메타데이터와 일치하지 않습니다.")

        expected_files = {"manifest.json", *expected_images}
        report_path = manifest.get("sk_report")
        if report_path is not None:
            if not isinstance(report_path, str):
                raise TransferError("Relay ZIP SK 보고서 경로가 올바르지 않습니다.")
            report_relative = PurePosixPath(report_path)
            if (report_relative.is_absolute()
                    or any(part in {"", ".", ".."} for part in report_relative.parts)):
                raise TransferError("Relay ZIP SK 보고서 경로가 허용 범위를 벗어났습니다.")
            expected_files.add(report_relative.as_posix())
        for relative in expected_images:
            safe = PurePosixPath(relative)
            if (safe.is_absolute() or PureWindowsPath(relative).is_absolute()
                    or PureWindowsPath(relative).drive
                    or any(part in {"", ".", ".."} for part in safe.parts)
                    or safe.as_posix() not in names
                    or safe.suffix.casefold() not in {".jpg", ".jpeg"}):
                raise TransferError("Relay ZIP 이미지 경로가 없거나 허용 범위를 벗어났습니다.")
            info = archive.getinfo(safe.as_posix())
            if info.file_size > MAX_PAGE_BYTES or info.file_size < 4:
                raise TransferError("Relay ZIP 이미지 크기가 허용 범위를 벗어났습니다.")
        actual_files = {info.filename for info in infos if not info.is_dir()}
        if actual_files != expected_files:
            raise TransferError("Relay ZIP에 manifest에서 참조하지 않는 파일이 있습니다.")

        for info in infos:
            path = PurePosixPath(info.filename)
            target = staging.joinpath(*path.parts)
            if not target.resolve().is_relative_to(staging.resolve()):
                raise TransferError("Relay ZIP 경로가 Receiver 작업 폴더를 벗어났습니다.")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info, "r") as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                raise TransferError(f"Relay ZIP 압축 해제에 실패했습니다: {exc}") from exc

        for relative in expected_images:
            image_path = staging.joinpath(*PurePosixPath(relative).parts)
            _trim_jpeg_zero_padding(image_path)
            with image_path.open("rb") as image:
                image.seek(-2, os.SEEK_END)
                if image.read(2) != b"\xff\xd9":
                    raise TransferError(f"Relay ZIP 이미지가 올바른 JPG가 아닙니다: {relative}")
        if identity is not None:
            identity_digest = hashlib.sha256()
            with staging.joinpath(*PurePosixPath(identity).parts).open("rb") as image:
                while chunk := image.read(1024 * 1024):
                    identity_digest.update(chunk)
            manifest["identity_sha256"] = identity_digest.hexdigest()
        return manifest
