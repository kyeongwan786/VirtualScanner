from __future__ import annotations

import base64
import json
import multiprocessing
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from PIL import Image


_HTML_PATH = Path(__file__).with_name("receiver_ui.html")


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _image_payload(path: Path, bounds: tuple[int, int]) -> bytes:
    with Image.open(path) as image:
        image.thumbnail(bounds, Image.Resampling.LANCZOS)
        output = BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=82, optimize=True)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _display_document_name(value: object) -> str:
    name = Path(str(value or "서류")).name
    return re.sub(r"^[0-9a-f]{32}_", "", name, flags=re.IGNORECASE)


def _parse_crop_bounds(value: object) -> tuple[int, int, int, int] | None:
    if not value:
        return None
    try:
        bounds = tuple(int(part.strip()) for part in str(value).split(","))
    except (TypeError, ValueError):
        return None
    if len(bounds) != 4:
        return None
    left, top, right, bottom = bounds
    if left < 0 or top < 0 or right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _fallback_crop_bounds(width: int, height: int, code: str) -> tuple[int, int, int, int] | None:
    crop_width, crop_height = (340, 406) if code == "1001" else (320, 420)
    crop_width, crop_height = min(crop_width, width), min(crop_height, height)
    if crop_width < 20 or crop_height < 20:
        return None
    center_x_ratio = 0.75 if code == "1001" else (
        0.25 if code in {"1101", "1102", "1201", "1302", "1401"} else 0.5
    )
    left = min(max(round(width * center_x_ratio) - crop_width // 2, 0), width - crop_width)
    top = min(max(round(height * 0.52) - crop_height // 2, 0), height - crop_height)
    return left, top, left + crop_width, top + crop_height


def _snapshot(
    app,
    image_cache: dict[str, tuple[int, int, str]],
    cache_lock: threading.Lock,
) -> dict:
    from new_work.ocr.id_types import ID_TYPES

    jobs = sorted(app._jobs.values(), key=lambda item: item["mtime"], reverse=True)
    selected_id = getattr(app, "_web_selected_job_id", "")
    if selected_id not in app._jobs:
        selected_id = ""
        app._web_selected_job_id = ""

    folder_rows = []
    for job in jobs:
        manifest = job["manifest"]
        documents = manifest.get("documents", [])
        if not isinstance(documents, list):
            documents = []
        identity_name = manifest.get("identity_image")
        identity_present = isinstance(identity_name, str) and bool(identity_name)
        if identity_present and documents:
            package_type = "신분증 + 서류"
        elif identity_present:
            package_type = "신분증만"
        else:
            package_type = "서류만"
        code = str(manifest.get("id_type_code", ""))
        values = manifest.get("reviewed_values", {})
        if isinstance(values, dict):
            code = str(values.get("kaitIdType") or code)
        detection = manifest.get("id_type_detection", {})
        if not isinstance(detection, dict):
            detection = {}
        id_type = ID_TYPES.get(code, {}).get("name") or detection.get("name") or "신분증"
        document_names = [
            _display_document_name(item.get("source_name") or item.get("label") or "서류")
            for item in documents if isinstance(item, dict)
        ]
        folder_label = (
            f"{id_type} · {document_names[0]}" if identity_present and document_names
            else id_type if identity_present
            else document_names[0] if document_names
            else "빈 접수"
        )
        applied_path = Path(job["job_dir"]) / "applied.json"
        confirmed = applied_path.exists()
        folder_rows.append({
            "job_id": job["job_id"],
            "carrier": manifest.get("carrier", ""),
            "received_at": datetime.fromtimestamp(job["mtime"]).strftime("%m-%d %H:%M:%S"),
            "received_label": datetime.fromtimestamp(job["mtime"]).strftime("%Y-%m-%d %H:%M"),
            "sort_time": job["mtime"],
            "package_type": package_type,
            "folder_label": folder_label,
            "document_names": document_names,
            "identity_present": identity_present,
            "identity_type": id_type,
            "identity_confirmed": confirmed,
            "document_count": len(documents),
            "page_count": sum(
                int(item.get("page_count", 0) or 0)
                for item in documents if isinstance(item, dict)
            ),
            "selected": job["job_id"] == selected_id,
        })

    detail = None
    if selected_id in app._jobs:
        job = app._jobs[selected_id]
        manifest = job["manifest"]
        identity_name = manifest.get("identity_image")
        identity_present = isinstance(identity_name, str) and bool(identity_name)
        code = str(manifest.get("id_type_code", ""))
        values = manifest.get("reviewed_values", {})
        if isinstance(values, dict):
            code = str(values.get("kaitIdType") or code)
        crop_bounds = _parse_crop_bounds(
            values.get("_idCropBounds") if isinstance(values, dict) else None
        )
        crop_is_fallback = False
        identity_dimensions = None
        if identity_present:
            try:
                image_path = app._job_file(job["job_dir"], identity_name)
                stamp = image_path.stat()
                cache_key = f"identity-info:{selected_id}"
                signature = (stamp.st_mtime_ns, stamp.st_size)
                with cache_lock:
                    cached = image_cache.get(cache_key)
                    raw_dimensions = cached[2] if cached and cached[:2] == signature else ""
                    if not raw_dimensions:
                        with Image.open(image_path) as source:
                            raw_dimensions = json.dumps([source.width, source.height])
                        image_cache[cache_key] = (*signature, raw_dimensions)
                identity_dimensions = json.loads(raw_dimensions)
                if crop_bounds is None:
                    crop_bounds = _fallback_crop_bounds(
                        int(identity_dimensions[0]), int(identity_dimensions[1]), code
                    )
                    crop_is_fallback = crop_bounds is not None
            except (OSError, TypeError, ValueError):
                identity_dimensions = None
        detection = manifest.get("id_type_detection", {})
        if not isinstance(detection, dict):
            detection = {}
        id_type = ID_TYPES.get(code, {}).get("name") or detection.get("name") or "신분증"
        doc_names = [
            _display_document_name(item.get("source_name") or item.get("label") or "서류")
            for item in manifest.get("documents", [])
            if isinstance(item, dict)
        ]
        folder_label = (
            f"{id_type} · {doc_names[0]}" if identity_present and doc_names
            else id_type if identity_present
            else doc_names[0] if doc_names
            else "빈 접수"
        )
        docs = []
        active_keys = app._ready_document_keys()
        for document in manifest.get("documents", []) if isinstance(manifest.get("documents"), list) else []:
            if not isinstance(document, dict):
                continue
            key = f"{selected_id}:{document.get('document_id', '')}"
            item = app._items.get(key)
            if item is None:
                continue
            if key in app._completed:
                status = "스캔 완료"
                lane = "completed"
            elif key in active_keys:
                status = "스캔 대기 중"
                lane = "scan"
            else:
                status = "스캔 서류 대기열"
                lane = "wait"
            pages = document.get("pages", [])
            docs.append({
                "key": key,
                "name": _display_document_name(document.get("source_name") or document.get("label") or "서류.pdf"),
                "page_count": int(document.get("page_count", len(pages)) or 0),
                "size": sum(
                    page.stat().st_size for relative in pages
                    for page in [Path(job["job_dir"]) / relative]
                    if page.is_file()
                ),
                "status": status,
                "lane": lane,
                "preview_url": "/api/preview?token=" + urllib.parse.quote(app._web_token)
                + "&key=" + urllib.parse.quote(key, safe=""),
            })
        marker_path = Path(job["job_dir"]) / "applied.json"
        detail = {
            "job_id": selected_id,
            "carrier": manifest.get("carrier", ""),
            "id_type_code": code,
            "id_type_options": [
                {"code": type_code, "name": item["name"]}
                for type_code, item in ID_TYPES.items()
            ],
            "reviewed_values": {
                key: str(values.get(key, ""))
                for key in (
                    "custNm", "ctzCorpNum", "addr", "isueDt",
                    "drvLicenIsueNum", "isuePlc", "veteranNum", "mskPos",
                )
            } if isinstance(values, dict) else {},
            "folder_label": folder_label,
            "received_at": datetime.fromtimestamp(job["mtime"]).strftime("%Y-%m-%d %H:%M:%S"),
            "package_type": "신분증 + 서류" if identity_present and docs else
                "신분증만" if identity_present else "서류만",
            "identity_present": identity_present,
            "identity_type": id_type,
            "identity_confirmed": marker_path.exists(),
            "identity_preview_url": (
                "/api/identity-preview?token=" + urllib.parse.quote(app._web_token)
                + "&job_id=" + urllib.parse.quote(selected_id, safe="")
                if identity_present else ""
            ),
            "identity_image_size": identity_dimensions,
            "id_crop_bounds": list(crop_bounds) if crop_bounds else None,
            "id_crop_is_fallback": crop_is_fallback,
            "image_count": sum(doc.get("page_count", 0) for doc in docs),
            "documents": docs,
        }
    return {
        "ready": True,
        "configured": all(app.config.get(key) for key in ("station_id", "relay_url", "token")),
        "station_id": app.config.get("station_id", ""),
        "settings": {key: app.config.get(key, "") for key in (
            "station_id", "relay_url", "work_path", "sk_report_path",
            "kt_database_path", "kt_image_directory", "scanner_ready_path",
        )},
        "token_configured": bool(app.config.get("token")),
        "status": app.status.get(),
        "progress": (
            app.receive_progress_label.get()
            if app._processing
            else ""
        ),
        "folder_count": len(folder_rows),
        "queue_count": len(app._scan_order),
        "wait_count": len(app._queue_order),
        "completed_count": len(app._completed),
        "folders": folder_rows,
        "detail": detail,
        "selected_job_id": selected_id,
    }


def _backend_process(port: int, token: str) -> None:
    import tkinter as tk

    from new_work.receiver_gui import ReceiverApp
    from new_work.webview_backend import hide_backend_application

    root = tk.Tk()
    app = ReceiverApp(root)
    hide_backend_application(root)
    app._web_token = token
    # The web UI opens a job only after the operator selects it from the list.
    app._web_selected_job_id = ""
    snapshot_lock = threading.Lock()
    state: dict = {"ready": False}
    image_cache: dict[str, tuple[int, int, str]] = {}
    stop_event = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        server_version = "NewWorkReceiverUI/1"

        def log_message(self, _format, *_args) -> None:
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
            if parsed.path in {"/", "/api/health"} and query.get("token", [""])[0] == token:
                if parsed.path == "/api/health":
                    self._reply(200, {"ok": True})
                else:
                    try:
                        self._reply(200, _HTML_PATH.read_bytes(), "text/html; charset=utf-8")
                    except OSError as exc:
                        self._reply(500, {"error": str(exc)})
                return
            if parsed.path == "/api/state" and self._authorized():
                with snapshot_lock:
                    self._reply(200, dict(state))
                return
            if parsed.path == "/api/preview" and query.get("token", [""])[0] == token:
                key = query.get("key", [""])[0]
                item = app._items.get(key)
                if item is None:
                    self._reply(404, {"error": "서류를 찾지 못했습니다."})
                    return
                try:
                    pages = item["document"].get("pages", [])
                    page_index = max(0, min(int(query.get("page", ["0"])[0]), len(pages) - 1))
                    path = app._job_file(item["job_dir"], str(pages[page_index]))
                    with Image.open(path) as image:
                        image.thumbnail((1500, 1100), Image.Resampling.LANCZOS)
                        output = BytesIO()
                        image.convert("RGB").save(output, format="JPEG", quality=88)
                    self._reply(200, output.getvalue(), "image/jpeg")
                except (OSError, TypeError, ValueError, IndexError) as exc:
                    self._reply(400, {"error": str(exc)})
                return
            if parsed.path == "/api/identity-preview" and query.get("token", [""])[0] == token:
                job_id = query.get("job_id", [""])[0]
                job = app._jobs.get(job_id)
                if job is None:
                    self._reply(404, {"error": "신분증 접수 폴더를 찾지 못했습니다."})
                    return
                try:
                    image_name = job["manifest"].get("identity_image")
                    if not isinstance(image_name, str) or not image_name:
                        raise ValueError("받은 신분증이 없습니다.")
                    image_path = app._job_file(job["job_dir"], image_name)
                    stamp = image_path.stat()
                    cache_key = f"identity:{job_id}"
                    signature = (stamp.st_mtime_ns, stamp.st_size)
                    with snapshot_lock:
                        cached = image_cache.get(cache_key)
                        payload = cached[2] if cached and cached[:2] == signature else ""
                        if not payload:
                            payload = _image_payload(image_path, (1200, 800))
                            image_cache[cache_key] = (*signature, payload)
                    self._reply(200, base64.b64decode(payload), "image/jpeg")
                except (OSError, TypeError, ValueError) as exc:
                    self._reply(400, {"error": str(exc)})
                return
            self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._authorized():
                self._reply(403, {"error": "unauthorized"})
                return
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/api/shutdown":
                self._reply(200, {"ok": True})
                root.after(0, root.destroy)
                stop_event.set()
                return
            if parsed.path != "/api/action":
                self._reply(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1024 * 1024:
                    raise ValueError("action payload too large")
                payload = json.loads(self.rfile.read(length) or b"{}")
                action = str(payload.get("action", ""))
                data = payload.get("data", {})
                if not isinstance(data, dict):
                    raise ValueError("invalid action data")
                value = self._dispatch(action, data)
                self._reply(200, {"ok": True, "result": value})
            except Exception as exc:
                self._reply(400, {"ok": False, "error": str(exc)})

        def _dispatch(self, action: str, data: dict) -> object:
            result = {"value": None, "error": None}
            completed = threading.Event()

            def run_action() -> None:
                try:
                    if action == "select_job":
                        job_id = str(data.get("job_id", ""))
                        if job_id not in app._jobs:
                            raise ValueError("접수 폴더를 찾지 못했습니다.")
                        app._web_selected_job_id = job_id
                        app._selected_job_id = job_id
                        app._show_job(job_id)
                    elif action == "delete_received_job":
                        job_id = str(data.get("job_id", ""))
                        app._delete_received_job(job_id)
                        with snapshot_lock:
                            image_cache.pop(f"identity:{job_id}", None)
                            image_cache.pop(f"identity-info:{job_id}", None)
                        result["value"] = {
                            "message": "접수 건을 삭제했습니다. Ctrl+Z로 되돌릴 수 있습니다."
                        }
                    elif action == "confirm_identity":
                        app._confirm_identity(str(data.get("job_id", "")))
                    elif action == "save_identity_edit":
                        raw_values = data.get("values", {})
                        if not isinstance(raw_values, dict):
                            raise ValueError("수정할 신분증 항목 형식이 올바르지 않습니다.")
                        allowed_fields = {
                            "custNm", "ctzCorpNum", "addr", "isueDt",
                            "drvLicenIsueNum", "isuePlc", "veteranNum", "mskPos",
                        }
                        values = {}
                        for key in allowed_fields:
                            value = raw_values.get(key, "")
                            if not isinstance(value, str) or len(value) > 2048:
                                raise ValueError("수정 항목 값이 너무 길거나 문자열이 아닙니다.")
                            values[key] = value
                        if "_idCropBounds" in raw_values:
                            crop_value = raw_values.get("_idCropBounds")
                            if not isinstance(crop_value, str) or len(crop_value) > 100:
                                raise ValueError("idCrop 영역 좌표가 올바르지 않습니다.")
                            bounds = _parse_crop_bounds(crop_value)
                            if bounds is None:
                                raise ValueError("idCrop 영역 좌표가 올바르지 않습니다.")
                            values["_idCropBounds"] = ",".join(str(item) for item in bounds)
                        app._save_web_identity_edit(
                            str(data.get("job_id", "")),
                            str(data.get("carrier", "")),
                            str(data.get("id_type_code", "")),
                            values,
                        )
                        result["value"] = {"message": "수정값을 저장하고 적용 중입니다."}
                    elif action in {"queue_document", "unqueue_document"}:
                        key = str(data.get("key", ""))
                        if key not in app._items:
                            raise ValueError("서류를 찾지 못했습니다.")
                        if action == "queue_document":
                            app._transfer_documents(
                                app.wait_tree, app.scan_tree,
                                app._queue_order, app._scan_order, [key],
                            )
                        else:
                            app._transfer_documents(
                                app.scan_tree, app.wait_tree,
                                app._scan_order, app._queue_order, [key],
                            )
                    elif action == "delete_document":
                        key = str(data.get("key", ""))
                        if key in app._scan_order:
                            tree = app.scan_tree
                        elif key in app._queue_order:
                            tree = app.wait_tree
                        else:
                            raise ValueError("삭제할 수 있는 서류를 찾지 못했습니다.")
                        tree.selection_set(key)
                        app._delete_selected_documents(tree, already_confirmed=True)
                    elif action == "undo_delete":
                        app._undo_document_delete()
                    elif action == "settings":
                        return True
                    elif action == "save_settings":
                        config = {key: str(data.get(key, "")).strip() for key in (
                            "station_id", "relay_url", "work_path", "sk_report_path",
                            "kt_database_path", "kt_image_directory", "scanner_ready_path",
                        )}
                        supplied_token = str(data.get("token", "")).strip()
                        config["token"] = supplied_token or str(app.config.get("token", ""))
                        if any(not value for value in config.values()):
                            raise ValueError("모든 설정을 입력하세요. 저장된 토큰은 비워두면 유지됩니다.")
                        parsed_relay = urllib.parse.urlsplit(config["relay_url"].rstrip("/"))
                        if (parsed_relay.scheme != "https" or not parsed_relay.netloc
                                or parsed_relay.path not in {"", "/"} or parsed_relay.query
                                or parsed_relay.fragment or len(config["token"]) < 32
                                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", config["station_id"])):
                            raise ValueError("Relay HTTPS 주소, 자리 ID, 자리 토큰(32자 이상)을 확인하세요.")
                        app.config_path.parent.mkdir(parents=True, exist_ok=True)
                        app.config_path.write_text(
                            json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                        )
                        app.config = config
                        app.status.set("Render Relay 수신 대기")
                        app._refresh_queue()
                        result["value"] = {"message": "Receiver 설정을 저장했습니다."}
                    elif action == "reset":
                        app._reset_received_data()
                    elif action == "retry":
                        app._retry_failed()
                    elif action == "refresh":
                        app._refresh_queue()
                    else:
                        raise ValueError("알 수 없는 화면 동작입니다.")
                except Exception as exc:
                    result["error"] = str(exc)
                finally:
                    completed.set()

            root.after(0, run_action)
            if not completed.wait(60):
                raise TimeoutError("화면 동작 응답 시간이 초과됐습니다.")
            if result["error"]:
                raise RuntimeError(str(result["error"]))
            return result["value"]

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="receiver-local-http").start()

    def refresh_state() -> None:
        try:
            current = _snapshot(app, image_cache, snapshot_lock)
            with snapshot_lock:
                state.clear()
                state.update(current)
        except Exception:
            pass
        if not stop_event.is_set():
            root.after(500, refresh_state)

    root.after(0, refresh_state)
    root.mainloop()
    server.shutdown()


def main() -> None:
    multiprocessing.freeze_support()
    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "Receiver CSS 화면 의존성이 없습니다. requirements_receiver.txt의 pywebview를 설치하세요."
        ) from exc

    port = _available_port()
    token = secrets.token_urlsafe(32)
    context = multiprocessing.get_context("spawn")
    worker = context.Process(
        target=_backend_process,
        args=(port, token),
        name="NewWorkReceiverBackend",
        daemon=True,
    )
    worker.start()
    origin = f"http://127.0.0.1:{port}"
    health_url = f"{origin}/api/health?token={urllib.parse.quote(token)}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not worker.is_alive():
            raise RuntimeError("Receiver 데이터 처리가 시작하지 못했습니다.")
        try:
            with urllib.request.urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    else:
        worker.terminate()
        worker.join(timeout=2)
        raise RuntimeError("Receiver 화면 서버가 제한 시간 안에 준비되지 않았습니다.")

    webview.create_window(
        "접수 자료함",
        f"{origin}/?token={urllib.parse.quote(token)}",
        width=1600,
        height=1000,
        min_size=(1180, 760),
        background_color="#f6f8fc",
    )
    try:
        webview.start(debug=False)
    finally:
        try:
            request = urllib.request.Request(
                f"{origin}/api/shutdown", data=b"{}",
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
