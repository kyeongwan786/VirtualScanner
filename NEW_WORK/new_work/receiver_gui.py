from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tkinter as tk
from threading import Thread
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


class ReceiverApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("NEW_WORK Receiver")
        self.root.geometry("1000x700")
        self.root.minsize(820, 560)
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
        self._queue_order: list[str] = []
        self._completed: set[str] = set()
        self._active: dict | None = None
        self._build()
        self._load_queue_state()
        if not all(self.config.get(key) for key in ("station_id", "relay_url", "token")):
            self.status.set("Receiver 설정 필요 · Relay 주소, 자리 ID, 자리 토큰을 지정하세요")
        self._refresh_queue()
        self.root.after(1000, self._poll_inbox)

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=10)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=3)
        shell.columnconfigure(1, weight=2)
        shell.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(shell)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Button(toolbar, text="Receiver 설정", command=self._edit_config).pack(side="left")
        ttk.Button(toolbar, text="새로고침", command=self._refresh_queue).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Relay 다시 확인", command=self._retry_failed).pack(side="left")
        self.status = tk.StringVar(value="수신 대기 중")
        ttk.Label(toolbar, textvariable=self.status).pack(side="left", padx=12)

        queue_frame = ttk.LabelFrame(shell, text="서류 스캔 대기열", padding=6)
        queue_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            queue_frame,
            columns=("carrier", "pages", "state"),
            show="tree headings",
            selectmode="extended",
        )
        self.tree.heading("#0", text="서류 / 작업")
        self.tree.heading("carrier", text="포탈")
        self.tree.heading("pages", text="페이지")
        self.tree.heading("state", text="상태")
        self.tree.column("#0", width=270, anchor="w")
        self.tree.column("carrier", width=65, anchor="center", stretch=False)
        self.tree.column("pages", width=65, anchor="center", stretch=False)
        self.tree.column("state", width=90, anchor="center", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(queue_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._show_selected)
        controls = ttk.Frame(queue_frame)
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Button(controls, text="위로", command=lambda: self._move_selected(-1)).pack(side="left")
        ttk.Button(controls, text="아래로", command=lambda: self._move_selected(1)).pack(side="left", padx=5)
        ttk.Button(controls, text="선택 서류 스캔에 올리기", command=self._activate_selected).pack(side="right")

        preview_frame = ttk.LabelFrame(shell, text="수신된 신분증", padding=6)
        preview_frame.grid(row=1, column=1, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview = ttk.Label(preview_frame, text="작업을 선택하면 이미지가 표시됩니다", anchor="center")
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.detail = tk.Text(preview_frame, height=8, wrap="word", state="disabled")
        self.detail.grid(row=1, column=0, sticky="ew", pady=(8, 0))

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
        order = state.get("order", [])
        self._queue_order = [item for item in order if isinstance(item, str)]
        completed = state.get("completed", [])
        self._completed = {item for item in completed if isinstance(item, str)}
        active = state.get("active")
        if isinstance(active, dict):
            self._active = active

    def _save_queue_state(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"order": self._queue_order, "active": self._active, "completed": sorted(self._completed)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _poll_inbox(self) -> None:
        self._sync_active_scan()
        configured = all(self.config.get(key) for key in ("relay_url", "station_id", "token"))
        if configured and not self._processing and monotonic() >= self._retry_after:
            self._processing = True
            Thread(target=self._receive_relay_work, daemon=True).start()
        self.root.after(5000, self._poll_inbox)

    def _receive_relay_work(self) -> None:
        try:
            job_dir, manifest = receive_work_package(
                {"relay_url": self.config["relay_url"], "token": self.config["token"]},
                self.config["station_id"], Path(self.config["work_path"]),
            ) or (None, None)
            if job_dir is None:
                self.root.after(0, self._receive_finished, None)
                return
            applied_marker = job_dir / "applied.json"
            if not applied_marker.exists():
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
            if self.tree.exists(str(key)):
                self.tree.set(str(key), "state", "스캔 완료")
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

    def _refresh_queue(self) -> None:
        self._sync_active_scan()
        self._items.clear()
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
            documents = manifest.get("documents", [])
            if not isinstance(documents, list):
                continue
            for document in documents:
                if not isinstance(document, dict):
                    continue
                doc_id = document.get("document_id")
                key = f"{job_id}:{doc_id}"
                try:
                    for relative in document.get("pages", []):
                        self._job_file(manifest_path.parent, str(relative))
                except (TypeError, ValueError):
                    continue
                self._items[key] = {
                    "key": key,
                    "job_id": job_id,
                    "job_dir": manifest_path.parent,
                    "carrier": manifest.get("carrier", ""),
                    "document": document,
                    "state": "대기",
                }
        valid = [key for key in self._queue_order if key in self._items]
        valid.extend(key for key in self._items if key not in valid)
        self._queue_order = valid
        active_keys = set()
        if self._active:
            active_keys = set(self._active.get("keys") or [self._active.get("key", "")])
        for key, item in self._items.items():
            if key in self._completed:
                item["state"] = "스캔 완료"
            elif key in active_keys:
                item["state"] = "스캔 중"
        self.tree.delete(*self.tree.get_children())
        for key in self._queue_order:
            item = self._items[key]
            doc = item["document"]
            self.tree.insert(
                "", "end", iid=key,
                text=f"{doc.get('label', '서류')} · {item['job_id'][:8]}",
                values=(item["carrier"], doc.get("page_count", 0), item["state"]),
            )
    def _selected_key(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _move_selected(self, offset: int) -> None:
        key = self._selected_key()
        if key is None:
            return
        index = self._queue_order.index(key)
        new_index = index + offset
        if not 0 <= new_index < len(self._queue_order):
            return
        self._queue_order[index], self._queue_order[new_index] = self._queue_order[new_index], self._queue_order[index]
        self._save_queue_state()
        self._refresh_queue()
        self.tree.selection_set(key)

    def _activate_selected(self) -> None:
        self._sync_active_scan()
        selected_keys = list(self.tree.selection())
        if not selected_keys or any(key not in self._items for key in selected_keys):
            messagebox.showinfo("서류 선택", "스캔할 서류를 선택하세요.", parent=self.root)
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
        first_pending = next(
            (queued for queued in self._queue_order if queued not in self._completed),
            None,
        )
        pending = [queued for queued in self._queue_order if queued not in self._completed]
        selected_in_order = [queued for queued in pending if queued in selected_keys]
        if (
            not selected_in_order
            or selected_in_order[0] != first_pending
            or set(selected_in_order) != set(pending[:len(selected_in_order)])
        ):
            messagebox.showinfo(
                "대기열 순서",
                "한 번에 스캔할 서류를 대기열 맨 위에서부터 연속으로 선택하세요.\n"
                "필요하면 위로/아래로 버튼으로 순서를 바꿀 수 있습니다.",
                parent=self.root,
            )
            return
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
                source_names.append(str(document.get("source_name", document.get("label", "documents"))))
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
                "dpi": 175,
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

    def _show_selected(self, _event=None) -> None:
        key = self._selected_key()
        item = self._items.get(key or "")
        if item is None:
            return
        image_path = item["job_dir"] / "identity" / "identity.jpg"
        if not image_path.is_file():
            image_path = item["job_dir"] / "identity" / "identity.jpeg"
        try:
            with Image.open(image_path) as image:
                preview = image.convert("RGB")
                preview.thumbnail((360, 390))
                self._photo = ImageTk.PhotoImage(preview)
            self.preview.configure(image=self._photo, text="")
        except OSError:
            self._photo = None
            self.preview.configure(image="", text="신분증 이미지를 열 수 없습니다")
        manifest = json.loads((item["job_dir"] / "manifest.json").read_text(encoding="utf-8"))
        values = manifest.get("reviewed_values", {})
        summary = "\n".join(f"{key}: {value}" for key, value in values.items())
        self.detail.configure(state="normal")
        self.detail.delete("1.0", tk.END)
        self.detail.insert("1.0", summary)
        self.detail.configure(state="disabled")


def main() -> None:
    root = tk.Tk()
    ReceiverApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
