from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4


@dataclass(frozen=True)
class Store:
    store_id: str
    name: str
    save_path: str = ""
    database_path: str = ""
    scan_id: str = ""
    send_org_id: str = ""
    scan_name: str = ""
    ip_address: str = ""
    mac_address: str = ""
    scanner_serial_number: str = ""
    relay_station_id: str = ""
    # Kept only to read old settings; the current transfer uses the HTTPS relay.
    receiver_inbox: str = ""

    @classmethod
    def create(
        cls,
        name: str,
        save_path: str = "",
        database_path: str = "",
        store_id: str | None = None,
        scan_id: str = "",
        send_org_id: str = "",
        scan_name: str = "",
        ip_address: str = "",
        mac_address: str = "",
        scanner_serial_number: str = "",
        relay_station_id: str = "",
        receiver_inbox: str = "",
    ) -> "Store":
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("접점 이름을 입력하세요.")
        clean_relay_station = relay_station_id.strip()
        if clean_relay_station and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", clean_relay_station):
            raise ValueError("Relay 자리 ID는 영문, 숫자, 밑줄, 하이픈으로 1~64자여야 합니다.")
        return cls(
            store_id=store_id or str(uuid4()),
            name=clean_name,
            save_path=save_path.strip(),
            database_path=database_path.strip(),
            scan_id=scan_id.strip(),
            send_org_id=send_org_id.strip(),
            scan_name=scan_name.strip(),
            ip_address=ip_address.strip(),
            mac_address=mac_address.strip(),
            scanner_serial_number=scanner_serial_number.strip(),
            relay_station_id=clean_relay_station,
            receiver_inbox=receiver_inbox.strip(),
        )


def default_config_path() -> Path:
    override = os.environ.get("NEW_WORK_DATA_DIR")
    if override:
        return Path(override).expanduser() / "stores.json"
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        return base / "NEW_WORK" / "stores.json"
    return Path.home() / "Library" / "Application Support" / "NEW_WORK" / "stores.json"


def load_stores(path: Path | None = None) -> list[Store]:
    config_path = path or default_config_path()
    if not config_path.exists():
        return []
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"접점 설정을 읽지 못했습니다: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("접점 설정은 JSON 배열이어야 합니다.")
    stores: list[Store] = []
    seen: set[str] = set()
    seen_relay_stations: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("접점 항목 형식이 올바르지 않습니다.")
        legacy_sk = str(item.get("sk_destination", "")).strip()
        legacy_kt = str(item.get("kt_destination", "")).strip()
        configured_root = str(item.get("save_path", "")).strip()
        if not configured_root and legacy_sk and legacy_kt and legacy_sk != legacy_kt:
            raise ValueError(
                "기존 접점 설정에 서로 다른 SK/KT 목적지가 있습니다. "
                "접점 관리에서 원본 저장경로를 다시 지정하세요."
            )
        store = Store.create(
            name=str(item.get("name", "")),
            save_path=configured_root or legacy_sk or legacy_kt,
            database_path=str(item.get("database_path") or item.get("kt_database_template", "")),
            store_id=str(item.get("store_id") or uuid4()),
            scan_id=str(item.get("scan_id", "")),
            send_org_id=str(item.get("send_org_id", "")),
            scan_name=str(item.get("scan_name", "")),
            ip_address=str(item.get("ip_address", "")),
            mac_address=str(item.get("mac_address", "")),
            scanner_serial_number=str(item.get("scanner_serial_number", "")),
            relay_station_id=str(item.get("relay_station_id", "")),
            receiver_inbox=str(item.get("receiver_inbox", "")),
        )
        if store.store_id in seen:
            raise ValueError("중복된 접점 ID가 있습니다.")
        seen.add(store.store_id)
        if store.relay_station_id and store.relay_station_id in seen_relay_stations:
            raise ValueError("같은 Relay 자리 ID가 두 접점에 등록되어 있습니다.")
        if store.relay_station_id:
            seen_relay_stations.add(store.relay_station_id)
        stores.append(store)
    return stores


def save_stores(stores: list[Store], path: Path | None = None) -> Path:
    station_ids = [store.relay_station_id for store in stores if store.relay_station_id]
    if len(station_ids) != len(set(station_ids)):
        raise ValueError("같은 Relay 자리 ID가 두 접점에 등록되어 있습니다.")
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(store) for store in stores]
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=config_path.parent,
            prefix=f".{config_path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, config_path)
    except OSError:
        if temporary:
            temporary.unlink(missing_ok=True)
        raise
    return config_path
