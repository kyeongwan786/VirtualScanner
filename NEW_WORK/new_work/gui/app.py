from __future__ import annotations

from pathlib import Path
import os
from threading import Thread
from time import perf_counter
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from new_work.domain import REVIEW_TEXT_FIELDS, OcrResult, ReviewDraft
from new_work.database.reader import fetch_latest_kt_data
from new_work.carrier.kt import (
    create_kt_images,
    get_latest_kt_recog_sub_cd,
    kt_store_destinations,
    update_kt_database_copy,
)
from new_work.carrier.sk import create_sk_report
from new_work.gui.stores_dialog import StoreManagerDialog
from new_work.ocr.adapter import OcrAdapter, OcrUnavailableError, PaddleOcrAdapter
from new_work.ocr.id_types import ID_TYPES, IdTypeDetection, detect_id_type
from new_work.ocr.field_extractor import extract_identity_fields
from new_work.ocr.mask_position import detect_mask_position
from new_work.ocr.result_writer import write_ocr_result
from new_work.stores import Store, default_config_path, load_stores
from new_work.transfer import append_transfer_log, transfer_file


ID_TYPE_LABEL_TO_CODE = {
    f"{code} / {data['type']} — {data['name']}": code
    for code, data in ID_TYPES.items()
}
ID_TYPE_CODE_TO_LABEL = {code: label for label, code in ID_TYPE_LABEL_TO_CODE.items()}
FIELD_LABELS = {
    "kaitIdType": "문서 코드",
    "recogSubCd": "인식 세부 코드",
    "idType": "신분증 구분",
    "ctzCorpNum": "주민등록번호",
    "custNm": "이름",
    "isueDt": "발급일자",
    "drvLicenIsueNum": "운전면허번호",
    "isuePlc": "발급기관",
    "nationClCd": "국적 코드",
    "addr": "주소",
    "mskPos": "마스킹 좌표",
}


class NewWorkApp:
    def __init__(self, root: tk.Tk, ocr: OcrAdapter | None = None) -> None:
        self.root = root
        self.ocr = ocr or PaddleOcrAdapter()
        self.draft: ReviewDraft | None = None
        self._preview: ImageTk.PhotoImage | None = None
        self._entries: dict[str, ttk.Entry] = {}
        self._focused_entry: ttk.Entry | None = None
        self._id_type_detection = IdTypeDetection(None, None)
        try:
            self.stores = load_stores()
        except ValueError as exc:
            self.stores = []
            messagebox.showerror("접점 설정 오류", str(exc), parent=root)
        self.selected_store_id: str | None = None

        root.title("NEW_WORK — OCR 검토 준비")
        root.geometry("1160x780")
        root.minsize(1000, 700)
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

        shell = ttk.Frame(self.root, style="App.TFrame", padding=16)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        header = ttk.Frame(shell, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        title_stack = ttk.Frame(header, style="App.TFrame")
        title_stack.grid(row=0, column=0, sticky="w")
        ttk.Label(title_stack, text="신분증 OCR 검토", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_stack,
            text="이미지를 확인하고, 추출된 정보를 검토한 뒤 저장하세요.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        actions = ttk.Frame(header, style="App.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        ttk.Button(actions, text="이미지 불러오기", command=self.choose_image).pack(side="left", padx=(0, 8))
        self.ocr_button = ttk.Button(
            actions, text="OCR 실행", command=self.run_ocr, style="Primary.TButton"
        )
        self.ocr_button.pack(side="left")

        settings = ttk.LabelFrame(shell, text="작업 설정", padding=(12, 9))
        settings.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(4, weight=1)
        ttk.Label(settings, text="문서 유형", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.id_type_choice = ttk.Combobox(
            settings,
            state="readonly",
            width=34,
            values=("유형 선택…", *ID_TYPE_LABEL_TO_CODE.keys()),
        )
        self.id_type_choice.current(0)
        self.id_type_choice.grid(row=0, column=1, sticky="ew", padx=(0, 22))
        self.id_type_choice.bind("<<ComboboxSelected>>", self._id_type_choice_selected)
        ttk.Label(settings, text="접점", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.store_choice = ttk.Combobox(settings, state="readonly", width=30)
        self.store_choice.grid(row=0, column=3, sticky="ew", padx=(0, 8))
        self.store_choice.bind("<<ComboboxSelected>>", self._store_selected)
        ttk.Button(settings, text="접점 설정", command=self.manage_stores).grid(
            row=0, column=4, sticky="w", padx=(0, 8)
        )
        ttk.Button(settings, text="KT 최신 원본 참고", command=self.show_kt_latest_row).grid(
            row=0, column=5, sticky="w"
        )
        self._refresh_store_choice()

        work = ttk.Panedwindow(shell, orient="horizontal")
        work.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        image_box = ttk.LabelFrame(work, text="1. 신분증 이미지", padding=12)
        image_box.columnconfigure(0, weight=1)
        image_box.rowconfigure(0, weight=1)
        self.image_label = ttk.Label(
            image_box,
            text="이미지를 불러오면\n여기에 미리보기가 표시됩니다.",
            anchor="center",
            justify="center",
            style="Preview.TLabel",
        )
        self.image_label.grid(row=0, column=0, sticky="nsew")
        self.image_path_text = tk.StringVar(value="선택된 이미지 없음")
        ttk.Label(
            image_box,
            textvariable=self.image_path_text,
            style="Muted.TLabel",
            wraplength=470,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(10, 0))

        review_box = ttk.LabelFrame(work, text="2. 인식 결과 검토", padding=10)
        review_box.columnconfigure(0, weight=1)
        review_box.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(review_box)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        fields_page = ttk.Frame(self.notebook, padding=12, style="Card.TFrame")
        fields_page.columnconfigure(1, weight=1)
        fields_page.columnconfigure(2, weight=2)
        fields_page.rowconfigure(len(REVIEW_TEXT_FIELDS) + 1, weight=1)
        self.notebook.add(fields_page, text="필드 검토")
        ttk.Label(fields_page, text="항목", style="TableHead.TLabel").grid(
            row=0, column=0, sticky="w", padx=(6, 12), pady=(0, 8)
        )
        ttk.Label(fields_page, text="OCR 추출값", style="TableHead.TLabel").grid(
            row=0, column=1, sticky="w", padx=(0, 10), pady=(0, 8)
        )
        ttk.Label(fields_page, text="저장할 값", style="TableHead.TLabel").grid(
            row=0, column=2, sticky="w", pady=(0, 8)
        )

        self._original_labels: dict[str, tk.StringVar] = {}
        self._entries = {}
        for row, field in enumerate(REVIEW_TEXT_FIELDS, start=1):
            ttk.Label(
                fields_page,
                text=f"{FIELD_LABELS.get(field, field)}\n{field}",
                style="FieldName.TLabel",
                justify="left",
            ).grid(row=row, column=0, sticky="w", padx=(6, 12), pady=4)
            original = tk.StringVar(value="인식 대기")
            self._original_labels[field] = original
            ttk.Label(
                fields_page,
                textvariable=original,
                style="Original.TLabel",
                wraplength=260,
                justify="left",
            ).grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=4)
            entry = ttk.Entry(fields_page, style="Field.TEntry")
            entry.grid(row=row, column=2, sticky="ew", pady=4, ipady=3)
            entry.bind("<FocusIn>", lambda _event, selected=entry: self._set_focused_entry(selected))
            self._entries[field] = entry

        ocr_page = ttk.Frame(self.notebook, padding=12, style="Card.TFrame")
        ocr_page.columnconfigure(0, weight=1)
        ocr_page.rowconfigure(1, weight=1)
        self.notebook.add(ocr_page, text="OCR 원문")
        ttk.Label(
            ocr_page,
            text="원문에서 줄을 골라 입력칸에 넣을 수 있어요. 먼저 ‘필드 검토’에서 입력칸을 클릭하세요.",
            style="Muted.TLabel",
            wraplength=660,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 9))
        self.ocr_lines = tk.Listbox(
            ocr_page,
            height=14,
            exportselection=False,
            font=("Segoe UI", 10),
            bg="#FFFFFF",
            fg="#172033",
            selectbackground="#DCE8FF",
            selectforeground="#172033",
            highlightthickness=1,
            highlightbackground="#D8DEE9",
            relief="flat",
            activestyle="none",
        )
        self.ocr_lines.grid(row=1, column=0, sticky="nsew")
        lines_scroll = ttk.Scrollbar(ocr_page, orient="vertical", command=self.ocr_lines.yview)
        lines_scroll.grid(row=1, column=1, sticky="ns")
        self.ocr_lines.configure(yscrollcommand=lines_scroll.set)
        ttk.Button(
            ocr_page,
            text="선택한 원문 줄 입력",
            command=self.insert_selected_ocr_line,
            style="Secondary.TButton",
        ).grid(row=2, column=0, sticky="e", pady=(10, 0))

        work.add(image_box, weight=2)
        work.add(review_box, weight=3)

        status_card = ttk.Frame(shell, style="Card.TFrame", padding=(12, 8))
        status_card.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        status_card.columnconfigure(0, weight=1)
        self.ocr_status = tk.StringVar(value=f"OCR 준비됨 · {self.ocr.status}")
        ttk.Label(status_card, textvariable=self.ocr_status, style="Status.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.review_status = tk.StringVar(value="이미지를 불러온 다음 OCR을 실행하세요.")
        ttk.Label(
            status_card,
            textvariable=self.review_status,
            style="Muted.TLabel",
            wraplength=1050,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Button(
            status_card,
            text="검토값 저장",
            command=self.mark_reviewed,
            style="Primary.TButton",
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))

        carrier = ttk.LabelFrame(shell, text="후속 통신사 처리", padding=(12, 8))
        carrier.grid(row=4, column=0, sticky="ew")
        ttk.Button(carrier, text="SK 보고서 생성/전달", command=self.run_sk).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(carrier, text="KT 결과 생성/전달", command=self.run_kt).pack(
            side="left", padx=(0, 12)
        )
        ttk.Label(
            carrier,
            text="SK/KT 결과는 선택한 접점 설정을 사용합니다. LG는 제외됩니다.",
            style="Muted.TLabel",
        ).pack(side="left")

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(bg="#F3F5F9")
        style.configure("App.TFrame", background="#F3F5F9")
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("TLabel", background="#F3F5F9", foreground="#172033", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background="#F3F5F9", foreground="#667085", font=("Segoe UI", 9))
        style.configure("Title.TLabel", background="#F3F5F9", foreground="#172033", font=("Segoe UI", 19, "bold"))
        style.configure("Status.TLabel", background="#FFFFFF", foreground="#344054", font=("Segoe UI", 9, "bold"))
        style.configure("FieldName.TLabel", background="#FFFFFF", foreground="#344054", font=("Segoe UI", 9, "bold"))
        style.configure("Original.TLabel", background="#FFFFFF", foreground="#667085", font=("Segoe UI", 9))
        style.configure("TableHead.TLabel", background="#FFFFFF", foreground="#667085", font=("Segoe UI", 9, "bold"))
        style.configure("Preview.TLabel", background="#E9EEF5", foreground="#667085", font=("Segoe UI", 12))
        style.configure("TLabelframe", background="#FFFFFF", bordercolor="#DCE2EB", relief="solid")
        style.configure("TLabelframe.Label", background="#F3F5F9", foreground="#344054", font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook", background="#FFFFFF", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#FFFFFF")], foreground=[("selected", "#2458C6")])
        style.configure("TEntry", padding=(8, 6), fieldbackground="#FFFFFF")
        style.configure("Field.TEntry", padding=(8, 6), fieldbackground="#FFFFFF")
        style.configure("TCombobox", padding=(6, 5), fieldbackground="#FFFFFF")
        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 9))
        style.configure("Primary.TButton", background="#2864DC", foreground="#FFFFFF", padding=(14, 8), font=("Segoe UI", 9, "bold"))
        style.map("Primary.TButton", background=[("active", "#1D4FB5"), ("disabled", "#AEBAD0")], foreground=[("disabled", "#F5F7FA")])
        style.configure("Secondary.TButton", background="#EAF0FF", foreground="#214FAF", padding=(12, 8), font=("Segoe UI", 9, "bold"))
        style.map("Secondary.TButton", background=[("active", "#D8E4FF")])

    def choose_image(self) -> None:
        selected = filedialog.askopenfilename(
            title="신분증 JPG 선택",
            filetypes=(("JPEG 이미지", "*.jpg *.jpeg"), ("모든 파일", "*.*")),
        )
        if not selected:
            return
        path = Path(selected)
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                preview = image.convert("RGB")
                preview.thumbnail((440, 620))
                self._preview = ImageTk.PhotoImage(preview)
        except Exception as exc:
            messagebox.showerror("이미지 열기 실패", f"선택한 파일을 읽지 못했습니다: {exc}")
            return

        result = OcrResult.empty(path)
        self.draft = ReviewDraft.from_result(result)
        self._id_type_detection = IdTypeDetection(None, None)
        self.id_type_choice.current(0)
        self.image_label.configure(image=self._preview, text="")
        self.image_path_text.set(str(path))
        for field in REVIEW_TEXT_FIELDS:
            self._original_labels[field].set("(필드 자동 분류 안 함)")
            self._entries[field].delete(0, tk.END)
        self.ocr_lines.delete(0, tk.END)
        self.review_status.set("JPG 선택 완료. OCR 실행을 눌러 문자 인식을 시작하세요.")

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
            self._original_labels[field].set(result.values.get(field) or "(필드 자동 분류 안 함)")
            entry = self._entries[field]
            entry.delete(0, tk.END)
            entry.insert(0, result.values.get(field, ""))
        self.ocr_lines.delete(0, tk.END)
        for line in result.lines:
            score = "" if line.confidence is None else f"  [{line.confidence:.3f}]"
            self.ocr_lines.insert(tk.END, f"{line.text}{score}")
        self._id_type_detection = detection
        self._apply_detected_id_type(self._id_type_detection)
        mask_position = result.values.get("mskPos") or detect_mask_position(result.lines)
        mask_entry = self._entries["mskPos"]
        mask_entry.delete(0, tk.END)
        if mask_position:
            mask_entry.insert(0, mask_position)
        try:
            result_path = write_ocr_result(
                result,
                self._id_type_detection,
                values={field: entry.get() for field, entry in self._entries.items()},
            )
        except OSError as exc:
            result_path = None
            messagebox.showwarning(
                "결과 파일 저장 실패",
                f"OCR은 끝났지만 sk_report.txt를 저장하지 못했습니다: {exc}",
                parent=self.root,
            )
        elapsed = "" if result.elapsed_seconds is None else f" · {result.elapsed_seconds:.2f}초"
        self.ocr_status.set(f"OCR 상태: {result.engine_name} {result.engine_version}")
        if self._id_type_detection.name is None:
            detection_status = "문서 종류 미판별"
        elif self._id_type_detection.code is None:
            detection_status = (
                f"{self._id_type_detection.name} 감지; 코드 선택 필요 "
                f"({', '.join(self._id_type_detection.candidate_codes)})"
            )
        else:
            detection_status = (
                f"문서 종류: {self._id_type_detection.name} "
                f"(kaitIdType={self._id_type_detection.code}, "
                f"idType={self._id_type_detection.type_code})"
            )
        result_status = f" · 결과: {result_path}" if result_path else ""
        name_status = (
            " · 영문 이름 미인식(원문 검토 필요)"
            if detection.code == "1401" and not result.values.get("custNm")
            else ""
        )
        number_label = "등록번호" if detection.code == "1401" else "주민번호"
        mask_status = (
            f"{number_label} 마스킹 위치 추정값(저장 전 확인 필요)"
            if mask_position and result.values.get("_mskPosEstimated")
            else f"{number_label} 뒷자리 영역 감지"
            if mask_position
            else f"{number_label} 위치 미감지(인식/좌표 확인 필요)"
        )
        self.review_status.set(
            f"문자 {len(result.lines)}줄 인식 완료{elapsed}. {detection_status}{result_status}. "
            f"{mask_status}{name_status}. 이름·번호 등 업무 필드 값은 확인 후 직접 입력하세요."
        )

    def _apply_detected_id_type(self, detection: IdTypeDetection) -> None:
        if detection.code:
            self.id_type_choice.set(ID_TYPE_CODE_TO_LABEL[detection.code])
            self._legacy_type_selected()
            return
        self.id_type_choice.current(0)
        for field in ("kaitIdType", "recogSubCd"):
            self._entries[field].delete(0, tk.END)
        id_type_entry = self._entries["idType"]
        id_type_entry.delete(0, tk.END)
        if detection.type_code:
            id_type_entry.insert(0, detection.type_code)

    def _set_focused_entry(self, entry: ttk.Entry) -> None:
        self._focused_entry = entry

    def _legacy_type_selected(self, _event=None) -> None:
        selection = self.id_type_choice.get()
        code = ID_TYPE_LABEL_TO_CODE.get(selection)
        if code is None:
            return
        id_type = ID_TYPES[code]["type"]
        # com_sk maps the same RECOG_SUB_CD to both report keys and maps the
        # chosen identity kind to ID_TYPE. This is an explicit user choice.
        for field, value in (("kaitIdType", code), ("recogSubCd", code), ("idType", id_type)):
            entry = self._entries[field]
            entry.delete(0, tk.END)
            entry.insert(0, value)
    def _id_type_choice_selected(self, _event=None) -> None:
        self._legacy_type_selected()
        code = ID_TYPE_LABEL_TO_CODE.get(self.id_type_choice.get())
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
                values={field: entry.get() for field, entry in self._entries.items()},
            )
        except OSError as exc:
            messagebox.showwarning(
                "결과 파일 저장 실패",
                f"선택한 문서 유형을 sk_report.txt에 저장하지 못했습니다: {exc}",
                parent=self.root,
            )
            return
        self.review_status.set(
            f"문서 종류 수동 선택: {data['name']} ({code}) · 결과: {path}"
        )

    def insert_selected_ocr_line(self) -> None:
        selected = self.ocr_lines.curselection()
        if not selected:
            messagebox.showinfo("OCR 줄 선택", "먼저 삽입할 OCR 결과 줄을 선택하세요.")
            return
        if self._focused_entry is None:
            messagebox.showinfo("입력칸 선택", "먼저 값을 넣을 검토 입력칸을 선택하세요.")
            return
        line = self.draft.result.lines[selected[0]] if self.draft else None
        if line is None:
            return
        entry = self._focused_entry
        entry.insert(tk.INSERT, line.text)
        entry.focus_set()
        try:
            result_path = write_ocr_result(
                self.draft.result,
                self._id_type_detection,
                values={field: item.get() for field, item in self._entries.items()},
            )
        except OSError as exc:
            messagebox.showwarning(
                "결과 파일 저장 실패",
                f"입력칸에는 반영했지만 sk_report.txt 저장에 실패했습니다: {exc}",
                parent=self.root,
            )
            return
        self.review_status.set(f"선택한 OCR 줄을 입력칸에 반영하고 sk_report.txt 저장: {result_path}")

    def mark_reviewed(self) -> None:
        if self.draft is None:
            messagebox.showinfo("JPG 선택", "먼저 JPG 이미지를 선택하세요.")
            return
        values = {field: entry.get() for field, entry in self._entries.items()}
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
        self.review_status.set(
            f"검토 완료: {len(self.draft.changed_fields)}개 필드 수정"
            + (f" · {result_path}" if result_path else "")
        )

    def _selected_store(self) -> Store | None:
        return next(
            (store for store in self.stores if store.store_id == self.selected_store_id),
            None,
        )

    def _refresh_store_choice(self) -> None:
        names = [store.name for store in self.stores]
        self.store_choice["values"] = names
        selected = self._selected_store()
        if selected:
            self.store_choice.set(selected.name)
        elif names:
            self.selected_store_id = self.stores[0].store_id
            self.store_choice.current(0)
        else:
            self.selected_store_id = None
            self.store_choice.set("")

    def _store_selected(self, _event=None) -> None:
        index = self.store_choice.current()
        self.selected_store_id = self.stores[index].store_id if 0 <= index < len(self.stores) else None

    def manage_stores(self) -> None:
        StoreManagerDialog(self.root, self._stores_changed)

    def show_kt_latest_row(self) -> None:
        store = self._selected_store()
        if store is None or not store.database_path:
            messagebox.showinfo("KT DB 선택", "접점 설정에서 KT DB 템플릿을 지정하세요.", parent=self.root)
            return
        try:
            values = fetch_latest_kt_data(Path(store.database_path))
        except Exception as exc:
            messagebox.showerror("KT DB 조회 실패", str(exc), parent=self.root)
            return
        if values is None:
            messagebox.showinfo("KT DB 조회", "T_SCAN_PAPER에 행이 없습니다.", parent=self.root)
            return

        window = tk.Toplevel(self.root)
        window.title("KT 원본 최신행 — 참고 전용")
        window.geometry("760x460")
        window.transient(self.root)
        ttk.Label(
            window,
            text=(
                "이 행은 선택한 JPG와 연결됐다고 확인되지 않았습니다. "
                "OCR 검토값을 채우거나 신원 확인 결과로 취급하지 마세요."
            ),
            wraplength=720,
            foreground="#8a3b12",
            padding=10,
        ).pack(fill="x")
        table = ttk.Treeview(window, columns=("field", "value"), show="headings")
        table.heading("field", text="컬럼")
        table.heading("value", text="원본 최신행 값")
        table.column("field", width=220, anchor="w")
        table.column("value", width=500, anchor="w")
        for field, value in values.items():
            if value is None:
                display = "<NULL>"
            elif value == "":
                display = "<빈 문자열>"
            else:
                display = str(value)
            table.insert("", "end", values=(field, display))
        table.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _stores_changed(self, stores: list[Store]) -> None:
        selected_id = self.selected_store_id
        self.stores = stores
        self.selected_store_id = selected_id if any(s.store_id == selected_id for s in stores) else None
        self._refresh_store_choice()

    def _require_reviewed_inputs(self) -> tuple[ReviewDraft, Store | None]:
        if self.draft is None:
            raise ValueError("먼저 JPG를 선택하고 OCR을 실행하세요.")
        if not self.draft.reviewed:
            raise ValueError("검토 완료 표시를 먼저 누르세요.")
        return self.draft, self._selected_store()

    def _job_output_directory(self, draft: ReviewDraft) -> Path:
        return default_config_path().parent / "output" / draft.result.job_id

    def _transfer_result_files(
        self, files: list[tuple[Path, Path]], save_path: str, job_id: str
    ) -> list[str]:
        if not save_path.strip():
            return ["공유폴더 미설정: 로컬 출력 폴더에 저장했습니다."]
        outcomes: list[str] = []
        log_path = default_config_path().parent / "logs" / "transfer.jsonl"
        for source, target in files:
            overwrite = False
            if target.exists():
                overwrite = messagebox.askyesno(
                    "파일 덮어쓰기",
                    f"대상 파일이 이미 있습니다. 덮어쓸까요?\n{target}",
                    parent=self.root,
                )
                if not overwrite:
                    raise FileExistsError(target)
            try:
                result = transfer_file(source, target, overwrite=overwrite, job_id=job_id)
            except Exception as exc:
                try:
                    append_transfer_log(
                        log_path,
                        job_id=job_id,
                        filename=source.name,
                        status="failed",
                        error_type=type(exc).__name__,
                    )
                except OSError:
                    pass
                raise
            try:
                append_transfer_log(
                    log_path,
                    job_id=job_id,
                    filename=source.name,
                    status="complete",
                    size=result.size,
                    sha256=result.sha256,
                )
            except OSError:
                outcomes.append(f"{source.name}: 전송 로그 기록 실패")
            outcomes.append(f"{result.path.name}: {result.size} bytes / SHA-256 {result.sha256[:12]}")
        return outcomes

    def run_sk(self) -> None:
        try:
            draft, store = self._require_reviewed_inputs()
            output_directory = self._job_output_directory(draft)
            report = create_sk_report(
                draft, output_directory / "sk_report.txt"
            )
            save_path = store.save_path if store else ""
            outcomes = self._transfer_result_files(
                [(report, Path(save_path).expanduser() / "classes" / "sk_report.txt")]
                if save_path else [(report, Path())],
                save_path,
                draft.result.job_id,
            )
            messagebox.showinfo(
                "SK 처리 완료",
                f"보고서 생성 완료\n{report}\n\n" + "\n".join(outcomes),
                parent=self.root,
            )
        except Exception as exc:
            messagebox.showerror("SK 처리 실패", str(exc), parent=self.root)

    def run_kt(self) -> None:
        try:
            draft, store = self._require_reviewed_inputs()
            if store is None or not store.save_path:
                raise ValueError("접점을 선택하고 공유폴더 저장경로를 설정하세요.")
            if not store.database_path:
                raise ValueError("선택 접점에 KT DB 템플릿 경로를 설정하세요.")
            output_directory = self._job_output_directory(draft)
            # com_kt selects the latest DB row and uses its identity code to
            # choose the crop dimensions; do not infer this from OCR text.
            recog_sub_cd = get_latest_kt_recog_sub_cd(Path(store.database_path))
            database, updated_rows = update_kt_database_copy(
                Path(store.database_path),
                output_directory / "myCustomer.db",
                draft.reviewed_values,
            )
            original_image, crop_image = create_kt_images(
                draft.result.image_path, output_directory,
                recog_sub_cd,
            )
            notice = output_directory / "KT_OUTPUT_STATUS.txt"
            notice.write_text(
                "KT output generated from a working copy of the configured source DB. "
                "The source DB was not modified. JPG OCR is not scanner, sensor, or "
                "identity verification.\n",
                encoding="utf-8",
            )
            outcomes = self._transfer_result_files(
                kt_store_destinations(
                    Path(store.save_path), database, original_image, crop_image
                ),
                store.save_path,
                draft.result.job_id,
            )
            messagebox.showinfo(
                "KT 처리 완료",
                f"원본 DB는 유지하고 복사본 {updated_rows}행을 갱신했습니다.\n"
                f"공유폴더 전달 위치: {Path(store.save_path).expanduser() / 'IMAGE'}\n"
                + "\n".join(outcomes)
                + f"\n\n로컬 상태 안내: {notice}",
                parent=self.root,
            )
        except Exception as exc:
            messagebox.showerror("KT 처리 실패", str(exc), parent=self.root)


def main() -> None:
    root = tk.Tk()
    # Keep the source run and the Windows EXE on the same pipeline.
    NewWorkApp(root, ocr=PaddleOcrAdapter(device="cpu", ocr_version="PP-OCRv5"))
    root.mainloop()
