from __future__ import annotations

import sys
from pathlib import Path


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "NewWorkIdReview"


def register_current_user_autostart(value_name: str = _RUN_VALUE_NAME) -> None:
    """Start this app when the Windows user who first launches it signs in."""
    if sys.platform != "win32":
        return

    import winreg

    if getattr(sys, "frozen", False):
        command = f'"{Path(sys.executable).resolve()}"'
    else:
        script = Path(sys.argv[0]).resolve()
        command = f'"{Path(sys.executable).resolve()}" "{script}"'
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)


def create_tray_icon(on_open, on_exit):
    """Create the small Windows notification-area icon for a hidden window."""
    import pystray
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 8, 60, 56), radius=9, fill="#18324A")
    draw.rounded_rectangle((10, 15, 54, 49), radius=5, fill="#F5F8FC")
    draw.rectangle((10, 15, 54, 24), fill="#31B7A4")
    draw.ellipse((17, 29, 29, 41), fill="#31B7A4")
    draw.rounded_rectangle((34, 29, 48, 32), radius=1, fill="#18324A")
    draw.rounded_rectangle((34, 36, 48, 39), radius=1, fill="#18324A")

    menu = pystray.Menu(
        pystray.MenuItem("열기", lambda _icon, _item: on_open(), default=True),
        pystray.MenuItem("종료", lambda _icon, _item: on_exit()),
    )
    return pystray.Icon("new_work_id_review", image, "신분증 확인", menu=menu)
