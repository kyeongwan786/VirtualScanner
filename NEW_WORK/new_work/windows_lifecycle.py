from __future__ import annotations

import sys
from pathlib import Path


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE_NAME = "NewWorkIdReview"
_RECEIVER_INSTANCE_HANDLE = None


def acquire_receiver_instance() -> bool:
    """Keep login startup and a manual launch from running two receivers."""
    global _RECEIVER_INSTANCE_HANDLE
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return True
    if _RECEIVER_INSTANCE_HANDLE is not None:
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateMutexW(None, False, r"Local\NewWorkReceiverV4")
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    # Windows closes this handle when the UI process exits, including crashes.
    _RECEIVER_INSTANCE_HANDLE = handle
    return True


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


def unregister_current_user_autostart(value_name: str = _RUN_VALUE_NAME) -> None:
    """Remove one startup entry owned by this app for the current user."""
    if sys.platform != "win32":
        return

    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, value_name)
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass


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
