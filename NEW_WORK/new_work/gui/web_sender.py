from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from new_work.domain import REVIEW_TEXT_FIELDS
from new_work.pdf_render import default_job_directory


MAX_UPLOAD_BYTES = 256 * 1024 * 1024
_HTML_PATH = Path(__file__).with_name("sender_ui.html")


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _identity_preview_data(app) -> tuple[str, int, int]:
    image = getattr(app, "_source_image", None)
    if image is None:
        return "", 0, 0
    cache = getattr(_identity_preview_data, "_cache", None)
    cache_key = id(image)
    if cache and cache[0] == cache_key:
        return cache[1], cache[2], cache[3]
    width, height = image.size
    preview = image.copy()
    preview.thumbnail((1600, 1100), Image.Resampling.LANCZOS)
    output = BytesIO()
    preview.convert("RGB").save(output, format="JPEG", quality=88, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    _identity_preview_data._cache = (cache_key, encoded, width, height)
    return encoded, width, height


def _station_rows(app) -> list[dict[str, object]]:
    from new_work.gui.app import RECEIVER_STATION_NAMES

    stores = list(getattr(app, "stores", []))
    slots: list[int | None] = [None] * max(17, len(stores))
    unassigned: list[int] = []
    for index, store in enumerate(stores):
        station_id = (store.relay_station_id or "").strip().lower()
        if station_id.startswith("desk-"):
            try:
                slot = int(station_id[5:]) - 1
            except ValueError:
                slot = -1
            if 0 <= slot < len(slots) and slots[slot] is None:
                slots[slot] = index
                continue
        unassigned.append(index)
    next_slot = 0
    for index in unassigned:
        while next_slot < len(slots) and slots[next_slot] is not None:
            next_slot += 1
        if next_slot == len(slots):
            slots.append(None)
        slots[next_slot] = index

    rows: list[dict[str, object]] = []
    for slot, store_index in enumerate(slots):
        station_name = (
            RECEIVER_STATION_NAMES[slot]
            if slot < len(RECEIVER_STATION_NAMES) else f"자리 {slot + 1}"
        )
        if store_index is None:
            rows.append({"slot": slot, "name": station_name, "configured": False,
                         "relay_station_id": f"desk-{slot + 1:02d}"})
            continue
        store = stores[store_index]
        display_name = getattr(app, "_store_display_names", {}).get(
            store.store_id, station_name,
        )
        rows.append({
            "slot": slot,
            "name": display_name,
            "store_id": store.store_id,
            "relay_station_id": store.relay_station_id,
            "configured": bool(store.relay_station_id),
            "selected": store_index == getattr(app, "_selected_store_index", -1),
        })
    return rows


def _snapshot(app) -> dict[str, object]:
    image_data, image_width, image_height = _identity_preview_data(app)
    draft = getattr(app, "draft", None)
    result = getattr(draft, "result", None)
    identity_name = ""
    if result is not None:
        identity_path = Path(result.image_path)
        identity_name = identity_path.name
        source_name_var = getattr(app, "id_image_name", None)
        if source_name_var is not None:
            identity_name = source_name_var.get() or identity_name

    documents = []
    for item in getattr(app, "_document_jobs", []):
        source = Path(item.get("source", ""))
        try:
            size = source.stat().st_size if source.is_file() else 0
        except OSError:
            size = 0
        documents.append({
            "key": str(item.get("key", "")),
            "name": source.name,
            "status": str(item.get("status", "")),
            "detail": str(item.get("pages", "")),
            "page_count": int(item.get("page_count", 0) or 0),
            "size": size,
        })

    values = {
        field: variable.get()
        for field, variable in getattr(app, "_value_vars", {}).items()
        if field in REVIEW_TEXT_FIELDS
    }
    fields = [
        {"key": field, "label": label}
        for field, label in app._review_specs()
    ]
    id_type_var = getattr(app, "_id_type_var", None)
    id_type = id_type_var.get() if id_type_var is not None else "유형 선택…"
    crop_bounds = getattr(app, "_id_crop_preview_bounds", None)
    crop = list(crop_bounds) if crop_bounds else None
    crop_size = None
    detected_code = getattr(getattr(app, "_id_type_detection", None), "code", None)
    if detected_code:
        from new_work.image.face_crop import id_portrait_size

        crop_size = list(id_portrait_size(detected_code))

    status_var = getattr(app, "review_status", None)
    ocr_status_var = getattr(app, "ocr_status", None)
    selected_store = app._selected_store()
    selected_name = ""
    if selected_store is not None:
        selected_name = getattr(app, "_store_display_names", {}).get(
            selected_store.store_id, selected_store.name,
        )

    return {
        "portal": app.portal_var.get(),
        "relay_configured": bool(getattr(app, "relay_config", None)),
        "relay_url": (getattr(app, "relay_config", None) or {}).get("relay_url", ""),
        "ocr_status": ocr_status_var.get() if ocr_status_var else "OCR 대기",
        "review_status": status_var.get() if status_var else "",
        "identity_name": identity_name,
        "identity_image": image_data,
        "image_width": image_width,
        "image_height": image_height,
        "crop_bounds": crop,
        "crop_size": crop_size,
        "crop_fallback": bool(getattr(app, "_id_crop_preview_is_fallback", False)),
        "crop_manual": bool(getattr(app, "_id_crop_manually_adjusted", False)),
        "id_type": id_type,
        "id_type_options": ["유형 선택…", *app.id_type_choice.cget("values")[1:]],
        "values": values,
        "fields": fields,
        "documents": documents,
        "stations": _station_rows(app),
        "selected_station": selected_name,
        "selected_store_index": getattr(app, "_selected_store_index", -1),
        "sending": bool(
            getattr(app, "_transfer_cancel_event", None)
            or getattr(app, "_pending_confirmation", None)
            or getattr(app, "_pending_auto_send", None)
        ),
        "awaiting_confirmation": bool(getattr(app, "_pending_confirmation", None)),
        "package_id": identity_name or "신분증 없음",
        "package_pdf_count": len(documents),
        "window_title": "신분증 확인 송신기",
    }


def _backend_process(port: int, token: str) -> None:
    from tkinterdnd2 import TkinterDnD

    from new_work.gui.app import NewWorkApp
    from new_work.ocr.adapter import PaddleOcrAdapter
    from new_work.webview_backend import hide_backend_application

    root = TkinterDnD.Tk()
    app = NewWorkApp(
        root,
        ocr=PaddleOcrAdapter(device="cpu", ocr_version="PP-OCRv5"),
        version="v2",
    )
    hide_backend_application(root)

    snapshot_lock = threading.Lock()
    state: dict[str, object] = {"ready": False}
    upload_root = default_job_directory() / ".sender_web_uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    temporary_uploads: dict[str, str] = {}
    batch_lock = threading.RLock()
    batch_ocr_adapters = [app.ocr, None]
    batch_ocr_executors = [
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="batch-ocr-1"),
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="batch-ocr-2"),
    ]
    batch: dict[str, object] = {
        "id": "", "portal": "SK", "status": "idle", "error": "", "items": [],
        "expected_count": 0,
    }
    stop_event = threading.Event()

    def batch_summary() -> dict[str, object]:
        with batch_lock:
            items = batch["items"]
            return {
                "id": batch["id"], "portal": batch["portal"],
                "status": batch["status"], "error": batch["error"],
                "expected_count": batch["expected_count"],
                "items": [
                    {key: item.get(key) for key in (
                        "id", "index", "name", "ocr_status", "code", "type_label",
                        "send_status", "job_id", "error", "reviewed", "defer",
                    )}
                    | {"document_count": len(item.get("documents", []))}
                    for item in sorted(items, key=lambda row: row.get("index", 0))
                ],
            }

    def batch_item_payload(item_id: str) -> dict[str, object] | None:
        with batch_lock:
            item = next((row for row in batch["items"] if row["id"] == item_id), None)
            if item is None or item.get("ocr_status") != "완료":
                return None
            path = Path(item["path"])
            from PIL import ImageOps
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
            image_stream = BytesIO()
            image.save(image_stream, format="JPEG", quality=86)
            return {
                "id": item["id"], "name": item["name"],
                "image": base64.b64encode(image_stream.getvalue()).decode("ascii"),
                "image_width": item["width"], "image_height": item["height"],
                "crop_bounds": item["crop_bounds"], "crop_size": item["crop_size"],
                "crop_fallback": item["crop_fallback"], "crop_manual": item["crop_manual"],
                "code": item["code"], "type_label": item["type_label"],
                "id_type_options": item["id_type_options"],
                "fields": item["fields"], "values": dict(item["values"]),
                "ocr_status": item["ocr_status"],
                "reviewed": item["reviewed"],
                "defer": item["defer"],
                "documents": [
                    {"key": document["key"], "name": document.get("name", Path(document["source"]).name),
                     "page_count": document["page_count"], "status": document["status"]}
                    for document in item["documents"]
                ],
            }

    def update_batch_item(data: dict[str, object]) -> None:
        from new_work.gui.app import (
            FOCUS_FIELDS, FOCUS_GROUP_BY_CODE, ID_TYPE_LABEL_TO_CODE,
        )
        from new_work.ocr.id_types import ID_TYPES, IdTypeDetection
        from new_work.ocr.field_extractor import extract_identity_fields
        from new_work.image.face_crop import (
            id_portrait_bounds, id_portrait_fallback_bounds, id_portrait_size,
        )
        from new_work.carrier.sk_report import SK_ID_CROP_CODES

        item_id = str(data.get("item_id", ""))
        with batch_lock:
            item = next((row for row in batch["items"] if row["id"] == item_id), None)
            if item is None or item.get("ocr_status") != "완료":
                raise ValueError("검토할 신분증을 찾지 못했습니다.")
            if item.get("send_status") in {"전송 중", "확인 필요", "Relay 등록"}:
                raise ValueError("이미 Relay 전송을 시작한 신분증은 수정할 수 없습니다.")
            raw_values = data.get("values")
            if not isinstance(raw_values, dict):
                raise ValueError("OCR 수정값 형식이 올바르지 않습니다.")
            values = dict(item["values"])
            for key, value in raw_values.items():
                if key in REVIEW_TEXT_FIELDS:
                    values[key] = str(value)[:2048]
            label = str(data.get("type_label", ""))
            code = ID_TYPE_LABEL_TO_CODE.get(label)
            if not code:
                raise ValueError("신분증 종류를 선택하세요.")
            values["kaitIdType"] = code
            values["recogSubCd"] = code
            values["idType"] = ID_TYPES[code]["type"]
            values["nationClCd"] = "F" if code == "1401" else "I"

            raw_bounds = None if data.get("type_changed") else data.get("crop_bounds")
            crop_manual = bool(data.get("crop_manual", False)) and not data.get("type_changed")
            bounds = None
            if raw_bounds is not None:
                if (not isinstance(raw_bounds, list) or len(raw_bounds) != 4
                        or any(not isinstance(value, int) for value in raw_bounds)):
                    raise ValueError("idCrop 좌표가 올바르지 않습니다.")
                left, top, right, bottom = raw_bounds
                if not (0 <= left < right <= item["width"]
                        and 0 <= top < bottom <= item["height"]):
                    raise ValueError("idCrop 영역이 이미지 범위를 벗어났습니다.")
                bounds = [left, top, right, bottom]
                if crop_manual or not item.get("crop_fallback"):
                    values["_idCropBounds"] = ",".join(str(value) for value in bounds)

            if code == "1302":
                extracted = extract_identity_fields(item["result"].lines, id_type_code=code)
                for field in ("custNm", "ctzCorpNum", "veteranNum"):
                    if extracted.get(field) and not values.get(field, "").strip():
                        values[field] = extracted[field]

            documents = item.get("documents", [])
            defer = bool(data.get("defer", item.get("defer", False))) or bool(documents)

            source = Image.open(item["path"]).convert("RGB")
            portal = str(batch["portal"])
            is_crop_type = portal == "KT" or code in SK_ID_CROP_CODES
            fallback = bool(item.get("crop_fallback")) and not crop_manual
            crop_size = list(id_portrait_size(code)) if is_crop_type else None
            if is_crop_type and bounds is None:
                automatic = id_portrait_bounds(
                    source, recog_sub_cd=code, require_face=True,
                )
                fallback = automatic is None
                automatic = automatic or id_portrait_fallback_bounds(
                    source, recog_sub_cd=code,
                )
                bounds = list(automatic) if automatic else None
                if bounds and fallback:
                    values.pop("_idCropBounds", None)
            if not is_crop_type:
                values.pop("_idCropBounds", None)
                bounds = None

            detection = IdTypeDetection(
                name=ID_TYPES[code]["name"], type_code=ID_TYPES[code]["type"],
                candidate_codes=(code,), preferred_code=code,
            )
            item.update({
                "values": values, "code": code, "type_label": label,
                "detection": detection, "crop_bounds": bounds,
                "crop_size": crop_size, "crop_fallback": fallback,
                "crop_manual": crop_manual,
                "defer": defer,
                "fields": [
                    {"key": field, "label": title}
                    for field, title in FOCUS_FIELDS.get(
                        FOCUS_GROUP_BY_CODE.get(code, "resident"), FOCUS_FIELDS["resident"],
                    )
                ],
                "reviewed": True,
            })
            item["fields"] = item["fields"] + [
                {"key": field, "label": label}
                for field, label in (("addr", "주소"), ("isuePlc", "발급기관"))
                if values.get(field, "").strip()
                and field not in {entry["key"] for entry in item["fields"]}
            ]

    def _start_batch_send() -> None:
        store = app._selected_store()
        if store is None or not store.relay_station_id:
            raise ValueError("전송 대상을 선택하고 Relay 자리 ID를 설정하세요.")
        if app.relay_config is None:
            raise ValueError(app._relay_config_error or "Relay 설정을 확인하세요.")
        with batch_lock:
            items = sorted(batch["items"], key=lambda row: row.get("index", 0))
            if len(items) < 2 or batch["status"] not in {"reviewing", "partial"}:
                raise ValueError("전송할 검토 묶음이 없습니다.")
            if len(items) != batch["expected_count"]:
                raise ValueError("선택한 모든 신분증의 OCR 준비를 마친 뒤 전송하세요.")
            if any(item.get("ocr_status") != "완료" for item in items):
                raise ValueError("OCR가 완료되지 않은 신분증이 있습니다.")
            if any(not item.get("reviewed") for item in items):
                raise ValueError("모든 신분증을 하나씩 확인하고 저장하세요.")
            if any(not item.get("code") for item in items):
                raise ValueError("모든 신분증의 종류를 선택하세요.")
            if any(item.get("send_status") == "확인 필요" for item in items):
                raise ValueError("Relay 작업 상태를 먼저 확인하세요.")
            pending = [item for item in items if item.get("send_status") != "Relay 등록"]
            pending.sort(key=lambda item: bool(item.get("defer") or item.get("documents")))
            if not pending:
                raise ValueError("모든 신분증이 이미 Relay에 등록되었습니다.")
            batch["status"] = "sending"
            batch["error"] = ""
            portal = str(batch["portal"])
            batch_id = str(batch["id"])
            relay_config = dict(app.relay_config)
        threading.Thread(
            target=_send_batch_worker,
            args=(batch_id, items, pending, store, portal, relay_config),
            daemon=True,
            name="new-work-batch-send",
        ).start()

    def _send_batch_worker(
        batch_id: str, all_items: list[dict[str, object]],
        pending: list[dict[str, object]], store, carrier: str,
        relay_config: dict[str, str],
    ) -> None:
        from new_work.gui.app import DeliveryPendingError
        from new_work.relay_client import send_work_package
        from new_work.job_package import create_work_package
        from new_work.domain import OcrResult

        for item in pending:
            with batch_lock:
                item["send_status"] = "전송 중"
                item["error"] = ""
            transfer_id = str(uuid4())
            item_dir = Path(item["path"]).parent.parent
            package = item_dir / f"{transfer_id}_{carrier}.zip"
            result = item["result"]
            if not isinstance(result, OcrResult):
                with batch_lock:
                    item["send_status"] = "실패"
                    item["error"] = "OCR 결과를 찾지 못했습니다."
                    batch["status"] = "partial"
                    batch["error"] = item["error"]
                break
            try:
                create_work_package(
                    package, job_id=transfer_id,
                    station_id=store.relay_station_id, carrier=carrier,
                    result=result, detection=item["detection"],
                    reviewed_values=dict(item["values"]),
                    document_jobs=list(item.get("documents", [])),
                )
                job_id = send_work_package(
                    relay_config, store.relay_station_id, package,
                    progress=lambda message, row=item: _set_batch_item_progress(row, message),
                    wait_for_delivery=False,
                )
            except DeliveryPendingError as exc:
                with batch_lock:
                    item["send_status"] = "확인 필요"
                    item["job_id"] = exc.job_id
                    item["error"] = str(exc)
                    batch["status"] = "partial"
                    batch["error"] = str(exc)
                break
            except Exception as exc:
                with batch_lock:
                    item["send_status"] = "실패"
                    item["error"] = str(exc)
                    batch["status"] = "partial"
                    batch["error"] = str(exc)
                break
            else:
                with batch_lock:
                    item["send_status"] = "Relay 등록"
                    item["job_id"] = job_id
                    item["error"] = ""

        with batch_lock:
            if all(item.get("send_status") == "Relay 등록" for item in all_items):
                batch["status"] = "complete"
                batch["error"] = ""
            elif batch["status"] == "sending":
                batch["status"] = "partial"
        app.root.after(0, lambda: app.review_status.set(
            f"신분증 묶음 전송 상태: {batch_summary()['status']}"
        ))

    def _set_batch_item_progress(item: dict[str, object], message: str) -> None:
        with batch_lock:
            item["error"] = message

    def _check_batch_status() -> None:
        with batch_lock:
            checking = [item for item in batch["items"] if item.get("send_status") == "확인 필요"]
            if not checking:
                raise ValueError("확인할 Relay 작업이 없습니다.")
            batch["status"] = "checking"
        threading.Thread(
            target=_check_batch_worker, args=(checking,), daemon=True,
            name="new-work-batch-status",
        ).start()

    def _check_batch_worker(items: list[dict[str, object]]) -> None:
        from new_work.relay_client import get_work_package_status

        for item in items:
            try:
                status = get_work_package_status(
                    dict(app.relay_config), str(item["job_id"]),
                )
            except Exception as exc:
                with batch_lock:
                    item["error"] = f"상태 확인 실패: {exc}"
                continue
            with batch_lock:
                if status in {"queued", "delivered"}:
                    item["send_status"] = "Relay 등록"
                    item["error"] = ""
                elif status == "cancelled":
                    item["send_status"] = "실패"
                    item["error"] = "Relay에서 취소된 작업입니다. 재전송할 수 있습니다."
                else:
                    item["error"] = f"Relay 상태: {status} · 중복 전송하지 않았습니다."
        with batch_lock:
            batch["status"] = (
                "complete" if all(row.get("send_status") == "Relay 등록" for row in batch["items"])
                else "partial"
            )

    class Handler(BaseHTTPRequestHandler):
        server_version = "NewWorkLocalUI/1"

        def log_message(self, _format: str, *_args) -> None:
            return

        def _reply(self, status: int, payload: object, content_type: str = "application/json") -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(
                payload, ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            return self.headers.get("X-NewWork-Token", "") == token

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/" and query.get("token", [""])[0] == token:
                try:
                    self._reply(200, _HTML_PATH.read_bytes(), "text/html; charset=utf-8")
                except OSError as exc:
                    self._reply(500, {"error": str(exc)})
                return
            if parsed.path == "/api/health" and query.get("token", [""])[0] == token:
                self._reply(200, {"ok": True})
                return
            if parsed.path == "/api/state" and self._authorized():
                with snapshot_lock:
                    current = dict(state)
                self._reply(200, current)
                return
            if parsed.path == "/api/batch/state" and self._authorized():
                self._reply(200, batch_summary())
                return
            if parsed.path == "/api/batch/item" and self._authorized():
                item_id = query.get("item_id", [""])[0]
                item = batch_item_payload(item_id)
                self._reply(200 if item else 404, item or {"error": "신분증을 찾지 못했습니다."})
                return
            self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                self._reply(403, {"error": "unauthorized"})
                return
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/api/action":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > 1024 * 1024:
                        raise ValueError("action payload too large")
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    action = str(payload.get("action", ""))
                    data = payload.get("data", {})
                    if not isinstance(data, dict):
                        raise ValueError("invalid action data")
                    result = self._dispatch(action, data)
                    self._reply(200, {"ok": True, "result": result})
                except Exception as exc:
                    self._reply(400, {"ok": False, "error": str(exc)})
                return
            if parsed.path == "/api/upload":
                self._upload(parsed.query)
                return
            if parsed.path == "/api/shutdown":
                self._reply(200, {"ok": True})
                root.after(0, root.destroy)
                stop_event.set()
                return
            self._reply(404, {"error": "not found"})

        def _dispatch(self, action: str, data: dict[str, object]) -> object:
            result: dict[str, object] = {"done": False, "value": None, "error": None}
            completed = threading.Event()

            def run_action() -> None:
                try:
                    if action == "set_portal":
                        portal = str(data.get("portal", ""))
                        if portal not in {"SK", "KT"}:
                            raise ValueError("포탈 값이 올바르지 않습니다.")
                        app.portal_var.set(portal)
                    elif action == "set_field":
                        field = str(data.get("field", ""))
                        if field not in REVIEW_TEXT_FIELDS:
                            raise ValueError("수정할 수 없는 OCR 항목입니다.")
                        app._value_vars[field].set(str(data.get("value", "")))
                        app._format_field_display(field)
                    elif action == "set_id_type":
                        label = str(data.get("value", ""))
                        valid = set(app.id_type_choice.cget("values"))
                        if label not in valid:
                            raise ValueError("신분증 종류를 확인하세요.")
                        app.id_type_choice.set(label)
                        app._id_type_choice_selected()
                    elif action == "select_station":
                        store_id = str(data.get("store_id", ""))
                        index = next(
                            (i for i, store in enumerate(app.stores)
                             if store.store_id == store_id), -1,
                        )
                        if index < 0:
                            raise ValueError("접점이 등록되지 않은 자리입니다.")
                        app._select_store(index)
                    elif action == "run_ocr":
                        app.run_ocr()
                    elif action == "send":
                        app.mark_reviewed()
                    elif action == "batch_start":
                        count = int(data.get("count", 0))
                        if count < 2 or count > 50:
                            raise ValueError("한 번에 2~50장의 신분증을 선택하세요.")
                        if (app._transfer_cancel_event is not None
                                or app._pending_confirmation is not None
                                or app._pending_auto_send is not None):
                            raise ValueError("진행 중인 전송을 마친 뒤 여러 신분증을 등록하세요.")
                        if app.draft is not None:
                            raise ValueError("현재 열려 있는 신분증을 먼저 초기화하거나 전송하세요.")
                        if not app._ocr_warmup_finished or not app._ocr_models_ready:
                            raise ValueError("OCR 모델 준비가 끝난 뒤 신분증 묶음을 시작하세요.")
                        selected = app._selected_store()
                        if selected is None or not selected.relay_station_id:
                            raise ValueError("먼저 전송 대상을 선택하고 Relay 자리 ID를 설정하세요.")
                        if app.relay_config is None:
                            raise ValueError(app._relay_config_error or "먼저 Relay 설정을 완료하세요.")
                        if batch["status"] not in {"idle", "closed"}:
                            raise ValueError("이미 여러 신분증 검토 작업이 열려 있습니다.")
                        batch.update({
                            "id": str(uuid4()), "portal": app.portal_var.get(),
                            "status": "reviewing", "error": "", "items": [],
                            "expected_count": count,
                        })
                        result["value"] = {"batch_id": batch["id"]}
                    elif action == "batch_update":
                        update_batch_item(data)
                    elif action == "batch_remove_pdf":
                        item_id = str(data.get("item_id", ""))
                        document_key = str(data.get("document_key", ""))
                        with batch_lock:
                            item = next(
                                (row for row in batch["items"] if row["id"] == item_id), None,
                            )
                            if item is None or item.get("send_status") != "대기":
                                raise ValueError("PDF를 제거할 신분증을 찾지 못했습니다.")
                            document = next(
                                (row for row in item.get("documents", [])
                                 if row["key"] == document_key), None,
                            )
                            if document is None:
                                raise ValueError("PDF를 찾지 못했습니다.")
                            item["documents"].remove(document)
                            shutil.rmtree(Path(document["source"]).parent, ignore_errors=True)
                            if not item["documents"]:
                                item["defer"] = False
                    elif action == "batch_cancel":
                        with batch_lock:
                            if batch["status"] in {"sending", "partial", "checking"}:
                                raise ValueError("전송이 시작된 묶음은 취소할 수 없습니다.")
                            if any(
                                row.get("ocr_status") in {"업로드 중", "대기 중", "인식 중"}
                                for row in batch["items"]
                            ):
                                raise ValueError("진행 중인 OCR이 끝난 뒤 묶음을 취소하세요.")
                            batch_dir = (
                                default_job_directory() / ".sender-batches" / str(batch["id"])
                            )
                            shutil.rmtree(batch_dir, ignore_errors=True)
                            batch.update({
                                "id": "", "status": "closed", "error": "", "items": [],
                                "expected_count": 0,
                            })
                    elif action == "batch_send":
                        _start_batch_send()
                    elif action == "batch_check":
                        _check_batch_status()
                    elif action == "batch_close":
                        with batch_lock:
                            if batch["status"] not in {"complete", "closed"}:
                                raise ValueError("모든 전송이 Relay에 등록된 뒤 묶음을 닫을 수 있습니다.")
                            batch_dir = (
                                default_job_directory() / ".sender-batches" / str(batch["id"])
                            )
                            shutil.rmtree(batch_dir, ignore_errors=True)
                            batch.update({
                                "id": "", "status": "closed", "error": "", "items": [],
                                "expected_count": 0,
                            })
                    elif action == "reset":
                        app._reset_sender_session()
                    elif action == "clear_pdfs":
                        app._clear_pdf_list()
                    elif action == "settings_stations":
                        result["value"] = [{"store_id": store.store_id, "name": store.name,
                                            "relay_station_id": store.relay_station_id}
                                           for store in app.stores]
                    elif action == "settings_relay":
                        result["value"] = {"relay_url": (app.relay_config or {}).get("relay_url", ""),
                                           "token_configured": bool((app.relay_config or {}).get("central_token"))}
                    elif action == "save_stations":
                        from new_work.stores import Store, save_stores

                        rows = data.get("stations")
                        if not isinstance(rows, list) or len(rows) > 100:
                            raise ValueError("접점 목록 형식이 올바르지 않습니다.")
                        old_by_id = {store.store_id: store for store in app.stores}
                        stores = []
                        for row in rows:
                            if not isinstance(row, dict):
                                raise ValueError("접점 정보 형식이 올바르지 않습니다.")
                            store_id = str(row.get("store_id", "")).strip()
                            name = str(row.get("name", "")).strip()
                            relay_id = str(row.get("relay_station_id", "")).strip()
                            old = old_by_id.get(store_id)
                            if old is None:
                                stores.append(Store.create(name, relay_station_id=relay_id))
                            else:
                                validated = Store.create(
                                    name,
                                    save_path=old.save_path,
                                    database_path=old.database_path,
                                    store_id=old.store_id,
                                    scan_id=old.scan_id,
                                    send_org_id=old.send_org_id,
                                    scan_name=old.scan_name,
                                    ip_address=old.ip_address,
                                    mac_address=old.mac_address,
                                    scanner_serial_number=old.scanner_serial_number,
                                    relay_station_id=relay_id,
                                    receiver_inbox=old.receiver_inbox,
                                )
                                stores.append(validated)
                        save_stores(stores)
                        app._stores_changed(stores)
                    elif action == "save_relay_settings":
                        from new_work.relay_config import load_relay_config, save_relay_config

                        relay_url = str(data.get("relay_url", "")).strip()
                        central_token = str(data.get("central_token", "")).strip()
                        if not central_token and app.relay_config:
                            central_token = app.relay_config.get("central_token", "")
                        save_relay_config(relay_url, central_token)
                        app.relay_config = load_relay_config()
                        app._relay_config_error = ""
                        app._refresh_stores()
                    elif action == "check_status":
                        app._check_pending_status()
                    elif action == "show_raw_ocr":
                        app._show_raw_ocr()
                    elif action == "set_crop":
                        raw = data.get("bounds")
                        if raw is None:
                            app._reset_crop_bounds()
                        else:
                            if not isinstance(raw, list) or len(raw) != 4:
                                raise ValueError("크롭 좌표가 올바르지 않습니다.")
                            if app._source_image is None:
                                raise ValueError("신분증 이미지를 먼저 선택하세요.")
                            left, top, right, bottom = (int(value) for value in raw)
                            width, height = app._source_image.size
                            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                                raise ValueError("크롭 영역이 이미지 범위를 벗어났습니다.")
                            app._id_crop_preview_bounds = (left, top, right, bottom)
                            app._id_crop_manually_adjusted = True
                            app._id_crop_preview_is_fallback = False
                            app._id_crop_drag = None
                            app._update_id_crop_value()
                    elif action in {"web_identity", "web_pdf"}:
                        path = Path(str(data.get("path", "")))
                        if not path.is_file():
                            raise FileNotFoundError("업로드한 파일을 찾지 못했습니다.")
                        if action == "web_identity":
                            if path.suffix.casefold() not in {".jpg", ".jpeg", ".png"}:
                                raise ValueError("JPG 또는 PNG 신분증 이미지를 선택하세요.")
                            previous_result = getattr(getattr(app, "draft", None), "result", None)
                            app._load_new_identity(path)
                            current_result = getattr(getattr(app, "draft", None), "result", None)
                            if current_result is None or current_result is previous_result:
                                raise ValueError("신분증 이미지를 불러오지 못했습니다.")
                            return
                        if path.suffix.casefold() != ".pdf":
                            raise ValueError("PDF 파일을 선택하세요.")
                        previous_count = len(app._document_jobs)
                        app._queue_pdf_paths([path])
                        if len(app._document_jobs) == previous_count:
                            raise ValueError("PDF를 변환 목록에 추가하지 못했습니다.")
                    else:
                        raise ValueError("알 수 없는 화면 동작입니다.")
                except Exception as exc:
                    result["error"] = str(exc)
                finally:
                    result["done"] = True
                    completed.set()

            root.after(0, run_action)
            if not completed.wait(60):
                raise TimeoutError("화면 동작 응답 시간이 초과됐습니다.")
            if result["error"]:
                raise RuntimeError(str(result["error"]))
            return result["value"]

        def _upload(self, raw_query: str) -> None:
            query = urllib.parse.parse_qs(raw_query)
            kind = query.get("kind", [""])[0]
            filename = Path(query.get("name", [""])[0]).name
            batch_id = query.get("batch_id", [""])[0]
            item_id = query.get("item_id", [""])[0]
            try:
                item_index = int(query.get("index", ["0"])[0])
            except ValueError:
                self._reply(400, {"ok": False, "error": "신분증 순서 값이 올바르지 않습니다."})
                return
            length = int(self.headers.get("Content-Length", "0"))
            allowed = {"identity": {".jpg", ".jpeg", ".png"}, "pdf": {".pdf"}}
            if kind not in allowed or Path(filename).suffix.casefold() not in allowed[kind]:
                self._reply(400, {"ok": False, "error": "파일 형식을 확인하세요."})
                return
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self._reply(413, {"ok": False, "error": "파일 크기가 허용 범위를 벗어났습니다."})
                return
            safe_name = filename[:180]
            temporary = upload_root / f"{uuid4().hex}_{safe_name}"
            try:
                remaining = length
                with temporary.open("wb") as output:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError("파일 업로드가 중간에 끊겼습니다.")
                        output.write(chunk)
                        remaining -= len(chunk)
                if kind == "identity":
                    if batch_id:
                        self._add_batch_identity(
                            batch_id, item_index, safe_name, temporary,
                        )
                        self._reply(200, {"ok": True, "name": safe_name})
                        return
                    self._dispatch("web_identity", {"path": str(temporary)})
                    temporary.unlink(missing_ok=True)
                else:
                    if batch_id:
                        self._add_batch_pdf(batch_id, item_id, safe_name, temporary)
                        self._reply(200, {"ok": True, "name": safe_name})
                        return
                    self._dispatch("web_pdf", {"path": str(temporary)})
                    temporary_uploads[str(temporary)] = "pdf"
                self._reply(200, {"ok": True, "name": safe_name})
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                self._reply(400, {"ok": False, "error": str(exc)})

        def _add_batch_pdf(
            self, requested_batch_id: str, item_id: str, filename: str, temporary: Path,
        ) -> None:
            from new_work.pdf_render import render_pdf_to_jpegs

            if not item_id:
                raise ValueError("PDF를 연결할 신분증을 선택하세요.")
            with batch_lock:
                if requested_batch_id != batch["id"] or batch["status"] != "reviewing":
                    raise ValueError("신분증 묶음 검토 작업이 만료되었습니다.")
                item = next((row for row in batch["items"] if row["id"] == item_id), None)
                if item is None or item.get("send_status") != "대기":
                    raise ValueError("PDF를 연결할 신분증을 찾지 못했습니다.")
                if not item.get("defer"):
                    raise ValueError("먼저 이 신분증을 PDF와 함께 뒤에 보내기로 표시하세요.")
                previous_bytes = sum(
                    Path(row["path"]).stat().st_size
                    for row in batch["items"] if Path(row["path"]).is_file()
                )
                previous_bytes += sum(
                    Path(document["source"]).stat().st_size
                    for row in batch["items"] for document in row.get("documents", [])
                    if Path(document["source"]).is_file()
                )
                if previous_bytes + temporary.stat().st_size > 512 * 1024 * 1024:
                    raise ValueError("신분증과 PDF 묶음이 512 MiB 제한을 초과합니다.")
                document_key = str(uuid4())
                item_dir = Path(item["path"]).parent.parent
                docs_root = item_dir / "documents"
                docs_root.mkdir(parents=True, exist_ok=True)
                safe_stem = "".join(
                    char if char.isalnum() or char in " ._-" else "_"
                    for char in Path(filename).stem
                ).strip(" .")[:100] or "document"
                document_dir = docs_root / document_key
                document_dir.mkdir(parents=True, exist_ok=False)
                source = document_dir / f"{safe_stem}.pdf"
                output = document_dir / "pages"
                temporary.replace(source)
            try:
                pages = render_pdf_to_jpegs(source, output)
            except Exception:
                shutil.rmtree(source.parent, ignore_errors=True)
                raise
            with batch_lock:
                current = next((row for row in batch["items"] if row["id"] == item_id), None)
                if requested_batch_id != batch["id"] or current is not item:
                    shutil.rmtree(source.parent, ignore_errors=True)
                    raise ValueError("신분증 묶음 검토가 취소되었습니다.")
                item.setdefault("documents", []).append({
                    "key": document_key, "source": source, "output": output,
                    "name": source.name, "status": "변환 완료", "page_count": len(pages),
                })
                item["defer"] = True

        def _add_batch_identity(
            self, requested_batch_id: str, item_index: int,
            filename: str, temporary: Path,
        ) -> None:
            from new_work.gui.app import (
                ID_TYPE_LABEL_TO_CODE,
            )
            from PIL import ImageOps

            with batch_lock:
                if requested_batch_id != batch["id"] or batch["status"] != "reviewing":
                    raise ValueError("여러 신분증 검토 작업이 만료되었습니다. 다시 선택하세요.")
                if not 0 <= item_index < int(batch["expected_count"]):
                    raise ValueError("신분증 순서 값이 올바르지 않습니다.")
                if any(row.get("index") == item_index for row in batch["items"]):
                    raise ValueError("같은 신분증 업로드가 중복되었습니다.")
                item_id = str(uuid4())
                batch_dir = default_job_directory() / ".sender-batches" / requested_batch_id / item_id
                image_dir = batch_dir / "identity"
                image_dir.mkdir(parents=True, exist_ok=False)
                destination = image_dir / "identity.jpg"
                total_bytes = sum(
                    Path(row["path"]).stat().st_size
                    for row in batch["items"] if Path(row["path"]).is_file()
                ) + temporary.stat().st_size
                if total_bytes > 512 * 1024 * 1024:
                    raise ValueError("신분증 묶음이 512 MiB 제한을 초과합니다.")
                item = {
                    "id": item_id, "index": item_index, "name": filename,
                    "path": destination, "result": None, "detection": None,
                    "values": {}, "code": None, "type_label": "",
                    "crop_bounds": None, "crop_size": None,
                    "crop_fallback": False, "crop_manual": False,
                    "width": 0, "height": 0,
                    "ocr_status": "업로드 중", "send_status": "대기", "job_id": "",
                    "error": "", "reviewed": False, "defer": False,
                    "documents": [], "fields": [],
                    "id_type_options": ["유형 선택…", *ID_TYPE_LABEL_TO_CODE.keys()],
                }
                batch["items"].append(item)
            try:
                with Image.open(temporary) as opened:
                    staged = ImageOps.exif_transpose(opened).convert("RGB")
                    staged.save(destination, format="JPEG", quality=95, optimize=True)
                temporary.unlink(missing_ok=True)
            except Exception as exc:
                shutil.rmtree(batch_dir, ignore_errors=True)
                with batch_lock:
                    if requested_batch_id == batch["id"]:
                        item["ocr_status"] = "실패"
                        item["error"] = str(exc)
                raise

            with batch_lock:
                if requested_batch_id != batch["id"]:
                    shutil.rmtree(batch_dir, ignore_errors=True)
                    raise ValueError("여러 신분증 검토 작업이 만료되었습니다.")
                item["ocr_status"] = "대기 중"
                worker_index = item_index % len(batch_ocr_executors)
                batch_ocr_executors[worker_index].submit(
                    _recognize_batch_identity, item, worker_index, requested_batch_id,
                )

        def _recognize_batch_identity(
            item: dict[str, object], worker_index: int, requested_batch_id: str,
        ) -> None:
            from new_work.gui.app import (
                FOCUS_FIELDS, FOCUS_GROUP_BY_CODE, ID_TYPE_CODE_TO_LABEL,
                recognize_identity_for_review,
            )
            from new_work.ocr.adapter import PaddleOcrAdapter
            from new_work.carrier.sk_report import SK_ID_CROP_CODES
            from new_work.image.face_crop import (
                id_portrait_bounds, id_portrait_fallback_bounds, id_portrait_size,
            )
            from PIL import ImageOps

            destination = Path(item["path"])
            try:
                with batch_lock:
                    if requested_batch_id != batch["id"]:
                        return
                    item["ocr_status"] = "인식 중"
                if worker_index == 1 and batch_ocr_adapters[worker_index] is None:
                    adapter = PaddleOcrAdapter(device="cpu", ocr_version="PP-OCRv5")
                    adapter.warmup()
                    batch_ocr_adapters[worker_index] = adapter
                adapter = batch_ocr_adapters[worker_index]
                ocr_result, detection, values = recognize_identity_for_review(
                    adapter, destination,
                )
                with Image.open(destination) as opened:
                    source_image = ImageOps.exif_transpose(opened).convert("RGB")
                    width, height = source_image.size
                code = detection.code
                label = ID_TYPE_CODE_TO_LABEL.get(code or "", "")
                portal = str(batch["portal"])
                crop_type = bool(code and (portal == "KT" or code in SK_ID_CROP_CODES))
                crop_size = list(id_portrait_size(code)) if crop_type else None
                crop_bounds = None
                crop_fallback = False
                if crop_type:
                    crop_bounds = id_portrait_bounds(
                        source_image, recog_sub_cd=code, require_face=True,
                    )
                    crop_fallback = crop_bounds is None
                    crop_bounds = crop_bounds or id_portrait_fallback_bounds(
                        source_image, recog_sub_cd=code,
                    )
                fields = [
                    {"key": field, "label": title}
                    for field, title in FOCUS_FIELDS.get(
                        FOCUS_GROUP_BY_CODE.get(code or "", "resident"),
                        FOCUS_FIELDS["resident"],
                    )
                ]
                fields.extend(
                    {"key": field, "label": title}
                    for field, title in (("addr", "주소"), ("isuePlc", "발급기관"))
                    if values.get(field, "").strip()
                    and field not in {entry["key"] for entry in fields}
                )
                with batch_lock:
                    if requested_batch_id != batch["id"]:
                        return
                    item.update({
                        "result": ocr_result, "detection": detection,
                        "values": values, "code": code, "type_label": label,
                        "crop_bounds": list(crop_bounds) if crop_bounds else None,
                        "crop_size": crop_size, "crop_fallback": crop_fallback,
                        "width": width, "height": height,
                        "ocr_status": "완료", "error": "", "fields": fields,
                    })
            except Exception as exc:
                with batch_lock:
                    if requested_batch_id == batch["id"]:
                        item["ocr_status"] = "실패"
                        item["error"] = str(exc)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True

    def serve() -> None:
        server.serve_forever(poll_interval=0.2)

    threading.Thread(target=serve, daemon=True, name="sender-local-http").start()

    def refresh_state() -> None:
        try:
            current = _snapshot(app)
            current["ready"] = True
            current["batch_count"] = len(batch["items"])
            if batch["status"] in {"sending", "checking"}:
                current["sending"] = True
            with snapshot_lock:
                state.clear()
                state.update(current)
            for path, kind in list(temporary_uploads.items()):
                source = Path(path)
                if not source.exists():
                    temporary_uploads.pop(path, None)
                    continue
                active = any(
                    str(item.get("source", "")) == path
                    and item.get("status") not in {"변환 완료", "변환 실패"}
                    for item in app._document_jobs
                )
                if kind == "pdf" and not active:
                    source.unlink(missing_ok=True)
                    temporary_uploads.pop(path, None)
        except Exception:
            pass
        if not stop_event.is_set():
            root.after(350, refresh_state)

    root.after(0, refresh_state)
    root.mainloop()
    stop_event.set()
    for executor in batch_ocr_executors:
        executor.shutdown(wait=False, cancel_futures=True)
    server.shutdown()
    for path in upload_root.glob("*"):
        try:
            path.unlink()
        except OSError:
            pass
    try:
        upload_root.rmdir()
    except OSError:
        pass


def main() -> None:
    multiprocessing.freeze_support()
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "V3 HTML 화면 의존성이 없습니다. requirements.txt의 pywebview를 설치한 뒤 실행하세요."
        ) from exc

    from new_work.windows_lifecycle import register_current_user_autostart

    if sys.platform == "win32":
        try:
            register_current_user_autostart("NewWorkIdReviewV2")
        except OSError:
            pass

    port = _available_port()
    token = secrets.token_urlsafe(32)
    context = multiprocessing.get_context("spawn")
    worker = context.Process(
        target=_backend_process,
        args=(port, token),
        name="NewWorkSenderBackend",
        daemon=True,
    )
    worker.start()
    origin = f"http://127.0.0.1:{port}"
    health_url = f"{origin}/api/health?token={urllib.parse.quote(token)}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not worker.is_alive():
            raise RuntimeError("송신기 OCR 백엔드가 시작하지 못했습니다.")
        try:
            with urllib.request.urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    else:
        worker.terminate()
        worker.join(timeout=2)
        raise RuntimeError("송신기 화면 서버가 제한 시간 안에 준비되지 않았습니다.")

    window = webview.create_window(
        "신분증 확인 송신기",
        f"{origin}/?token={urllib.parse.quote(token)}",
        width=1540,
        height=1000,
        min_size=(1120, 760),
        background_color="#f4f7fb",
    )
    try:
        webview.start(debug=False)
    finally:
        try:
            request = urllib.request.Request(
                f"{origin}/api/shutdown",
                data=b"{}",
                headers={"Content-Type": "application/json", "X-NewWork-Token": token},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=1).close()
        except Exception:
            pass
        worker.join(timeout=3)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=2)
