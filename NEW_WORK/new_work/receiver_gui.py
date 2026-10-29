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
from time import monotonic, time_ns
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
        self.root.title("접수 자료 Receiver")
        self.root.geometry("1580x940")
        self.root.minsize(1240, 760)
        self.config_path = default_receiver_config_path()
        self.config = default_receiver_config()
        if self.config_path.exists():
            try:
                self.config.update(json.loads(self.config_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                messagebox.showerror("Receiver 설정 오류", str(exc), parent=root)
        self._processing = False
        self._receive_progress_started = 0.0
        self._retry_after = 0.0
        self._photo: ImageTk.PhotoImage | None = None
        self._items: dict[str, dict] = {}
        self._jobs: dict[str, dict] = {}
        self._job_labels: dict[str, str] = {}
        self._queue_order: list[str] = []
        self._scan_order: list[str] = []
        self._completed: set[str] = set()
        self._document_undo_stack: list[dict] = []
        self._ready_jobs: list[dict[str, object]] = []
        self._identity_entries: dict[str, ttk.Entry] = {}
        self._identity_type_label_to_code = {
            f"{metadata['name']} ({code})": code for code, metadata in ID_TYPES.items()
        }
        self._identity_editing = False
        self._correction_busy = False
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
        font_family = (
            "Malgun Gothic" if os.name == "nt"
            else "Apple SD Gothic Neo" if sys.platform == "darwin"
            else "Noto Sans CJK KR"
        )
        self.root.configure(background="#F3F6FB")
        style.configure("TLabel", font=(font_family, 10), foreground="#243B5B")
        style.configure("Title.TLabel", font=(font_family, 16, "bold"), foreground="#18365F")
        style.configure("Subtitle.TLabel", font=(font_family, 9), foreground="#7385A0")
        style.configure("Treeview", font=(font_family, 9), rowheight=34,
                        background="#FFFFFF", fieldbackground="#FFFFFF",
                        foreground="#243B5B", borderwidth=0)
        style.configure("Treeview.Heading", font=(font_family, 9, "bold"),
                        background="#EEF3FA", foreground="#5E7290", relief="flat")
        style.map("Treeview", background=[("selected", "#E5F0FF")],
                  foreground=[("selected", "#174D91")])
        style.configure("TButton", font=(font_family, 9, "bold"), padding=(9, 6),
                        background="#EDF4FF", foreground="#2056A8",
                        bordercolor="#D6E4F5", lightcolor="#D6E4F5",
                        darkcolor="#D6E4F5")
        style.map("TButton", background=[("active", "#DCEAFF"),
                                          ("disabled", "#F1F4F8")],
                  foreground=[("disabled", "#9AA9BC")])
        style.configure("Primary.TButton", background="#1769E8", foreground="#FFFFFF",
                        bordercolor="#1769E8", lightcolor="#1769E8",
                        darkcolor="#1769E8")
        style.map("Primary.TButton", background=[("active", "#0F57C8")])
        style.configure("Receiver.TFrame", background="#F3F6FB")
        style.configure("ReceiverPanel.TFrame", background="#FFFFFF")
        style.configure("ReceiverPanel.TLabelframe", background="#FFFFFF",
                        bordercolor="#D8E3F0", relief="solid", padding=10)
        style.configure("ReceiverPanel.TLabelframe.Label", background="#FFFFFF",
                        foreground="#18365F", font=(font_family, 10, "bold"))
        style.configure("TLabelframe.Label", font=(font_family, 10, "bold"),
                        foreground="#18365F")
        style.configure("TNotebook", background="#F3F6FB", borderwidth=0)
        style.configure("Receiver.Horizontal.TProgressbar", troughcolor="#E4EAF3",
                        background="#2374EA", bordercolor="#E4EAF3",
                        lightcolor="#2374EA", darkcolor="#2374EA")

        shell = ttk.Frame(self.root, padding=12, style="Receiver.TFrame")
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)
        toolbar = ttk.Frame(shell)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="접수 자료", style="Title.TLabel").pack(side="left", padx=(0, 16))
        ttk.Button(toolbar, text="Receiver 설정", command=self._edit_config).pack(side="left")
        ttk.Button(toolbar, text="새로고침", command=self._refresh_queue).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Relay 다시 확인", command=self._retry_failed).pack(side="left")
        ttk.Button(toolbar, text="받은 자료 초기화", command=self._reset_received_data).pack(
            side="left", padx=(6, 0)
        )
        self.status = tk.StringVar(value="수신 대기 중")
        ttk.Label(toolbar, textvariable=self.status, style="Subtitle.TLabel").pack(
            side="right", padx=(14, 2)
        )

        self.receive_progress_frame = ttk.Frame(shell)
        self.receive_progress_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.receive_progress_frame.columnconfigure(1, weight=1)
        self.receive_progress_label = tk.StringVar(value="")
        ttk.Label(self.receive_progress_frame, textvariable=self.receive_progress_label).grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.receive_progress_bar = ttk.Progressbar(
            self.receive_progress_frame, mode="determinate", maximum=100,
            style="Receiver.Horizontal.TProgressbar",
        )
        self.receive_progress_bar.grid(row=0, column=1, sticky="ew")
        self.receive_progress_frame.grid_remove()

        dashboard = ttk.Frame(shell, style="Receiver.TFrame")
        dashboard.grid(row=2, column=0, sticky="nsew")
        dashboard.columnconfigure(0, weight=5, uniform="receiver_dashboard")
        dashboard.columnconfigure(1, weight=6, uniform="receiver_dashboard")
        dashboard.rowconfigure(0, weight=1)
        self._build_identity_panel(dashboard)
        self._build_documents_panel(dashboard)

    def _build_identity_panel(self, dashboard: ttk.Frame) -> None:
        tab = ttk.Frame(dashboard, style="Receiver.TFrame")
        tab.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        tab.columnconfigure(0, weight=4, uniform="identity_columns")
        tab.columnconfigure(1, weight=5, uniform="identity_columns")
        tab.rowconfigure(0, weight=1)

        preview_frame = ttk.LabelFrame(
            tab, text="받은 신분증", padding=10, style="ReceiverPanel.TLabelframe",
        )
        preview_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        preview_frame.rowconfigure(1, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        job_selector = ttk.Frame(preview_frame, style="ReceiverPanel.TFrame")
        job_selector.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        job_selector.columnconfigure(1, weight=1)
        ttk.Label(job_selector, text="받은 접수", style="Subtitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.job_box = ttk.Combobox(job_selector, state="readonly")
        self.job_box.grid(row=0, column=1, sticky="ew")
        self.job_box.bind("<<ComboboxSelected>>", self._select_job)
        self.preview = tk.Label(
            preview_frame, text="수신된 신분증이 여기에 표시됩니다",
            anchor="center", justify="center", background="#F7F9FC", foreground="#71839D",
        )
        self.preview.grid(row=1, column=0, sticky="nsew")
        self.preview.bind("<Configure>", self._resize_identity_preview)

        result_frame = ttk.LabelFrame(
            tab, text="OCR 확인 및 수정", padding=12, style="ReceiverPanel.TLabelframe",
        )
        result_frame.grid(row=0, column=1, sticky="nsew")
        result_frame.columnconfigure(1, weight=1)
        result_frame.rowconfigure(1, weight=1)
        fields = ttk.Frame(result_frame, style="ReceiverPanel.TFrame")
        fields.grid(row=1, column=0, columnspan=2, sticky="new")
        fields.columnconfigure(1, weight=1)
        self.identity_vars: dict[str, tk.StringVar] = {}
        self._identity_field_rows: dict[str, tuple[ttk.Label, ttk.Entry, int]] = {}
        field_specs = (
            ("custNm", "이름"),
            ("ctzCorpNum", "주민등록번호 / 등록번호"), ("addr", "주소"),
            ("isueDt", "발급일자"), ("drvLicenIsueNum", "운전면허번호"),
            ("isuePlc", "발급기관"), ("veteranNum", "보훈번호"),
        )
        type_label = ttk.Label(fields, text="신분증 종류")
        type_label.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
        self.identity_vars["id_type"] = tk.StringVar(value="")
        self.identity_type_box = ttk.Combobox(
            fields, textvariable=self.identity_vars["id_type"], state="readonly",
            values=tuple(self._identity_type_label_to_code),
        )
        self.identity_type_box.grid(row=0, column=1, sticky="ew", pady=6, ipady=2)
        self._identity_field_rows["id_type"] = (type_label, self.identity_type_box, 0)
        for row, (key, label) in enumerate(field_specs, start=1):
            field_label = ttk.Label(fields, text=label)
            field_label.grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
            variable = tk.StringVar(value="")
            self.identity_vars[key] = variable
            entry = ttk.Entry(fields, textvariable=variable, state="readonly", width=20)
            entry.grid(row=row, column=1, sticky="ew", pady=6, ipady=4)
            self._identity_entries[key] = entry
            self._identity_field_rows[key] = (field_label, entry, row)
        self.identity_vars["mskPos"] = tk.StringVar(value="")
        self.identity_hint = tk.StringVar(value="중앙 PC에서 검토한 OCR 값을 표시합니다.")
        ttk.Label(result_frame, textvariable=self.identity_hint, style="Subtitle.TLabel",
                  wraplength=460).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )
        correction_controls = ttk.Frame(result_frame)
        correction_controls.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self.edit_identity_button = ttk.Button(
            correction_controls, text="값 수정", command=self._begin_identity_edit,
            state="disabled",
        )
        self.edit_identity_button.pack(side="left", padx=(0, 6))
        self.cancel_identity_edit_button = ttk.Button(
            correction_controls, text="취소", command=self._cancel_identity_edit, state="disabled",
        )
        self.cancel_identity_edit_button.pack(side="left", padx=(0, 6))
        self.save_identity_edit_button = ttk.Button(
            correction_controls, text="수정 저장", command=self._save_identity_edit,
            style="Primary.TButton", state="disabled",
        )
        self.save_identity_edit_button.pack(side="left")

    def _build_documents_panel(self, dashboard: ttk.Frame) -> None:
        tab = ttk.Frame(dashboard, style="Receiver.TFrame")
        tab.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        header = ttk.Frame(tab, style="Receiver.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="PDF 서류", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header, text="서류를 더블클릭하면 JPG 페이지를 확인할 수 있습니다.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Button(header, text="삭제 취소  Ctrl+Z", command=self._undo_document_delete).grid(
            row=0, column=1, sticky="e", padx=(8, 0)
        )
        ttk.Button(header, text="서류 목록 초기화", command=self._reset_document_lists).grid(
            row=1, column=1, sticky="e", padx=(8, 0), pady=(2, 0)
        )

        panes = ttk.Frame(tab, style="Receiver.TFrame")
        panes.grid(row=1, column=0, sticky="nsew")
        panes.columnconfigure(0, weight=1, uniform="document_queues")
        panes.columnconfigure(1, weight=1, uniform="document_queues")
        panes.rowconfigure(0, weight=1)
        self.scan_frame = ttk.LabelFrame(
            panes, text="스캔할 서류 · 바로 읽을 수 있음", padding=8,
            style="ReceiverPanel.TLabelframe",
        )
        self.wait_frame = ttk.LabelFrame(
            panes, text="스캔 대기열 · 아직 ready에 없음", padding=8,
            style="ReceiverPanel.TLabelframe",
        )
        self.scan_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.wait_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.scan_frame.columnconfigure(0, weight=1)
        self.scan_frame.rowconfigure(1, weight=1)
        self.wait_frame.columnconfigure(0, weight=1)
        self.wait_frame.rowconfigure(1, weight=1)
        self.scan_count_var = tk.StringVar(value="0개")
        self.wait_count_var = tk.StringVar(value="0개")
        ttk.Label(self.scan_frame, textvariable=self.scan_count_var,
                  style="Subtitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 5))
        ttk.Label(self.wait_frame, textvariable=self.wait_count_var,
                  style="Subtitle.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.scan_tree = self._make_document_tree(self.scan_frame, 1)
        self.wait_tree = self._make_document_tree(self.wait_frame, 1)
        self.scan_tree.bind("<Double-1>", lambda _event: self._preview_selected(self.scan_tree))
        self.wait_tree.bind("<Double-1>", lambda _event: self._preview_selected(self.wait_tree))
        for tree in (self.scan_tree, self.wait_tree):
            tree.bind("<Delete>", lambda _event, target=tree: self._delete_selected_documents(target))
            tree.bind("<Control-z>", self._undo_document_delete)
            tree.bind("<Control-Z>", self._undo_document_delete)
            tree.bind("<ButtonPress-1>", self._drag_press, add="+")
            tree.bind("<ButtonRelease-1>", self._drag_release, add="+")
        scan_controls = ttk.Frame(self.scan_frame)
        scan_controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(scan_controls, text="↑", width=3,
                   command=lambda: self._move_selected(self.scan_tree, self._scan_order, -1)).pack(side="left")
        ttk.Button(scan_controls, text="↓", width=3,
                   command=lambda: self._move_selected(self.scan_tree, self._scan_order, 1)).pack(side="left", padx=3)
        ttk.Button(scan_controls, text="대기열로 되돌리기",
                   command=self._demote_selected).pack(side="left", padx=(4, 0))
        ttk.Button(scan_controls, text="선택 삭제",
                   command=lambda: self._delete_selected_documents(self.scan_tree)).pack(side="right")
        wait_controls = ttk.Frame(self.wait_frame)
        wait_controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(wait_controls, text="↑", width=3,
                   command=lambda: self._move_selected(self.wait_tree, self._queue_order, -1)).pack(side="left")
        ttk.Button(wait_controls, text="↓", width=3,
                   command=lambda: self._move_selected(self.wait_tree, self._queue_order, 1)).pack(side="left", padx=3)
        ttk.Button(wait_controls, text="선택 삭제",
                   command=lambda: self._delete_selected_documents(self.wait_tree)).pack(side="left", padx=(4, 0))
        ttk.Button(wait_controls, text="스캔 목록에 추가 →",
                   style="Primary.TButton", command=self._promote_selected).pack(side="right")
        self.completed_count = tk.StringVar(value="스캔 완료 0개")
        ttk.Label(tab, textvariable=self.completed_count, style="Subtitle.TLabel").grid(
            row=2, column=0, sticky="e", pady=(7, 0)
        )

    @staticmethod
    def _make_document_tree(parent: ttk.LabelFrame, row: int) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=("pages", "state"), selectmode="extended")
        tree.heading("#0", text="PDF 폴더")
        tree.heading("pages", text="페이지")
        tree.heading("state", text="상태")
        tree.column("#0", width=220, minwidth=130, anchor="w")
        tree.column("pages", width=55, minwidth=48, anchor="center", stretch=False)
        tree.column("state", width=82, minwidth=72, anchor="center", stretch=False)
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
                if isinstance(item, dict) and (
                    isinstance(item.get("entries"), list)
                    or (
                        item.get("kind") == "job"
                        and isinstance(item.get("job_rel"), str)
                        and isinstance(item.get("trash_rel"), str)
                    )
                )
            ]
        ready_jobs = state.get("ready_jobs")
        if isinstance(ready_jobs, list):
            self._ready_jobs = [
                {"scanner_job_id": item["scanner_job_id"], "keys": item["keys"]}
                for item in ready_jobs
                if isinstance(item, dict)
                and isinstance(item.get("scanner_job_id"), str)
                and isinstance(item.get("keys"), list)
                and all(isinstance(key, str) for key in item["keys"])
            ]
        else:
            # Migrate the previous single-ready-job state format.
            active = state.get("active")
            if isinstance(active, dict):
                scanner_job_id = active.get("scanner_job_id")
                keys = active.get("keys") or [active.get("key", "")]
                if isinstance(scanner_job_id, str) and isinstance(keys, list):
                    self._ready_jobs = [{
                        "scanner_job_id": scanner_job_id,
                        "keys": [key for key in keys if isinstance(key, str)],
                    }]
        ready_keys = self._ready_document_keys()
        self._queue_order = [item for item in self._queue_order if item not in ready_keys]
        self._scan_order = list(dict.fromkeys([*ready_keys, *self._scan_order]))

    def _ready_document_keys(self) -> set[str]:
        return {
            key
            for job in self._ready_jobs
            for key in job.get("keys", [])
            if isinstance(key, str)
        }

    def _save_queue_state(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "scan_order": self._scan_order,
                "queue_order": self._queue_order,
                "order": self._scan_order + self._queue_order,
                "ready_jobs": self._ready_jobs,
                "active": self._ready_jobs[0] if len(self._ready_jobs) == 1 else None,
                "completed": sorted(self._completed),
                "document_undo_stack": self._document_undo_stack,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _poll_inbox(self) -> None:
        self._sync_active_scan()
        configured = all(self.config.get(key) for key in ("relay_url", "station_id", "token"))
        if (configured and not self._processing and not self._clearing_received
                and not self._correction_busy
                and monotonic() >= self._retry_after):
            self._processing = True
            Thread(target=self._receive_relay_work, daemon=True).start()
        self.root.after(2000, self._poll_inbox)

    def _receive_relay_work(self) -> None:
        new_job_id: str | None = None
        try:
            def progress(done: int, total: int, unit: str) -> None:
                self.root.after(0, self._update_receive_progress, done, total, unit)

            job_dir, manifest = receive_work_package(
                {"relay_url": self.config["relay_url"], "token": self.config["token"]},
                self.config["station_id"], Path(self.config["work_path"]),
                cancel_event=self._cancel_receive,
                progress=progress,
            ) or (None, None)
            if job_dir is None:
                self.root.after(0, self._receive_finished, None)
                return
            self.root.after(0, self._set_receive_progress_stage, "전송 완료 · 검증/압축 해제/저장 중")
            if self._cancel_receive.is_set():
                raise RuntimeError("받은 자료 초기화 요청으로 Relay 수신을 취소했습니다.")
            identity_image = manifest.get("identity_image")
            has_identity = isinstance(identity_image, str) and bool(identity_image)
            documents = manifest.get("documents", [])
            if not has_identity and (not isinstance(documents, list) or not documents):
                raise ValueError("신분증과 PDF가 모두 없는 빈 작업입니다.")
            if has_identity:
                self._job_file(job_dir, identity_image)
                if manifest.get("carrier") not in {"SK", "KT"}:
                    raise ValueError("지원하지 않는 포탈 작업입니다.")
            for document in documents:
                if not isinstance(document, dict) or not isinstance(document.get("pages"), list):
                    raise ValueError("서류 메타데이터가 올바르지 않습니다.")
                for page in document["pages"]:
                    self._job_file(job_dir, str(page))
            received_marker = job_dir / "received.json"
            if not received_marker.exists():
                received_marker.write_text(
                    json.dumps({
                        "received_at": datetime.now().isoformat(timespec="seconds"),
                        "identity_pending": has_identity,
                    }, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            acknowledge_work_package(
                {"relay_url": self.config["relay_url"], "token": self.config["token"]},
                self.config["station_id"], str(manifest["job_id"]),
            )
            new_job_id = str(manifest["job_id"])
            if has_identity and documents:
                outcome = "신분증과 서류 수신 완료 · 폴더에서 각각 확인하세요"
            elif has_identity:
                outcome = "신분증 수신 완료 · 확인 버튼을 누르면 포탈에 저장됩니다"
            else:
                outcome = "서류 수신 완료 · 원하는 서류를 스캔 대기열에 추가하세요"
            new_job_id = str(manifest["job_id"])
            self._retry_after = 0.0
        except Exception as exc:
            outcome = f"Relay 수신/검증 실패 · {exc}"
            self._retry_after = monotonic() + 30
        self.root.after(0, self._receive_finished, outcome, new_job_id)

    def _update_receive_progress(self, done: int, total: int, unit: str) -> None:
        if total <= 0:
            return
        if not self.receive_progress_frame.winfo_ismapped():
            self._receive_progress_started = monotonic()
            self.receive_progress_frame.grid()
        done = max(0, min(done, total))
        percent = done * 100.0 / total
        self.receive_progress_bar.configure(value=percent)
        if done >= total:
            self.receive_progress_label.set("전송 완료 · 검증/압축 해제/저장 중")
            return
        elapsed = max(0.001, monotonic() - self._receive_progress_started)
        rate = done / elapsed
        remaining = (total - done) / rate if rate > 0 else None
        if unit == "bytes":
            amount = f"{done / (1024 * 1024):.1f}/{total / (1024 * 1024):.1f} MiB"
            speed = f"{rate / (1024 * 1024):.1f} MiB/s"
        else:
            amount = f"{done}/{total}페이지"
            speed = f"{rate:.1f}페이지/s"
        eta = self._format_receive_eta(remaining)
        self.receive_progress_label.set(
            f"Relay 수신 {percent:.0f}% · {amount} · {speed} · 약 {eta} 남음"
        )

    @staticmethod
    def _format_receive_eta(seconds: float | None) -> str:
        if seconds is None:
            return "계산 중"
        seconds = max(0, int(seconds + 0.5))
        if seconds < 60:
            return f"{seconds}초"
        minutes, remainder = divmod(seconds, 60)
        return f"{minutes}분 {remainder}초"

    def _set_receive_progress_stage(self, message: str) -> None:
        self.receive_progress_frame.grid()
        self.receive_progress_label.set(message)
        self.receive_progress_bar.configure(value=100)

    def _sync_active_scan(self) -> None:
        if not self._ready_jobs:
            return
        ready = Path(self.config.get("scanner_ready_path", ""))
        if not ready.is_dir():
            return
        remaining: list[dict[str, object]] = []
        finished_keys: list[str] = []
        for job in self._ready_jobs:
            scanner_job_id = str(job.get("scanner_job_id", ""))
            if scanner_job_id and (ready / scanner_job_id).exists():
                remaining.append(job)
            else:
                finished_keys.extend(
                    key for key in job.get("keys", []) if isinstance(key, str)
                )
        if not finished_keys:
            return
        for key in finished_keys:
            self._completed.add(key)
        self._ready_jobs = remaining
        self._save_queue_state()
        for key in finished_keys:
            if self.scan_tree.exists(str(key)):
                self.scan_tree.delete(str(key))
            if self.wait_tree.exists(str(key)):
                self.wait_tree.delete(str(key))
        self.status.set(f"가상 스캐너 작업 완료 확인 · 서류 {len(finished_keys)}개")

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

    def _apply_kt_result(
        self, job_dir: Path, manifest: dict, *, target_element_id: str | None = None,
    ) -> str:
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
        _, new_element_id = save_kt_submission(
            result, detection, values,
            database_path=database,
            image_directory=images,
            target_element_id=target_element_id,
        )
        return new_element_id

    def _confirm_identity(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("확인할 접수 폴더를 찾지 못했습니다.")
        manifest = job["manifest"]
        identity_name = manifest.get("identity_image")
        if not isinstance(identity_name, str) or not identity_name:
            raise ValueError("이 접수 폴더에는 받은 신분증이 없습니다.")
        marker_path = Path(job["job_dir"]) / "applied.json"
        if marker_path.exists():
            return
        if manifest.get("carrier") == "SK":
            identity = self._job_file(Path(job["job_dir"]), identity_name)
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
            report_path = Path(job["job_dir"]) / "sk_report.txt"
            result = OcrResult(job_id=job_id, image_path=identity)
            write_ocr_result(
                result, detection, values=manifest.get("reviewed_values", {}),
                path=report_path,
            )
            self._apply_sk_report(report_path)
            details: dict[str, object] = {"identity_applied": True}
        elif manifest.get("carrier") == "KT":
            details = {
                "identity_applied": True,
                "kt_element_id": self._apply_kt_result(Path(job["job_dir"]), manifest),
            }
        else:
            raise ValueError("지원하지 않는 포탈 작업입니다.")
        marker_path.write_text(
            json.dumps({
                "carrier": manifest["carrier"],
                "applied_at": datetime.now().isoformat(timespec="seconds"),
                **details,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.status.set(f"{manifest['carrier']} 신분증 확인 및 저장 완료")
        self._refresh_queue()

    @staticmethod
    def _job_file(job_dir: Path, relative_name: str) -> Path:
        relative = _safe_package_path(relative_name)
        target = job_dir.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(job_dir.resolve()) or not target.is_file():
            raise ValueError(f"작업 패키지 파일을 찾지 못했습니다: {relative_name}")
        return target

    def _receive_finished(
        self,
        outcome: str | None,
        new_job_id: str | None = None,
    ) -> None:
        self._processing = False
        self.receive_progress_frame.grid_remove()
        self.receive_progress_bar.configure(value=0)
        if self._pending_received_reset is not None:
            self._finish_received_reset()
            return
        if new_job_id:
            self._selected_job_id = new_job_id
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
        if self._correction_busy:
            messagebox.showinfo(
                "수정 적용 중",
                "수신기 수정 저장이 끝난 뒤 초기화할 수 있습니다.",
                parent=self.root,
            )
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
            self._ready_jobs.clear()
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
            if not (
                (manifest_path.parent / "received.json").exists()
                or (manifest_path.parent / "applied.json").exists()
            ):
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
        active_keys = self._ready_document_keys()
        for key, item in self._items.items():
            if key in active_keys:
                item["state"] = "스캔 중"
        self._render_document_tree(self.scan_tree, self._scan_order, active_keys, "스캔 준비")
        self._render_document_tree(self.wait_tree, self._queue_order, active_keys, "대기 중")
        self.scan_count_var.set(f"{len(self._scan_order)}개 · ready 폴더 등록")
        self.wait_count_var.set(f"{len(self._queue_order)}개 · 아직 스캔 목록에 없음")
        self.completed_count.set(f"스캔 완료 {len(self._completed)}개")
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
                values=(document.get("page_count", 0),
                        "ready 등록" if key in active_keys else idle_state),
            )

    def _is_document_deleted(self, key: str) -> bool:
        return any(
            entry.get("key") == key
            for action in self._document_undo_stack
            for entry in action.get("entries", [])
            if isinstance(entry, dict)
        )

    def _delete_received_job(self, job_id: str) -> None:
        self._sync_active_scan()
        if self._correction_busy:
            raise ValueError("신분증 수정값을 저장 중이라 접수 건을 지울 수 없습니다.")
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("지울 접수 건을 찾지 못했습니다.")
        job_id = str(job["job_id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
            raise ValueError("접수 작업 ID가 올바르지 않습니다.")

        keys = [key for key, item in self._items.items() if str(item.get("job_id")) == job_id]
        if any(set(job.get("keys", [])).intersection(keys) for job in self._ready_jobs):
            raise ValueError("이 접수 건의 서류가 ready 폴더에 있어 삭제할 수 없습니다. 스캔 대기열에서 먼저 취소하세요.")

        work_root = Path(self.config.get("work_path", "")).expanduser().resolve()
        jobs_root = (work_root / "jobs").resolve()
        source = Path(job["job_dir"]).resolve()
        source.relative_to(jobs_root)
        if source.parent != jobs_root or source.is_symlink() or not source.is_dir():
            raise ValueError("접수 폴더 경로가 올바르지 않아 삭제할 수 없습니다.")
        trash_root = jobs_root / ".job-trash"
        if trash_root.is_symlink():
            raise ValueError("접수 휴지통 경로가 바로가기로 연결되어 있습니다.")
        trash_root.mkdir(parents=True, exist_ok=True)
        trash_root.resolve().relative_to(jobs_root)
        action_id = uuid4().hex
        trash = trash_root / action_id / job_id
        trash.parent.mkdir(parents=True, exist_ok=False)

        queue_entries = []
        for key in keys:
            if key in self._scan_order:
                lane, index = "scan", self._scan_order.index(key)
            elif key in self._queue_order:
                lane, index = "queue", self._queue_order.index(key)
            else:
                lane, index = "completed", len(self._completed)
            queue_entries.append({"key": key, "lane": lane, "index": index, "completed": key in self._completed})

        action = {
            "kind": "job",
            "job_id": job_id,
            "job_rel": source.relative_to(work_root).as_posix(),
            "trash_rel": trash.relative_to(work_root).as_posix(),
            "queue_entries": queue_entries,
        }
        previous_scan, previous_queue = list(self._scan_order), list(self._queue_order)
        previous_completed = set(self._completed)
        try:
            os.replace(source, trash)
            self._scan_order = [key for key in self._scan_order if key not in keys]
            self._queue_order = [key for key in self._queue_order if key not in keys]
            self._completed.difference_update(keys)
            self._document_undo_stack.append(action)
            self._save_queue_state()
        except (OSError, ValueError) as exc:
            if self._document_undo_stack and self._document_undo_stack[-1] is action:
                self._document_undo_stack.pop()
            self._scan_order, self._queue_order = previous_scan, previous_queue
            self._completed = previous_completed
            if trash.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(trash, source)
            raise ValueError(f"접수 건을 삭제하지 못했습니다: {exc}") from exc

        if getattr(self, "_web_selected_job_id", "") == job_id:
            self._web_selected_job_id = ""
        if getattr(self, "_selected_job_id", "") == job_id:
            self._selected_job_id = ""
        self._refresh_queue()
        self.status.set("접수 건을 삭제했습니다 · Ctrl+Z로 되돌릴 수 있습니다")

    def _undo_received_job_delete(self, action: dict) -> str:
        work_root = Path(self.config.get("work_path", "")).expanduser().resolve()
        jobs_root = (work_root / "jobs").resolve()
        source = (work_root / Path(*PurePosixPath(action["job_rel"]).parts)).resolve()
        trash = (work_root / Path(*PurePosixPath(action["trash_rel"]).parts)).resolve()
        source.relative_to(jobs_root)
        trash.relative_to(jobs_root / ".job-trash")
        if source.parent != jobs_root or source.exists():
            raise ValueError("원래 접수 폴더가 이미 있어 복구하지 않았습니다.")
        if trash.is_symlink() or not trash.is_dir():
            raise ValueError("휴지통에서 접수 폴더를 찾을 수 없습니다.")
        source.parent.mkdir(parents=True, exist_ok=True)
        os.replace(trash, source)
        self._document_undo_stack.pop()
        for entry in action.get("queue_entries", []):
            key = entry.get("key")
            lane = entry.get("lane")
            if not isinstance(key, str):
                continue
            if lane in {"scan", "queue"}:
                order = self._scan_order if lane == "scan" else self._queue_order
                if key not in order:
                    order.insert(min(int(entry.get("index", len(order))), len(order)), key)
            if entry.get("completed"):
                self._completed.add(key)
        try:
            self._save_queue_state()
        except OSError:
            self._document_undo_stack.append(action)
            for entry in action.get("queue_entries", []):
                key = entry.get("key")
                if isinstance(key, str):
                    self._scan_order[:] = [item for item in self._scan_order if item != key]
                    self._queue_order[:] = [item for item in self._queue_order if item != key]
                    self._completed.discard(key)
            os.replace(source, trash)
            raise
        self._web_selected_job_id = str(action.get("job_id", ""))
        self._selected_job_id = self._web_selected_job_id
        self._refresh_queue()
        self.status.set("마지막 접수 건 삭제를 되돌렸습니다")
        return "break"

    def _delete_selected_documents(
        self, tree: ttk.Treeview, *, already_confirmed: bool = False,
    ) -> str:
        self._sync_active_scan()
        order = self._scan_order if tree is self.scan_tree else self._queue_order
        selected = set(tree.selection())
        keys = [key for key in order if key in selected and key in self._items]
        if not keys:
            return "break"

        active_keys = self._ready_document_keys()
        if active_keys.intersection(keys):
            messagebox.showinfo(
                "스캔 진행 중", "현재 스캔 중인 서류는 삭제할 수 없습니다.", parent=self.root,
            )
            return "break"

        names = [
            str(self._items[key]["document"].get("source_name", "서류")) for key in keys
        ]
        if not already_confirmed:
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
        if action.get("kind") == "job":
            try:
                return self._undo_received_job_delete(action)
            except (KeyError, OSError, ValueError) as exc:
                messagebox.showerror("접수 삭제 취소 실패", str(exc), parent=self.root)
                return "break"
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
            stamp = datetime.fromtimestamp(job["mtime"]).strftime("%m-%d %H:%M:%S")
            values = manifest.get("reviewed_values", {})
            customer = values.get("custNm", "") if isinstance(values, dict) else ""
            if not customer:
                documents = manifest.get("documents", [])
                customer = (
                    "PDF 접수" if not manifest.get("identity_image")
                    else f"신분증 · 서류 {len(documents) if isinstance(documents, list) else 0}건"
                )
            label = f"{manifest.get('carrier', '접수')} · {_display_text(customer)} · {stamp}"
            original_label = label
            suffix = 2
            while label in self._job_labels:
                label = f"{original_label} ({suffix})"
                suffix += 1
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
        if not self._correction_busy:
            self._set_identity_editing(False)
        self._selected_job_id = job_id
        job = self._jobs.get(job_id)
        if job is None:
            self.preview.configure(image="", text="접수 작업을 선택하면 신분증이 크게 표시됩니다")
            self._photo = None
            self._set_identity_fields_visible(False)
            for variable in self.identity_vars.values():
                variable.set("")
            self.identity_hint.set("아직 표시할 접수 결과가 없습니다.")
            self._set_identity_editing(False)
            return
        manifest = job["manifest"]
        image_name = manifest.get("identity_image", "")
        if isinstance(image_name, str) and image_name:
            try:
                image_path = self._job_file(job["job_dir"], image_name)
                with Image.open(image_path) as image:
                    self._render_identity_image(image)
            except (OSError, TypeError, ValueError) as exc:
                self.preview.configure(image="", text=f"신분증 이미지를 열지 못했습니다\n{exc}")
                self._photo = None
        else:
            self.preview.configure(image="", text="신분증 없이 PDF 서류만 받은 작업입니다")
            self._photo = None
        self._set_identity_fields_visible(bool(image_name))
        values = manifest.get("reviewed_values", {})
        if not isinstance(values, dict):
            values = {}
        detection = manifest.get("id_type_detection", {})
        if not isinstance(detection, dict):
            detection = {}
        code = str(values.get("kaitIdType") or manifest.get("id_type_code", ""))
        id_type = ID_TYPES.get(code, {}).get("name") or detection.get("name") or values.get("idType", "")
        type_label = next(
            (label for label, item_code in self._identity_type_label_to_code.items()
             if item_code == code),
            "",
        )
        self.identity_vars["id_type"].set(type_label or _display_text(id_type))
        for key in (
            "custNm", "ctzCorpNum", "addr", "isueDt", "drvLicenIsueNum",
            "isuePlc", "veteranNum", "mskPos",
        ):
            self.identity_vars[key].set(_display_text(values.get(key, "")))
        document_count = len(manifest.get("documents", [])) if isinstance(manifest.get("documents"), list) else 0
        if not image_name:
            correction_hint = "서류 전용 작업 · 신분증 결과는 변경하지 않음"
        else:
            correction_hint = (
                "수신 PC에서 수정 가능"
                if self._job_can_correct(job)
                else "구버전 KT 작업 · 최초 적용 행 ID가 없어 수정은 새 전송부터 가능"
            )
        self.identity_hint.set(
            f"{manifest.get('carrier', '')} 접수 · 서류 {document_count}개 · {correction_hint}"
        )
        self._set_identity_editing(False)

    def _set_identity_fields_visible(self, visible: bool) -> None:
        for label, entry, _row in self._identity_field_rows.values():
            if visible:
                label.grid()
                entry.grid()
            else:
                label.grid_remove()
                entry.grid_remove()

    @staticmethod
    def _job_can_correct(job: dict) -> bool:
        manifest = job.get("manifest", {})
        if not isinstance(manifest, dict) or not manifest.get("identity_image"):
            return False
        carrier = job.get("carrier") or (
            manifest.get("carrier") if isinstance(manifest, dict) else None
        )
        if carrier == "SK":
            return True
        if carrier != "KT":
            return False
        try:
            marker = json.loads((job["job_dir"] / "applied.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return False
        return (
            isinstance(marker, dict)
            and isinstance(marker.get("kt_element_id"), str)
            and bool(marker["kt_element_id"])
        )

    def _set_identity_editing(self, editing: bool) -> None:
        self._identity_editing = editing
        busy = self._correction_busy
        job = self._jobs.get(getattr(self, "_selected_job_id", ""))
        selected = job is not None and self._job_can_correct(job)
        for entry in self._identity_entries.values():
            entry.configure(state="normal" if editing and not busy else "readonly")
        self.identity_type_box.configure(
            state="readonly" if editing and not busy else "disabled"
        )
        self.job_box.configure(state="disabled" if editing or busy else "readonly")
        self.edit_identity_button.configure(
            state="normal" if selected and not editing and not busy else "disabled"
        )
        self.cancel_identity_edit_button.configure(
            state="normal" if editing and not busy else "disabled"
        )
        self.save_identity_edit_button.configure(
            state="normal" if editing and not busy else "disabled"
        )

    def _begin_identity_edit(self) -> None:
        if self._selected_job_id not in self._jobs or self._correction_busy:
            return
        self._set_identity_editing(True)
        self.identity_hint.set(
            "값을 수정한 뒤 ‘수정 적용’을 누르면 이 접수 건의 SK 보고서 또는 KT DB/JPG에 반영됩니다."
        )

    def _cancel_identity_edit(self) -> None:
        if self._correction_busy:
            return
        self._show_job(self._selected_job_id)

    def _save_identity_edit(self) -> None:
        job = self._jobs.get(self._selected_job_id)
        if job is None or not self._identity_editing or self._correction_busy:
            return
        code = self._identity_type_label_to_code.get(self.identity_vars["id_type"].get())
        if code not in ID_TYPES:
            messagebox.showwarning("신분증 종류 확인", "신분증 종류를 선택하세요.", parent=self.root)
            return
        manifest = dict(job["manifest"])
        original_values = manifest.get("reviewed_values", {})
        values = dict(original_values) if isinstance(original_values, dict) else {}
        values.update({
            key: self.identity_vars[key].get()
            for key in (
                "custNm", "ctzCorpNum", "addr", "isueDt", "drvLicenIsueNum",
                "isuePlc", "veteranNum", "mskPos",
            )
        })
        values["kaitIdType"] = code
        values["recogSubCd"] = code
        values["idType"] = ID_TYPES[code]["type"]
        values["nationClCd"] = "F" if code == "1401" else "I"
        carrier = str(manifest.get("carrier", ""))
        explanation = (
            "SK sk_report.txt를 수정하고 기존 파일은 백업합니다."
            if carrier == "SK"
            else "최초 적용한 같은 KT DB 행과 ORG.jpg/CR.jpg를 수정하고 변경 전 파일은 백업합니다."
        )
        if not messagebox.askyesno(
            "수정 내용 적용",
            f"선택한 접수 건의 수정값을 적용할까요?\n\n{explanation}\n"
            "이 수정은 중앙 PC로 되돌아가지 않습니다.",
            parent=self.root,
        ):
            return
        self._correction_busy = True
        self._set_identity_editing(False)
        self.status.set("수신 PC 수정값 저장 중…")
        Thread(
            target=self._apply_identity_correction,
            args=(self._selected_job_id, job["job_dir"], manifest, values, code),
            daemon=True,
        ).start()

    def _save_web_identity_edit(
        self, job_id: str, carrier: str, code: str, values: dict[str, str],
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None or not job.get("manifest", {}).get("identity_image"):
            raise ValueError("수정할 신분증 접수 건을 찾지 못했습니다.")
        if self._correction_busy:
            raise ValueError("다른 수정 작업을 저장 중입니다.")
        if carrier not in {"SK", "KT"} or code not in ID_TYPES:
            raise ValueError("포탈과 신분증 종류를 확인하세요.")
        manifest = dict(job["manifest"])
        crop_value = values.get("_idCropBounds")
        if crop_value:
            try:
                crop_bounds = tuple(int(part) for part in crop_value.split(","))
                image_path = self._job_file(
                    job["job_dir"], str(manifest.get("identity_image", ""))
                )
                with Image.open(image_path) as identity_image:
                    image_width, image_height = identity_image.size
            except (OSError, TypeError, ValueError):
                raise ValueError("idCrop 영역 또는 신분증 이미지를 확인할 수 없습니다.") from None
            if (
                len(crop_bounds) != 4
                or crop_bounds[0] < 0 or crop_bounds[1] < 0
                or crop_bounds[2] <= crop_bounds[0]
                or crop_bounds[3] <= crop_bounds[1]
                or crop_bounds[2] > image_width
                or crop_bounds[3] > image_height
            ):
                raise ValueError("idCrop 영역이 신분증 이미지 범위를 벗어났습니다.")
        marker_path = Path(job["job_dir"]) / "applied.json"
        if marker_path.exists():
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("기존 저장 상태를 읽지 못해 수정할 수 없습니다.") from exc
            if marker.get("carrier") != carrier:
                raise ValueError(
                    "이미 적용된 자료의 포탈은 여기서 변경할 수 없습니다. "
                    "기존 포탈 자료를 먼저 정리한 뒤 수정하세요."
                )
        manifest["carrier"] = carrier
        manifest["id_type_code"] = code
        manifest["id_type_detection"] = {
            "name": ID_TYPES[code]["name"],
            "type_code": ID_TYPES[code]["type"],
            "candidate_codes": [code],
            "preferred_code": code,
        }
        manifest["reviewed_values"] = {
            **(manifest.get("reviewed_values", {}) if isinstance(manifest.get("reviewed_values"), dict) else {}),
            **values,
            "kaitIdType": code,
            "recogSubCd": code,
            "idType": ID_TYPES[code]["type"],
            "nationClCd": "F" if code == "1401" else "I",
        }
        self._correction_busy = True
        self.status.set("수정값 저장 중…")
        Thread(
            target=self._apply_identity_correction,
            args=(job_id, job["job_dir"], manifest, manifest["reviewed_values"], code),
            kwargs={"carrier_override": carrier, "web": True},
            daemon=True,
        ).start()

    def _apply_identity_correction(
        self,
        job_id: str,
        job_dir: Path,
        manifest: dict,
        values: dict[str, str],
        code: str,
        *,
        carrier_override: str | None = None,
        web: bool = False,
    ) -> None:
        try:
            carrier = carrier_override or str(manifest.get("carrier", ""))
            image_path = self._job_file(job_dir, str(manifest.get("identity_image", "")))
            result = OcrResult(job_id=job_id, image_path=image_path)
            detection = IdTypeDetection(
                name=ID_TYPES[code]["name"],
                type_code=ID_TYPES[code]["type"],
                candidate_codes=(code,),
                preferred_code=code,
            )
            updated_manifest = dict(manifest)
            updated_manifest["id_type_code"] = code
            updated_manifest["id_type_detection"] = {
                "name": detection.name,
                "type_code": detection.type_code,
                "candidate_codes": [code],
                "preferred_code": code,
            }
            updated_manifest["reviewed_values"] = values

            marker_path = job_dir / "applied.json"
            marker_exists = marker_path.exists()
            marker = (
                json.loads(marker_path.read_text(encoding="utf-8"))
                if marker_exists else {}
            )
            if marker_exists and marker.get("carrier") != carrier:
                raise ValueError("적용 완료 정보의 포탈이 작업 manifest와 다릅니다.")
            history_path = (
                Path(self.config["work_path"]).expanduser()
                / "backups" / "receiver-corrections.json"
            )
            history_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                history = json.loads(history_path.read_text(encoding="utf-8"))
                if not isinstance(history, list):
                    raise ValueError("수정 이력 파일 형식이 올바르지 않습니다.")
            except FileNotFoundError:
                history = []
            if not marker_exists and carrier == "SK":
                staged_report = job_dir / f".sk-initial-{uuid4().hex}.txt"
                try:
                    write_ocr_result(result, detection, values=values, path=staged_report)
                    self._apply_sk_report(staged_report)
                finally:
                    staged_report.unlink(missing_ok=True)
                marker = {"carrier": carrier, "identity_applied": True}
            elif not marker_exists and carrier == "KT":
                marker = {
                    "carrier": carrier,
                    "identity_applied": True,
                    "kt_element_id": self._apply_kt_result(job_dir, updated_manifest),
                }
            elif carrier == "SK":
                staged_report = job_dir / f".sk-correction-{uuid4().hex}.txt"
                try:
                    write_ocr_result(result, detection, values=values, path=staged_report)
                    self._apply_sk_report(staged_report)
                finally:
                    staged_report.unlink(missing_ok=True)
            elif carrier == "KT":
                target_element_id = marker.get("kt_element_id")
                if not isinstance(target_element_id, str) or not target_element_id:
                    raise ValueError(
                        "이 KT 작업은 최초 적용 행 ID가 기록되지 않은 구버전입니다. "
                        "다른 접수 건을 잘못 수정하지 않도록 수정 적용을 중단했습니다."
                    )
                image_dir = Path(self.config["kt_image_directory"]).expanduser()
                backup_dir = (
                    Path(self.config["work_path"]).expanduser() / "backups"
                    / f"kt_images_{job_id}_{datetime.now():%Y%m%d_%H%M%S_%f}"
                )
                image_backups: dict[str, Path] = {}
                missing_images: set[str] = set()
                for name in ("ORG.jpg", "CR.jpg"):
                    source = image_dir / name
                    if source.is_file():
                        backup_dir.mkdir(parents=True, exist_ok=True)
                        backup_path = backup_dir / name
                        shutil.copy2(source, backup_path)
                        image_backups[name] = backup_path
                    else:
                        missing_images.add(name)
                try:
                    self._apply_kt_result(
                        job_dir, updated_manifest, target_element_id=target_element_id,
                    )
                except Exception:
                    for name in missing_images:
                        (image_dir / name).unlink(missing_ok=True)
                    for name, backup_path in image_backups.items():
                        shutil.copy2(backup_path, image_dir / name)
                    raise
                marker["corrected_at"] = datetime.now().isoformat(timespec="seconds")
            else:
                raise ValueError("지원하지 않는 포탈 작업입니다.")

            corrected_at = datetime.now().isoformat(timespec="seconds")
            if not marker_exists:
                marker["applied_at"] = corrected_at
            history.append({
                "job_id": job_id,
                "corrected_at": corrected_at,
                "carrier": carrier,
                "before": manifest.get("reviewed_values", {}),
                "after": values,
            })
            self._write_json_atomic(history_path, history)
            self._write_json_atomic(job_dir / "manifest.json", updated_manifest)
            self._write_json_atomic(marker_path, marker)
            callback = self._web_identity_correction_finished if web else self._identity_correction_finished
            self.root.after(0, callback, job_id, "")
        except Exception as exc:
            callback = self._web_identity_correction_finished if web else self._identity_correction_finished
            self.root.after(0, callback, job_id, str(exc))

    @staticmethod
    def _write_json_atomic(path: Path, value: object) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _identity_correction_finished(self, job_id: str, error: str) -> None:
        self._correction_busy = False
        self._set_identity_editing(False)
        if error:
            self.status.set("수신 PC 수정 적용 실패")
            self._show_job(job_id)
            messagebox.showerror(
                "수정 적용 실패",
                f"수정값을 적용하지 못했습니다. 백업과 업무 데이터 상태를 확인하세요.\n{error}",
                parent=self.root,
            )
            return
        self._refresh_queue()
        self.status.set("수신 PC 수정값 적용 완료 · 중앙 PC에는 반영되지 않음")
        messagebox.showinfo(
            "수정 적용 완료",
            "수정값을 이 접수 건에 적용했습니다. 변경 전 값은 로컬 수정 이력과 백업에 보관했습니다.",
            parent=self.root,
        )

    def _web_identity_correction_finished(self, job_id: str, error: str) -> None:
        self._correction_busy = False
        if error:
            self.status.set("수신 PC 수정 적용 실패")
            return
        job = self._jobs.get(job_id)
        if job is not None:
            try:
                job["manifest"] = json.loads(
                    (job["job_dir"] / "manifest.json").read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                pass
        self._refresh_queue()
        self.status.set("수정값 저장 완료")

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
        is_promoting = source_tree is self.wait_tree and target_tree is self.scan_tree
        is_demoting = source_tree is self.scan_tree and target_tree is self.wait_tree
        demoted_jobs = [
            job for job in self._ready_jobs
            if is_demoting and set(job.get("keys", [])).intersection(keys)
        ]
        if demoted_jobs:
            # A staged copy is what the TWAIN source scans. Move it out of
            # ready before returning the original received document to the wait
            # list. If Acquire is already reading it, that scan can fail.
            keys = list(dict.fromkeys(
                key
                for job in demoted_jobs
                for key in job.get("keys", [])
                if key in source_order and key in self._items
            ))
            if not keys:
                return

        published: list[dict[str, object]] = []
        quarantined: list[tuple[Path, Path]] = []
        quarantine_root: Path | None = None
        previous_source = list(source_order)
        previous_target = list(target_order)
        previous_ready_jobs = list(self._ready_jobs)
        try:
            if is_promoting:
                self._validate_ready_directory()
                for key in keys:
                    published.append(self._publish_ready_document(key))
            elif demoted_jobs:
                ready = self._validate_ready_directory()
                if (ready.is_symlink() or ready.name.casefold() != "ready"
                        or ready.resolve() == Path(ready.anchor)):
                    raise ValueError("가상 스캐너 ready 경로가 안전한 폴더가 아닙니다.")
                quarantine_root = ready.parent / f".ready-demoting-{uuid4().hex}"
                quarantine_root.mkdir(parents=False, exist_ok=False)
                for job in demoted_jobs:
                    scanner_job_id = str(job.get("scanner_job_id", ""))
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scanner_job_id):
                        raise ValueError("ready 작업 ID가 올바르지 않습니다.")
                    staged = ready / scanner_job_id
                    if not staged.exists():
                        continue
                    if staged.is_symlink() or not staged.is_dir():
                        raise ValueError(f"ready 작업 폴더가 올바르지 않습니다: {scanner_job_id}")
                    held = quarantine_root / scanner_job_id
                    os.replace(staged, held)
                    quarantined.append((held, staged))

            selected = set(keys)
            source_order[:] = [key for key in source_order if key not in selected]
            target_order.extend(key for key in keys if key not in target_order)
            if is_promoting:
                self._ready_jobs.extend(published)
                self._apply_ready_job_order()
            elif demoted_jobs:
                demoted_ids = {str(job.get("scanner_job_id", "")) for job in demoted_jobs}
                self._ready_jobs = [
                    job for job in self._ready_jobs
                    if str(job.get("scanner_job_id", "")) not in demoted_ids
                ]
            self._save_queue_state()
        except (OSError, ValueError, RuntimeError) as exc:
            source_order[:] = previous_source
            target_order[:] = previous_target
            self._ready_jobs = previous_ready_jobs
            for held, staged in reversed(quarantined):
                if held.exists() and not staged.exists():
                    try:
                        os.replace(held, staged)
                    except OSError:
                        pass
            for job in published:
                shutil.rmtree(
                    Path(self.config.get("scanner_ready_path", ""))
                    / str(job.get("scanner_job_id", "")),
                    ignore_errors=True,
                )
            self._refresh_queue()
            messagebox.showerror("ready 등록 실패", str(exc), parent=self.root)
            return
        finally:
            if quarantine_root is not None:
                shutil.rmtree(quarantine_root, ignore_errors=True)

        self._refresh_queue()
        for key in keys:
            target_tree.selection_add(key)
        if is_promoting:
            self.status.set(f"서류 {len(keys)}개를 스캔 목록으로 옮기고 ready에 등록했습니다")
        elif demoted_jobs:
            self.status.set(
                f"서류 {len(keys)}개를 대기열로 내렸습니다 · 스캔 중이었다면 해당 스캔은 실패할 수 있습니다"
            )
        else:
            self.status.set(f"서류 {len(keys)}개를 스캔 서류 대기열로 옮겼습니다")

    def _validate_ready_directory(self) -> Path:
        ready_value = self.config.get("scanner_ready_path", "").strip()
        if not ready_value:
            raise ValueError("Receiver 설정에서 가상 스캐너 ready 폴더를 지정하세요.")
        ready = Path(ready_value).expanduser()
        ready.mkdir(parents=True, exist_ok=True)
        known_ids = {
            str(job.get("scanner_job_id", "")) for job in self._ready_jobs
        }
        unexpected = [
            item.name for item in ready.iterdir()
            if item.is_dir() and item.name not in known_ids
        ]
        if unexpected:
            raise RuntimeError(
                "ready 폴더에 Receiver가 관리하지 않는 작업이 있습니다. "
                "기존 작업을 확인한 뒤 다시 옮기세요: " + ", ".join(unexpected[:5])
            )
        return ready

    def _publish_ready_document(self, key: str) -> dict[str, object]:
        ready = Path(self.config["scanner_ready_path"]).expanduser()
        item = self._items[key]
        document = item["document"]
        pages = document.get("pages", [])
        if not isinstance(pages, list) or not pages:
            raise ValueError(f"{document.get('label', '서류')}에 JPG 페이지가 없습니다.")
        if len(pages) > 100:
            raise ValueError("가상 스캐너 작업은 한 번에 최대 100페이지까지 올릴 수 있습니다.")

        scanner_job_id = uuid4().hex
        staging = ready / f".{scanner_job_id}.incoming"
        destination = ready / scanner_job_id
        try:
            staging.mkdir(parents=True, exist_ok=False)
            page_manifest = []
            for number, relative in enumerate(pages, start=1):
                source = self._job_file(item["job_dir"], str(relative))
                target = staging / f"{number:03d}.jpg"
                shutil.copy2(source, target)
                page_manifest.append({
                    "number": number,
                    "file": target.name,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                })
            manifest = {
                "job_id": scanner_job_id,
                "station_id": self.config["station_id"],
                "source_name": _display_text(
                    document.get("source_name", document.get("label", "documents"))
                )[:255],
                "document_keys": [key],
                "dpi": 100,
                "page_count": len(page_manifest),
                "pages": page_manifest,
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(staging, destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return {"scanner_job_id": scanner_job_id, "keys": [key]}

    def _apply_ready_job_order(self) -> None:
        positions = {key: index for index, key in enumerate(self._scan_order)}
        self._ready_jobs.sort(
            key=lambda job: min(
                (positions.get(key, len(positions)) for key in job.get("keys", [])),
                default=len(positions),
            )
        )
        ready = Path(self.config["scanner_ready_path"]).expanduser()
        base_ns = time_ns() - len(self._ready_jobs) * 1_000_000_000
        for index, job in enumerate(self._ready_jobs):
            directory = ready / str(job["scanner_job_id"])
            if directory.is_dir():
                stamp = base_ns + index * 1_000_000_000
                os.utime(directory, ns=(stamp, stamp))

    def _reset_document_lists(self) -> None:
        self._sync_active_scan()
        ready = Path(self.config.get("scanner_ready_path", ""))
        if self._ready_jobs or (ready.is_dir() and any(ready.iterdir())):
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
        index = order.index(key)
        new_index = index + offset
        if not 0 <= new_index < len(order):
            return
        order[index], order[new_index] = order[new_index], order[index]
        if tree is self.scan_tree:
            self._apply_ready_job_order()
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

def main() -> None:
    from new_work.web_receiver import main as web_receiver_main

    web_receiver_main()


if __name__ == "__main__":
    main()
