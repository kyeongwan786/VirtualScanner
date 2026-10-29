from __future__ import annotations

import base64
import json
import multiprocessing
import re
import secrets
import shutil
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
from uuid import uuid4

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
    local_lock = threading.RLock()
    local_session: dict | None = None
    local_ocr = None
    local_ocr_lock = threading.Lock()

    def warm_local_ocr() -> None:
        """Move one-time OCR model initialization out of the first review."""
        nonlocal local_ocr
        try:
            from new_work.ocr.adapter import PaddleOcrAdapter

            with local_ocr_lock:
                if local_ocr is None:
                    local_ocr = PaddleOcrAdapter(device="cpu", ocr_version="PP-OCRv5")
                local_ocr.warmup()
                local_ocr.warmup_focused_recognizer()
        except Exception:
            # Keep the Receiver usable if the optional OCR runtime is missing;
            # run_local_ocr will surface the actionable error when requested.
            return

    def ensure_local_session() -> dict:
        nonlocal local_session
        with local_lock:
            if local_session is None:
                work_root = Path(app.config.get("work_path") or "").expanduser()
                if not str(work_root):
                    raise ValueError("Receiver 작업 폴더를 설정할 수 없습니다.")
                draft_id = uuid4().hex
                draft_dir = work_root / "local-prep" / draft_id
                draft_dir.mkdir(parents=True, exist_ok=False)
                local_session = {
                    "draft_id": draft_id,
                    "draft_dir": draft_dir,
                    "carrier": "SK",
                    "identity": None,
                    "documents": [],
                }
            return local_session

    def run_local_ocr(draft_id: str, identity_path: Path) -> None:
        nonlocal local_ocr
        try:
            from new_work.gui.app import recognize_identity_for_review
            from new_work.ocr.adapter import PaddleOcrAdapter
            from new_work.ocr.id_types import ID_TYPES
            from new_work.image.face_crop import id_portrait_bounds, id_portrait_fallback_bounds

            with local_ocr_lock:
                if local_ocr is None:
                    local_ocr = PaddleOcrAdapter(device="cpu", ocr_version="PP-OCRv5")
                result, detection, values = recognize_identity_for_review(local_ocr, identity_path)
            code = str(detection.code or "")
            with Image.open(identity_path) as opened:
                image = opened.convert("RGB")
                size = [image.width, image.height]
                bounds = id_portrait_bounds(image, recog_sub_cd=code, require_face=True) if code else None
                fallback = bounds is None
                if bounds is None and code:
                    bounds = id_portrait_fallback_bounds(image, recog_sub_cd=code)
            values["_idCropBounds"] = ",".join(str(value) for value in bounds) if bounds else ""
            with local_lock:
                current = local_session
                if not current or current.get("draft_id") != draft_id:
                    return
                identity = current["identity"]
                if not identity or Path(identity["path"]) != identity_path:
                    return
                identity.update({
                    "status": "완료", "values": values, "code": code,
                    "type_name": ID_TYPES.get(code, {}).get("name") or detection.name or "",
                    "type_options": [{"code": key, "name": item["name"]} for key, item in ID_TYPES.items()],
                    "image_size": size, "crop_bounds": list(bounds) if bounds else None,
                    "crop_is_fallback": fallback,
                    "ocr_engine": result.engine_name,
                    "ocr_seconds": round(result.elapsed_seconds, 1),
                })
        except Exception as exc:
            with local_lock:
                if local_session and local_session.get("draft_id") == draft_id and local_session.get("identity"):
                    local_session["identity"].update({"status": "오류", "error": str(exc)[:500]})

    def run_local_pdf(draft_id: str, document_id: str, pdf_path: Path) -> None:
        try:
            from new_work.pdf_render import render_pdf_to_jpegs

            output_dir = pdf_path.parent.parent / "documents" / document_id
            pages = render_pdf_to_jpegs(pdf_path, output_dir)
            with local_lock:
                if not local_session or local_session.get("draft_id") != draft_id:
                    return
                document = next((item for item in local_session["documents"] if item["document_id"] == document_id), None)
                if document:
                    document.update({"status": "완료", "pages": [path.relative_to(local_session["draft_dir"]).as_posix() for path in pages], "page_count": len(pages)})
        except Exception as exc:
            with local_lock:
                if local_session and local_session.get("draft_id") == draft_id:
                    document = next((item for item in local_session["documents"] if item["document_id"] == document_id), None)
                    if document:
                        document.update({"status": "오류", "error": str(exc)[:500]})

    def local_state() -> dict | None:
        from new_work.ocr.id_types import ID_TYPES

        with local_lock:
            if not local_session:
                return None
            identity = local_session.get("identity")
            applied_marker = {}
            if identity:
                try:
                    marker_value = json.loads(
                        (Path(local_session["draft_dir"]) / "applied.json").read_text(encoding="utf-8")
                    )
                    if isinstance(marker_value, dict):
                        applied_marker = marker_value
                except (OSError, json.JSONDecodeError):
                    pass
            return {
                "draft_id": local_session["draft_id"],
                "carrier": local_session["carrier"],
                "identity": ({key: value for key, value in identity.items() if key != "path"} | {"preview_url": "/api/local-identity-preview?token=" + urllib.parse.quote(token) + "&draft_id=" + urllib.parse.quote(local_session["draft_id"]) + "&image_id=" + urllib.parse.quote(Path(identity["path"]).name, safe=""), "type_options": [{"code": key, "name": item["name"]} for key, item in ID_TYPES.items()], "applied_carrier": applied_marker.get("carrier", ""), "applied_at": applied_marker.get("applied_at", "")} if identity else None),
                "documents": [{key: value for key, value in item.items() if key not in {"path", "pages"}} | {"pages": list(item.get("pages", [])), "page_count": item.get("page_count", 0), "preview_url": "/api/local-preview?token=" + urllib.parse.quote(token) + "&draft_id=" + urllib.parse.quote(local_session["draft_id"]) + "&document_id=" + urllib.parse.quote(item["document_id"])} for item in local_session["documents"]],
                "identity_busy": bool(identity and identity.get("status") == "처리 중"),
                "correction_busy": bool(app._correction_busy),
                "busy": bool(identity and identity.get("status") == "처리 중") or any(item.get("status") == "변환 중" for item in local_session["documents"]) or bool(app._correction_busy),
            }

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
                    current = dict(state)
                current["local"] = local_state()
                self._reply(200, current)
                return
            if parsed.path == "/api/local-identity-preview" and query.get("token", [""])[0] == token:
                with local_lock:
                    identity = local_session.get("identity") if local_session and local_session.get("draft_id") == query.get("draft_id", [""])[0] else None
                    path = Path(identity["path"]) if identity else None
                if not path or not path.is_file():
                    self._reply(404, {"error": "로컬 신분증 미리보기를 찾지 못했습니다."})
                    return
                try:
                    self._reply(200, path.read_bytes(), "image/jpeg" if path.suffix.casefold() in {".jpg", ".jpeg"} else "image/png")
                except OSError as exc:
                    self._reply(400, {"error": str(exc)})
                return
            if parsed.path == "/api/local-preview" and query.get("token", [""])[0] == token:
                with local_lock:
                    draft = local_session if local_session and local_session.get("draft_id") == query.get("draft_id", [""])[0] else None
                    document = next((item for item in draft["documents"] if item["document_id"] == query.get("document_id", [""])[0]), None) if draft else None
                    pages = list(document.get("pages", [])) if document else []
                    index = max(0, min(int(query.get("page", ["0"])[0]), len(pages) - 1)) if pages else -1
                    page_path = draft["draft_dir"] / pages[index] if index >= 0 else None
                if page_path is None or not page_path.is_file():
                    self._reply(404, {"error": "변환된 JPG를 찾지 못했습니다."})
                    return
                try:
                    with Image.open(page_path) as image:
                        image.thumbnail((1500, 1100), Image.Resampling.LANCZOS)
                        output = BytesIO()
                        image.convert("RGB").save(output, format="JPEG", quality=88)
                    self._reply(200, output.getvalue(), "image/jpeg")
                except OSError as exc:
                    self._reply(400, {"error": str(exc)})
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
            if parsed.path == "/api/local-upload":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 256 * 1024 * 1024:
                        raise ValueError("파일 크기는 256MB 이하여야 합니다.")
                    query = urllib.parse.parse_qs(parsed.query)
                    kind = query.get("kind", [""])[0]
                    filename = Path(query.get("name", ["자료"])[0]).name
                    suffix = Path(filename).suffix.casefold()
                    allowed = {"identity": {".jpg", ".jpeg", ".png"}, "pdf": {".pdf"}}
                    if kind not in allowed or suffix not in allowed[kind]:
                        raise ValueError("신분증은 JPG/PNG, 서류는 PDF 파일만 넣을 수 있습니다.")
                    payload = self.rfile.read(length)
                    if len(payload) != length:
                        raise ValueError("파일을 끝까지 읽지 못했습니다.")
                    session = ensure_local_session()
                    draft_id = session["draft_id"]
                    if kind == "identity":
                        with local_lock:
                            if session["identity"] and session["identity"].get("status") == "처리 중":
                                raise ValueError("신분증 OCR을 처리 중입니다.")
                            if session["identity"]:
                                Path(session["identity"]["path"]).unlink(missing_ok=True)
                                (session["draft_dir"] / "applied.json").unlink(missing_ok=True)
                                (session["draft_dir"] / "manifest.json").unlink(missing_ok=True)
                                app._jobs.pop(draft_id, None)
                            identity_dir = session["draft_dir"] / "identity"
                            identity_dir.mkdir(parents=True, exist_ok=True)
                            path = identity_dir / (uuid4().hex + suffix)
                            path.write_bytes(payload)
                            session["identity"] = {"path": str(path), "name": filename, "status": "처리 중", "values": {}, "code": "", "type_name": ""}
                        threading.Thread(target=run_local_ocr, args=(draft_id, path), daemon=True, name="receiver-local-ocr").start()
                    else:
                        with local_lock:
                            pdf_dir = session["draft_dir"] / "source-pdf"
                            pdf_dir.mkdir(parents=True, exist_ok=True)
                            document_id = uuid4().hex
                            path = pdf_dir / f"{document_id}{suffix}"
                            path.write_bytes(payload)
                            session["documents"].append({"document_id": document_id, "path": str(path), "name": filename, "status": "변환 중", "page_count": 0, "pages": []})
                        threading.Thread(target=run_local_pdf, args=(draft_id, document_id, path), daemon=True, name="receiver-local-pdf").start()
                    self._reply(200, {"ok": True})
                except Exception as exc:
                    self._reply(400, {"ok": False, "error": str(exc)})
                return
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
                nonlocal local_session
                try:
                    if action == "select_job":
                        job_id = str(data.get("job_id", ""))
                        if job_id not in app._jobs:
                            raise ValueError("접수 폴더를 찾지 못했습니다.")
                        app._web_selected_job_id = job_id
                        app._selected_job_id = job_id
                        app._show_job(job_id)
                    elif action == "local_carrier":
                        carrier = str(data.get("carrier", ""))
                        if carrier not in {"SK", "KT"}:
                            raise ValueError("포탈은 SK 또는 KT로 선택하세요.")
                        session = ensure_local_session()
                        with local_lock:
                            marker_path = Path(session["draft_dir"]) / "applied.json"
                            if marker_path.is_file():
                                try:
                                    applied = json.loads(marker_path.read_text(encoding="utf-8"))
                                except (OSError, json.JSONDecodeError) as exc:
                                    raise ValueError("기존 신분증 저장 상태를 확인할 수 없습니다.") from exc
                                if applied.get("carrier") != carrier:
                                    raise ValueError("이미 저장된 포탈은 변경할 수 없습니다. 수정은 기존 포탈로 저장하세요.")
                            session["carrier"] = carrier
                    elif action == "local_clear":
                        with local_lock:
                            old = local_session
                            if old and ((old.get("identity") or {}).get("status") == "처리 중" or any(item.get("status") == "변환 중" for item in old["documents"])):
                                raise ValueError("OCR 또는 PDF 변환이 끝난 뒤 초기화할 수 있습니다.")
                            if old:
                                shutil.rmtree(old["draft_dir"], ignore_errors=True)
                                app._jobs.pop(old["draft_id"], None)
                            local_session = None
                    elif action == "local_remove_document":
                        document_id = str(data.get("document_id", ""))
                        with local_lock:
                            session = local_session
                            document = next((item for item in session["documents"] if item["document_id"] == document_id), None) if session else None
                            if not document or document.get("status") == "변환 중":
                                raise ValueError("변환 중이거나 없는 PDF입니다.")
                            shutil.rmtree(Path(session["draft_dir"]) / "documents" / document_id, ignore_errors=True)
                            Path(document["path"]).unlink(missing_ok=True)
                            session["documents"].remove(document)
                    elif action == "local_remove_identity":
                        with local_lock:
                            session = local_session
                            identity = session.get("identity") if session else None
                            if not identity or identity.get("status") == "처리 중":
                                raise ValueError("OCR 처리 중이거나 신분증이 없습니다.")
                            Path(identity["path"]).unlink(missing_ok=True)
                            session["identity"] = None
                    elif action == "local_set_identity":
                        session = ensure_local_session()
                        values = data.get("values", {})
                        code = str(data.get("code", ""))
                        if not isinstance(values, dict) or len(values) > 20:
                            raise ValueError("OCR 수정값을 확인하세요.")
                        from new_work.ocr.id_types import ID_TYPES
                        if code not in ID_TYPES:
                            raise ValueError("신분증 종류를 선택하세요.")
                        with local_lock:
                            identity = session.get("identity")
                            if not identity or identity.get("status") == "처리 중":
                                raise ValueError("신분증 OCR이 끝난 뒤 저장할 수 있습니다.")
                            for key, value in values.items():
                                if not isinstance(key, str) or not isinstance(value, str) or len(value) > 2048:
                                    raise ValueError("OCR 항목 형식이 올바르지 않습니다.")
                            bounds = _parse_crop_bounds(values.get("_idCropBounds")) if values.get("_idCropBounds") else None
                            if values.get("_idCropBounds") and bounds is None:
                                raise ValueError("idCrop 좌표를 확인하세요.")
                            from new_work.image.face_crop import id_portrait_fallback_bounds, id_portrait_size
                            with Image.open(identity["path"]) as opened:
                                image_size = [opened.width, opened.height]
                                if bounds:
                                    if bounds[2] > opened.width or bounds[3] > opened.height:
                                        raise ValueError("idCrop 영역이 신분증 이미지 범위를 벗어났습니다.")
                                    expected = tuple(min(value, dimension) for value, dimension in zip(id_portrait_size(code), opened.size))
                                    if (bounds[2] - bounds[0], bounds[3] - bounds[1]) != expected:
                                        bounds = None
                                        values.pop("_idCropBounds", None)
                                used_fallback = identity.get("code") != code or bounds is None
                                if used_fallback:
                                    bounds = id_portrait_fallback_bounds(opened.convert("RGB"), recog_sub_cd=code)
                            if bounds:
                                values["_idCropBounds"] = ",".join(str(value) for value in bounds)
                            else:
                                values.pop("_idCropBounds", None)
                            identity.update({
                                "values": values,
                                "code": code,
                                "type_name": ID_TYPES[code]["name"],
                                "image_size": image_size,
                                "crop_bounds": list(bounds) if bounds else None,
                                "crop_is_fallback": used_fallback,
                            })
                    elif action == "local_apply_identity":
                        if app._correction_busy:
                            raise ValueError("다른 신분증 저장 작업이 진행 중입니다.")
                        session = ensure_local_session()
                        code = str(data.get("code", ""))
                        raw_values = data.get("values", {})
                        from new_work.ocr.id_types import ID_TYPES
                        if not isinstance(raw_values, dict) or code not in ID_TYPES:
                            raise ValueError("신분증 종류와 OCR 수정값을 확인하세요.")
                        allowed_fields = {"custNm", "ctzCorpNum", "addr", "isueDt", "drvLicenIsueNum", "isuePlc", "veteranNum", "mskPos", "_idCropBounds"}
                        if set(raw_values) - allowed_fields:
                            raise ValueError("지원하지 않는 OCR 항목이 포함되어 있습니다.")
                        values: dict[str, str] = {}
                        for key in allowed_fields:
                            value = raw_values.get(key, "")
                            if not isinstance(value, str) or len(value) > 2048:
                                raise ValueError("OCR 수정 항목 값이 너무 길거나 문자열이 아닙니다.")
                            if key in raw_values:
                                values[key] = value
                        with local_lock:
                            identity = session.get("identity")
                            if not identity or identity.get("status") == "처리 중":
                                raise ValueError("신분증 OCR이 끝난 뒤 저장할 수 있습니다.")
                            carrier = str(session.get("carrier", ""))
                            if carrier not in {"SK", "KT"}:
                                raise ValueError("저장 포탈을 SK 또는 KT로 선택하세요.")
                            identity_path = Path(identity["path"])
                            bounds = _parse_crop_bounds(values.get("_idCropBounds")) if values.get("_idCropBounds") else None
                            if values.get("_idCropBounds") and bounds is None:
                                raise ValueError("idCrop 좌표를 확인하세요.")
                            with Image.open(identity_path) as opened:
                                image = opened.convert("RGB")
                                image_size = [image.width, image.height]
                                if bounds and (bounds[2] > image.width or bounds[3] > image.height):
                                    raise ValueError("idCrop 영역이 신분증 이미지 범위를 벗어났습니다.")
                                from new_work.image.face_crop import id_portrait_fallback_bounds, id_portrait_size
                                expected = tuple(min(value, dimension) for value, dimension in zip(id_portrait_size(code), image.size))
                                if bounds and (bounds[2] - bounds[0], bounds[3] - bounds[1]) != expected:
                                    bounds = None
                                if identity.get("code") != code or bounds is None:
                                    bounds = id_portrait_fallback_bounds(image, recog_sub_cd=code)
                            if bounds:
                                values["_idCropBounds"] = ",".join(str(value) for value in bounds)
                            values.update({"kaitIdType": code, "recogSubCd": code, "idType": ID_TYPES[code]["type"], "nationClCd": "F" if code == "1401" else "I"})
                            identity.update({"values": values, "code": code, "type_name": ID_TYPES[code]["name"], "image_size": image_size, "crop_bounds": list(bounds) if bounds else None})
                            draft_id = session["draft_id"]
                            draft_dir = Path(session["draft_dir"])
                            previous_manifest = {}
                            try:
                                loaded = json.loads((draft_dir / "manifest.json").read_text(encoding="utf-8"))
                                if isinstance(loaded, dict):
                                    previous_manifest = loaded
                            except (OSError, json.JSONDecodeError):
                                pass
                            detection = {"name": ID_TYPES[code]["name"], "type_code": ID_TYPES[code]["type"], "candidate_codes": [code], "preferred_code": code}
                            manifest = {**previous_manifest, "job_id": draft_id, "station_id": app.config.get("station_id") or "local", "source": "local", "carrier": carrier, "identity_image": identity_path.relative_to(draft_dir).as_posix(), "id_type_code": code, "id_type_detection": detection, "reviewed_values": values, "documents": previous_manifest.get("documents", []), "created_at": previous_manifest.get("created_at") or datetime.now().isoformat(timespec="seconds")}
                            app._write_json_atomic(draft_dir / "manifest.json", manifest)
                            app._jobs[draft_id] = {"manifest": manifest, "job_dir": draft_dir}
                        error = app._apply_identity_correction(draft_id, draft_dir, manifest, values, code, carrier_override=carrier, web=True)
                        if error:
                            raise RuntimeError(error)
                        result["value"] = {"message": f"수정한 OCR과 idCrop을 {carrier}에 저장했습니다."}
                    elif action == "local_save":
                        with local_lock:
                            session = local_session
                            if not session:
                                raise ValueError("저장할 로컬 접수 자료가 없습니다.")
                            identity = session.get("identity")
                            documents = list(session["documents"])
                            if (identity and identity.get("status") == "처리 중") or any(item.get("status") == "변환 중" for item in documents):
                                raise ValueError("OCR과 PDF 변환이 끝난 뒤 받은 자료함에 등록하세요.")
                            if identity and identity.get("status") not in {"완료", "오류"}:
                                raise ValueError("신분증 OCR 오류를 확인하거나 신분증을 제거한 뒤 등록하세요.")
                            from new_work.ocr.id_types import ID_TYPES
                            if identity and str(identity.get("code", "")) not in ID_TYPES:
                                raise ValueError("신분증 종류를 선택하고 OCR 항목을 확인하세요.")
                            if not identity and not documents:
                                raise ValueError("신분증 이미지 또는 PDF를 추가하세요.")
                            if any(item.get("status") != "완료" for item in documents):
                                raise ValueError("변환에 실패한 PDF를 제거한 뒤 등록하세요.")
                            draft_id = session["draft_id"]
                            draft_dir = Path(session["draft_dir"])
                            job_id = uuid4().hex
                            job_dir = Path(app.config["work_path"]).expanduser() / "jobs" / job_id
                            job_dir.parent.mkdir(parents=True, exist_ok=True)
                            job_dir.mkdir(parents=False, exist_ok=False)
                            try:
                                identity_name = ""
                                values = {}
                                code = ""
                                detection = {}
                                if identity:
                                    identity_path = Path(identity["path"])
                                    identity_name = f"identity/{identity_path.name}"
                                    (job_dir / "identity").mkdir()
                                    shutil.copy2(identity_path, job_dir / identity_name)
                                    values = dict(identity.get("values") or {})
                                    code = str(identity.get("code", ""))
                                    from new_work.ocr.id_types import ID_TYPES
                                    values.update({"kaitIdType": code, "recogSubCd": code, "idType": ID_TYPES[code]["type"], "nationClCd": "F" if code == "1401" else "I"})
                                    detection = {"name": identity.get("type_name", ""), "type_code": values["idType"], "candidate_codes": [code], "preferred_code": code}
                                manifest_documents = []
                                for item in documents:
                                    pages = item.get("pages", [])
                                    target_root = job_dir / "documents" / item["document_id"]
                                    target_root.mkdir(parents=True, exist_ok=True)
                                    target_pages = []
                                    for relative in pages:
                                        source_page = draft_dir / relative
                                        target_page = target_root / Path(relative).name
                                        shutil.copy2(source_page, target_page)
                                        target_pages.append(target_page.relative_to(job_dir).as_posix())
                                    manifest_documents.append({"document_id": item["document_id"], "source_name": item["name"], "label": item["name"], "page_count": len(target_pages), "pages": target_pages})
                                manifest = {"job_id": job_id, "station_id": app.config.get("station_id") or "local", "source": "local", "carrier": session["carrier"], "identity_image": identity_name, "id_type_code": code, "id_type_detection": detection, "reviewed_values": values, "documents": manifest_documents, "created_at": datetime.now().isoformat(timespec="seconds")}
                                (job_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                                (job_dir / "received.json").write_text(json.dumps({"received_at": datetime.now().isoformat(timespec="seconds"), "source": "local"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                                applied_marker = draft_dir / "applied.json"
                                if identity and applied_marker.is_file():
                                    shutil.copy2(applied_marker, job_dir / "applied.json")
                            except Exception:
                                shutil.rmtree(job_dir, ignore_errors=True)
                                raise
                            shutil.rmtree(draft_dir, ignore_errors=True)
                            app._jobs.pop(draft_id, None)
                            local_session = None
                        app._refresh_queue()
                        app._web_selected_job_id = job_id
                        app._selected_job_id = job_id
                        app._show_job(job_id)
                        result["value"] = {"message": "이 PC에서 준비한 자료를 받은 자료함에 등록했습니다. 신분증 확인과 스캔 대기열 추가는 해당 접수 건에서 진행하세요.", "job_id": job_id}
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
    threading.Thread(target=warm_local_ocr, daemon=True, name="receiver-ocr-warmup").start()

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
