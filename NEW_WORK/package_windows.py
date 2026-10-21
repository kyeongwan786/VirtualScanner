from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import subprocess
import sys

import paddlex


def main() -> int:
    debug_console = os.environ.get("NEW_WORK_DEBUG", "").strip() == "1"
    required_metadata = {
        name.casefold()
        for name in paddlex.utils.deps.BASE_DEP_SPECS
    }
    required_metadata.update({"paddleocr", "paddlex", "paddlepaddle"})
    installed_distributions = {
        distribution.metadata["Name"].casefold(): distribution.metadata["Name"]
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    metadata_to_copy = sorted(
        installed_distributions[name]
        for name in required_metadata
        if name in installed_distributions
    )

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console" if debug_console else "--windowed",
        "--name",
        "new_work",
        "--collect-all",
        "paddleocr",
        "--collect-all",
        "paddlex",
        "--collect-all",
        "chardet",
        "--collect-binaries",
        "paddle",
        "--collect-data",
        "cv2",
    ]
    model_cache = Path.home() / ".paddlex" / "official_models"
    bundled_model_names = (
        "PP-OCRv5_mobile_det",
        "korean_PP-OCRv5_mobile_rec",
        "en_PP-OCRv5_mobile_rec",
    )
    bundled_models = [
        (name, model_cache / name)
        for name in bundled_model_names
        if (model_cache / name / "inference.pdiparams").is_file()
    ]
    if bundled_models:
        for name, source in bundled_models:
            command.extend(("--add-data", f"{source};bundled_models/official_models/{name}"))
        print("Bundling cached OCR models:", ", ".join(name for name, _ in bundled_models))
    else:
        print("No cached OCR models found; the app will download models on first run.")
    for distribution in metadata_to_copy:
        command.extend(("--copy-metadata", distribution))
    command.append("run.py")

    print("Including PaddleX dependency metadata:", ", ".join(metadata_to_copy))
    print("Building console diagnostic EXE." if debug_console else
          "Running PyInstaller with PaddleOCR runtime dependencies.")
    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
