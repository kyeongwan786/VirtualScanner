import os
from pathlib import Path
import shutil
import sys


def _install_bundled_models() -> None:
    if not getattr(sys, "frozen", False):
        return
    bundled = Path(sys._MEIPASS) / "bundled_models" / "official_models"
    if not bundled.is_dir():
        return

    cache_home = Path(os.environ.get("PADDLE_PDX_CACHE_HOME", Path.home() / ".paddlex"))
    target_root = cache_home / "official_models"
    for source in bundled.iterdir():
        target = target_root / source.name
        if not (target / "inference.pdiparams").is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True)
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"


_install_bundled_models()

from new_work.gui.app import main


if __name__ == "__main__":
    main()
