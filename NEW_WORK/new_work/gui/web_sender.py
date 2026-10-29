from __future__ import annotations

import base64
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
    stop_event = threading.Event()

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
                    self._dispatch("web_identity", {"path": str(temporary)})
                    temporary.unlink(missing_ok=True)
                else:
                    self._dispatch("web_pdf", {"path": str(temporary)})
                    temporary_uploads[str(temporary)] = "pdf"
                self._reply(200, {"ok": True, "name": safe_name})
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                self._reply(400, {"ok": False, "error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True

    def serve() -> None:
        server.serve_forever(poll_interval=0.2)

    threading.Thread(target=serve, daemon=True, name="sender-local-http").start()

    def refresh_state() -> None:
        try:
            current = _snapshot(app)
            current["ready"] = True
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
