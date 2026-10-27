from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from time import perf_counter
import shutil
import sqlite3
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

from PIL import Image, ImageDraw, ImageOps, ImageTk
from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
from urllib.parse import unquote_plus

from new_work.domain import REVIEW_TEXT_FIELDS, OcrResult, ReviewDraft
from new_work.carrier.sk_report import SK_ID_CROP_CODES
from new_work.ocr.adapter import OcrAdapter, OcrUnavailableError, PaddleOcrAdapter
from new_work.ocr.id_types import ID_TYPES, IdTypeDetection, detect_id_type
from new_work.ocr.field_extractor import extract_identity_fields
from new_work.ocr.mask_position import detect_mask_position
from new_work.ocr.result_writer import default_result_path, write_ocr_result
from new_work.carrier.kt import default_kt_image_directory, save_kt_submission
from new_work.image.face_crop import id_portrait_bounds, id_portrait_size
from new_work.windows_lifecycle import (
    create_tray_icon,
    register_current_user_autostart,
    unregister_current_user_autostart,
)
from new_work.pdf_render import default_job_directory, render_pdf_to_jpegs
from new_work.job_package import create_work_package
from new_work.stores import Store, load_stores
from new_work.gui.stores_dialog import StoreManagerDialog
from new_work.relay_config import load_relay_config, save_relay_config
from new_work.relay_client import (
    DeliveryPendingError,
    get_work_package_status,
    send_work_package,
)


ID_TYPE_LABEL_TO_CODE: dict[str, str] = {}
for _code, _data in ID_TYPES.items():
    ID_TYPE_LABEL_TO_CODE.setdefault(_data["name"], _code)
# Several legacy codes share one human-readable document name. Keep the
# familiar names in the selector and choose the project's default code.
ID_TYPE_LABEL_TO_CODE["운전면허증"] = "1102"
ID_TYPE_LABEL_TO_CODE["복지카드"] = "1301"
ID_TYPE_CODE_TO_LABEL = {code: label for label, code in ID_TYPE_LABEL_TO_CODE.items()}
FOCUS_FIELDS = {
    "resident": (
        ("custNm", "이름"), ("ctzCorpNum", "주민등록번호"),
        ("addr", "주소"), ("isueDt", "발급일자"), ("isuePlc", "발급기관"),
    ),
    "driver": (
        ("custNm", "이름"), ("ctzCorpNum", "주민등록번호"), ("addr", "주소"),
        ("isueDt", "발급일자"), ("drvLicenIsueNum", "운전면허번호"),
        ("isuePlc", "발급기관"),
    ),
    "foreigner": (
        ("custNm", "성명"), ("ctzCorpNum", "외국인등록번호"),
        ("addr", "주소"), ("isueDt", "발급일자"), ("isuePlc", "발급기관"),
    ),
    "welfare": (
        ("custNm", "이름"), ("ctzCorpNum", "등록번호"),
        ("addr", "주소"), ("isueDt", "발급일자"), ("isuePlc", "발급기관"),
    ),
    "veteran": (
        ("custNm", "이름"), ("ctzCorpNum", "주민등록번호"),
        ("veteranNum", "보훈번호"),
        ("addr", "주소"), ("isueDt", "발급일자"), ("isuePlc", "발급기관"),
    ),
    "passport": (("custNm", "성명"), ("ctzCorpNum", "여권번호"), ("isueDt", "발급일자")),
}
FOCUS_GROUP_BY_CODE = {
    "1001": "resident", "3001": "resident",
    "1101": "driver", "1102": "driver", "1201": "driver",
    "1301": "welfare", "1303": "welfare", "1302": "veteran",
    "1401": "foreigner", "2001": "passport", "2002": "passport",
}
# Temporary visual aid for checking the OCR mask coordinates on the source ID.
MASK_PREVIEW_ENABLED = True


def _parse_mask_position(value: str) -> tuple[int, int, int, int] | None:
    try:
        left, top, right, bottom = (
            int(part.strip()) for part in unquote_plus(value).split(",")
        )
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


class NewWorkApp:
    def __init__(self, root: tk.Tk, ocr: OcrAdapter | None = None,
                 version: str = "v2") -> None:
        if version not in {"v1", "v2"}:
            raise ValueError("version must be 'v1' or 'v2'")
        self.root = root
        self.version = version
        self.ocr = ocr or PaddleOcrAdapter()
        self.draft: ReviewDraft | None = None
        self._preview: ImageTk.PhotoImage | None = None
        self._preview_base: Image.Image | None = None
        self._source_image: Image.Image | None = None
        self._id_crop_preview_bounds: tuple[int, int, int, int] | None = None
        self._id_crop_manually_adjusted = False
        self._id_crop_drag: dict[str, object] | None = None
        self._mask_preview_position: str | None = None
        self._entries: dict[str, ttk.Entry] = {}
        self._value_vars: dict[str, tk.StringVar] = {
            field: tk.StringVar(root) for field in REVIEW_TEXT_FIELDS
        }
        self._id_type_var = tk.StringVar(root, value="유형 선택…")
        self.portal_var = tk.StringVar(root, value="SK")
        self.portal_var.trace_add("write", self._portal_changed)
        self._preview_resize_job: str | None = None
        self._v1_fit_job: str | None = None
        self._scan_animation_job: str | None = None
        self._scan_y = 0
        self._scan_direction = 1
        self._scan_active = False
        self._scan_image_path: Path | None = None
        self._id_type_detection = IdTypeDetection(None, None)
        self._tray_icon = None
        self._ocr_warmup_finished = not isinstance(self.ocr, PaddleOcrAdapter)
        self._ocr_models_ready = self._ocr_warmup_finished
        self._pending_ocr_path: Path | None = None
        self._document_job_id: str | None = None
        self._document_jobs: list[dict[str, object]] = []
        self._job_sent = False
        self._pending_confirmation: tuple[str, str, str, dict[str, str]] | None = None
        try:
            self.stores = load_stores()
        except ValueError as exc:
            self.stores = []
            self._store_config_error = str(exc)
        else:
            self._store_config_error = ""
        try:
            self.relay_config = load_relay_config()
            self._relay_config_error = ""
        except ValueError as exc:
            self.relay_config = None
            self._relay_config_error = str(exc)
        root.title("신분증 확인" if version == "v1"
                   else "신분증 확인 V2 · 선택 PC 전송")
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._handle_window_close)
        if isinstance(self.ocr, PaddleOcrAdapter):
            self.root.after_idle(self._begin_ocr_warmup)

    def _handle_window_close(self) -> None:
        if sys.platform != "win32":
            self.root.destroy()
            return
        self.root.withdraw()
        if self._tray_icon is not None:
            return
        try:
            self._tray_icon = create_tray_icon(
                lambda: self.root.after(0, self._restore_window),
                lambda: self.root.after(0, self._exit_from_tray),
            )
            Thread(target=self._tray_icon.run, daemon=True).start()
        except Exception as exc:
            self._tray_icon = None
            self.root.deiconify()
            messagebox.showerror(
                "트레이 실행 실패",
                f"프로그램을 트레이에 숨기지 못했습니다. 창은 계속 열어둡니다.\n{exc}",
                parent=self.root,
            )

    def _restore_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _exit_from_tray(self) -> None:
        icon = self._tray_icon
        self._tray_icon = None
        if icon is not None:
            icon.stop()
        self.root.destroy()

    def _begin_ocr_warmup(self) -> None:
        self.ocr_status.set("OCR 모델 준비 중 · 백그라운드에서 불러오는 중")
        self._show_ocr_loading()
        Thread(target=self._warmup_ocr, daemon=True).start()

    def _show_ocr_loading(self) -> None:
        overlay = self._loading_overlay
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        overlay.lift()

    def _hide_ocr_loading(self) -> None:
        if self._loading_overlay is not None:
            self._loading_overlay.destroy()
            self._loading_overlay = None
        # Removing a full-window child can leave stale pixels on Tk/Aqua until
        # the pointer crosses them. Flush geometry and redraw work immediately.
        self.root.update_idletasks()

    def _warmup_ocr(self) -> None:
        try:
            self.ocr.warmup()
        except Exception as exc:
            self.root.after(
                0, self._ocr_warmup_failed, str(exc),
            )
            return
        self.root.after(0, self._ocr_warmup_succeeded)

    def _ocr_warmup_succeeded(self) -> None:
        self._ocr_warmup_finished = True
        self._ocr_models_ready = True
        self._hide_ocr_loading()
        self.ocr_button.configure(state="normal")
        self.ocr_status.set(f"OCR 준비됨 · {self.ocr.status}")
        pending_path = self._pending_ocr_path
        self._pending_ocr_path = None
        if (
            pending_path is not None
            and self.draft is not None
            and self.draft.result.image_path == pending_path
        ):
            self._start_ocr(pending_path)

    def _ocr_warmup_failed(self, error: str) -> None:
        self._ocr_warmup_finished = True
        self._ocr_models_ready = False
        self._hide_ocr_loading()
        self.ocr_button.configure(state="normal")
        self.ocr_status.set(f"OCR 모델 준비 실패 · 다시 실행하면 재시도합니다: {error}")
        self._pending_ocr_path = None

    def _build(self) -> None:
        if self.version == "v1":
            self._build_v1()
        else:
            self._build_v2()

    def _build_v1(self) -> None:
        """Legacy OCR-only window backed by the current OCR and save logic."""
        self._setup_style()
        # This is the original compact V1 window size. Its height is adjusted
        # after OCR to fit the result rows and the source ID preview.
        self.root.geometry("560x700")
        self.root.resizable(False, False)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        shell = ttk.Frame(self.root, style="App.TFrame", padding=5)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)

        settings = ttk.Frame(shell, style="App.TFrame")
        settings.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="포탈", style="FieldName.TLabel").grid(
            row=0, column=0, sticky="w", padx=(2, 5)
        )
        portal_choices = ttk.Frame(settings, style="App.TFrame")
        portal_choices.grid(row=0, column=1, sticky="e")
        for portal in ("SK", "KT"):
            ttk.Radiobutton(
                portal_choices, text=portal,
                variable=self.portal_var, value=portal,
            ).pack(side="left", padx=(0, 8))

        self.result_table = ttk.Frame(shell, style="Card.TFrame", padding=(2, 1))
        self.result_table.grid(row=1, column=0, sticky="ew", pady=(0, 3))
        self.result_table.columnconfigure(1, weight=1)

        buttons = ttk.Frame(shell, style="App.TFrame")
        buttons.grid(row=2, column=0, pady=(1, 4))
        self.ocr_button = ttk.Button(buttons, text="가져오기", command=self.import_and_ocr)
        self.ocr_button.pack(side="left", padx=3)
        self.save_button = ttk.Button(buttons, text="저장하기", command=self.mark_reviewed)
        self.save_button.pack(side="left", padx=3)
        # Preserve current crop calculations without adding a crop control to
        # the legacy OCR window.
        self.crop_reset_button = ttk.Button(
            buttons, text="자동 영역 복원", command=self._reset_crop_bounds,
            state="disabled",
        )

        self.review_status = tk.StringVar(value="")
        self.ocr_status = tk.StringVar(value=f"OCR 준비됨 · {self.ocr.status}")
        self._loading_overlay = tk.Frame(self.root, background="#E9EDF3")
        tk.Label(
            self._loading_overlay, text="ㄱㄷ", background="#E9EDF3",
            foreground="#1D2939", font=("궁서", 72), borderwidth=0,
            highlightthickness=0,
        ).place(relx=0.5, rely=0.5, anchor="center")

        preview_area = tk.Frame(shell, background="#FFFFFF", borderwidth=0)
        self._v1_shell = shell
        self._v1_preview_area = preview_area
        preview_area.grid(row=3, column=0, sticky="nsew")
        preview_area.rowconfigure(0, weight=1)
        preview_area.columnconfigure(0, weight=1)
        self.image_label = tk.Label(
            preview_area, text="", anchor="center", justify="center",
            background="#FFFFFF", foreground="#667085", font=("Segoe UI", 9),
            borderwidth=0, highlightthickness=0,
        )
        self.image_label.grid(row=0, column=0, sticky="nsew")
        self.image_label.bind("<Configure>", self._schedule_preview_resize)
        self._refresh_result_table()
        self._register_drop_targets(self.root, self._handle_id_drop)

    def _fit_v1_window_to_content(self) -> None:
        self._v1_fit_job = None
        if (self.version != "v1" or self._source_image is None or self.draft is None
                or not self.draft.result.lines):
            return
        self.root.update_idletasks()
        target_image_width = max(1, 560 - 10 - 8)
        target_image_height = round(
            target_image_width * self._source_image.height / self._source_image.width
        )
        natural_preview_height = self._v1_preview_area.winfo_reqheight()
        requested_height = (
            self._v1_shell.winfo_reqheight()
            - natural_preview_height
            + target_image_height
        )
        screen_limit = max(560, self.root.winfo_screenheight() - 80)
        height = min(max(420, requested_height), screen_limit)
        self.root.geometry(f"560x{height}")
        self.root.update_idletasks()
        self._resize_source_preview()

    def _build_v2(self) -> None:
        self._setup_style()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.geometry("1000x760")
        self.root.minsize(850, 620)
        self.root.resizable(True, True)
        shell = ttk.Frame(self.root, style="App.TFrame", padding=8)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=3 if self.version == "v2" else 1, uniform="work_panels")
        shell.columnconfigure(1, weight=2 if self.version == "v2" else 0, uniform="work_panels")
        shell.rowconfigure(1, weight=1)

        settings = ttk.Frame(shell, style="App.TFrame")
        settings.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="포탈", style="FieldName.TLabel").grid(
            row=0, column=0, sticky="w", padx=(2, 5)
        )
        portal_choices = ttk.Frame(settings, style="App.TFrame")
        portal_choices.grid(row=0, column=1, sticky="e")
        self.portal_buttons = []
        for portal in ("SK", "KT"):
            button = ttk.Radiobutton(
                portal_choices,
                text=portal,
                variable=self.portal_var,
                value=portal,
            )
            button.pack(side="left", padx=(0, 8))
            self.portal_buttons.append(button)
        if self.version == "v2":
            ttk.Label(settings, text="접수 PC", style="FieldName.TLabel").grid(
                row=0, column=2, sticky="w", padx=(14, 5)
            )
            self.store_box = ttk.Combobox(settings, state="readonly", width=24)
            self.store_box.grid(row=0, column=3, sticky="e")
            ttk.Button(settings, text="접점 설정", command=self._open_store_manager).grid(
                row=0, column=4, sticky="e", padx=(6, 0)
            )
            ttk.Button(settings, text="Relay 서버", command=self._open_relay_settings).grid(
                row=0, column=5, sticky="e", padx=(6, 0)
            )

        ocr_panel = ttk.LabelFrame(shell, text="신분증 OCR", padding=7)
        ocr_panel.grid(row=1, column=0, columnspan=2 if self.version == "v1" else 1,
                       sticky="nsew", padx=(0, 5))
        ocr_panel.columnconfigure(0, weight=1)
        ocr_panel.rowconfigure(2, weight=1)

        buttons = ttk.Frame(ocr_panel, style="App.TFrame")
        buttons.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.ocr_button = ttk.Button(
            buttons, text="신분증 JPG 선택", command=self.import_and_ocr
        )
        self.ocr_button.pack(side="left", padx=(0, 5))
        self.save_button = ttk.Button(
            buttons,
            text="선택 PC로 저장" if self.version == "v2" else "저장하기",
            command=self.mark_reviewed,
        )
        self.save_button.pack(side="left")
        if self.version == "v2":
            self.status_button = ttk.Button(
                buttons, text="전송 상태 확인", command=self._check_pending_status,
                state="disabled",
            )
            self.status_button.pack(side="left", padx=(5, 0))
        self.crop_reset_button = ttk.Button(
            buttons, text="자동 영역 복원", command=self._reset_crop_bounds,
            state="disabled",
        )
        self.crop_reset_button.pack(side="left", padx=(5, 0))

        self.result_table = ttk.Frame(ocr_panel, style="Card.TFrame", padding=(4, 3))
        self.result_table.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        self.result_table.columnconfigure(1, weight=1)

        self.review_status = tk.StringVar(value="")
        self.ocr_status = tk.StringVar(value=f"OCR 준비됨 · {self.ocr.status}")
        self._loading_overlay = tk.Frame(self.root, background="#E9EDF3")
        tk.Label(
            self._loading_overlay,
            text="ㄱㄷ",
            background="#E9EDF3",
            foreground="#1D2939",
            font=("궁서", 72),
            borderwidth=0,
            highlightthickness=0,
        ).place(relx=0.5, rely=0.5, anchor="center")

        preview_area = tk.Frame(ocr_panel, background="#FFFFFF", borderwidth=0)
        preview_area.grid(row=2, column=0, sticky="nsew")
        preview_area.rowconfigure(0, weight=1)
        preview_area.columnconfigure(0, weight=1)
        self.image_label = tk.Label(
            preview_area,
            text="신분증 JPG를 선택하거나 이 영역에 끌어다 놓으세요",
            anchor="center",
            justify="center",
            background="#FFFFFF",
            foreground="#667085",
            font=("Segoe UI", 10),
            borderwidth=0,
            highlightthickness=0,
        )
        self.image_label.grid(row=0, column=0, sticky="nsew")
        self.image_label.bind("<Configure>", self._schedule_preview_resize)
        self.image_label.bind("<ButtonPress-1>", self._crop_pointer_down)
        self.image_label.bind("<B1-Motion>", self._crop_pointer_move)
        self.image_label.bind("<ButtonRelease-1>", self._crop_pointer_up)
        ttk.Label(
            ocr_panel, textvariable=self.ocr_status, style="Muted.TLabel"
        ).grid(row=3, column=0, sticky="w", pady=(5, 0))
        ttk.Label(
            ocr_panel, textvariable=self.review_status, style="Status.TLabel"
        ).grid(row=4, column=0, sticky="w", pady=(2, 0))
        ttk.Label(
            ocr_panel,
            text="파란 네모를 끌어 위치를 옮깁니다. 크기는 신분증 종류별 규격으로 고정됩니다.",
            style="Muted.TLabel",
        ).grid(row=5, column=0, sticky="w", pady=(2, 0))

        documents = ttk.LabelFrame(shell, text="서류 PDF", padding=7)
        if self.version == "v2":
            documents.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        documents.columnconfigure(0, weight=1)
        documents.rowconfigure(2, weight=1)
        ttk.Label(
            documents,
            text="PDF는 별도로 관리되며 신분증 OCR과 동시에 변환됩니다.",
            style="Muted.TLabel",
            wraplength=330,
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.pdf_button = ttk.Button(
            documents, text="서류 PDF 추가", command=self.add_pdf_documents
        )
        if self.version == "v2":
            self.pdf_button.grid(row=1, column=0, sticky="w", pady=(0, 5))
        self.document_table = ttk.Treeview(
            documents, columns=("status", "pages"), show="headings", height=10
        )
        self.document_table.heading("status", text="파일 / 상태")
        self.document_table.heading("pages", text="페이지 / 저장 위치")
        self.document_table.column("status", width=190, anchor="w", stretch=False)
        self.document_table.column("pages", width=420, anchor="w", stretch=False)
        if self.version == "v2":
            self.document_table.grid(row=2, column=0, sticky="nsew")
        document_scroll = ttk.Scrollbar(
            documents, orient="horizontal", command=self.document_table.xview
        )
        if self.version == "v2":
            document_scroll.grid(row=3, column=0, sticky="ew")
            self.document_table.configure(xscrollcommand=document_scroll.set)
        self._refresh_result_table()
        if self.version == "v2":
            self._refresh_stores()
        self._register_drop_targets(ocr_panel, self._handle_id_drop)
        if self.version == "v2":
            self._register_drop_targets(documents, self._handle_pdf_drop)

    def _register_drop_targets(self, widget: tk.Misc, handler) -> None:
        if not getattr(widget, "_new_work_drop_target", False):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", handler)
            widget._new_work_drop_target = True
        for child in widget.winfo_children():
            self._register_drop_targets(child, handler)

    def _refresh_stores(self) -> None:
        if not hasattr(self, "store_box"):
            return
        self.store_box["values"] = [
            f"{store.name} · {store.relay_station_id or '자리 ID 설정 필요'}"
            for store in self.stores
        ]
        if self.stores:
            self.store_box.current(0)
        else:
            self.store_box.set("접점 설정 필요")

    def _open_store_manager(self) -> None:
        StoreManagerDialog(self.root, self._stores_changed)

    def _open_relay_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Render Relay 설정")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text="Relay HTTPS 주소").grid(
            row=0, column=0, sticky="w", padx=10, pady=7
        )
        url_entry = ttk.Entry(dialog, width=58)
        url_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=7)
        ttk.Label(dialog, text="중앙 송신 토큰").grid(
            row=1, column=0, sticky="w", padx=10, pady=7
        )
        token_entry = ttk.Entry(dialog, width=58, show="•")
        token_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=7)
        if self.relay_config:
            url_entry.insert(0, self.relay_config["relay_url"])
            token_entry.insert(0, self.relay_config["central_token"])
        ttk.Label(
            dialog,
            text="토큰은 이 PC의 사용자 설정 폴더에 저장됩니다. Receiver 자리 토큰과 다릅니다.",
            wraplength=480,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 8))

        def save() -> None:
            try:
                save_relay_config(url_entry.get(), token_entry.get())
                self.relay_config = load_relay_config()
                self._relay_config_error = ""
            except (OSError, ValueError) as exc:
                messagebox.showerror("Relay 설정 오류", str(exc), parent=dialog)
                return
            dialog.destroy()
            messagebox.showinfo("Relay 설정", "중앙 Relay 설정을 저장했습니다.", parent=self.root)

        ttk.Button(dialog, text="저장", command=save).grid(
            row=3, column=1, sticky="e", padx=10, pady=10
        )
        dialog.columnconfigure(1, weight=1)

    def _stores_changed(self, stores: list[Store]) -> None:
        self.stores = stores
        self._refresh_stores()

    def _selected_store(self) -> Store | None:
        index = self.store_box.current()
        return self.stores[index] if 0 <= index < len(self.stores) else None

    def _set_transfer_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (self.ocr_button, self.pdf_button, self.store_box, *self.portal_buttons):
            widget.configure(state=state)
        for widget in self.result_table.winfo_children():
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(bg="#F2F4F7")
        style.configure("App.TFrame", background="#F2F4F7")
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("TLabel", background="#F2F4F7", foreground="#1D2939", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background="#F2F4F7", foreground="#667085", font=("Segoe UI", 9))
        style.configure("Title.TLabel", background="#F2F4F7", foreground="#101828", font=("Segoe UI", 13, "bold"))
        style.configure("Status.TLabel", background="#FFFFFF", foreground="#344054", font=("Segoe UI", 9, "bold"))
        style.configure("TableHead.TLabel", background="#FFFFFF", foreground="#667085", font=("Segoe UI", 9, "bold"))
        style.configure("FieldName.TLabel", background="#FFFFFF", foreground="#344054", font=("Segoe UI", 10, "bold"))
        style.configure("Original.TLabel", background="#FFFFFF", foreground="#475467", font=("Segoe UI", 10))
        style.configure("TLabelframe", background="#FFFFFF", bordercolor="#D8DEE8", relief="solid")
        style.configure("TLabelframe.Label", background="#F2F4F7", foreground="#344054", font=("Segoe UI", 10, "bold"))
        style.configure("TEntry", padding=(4, 2), fieldbackground="#FFFFFF")
        style.configure("Field.TEntry", padding=(4, 2), fieldbackground="#FFFFFF", font=("Segoe UI", 9))
        style.configure("TCombobox", padding=(3, 1), fieldbackground="#FFFFFF")
        style.configure("TButton", padding=(5, 2), font=("Segoe UI", 9))
        style.configure("Primary.TButton", background="#2864DC", foreground="#FFFFFF", padding=(7, 3), font=("Segoe UI", 9, "bold"))
        style.map("Primary.TButton", background=[("active", "#1D4FB5"), ("disabled", "#AEBAD0")], foreground=[("disabled", "#F5F7FA")])
        style.configure("Secondary.TButton", background="#EAF0FF", foreground="#214FAF", padding=(7, 3), font=("Segoe UI", 9, "bold"))
        style.map("Secondary.TButton", background=[("active", "#D8E4FF")])

    def _review_specs(self) -> tuple[tuple[str, str], ...]:
        code = ID_TYPE_LABEL_TO_CODE.get(self.id_type_choice.get())
        group = FOCUS_GROUP_BY_CODE.get(code or "", "resident")
        fields = list(FOCUS_FIELDS[group])
        for field, label in (("isuePlc", "발급기관"), ("addr", "주소")):
            if self._value_vars[field].get().strip() and field not in {key for key, _ in fields}:
                fields.append((field, label))
        return tuple(fields)

    def _current_values(self) -> dict[str, str]:
        values = {field: value.get() for field, value in self._value_vars.items()}
        if self._id_crop_preview_bounds is not None:
            values["_idCropBounds"] = ",".join(
                str(value) for value in self._id_crop_preview_bounds
            )
        return values

    def _write_selected_portal_report(self, result: OcrResult) -> Path | None:
        values = self._current_values()
        if self.portal_var.get() == "KT":
            return None
        report_path = default_job_directory() / result.job_id / "sk_report.txt"
        return write_ocr_result(
            result, self._id_type_detection, values=values, path=report_path
        )

    def _refresh_result_table(self) -> None:
        if not hasattr(self, "result_table"):
            return
        for child in self.result_table.winfo_children():
            child.destroy()
        self._entries.clear()

        ttk.Label(
            self.result_table, text="신분증 종류", style="FieldName.TLabel"
        ).grid(row=0, column=0, sticky="w", padx=(2, 6), pady=1)
        self.id_type_choice = ttk.Combobox(
            self.result_table,
            state="readonly",
            textvariable=self._id_type_var,
            values=("유형 선택…", *ID_TYPE_LABEL_TO_CODE.keys()),
        )
        self.id_type_choice.grid(row=0, column=1, sticky="ew", pady=1)
        self.id_type_choice.bind("<<ComboboxSelected>>", self._id_type_choice_selected)

        specs = self._review_specs()
        for index, (field, label) in enumerate(specs, start=1):
            ttk.Label(self.result_table, text=label, style="FieldName.TLabel").grid(
                row=index, column=0, sticky="w", padx=(2, 6), pady=1
            )
            entry = ttk.Entry(
                self.result_table,
                textvariable=self._value_vars[field],
                style="Field.TEntry",
            )
            entry.grid(row=index, column=1, sticky="ew", pady=1)
            entry.bind(
                "<FocusOut>",
                lambda _event, selected=field: self._format_field_display(selected),
            )
            self._entries[field] = entry
        self._register_drop_targets(self.result_table, self._handle_id_drop)
        if (self.version == "v1" and self.draft is not None
                and self.draft.result.lines and self._v1_fit_job is None):
            self._v1_fit_job = self.root.after_idle(self._fit_v1_window_to_content)

    def _format_field_display(self, field: str) -> None:
        value = self._value_vars[field].get().strip()
        if any(not char.isdecimal() and char not in "-./ " for char in value):
            return
        digits = "".join(char for char in value if char.isdecimal())
        if field == "ctzCorpNum" and len(digits) == 13:
            self._value_vars[field].set(f"{digits[:6]}-{digits[6:]}")
        elif field == "isueDt" and len(digits) == 8:
            self._value_vars[field].set(f"{digits[:4]}.{digits[4:6]}.{digits[6:]}.")

    def _schedule_preview_resize(self, _event=None) -> None:
        if self._source_image is None:
            return
        if self._preview_resize_job is not None:
            try:
                self.root.after_cancel(self._preview_resize_job)
            except tk.TclError:
                pass
        self._preview_resize_job = self.root.after(80, self._resize_source_preview)

    def _resize_source_preview(self) -> None:
        self._preview_resize_job = None
        if self._source_image is None:
            return
        width = max(1, self.image_label.winfo_width() - 8)
        height = max(1, self.image_label.winfo_height() - 8)
        preview = self._source_image.copy()
        if self.version == "v2" and self._id_crop_preview_bounds is not None:
            left, top, right, bottom = self._id_crop_preview_bounds
            overlay = Image.new("RGBA", preview.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            outline_width = max(3, round(min(preview.size) / 220))
            draw.rectangle(
                (left, top, right - 1, bottom - 1),
                outline=(0, 120, 255, 255),
                width=outline_width,
            )
            label = "idCrop" if self.portal_var.get() == "SK" else "CR.jpg"
            label_box = draw.textbbox((left, top), label)
            label_height = label_box[3] - label_box[1]
            label_top = max(0, top - label_height - 8)
            draw.rectangle(
                (left, label_top, label_box[2] + 6, top),
                fill=(0, 90, 220, 235),
            )
            draw.text((left + 3, label_top + 2), label, fill=(255, 255, 255, 255))
            preview = Image.alpha_composite(
                preview.convert("RGBA"), overlay
            ).convert("RGB")
        if self.version == "v2" and MASK_PREVIEW_ENABLED and self._mask_preview_position:
            bounds = _parse_mask_position(self._mask_preview_position)
            if bounds is not None:
                left, top, right, bottom = bounds
                overlay = Image.new("RGBA", preview.size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                outline_width = max(2, round(min(preview.size) / 320))
                draw.rectangle(
                    (left, top, right - 1, bottom - 1),
                    fill=(239, 68, 84, 92),
                    outline=(211, 47, 62, 255),
                    width=outline_width,
                )
                preview = Image.alpha_composite(
                    preview.convert("RGBA"), overlay
                ).convert("RGB")
        preview.thumbnail((width, height), Image.Resampling.LANCZOS)
        self._preview_base = preview
        if self._scan_active:
            self._render_scan_frame()
        else:
            self._display_preview(preview)

    def _preview_point_to_source(self, event) -> tuple[int, int] | None:
        if self._source_image is None or self._preview_base is None:
            return None
        preview_width, preview_height = self._preview_base.size
        if preview_width < 1 or preview_height < 1:
            return None
        image_left = (self.image_label.winfo_width() - preview_width) / 2
        image_top = (self.image_label.winfo_height() - preview_height) / 2
        preview_x = min(max(event.x - image_left, 0), preview_width - 1)
        preview_y = min(max(event.y - image_top, 0), preview_height - 1)
        source_x = round(preview_x * self._source_image.width / preview_width)
        source_y = round(preview_y * self._source_image.height / preview_height)
        return (
            min(source_x, self._source_image.width - 1),
            min(source_y, self._source_image.height - 1),
        )

    def _crop_pointer_down(self, event) -> None:
        point = self._preview_point_to_source(event)
        if point is None or self._source_image is None:
            return
        x, y = point
        bounds = self._id_crop_preview_bounds
        if bounds is not None:
            left, top, right, bottom = bounds
            if left <= x < right and top <= y < bottom:
                mode = "move"
                original = bounds
            else:
                mode = "place"
                original = bounds
        else:
            mode = "place"
            crop_width, crop_height = id_portrait_size(
                self._id_type_detection.code
            )
            crop_width = min(crop_width, self._source_image.width)
            crop_height = min(crop_height, self._source_image.height)
            original = (0, 0, crop_width, crop_height)
        self._id_crop_drag = {
            "mode": mode, "start": point, "original": original,
        }
        if mode == "place":
            _, _, width, height = original
            left = min(max(x - width // 2, 0), self._source_image.width - width)
            top = min(max(y - height // 2, 0), self._source_image.height - height)
            self._id_crop_preview_bounds = (left, top, left + width, top + height)
            self._resize_source_preview()
        self.image_label.configure(cursor="fleur")

    def _crop_pointer_move(self, event) -> None:
        drag = self._id_crop_drag
        point = self._preview_point_to_source(event)
        if drag is None or point is None or self._source_image is None:
            return
        x, y = point
        mode = drag["mode"]
        if mode == "move":
            left, top, right, bottom = drag["original"]
            start_x, start_y = drag["start"]
            width, height = right - left, bottom - top
            new_left = min(max(left + x - start_x, 0), self._source_image.width - width)
            new_top = min(max(top + y - start_y, 0), self._source_image.height - height)
            bounds = (new_left, new_top, new_left + width, new_top + height)
        else:
            _, _, right, bottom = drag["original"]
            width, height = right, bottom
            left = min(max(x - width // 2, 0), self._source_image.width - width)
            top = min(max(y - height // 2, 0), self._source_image.height - height)
            bounds = (left, top, left + width, top + height)
        self._id_crop_preview_bounds = bounds
        self._resize_source_preview()

    def _crop_pointer_up(self, _event=None) -> None:
        if self._id_crop_drag is None:
            return
        self._id_crop_drag = None
        self._id_crop_manually_adjusted = True
        self.image_label.configure(cursor="")
        if self.crop_reset_button.winfo_exists():
            self.crop_reset_button.configure(state="normal")
        self.review_status.set("크롭 영역 직접 조정됨 · 저장 시 이 영역을 사용합니다.")

    def _reset_crop_bounds(self) -> None:
        self._id_crop_manually_adjusted = False
        self._refresh_id_crop_preview_bounds(reset=True)
        self.review_status.set("자동 크롭 영역으로 복원했습니다.")
        self._resize_source_preview()

    def _display_preview(self, preview: Image.Image) -> None:
        self._preview = ImageTk.PhotoImage(preview, master=self.root)
        self.image_label.configure(image=self._preview, text="")

    def _start_scan_animation(self, image_path: Path) -> None:
        self._stop_scan_animation(restore=False)
        if self._preview_base is None:
            self._resize_source_preview()
        if self._preview_base is None or self._preview_base.height < 2:
            return
        self._scan_active = True
        self._scan_image_path = image_path
        self._scan_y = 0
        self._scan_direction = 1
        self._animate_scanline()

    def _animate_scanline(self) -> None:
        self._scan_animation_job = None
        if not self._scan_active or self._preview_base is None:
            return
        self._render_scan_frame()
        step = max(2, self._preview_base.height // 52)
        self._scan_y += self._scan_direction * step
        max_y = self._preview_base.height - 1
        if self._scan_y >= max_y:
            self._scan_y = max_y
            self._scan_direction = -1
        elif self._scan_y <= 0:
            self._scan_y = 0
            self._scan_direction = 1
        self._scan_animation_job = self.root.after(28, self._animate_scanline)

    def _render_scan_frame(self) -> None:
        base = self._preview_base
        if base is None:
            return
        height = base.height
        y = min(max(self._scan_y, 0), height - 1)
        glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(glow)
        # Subtle warm glow fades on both sides of a thicker, bright center beam.
        for offset, alpha in ((10, 10), (8, 16), (6, 24), (4, 34), (2, 48)):
            for band_y in {y - offset, y + offset}:
                if 0 <= band_y < height:
                    draw.line(
                        (0, band_y, base.width, band_y),
                        fill=(255, 76, 116, alpha),
                        width=1,
                    )
        draw.line(
            (0, max(0, y - 1), base.width, max(0, y - 1)),
            fill=(255, 118, 145, 115), width=3,
        )
        draw.line(
            (0, y, base.width, y), fill=(255, 224, 229, 245), width=2
        )
        frame = Image.alpha_composite(base.convert("RGBA"), glow).convert("RGB")
        self._display_preview(frame)

    def _stop_scan_animation(
        self, image_path: Path | None = None, *, restore: bool = True
    ) -> None:
        if image_path is not None and image_path != self._scan_image_path:
            return
        self._scan_active = False
        self._scan_image_path = None
        if self._scan_animation_job is not None:
            try:
                self.root.after_cancel(self._scan_animation_job)
            except tk.TclError:
                pass
            self._scan_animation_job = None
        if restore and self._preview_base is not None:
            self._display_preview(self._preview_base)

    def choose_image(self) -> bool:
        selected = filedialog.askopenfilename(
            title="신분증 JPG 선택",
            filetypes=(("JPEG 이미지", "*.jpg *.jpeg"), ("모든 파일", "*.*")),
        )
        if not selected:
            return False
        return self._load_image(Path(selected))

    def _load_image(self, path: Path, *, job_id: str | None = None) -> bool:
        self._stop_scan_animation(restore=False)
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                self._source_image = ImageOps.exif_transpose(image).convert("RGB")
            job_id = job_id or str(uuid4())
            identity_directory = default_job_directory() / job_id / "identity"
            identity_directory.mkdir(parents=True, exist_ok=True)
            staged_image = identity_directory / f"identity{path.suffix.lower()}"
            if path.expanduser().resolve() != staged_image.resolve():
                shutil.copy2(path, staged_image)
        except Exception as exc:
            messagebox.showerror(
                "이미지 준비 실패", f"신분증 이미지를 작업 폴더에 준비하지 못했습니다: {exc}"
            )
            return False

        self._document_job_id = job_id
        self._id_crop_preview_bounds = None
        self._id_crop_manually_adjusted = False
        self._id_crop_drag = None
        self.crop_reset_button.configure(state="disabled")
        result = OcrResult(job_id=job_id, image_path=staged_image)
        self.draft = ReviewDraft.from_result(result)
        self._mask_preview_position = None
        self._id_type_detection = IdTypeDetection(None, None)
        self.id_type_choice.current(0)
        for field in REVIEW_TEXT_FIELDS:
            self._value_vars[field].set("")
        self.review_status.set("")
        self._refresh_result_table()
        self._resize_source_preview()
        return True

    def _handle_id_drop(self, event) -> str:
        try:
            dropped_paths = tuple(self.root.tk.splitlist(event.data))
        except tk.TclError:
            dropped_paths = ()
        paths = tuple(Path(item) for item in dropped_paths)
        if len(paths) != 1 or paths[0].suffix.casefold() not in {".jpg", ".jpeg"}:
            messagebox.showinfo(
                "신분증 이미지",
                "신분증 OCR 영역에는 JPG 이미지 한 장을 놓아 주세요.",
                parent=self.root,
            )
            return COPY
        self._load_new_identity(paths[0])
        return COPY

    def _handle_pdf_drop(self, event) -> str:
        try:
            dropped_paths = tuple(self.root.tk.splitlist(event.data))
        except tk.TclError:
            dropped_paths = ()
        paths = tuple(Path(item) for item in dropped_paths)
        if not paths or any(path.suffix.casefold() != ".pdf" for path in paths):
            messagebox.showinfo(
                "서류 PDF",
                "서류 영역에는 PDF 파일만 놓아 주세요.",
                parent=self.root,
            )
            return COPY
        self._queue_pdf_paths(list(paths))
        return COPY

    def import_and_ocr(self) -> None:
        selected = filedialog.askopenfilename(
            title="신분증 JPG 선택",
            filetypes=(("JPEG 이미지", "*.jpg *.jpeg"), ("모든 파일", "*.*")),
        )
        if selected:
            self._load_new_identity(Path(selected))

    def add_pdf_documents(self) -> None:
        selected = filedialog.askopenfilenames(
            title="서류 PDF 추가",
            filetypes=(("PDF 문서", "*.pdf"), ("모든 파일", "*.*")),
        )
        if selected:
            self._queue_pdf_paths([Path(item) for item in selected])

    def _load_new_identity(self, path: Path) -> None:
        # If PDFs were added first, attach the ID image to that same job.
        reuse_job = self.draft is None and self._document_job_id is not None
        job_id = self._document_job_id if reuse_job else str(uuid4())
        if not self._load_image(path, job_id=job_id):
            return
        self._job_sent = False
        self._pending_confirmation = None
        if hasattr(self, "status_button"):
            self.status_button.configure(state="disabled", text="전송 상태 확인")
        self.save_button.configure(
            state="normal", text="선택 PC로 저장" if self.version == "v2" else "저장하기"
        )
        if hasattr(self, "pdf_button"):
            self.pdf_button.configure(state="normal")
        if not reuse_job:
            self._document_jobs.clear()
            self._refresh_document_table()
        # Begin OCR immediately; adding PDFs remains available while it runs.
        self.run_ocr()

    def _queue_pdf_paths(self, paths: list[Path]) -> None:
        if self._job_sent:
            messagebox.showinfo(
                "새 작업 필요", "전송을 마친 작업에는 PDF를 추가할 수 없습니다. 새 신분증 작업을 시작하세요.",
                parent=self.root,
            )
            return
        if not self._document_job_id:
            self._document_job_id = str(uuid4())
        self._add_pdf_paths(paths, self._document_job_id)

    def _add_pdf_paths(self, paths: list[Path], job_id: str) -> None:
        jobs_root = default_job_directory() / job_id / "documents"
        try:
            jobs_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(
                "PDF 작업 폴더 오류", f"변환 폴더를 만들지 못했습니다: {exc}",
                parent=self.root,
            )
            return
        for source in paths:
            key = str(uuid4())
            output_directory = jobs_root / f"{key}_{source.stem}"
            item: dict[str, object] = {
                "key": key,
                "source": source,
                "output": output_directory,
                "status": "변환 중",
                "pages": "",
            }
            self._document_jobs.append(item)
            self._refresh_document_table()
            Thread(
                target=self._render_pdf_in_background,
                args=(key, source, output_directory),
                daemon=True,
            ).start()

    def _render_pdf_in_background(
        self, key: str, source: Path, output_directory: Path
    ) -> None:
        try:
            pages = render_pdf_to_jpegs(source, output_directory)
        except Exception as exc:
            self.root.after(0, self._pdf_conversion_failed, key, str(exc))
            return
        self.root.after(0, self._pdf_conversion_succeeded, key, pages)

    def _pdf_conversion_succeeded(self, key: str, pages: tuple[Path, ...]) -> None:
        item = next((row for row in self._document_jobs if row["key"] == key), None)
        if item is None:
            return
        item["status"] = "변환 완료"
        item["pages"] = f"{len(pages)}페이지 · {item['output']}"
        self._refresh_document_table()

    def _pdf_conversion_failed(self, key: str, error: str) -> None:
        item = next((row for row in self._document_jobs if row["key"] == key), None)
        if item is None:
            return
        item["status"] = "변환 실패"
        item["pages"] = error
        self._refresh_document_table()

    def _refresh_document_table(self) -> None:
        if not hasattr(self, "document_table"):
            return
        self.document_table.delete(*self.document_table.get_children())
        for item in self._document_jobs:
            source = item["source"]
            self.document_table.insert(
                "", "end", iid=str(item["key"]),
                values=(f"{source.name} · {item['status']}", item["pages"]),
            )

    def run_ocr(self) -> None:
        if self.draft is None:
            messagebox.showinfo("JPG 선택", "먼저 JPG 이미지를 선택하세요.")
            return
        image_path = self.draft.result.image_path
        if not self._ocr_warmup_finished:
            self._pending_ocr_path = image_path
            self.review_status.set("OCR 모델 준비 중 · 준비되면 바로 실행합니다.")
            return
        self._start_ocr(image_path)

    def _start_ocr(self, image_path: Path) -> None:
        self.ocr_button.configure(state="disabled")
        self._start_scan_animation(image_path)
        if self._ocr_models_ready:
            self.review_status.set("OCR 실행 중…")
        else:
            self.review_status.set("OCR 모델 준비를 다시 시도하는 중…")
        Thread(target=self._recognize_in_background, args=(image_path,), daemon=True).start()

    def _recognize_in_background(self, image_path: Path) -> None:
        started = perf_counter()
        try:
            result = self.ocr.recognize(image_path)
            detection = detect_id_type(result.lines)
            if detection.code is None:
                heading_reader = getattr(self.ocr, "recognize_card_heading", None)
                if callable(heading_reader):
                    try:
                        heading_lines = heading_reader(image_path)
                    except Exception:
                        heading_lines = ()
                    if heading_lines:
                        result = OcrResult(
                            job_id=result.job_id,
                            image_path=result.image_path,
                            values=result.values,
                            lines=tuple((*result.lines, *heading_lines)),
                            engine_name=result.engine_name,
                            engine_version=result.engine_version,
                            elapsed_seconds=result.elapsed_seconds,
                        )
                        detection = detect_id_type(result.lines)
            numeric_reader = getattr(self.ocr, "recognize_numeric_fields", None)
            if detection.code and callable(numeric_reader):
                try:
                    numeric_fields = numeric_reader(
                        image_path, result.lines, detection.code
                    ) or {}
                except Exception:
                    numeric_fields = {}
                if numeric_fields:
                    result = OcrResult(
                        job_id=result.job_id,
                        image_path=result.image_path,
                        values={**result.values, **numeric_fields},
                        lines=result.lines,
                        engine_name=f"{result.engine_name} + focused numbers",
                        engine_version=result.engine_version,
                        elapsed_seconds=result.elapsed_seconds,
                    )
            if detection.code == "1401":
                english_reader = getattr(self.ocr, "recognize_english", None)
                details_reader = getattr(
                    self.ocr, "recognize_foreigner_reg_number_details", None
                )
                source_lines = result.lines
                english_lines = ()
                english_engine_used = False
                english_error = ""
                details = None
                if (
                    isinstance(self.ocr, PaddleOcrAdapter)
                    and callable(english_reader)
                    and callable(details_reader)
                ):
                    # These run on separate, preloaded recognition models.
                    # Overlap them after the shared Korean full-card pass.
                    with ThreadPoolExecutor(max_workers=2) as workers:
                        english_future = workers.submit(english_reader, image_path)
                        details_future = workers.submit(
                            details_reader, image_path, source_lines
                        )
                        try:
                            english_lines = english_future.result()
                        except Exception as exc:
                            english_error = str(exc)
                        try:
                            details = details_future.result()
                        except Exception:
                            details = None
                else:
                    if callable(english_reader):
                        try:
                            english_lines = english_reader(image_path)
                        except Exception as exc:
                            english_error = str(exc)
                    if callable(details_reader):
                        try:
                            details = details_reader(image_path, result.lines)
                        except TypeError:
                            # Keep compatibility with adapters implementing the
                            # earlier one-argument focused-number interface.
                            try:
                                details = details_reader(image_path)
                            except Exception:
                                details = None
                        except Exception:
                            details = None
                if english_lines:
                    english_engine_used = True
                    result = OcrResult(
                        job_id=result.job_id,
                        image_path=result.image_path,
                        values=result.values,
                        lines=tuple((*source_lines, *english_lines)),
                        engine_name=f"{result.engine_name} + English",
                        engine_version=result.engine_version,
                        elapsed_seconds=result.elapsed_seconds,
                    )
                if callable(english_reader) and not english_engine_used:
                    result = OcrResult(
                        job_id=result.job_id,
                        image_path=result.image_path,
                        values=result.values,
                        lines=result.lines,
                        engine_name=(
                            f"{result.engine_name} (English OCR unavailable: "
                            f"{english_error or 'no text returned'})"
                        ),
                        engine_version=result.engine_version,
                        elapsed_seconds=result.elapsed_seconds,
                    )
                if details and details[0]:
                    values = {**result.values, "ctzCorpNum": details[0]}
                    if details[1]:
                        values["mskPos"] = details[1]
                        values["_mskPosEstimated"] = "1"
                    result = OcrResult(
                        job_id=result.job_id,
                        image_path=result.image_path,
                        values=values,
                        lines=result.lines,
                        engine_name=result.engine_name,
                        engine_version=result.engine_version,
                        elapsed_seconds=result.elapsed_seconds,
                    )
                number_reader = getattr(self.ocr, "recognize_foreigner_reg_number", None)
                if not callable(details_reader) and callable(number_reader):
                    try:
                        number = number_reader(image_path)
                    except Exception:
                        number = ""
                    if number:
                        result = OcrResult(
                            job_id=result.job_id,
                            image_path=result.image_path,
                            values={**result.values, "ctzCorpNum": number},
                            lines=result.lines,
                            engine_name=result.engine_name,
                            engine_version=result.engine_version,
                            elapsed_seconds=result.elapsed_seconds,
                        )
            # Some Korean cards print the holder name in an unlabelled row
            # immediately above the resident number. If full-card OCR missed
            # it, use the detected number box as a dynamic anchor and reread
            # only the adjacent row.
            name_fields = extract_identity_fields(
                result.lines, id_type_code=detection.code
            )
            if detection.code not in {"1401", "2001", "2002"}:
                name_reader = getattr(
                    self.ocr, "recognize_korean_name_near_number", None
                )
                if callable(name_reader):
                    try:
                        name = name_reader(image_path, result.lines, detection.code)
                    except Exception:
                        name = ""
                    current_name = (
                        result.values.get("custNm")
                        or name_fields.get("custNm", "")
                    )
                    if name and (
                        not current_name
                        or (
                            len(name) > len(current_name)
                            and current_name in name
                        )
                    ):
                        result = OcrResult(
                            job_id=result.job_id,
                            image_path=result.image_path,
                            values={**result.values, "custNm": name},
                            lines=result.lines,
                            engine_name=result.engine_name,
                            engine_version=result.engine_version,
                            elapsed_seconds=result.elapsed_seconds,
                        )
            # Use the full-card result when its date is visibly tied to the
            # issuing authority. Focused OCR is only a recovery path for a
            # missing issuer or an unanchored/missing date.
            full_card_fields = extract_identity_fields(
                result.lines, id_type_code=detection.code
            )
            from new_work.ocr.field_extractor import (
                _ISSUER,
                _dates_in_text,
                _strip_issue_date,
            )

            full_card_issue_date = full_card_fields.get("isueDt", "")
            source_date_is_linked_to_issuer = bool(full_card_issue_date) and any(
                _ISSUER.search(_strip_issue_date(line.text))
                and full_card_issue_date in _dates_in_text(line.text)
                for line in result.lines
            )
            needs_focused_issuer_read = (
                not full_card_fields.get("isuePlc")
                or not source_date_is_linked_to_issuer
                # Driver licenses place the issue date in fine print beside
                # the issuer at the card bottom. Re-read that row even when
                # full-card OCR linked a date, so a consensus can correct a
                # plausible but wrong validity date (for example 2027.01.01).
                or detection.code in {"1101", "1102", "1201"}
            )
            focused_fields: dict[str, str] = {}
            focused_reader = getattr(self.ocr, "recognize_issuer_fields", None)
            if needs_focused_issuer_read and callable(focused_reader):
                try:
                    try:
                        focused_fields = focused_reader(
                            image_path, detection.code, result.lines
                        ) or {}
                    except TypeError:
                        focused_fields = focused_reader(
                            image_path, detection.code
                        ) or {}
                except Exception:
                    focused_fields = {}
            if (
                needs_focused_issuer_read
                and not callable(focused_reader)
                and not focused_fields.get("isuePlc")
            ):
                issuer_reader = getattr(self.ocr, "recognize_issuer", None)
                if callable(issuer_reader):
                    try:
                        issuer = issuer_reader(image_path, detection.code)
                    except Exception:
                        issuer = ""
                    if issuer:
                        focused_fields["isuePlc"] = issuer
            focused_updates: dict[str, str] = {}
            focused_date = focused_fields.get("isueDt", "")
            # A focused crop can contain nearby expiry/security dates and
            # misread digits. Never replace a date already extracted from
            # the full card; use it only when the full-card pass found none.
            existing_issue_date = (
                result.values.get("isueDt") or full_card_issue_date
            )
            focused_date_consensus = (
                focused_fields.get("_isueDt_consensus") == "1"
            )
            if focused_date and (
                not existing_issue_date
                or (
                    focused_date_consensus
                    and focused_date != existing_issue_date
                )
            ):
                focused_updates["isueDt"] = focused_date
            focused_issuer = focused_fields.get("isuePlc", "")
            if (
                focused_issuer
                and not result.values.get("isuePlc")
                and not full_card_fields.get("isuePlc")
            ):
                focused_updates["isuePlc"] = focused_issuer
            if focused_updates:
                result = OcrResult(
                    job_id=result.job_id,
                    image_path=result.image_path,
                    values={**result.values, **focused_updates},
                    lines=result.lines,
                    engine_name=result.engine_name,
                    engine_version=result.engine_version,
                    elapsed_seconds=result.elapsed_seconds,
                )
        except Exception as exc:
            self.root.after(0, self._ocr_failed, image_path, exc)
            return
        result = OcrResult(
            job_id=result.job_id,
            image_path=result.image_path,
            values=result.values,
            lines=result.lines,
            engine_name=result.engine_name,
            engine_version=result.engine_version,
            elapsed_seconds=perf_counter() - started,
        )
        self.root.after(0, self._ocr_succeeded, image_path, result)

    def _ocr_failed(self, image_path: Path, error: Exception) -> None:
        self._stop_scan_animation(image_path)
        self.ocr_button.configure(state="normal")
        if isinstance(error, OcrUnavailableError):
            self.ocr_status.set(f"OCR 상태: 오류 — {error}")
            messagebox.showerror("PaddleOCR 실행 실패", str(error))
            return
        self.ocr_status.set(f"OCR 상태: 오류 — {error}")
        messagebox.showerror("PaddleOCR 실행 실패", str(error))
        self.review_status.set("OCR 실행에 실패했습니다.")

    def _ocr_succeeded(self, image_path: Path, result: OcrResult) -> None:
        self._stop_scan_animation(image_path)
        self.ocr_button.configure(state="normal")
        if self.draft is None or self.draft.result.image_path != image_path:
            return
        detection = detect_id_type(result.lines)
        extracted_values = extract_identity_fields(
            result.lines, id_type_code=detection.code
        )
        merged_values = {key: value for key, value in extracted_values.items() if value}
        merged_values.update({key: value for key, value in result.values.items() if value})
        invalid_resident_number = False
        if detection.code in {"1001", "1101", "1102", "1201", "1301", "1302", "1303"}:
            from new_work.ocr.field_extractor import is_valid_resident_number

            resident_number = merged_values.get("ctzCorpNum", "")
            if resident_number and not is_valid_resident_number(resident_number):
                merged_values["ctzCorpNum"] = ""
                invalid_resident_number = True
        result = OcrResult(
            job_id=result.job_id,
            image_path=result.image_path,
            values=merged_values,
            lines=result.lines,
            engine_name=result.engine_name,
            engine_version=result.engine_version,
            elapsed_seconds=result.elapsed_seconds,
        )
        self.draft = ReviewDraft.from_result(result)
        for field in REVIEW_TEXT_FIELDS:
            value = result.values.get(field, "")
            self._value_vars[field].set(value)
            self._format_field_display(field)
        self._id_type_detection = detection
        self._apply_detected_id_type(self._id_type_detection)
        self._id_crop_manually_adjusted = False
        self._refresh_id_crop_preview_bounds(reset=True)
        if detection.code == "1401":
            mask_position = result.values.get("mskPos", "")
        else:
            mask_position = (
                result.values.get("mskPos")
                or detect_mask_position(result.lines, image_path=result.image_path)
            )
        self._value_vars["mskPos"].set(mask_position or "")
        self._mask_preview_position = mask_position or None
        self._resize_source_preview()
        try:
            result_path = self._write_selected_portal_report(result)
        except OSError as exc:
            result_path = None
            messagebox.showwarning(
                "결과 파일 저장 실패",
                f"OCR은 끝났지만 결과 파일을 저장하지 못했습니다: {exc}",
                parent=self.root,
            )
        if self._id_type_detection.name is None:
            detection_status = "문서 종류를 확인할 수 없습니다. 표에서 종류를 선택하세요"
        elif self._id_type_detection.code is None:
            detection_status = f"{self._id_type_detection.name} 종류를 표에서 선택하세요"
        else:
            detection_status = self._id_type_detection.name
        if invalid_resident_number:
            detection_status += " · 주민번호 검증 실패, 직접 확인 필요"
        self.review_status.set(f"OCR 완료 · {detection_status}")
        self._refresh_result_table()

    def _apply_detected_id_type(self, detection: IdTypeDetection) -> None:
        if detection.code:
            self.id_type_choice.set(ID_TYPE_CODE_TO_LABEL[detection.code])
            self._legacy_type_selected()
            self._refresh_result_table()
            return
        self.id_type_choice.current(0)
        for field in ("kaitIdType", "recogSubCd"):
            self._value_vars[field].set("")
        self._value_vars["idType"].set("")
        if detection.type_code:
            self._value_vars["idType"].set(detection.type_code)
        self._refresh_result_table()

    def _refresh_id_crop_preview_bounds(self, *, reset: bool = False) -> None:
        if self._id_crop_manually_adjusted and not reset:
            return
        if self._source_image is None or not self._id_type_detection.code:
            self._id_crop_preview_bounds = None
            if hasattr(self, "crop_reset_button"):
                self.crop_reset_button.configure(state="disabled")
            return
        if (
            self.portal_var.get() == "SK"
            and self._id_type_detection.code not in SK_ID_CROP_CODES
        ):
            self._id_crop_preview_bounds = None
            if hasattr(self, "crop_reset_button"):
                self.crop_reset_button.configure(state="disabled")
            return
        self._id_crop_preview_bounds = id_portrait_bounds(
            self._source_image,
            recog_sub_cd=self._id_type_detection.code,
            require_face=True,
        )
        if hasattr(self, "crop_reset_button"):
            self.crop_reset_button.configure(
                state=("normal" if self._id_crop_preview_bounds is not None else "disabled")
            )

    def _portal_changed(self, *_args) -> None:
        self._refresh_id_crop_preview_bounds()
        self._resize_source_preview()

    def _legacy_type_selected(self, _event=None) -> None:
        selection = self.id_type_choice.get()
        code = ID_TYPE_LABEL_TO_CODE.get(selection)
        if code is None:
            return
        id_type = ID_TYPES[code]["type"]
        # com_sk maps the same RECOG_SUB_CD to both report keys and maps the
        # chosen identity kind to ID_TYPE. This is an explicit user choice.
        for field, value in (("kaitIdType", code), ("recogSubCd", code), ("idType", id_type)):
            self._value_vars[field].set(value)
    def _id_type_choice_selected(self, _event=None) -> None:
        self._legacy_type_selected()
        code = ID_TYPE_LABEL_TO_CODE.get(self.id_type_choice.get())
        self._refresh_result_table()
        if code is None or self.draft is None or not self.draft.result.lines:
            return
        data = ID_TYPES[code]
        self._id_type_detection = IdTypeDetection(
            name=data["name"],
            type_code=data["type"],
            candidate_codes=(code,),
            matched_text=self._id_type_detection.matched_text,
        )
        self._id_crop_manually_adjusted = False
        self._refresh_id_crop_preview_bounds(reset=True)
        if code == "1302":
            extracted = extract_identity_fields(
                self.draft.result.lines, id_type_code=code
            )
            if extracted.get("custNm") and not self._value_vars["custNm"].get().strip():
                self._value_vars["custNm"].set(extracted["custNm"])
            for field in ("ctzCorpNum", "veteranNum"):
                if extracted.get(field):
                    self._value_vars[field].set(extracted[field])
                    self._format_field_display(field)
        try:
            path = self._write_selected_portal_report(self.draft.result)
        except OSError as exc:
            messagebox.showwarning(
                "결과 파일 저장 실패",
                f"선택한 문서 유형을 결과 파일에 저장하지 못했습니다: {exc}",
                parent=self.root,
            )
            return
        self.review_status.set(
            f"문서 종류: {data['name']} · OCR 결과 항목을 확인하세요."
        )
        self._resize_source_preview()

    def mark_reviewed(self) -> None:
        if self.draft is None:
            messagebox.showinfo("JPG 선택", "먼저 JPG 이미지를 선택하세요.")
            return
        if not self.draft.result.lines:
            messagebox.showinfo("OCR 결과 확인", "먼저 OCR을 실행해 결과를 확인하세요.")
            return
        if self.version == "v1":
            self._save_locally()
            return
        store = self._selected_store()
        if store is None:
            messagebox.showinfo("접수 PC 선택", "먼저 접점 설정에서 대상 PC를 등록하세요.")
            return
        if not store.relay_station_id:
            messagebox.showwarning(
                "Relay 자리 ID 설정",
                f"'{store.name}'의 Render Relay 자리 ID를 접점 설정에 입력하세요.",
                parent=self.root,
            )
            return
        if self.relay_config is None:
            messagebox.showwarning(
                "Relay 서버 설정",
                self._relay_config_error or "먼저 Relay 서버 주소와 중앙 송신 토큰을 설정하세요.",
                parent=self.root,
            )
            return
        incomplete = [
            str(item["source"].name)
            for item in self._document_jobs
            if item["status"] != "변환 완료"
        ]
        if incomplete:
            messagebox.showinfo(
                "PDF 변환 중",
                "변환이 완료되지 않은 PDF가 있어 전송하지 않았습니다:\n"
                + "\n".join(incomplete),
                parent=self.root,
            )
            return
        values = self._current_values()
        carrier = self.portal_var.get()
        if values.get("kaitIdType", "").strip() not in ID_TYPES:
            messagebox.showinfo(
                "신분증 종류 확인",
                "전송 전에 신분증 종류를 선택하고 OCR 항목을 확인하세요.",
                parent=self.root,
            )
            return
        result = self.draft.result
        detection = self._id_type_detection
        documents = list(self._document_jobs)
        relay_config = dict(self.relay_config)
        transfer_job_id = str(uuid4())
        self.save_button.configure(state="disabled", text="전송 중…")
        self._set_transfer_controls(False)
        self.review_status.set(f"Render Relay로 {carrier} 작업 전송 중…")
        Thread(
            target=self._send_work_package,
            args=(store, carrier, result, detection, values, documents,
                  relay_config, transfer_job_id),
            daemon=True,
        ).start()

    def _save_locally(self) -> None:
        """Apply reviewed OCR directly on this PC, matching the original V1 flow."""
        assert self.draft is not None
        values = self._current_values()
        database_row_id: str | None = None
        try:
            if self.portal_var.get() == "KT":
                result_path, database_row_id = save_kt_submission(
                    self.draft.result, self._id_type_detection, values
                )
            else:
                result_path = write_ocr_result(
                    self.draft.result,
                    self._id_type_detection,
                    values=values,
                    path=default_result_path(),
                )
            self.draft.mark_reviewed(values)
        except (OSError, ValueError, sqlite3.Error) as exc:
            messagebox.showwarning(
                "저장 실패",
                f"현재 PC에 결과를 저장하지 못했습니다: {exc}",
                parent=self.root,
            )
            self.review_status.set("저장 실패")
            return
        self.review_status.set("현재 PC 저장 완료")
        saved_message = f"현재 PC에 OCR 결과를 저장했습니다.\n{result_path}"
        if database_row_id is not None:
            image_directory = default_kt_image_directory()
            saved_message += (
                f"\nT_SCAN_PAPER 문서 ID: {database_row_id}"
                f"\n원본 이미지: {image_directory / 'ORG.jpg'}"
                f"\n얼굴 이미지: {image_directory / 'CR.jpg'}"
            )
        messagebox.showinfo("저장하기", saved_message, parent=self.root)

    def _send_work_package(
        self,
        store: Store,
        carrier: str,
        result: OcrResult,
        detection: IdTypeDetection,
        values: dict[str, str],
        documents: list[dict[str, object]],
        relay_config: dict[str, str],
        transfer_job_id: str,
    ) -> None:
        try:
            job_root = default_job_directory() / result.job_id
            package_path = job_root / (
                f"{transfer_job_id}_{carrier}.zip"
            )
            package = create_work_package(
                package_path,
                job_id=transfer_job_id,
                station_id=store.relay_station_id,
                carrier=carrier,
                result=result,
                detection=detection,
                reviewed_values=values,
                document_jobs=documents,
            )
            def progress(message: str) -> None:
                self.root.after(0, self.review_status.set, message)

            job_id = send_work_package(
                relay_config, store.relay_station_id, package, progress
            )
        except DeliveryPendingError as exc:
            self.root.after(
                0, self._work_package_pending, store.name, carrier,
                exc.job_id, values, str(exc),
            )
            return
        except Exception as exc:
            self.root.after(0, self._work_package_failed, store.name, str(exc))
            return
        self.root.after(
            0, self._work_package_sent, store.name, carrier, job_id, values
        )

    def _work_package_failed(self, store_name: str, error: str) -> None:
        self._pending_confirmation = None
        self.status_button.configure(state="disabled")
        self.save_button.configure(state="normal", text="선택 PC로 저장")
        self._set_transfer_controls(True)
        self.review_status.set("Relay 전송 또는 Receiver 적용 실패")
        messagebox.showerror(
            "Receiver 전송 실패",
            f"{store_name} Receiver에 작업을 저장하지 못했습니다.\n{error}",
            parent=self.root,
        )

    def _work_package_pending(
        self, store_name: str, carrier: str, job_id: str,
        values: dict[str, str], error: str,
    ) -> None:
        self._set_transfer_controls(True)
        self.save_button.configure(state="disabled", text="Receiver 확인 대기")
        self.pdf_button.configure(state="disabled")
        self._pending_confirmation = (store_name, carrier, job_id, values)
        self.status_button.configure(state="normal")
        self._job_sent = True
        self.review_status.set(f"{store_name} Receiver 적용 확인 대기 · {job_id}")
        messagebox.showwarning("Receiver 확인 대기", error, parent=self.root)

    def _check_pending_status(self) -> None:
        pending = self._pending_confirmation
        if pending is None or self.relay_config is None:
            return
        store_name, carrier, job_id, values = pending
        self.status_button.configure(state="disabled", text="확인 중…")
        Thread(
            target=self._check_pending_status_worker,
            args=(store_name, carrier, job_id, values, dict(self.relay_config)),
            daemon=True,
        ).start()

    def _check_pending_status_worker(
        self, store_name: str, carrier: str, job_id: str,
        values: dict[str, str], relay_config: dict[str, str],
    ) -> None:
        try:
            status = get_work_package_status(relay_config, job_id)
        except Exception as exc:
            self.root.after(0, self._pending_status_failed, str(exc))
            return
        self.root.after(
            0, self._pending_status_updated,
            store_name, carrier, job_id, values, status,
        )

    def _pending_status_failed(self, error: str) -> None:
        self.status_button.configure(state="normal", text="전송 상태 확인")
        self.review_status.set(f"전송 상태 확인 실패 · {error}")

    def _pending_status_updated(
        self, store_name: str, carrier: str, job_id: str,
        values: dict[str, str], status: str,
    ) -> None:
        self.status_button.configure(state="normal", text="전송 상태 확인")
        if status == "delivered":
            self._pending_confirmation = None
            self.status_button.configure(state="disabled")
            self._work_package_sent(store_name, carrier, job_id, values)
        else:
            self.review_status.set(f"Receiver 작업 상태: {status} · {job_id}")

    def _work_package_sent(
        self, store_name: str, carrier: str, job_id: str,
        values: dict[str, str],
    ) -> None:
        self._pending_confirmation = None
        self.status_button.configure(state="disabled", text="전송 상태 확인")
        self._set_transfer_controls(True)
        self.pdf_button.configure(state="disabled")
        self.save_button.configure(state="disabled", text="전송 완료")
        self._job_sent = True
        if self.draft is not None:
            self.draft.mark_reviewed(values)
        self.review_status.set(f"{store_name} Receiver에서 {carrier} 저장 완료 확인")
        messagebox.showinfo(
            "저장 완료",
            f"{store_name} Receiver가 {carrier} 작업 적용을 완료했습니다.\n작업 ID: {job_id}",
            parent=self.root,
        )

def main(version: str = "v2") -> None:
    root = TkinterDnD.Tk()
    # Keep the source run and the Windows EXE on the same pipeline.
    NewWorkApp(root, ocr=PaddleOcrAdapter(device="cpu", ocr_version="PP-OCRv5"),
               version=version)
    if sys.platform == "win32":
        if version == "v1":
            try:
                unregister_current_user_autostart("NewWorkIdReviewV1")
            except OSError as exc:
                root.after(
                    1000,
                    lambda error=exc: messagebox.showwarning(
                        "자동 실행 해제 실패",
                        f"V1의 Windows 로그인 자동 실행을 해제하지 못했습니다.\n{error}",
                        parent=root,
                    ),
                )
        else:
            try:
                register_current_user_autostart("NewWorkIdReviewV2")
            except OSError as exc:
                root.after(
                    1000,
                    lambda error=exc: messagebox.showwarning(
                        "자동 실행 설정 실패",
                        f"Windows 로그인 시 자동 실행을 등록하지 못했습니다.\n{error}",
                        parent=root,
                    ),
                )
    root.mainloop()
