from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name",
        "new_work_receiver",
        "--paths",
        str(Path(__file__).resolve().parent),
        "--collect-submodules",
        "new_work",
        "--hidden-import",
        "new_work.ocr.result_writer",
        "--collect-all",
        "cv2",
        "--collect-all",
        "PIL",
        "receiver_run.py",
    ]
    subprocess.run(command, check=True, cwd=Path(__file__).resolve().parent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
