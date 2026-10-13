"""Drag-and-drop PDF/JPG sender for workstation scanner Receivers."""

from __future__ import annotations

import io
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote, urlsplit, urlunsplit

from PIL import Image
import pymupdf as fitz
from tkinterdnd2 import DND_FILES, TkinterDnD


APP_NAME = "Kyeongwan Scanner — 중앙 전송"
MAX_PAGE_BYTES = 40 * 1024 * 1024
MAX_PAGES = 100
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
APP_CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "KyeongwanScanner"
STATIONS_FILE = APP_CONFIG_DIR / "stations.json"


class TransferError(RuntimeError):
    pass


class TransferCancelled(TransferError):
    pass


def load_stations() -> list[dict]:
    if not STATIONS_FILE.is_file():
        APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATIONS_FILE.write_text(
            json.dumps({"stations": []}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    try:
        raw = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TransferError(f"자리 목록을 읽지 못했습니다: {STATIONS_FILE}: {error}") from error
    stations = raw.get("stations") if isinstance(raw, dict) else None
    if not isinstance(stations, list):
        raise TransferError("stations.json에는 stations 배열이 있어야 합니다.")
    valid = []
    for station in stations:
        if not isinstance(station, dict):
            continue
        station_id = station.get("station_id")
        label = station.get("label", station_id)
        url = station.get("url")
        token = station.get("token")
        if not all(isinstance(value, str) and value for value in (station_id, label, url, token)):
            continue
        if not JOB_ID_RE.fullmatch(station_id) or len(token) < 32:
            continue
        parsed = urlsplit(url.rstrip("/"))
        if parsed.scheme != "http" or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            continue
        valid.append({
            "station_id": station_id,
            "label": label,
            "url": urlunsplit((parsed.scheme, parsed.netloc, "", "", "")),
            "token": token,
        })
    return valid


def request_json(url: str, method: str = "GET", token: str | None = None,
                 body: bytes | None = None, content_type: str | None = None,
                 timeout: float = 40) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Receiver-Token"] = token
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, data = response.status, response.read(64 * 1024)
    except urllib.error.HTTPError as error:
        status, data = error.code, error.read(64 * 1024)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TransferError(f"Receiver 연결 오류: {error}") from error
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransferError(f"Receiver 응답을 해석하지 못했습니다. HTTP {status}") from error
    if not isinstance(payload, dict):
        raise TransferError(f"Receiver 응답 형식이 올바르지 않습니다. HTTP {status}")
    return status, payload


def natural_name_key(path: Path):
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold())
                 for part in re.split(r"(\d+)", path.name))


def wait_until_stable(path: Path, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    previous = None
    unchanged_since = None
    while time.monotonic() < deadline:
        try:
            stat = path.stat()
            current = (stat.st_size, stat.st_mtime_ns)
        except OSError as error:
            raise TransferError(f"파일을 읽을 수 없습니다: {path}: {error}") from error
        if stat.st_size > 0 and current == previous:
            if unchanged_since is not None and time.monotonic() - unchanged_since >= 1.5:
                return
        else:
            unchanged_since = time.monotonic() if stat.st_size > 0 else None
        previous = current
        time.sleep(0.4)
    raise TransferError(f"파일이 저장 중이거나 열리지 않습니다. 다운로드가 끝난 뒤 다시 드롭하세요: {path.name}")


def collect_files(paths: list[Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for original in paths:
        path = original.expanduser().resolve()
        candidates = path.rglob("*") if path.is_dir() else (path,)
        for item in candidates:
            if item.is_file() and item.suffix.casefold() in (".pdf", ".jpg", ".jpeg"):
                found[str(item).casefold()] = item
    return sorted(found.values(), key=natural_name_key)


def render_pdf(source: Path, dpi: int, quality: int):
    document = fitz.open(source)
    pdfium_document = None
    try:
        if document.needs_pass:
            raise TransferError(f"암호가 설정된 PDF는 변환할 수 없습니다: {source.name}")
        if not 1 <= len(document) <= MAX_PAGES:
            raise TransferError(f"PDF 페이지 수는 1~{MAX_PAGES}장이어야 합니다: {source.name}")
        scale = dpi / 72.0
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            if pixmap.is_unicolor:
                if pdfium_document is None:
                    import pypdfium2 as pdfium
                    pdfium_document = pdfium.PdfDocument(str(source))
                image = pdfium_document[index].render(scale=scale).to_pil().convert("RGB")
                if all(low == high for low, high in image.getextrema()):
                    raise TransferError(f"PDF {index + 1}페이지가 빈 이미지로 렌더링되어 전송을 중단했습니다.")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=False)
            content = output.getvalue()
            if not content or len(content) > MAX_PAGE_BYTES:
                raise TransferError(f"변환한 페이지가 비었거나 40 MiB를 초과합니다: {source.name}, {index + 1}페이지")
            yield index + 1, content
    finally:
        if pdfium_document is not None:
            pdfium_document.close()
        document.close()


def request_job_cancellation(station: dict, job_id: str) -> dict:
    url = f"{station['url']}/v1/jobs/{quote(job_id, safe='')}/cancel"
    status, payload = request_json(url, "DELETE", station["token"], timeout=5)
    if status == 200:
        return payload
    if status == 409:
        raise TransferCancelled(
            f"취소 요청 시점에 작업이 이미 준비 상태가 됐습니다. 작업 ID: {job_id}; {payload.get('error', '')}"
        )
    raise TransferError(f"Receiver가 취소를 확인하지 않았습니다 (HTTP {status}): {payload}")


def stop_if_cancelled(cancel_event: threading.Event, station: dict, job_id: str) -> None:
    if not cancel_event.is_set():
        return
    try:
        request_job_cancellation(station, job_id)
    except TransferCancelled:
        raise
    except TransferError as error:
        raise TransferCancelled(
            f"취소 요청을 보냈지만 Receiver의 보관 상태를 확인하지 못했습니다. "
            f"Receiver 연결을 확인하세요. 작업 ID: {job_id}; {error}"
        ) from error
    raise TransferCancelled(f"전송을 취소했습니다. 수신된 데이터는 삭제하지 않고 Receiver에 보관했습니다. 작업 ID: {job_id}")


def send_page(station: dict, job_id: str, page_number: int, content: bytes,
              cancel_event: threading.Event) -> None:
    url = f"{station['url']}/v1/jobs/{quote(job_id, safe='')}/pages/{page_number}"
    last_error = None
    for attempt in range(3):
        stop_if_cancelled(cancel_event, station, job_id)
        try:
            status, payload = request_json(url, "PUT", station["token"], content, "image/jpeg")
        except TransferError as error:
            last_error = error
            if attempt < 2:
                time.sleep(1 << attempt)
                continue
            raise
        if status in (200, 201):
            return
        raise TransferError(f"{station['label']} Receiver가 {page_number}페이지를 거부했습니다: {payload}")
    raise last_error or TransferError("페이지 전송에 실패했습니다.")


def send_one(source: Path, station: dict, dpi: int, quality: int, progress,
             cancel_event: threading.Event) -> str:
    job_id = uuid.uuid4().hex
    stop_if_cancelled(cancel_event, station, job_id)
    progress(f"작업 ID: {job_id} ({source.name})")
    status, health = request_json(station["url"] + "/health")
    if status != 200 or health.get("status") != "ready":
        raise TransferError(f"{station['label']} Receiver가 준비되지 않았습니다: {health}")
    if health.get("station_id") != station["station_id"]:
        raise TransferError(
            f"자리 불일치: 설정은 {station['station_id']}인데 Receiver는 {health.get('station_id')}입니다."
        )

    if source.suffix.casefold() == ".pdf":
        wait_until_stable(source)
        document = fitz.open(source)
        page_count = len(document)
        document.close()
        if not 1 <= page_count <= MAX_PAGES:
            raise TransferError(f"PDF 페이지 수는 1~{MAX_PAGES}장이어야 합니다: {source.name}")
        page_iter = render_pdf(source, dpi, quality)
    else:
        page_count = 1
        def jpg_page():
            content = source.read_bytes()
            if not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9"):
                raise TransferError(f"올바른 JPG 파일이 아닙니다: {source.name}")
            if len(content) > MAX_PAGE_BYTES:
                raise TransferError(f"JPG가 40 MiB를 초과합니다: {source.name}")
            yield 1, content
        page_iter = jpg_page()

    for number, content in page_iter:
        send_page(station, job_id, number, content, cancel_event)
        progress(f"{source.name}: {number}/{page_count} 페이지 전송")

    stop_if_cancelled(cancel_event, station, job_id)
    source_dpi = dpi if source.suffix.casefold() == ".pdf" else 175
    metadata = json.dumps(
        {"page_count": page_count, "source_name": source.name, "dpi": source_dpi}, ensure_ascii=False
    ).encode("utf-8")
    last_error = None
    for attempt in range(3):
        try:
            status, result = request_json(
                f"{station['url']}/v1/jobs/{quote(job_id, safe='')}/complete",
                "POST", station["token"], metadata, "application/json",
            )
            break
        except TransferError as error:
            last_error = error
            if attempt == 2:
                raise TransferError(f"{error} (작업 ID: {job_id})") from error
            time.sleep(1 << attempt)
    else:
        raise last_error or TransferError(f"작업 완료 요청 실패 (작업 ID: {job_id})")
    if status not in (200, 201):
        raise TransferError(f"Receiver가 작업 완료를 확인하지 않았습니다: {result}")
    return job_id


class CentralSenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("760x580")
        self.root.minsize(640, 480)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        self.cancel_event: threading.Event | None = None
        self.stations = []
        self.selection = tk.StringVar()
        self.status = tk.StringVar(value="PDF 또는 JPG를 드롭하고 접수 자리를 선택하세요.")
        self._build()
        self._reload_stations()
        self.root.after(200, self._poll_events)

    def _build(self):
        ttk.Label(self.root, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=18, pady=(16, 5))
        ttk.Label(
            self.root,
            text="PDF를 페이지별 JPG로 변환해 선택한 자리의 Receiver 폴더로 보냅니다. 원본 PDF는 변경하거나 삭제하지 않습니다.",
            wraplength=710,
        ).pack(anchor="w", padx=19, pady=(0, 14))
        row = ttk.Frame(self.root, padding=(18, 0))
        row.pack(fill="x")
        ttk.Label(row, text="접수 자리").pack(side="left")
        self.station_box = ttk.Combobox(row, textvariable=self.selection, state="readonly", width=38)
        self.station_box.pack(side="left", padx=8)
        ttk.Button(row, text="자리 목록 열기", command=self._open_station_file).pack(side="left")
        ttk.Button(row, text="다시 불러오기", command=self._reload_stations).pack(side="left", padx=6)
        options = ttk.Frame(self.root, padding=(18, 10))
        options.pack(fill="x")
        ttk.Label(options, text="PDF 해상도 DPI").pack(side="left")
        self.dpi = tk.StringVar(value="175")
        ttk.Spinbox(options, from_=72, to=600, increment=25, textvariable=self.dpi, width=8).pack(side="left", padx=(6, 20))
        ttk.Label(options, text="JPG 품질").pack(side="left")
        self.quality = tk.StringVar(value="85")
        ttk.Spinbox(options, from_=60, to=100, increment=5, textvariable=self.quality, width=7).pack(side="left", padx=6)
        self.dropbox = tk.Label(
            self.root,
            text="텔레그램에서 받은 PDF를 여기로 끌어놓으세요\n여러 파일 또는 폴더도 선택할 수 있습니다",
            relief="ridge", borderwidth=2, height=8, bg="#f2f5f9", font=("Segoe UI", 12),
        )
        self.dropbox.pack(fill="both", expand=True, padx=18, pady=(8, 10))
        self.dropbox.drop_target_register(DND_FILES)
        self.dropbox.dnd_bind("<<Drop>>", self._on_drop)
        buttons = ttk.Frame(self.root, padding=(18, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="파일 선택…", command=self._choose_files).pack(side="left")
        ttk.Button(buttons, text="폴더 선택…", command=self._choose_folder).pack(side="left", padx=7)
        self.cancel_button = ttk.Button(buttons, text="전송 취소", command=self._cancel_transfer, state="disabled")
        self.cancel_button.pack(side="right")
        self.log = tk.Listbox(self.root, height=8)
        self.log.pack(fill="both", expand=True, padx=18, pady=10)
        ttk.Label(self.root, textvariable=self.status, wraplength=710).pack(anchor="w", padx=18, pady=(0, 14))

    def _reload_stations(self):
        try:
            self.stations = load_stations()
        except TransferError as error:
            messagebox.showerror(APP_NAME, str(error), parent=self.root)
            self.stations = []
        self.station_box["values"] = [f"{item['label']} ({item['station_id']})" for item in self.stations]
        if self.stations:
            self.station_box.current(0)
            self.status.set(f"접수 자리 {len(self.stations)}개를 불러왔습니다. 자리 목록: {STATIONS_FILE}")
        else:
            self.selection.set("")
            self.status.set(f"자리 목록을 설정하세요: {STATIONS_FILE}")

    def _open_station_file(self):
        APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not STATIONS_FILE.exists():
            STATIONS_FILE.write_text(json.dumps({"stations": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.startfile(str(STATIONS_FILE))
        except (AttributeError, OSError):
            messagebox.showinfo(APP_NAME, f"이 파일을 편집한 뒤 앱을 다시 여세요:\n{STATIONS_FILE}", parent=self.root)

    def _selected_station(self):
        index = self.station_box.current()
        return self.stations[index] if 0 <= index < len(self.stations) else None

    def _on_drop(self, event):
        paths = [Path(value) for value in self.root.tk.splitlist(event.data)]
        self._queue_paths(paths)

    def _choose_files(self):
        selected = filedialog.askopenfilenames(filetypes=[("PDF / JPG", "*.pdf *.jpg *.jpeg"), ("All files", "*.*")])
        self._queue_paths([Path(value) for value in selected])

    def _choose_folder(self):
        selected = filedialog.askdirectory()
        if selected:
            self._queue_paths([Path(selected)])

    def _queue_paths(self, paths: list[Path]):
        if self.busy:
            messagebox.showinfo(APP_NAME, "현재 전송이 끝난 뒤 다시 추가해 주세요.", parent=self.root)
            return
        station = self._selected_station()
        if station is None:
            messagebox.showerror(APP_NAME, f"먼저 자리 목록을 설정하세요.\n{STATIONS_FILE}", parent=self.root)
            return
        try:
            dpi_value, quality_value = int(self.dpi.get()), int(self.quality.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "DPI와 JPG 품질은 숫자로 입력하세요.", parent=self.root)
            return
        if not 72 <= dpi_value <= 600 or not 60 <= quality_value <= 100:
            messagebox.showerror(APP_NAME, "DPI는 72~600, JPG 품질은 60~100 범위로 설정하세요.", parent=self.root)
            return
        files = collect_files(paths)
        if not files:
            messagebox.showinfo(APP_NAME, "선택한 위치에서 PDF/JPG 파일을 찾지 못했습니다.", parent=self.root)
            return
        self.busy = True
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self.cancel_button.configure(state="normal")
        self.status.set(f"{station['label']}로 {len(files)}개 파일을 보내는 중입니다…")

        def worker():
            try:
                for source in files:
                    if cancel_event.is_set():
                        raise TransferCancelled("남은 파일 전송을 취소했습니다.")
                    self.events.put(("status", f"처리 중: {source.name}"))
                    job_id = send_one(source, station, dpi_value, quality_value,
                                      lambda message: self.events.put(("log", message)) if message.startswith("작업 ID:")
                                      else self.events.put(("status", message)), cancel_event)
                    self.events.put(("log", f"완료 — {source.name} → {station['label']} / 작업 {job_id}"))
                self.events.put(("done", f"{len(files)}개 파일 전송 완료: {station['label']}"))
            except TransferCancelled as error:
                self.events.put(("cancelled", str(error)))
            except Exception as error:
                self.events.put(("error", str(error)))

        threading.Thread(target=worker, name="central-scan-sender", daemon=True).start()

    def _cancel_transfer(self):
        if self.cancel_event is None or self.cancel_event.is_set():
            return
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("취소 요청 중입니다. 현재 페이지 전송이 끝나면 Receiver에 취소 상태를 기록합니다…")

    def _poll_events(self):
        try:
            while True:
                kind, message = self.events.get_nowait()
                if kind == "log":
                    self.log.insert("end", message)
                    self.log.see("end")
                elif kind == "status":
                    self.status.set(message)
                elif kind == "done":
                    self.busy = False
                    self.cancel_event = None
                    self.cancel_button.configure(state="disabled")
                    self.status.set(message)
                elif kind == "cancelled":
                    self.busy = False
                    self.cancel_event = None
                    self.cancel_button.configure(state="disabled")
                    self.status.set(message)
                    self.log.insert("end", message)
                    self.log.see("end")
                elif kind == "error":
                    self.busy = False
                    self.cancel_event = None
                    self.cancel_button.configure(state="disabled")
                    self.status.set("전송이 중단됐습니다. 이미 전송된 원본 PDF/JPG는 삭제하지 않았습니다.")
                    messagebox.showerror(APP_NAME, message, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_events)


def main():
    root = TkinterDnD.Tk()
    CentralSenderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
