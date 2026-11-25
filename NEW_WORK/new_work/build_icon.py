"""Check the icon embedded in a Windows build before reporting success."""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path


def verify_executable_icon(executable: Path, icon: Path) -> None:
    import pefile  # Installed by PyInstaller on Windows.

    raw = icon.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", raw)
    if reserved != 0 or kind != 1 or count == 0:
        raise RuntimeError(f"Invalid ICO file: {icon}")
    expected = set()
    for index in range(count):
        size, offset = struct.unpack_from("<II", raw, 6 + index * 16 + 8)
        payload = raw[offset:offset + size]
        if len(payload) != size or not size:
            raise RuntimeError(f"Incomplete ICO file: {icon}")
        expected.add(payload)

    images = {}
    groups = []
    with pefile.PE(str(executable)) as pe:
        resources = getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None)
        if resources is None:
            raise RuntimeError(f"EXE has no icon resources: {executable}")
        for resource in resources.entries:
            if resource.id not in (3, 14):  # RT_ICON / RT_GROUP_ICON
                continue
            for item in resource.directory.entries:
                for language in item.directory.entries:
                    data = language.data.struct
                    payload = pe.get_data(data.OffsetToData, data.Size)
                    if resource.id == 3:
                        images[(item.id, language.id)] = payload
                    else:
                        groups.append((payload, language.id))
    if not groups or not expected.issubset(set(images.values())):
        raise RuntimeError(f"Custom icon was not embedded correctly: {executable}")
    for group, language in groups:
        group_count = struct.unpack_from("<H", group, 4)[0]
        if not group_count:
            raise RuntimeError(f"Empty icon group: {executable}")
        for index in range(group_count):
            image_id = struct.unpack_from("<H", group, 6 + index * 14 + 12)[0]
            if images.get((image_id, language)) not in expected:
                raise RuntimeError(f"Unexpected/default icon remains in EXE: {executable}")
    print(f"Verified embedded icon: {icon.name} -> {executable.resolve()}")
    print(f"EXE SHA-256: {hashlib.sha256(executable.read_bytes()).hexdigest()}")

    # Tell Explorer this exact file changed; do not delete the user's icon cache.
    import ctypes
    from ctypes import wintypes

    notify = ctypes.windll.shell32.SHChangeNotify
    notify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    notify.restype = None
    path = wintypes.LPCWSTR(str(executable.resolve()))
    notify(0x00002000, 0x0005, ctypes.cast(path, ctypes.c_void_p), None)

