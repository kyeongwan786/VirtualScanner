from __future__ import annotations

from pathlib import Path
from threading import Thread
from time import perf_counter
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
from tkinterdnd2 import COPY, DND_FILES, TkinterDnD

from new_work.domain import REVIEW_TEXT_FIELDS, OcrResult, ReviewDraft
from new_work.ocr.adapter import OcrAdapter, OcrUnavailableError, PaddleOcrAdapter
from new_work.ocr.id_types import ID_TYPES, IdTypeDetection, detect_id_type
from new_work.ocr.field_extractor import extract_identity_fields
from new_work.ocr.mask_position import detect_mask_position
from new_work.ocr.result_writer import write_ocr_result


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
        ("custNm", "이름"), ("ctzCorpNum", "보훈번호"),
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


class NewWorkApp:
    def __init__(self, root: tk.Tk, ocr: OcrAdapter | None = None) -> None:
        self.root = root
        self.ocr = ocr or PaddleOcrAdapter()
        self.draft: ReviewDraft | None = None
        self._preview: ImageTk.PhotoImage | None = None
        self._source_image: Image.Image | None = None
        self._entries: dict[str, ttk.Entry] = {}
        self._value_vars: dict[str, tk.StringVar] = {
            field: tk.StringVar(root) for field in REVIEW_TEXT_FIELDS
        }
        self._preview_resize_job: str | None = None
        self._id_type_detection = IdTypeDetection(None, None)
        root.title("신분증 확인")
        root.geometry("560x700")
        root.resizable(False, False)
        self._build()
        if isinstance(self.ocr, PaddleOcrAdapter):
            self.ocr_status.set("OCR 모델 준비 중 · 백그라운드에서 불러오는 중")
            Thread(target=self._warmup_ocr, daemon=True).start()

    def _warmup_ocr(self) -> None:
        try:
            self.ocr.warmup()
        except Exception as exc:
            self.root.after(
                0, self.ocr_status.set,
                f"OCR 모델 준비 실패 · OCR 실행 시 오류 상세를 표시합니다: {exc}",
            )
            return
        self.root.after(
            0, self.ocr_status.set,
            f"OCR 준비됨 · {self.ocr.status}",
        )

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
        ttk.Label(settings, text="신분증 종류").grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.id_type_choice = ttk.Combobox(
            settings,
            state="readonly",
            values=("유형 선택…", *ID_TYPE_LABEL_TO_CODE.keys()),
        )
        self.id_type_choice.current(0)
        self.id_type_choice.grid(row=0, column=1, sticky="ew")
        self.id_type_choice.bind("<<ComboboxSelected>>", self._id_type_choice_selected)

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

    def _refresh_result_table(self) -> None:
        if not hasattr(self, "result_table"):
            return
        for child in self.result_table.winfo_children():
            child.destroy()
        self._entries.clear()
        specs = self._review_specs()
        for index, (field, label) in enumerate(specs):
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
        preview.thumbnail((width, height), Image.Resampling.LANCZOS)
        self._preview = ImageTk.PhotoImage(preview)
        self.image_label.configure(image=self._preview, text="")

    def choose_image(self) -> bool:
        selected = filedialog.askopenfilename(
            title="신분증 JPG 선택",
            filetypes=(("JPEG 이미지", "*.jpg *.jpeg"), ("모든 파일", "*.*")),
        )
        if not selected:
            return False
        return self._load_image(Path(selected))

    def _load_image(self, path: Path) -> bool:
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
        self.ocr_button.configure(state="disabled")
        self.review_status.set("PaddleOCR 실행 중… 최초 실행은 모델 준비에 시간이 걸릴 수 있습니다.")
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
                english_lines = ()
                english_engine_used = False
                english_error = ""
                if callable(english_reader):
                    try:
                        english_lines = english_reader(image_path)
                    except Exception as exc:
                        english_lines = ()
                        english_error = str(exc)
                    if english_lines:
                        english_engine_used = True
                        result = OcrResult(
                            job_id=result.job_id,
                            image_path=result.image_path,
                            values=result.values,
                            lines=tuple((*result.lines, *english_lines)),
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
                details_reader = getattr(
                    self.ocr, "recognize_foreigner_reg_number_details", None
                )
                details = None
                # Most cards already have a full-line number box. Reuse it for
                # masking rather than running another detector on a crop.
                has_full_line_mask = bool(
                    result.values.get("mskPos") or detect_mask_position(result.lines)
                )
                if callable(details_reader) and not has_full_line_mask:
                    try:
                        details = details_reader(image_path)
                    except Exception:
                        details = None
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
        except Exception as exc:
            self.root.after(0, self._ocr_failed, exc)
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

    def _ocr_failed(self, error: Exception) -> None:
        self.ocr_button.configure(state="normal")
        if isinstance(error, OcrUnavailableError):
            self.ocr_status.set(f"OCR 상태: 오류 — {error}")
            messagebox.showerror("PaddleOCR 실행 실패", str(error))
            return
        self.ocr_status.set(f"OCR 상태: 오류 — {error}")
        messagebox.showerror("PaddleOCR 실행 실패", str(error))
        self.review_status.set("OCR 실행에 실패했습니다.")

    def _ocr_succeeded(self, image_path: Path, result: OcrResult) -> None:
        self.ocr_button.configure(state="normal")
        if self.draft is None or self.draft.result.image_path != image_path:
            return
        detection = detect_id_type(result.lines)
        extracted_values = extract_identity_fields(result.lines)
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
        mask_position = result.values.get("mskPos") or detect_mask_position(result.lines)
        self._value_vars["mskPos"].set(mask_position or "")
        try:
            result_path = write_ocr_result(
                result,
                self._id_type_detection,
                values=self._current_values(),
            )
        except OSError as exc:
            result_path = None
            messagebox.showwarning(
                "결과 파일 저장 실패",
                f"OCR은 끝났지만 sk_report.txt를 저장하지 못했습니다: {exc}",
                parent=self.root,
            )
        if self._id_type_detection.name is None:
            detection_status = "문서 종류를 확인할 수 없습니다. 위에서 종류를 선택하세요"
        elif self._id_type_detection.code is None:
            detection_status = f"{self._id_type_detection.name} 종류를 위에서 선택하세요"
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
        try:
            path = write_ocr_result(
                self.draft.result,
                self._id_type_detection,
                values=self._current_values(),
            )
        except OSError as exc:
            messagebox.showwarning(
                "결과 파일 저장 실패",
                f"선택한 문서 유형을 sk_report.txt에 저장하지 못했습니다: {exc}",
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
        self.draft.mark_reviewed(values)
        try:
            result_path = write_ocr_result(
                self.draft.result, self._id_type_detection, values=values
            )
        except OSError as exc:
            messagebox.showwarning(
                "결과 파일 저장 실패",
                f"검토값은 현재 메모리에 반영됐지만 sk_report.txt 저장에 실패했습니다: {exc}",
                parent=self.root,
            )
            result_path = None
        self.review_status.set("저장 완료" if result_path else "저장 실패")
        if result_path:
            messagebox.showinfo("저장하기", "수정한 OCR 결과를 저장했습니다.", parent=self.root)

def main() -> None:
    root = TkinterDnD.Tk()
    # Keep the source run and the Windows EXE on the same pipeline.
    NewWorkApp(root, ocr=PaddleOcrAdapter(device="cpu", ocr_version="PP-OCRv5"))
    root.mainloop()
