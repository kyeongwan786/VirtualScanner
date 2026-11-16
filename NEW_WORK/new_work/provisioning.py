from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit


_STATION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_FILE_NAME = "relay-provisioning.json"


def _candidates() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("NEW_WORK_PROVISIONING_FILE", "").strip()
    if override:
        paths.append(Path(override).expanduser())
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    paths.extend((
        bundle_root / "new_work" / _FILE_NAME,
        bundle_root / _FILE_NAME,
        Path(__file__).resolve().parents[1] / _FILE_NAME,
        Path(__file__).resolve().parents[2] / "KyeongwanReceiver" / _FILE_NAME,
    ))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        normalized = path.resolve(strict=False)
        if normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def load_provisioning() -> dict[str, object] | None:
    path = next((item for item in _candidates() if item.is_file()), None)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Relay 자동 설정 파일을 읽지 못했습니다: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("Relay 자동 설정 파일 형식이 올바르지 않습니다.")
    relay_url = str(value.get("relay_url", "")).strip().rstrip("/")
    parsed = urlsplit(relay_url)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Relay 자동 설정 파일의 HTTPS 주소를 확인하세요.")
    central_token = str(value.get("central_token", "")).strip()
    if central_token and len(central_token) < 32:
        raise ValueError("Relay 자동 설정 파일의 중앙 토큰이 없거나 너무 짧습니다.")
    raw_stations = value.get("stations")
    if not isinstance(raw_stations, list):
        raise ValueError("Relay 자동 설정 파일의 자리 목록 형식이 올바르지 않습니다.")
    stations: list[dict[str, str]] = []
    seen: set[str] = set()
    seen_tokens: set[str] = set()
    for entry in raw_stations:
        if not isinstance(entry, dict):
            raise ValueError("Relay 자동 설정 파일의 자리 항목 형식이 올바르지 않습니다.")
        station_id = str(entry.get("station_id", "")).strip()
        label = str(entry.get("label", station_id)).strip() or station_id
        station_token = str(entry.get("token", "")).strip()
        if not _STATION_ID.fullmatch(station_id) or station_id in seen:
            raise ValueError("Relay 자동 설정 파일의 자리 ID가 잘못되었거나 중복됩니다.")
        if station_token and len(station_token) < 32:
            raise ValueError(f"{label} 자리 토큰은 32자 이상이어야 합니다.")
        if station_token and station_token in seen_tokens:
            raise ValueError("각 접수 자리에는 서로 다른 Relay 토큰이 필요합니다.")
        seen.add(station_id)
        if station_token:
            seen_tokens.add(station_token)
        stations.append({
            "label": label,
            "station_id": station_id,
            "token": station_token,
        })
    return {
        "relay_url": relay_url,
        "central_token": central_token,
        "stations": stations,
    }


def central_relay_config() -> dict[str, str] | None:
    value = load_provisioning()
    if value is None or not value["central_token"]:
        return None
    return {
        "relay_url": str(value["relay_url"]),
        "central_token": str(value["central_token"]),
    }


def station_options() -> list[dict[str, str]]:
    value = load_provisioning()
    if value is None:
        return []
    return [
        {"label": station["label"], "station_id": station["station_id"]}
        for station in value["stations"]
        if station["token"]
    ]


def station_labels() -> list[dict[str, str]]:
    value = load_provisioning()
    if value is None:
        return []
    return [
        {"label": station["label"], "station_id": station["station_id"]}
        for station in value["stations"]
    ]


def station_credentials(station_id: str) -> dict[str, str] | None:
    value = load_provisioning()
    if value is None:
        return None
    for station in value["stations"]:
        if station["station_id"] == station_id and station["token"]:
            return {
                "relay_url": str(value["relay_url"]),
                "station_id": station["station_id"],
                "token": station["token"],
            }
    return None


def build_bundle_payload(
    *, include_station_tokens: bool, include_central_token: bool,
) -> dict[str, object] | None:
    """Return role-limited credentials to embed in a Windows package."""
    value = load_provisioning()
    if value is None:
        return None
    if include_station_tokens and not value["stations"]:
        raise ValueError("Relay 자동 설정 파일에 Receiver 자리가 없습니다.")
    if include_central_token and not value["central_token"]:
        raise ValueError("Relay 자동 설정 파일에 중앙 토큰이 없습니다.")
    if any(not station["token"] for station in value["stations"]):
        raise ValueError("Relay 자동 설정 파일의 모든 Receiver 자리 토큰을 채우세요.")
    payload: dict[str, object] = {
        "relay_url": value["relay_url"],
        "stations": [
            {
                "label": station["label"],
                "station_id": station["station_id"],
                "token": station["token"] if include_station_tokens else "",
            }
            for station in value["stations"]
        ],
    }
    if include_central_token and value["central_token"]:
        payload["central_token"] = value["central_token"]
    return payload
