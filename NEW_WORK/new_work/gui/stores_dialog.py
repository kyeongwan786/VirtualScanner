from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from new_work.stores import Store, load_stores, save_stores


class StoreManagerDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, on_change) -> None:
        super().__init__(master)
        self.title("접수 PC / Relay 자리 관리")
        self.geometry("700x380")
        self.transient(master)
        self.on_change = on_change
        try:
            self.stores = load_stores()
        except ValueError as exc:
            self.stores = []
            messagebox.showerror("접점 설정 오류", str(exc), parent=self)

        self.tree = ttk.Treeview(
            self,
            columns=("name", "station_id"),
            show="headings",
            selectmode="browse",
        )
        for key, title, width in (
            ("name", "접수 PC", 260),
            ("station_id", "Render Relay 자리 ID", 350),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        buttons = ttk.Frame(self, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="접점 추가", command=self.add_store).pack(side="left")
        ttk.Button(buttons, text="선택 수정", command=self.edit_store).pack(side="left", padx=6)
        ttk.Button(buttons, text="선택 삭제", command=self.delete_store).pack(side="left")
        ttk.Button(buttons, text="닫기", command=self.destroy).pack(side="right")
        self.refresh()

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for store in self.stores:
            self.tree.insert(
                "", "end", iid=store.store_id,
                values=(store.name, store.relay_station_id),
            )

    def selected_store(self) -> Store | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("접점 선택", "수정하거나 삭제할 접점을 선택하세요.", parent=self)
            return None
        selected_id = selection[0]
        return next((store for store in self.stores if store.store_id == selected_id), None)

    def edit_form(self, existing: Store | None = None) -> Store | None:
        dialog = tk.Toplevel(self)
        dialog.title("접점 수정" if existing else "접점 추가")
        dialog.transient(self)
        dialog.grab_set()
        fields = (
            ("name", "접점 이름", existing.name if existing else ""),
            ("relay_station_id", "Render Relay 자리 ID (Receiver 설정과 동일)",
             existing.relay_station_id if existing else ""),
        )
        entries: dict[str, ttk.Entry] = {}
        for row, (key, label, value) in enumerate(fields):
            ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=6)
            entry = ttk.Entry(dialog, width=75)
            entry.insert(0, value)
            entry.grid(row=row, column=1, sticky="ew", padx=6, pady=6)
            entries[key] = entry
        dialog.columnconfigure(1, weight=1)
        result: list[Store | None] = [None]

        def save() -> None:
            try:
                result[0] = Store.create(
                    name=entries["name"].get(),
                    save_path=existing.save_path if existing else "",
                    database_path=existing.database_path if existing else "",
                    store_id=existing.store_id if existing else None,
                    scan_id=existing.scan_id if existing else "",
                    send_org_id=existing.send_org_id if existing else "",
                    scan_name=existing.scan_name if existing else "",
                    ip_address=existing.ip_address if existing else "",
                    mac_address=existing.mac_address if existing else "",
                    scanner_serial_number=existing.scanner_serial_number if existing else "",
                    relay_station_id=entries["relay_station_id"].get(),
                    receiver_inbox=existing.receiver_inbox if existing else "",
                )
            except ValueError as exc:
                messagebox.showerror("입력 확인", str(exc), parent=dialog)
                return
            dialog.destroy()

        ttk.Button(dialog, text="저장", command=save).grid(row=len(fields), column=1, sticky="e", padx=10, pady=12)
        dialog.wait_window()
        return result[0]

    def add_store(self) -> None:
        store = self.edit_form()
        if store:
            self.stores.append(store)
            self.persist()

    def edit_store(self) -> None:
        old = self.selected_store()
        if old is None:
            return
        store = self.edit_form(old)
        if store:
            self.stores = [store if item.store_id == old.store_id else item for item in self.stores]
            self.persist()

    def delete_store(self) -> None:
        store = self.selected_store()
        if store is None:
            return
        if not messagebox.askyesno(
            "접점 삭제",
            f"'{store.name}'의 중앙 접점 설정만 삭제할까요? Relay 작업과 접수 PC 파일은 삭제하지 않습니다.",
            parent=self,
        ):
            return
        self.stores = [item for item in self.stores if item.store_id != store.store_id]
        self.persist()

    def persist(self) -> None:
        try:
            path = save_stores(self.stores)
        except (OSError, ValueError) as exc:
            messagebox.showerror("저장 실패", str(exc), parent=self)
            return
        self.refresh()
        self.on_change(self.stores)


def choose_store_index(stores: list[Store], store_id: str | None) -> int:
    return next((index for index, store in enumerate(stores) if store.store_id == store_id), -1)
