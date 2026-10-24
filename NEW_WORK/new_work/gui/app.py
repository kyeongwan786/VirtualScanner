from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from time import perf_counter
import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk
from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
from urllib.parse import unquote_plus

from new_work.domain import REVIEW_TEXT_FIELDS, OcrResult, ReviewDraft
from new_work.ocr.adapter import OcrAdapter, OcrUnavailableError, PaddleOcrAdapter
from new_work.ocr.id_types import ID_TYPES, IdTypeDetection, detect_id_type
from new_work.ocr.field_extractor import extract_identity_fields
from new_work.ocr.mask_position import detect_mask_position
from new_work.ocr.result_writer import write_ocr_result
from new_work.carrier.kt import default_kt_image_directory, save_kt_submission


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
    def __init__(self, root: tk.Tk, ocr: OcrAdapter | None = None) -> None:
        self.root = root
        self.ocr = ocr or PaddleOcrAdapter()
        self.draft: ReviewDraft | None = None
        self._preview: ImageTk.PhotoImage | None = None
        self._preview_base: Image.Image | None = None
        self._source_image: Image.Image | None = None
        self._mask_preview_position: str | None = None
        self._entries: dict[str, ttk.Entry] = {}
        self._value_vars: dict[str, tk.StringVar] = {
            field: tk.StringVar(root) for field in REVIEW_TEXT_FIELDS
        }
        self._id_type_var = tk.StringVar(root, value="유형 선택…")
        self.portal_var = tk.StringVar(root, value="SK")
        self._preview_resize_job: str | None = None
        self._scan_animation_job: str | None = None
        self._scan_y = 0
        self._scan_direction = 1
        self._scan_active = False
        self._scan_image_path: Path | None = None
        self._id_type_detection = IdTypeDetection(None, None)
        self._ocr_warmup_finished = not isinstance(self.ocr, PaddleOcrAdapter)
        self._ocr_models_ready = self._ocr_warmup_finished
        self._pending_ocr_path: Path | None = None
        root.title("신분증 확인")
        root.geometry("560x700")
        root.resizable(False, False)
        self._build()
        if isinstance(self.ocr, PaddleOcrAdapter):
            self.root.after_idle(self._begin_ocr_warmup)

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
        self._setup_style()
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
                portal_choices,
                text=portal,
                variable=self.portal_var,
                value=portal,
            ).pack(side="left", padx=(0, 8))

        self.result_table = ttk.Frame(shell, style="Card.TFrame", padding=(2, 1))
        self.result_table.grid(row=1, column=0, sticky="ew", pady=(0, 3))
        self.result_table.columnconfigure(1, weight=1)

        buttons = ttk.Frame(shell, style="App.TFrame")
        buttons.grid(row=2, column=0, pady=(1, 4))
        self.ocr_button = ttk.Button(
            buttons, text="가져오기", command=self.import_and_ocr
        )
        self.ocr_button.pack(side="left", padx=3)
        ttk.Button(buttons, text="저장하기", command=self.mark_reviewed).pack(
            side="left", padx=3
        )

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
        preview_area = tk.Frame(shell, background="#FFFFFF", borderwidth=0)
        preview_area.grid(row=3, column=0, sticky="nsew")
        preview_area.rowconfigure(0, weight=1)
        preview_area.columnconfigure(0, weight=1)
        self.image_label = tk.Label(
            preview_area,
            text="",
            anchor="center",
            justify="center",
            background="#FFFFFF",
            foreground="#667085",
            font=("Segoe UI", 9),
            borderwidth=0,
            highlightthickness=0,
        )
        self.image_label.grid(row=0, column=0, sticky="nsew")
        self.image_label.bind("<Configure>", self._schedule_preview_resize)
        self._refresh_result_table()
        self._register_drop_targets(self.root)

    def _register_drop_targets(self, widget: tk.Misc) -> None:
        if not getattr(widget, "_new_work_drop_target", False):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._handle_image_drop)
            widget._new_work_drop_target = True
        for child in widget.winfo_children():
            self._register_drop_targets(child)

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
        return {field: value.get() for field, value in self._value_vars.items()}

    def _write_selected_portal_report(self, result: OcrResult) -> Path | None:
        values = self._current_values()
        if self.portal_var.get() == "KT":
            return None
        return write_ocr_result(result, self._id_type_detection, values=values)

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
        self._register_drop_targets(self.result_table)

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
        if MASK_PREVIEW_ENABLED and self._mask_preview_position:
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

    def _load_image(self, path: Path) -> bool:
        self._stop_scan_animation(restore=False)
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                self._source_image = image.convert("RGB")
        except Exception as exc:
            messagebox.showerror("이미지 열기 실패", f"선택한 파일을 읽지 못했습니다: {exc}")
            return False

        result = OcrResult.empty(path)
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

    def _handle_image_drop(self, event) -> str:
        try:
            dropped_paths = tuple(self.root.tk.splitlist(event.data))
        except tk.TclError:
            dropped_paths = ()
        if len(dropped_paths) != 1:
            messagebox.showinfo(
                "이미지 가져오기", "신분증 이미지 한 장씩 끌어다 놓아 주세요.",
                parent=self.root,
            )
            return COPY
        path = Path(dropped_paths[0])
        if path.suffix.lower() not in {".jpg", ".jpeg"}:
            messagebox.showinfo(
                "이미지 가져오기", "JPG 이미지 파일을 끌어다 놓아 주세요.",
                parent=self.root,
            )
            return COPY
        if self._load_image(path):
            self.run_ocr()
        return COPY

    def import_and_ocr(self) -> None:
        if self.choose_image():
            self.run_ocr()

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
            # Issuing authorities are printed in a small lower-card row and
            # are often missed by full-card OCR. Keep this focused result out
            # of `lines` so it cannot change name, number, date, or address.
            if not extract_identity_fields(result.lines).get("isuePlc"):
                issuer_reader = getattr(self.ocr, "recognize_issuer", None)
                if callable(issuer_reader):
                    try:
                        issuer = issuer_reader(image_path, detection.code)
                    except Exception:
                        issuer = ""
                    if issuer:
                        result = OcrResult(
                            job_id=result.job_id,
                            image_path=result.image_path,
                            values={**result.values, "isuePlc": issuer},
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
        if code == "1302":
            extracted = extract_identity_fields(
                self.draft.result.lines, id_type_code=code
            )
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

    def mark_reviewed(self) -> None:
        if self.draft is None:
            messagebox.showinfo("JPG 선택", "먼저 JPG 이미지를 선택하세요.")
            return
        if not self.draft.result.lines:
            messagebox.showinfo("OCR 결과 확인", "먼저 OCR을 실행해 결과를 확인하세요.")
            return
        values = self._current_values()
        database_row_id: str | None = None
        try:
            if self.portal_var.get() == "KT":
                result_path, database_row_id = save_kt_submission(
                    self.draft.result,
                    self._id_type_detection,
                    values,
                )
            else:
                result_path = self._write_selected_portal_report(
                    self.draft.result
                )
            self.draft.mark_reviewed(values)
        except (OSError, ValueError, sqlite3.Error) as exc:
            messagebox.showwarning(
                "저장 실패",
                f"검토값은 현재 메모리에 반영됐지만 저장에 실패했습니다: {exc}",
                parent=self.root,
            )
            result_path = None
        self.review_status.set("저장 완료" if result_path else "저장 실패")
        if result_path:
            saved_message = f"수정한 OCR 결과를 저장했습니다.\n{result_path}"
            if database_row_id is not None:
                saved_message += f"\nT_SCAN_PAPER 문서 ID: {database_row_id}"
                image_directory = default_kt_image_directory()
                saved_message += (
                    f"\n원본 이미지: {image_directory / 'ORG.jpg'}"
                    f"\n얼굴 이미지: {image_directory / 'CR.jpg'}"
                )
            messagebox.showinfo("저장하기", saved_message, parent=self.root)

def main() -> None:
    root = TkinterDnD.Tk()
    # Keep the source run and the Windows EXE on the same pipeline.
    NewWorkApp(root, ocr=PaddleOcrAdapter(device="cpu", ocr_version="PP-OCRv5"))
    root.mainloop()
