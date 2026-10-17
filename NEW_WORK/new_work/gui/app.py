from __future__ import annotations

from pathlib import Path
import os
from threading import Thread
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
from new_work.stores import Store, default_config_path, load_stores
from new_work.transfer import append_transfer_log, transfer_file


LEGACY_ID_TYPES = {
    "주민등록증 (1001 / R)": ("1001", "R"),
    "운전면허증 (1102 / D)": ("1102", "D"),
    "외국인등록증 (1401 / F)": ("1401", "F"),
    "복지카드 (1301 / S)": ("1301", "S"),
    "보훈카드 (1302 / N)": ("1302", "N"),
}


class NewWorkApp:
    def __init__(self, root: tk.Tk, ocr: OcrAdapter | None = None) -> None:
        self.root = root
        self.ocr = ocr or PaddleOcrAdapter()
        self.draft: ReviewDraft | None = None
        self._preview: ImageTk.PhotoImage | None = None
        self._entries: dict[str, ttk.Entry] = {}
        self._focused_entry: ttk.Entry | None = None
        try:
            self.stores = load_stores()
        except ValueError as exc:
            self.stores = []
            messagebox.showerror("접점 설정 오류", str(exc), parent=root)
        self.selected_store_id: str | None = None

        root.title("NEW_WORK — OCR 검토 준비")
        root.geometry("1180x780")
        self._build()

    def _build(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self.root, padding=10)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(toolbar, text="JPG 불러오기", command=self.choose_image).pack(side="left")
        self.ocr_button = ttk.Button(toolbar, text="OCR 실행", command=self.run_ocr)
        self.ocr_button.pack(side="left", padx=(8, 0))
        self.ocr_status = tk.StringVar(value=f"OCR 상태: {self.ocr.status}")
        ttk.Label(toolbar, textvariable=self.ocr_status).pack(side="left", padx=16)
        ttk.Label(toolbar, text="원본 문서 유형").pack(side="left", padx=(12, 4))
        self.id_type_choice = ttk.Combobox(
            toolbar, state="readonly", width=27,
            values=("유형 선택…", *LEGACY_ID_TYPES.keys()),
        )
        self.id_type_choice.current(0)
        self.id_type_choice.pack(side="left")
        self.id_type_choice.bind("<<ComboboxSelected>>", self._legacy_type_selected)

        store_bar = ttk.Frame(self.root, padding=(10, 0, 10, 6))
        store_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(store_bar, text="접점").pack(side="left")
        self.store_choice = ttk.Combobox(store_bar, state="readonly", width=38)
        self.store_choice.pack(side="left", padx=8)
        self.store_choice.bind("<<ComboboxSelected>>", self._store_selected)
        ttk.Button(store_bar, text="접점/공유폴더 설정", command=self.manage_stores).pack(side="left")
        ttk.Button(store_bar, text="KT 원본 최신행 참고", command=self.show_kt_latest_row).pack(side="left", padx=(8, 0))
        self._refresh_store_choice()

        image_box = ttk.LabelFrame(self.root, text="이미지 미리보기", padding=8)
        image_box.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=5)
        image_box.rowconfigure(0, weight=1)
        image_box.columnconfigure(0, weight=1)
        self.image_label = ttk.Label(image_box, text="JPG를 선택하세요", anchor="center")
        self.image_label.grid(row=0, column=0, sticky="nsew")
        self.image_path_text = tk.StringVar(value="")
        ttk.Label(image_box, textvariable=self.image_path_text, wraplength=360).grid(
            row=1, column=0, sticky="ew", pady=(8, 0)
        )

        review_box = ttk.LabelFrame(self.root, text="문자인식 값 검토", padding=10)
        review_box.grid(row=2, column=1, sticky="nsew", padx=(5, 10), pady=5)
        review_box.columnconfigure(1, weight=1)
        self._original_labels: dict[str, tk.StringVar] = {}
        for row, field in enumerate(REVIEW_TEXT_FIELDS):
            ttk.Label(review_box, text=field, width=22).grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=3
            )
            original = tk.StringVar(value="(인식 대기)")
            self._original_labels[field] = original
            ttk.Label(review_box, textvariable=original, width=24).grid(
                row=row, column=1, sticky="w", pady=3
            )
            entry = ttk.Entry(review_box)
            entry.grid(row=row, column=2, sticky="ew", pady=3)
            entry.bind("<FocusIn>", lambda _event, selected=entry: self._set_focused_entry(selected))
            self._entries[field] = entry

        ttk.Label(review_box, text="원본 인식값", foreground="#555").grid(
            row=len(REVIEW_TEXT_FIELDS), column=1, sticky="w", pady=(8, 2)
        )
        ttk.Label(review_box, text="검토/수정값", foreground="#555").grid(
            row=len(REVIEW_TEXT_FIELDS), column=2, sticky="w", pady=(8, 2)
        )
        lines_row = len(REVIEW_TEXT_FIELDS) + 1
        ttk.Label(review_box, text="OCR 원문 줄 (선택 후 현재 입력칸에 삽입)").grid(
            row=lines_row, column=0, columnspan=3, sticky="w", pady=(8, 3)
        )
        self.ocr_lines = tk.Listbox(review_box, height=8, exportselection=False)
        self.ocr_lines.grid(row=lines_row + 1, column=0, columnspan=3, sticky="nsew")
        lines_scroll = ttk.Scrollbar(review_box, orient="vertical", command=self.ocr_lines.yview)
        lines_scroll.grid(row=lines_row + 1, column=3, sticky="ns")
        self.ocr_lines.configure(yscrollcommand=lines_scroll.set)
        ttk.Button(
            review_box,
            text="선택한 OCR 줄을 입력칸에 삽입",
            command=self.insert_selected_ocr_line,
        ).grid(row=lines_row + 2, column=2, sticky="e", pady=(4, 0))
        review_box.rowconfigure(lines_row + 1, weight=1)

        footer = ttk.Frame(self.root, padding=10)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.review_status = tk.StringVar(value="이미지 선택 후 검토값을 입력할 수 있습니다.")
        ttk.Label(footer, textvariable=self.review_status).pack(side="left", fill="x", expand=True)
        ttk.Button(footer, text="검토 완료 표시", command=self.mark_reviewed).pack(side="right")

        carrier = ttk.LabelFrame(self.root, text="통신사 결과 처리", padding=8)
        carrier.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        ttk.Button(carrier, text="SK 보고서 생성/전달", command=self.run_sk).pack(side="left", padx=(0, 8))
        ttk.Button(carrier, text="KT 결과 생성/공유폴더 전달", command=self.run_kt).pack(side="left", padx=(0, 8))
        ttk.Label(
            carrier,
            text="SK/KT 크롭은 첨부 com_sk/com_kt의 최신 m.jpg 탐색 및 중앙 좌표 알고리즘을 사용합니다. LG 제외.",
        ).pack(side="left", padx=8)

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
        try:
            result = self.ocr.recognize(image_path)
        except Exception as exc:
            self.root.after(0, self._ocr_failed, exc)
            return
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
        self._legacy_type_selected()
        elapsed = "" if result.elapsed_seconds is None else f" · {result.elapsed_seconds:.2f}초"
        self.ocr_status.set(f"OCR 상태: {result.engine_name} {result.engine_version}")
        self.review_status.set(
            f"문자 {len(result.lines)}줄 인식 완료{elapsed}. 업무 필드 자동 분류는 아직 적용하지 않았습니다."
        )

    def _set_focused_entry(self, entry: ttk.Entry) -> None:
        self._focused_entry = entry

    def _legacy_type_selected(self, _event=None) -> None:
        selection = self.id_type_choice.get()
        selected = LEGACY_ID_TYPES.get(selection)
        if selected is None:
            return
        code, id_type = selected
        # com_sk maps the same RECOG_SUB_CD to both report keys and maps the
        # chosen identity kind to ID_TYPE. This is an explicit user choice.
        for field, value in (("kaitIdType", code), ("recogSubCd", code), ("idType", id_type)):
            entry = self._entries[field]
            entry.delete(0, tk.END)
            entry.insert(0, value)
        if code == "1401":
            self._entries["nationClCd"].delete(0, tk.END)
            self._entries["nationClCd"].insert(0, "F")
            self._entries["mskPos"].delete(0, tk.END)

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

    def mark_reviewed(self) -> None:
        if self.draft is None:
            messagebox.showinfo("JPG 선택", "먼저 JPG 이미지를 선택하세요.")
            return
        values = {field: entry.get() for field, entry in self._entries.items()}
        self.draft.mark_reviewed(values)
        self.review_status.set(
            f"검토 완료(현재 메모리): {len(self.draft.changed_fields)}개 필드 수정"
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
    device = os.environ.get("NEW_WORK_OCR_DEVICE", "cpu").strip().lower()
    ocr_version = os.environ.get("NEW_WORK_OCR_VERSION", "PP-OCRv3").strip()
    NewWorkApp(root, ocr=PaddleOcrAdapter(device=device, ocr_version=ocr_version))
    root.mainloop()
