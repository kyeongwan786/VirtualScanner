from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def emergency_marker_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "NEW_WORK_EMERGENCY.lock"


def is_emergency_locked() -> bool:
    return emergency_marker_path().is_file()


def write_emergency_lock() -> None:
    marker = emergency_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"disabled": True}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _remove_file(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _remove_project_directory(path: Path) -> None:
    try:
        if path.is_symlink():
            return
        if path.is_dir():
            import shutil

            shutil.rmtree(path)
    except OSError:
        pass


def erase_sender_data() -> None:
    """Remove sender-side staged transfer payloads, preserving configuration."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    app_data = base / "NEW_WORK"
    _remove_project_directory(app_data / "jobs")
    override = os.environ.get("NEW_WORK_DATA_DIR")
    if override:
        override_dir = Path(override).expanduser()
        _remove_project_directory(override_dir / "jobs")


def erase_receiver_data(config: dict[str, str]) -> None:
    """Remove transferred packages and their staged scans, preserving business data."""
    write_emergency_lock()
    work_value = str(config.get("work_path", "")).strip()
    work_root = Path(work_value).expanduser() if work_value else None
    ready_ids: set[str] = set()
    if work_root is not None and work_root.name and work_root != Path(work_root.anchor):
        state_path = work_root / "receiver-state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            ready_jobs = state.get("ready_jobs", []) if isinstance(state, dict) else []
            if isinstance(ready_jobs, list):
                ready_ids.update(
                    job["scanner_job_id"] for job in ready_jobs
                    if isinstance(job, dict)
                    and isinstance(job.get("scanner_job_id"), str)
                    and job["scanner_job_id"].isascii()
                    and job["scanner_job_id"].replace("_", "").replace("-", "").isalnum()
                )
            active = state.get("active") if isinstance(state, dict) else None
            if (isinstance(active, dict)
                    and isinstance(active.get("scanner_job_id"), str)
                    and active["scanner_job_id"].isascii()
                    and active["scanner_job_id"].replace("_", "").replace("-", "").isalnum()):
                ready_ids.add(active["scanner_job_id"])
        except (OSError, json.JSONDecodeError):
            pass
        _remove_project_directory(work_root / "jobs")
        for name in ("receiver-state.json", "receiver-corrections.json"):
            _remove_file(work_root / name)

    ready_value = str(config.get("scanner_ready_path", "")).strip()
    ready = Path(ready_value).expanduser() if ready_value else None
    if (ready is not None and ready.name.casefold() == "ready"
            and not ready.is_symlink() and ready.is_dir()):
        try:
            for scanner_job_id in ready_ids:
                if scanner_job_id in {".", ".."}:
                    continue
                _remove_project_directory(ready / scanner_job_id)
        except OSError:
            pass


def unregister_new_work_autostart() -> None:
    if sys.platform != "win32":
        return
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            for name in (
                "NewWorkIdReview", "NewWorkIdReviewV1", "NewWorkIdReviewV2",
                "NewWorkIdReviewV3", "NewWorkReceiverV3",
            ):
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass


def schedule_windows_package_removal() -> None:
    """Delete only a frozen NEW_WORK onedir package after its process exits."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    executable = Path(sys.executable).resolve()
    if executable.name.casefold() not in {
        "new_work_v1.exe", "new_work_v3.exe", "new_work_receiver_v3.exe",
    }:
        return
    package_dir = executable.parent
    expected_package_dirs = {
        "new_work_v1", "new_work_v3", "new_work_receiver_v3",
    }
    if package_dir.name.casefold() not in expected_package_dirs:
        return
    temp_dir = Path(os.environ.get("TEMP", Path.home() / "AppData/Local/Temp"))
    script = temp_dir / f"new_work_emergency_{os.getpid()}.ps1"
    target = str(package_dir).replace("'", "''")
    script.write_text(
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        f"$target = '{target}'\n"
        "$processNames = @('new_work_v1.exe', 'new_work_v3.exe', 'new_work_receiver_v3.exe')\n"
        "$targetPrefix = [IO.Path]::GetFullPath($target).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar\n"
        "$deadline = (Get-Date).AddMinutes(10)\n"
        "while ((Get-Date) -lt $deadline) {\n"
        "  $running = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {\n"
        "    $processNames -contains $_.Name -and $_.ExecutablePath -and\n"
        "    $_.ExecutablePath.StartsWith($targetPrefix, [StringComparison]::OrdinalIgnoreCase)\n"
        "  })\n"
        "  if ($running.Count -eq 0) {\n"
        "    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue\n"
        "    if (-not (Test-Path -LiteralPath $target)) { break }\n"
        "  }\n"
        "  Start-Sleep -Seconds 1\n"
        "}\n"
        "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n",
        encoding="utf-8",
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    except OSError:
        script.unlink(missing_ok=True)
