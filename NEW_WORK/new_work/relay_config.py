from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def default_relay_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "NEW_WORK" / "relay.json"


def validate_relay_config(relay_url: str, central_token: str) -> dict[str, str]:
    url = relay_url.strip().rstrip("/")
    token = central_token.strip()
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Relay 주소는 경로 없이 https://호스트 형식으로 입력하세요.")
    if len(token) < 32:
        raise ValueError("중앙 Relay 토큰은 32자 이상이어야 합니다.")
    return {"relay_url": url, "central_token": token}


def load_relay_config(path: Path | None = None) -> dict[str, str] | None:
    config_path = path or default_relay_config_path()
    if not config_path.exists():
        return None
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Relay 설정을 읽지 못했습니다: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Relay 설정 형식이 올바르지 않습니다.")
    return validate_relay_config(
        str(value.get("relay_url", "")), str(value.get("central_token", ""))
    )


def save_relay_config(relay_url: str, central_token: str,
                      path: Path | None = None) -> Path:
    value = validate_relay_config(relay_url, central_token)
    target = path or default_relay_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target
