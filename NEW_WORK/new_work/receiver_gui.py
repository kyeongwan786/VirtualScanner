from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tkinter as tk
import unicodedata
from threading import Event, Thread
from time import monotonic
from datetime import datetime
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlsplit
from uuid import uuid4

from PIL import Image, ImageTk

from new_work.carrier.kt import default_kt_database_path, default_kt_image_directory, save_kt_submission
from new_work.domain import OcrResult
from new_work.ocr.id_types import ID_TYPES, IdTypeDetection
from new_work.ocr.result_writer import default_result_path, write_ocr_result
from new_work.pdf_render import default_job_directory
from new_work.relay_client import acknowledge_work_package, receive_work_package


def default_receiver_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "NEW_WORK" / "receiver.json"


def default_receiver_config() -> dict[str, str]:
    if os.name == "nt":
        program_data = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        ready = program_data / "KyeongwanScanner" / "VirtualSource" / "ready"
    else:
        ready = Path.home() / ".local/share/kyeongwan-scanner/receiver/ready"
    return {
        "station_id": "",
        "relay_url": "",
        "token": "",
        "work_path": str(default_job_directory().parent / "receiver"),
        "sk_report_path": str(default_result_path()),
        "kt_database_path": str(default_kt_database_path()),
        "kt_image_directory": str(default_kt_image_directory()),
        "scanner_ready_path": str(ready),
    }


def _safe_package_path(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name:
        raise ValueError("작업 패키지에 잘못된 경로가 있습니다.")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise ValueError("작업 패키지 경로가 허용 범위를 벗어났습니다.")
    return path


def _display_text(value: object) -> str:
    return unicodedata.normalize("NFC", str(value))


class DocumentPreviewDialog:
    def __init__(self, app: "ReceiverApp", item: dict) -> None:
        self.app = app
        self.item = item
        document = item["document"]
        self.pages = [
            app._job_file(item["job_dir"], str(relative))
            for relative in document.get("pages", [])
        ]
        self.index = 0
        self.photo: ImageTk.PhotoImage | None = None
        self.window = tk.Toplevel(app.root)
        self.window.title(f"서류 미리보기 · {_display_text(document.get('label', '서류'))}")
        self.window.geometry("1120x850")
        self.window.minsize(800, 620)
        self.window.transient(app.root)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.window, padding=10)
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(toolbar, text="← 목록으로", command=self._close).pack(side="left")
        self.title_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.title_var, style="Title.TLabel").pack(side="left", padx=16)
        ttk.Button(toolbar, text="이전 페이지", command=lambda: self._show(-1)).pack(side="right")
        ttk.Button(toolbar, text="다음 페이지", command=lambda: self._show(1)).pack(side="right", padx=6)

        self.image_label = tk.Label(
            self.window, text="이미지를 불러오는 중", anchor="center",
            background="#202938", foreground="white",
        )
        self.image_label.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.image_label.bind("<Configure>", self._resize_current)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        app._preview_dialogs.add(self)
        self._show(0)

    def _close(self) -> None:
        self.pages.clear()
        self.photo = None
        self.item = {}
        self.image_label.configure(image="", text="")
        self.app._preview_dialogs.discard(self)
        self.window.destroy()

    def _show(self, offset: int) -> None:
        if not self.pages:
            self.image_label.configure(image="", text="변환된 JPG 페이지가 없습니다")
            return
        self.index = max(0, min(len(self.pages) - 1, self.index + offset))
        try:
            with Image.open(self.pages[self.index]) as image:
                self._render_image(image)
            self.title_var.set(
                f"{_display_text(self.item['document'].get('label', '서류'))} · "
                f"페이지 {self.index + 1} / {len(self.pages)}"
            )
        except OSError as exc:
            self.image_label.configure(image="", text=f"페이지를 열지 못했습니다: {exc}")

    def _resize_current(self, _event=None) -> None:
        if self.pages:
            try:
                with Image.open(self.pages[self.index]) as image:
                    self._render_image(image)
            except OSError:
                return

    def _render_image(self, image: Image.Image) -> None:
        width = max(100, self.image_label.winfo_width() - 24)
        height = max(100, self.image_label.winfo_height() - 24)
        preview = image.convert("RGB")
        preview.thumbnail((width, height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(preview)
        self.image_label.configure(image=self.photo, text="")


class ReceiverApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("NEW_WORK Receiver")
        self.root.geometry("1420x900")
        self.root.minsize(1120, 720)
        self.config_path = default_receiver_config_path()
        self.config = default_receiver_config()
        if self.config_path.exists():
            try:
                self.config.update(json.loads(self.config_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                messagebox.showerror("Receiver 설정 오류", str(exc), parent=root)
        self._processing = False
        self._retry_after = 0.0
        self._photo: ImageTk.PhotoImage | None = None
        self._items: dict[str, dict] = {}
        self._jobs: dict[str, dict] = {}
        self._job_labels: dict[str, str] = {}
        self._queue_order: list[str] = []
        self._scan_order: list[str] = []
        self._completed: set[str] = set()
        self._document_undo_stack: list[dict] = []
        self._active: dict | None = None
        self._drag_start: tuple[ttk.Treeview, str] | None = None
        self._preview_dialogs: set[DocumentPreviewDialog] = set()
        self._clearing_received = False
        self._cancel_receive = Event()
        self._pending_received_reset: tuple[Path, Path | None, int, int, int] | None = None
        self._build()
        self._load_queue_state()
        self.root.bind_all("<Control-z>", self._undo_document_delete)
        if not all(self.config.get(key) for key in ("station_id", "relay_url", "token")):
            self.status.set("Receiver 설정 필요 · Relay 주소, 자리 ID, 자리 토큰을 지정하세요")
        self._refresh_queue()
        self.root.after(1000, self._poll_inbox)

    def _build(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        font_family = "Malgun Gothic" if os.name == "nt" else "Apple SD Gothic Neo"
        style.configure("TLabel", font=(font_family, 10))
        style.configure("Title.TLabel", font=(font_family, 13, "bold"))
        style.configure("Treeview", font=(font_family, 10), rowheight=30)
        style.configure("Treeview.Heading", font=(font_family, 10, "bold"))
        style.configure("TButton", font=(font_family, 10), padding=(8, 5))
        style.configure("TLabelframe.Label", font=(font_family, 10, "bold"))

        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(shell)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="NEW_WORK Receiver", style="Title.TLabel").pack(side="left", padx=(0, 18))
        ttk.Button(toolbar, text="Receiver 설정", command=self._edit_config).pack(side="left")
        ttk.Button(toolbar, text="새로고침", command=self._refresh_queue).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Relay 다시 확인", command=self._retry_failed).pack(side="left")
        ttk.Button(toolbar, text="받은 자료 초기화", command=self._reset_received_data).pack(
            side="left", padx=(6, 0)
        )
        self.status = tk.StringVar(value="수신 대기 중")
        ttk.Label(toolbar, textvariable=self.status).pack(side="left", padx=14)

        self.notebook = ttk.Notebook(shell)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self._build_identity_tab()
        self._build_documents_tab()

    def _build_identity_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        tab.columnconfigure(0, weight=3)
        tab.columnconfigure(1, weight=2)
        tab.rowconfigure(0, weight=1)
        self.notebook.add(tab, text="신분증 / 접수 결과")

        preview_frame = ttk.LabelFrame(tab, text="수신된 신분증", padding=8)
        preview_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        self.preview = tk.Label(
            preview_frame, text="접수 작업을 선택하면 신분증이 크게 표시됩니다",
            anchor="center", justify="center", background="#FFFFFF", foreground="#667085",
        )
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._resize_identity_preview)

        result_frame = ttk.LabelFrame(tab, text="중앙 OCR 접수 결과", padding=12)
        result_frame.grid(row=0, column=1, sticky="nsew")
        result_frame.columnconfigure(1, weight=1)
        result_frame.rowconfigure(1, weight=1)
        ttk.Label(result_frame, text="접수 작업").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 10))
        self.job_box = ttk.Combobox(result_frame, state="readonly")
        self.job_box.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        self.job_box.bind("<<ComboboxSelected>>", self._select_job)
        fields = ttk.Frame(result_frame)
        fields.grid(row=1, column=0, columnspan=2, sticky="new")
        fields.columnconfigure(1, weight=1)
        self.identity_vars: dict[str, tk.StringVar] = {}
        field_specs = (
            ("id_type", "신분증 종류"), ("custNm", "이름"),
            ("ctzCorpNum", "주민등록번호 / 등록번호"), ("addr", "주소"),
            ("isueDt", "발급일자"), ("drvLicenIsueNum", "운전면허번호"),
            ("isuePlc", "발급기관"), ("veteranNum", "보훈번호"),
        )
        for row, (key, label) in enumerate(field_specs):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
            variable = tk.StringVar(value="")
            self.identity_vars[key] = variable
            entry = ttk.Entry(fields, textvariable=variable, state="readonly")
            entry.grid(row=row, column=1, sticky="ew", pady=6, ipady=4)
        self.identity_hint = tk.StringVar(value="중앙 PC에서 검토한 OCR 값을 표시합니다.")
        ttk.Label(result_frame, textvariable=self.identity_hint, wraplength=430).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(14, 0)
        )

    def _build_documents_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        self.notebook.add(tab, text="서류 스캔 대기열")
        header = ttk.Frame(tab)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="PDF 하나가 서류 폴더 하나입니다. 더블클릭해 미리 보고, 드래그하거나 버튼으로 두 목록 사이에 옮기세요.",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="서류 목록 초기화", command=self._reset_document_lists).grid(
            row=0, column=1, sticky="e", padx=(8, 0)
        )
        ttk.Button(header, text="삭제 취소 (Ctrl+Z)", command=self._undo_document_delete).grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )
        panes = ttk.Panedwindow(tab, orient="horizontal")
        panes.grid(row=1, column=0, sticky="nsew")
        self.scan_frame, self.wait_frame = ttk.LabelFrame(panes, text="스캔할 서류", padding=8), ttk.LabelFrame(panes, text="스캔 서류 대기열", padding=8)
        panes.add(self.scan_frame, weight=1)
        panes.add(self.wait_frame, weight=1)
        self.scan_frame.columnconfigure(0, weight=1)
        self.scan_frame.rowconfigure(0, weight=1)
        self.wait_frame.columnconfigure(0, weight=1)
        self.wait_frame.rowconfigure(0, weight=1)
        self.scan_tree = self._make_document_tree(self.scan_frame, 0)
        self.wait_tree = self._make_document_tree(self.wait_frame, 0)
        self.scan_tree.bind("<Double-1>", lambda _event: self._preview_selected(self.scan_tree))
        self.wait_tree.bind("<Double-1>", lambda _event: self._preview_selected(self.wait_tree))
        for tree in (self.scan_tree, self.wait_tree):
            tree.bind("<Delete>", lambda _event, target=tree: self._delete_selected_documents(target))
            tree.bind("<Control-z>", self._undo_document_delete)
            tree.bind("<Control-Z>", self._undo_document_delete)
            tree.bind("<ButtonPress-1>", self._drag_press, add="+")
            tree.bind("<ButtonRelease-1>", self._drag_release, add="+")
        scan_controls = ttk.Frame(self.scan_frame)
        scan_controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(scan_controls, text="위로", command=lambda: self._move_selected(self.scan_tree, self._scan_order, -1)).pack(side="left")
        ttk.Button(scan_controls, text="아래로", command=lambda: self._move_selected(self.scan_tree, self._scan_order, 1)).pack(side="left", padx=5)
        ttk.Button(scan_controls, text="대기열로 내리기", command=self._demote_selected).pack(side="left", padx=5)
        ttk.Button(scan_controls, text="선택 서류 삭제", command=lambda: self._delete_selected_documents(self.scan_tree)).pack(side="left", padx=5)
        ttk.Button(scan_controls, text="선택 서류 스캔에 올리기", command=self._activate_selected).pack(side="right")
        wait_controls = ttk.Frame(self.wait_frame)
        wait_controls.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(wait_controls, text="위로", command=lambda: self._move_selected(self.wait_tree, self._queue_order, -1)).pack(side="left")
        ttk.Button(wait_controls, text="아래로", command=lambda: self._move_selected(self.wait_tree, self._queue_order, 1)).pack(side="left", padx=5)
        ttk.Button(wait_controls, text="선택 서류 삭제", command=lambda: self._delete_selected_documents(self.wait_tree)).pack(side="left", padx=5)
        ttk.Button(wait_controls, text="스캔할 서류로 올리기 →", command=self._promote_selected).pack(side="right")
        self.completed_count = tk.StringVar(value="완료된 서류 0개")
        ttk.Label(tab, textvariable=self.completed_count).grid(row=2, column=0, sticky="e", pady=(8, 0))

    @staticmethod
    def _make_document_tree(parent: ttk.LabelFrame, row: int) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=("pages", "carrier", "state"), selectmode="extended")
        tree.heading("#0", text="서류 폴더")
        tree.heading("pages", text="페이지")
        tree.heading("carrier", text="포탈")
        tree.heading("state", text="상태")
        tree.column("#0", width=360, anchor="w")
        tree.column("pages", width=75, anchor="center", stretch=False)
        tree.column("carrier", width=75, anchor="center", stretch=False)
        tree.column("state", width=100, anchor="center", stretch=False)
        tree.grid(row=row, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        scrollbar.grid(row=row, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        return tree

    def _edit_config(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Receiver 설정")
        dialog.transient(self.root)
        dialog.grab_set()
        labels = (
            ("relay_url", "Render Relay HTTPS 주소"),
            ("station_id", "접수 자리 ID"),
            ("token", "이 자리 전용 Relay 토큰"),
            ("work_path", "Receiver 작업 폴더"),
            ("sk_report_path", "SK sk_report.txt"),
            ("kt_database_path", "KT myCustomer.db"),
            ("kt_image_directory", "KT 이미지 폴더"),
            ("scanner_ready_path", "가상 스캐너 ready 폴더"),
        )
        entries: dict[str, ttk.Entry] = {}
        for row, (key, label) in enumerate(labels):
            ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=5)
            entry = ttk.Entry(dialog, width=76)
            if key == "token":
                entry.configure(show="•")
            entry.insert(0, str(self.config.get(key, "")))
            entry.grid(row=row, column=1, sticky="ew", padx=6, pady=5)
            entries[key] = entry
            if key not in {"station_id", "token", "relay_url"}:
                ttk.Button(
                    dialog,
                    text="찾기",
                    command=lambda target=entry, setting=key: self._browse_setting(target, setting),
                ).grid(row=row, column=2, padx=(0, 8), pady=5)
        dialog.columnconfigure(1, weight=1)

        def save() -> None:
            config = {key: entry.get().strip() for key, entry in entries.items()}
            if any(not value for value in config.values()):
                messagebox.showwarning("설정 확인", "모든 Receiver 설정을 입력하세요.", parent=dialog)
                return
            parsed_relay = urlsplit(config["relay_url"].rstrip("/"))
            if (parsed_relay.scheme != "https" or not parsed_relay.netloc
                    or parsed_relay.path not in {"", "/"} or parsed_relay.query
                    or parsed_relay.fragment or len(config["token"]) < 32
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", config["station_id"])):
                messagebox.showwarning(
                    "Relay 설정 확인",
                    "Relay 주소, 자리 ID 형식, 자리 토큰(32자 이상)을 확인하세요.",
                    parent=dialog,
                )
                return
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            self.config = config
            dialog.destroy()
            self.status.set("Render Relay 수신 대기")
            self._refresh_queue()

        ttk.Button(dialog, text="저장", command=save).grid(row=len(labels), column=1, sticky="e", padx=8, pady=10)

    @staticmethod
    def _browse_setting(entry: ttk.Entry, setting: str) -> None:
        if setting == "kt_database_path":
            chosen = filedialog.askopenfilename(
                title="KT myCustomer.db 선택",
                filetypes=(("SQLite DB", "*.db *.sqlite"), ("모든 파일", "*.*")),
            )
        elif setting == "sk_report_path":
            chosen = filedialog.askopenfilename(
                title="SK sk_report.txt 선택",
                filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*")),
            )
        else:
            chosen = filedialog.askdirectory(title="폴더 선택", mustexist=False)
        if chosen:
            entry.delete(0, tk.END)
            entry.insert(0, chosen)

    def _state_path(self) -> Path:
        return Path(self.config.get("work_path", "")) / "receiver-state.json"

    def _load_queue_state(self) -> None:
        try:
            state = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        scan_order = state.get("scan_order", [])
        queue_order = state.get("queue_order", state.get("order", []))
        self._scan_order = [item for item in scan_order if isinstance(item, str)]
        self._queue_order = [item for item in queue_order if isinstance(item, str)]
        completed = state.get("completed", [])
        self._completed = {item for item in completed if isinstance(item, str)}
        undo_stack = state.get("document_undo_stack", [])
        if isinstance(undo_stack, list):
            self._document_undo_stack = [
                item for item in undo_stack
                if isinstance(item, dict) and isinstance(item.get("entries"), list)
            ]
        active = state.get("active")
        if isinstance(active, dict):
            self._active = active
            active_keys = self._active.get("keys") or [self._active.get("key", "")]
            active_keys = [key for key in active_keys if isinstance(key, str)]
            self._queue_order = [key for key in self._queue_order if key not in active_keys]
            self._scan_order = list(dict.fromkeys([*active_keys, *self._scan_order]))

    def _save_queue_state(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "scan_order": self._scan_order,
                "queue_order": self._queue_order,
                "order": self._scan_order + self._queue_order,
                "active": self._active,
                "completed": sorted(self._completed),
                "document_undo_stack": self._document_undo_stack,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _poll_inbox(self) -> None:
        self._sync_active_scan()
        configured = all(self.config.get(key) for key in ("relay_url", "station_id", "token"))
        if (configured and not self._processing and not self._clearing_received
                and monotonic() >= self._retry_after):
            self._processing = True
            Thread(target=self._receive_relay_work, daemon=True).start()
        self.root.after(2000, self._poll_inbox)

    def _receive_relay_work(self) -> None:
        try:
            job_dir, manifest = receive_work_package(
                {"relay_url": self.config["relay_url"], "token": self.config["token"]},
                self.config["station_id"], Path(self.config["work_path"]),
                cancel_event=self._cancel_receive,
            ) or (None, None)
            if job_dir is None:
                self.root.after(0, self._receive_finished, None)
                return
            applied_marker = job_dir / "applied.json"
            if not applied_marker.exists():
                if self._cancel_receive.is_set():
                    raise RuntimeError("받은 자료 초기화 요청으로 Relay 적용을 취소했습니다.")
                if manifest.get("carrier") == "SK":
                    identity = self._job_file(job_dir, str(manifest.get("identity_image", "")))
                    detection_data = manifest.get("id_type_detection", {})
                    code = str(manifest.get("id_type_code", ""))
                    if not isinstance(detection_data, dict):
                        detection_data = {}
                    fallback = ID_TYPES.get(code, {})
                    detection = IdTypeDetection(
                        name=detection_data.get("name") or fallback.get("name"),
                        type_code=detection_data.get("type_code") or fallback.get("type"),
                        candidate_codes=tuple(detection_data.get("candidate_codes", (code,))),
                        preferred_code=detection_data.get("preferred_code") or code,
                    )
                    report_path = job_dir / "sk_report.txt"
                    result = OcrResult(job_id=str(manifest["job_id"]), image_path=identity)
                    write_ocr_result(
                        result, detection,
                        values=manifest.get("reviewed_values", {}), path=report_path,
                    )
                    self._apply_sk_report(report_path)
                elif manifest.get("carrier") == "KT":
                    self._apply_kt_result(job_dir, manifest)
                else:
                    raise ValueError("지원하지 않는 포탈 작업입니다.")
                applied_marker.write_text(
                    json.dumps({"carrier": manifest["carrier"],
                                "applied_at": datetime.now().isoformat(timespec="seconds")},
                               ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            acknowledge_work_package(
                {"relay_url": self.config["relay_url"], "token": self.config["token"]},
                self.config["station_id"], str(manifest["job_id"]),
            )
            outcome = f"{manifest['carrier']} 저장 완료 · {manifest['job_id']}"
            self._retry_after = 0.0
        except Exception as exc:
            outcome = f"Relay 수신/적용 실패 · {exc}"
            self._retry_after = monotonic() + 30
        self.root.after(0, self._receive_finished, outcome)

    def _sync_active_scan(self) -> None:
        if not self._active:
            return
        ready = Path(self.config.get("scanner_ready_path", ""))
        if not ready.is_dir():
            return
        if (ready / self._active.get("scanner_job_id", "")).exists():
            return
        keys = self._active.get("keys") or [self._active.get("key", "")]
        for key in keys:
            if key:
                self._completed.add(str(key))
        self._active = None
        self._save_queue_state()
        for key in keys:
            if self.scan_tree.exists(str(key)):
                self.scan_tree.delete(str(key))
            if self.wait_tree.exists(str(key)):
                self.wait_tree.delete(str(key))
        self.status.set("가상 스캐너가 작업 완료를 확인했습니다")

    def _apply_sk_report(self, source: Path) -> None:
        target = Path(self.config["sk_report_path"]).expanduser()
        if not source.is_file():
            raise ValueError("SK report 파일이 패키지에 없습니다.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = Path(self.config["work_path"]) / "backups" / f"sk_report_{datetime.now():%Y%m%d_%H%M%S_%f}.txt"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _apply_kt_result(self, job_dir: Path, manifest: dict) -> None:
        image_path = self._job_file(job_dir, str(manifest.get("identity_image", "")))
        values = manifest.get("reviewed_values")
        code = manifest.get("id_type_code")
        if not image_path.is_file() or not isinstance(values, dict) or code not in ID_TYPES:
            raise ValueError("KT OCR 결과 패키지 필드가 올바르지 않습니다.")
        result = OcrResult(job_id=str(manifest["job_id"]), image_path=image_path)
        detection = IdTypeDetection(
            name=ID_TYPES[code]["name"],
            type_code=ID_TYPES[code]["type"],
            candidate_codes=(code,),
            preferred_code=code,
        )
        database = Path(self.config["kt_database_path"]).expanduser()
        images = Path(self.config["kt_image_directory"]).expanduser()
        if not database.is_file():
            raise FileNotFoundError(f"KT DB를 찾을 수 없습니다: {database}")
        backup = (
            Path(self.config["work_path"])
            / "backups"
            / f"myCustomer_{datetime.now():%Y%m%d_%H%M%S_%f}_{manifest['job_id']}.db"
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database, timeout=5.0) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        save_kt_submission(
            result, detection, values,
            database_path=database,
            image_directory=images,
        )

    @staticmethod
    def _job_file(job_dir: Path, relative_name: str) -> Path:
        relative = _safe_package_path(relative_name)
        target = job_dir.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(job_dir.resolve()) or not target.is_file():
            raise ValueError(f"작업 패키지 파일을 찾지 못했습니다: {relative_name}")
        return target

    def _receive_finished(self, outcome: str | None) -> None:
        self._processing = False
        if self._pending_received_reset is not None:
            self._finish_received_reset()
            return
        if outcome:
            self.status.set(outcome)
            self._refresh_queue()

    def _retry_failed(self) -> None:
        self._retry_after = 0.0
        self.status.set("Render Relay를 다시 확인합니다")
        if (not self._processing
                and all(self.config.get(key) for key in ("relay_url", "station_id", "token"))):
            self._processing = True
            Thread(target=self._receive_relay_work, daemon=True).start()

    def _reset_received_data(self) -> None:
        if self._clearing_received:
            return

        ready_value = self.config.get("scanner_ready_path", "").strip()
        ready = Path(ready_value).expanduser() if ready_value else None
        configured_work_path = self.config.get("work_path", "").strip()
        if not configured_work_path:
            messagebox.showerror("Receiver 작업 폴더 오류", "Receiver 작업 폴더를 설정하세요.", parent=self.root)
            return
        work_root = Path(configured_work_path).expanduser().resolve()
        if work_root == Path(work_root.anchor):
            messagebox.showerror(
                "Receiver 작업 폴더 오류",
                "작업 폴더가 드라이브 또는 시스템 루트로 지정되어 초기화할 수 없습니다.",
                parent=self.root,
            )
            return
        jobs_root = work_root / "jobs"
        state_path = work_root / "receiver-state.json"
        if ready is not None and (
            ready.resolve() == Path(ready.anchor) or ready.name.casefold() != "ready"
        ):
            messagebox.showerror(
                "Receiver 작업 폴더 오류",
                "가상 스캐너 폴더는 드라이브 루트가 아닌 이름이 ready인 전용 폴더여야 합니다.",
                parent=self.root,
            )
            return
        if (jobs_root.is_symlink() or state_path.is_symlink()
                or (ready is not None and ready.is_symlink())):
            messagebox.showerror(
                "Receiver 작업 폴더 오류",
                "jobs 폴더, ready 폴더 또는 Receiver 상태 파일이 바로가기로 연결되어 있어 초기화할 수 없습니다.",
                parent=self.root,
            )
            return

        try:
            job_count = sum(1 for item in jobs_root.iterdir() if item.is_dir()) if jobs_root.exists() else 0
            file_count = sum(1 for item in jobs_root.rglob("*") if item.is_file()) if jobs_root.exists() else 0
            ready_file_count = (
                sum(1 for item in ready.rglob("*") if item.is_file())
                if ready is not None and ready.is_dir() else 0
            )
        except OSError as exc:
            messagebox.showerror("Receiver 작업 폴더 확인 실패", str(exc), parent=self.root)
            return

        confirmed = messagebox.askyesno(
            "받은 자료 전체 초기화",
            f"이 Receiver의 수신 작업 {job_count}건, 파일 {file_count}개와\n"
            f"가상 스캐너 ready 파일 {ready_file_count}개를 삭제할까요?\n"
            f"ready 폴더: {ready if ready is not None else '(설정 안 됨)'}\n\n"
            "신분증 원본, OCR 결과, 수신 서류 JPG, 작업 manifest와 로컬 작업 목록을 삭제합니다.\n"
            "화면의 신분증 미리보기와 OCR 표시값, 서류 미리보기 메모리도 비웁니다.\n\n"
            "스캔 또는 Relay 수신/적용 중에도 강제로 초기화합니다. 진행 중인 스캔은 실패할 수 있습니다.\n"
            "이미 적용된 SK 파일, KT DB·이미지와 백업, Relay에 남은 미수신 작업은 삭제하지 않습니다.\n"
            "삭제한 수신 자료와 ready 작업은 복구할 수 없습니다.",
            parent=self.root,
        )
        if not confirmed:
            return

        self._clearing_received = True
        self._pending_received_reset = (
            jobs_root, ready, job_count, file_count, ready_file_count
        )
        if self._processing:
            self._cancel_receive.set()
            self.status.set("강제 초기화 중 · 진행 중인 Relay 요청을 마치는 중")
            return
        self._finish_received_reset()

    def _finish_received_reset(self) -> None:
        plan = self._pending_received_reset
        if plan is None:
            return
        jobs_root, ready_root, job_count, file_count, ready_file_count = plan
        try:
            for dialog in tuple(self._preview_dialogs):
                try:
                    dialog._close()
                except tk.TclError:
                    self._preview_dialogs.discard(dialog)
            if jobs_root.exists():
                shutil.rmtree(jobs_root)
            if ready_root is not None and ready_root.is_dir():
                for item in ready_root.iterdir():
                    if item.is_symlink() or not item.is_dir():
                        item.unlink()
                    else:
                        shutil.rmtree(item)

            self._items.clear()
            self._jobs.clear()
            self._job_labels.clear()
            self._scan_order.clear()
            self._queue_order.clear()
            self._completed.clear()
            self._document_undo_stack.clear()
            self._active = None
            self._drag_start = None
            self._selected_job_id = ""
            self._photo = None
            self.preview.configure(image="", text="접수 작업을 선택하면 신분증이 크게 표시됩니다")
            self.job_box.configure(values=())
            self.job_box.set("")
            for variable in self.identity_vars.values():
                variable.set("")
            self.identity_hint.set("초기화할 수신 작업이 없습니다.")
            self._save_queue_state()
            gc.collect()
            self._refresh_queue()
            self.status.set(
                f"받은 자료 초기화 완료 · 작업 {job_count}건, 파일 {file_count}개, "
                f"ready 파일 {ready_file_count}개 삭제"
            )
        except (OSError, tk.TclError) as exc:
            self._refresh_queue()
            messagebox.showerror(
                "받은 자료 초기화 실패",
                f"일부 자료를 삭제하지 못했습니다. 파일 권한과 Receiver 작업 폴더를 확인하세요.\n{exc}",
                parent=self.root,
            )
        finally:
            self._pending_received_reset = None
            self._cancel_receive = Event()
            self._clearing_received = False

    def _refresh_queue(self) -> None:
        self._sync_active_scan()
        self._items.clear()
        self._jobs.clear()
        work_root = Path(self.config.get("work_path", "")) / "jobs"
        manifests = (
            sorted(
                work_root.glob("*/manifest.json"),
                key=lambda item: item.stat().st_mtime,
            )
            if work_root.exists()
            else ()
        )
        for manifest_path in manifests:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job_id = manifest.get("job_id")
            if manifest.get("station_id") != self.config.get("station_id"):
                continue
            if not (manifest_path.parent / "applied.json").exists():
                continue
            self._jobs[str(job_id)] = {
                "job_id": str(job_id),
                "job_dir": manifest_path.parent,
                "manifest": manifest,
                "mtime": manifest_path.stat().st_mtime,
            }
            documents = manifest.get("documents", [])
            if not isinstance(documents, list):
                continue
            for document in documents:
                if not isinstance(document, dict):
                    continue
                doc_id = document.get("document_id")
                key = f"{job_id}:{doc_id}"
                if self._is_document_deleted(key):
                    continue
                try:
                    for relative in document.get("pages", []):
                        self._job_file(manifest_path.parent, str(relative))
                except (TypeError, ValueError):
                    continue
                display_document = dict(document)
                display_document["label"] = _display_text(
                    document.get("source_name") or document.get("label", "서류")
                )
                display_document["source_name"] = _display_text(
                    document.get("source_name") or document.get("label", "서류")
                )
                self._items[key] = {
                    "key": key,
                    "job_id": job_id,
                    "job_dir": manifest_path.parent,
                    "carrier": manifest.get("carrier", ""),
                    "document": display_document,
                    "state": "대기",
                }
        self._scan_order = [
            key for key in self._scan_order
            if key in self._items and key not in self._completed
        ]
        self._queue_order = [
            key for key in self._queue_order
            if key in self._items and key not in self._completed and key not in self._scan_order
        ]
        known = set(self._scan_order) | set(self._queue_order) | self._completed
        self._queue_order.extend(key for key in self._items if key not in known)
        active_keys = set()
        if self._active:
            active_keys = set(self._active.get("keys") or [self._active.get("key", "")])
        for key, item in self._items.items():
            if key in active_keys:
                item["state"] = "스캔 중"
        self._render_document_tree(self.scan_tree, self._scan_order, active_keys, "스캔 대상")
        self._render_document_tree(self.wait_tree, self._queue_order, active_keys, "대기")
        self.completed_count.set(f"완료된 서류 {len(self._completed)}개")
        self._refresh_job_selector()

    def _render_document_tree(
        self, tree: ttk.Treeview, order: list[str], active_keys: set[str], idle_state: str,
    ) -> None:
        tree.delete(*tree.get_children())
        for key in order:
            item = self._items.get(key)
            if item is None:
                continue
            document = item["document"]
            tree.insert(
                "", "end", iid=key,
                text=_display_text(document.get("source_name") or document.get("label", "서류")),
                values=(document.get("page_count", 0), item["carrier"],
                        "스캔 중" if key in active_keys else idle_state),
            )

    def _is_document_deleted(self, key: str) -> bool:
        return any(
            entry.get("key") == key
            for action in self._document_undo_stack
            for entry in action.get("entries", [])
            if isinstance(entry, dict)
        )

    def _delete_selected_documents(self, tree: ttk.Treeview) -> str:
        self._sync_active_scan()
        order = self._scan_order if tree is self.scan_tree else self._queue_order
        selected = set(tree.selection())
        keys = [key for key in order if key in selected and key in self._items]
        if not keys:
            return "break"

        active_keys = set()
        if self._active:
            active_keys = set(self._active.get("keys") or [self._active.get("key", "")])
        if active_keys.intersection(keys):
            messagebox.showinfo(
                "스캔 진행 중", "현재 스캔 중인 서류는 삭제할 수 없습니다.", parent=self.root,
            )
            return "break"

        names = [
            str(self._items[key]["document"].get("source_name", "서류")) for key in keys
        ]
        confirmed = messagebox.askyesno(
            "서류 삭제",
            f"선택한 서류 {len(keys)}개를 목록에서 삭제할까요?\n\n"
            + "\n".join(names[:8])
            + (f"\n외 {len(names) - 8}개" if len(names) > 8 else "")
            + "\n\n삭제한 서류는 휴지통에 보관되며 Ctrl+Z로 되돌릴 수 있습니다.",
            parent=self.root,
        )
        if not confirmed:
            return "break"

        work_root = Path(self.config.get("work_path", "")).expanduser().resolve()
        action_id = uuid4().hex
        entries: list[dict] = []
        moved: list[tuple[Path, Path]] = []
        previous_scan_order = list(self._scan_order)
        previous_queue_order = list(self._queue_order)
        previous_completed = set(self._completed)
        try:
            for key in keys:
                item = self._items[key]
                job_dir = Path(item["job_dir"]).resolve()
                job_dir.relative_to(work_root)
                docs_root = (job_dir / "documents").resolve()
                document = item["document"]
                pages = document.get("pages", [])
                if not isinstance(pages, list) or not pages:
                    raise ValueError("서류 페이지 경로가 비어 있습니다.")
                page_paths = [self._job_file(job_dir, str(relative)).resolve() for relative in pages]
                page_dirs = {page.parent for page in page_paths}
                if len(page_dirs) != 1:
                    raise ValueError("한 서류의 페이지가 여러 폴더에 나뉘어 있습니다.")
                source_dir = next(iter(page_dirs))
                source_dir.relative_to(docs_root)
                if source_dir == docs_root or source_dir.is_symlink() or not source_dir.is_dir():
                    raise ValueError("서류 폴더가 올바르지 않아 삭제할 수 없습니다.")
                if any(not page.is_file() for page in page_paths):
                    raise ValueError("서류 페이지 파일이 없어 삭제할 수 없습니다.")

                lane = "scan" if key in self._scan_order else "queue"
                lane_order = self._scan_order if lane == "scan" else self._queue_order
                index = lane_order.index(key)
                trash_root = job_dir / ".document-trash"
                if trash_root.is_symlink():
                    raise ValueError("서류 휴지통 경로가 바로가기로 연결되어 있어 삭제할 수 없습니다.")
                trash_root.mkdir(parents=True, exist_ok=True)
                trash_root.resolve().relative_to(job_dir)
                trash_dir = trash_root / action_id / str(len(entries))
                trash_dir.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source_dir, trash_dir)
                moved.append((trash_dir, source_dir))
                entries.append({
                    "key": key,
                    "job_rel": job_dir.relative_to(work_root).as_posix(),
                    "source_rel": source_dir.relative_to(work_root).as_posix(),
                    "trash_rel": trash_dir.relative_to(work_root).as_posix(),
                    "lane": lane,
                    "index": index,
                    "completed": key in self._completed,
                })

            action = {"entries": entries}
            self._document_undo_stack.append(action)
            self._scan_order = [key for key in self._scan_order if key not in keys]
            self._queue_order = [key for key in self._queue_order if key not in keys]
            self._completed.difference_update(keys)
            self._save_queue_state()
        except (OSError, ValueError) as exc:
            if self._document_undo_stack and self._document_undo_stack[-1].get("entries") == entries:
                self._document_undo_stack.pop()
            self._scan_order = previous_scan_order
            self._queue_order = previous_queue_order
            self._completed = previous_completed
            for trash_dir, source_dir in reversed(moved):
                try:
                    source_dir.parent.mkdir(parents=True, exist_ok=True)
                    if trash_dir.exists() and not source_dir.exists():
                        os.replace(trash_dir, source_dir)
                except OSError:
                    pass
            self._refresh_queue()
            messagebox.showerror("서류 삭제 실패", str(exc), parent=self.root)
            return "break"

        self._refresh_queue()
        tree.focus_set()
        self.status.set(f"서류 {len(keys)}개 삭제 · Ctrl+Z로 되돌릴 수 있습니다")
        return "break"

    def _undo_document_delete(self, _event=None) -> str | None:
        focus = self.root.focus_get()
        if focus is not None and focus.winfo_class() in {"Entry", "TEntry", "Text"}:
            return None
        if not self._document_undo_stack:
            return None
        work_root = Path(self.config.get("work_path", "")).expanduser().resolve()
        action = self._document_undo_stack[-1]
        restored: list[tuple[Path, Path]] = []
        try:
            for entry in action["entries"]:
                job_dir = (work_root / Path(*PurePosixPath(entry["job_rel"]).parts)).resolve()
                source_dir = (work_root / Path(*PurePosixPath(entry["source_rel"]).parts)).resolve()
                trash_dir = (work_root / Path(*PurePosixPath(entry["trash_rel"]).parts)).resolve()
                job_dir.relative_to(work_root)
                source_dir.relative_to(job_dir / "documents")
                trash_dir.relative_to(job_dir / ".document-trash")
                if source_dir.exists():
                    raise ValueError("원래 서류 폴더가 이미 있어 복구하지 않았습니다.")
                if not trash_dir.is_dir() or trash_dir.is_symlink():
                    raise ValueError("휴지통의 서류 폴더를 찾을 수 없습니다.")
                source_dir.parent.mkdir(parents=True, exist_ok=True)
                os.replace(trash_dir, source_dir)
                restored.append((source_dir, trash_dir))
        except (KeyError, OSError, ValueError) as exc:
            for source_dir, trash_dir in reversed(restored):
                try:
                    trash_dir.parent.mkdir(parents=True, exist_ok=True)
                    if source_dir.exists() and not trash_dir.exists():
                        os.replace(source_dir, trash_dir)
                except OSError:
                    pass
            messagebox.showerror("삭제 취소 실패", str(exc), parent=self.root)
            return "break"

        self._document_undo_stack.pop()
        for entry in action["entries"]:
            key = entry["key"]
            lane_order = self._scan_order if entry.get("lane") == "scan" else self._queue_order
            if key not in lane_order:
                lane_order.insert(min(int(entry.get("index", len(lane_order))), len(lane_order)), key)
            if entry.get("completed"):
                self._completed.add(key)
        try:
            self._save_queue_state()
        except OSError as exc:
            for entry in action["entries"]:
                key = entry["key"]
                lane_order = self._scan_order if entry.get("lane") == "scan" else self._queue_order
                if key in lane_order:
                    lane_order.remove(key)
                self._completed.discard(key)
            self._document_undo_stack.append(action)
            for source_dir, trash_dir in reversed(restored):
                try:
                    trash_dir.parent.mkdir(parents=True, exist_ok=True)
                    if source_dir.exists() and not trash_dir.exists():
                        os.replace(source_dir, trash_dir)
                except OSError:
                    pass
            self._refresh_queue()
            messagebox.showerror("삭제 취소 상태 저장 실패", str(exc), parent=self.root)
            return "break"
        self._refresh_queue()
        self.status.set("마지막 서류 삭제를 되돌렸습니다")
        return "break"

    def _refresh_job_selector(self) -> None:
        jobs = sorted(self._jobs.values(), key=lambda item: item["mtime"], reverse=True)
        self._job_labels.clear()
        labels = []
        for job in jobs:
            manifest = job["manifest"]
            stamp = datetime.fromtimestamp(job["mtime"]).strftime("%m-%d %H:%M")
            label = f"{manifest.get('carrier', '접수')} · {stamp} · {job['job_id'][:8]}"
            self._job_labels[label] = job["job_id"]
            labels.append(label)
        self.job_box.configure(values=labels)
        current_id = getattr(self, "_selected_job_id", None)
        current_label = next(
            (label for label, job_id in self._job_labels.items() if job_id == current_id),
            labels[0] if labels else "",
        )
        self.job_box.set(current_label)
        selected_id = self._job_labels.get(current_label, "")
        if selected_id:
            self._show_job(selected_id)
        else:
            self._show_job("")

    def _select_job(self, _event=None) -> None:
        self._show_job(self._job_labels.get(self.job_box.get(), ""))

    def _show_job(self, job_id: str) -> None:
        self._selected_job_id = job_id
        job = self._jobs.get(job_id)
        if job is None:
            self.preview.configure(image="", text="접수 작업을 선택하면 신분증이 크게 표시됩니다")
            self._photo = None
            for variable in self.identity_vars.values():
                variable.set("")
            self.identity_hint.set("아직 표시할 접수 결과가 없습니다.")
            return
        manifest = job["manifest"]
        image_name = manifest.get("identity_image", "")
        try:
            image_path = self._job_file(job["job_dir"], str(image_name))
            with Image.open(image_path) as image:
                self._render_identity_image(image)
        except (OSError, TypeError, ValueError) as exc:
            self.preview.configure(image="", text=f"신분증 이미지를 열지 못했습니다\n{exc}")
            self._photo = None
        values = manifest.get("reviewed_values", {})
        if not isinstance(values, dict):
            values = {}
        detection = manifest.get("id_type_detection", {})
        if not isinstance(detection, dict):
            detection = {}
        code = str(manifest.get("id_type_code", ""))
        id_type = detection.get("name") or ID_TYPES.get(code, {}).get("name") or values.get("idType", "")
        self.identity_vars["id_type"].set(_display_text(id_type))
        for key in ("custNm", "ctzCorpNum", "addr", "isueDt", "drvLicenIsueNum", "isuePlc", "veteranNum"):
            self.identity_vars[key].set(_display_text(values.get(key, "")))
        document_count = len(manifest.get("documents", [])) if isinstance(manifest.get("documents"), list) else 0
        self.identity_hint.set(
            f"{manifest.get('carrier', '')} 접수 · 서류 폴더 {document_count}개 · 작업 {job_id[:8]}"
        )

    def _render_identity_image(self, image: Image.Image) -> None:
        width = max(400, self.preview.winfo_width() - 24)
        height = max(400, self.preview.winfo_height() - 24)
        rendered = image.convert("RGB")
        rendered.thumbnail((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(rendered)
        self.preview.configure(image=self._photo, text="")

    def _resize_identity_preview(self, _event=None) -> None:
        job = self._jobs.get(getattr(self, "_selected_job_id", ""))
        if not job:
            return
        try:
            image_path = self._job_file(job["job_dir"], str(job["manifest"].get("identity_image", "")))
            with Image.open(image_path) as image:
                self._render_identity_image(image)
        except (OSError, TypeError, ValueError):
            return

    def _selected_keys(self, tree: ttk.Treeview, order: list[str]) -> list[str]:
        selected = set(tree.selection())
        return [key for key in order if key in selected]

    def _preview_selected(self, tree: ttk.Treeview) -> None:
        selected = tree.selection()
        if selected and selected[0] in self._items:
            DocumentPreviewDialog(self, self._items[selected[0]])

    def _drag_press(self, event) -> None:
        tree = event.widget
        key = tree.identify_row(event.y)
        self._drag_start = (tree, key) if key else None

    def _drag_release(self, event) -> None:
        drag = self._drag_start
        self._drag_start = None
        if drag is None:
            return
        source_tree, dragged_key = drag
        target = self.root.winfo_containing(event.x_root, event.y_root)
        if source_tree is self.scan_tree and target is self.wait_tree:
            source_order, target_order = self._scan_order, self._queue_order
        elif source_tree is self.wait_tree and target is self.scan_tree:
            source_order, target_order = self._queue_order, self._scan_order
        else:
            return
        selection = set(source_tree.selection())
        keys = [key for key in source_order if key in selection] if dragged_key in selection else [dragged_key]
        self._transfer_documents(source_tree, target, source_order, target_order, keys)

    def _transfer_documents(
        self,
        source_tree: ttk.Treeview,
        target_tree: ttk.Treeview,
        source_order: list[str],
        target_order: list[str],
        keys: list[str],
    ) -> None:
        keys = [key for key in keys if key in source_order and key in self._items]
        if not keys:
            return
        active_keys = set()
        if self._active:
            active_keys = set(self._active.get("keys") or [self._active.get("key", "")])
        if source_tree is self.scan_tree and active_keys.intersection(keys):
            messagebox.showinfo("스캔 진행 중", "현재 스캔 중인 서류는 대기열로 옮길 수 없습니다.", parent=self.root)
            return
        selected = set(keys)
        source_order[:] = [key for key in source_order if key not in selected]
        target_order.extend(key for key in keys if key not in target_order)
        self._save_queue_state()
        self._refresh_queue()
        for key in keys:
            target_tree.selection_add(key)
        self.status.set(
            f"{len(keys)}개 서류 폴더를 "
            f"{'스캔할 서류' if target_tree is self.scan_tree else '스캔 서류 대기열'}로 옮겼습니다"
        )

    def _reset_document_lists(self) -> None:
        self._sync_active_scan()
        ready = Path(self.config.get("scanner_ready_path", ""))
        if self._active or (ready.is_dir() and any(ready.iterdir())):
            messagebox.showinfo(
                "스캔 작업 진행 중",
                "가상 스캐너 ready 폴더에 작업이 남아 있어 초기화하지 않았습니다.\n"
                "현재 작업을 완료하거나 확인한 뒤 다시 초기화하세요.",
                parent=self.root,
            )
            return
        manifest_documents: dict[Path, list[dict]] = {}
        for job in self._jobs.values():
            documents = job["manifest"].get("documents", [])
            if isinstance(documents, list):
                manifest_documents[job["job_dir"].resolve()] = [
                    document for document in documents if isinstance(document, dict)
                ]
        count = sum(len(documents) for documents in manifest_documents.values())
        if not count:
            messagebox.showinfo("서류 목록 초기화", "초기화할 서류 폴더가 없습니다.", parent=self.root)
            return
        confirmed = messagebox.askyesno(
            "서류 목록 초기화",
            f"Receiver에 있는 서류 폴더 {count}개를 초기화할까요?\n\n"
            "변환 JPG와 삭제 취소용 휴지통 자료를 영구 삭제하고 두 목록과 manifest에서 제거합니다.\n"
            "신분증 이미지와 OCR 결과, SK 보고서, KT DB, 가상 스캐너 폴더는 삭제하지 않습니다.\n"
            "이 초기화 후에는 Ctrl+Z로도 복구할 수 없습니다.",
            parent=self.root,
        )
        if not confirmed:
            return

        files_by_job: dict[Path, set[Path]] = {}
        ids_by_job: dict[Path, set[str]] = {}
        folders_by_job: dict[Path, set[Path]] = {}
        try:
            for job_dir, documents in manifest_documents.items():
                docs_root = (job_dir / "documents").resolve()
                for document in documents:
                    document_id = str(document.get("document_id", ""))
                    for relative_name in document.get("pages", []):
                        relative = _safe_package_path(str(relative_name))
                        page = job_dir.joinpath(*relative.parts).resolve()
                        if not page.is_relative_to(docs_root):
                            raise ValueError("서류 페이지가 documents 폴더 밖을 가리킵니다.")
                        if page.is_file():
                            files_by_job.setdefault(job_dir, set()).add(page)
                            folders_by_job.setdefault(job_dir, set()).add(page.parent)
                    ids_by_job.setdefault(job_dir, set()).add(document_id)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("서류 목록 초기화 실패", str(exc), parent=self.root)
            return

        try:
            for pages in files_by_job.values():
                for page in pages:
                    page.unlink(missing_ok=True)
            for job_dir, folders in folders_by_job.items():
                docs_root = (job_dir / "documents").resolve()
                for folder in sorted(folders, key=lambda path: len(path.parts), reverse=True):
                    if folder != docs_root and folder.is_relative_to(docs_root):
                        try:
                            folder.rmdir()
                        except OSError:
                            pass
            for job_dir, document_ids in ids_by_job.items():
                manifest_path = job_dir / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                documents = manifest.get("documents", [])
                if isinstance(documents, list):
                    manifest["documents"] = [
                        document for document in documents
                        if not isinstance(document, dict)
                        or str(document.get("document_id", "")) not in document_ids
                    ]
                temporary = manifest_path.with_name(f".{manifest_path.name}.reset.tmp")
                temporary.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                os.replace(temporary, manifest_path)
            for job_dir in manifest_documents:
                trash_root = job_dir / ".document-trash"
                if trash_root.is_symlink():
                    raise ValueError("서류 휴지통 경로가 바로가기로 연결되어 있어 초기화할 수 없습니다.")
                if trash_root.exists():
                    shutil.rmtree(trash_root)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror(
                "서류 목록 초기화 일부 실패",
                f"일부 서류를 삭제하지 못했습니다. 목록을 새로고침해 상태를 확인하세요.\n{exc}",
                parent=self.root,
            )
            self._refresh_queue()
            return

        self._scan_order.clear()
        self._queue_order.clear()
        self._completed.clear()
        self._document_undo_stack.clear()
        self._save_queue_state()
        self._refresh_queue()
        self.status.set(f"서류 목록 초기화 완료 · JPG {count}개 폴더 삭제")

    def _move_selected(self, tree: ttk.Treeview, order: list[str], offset: int) -> None:
        selected = tree.selection()
        key = selected[0] if selected else None
        if key is None or key not in order:
            return
        active_keys = set()
        if self._active:
            active_keys = set(self._active.get("keys") or [self._active.get("key", "")])
        if key in active_keys:
            self.status.set("스캔 중인 서류는 순서를 바꿀 수 없습니다")
            return
        index = order.index(key)
        new_index = index + offset
        if not 0 <= new_index < len(order):
            return
        order[index], order[new_index] = order[new_index], order[index]
        self._save_queue_state()
        self._refresh_queue()
        tree.selection_set(key)

    def _promote_selected(self) -> None:
        keys = self._selected_keys(self.wait_tree, self._queue_order)
        if not keys:
            messagebox.showinfo("스캔할 서류", "대기열에서 스캔 대상으로 올릴 서류 폴더를 선택하세요.", parent=self.root)
            return
        self._transfer_documents(
            self.wait_tree, self.scan_tree, self._queue_order, self._scan_order, keys,
        )

    def _demote_selected(self) -> None:
        keys = self._selected_keys(self.scan_tree, self._scan_order)
        if not keys:
            return
        self._transfer_documents(
            self.scan_tree, self.wait_tree, self._scan_order, self._queue_order, keys,
        )

    def _activate_selected(self) -> None:
        self._sync_active_scan()
        selected_keys = self._selected_keys(self.scan_tree, self._scan_order)
        selected_keys = [key for key in selected_keys if key not in self._completed]
        if not selected_keys or any(key not in self._items for key in selected_keys):
            messagebox.showinfo("서류 선택", "‘스캔할 서류’ 칸에서 한 개 이상의 폴더를 선택하세요.", parent=self.root)
            return
        ready = Path(self.config.get("scanner_ready_path", ""))
        if self._active and (ready / self._active.get("scanner_job_id", "")).exists():
            messagebox.showinfo(
                "기존 스캔 작업 대기",
                "현재 스캔에 올린 서류의 전송이 끝난 뒤 다음 서류를 올릴 수 있습니다.",
                parent=self.root,
            )
            return
        if ready.is_dir():
            occupied = [path for path in ready.iterdir() if path.is_dir()]
            if occupied:
                messagebox.showinfo(
                    "스캐너 대기열 사용 중",
                    "ready 폴더에 기존 작업이 있어 새 서류를 올리지 않았습니다. "
                    "기존 작업을 먼저 완료하거나 Receiver 상태를 확인하세요.",
                    parent=self.root,
                )
                return
        selected_in_order = selected_keys
        selected_items = [self._items[item_key] for item_key in selected_in_order]
        scanner_job_id = uuid4().hex
        staging = ready / f".{scanner_job_id}.incoming"
        try:
            ready.mkdir(parents=True, exist_ok=True)
            destination = ready / scanner_job_id
            staging.mkdir(parents=True, exist_ok=False)
            page_manifest = []
            source_names: list[str] = []
            for item in selected_items:
                document = item["document"]
                pages = document.get("pages", [])
                if not isinstance(pages, list) or not pages:
                    raise ValueError(f"{document.get('label', '서류')}에 JPG 페이지가 없습니다.")
                source_names.append(_display_text(document.get("source_name", document.get("label", "documents"))))
                for relative in pages:
                    source = self._job_file(item["job_dir"], str(relative))
                    number = len(page_manifest) + 1
                    name = f"{number:03d}.jpg"
                    target = staging / name
                    shutil.copy2(source, target)
                    page_manifest.append({
                        "number": number,
                        "file": name,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    })
            manifest = {
                "job_id": scanner_job_id,
                "station_id": self.config["station_id"],
                "source_name": " + ".join(source_names)[:255],
                "dpi": 100,
                "page_count": len(page_manifest),
                "pages": page_manifest,
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(staging, destination)
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            messagebox.showerror("스캐너 대기열 오류", str(exc), parent=self.root)
            return
        self._active = {"keys": selected_in_order, "scanner_job_id": scanner_job_id}
        self._save_queue_state()
        labels = [str(item["document"].get("label", "서류")) for item in selected_items]
        self.status.set(f"스캔 대기 중 · {' + '.join(labels)}")
        self._refresh_queue()


def main() -> None:
    root = tk.Tk()
    ReceiverApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
