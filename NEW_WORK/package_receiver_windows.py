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
        "new_work_receiver_v3",
        "--paths",
        str(Path(__file__).resolve().parent),
        "--collect-submodules",
        "new_work",
        "--hidden-import",
        "new_work.ocr.result_writer",
        "--hidden-import",
        "new_work.web_receiver",
        "--collect-all",
        "webview",
        "--add-data",
        str(Path(__file__).resolve().parent / "new_work" / "receiver_ui.html") + ";new_work",
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
