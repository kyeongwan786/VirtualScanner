from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4


class TransferError(RuntimeError):
    pass


_LOG_LOCK = Lock()


@dataclass(frozen=True)
class TransferResult:
    path: Path
    size: int
    sha256: str
    atomic: bool


def append_transfer_log(
    log_path: Path,
    *,
    job_id: str,
    filename: str,
    status: str,
    size: int | None = None,
    sha256: str | None = None,
    error_type: str | None = None,
) -> None:
    """Append a privacy-minimal event; never record full paths or OCR content."""
    event = {
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_id": job_id,
        "filename": Path(filename).name,
        "status": status,
        "size": size,
        "sha256": sha256,
        "error_type": error_type,
    }
    target = Path(log_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _LOG_LOCK, target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())


def _digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _transfer_file_once(
    source: Path,
    destination: Path,
    *,
    overwrite: bool = False,
    job_id: str | None = None,
) -> TransferResult:
    """Copy one result through a sibling temp file, then verify its digest.

    Existing destinations are protected unless overwrite was explicitly
    selected. If hard-link publication is unavailable on a share, an exclusive
    create is used and the result is still verified before returning.
    """
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser()
    if not source.is_file():
        raise TransferError("전달 원본이 일반 파일이 아닙니다.")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TransferError(f"전달 대상 폴더를 준비하지 못했습니다: {exc}") from exc
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)

    source_size, source_hash = _digest(source)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent,
            prefix=f".new_work_{job_id or uuid4().hex}_", suffix=".partial",
            delete=False,
        ) as target, source.open("rb") as origin:
            temp_path = Path(target.name)
            shutil.copyfileobj(origin, target, 1024 * 1024)
            target.flush()
            os.fsync(target.fileno())

        temp_size, temp_hash = _digest(temp_path)
        if (temp_size, temp_hash) != (source_size, source_hash):
            raise TransferError("임시 전달 파일의 크기 또는 SHA-256이 원본과 다릅니다.")

        atomic = True
        if overwrite:
            os.replace(temp_path, destination)
            temp_path = None
        else:
            try:
                os.link(temp_path, destination)
                temp_path.unlink()
                temp_path = None
            except FileExistsError:
                raise
            except OSError:
                # Some SMB/NAS implementations do not support hard links.
                atomic = False
                try:
                    with temp_path.open("rb") as origin, destination.open("xb") as target:
                        shutil.copyfileobj(origin, target, 1024 * 1024)
                        target.flush()
                        os.fsync(target.fileno())
                except FileExistsError:
                    raise
                except OSError:
                    destination.unlink(missing_ok=True)
                    raise
                temp_path.unlink()
                temp_path = None

        result_size, result_hash = _digest(destination)
        if (result_size, result_hash) != (source_size, source_hash):
            destination.unlink(missing_ok=True)
            raise TransferError("전달 완료 후 대상 파일 검증에 실패했습니다.")
        return TransferResult(destination, result_size, result_hash, atomic)
    except FileExistsError:
        raise
    except OSError as exc:
        raise TransferError(f"파일 전달 실패: {exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def transfer_file(
    source: Path,
    destination: Path,
    *,
    overwrite: bool = False,
    job_id: str | None = None,
    attempts: int = 3,
) -> TransferResult:
    """Retry transient share failures; collision and overwrite rules stay explicit."""
    if attempts < 1:
        raise ValueError("전달 시도 횟수는 1 이상이어야 합니다.")
    for attempt in range(attempts):
        try:
            return _transfer_file_once(
                source, destination, overwrite=overwrite, job_id=job_id
            )
        except FileExistsError:
            raise
        except TransferError:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.2 * (attempt + 1))
    raise AssertionError("unreachable")
