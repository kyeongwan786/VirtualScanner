from __future__ import annotations

import subprocess
import sys
import importlib.metadata
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    import paddlex

    required_metadata = {name.casefold() for name in paddlex.utils.deps.BASE_DEP_SPECS}
    required_metadata.update(
        name.casefold()
        for name in getattr(paddlex.utils.deps, "EXTRAS", {}).get("ocr", {})
    )
    required_metadata.update({"paddleocr", "paddlex", "paddlepaddle"})
    installed = {
        distribution.metadata["Name"].casefold(): distribution.metadata["Name"]
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    missing = sorted(name for name in required_metadata if name not in installed)
    if missing:
        raise RuntimeError("OCR build dependency metadata is missing: " + ", ".join(missing))
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
        "--osx-bundle-identifier",
        "com.kyeongwan.newwork.receiver.v3",
        "--paths",
        str(root),
        "--collect-submodules",
        "new_work",
        "--hidden-import",
        "new_work.ocr.result_writer",
        "--hidden-import",
        "new_work.web_receiver",
        "--hidden-import",
        "new_work.gui.app",
        "--hidden-import",
        "new_work.ocr.adapter",
        "--hidden-import",
        "pymupdf",
        "--hidden-import",
        "pypdfium2",
        "--hidden-import",
        "paddleocr",
        "--hidden-import",
        "paddlex",
        "--collect-all",
        "webview",
        "--collect-all",
        "tkinterdnd2",
        "--add-data",
        str(Path(__file__).resolve().parent / "new_work" / "receiver_ui.html") + ":new_work",
        "--collect-all",
        "cv2",
        "--collect-all",
        "PIL",
        "--collect-all",
        "paddleocr",
        "--collect-all",
        "paddlex",
        "--collect-all",
        "pymupdf",
        "--collect-all",
        "pypdfium2",
        "receiver_run.py",
    ]
    command.extend(("--collect-binaries", "paddle"))
    for model_name in (
        "PP-OCRv5_mobile_det", "korean_PP-OCRv5_mobile_rec", "en_PP-OCRv5_mobile_rec",
    ):
        model_dir = root / "ocr_models" / model_name
        for filename in ("inference.yml", "inference.pdiparams"):
            if not (model_dir / filename).is_file():
                raise RuntimeError(f"Required OCR model is missing: {model_dir / filename}")
        command.extend(("--add-data", f"{model_dir}:ocr_models/{model_name}"))
    for distribution in sorted(installed[name] for name in required_metadata):
        command.extend(("--copy-metadata", distribution))
    subprocess.run(command, check=True, cwd=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
