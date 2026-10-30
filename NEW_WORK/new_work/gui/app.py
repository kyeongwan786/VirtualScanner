from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from math import cos, pi, sin
from pathlib import Path
from threading import Event, Thread
from time import perf_counter
import shutil
import sqlite3
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from uuid import uuid4

from PIL import Image, ImageDraw, ImageOps, ImageTk
from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
from urllib.parse import unquote_plus

from new_work.domain import REVIEW_TEXT_FIELDS, OcrResult, ReviewDraft
from new_work.carrier.sk_report import SK_ID_CROP_CODES
from new_work.ocr.adapter import OcrAdapter, OcrUnavailableError, PaddleOcrAdapter
from new_work.ocr.id_types import ID_TYPES, IdTypeDetection, detect_id_type
from new_work.ocr.field_extractor import (
    extract_identity_fields,
    is_valid_resident_number,
)
from new_work.ocr.mask_position import detect_mask_position
from new_work.ocr.result_writer import default_result_path, write_ocr_result
from new_work.carrier.kt import default_kt_image_directory, save_kt_submission
from new_work.image.face_crop import (
    id_portrait_bounds,
    id_portrait_fallback_bounds,
    id_portrait_size,
)
from new_work.windows_lifecycle import (
    create_tray_icon,
    register_current_user_autostart,
    unregister_current_user_autostart,
)
from new_work.pdf_render import default_job_directory, render_pdf_to_jpegs
from new_work.job_package import create_work_package


RECEIVER_STATION_NAMES = (
    "현풍", "이곡", "총각 다사", "혜화", "첨단", "부산", "판교", "범일", "동래",
    "인생네폰 가산", "호랑폰 오산점", "뉴폰", "다사(성원TM)", "명덕", "울산",
    "대교SW", "헬리오스",
)


def _rounded_rect_points(
    x0: float, y0: float, x1: float, y1: float, radius: float, segments: int = 5,
) -> tuple[float, ...]:
    radius = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    corners = (
        (x1 - radius, y0 + radius, -90, 0),
        (x1 - radius, y1 - radius, 0, 90),
        (x0 + radius, y1 - radius, 90, 180),
        (x0 + radius, y0 + radius, 180, 270),
    )
    points: list[float] = []
    for center_x, center_y, start_angle, end_angle in corners:
        for step in range(segments + 1):
            angle = (start_angle + (end_angle - start_angle) * step / segments) * pi / 180
            points.extend((center_x + radius * cos(angle), center_y + radius * sin(angle)))
    return tuple(points)


def _draw_ui_icon(
    canvas: tk.Canvas, name: str, x: float, y: float, size: float, color: str,
) -> None:
    scale = size / 32
    def pt(px: float, py: float) -> tuple[float, float]:
        return x + px * scale, y + py * scale

    width = max(1, round(1.8 * scale))
    if name == "identity":
        canvas.create_rectangle(*pt(3, 5), *pt(29, 27), outline=color, width=width)
        canvas.create_oval(*pt(7, 9), *pt(15, 17), outline=color, width=width)
        canvas.create_arc(*pt(6, 16), *pt(16, 25), start=0, extent=180,
                          style="arc", outline=color, width=width)
        for yy in (11, 16, 21):
            canvas.create_line(*pt(19, yy), *pt(26, yy), fill=color, width=width)
    elif name in {"document", "pdf"}:
        canvas.create_polygon(
            *[coordinate for point in ((7, 3), (20, 3), (27, 10), (27, 29), (5, 29), (5, 5))
              for coordinate in pt(*point)],
            outline=color, fill="", width=width, joinstyle="round",
        )
        canvas.create_line(*pt(19, 4), *pt(19, 11), *pt(26, 11),
                           fill=color, width=width)
        if name == "pdf":
            canvas.create_text(*pt(16, 20), text="PDF", fill=color,
                               font=("Segoe UI", max(5, round(7 * scale)), "bold"))
        else:
            for yy in (16, 20, 24):
                canvas.create_line(*pt(9, yy), *pt(23, yy), fill=color, width=width)
    elif name == "folder":
        canvas.create_polygon(
            *[coordinate for point in ((3, 8), (13, 8), (16, 12), (29, 12),
                                        (27, 26), (4, 26)) for coordinate in pt(*point)],
            outline=color, fill="", width=width, joinstyle="round",
        )
        canvas.create_line(*pt(4, 12), *pt(29, 12), fill=color, width=width)
    elif name == "send":
        canvas.create_polygon(
            *[coordinate for point in ((3, 14), (29, 3), (22, 29), (16, 19), (10, 23),
                                        (12, 16)) for coordinate in pt(*point)],
            outline=color, fill=color, width=1, joinstyle="round",
        )
        canvas.create_line(*pt(12, 16), *pt(29, 3), fill="#FFFFFF", width=width)
    elif name == "monitor":
        canvas.create_rectangle(*pt(3, 4), *pt(29, 22), outline=color, width=width)
        canvas.create_line(*pt(16, 22), *pt(16, 27), fill=color, width=width)
        canvas.create_line(*pt(10, 28), *pt(22, 28), fill=color, width=width)
    elif name == "lightning":
        canvas.create_polygon(
            *[coordinate for point in ((18, 1), (7, 17), (14, 17), (11, 31),
                                        (25, 12), (18, 12)) for coordinate in pt(*point)],
            outline=color, fill=color, width=1,
        )


class RoundedActionButton(tk.Canvas):
    """Small canvas button so V2 keeps rounded controls across Tk platforms."""

    _COLORS = {
        "Primary.TButton": ("#1769E8", "#0F57C8", "#AFC6EA", "#FFFFFF", "#FFFFFF"),
        "Secondary.TButton": ("#EDF4FF", "#DCEAFF", "#F2F5FA", "#2056A8", "#A0AEC0"),
        "Store.TButton": ("#FFFFFF", "#F4F8FE", "#EEF2F7", "#162D55", "#A0AEC0"),
        "SelectedStore.TButton": ("#EAF2FF", "#DCEAFF", "#F2F5FA", "#1456C0", "#A0AEC0"),
        "UnregisteredStore.TButton": ("#F5F8FC", "#F5F8FC", "#F5F8FC", "#6E829F", "#6E829F"),
    }

    def __init__(
        self, master, *, text: str, command=None, style: str = "Secondary.TButton",
        width: int | None = None, icon: str | None = None, **kwargs,
    ):
        self._text = text
        self._command = command
        self._style_name = style
        self._state = "normal"
        self._width_chars = width
        self._icon = icon
        self._hovered = False
        self._pressed = False
        self._font = tkfont.Font(
            root=master, family="Segoe UI",
            size=10 if style == "Primary.TButton" else
            (8 if style.endswith("Store.TButton") else 9),
            weight="bold" if style not in {"Store.TButton", "UnregisteredStore.TButton"}
            else "normal",
        )
        super().__init__(
            master, background="#FFFFFF", borderwidth=0, highlightthickness=0,
            takefocus=True,
        )
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)
        self.configure(**kwargs)
        self._update_requested_size()
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if cnf is None and not kwargs:
            return super().configure()
        if isinstance(cnf, str) and not kwargs:
            if cnf in {"text", "state", "command", "style", "width"}:
                return self.cget(cnf)
            return super().configure(cnf)
        options = dict(cnf) if isinstance(cnf, dict) else {}
        options.update(kwargs)
        if not hasattr(self, "_text"):
            return super().configure(cnf, **kwargs)
        text = options.pop("text", None)
        state = options.pop("state", None)
        command = options.pop("command", None)
        style = options.pop("style", None)
        width = options.pop("width", None)
        icon = options.pop("icon", None)
        if text is not None:
            self._text = text
        if state is not None:
            self._state = state
        if command is not None:
            self._command = command
        if style is not None:
            self._style_name = style
            self._font.configure(
                weight="bold" if style not in {"Store.TButton", "UnregisteredStore.TButton"}
                else "normal"
            )
            self._font.configure(
                size=10 if style == "Primary.TButton" else
                (8 if style.endswith("Store.TButton") else 9)
            )
        if width is not None:
            self._width_chars = width
        if icon is not None:
            self._icon = icon
        if options:
            super().configure(**options)
        self._update_requested_size()
        self._draw()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        if key == "command":
            return self._command
        if key == "style":
            return self._style_name
        if key == "width":
            return self._width_chars
        return super().cget(key)

    def _update_requested_size(self) -> None:
        lines = self._text.splitlines() or [""]
        natural_width = max(self._font.measure(line) for line in lines) + 28
        if self._icon:
            natural_width += 24
        if self._width_chars:
            natural_width = max(natural_width, self._font.measure("M" * self._width_chars) + 28)
        height = max(
            52 if self._style_name.endswith("Store.TButton") else
            (48 if self._style_name == "Primary.TButton" else 38),
            self._font.metrics("linespace") * len(lines) + 18,
        )
        tk.Canvas.configure(self, width=natural_width, height=height)

    def _draw(self) -> None:
        if not hasattr(self, "_font"):
            return
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        fill, hover, disabled, foreground, disabled_fg = self._COLORS.get(
            self._style_name, self._COLORS["Secondary.TButton"],
        )
        if self._state == "disabled":
            fill, foreground = disabled, disabled_fg
        elif self._hovered:
            fill = hover
        outline = "#1769E8" if self._style_name == "SelectedStore.TButton" else fill
        if self._style_name == "Store.TButton":
            outline = "#DCE7F4"
        elif self._style_name == "UnregisteredStore.TButton":
            outline = "#E4EBF4"
        self.delete("button")
        left, top = 0.5, 0.5
        right, bottom = width - 0.5, height - 0.5
        points = _rounded_rect_points(left, top, right, bottom, 8)
        self.create_polygon(
            *points, smooth=False, fill=fill, outline=outline,
            width=1, tags="button",
        )
        if self._icon:
            icon_size = 19 if self._style_name.endswith("Store.TButton") else 17
            icon_x = 11 if self._style_name.endswith("Store.TButton") else 15
            _draw_ui_icon(
                self, self._icon, icon_x, (height - icon_size) / 2, icon_size, foreground,
            )
            if self._style_name.endswith("Store.TButton"):
                self.create_text(
                    36, height / 2, text=self._text, fill=foreground,
                    font=self._font, justify="left", anchor="w", tags="button",
                )
            else:
                self.create_text(
                    width / 2 + 9, height / 2, text=self._text, fill=foreground,
                    font=self._font, justify="center", tags="button",
                )
        else:
            self.create_text(
                width / 2, height / 2, text=self._text, fill=foreground,
                font=self._font,
                justify="left" if self._style_name.endswith("Store.TButton") else "center",
                tags="button",
            )

    def _on_enter(self, _event) -> None:
        self._hovered = True
        self._draw()

    def _on_leave(self, _event) -> None:
        self._hovered = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event) -> None:
        if self._state == "normal":
            self._pressed = True
            self.focus_set()

    def _on_release(self, event) -> None:
        was_pressed = self._pressed
        self._pressed = False
        if was_pressed and self._state == "normal" and 0 <= event.x <= self.winfo_width() \
                and 0 <= event.y <= self.winfo_height():
            self._invoke()

    def _invoke(self, _event=None) -> str:
        if self._state == "normal" and self._command is not None:
            self._command()
        return "break"


class RoundedEntry(tk.Frame):
    """Borderless entry over a rounded canvas outline for the V2 review form."""

    def __init__(self, master, *, textvariable: tk.StringVar):
        super().__init__(master, background="#FFFFFF", borderwidth=0, height=36)
        self.grid_propagate(False)
        self._focused = False
        self._canvas = tk.Canvas(
            self, background="#FFFFFF", highlightthickness=0, borderwidth=0,
        )
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self._entry = tk.Entry(
            self, textvariable=textvariable, relief="flat", borderwidth=0,
            highlightthickness=0, background="#FFFFFF", foreground="#162D55",
            insertbackground="#1769E8", font=("Segoe UI", 10),
        )
        self._entry.place(x=9, y=4, relwidth=1, relheight=1, width=-18, height=-8)
        self.bind("<Configure>", self._draw)
        self._entry.bind("<FocusIn>", self._on_focus_in)
        self._entry.bind("<FocusOut>", self._on_focus_out)
        self._draw()

    def bind(self, sequence=None, func=None, add=None):
        if hasattr(self, "_entry") and sequence not in {"<Configure>", None}:
            return self._entry.bind(sequence, func, add)
        return super().bind(sequence, func, add)

    def focus_set(self):
        self._entry.focus_set()

    def _draw(self, _event=None) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        self._canvas.delete("entry-border")
        corner = min(7, width // 2, height // 2)
        points = (
            corner, 0, width - corner, 0, width, 0, width, corner,
            width, height - corner, width, height, width - corner, height,
            corner, height, 0, height, 0, height - corner, 0, corner, 0, 0,
        )
        border = "#1769E8" if self._focused else "#C9D8EB"
        self._canvas.create_polygon(
            *points, smooth=True, splinesteps=10, fill="#FFFFFF", outline=border,
            width=1, tags="entry-border",
        )
        self._canvas.tag_lower("entry-border")

    def _on_focus_in(self, _event) -> None:
        self._focused = True
        self._draw()

    def _on_focus_out(self, _event) -> None:
        self._focused = False
        self._draw()


class RoundedStatusBadge(tk.Canvas):
    """Text-driven pill badge with no platform-specific ttk rendering."""

    def __init__(
        self, master, *, textvariable: tk.StringVar, bg: str, fg: str,
        font: tuple = ("Segoe UI", 9, "bold"), padx: int = 10, pady: int = 5,
    ):
        self._textvariable = textvariable
        self._bg = bg
        self._fg = fg
        self._font = tkfont.Font(root=master, font=font)
        self._padx = padx
        self._pady = pady
        super().__init__(master, background="#FFFFFF", highlightthickness=0, borderwidth=0)
        self._trace = textvariable.trace_add("write", self._draw)
        self.bind("<Configure>", self._draw)
        self._draw()

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf) if isinstance(cnf, dict) else {}
        options.update(kwargs)
        if not hasattr(self, "_textvariable"):
            return super().configure(cnf, **kwargs)
        bg = options.pop("bg", options.pop("background", None))
        fg = options.pop("fg", options.pop("foreground", None))
        if bg is not None:
            self._bg = bg
        if fg is not None:
            self._fg = fg
        if options:
            super().configure(**options)
        self._draw()

    config = configure

    def _draw(self, *_args) -> None:
        text = self._textvariable.get()
        width = max(1, self._font.measure(text) + self._padx * 2)
        height = max(1, self._font.metrics("linespace") + self._pady * 2)
        tk.Canvas.configure(self, width=width, height=height)
        self.delete("badge")
        if self.winfo_width() <= 1 or self.winfo_height() <= 1:
            return
        actual_width, actual_height = self.winfo_width(), self.winfo_height()
        radius = min(actual_height // 2, actual_width // 2)
        points = (
            radius, 0, actual_width - radius, 0, actual_width, 0,
            actual_width, radius, actual_width, actual_height - radius,
            actual_width, actual_height, actual_width - radius, actual_height,
            radius, actual_height, 0, actual_height, 0, actual_height - radius,
            0, radius, 0, 0,
        )
        self.create_polygon(
            *points, smooth=True, splinesteps=10, fill=self._bg, outline=self._bg,
            tags="badge",
        )
        self.create_text(
            actual_width / 2, actual_height / 2, text=text, fill=self._fg,
            font=self._font, tags="badge",
        )


class RoundedComboBox(tk.Canvas):
    """Compact rounded selector used for portal and identity type in V2."""

    def __init__(
        self, master, *, values: tuple[str, ...], textvariable: tk.StringVar,
        state: str = "readonly", width: int = 190,
    ):
        self._values = tuple(values)
        self._textvariable = textvariable
        self._state = state
        self._focused = False
        self._popup: tk.Toplevel | None = None
        self._popup_canvas: tk.Canvas | None = None
        self._popup_hover = -1
        self._font = tkfont.Font(root=master, family="Segoe UI", size=10)
        super().__init__(
            master, width=width, height=38, background="#FFFFFF",
            highlightthickness=0, borderwidth=0, takefocus=True,
        )
        self._textvariable.trace_add("write", self._draw)
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._show_menu)
        self.bind("<Return>", self._show_menu)
        self.bind("<space>", self._show_menu)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self._draw()

    def current(self, index: int | None = None):
        if index is None:
            try:
                return self._values.index(self.get())
            except ValueError:
                return -1
        if 0 <= index < len(self._values):
            self.set(self._values[index])

    def get(self) -> str:
        return self._textvariable.get()

    def set(self, value: str, *, notify: bool = False) -> None:
        if value not in self._values:
            return
        self._textvariable.set(value)
        if notify:
            self.event_generate("<<ComboboxSelected>>")

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf) if isinstance(cnf, dict) else {}
        options.update(kwargs)
        if not hasattr(self, "_values"):
            return super().configure(cnf, **kwargs)
        state = options.pop("state", None)
        values = options.pop("values", None)
        if state is not None:
            self._state = state
        if values is not None:
            self._values = tuple(values)
        if options:
            super().configure(**options)
        self._draw()

    config = configure

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "values":
            return self._values
        return super().cget(key)

    def _draw(self, *_args) -> None:
        try:
            width, height = self.winfo_width(), self.winfo_height()
            if width <= 1 or height <= 1:
                return
            self.delete("combo")
            border = "#1769E8" if self._focused else "#D8E3F0"
            radius = min(8, width // 2, height // 2)
            points = (
                radius, 0, width - radius, 0, width, 0, width, radius,
                width, height - radius, width, height, width - radius, height,
                radius, height, 0, height, 0, height - radius, 0, radius, 0, 0,
            )
            self.create_polygon(
                *points, smooth=True, splinesteps=10, fill="#FFFFFF", outline=border,
                width=1, tags="combo",
            )
            self.create_text(
                13, height / 2, text=self.get(), anchor="w", fill="#162D55",
                font=self._font, tags="combo",
            )
            arrow_x = width - 16
            self.create_line(
                arrow_x - 3, height / 2 - 1, arrow_x, height / 2 + 2,
                arrow_x + 3, height / 2 - 1, fill="#536C8D", width=1.6,
                capstyle="round", joinstyle="round", tags="combo",
            )
        except tk.TclError:
            # StringVar traces can run once more after this widget is destroyed.
            return

    def _show_menu(self, _event=None) -> str:
        if self._state == "disabled":
            return "break"
        if self._popup is not None and self._popup.winfo_exists():
            self._close_popup()
            return "break"

        row_height = 32
        padding = 6
        popup_width = max(self.winfo_width(), self._font.measure(max(self._values, key=len, default="")) + 32)
        popup_height = len(self._values) * row_height + padding * 2
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = min(self.winfo_rootx(), max(0, screen_width - popup_width - 8))
        y = self.winfo_rooty() + self.winfo_height() + 4
        if y + popup_height > screen_height - 8:
            y = max(8, self.winfo_rooty() - popup_height - 4)

        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        popup.geometry(f"{popup_width}x{popup_height}+{x}+{y}")
        popup.configure(background="#FFFFFF")
        popup_canvas = tk.Canvas(
            popup, width=popup_width, height=popup_height,
            background="#FFFFFF", highlightthickness=0, borderwidth=0,
        )
        popup_canvas.pack(fill="both", expand=True)
        self._popup = popup
        self._popup_canvas = popup_canvas
        self._popup_hover = -1
        popup_canvas.bind("<Configure>", lambda _event: self._draw_popup())
        popup_canvas.bind("<Motion>", self._on_popup_motion)
        popup_canvas.bind("<Leave>", self._on_popup_leave)
        popup_canvas.bind("<Button-1>", self._on_popup_click)
        popup.bind("<Escape>", lambda _event: self._close_popup())
        popup.bind("<FocusOut>", lambda _event: popup.after_idle(self._close_if_focus_left))
        popup.update_idletasks()
        self._draw_popup()
        popup.lift()
        popup.focus_force()
        return "break"

    def _draw_popup(self) -> None:
        canvas = self._popup_canvas
        popup = self._popup
        if canvas is None or popup is None or not popup.winfo_exists():
            return
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        canvas.delete("all")
        canvas.create_polygon(
            10, 2, width - 10, 2, width - 2, 2, width - 2, 10,
            width - 2, height - 10, width - 2, height - 2,
            width - 10, height - 2, 10, height - 2, 2, height - 2,
            2, height - 10, 2, 10, 2, 2,
            smooth=True, splinesteps=12, fill="#FFFFFF", outline="#D8E3F0", width=1,
        )
        selected = self.get()
        for index, value in enumerate(self._values):
            top = 6 + index * 32
            bottom = top + 32
            if value == selected or index == self._popup_hover:
                fill = "#EAF2FF" if value == selected else "#F4F8FE"
                canvas.create_rectangle(6, top, width - 6, bottom, fill=fill, outline=fill)
            canvas.create_text(
                16, (top + bottom) / 2, text=value, anchor="w",
                fill="#1456C0" if value == selected else "#243B5B",
                font=self._font if value != selected else ("Segoe UI", 10, "bold"),
            )

    def _popup_index_at(self, event) -> int:
        index = (event.y - 6) // 32
        return index if 0 <= index < len(self._values) else -1

    def _on_popup_motion(self, event) -> None:
        self._popup_hover = self._popup_index_at(event)
        self._draw_popup()

    def _on_popup_leave(self, _event) -> None:
        self._popup_hover = -1
        self._draw_popup()

    def _on_popup_click(self, event) -> None:
        index = self._popup_index_at(event)
        if index >= 0:
            self.set(self._values[index], notify=True)
        self._close_popup()

    def _close_if_focus_left(self) -> None:
        popup = self._popup
        if popup is None or not popup.winfo_exists():
            return
        focused = self.focus_get()
        if focused is None or focused.winfo_toplevel() is not popup:
            self._close_popup(restore_focus=False)

    def _close_popup(self, *, restore_focus: bool = True) -> None:
        popup = self._popup
        self._popup = None
        self._popup_canvas = None
        self._popup_hover = -1
        if popup is not None and popup.winfo_exists():
            popup.destroy()
        if restore_focus:
            self.focus_set()

    def _on_focus_in(self, _event) -> None:
        self._focused = True
        self._draw()

    def _on_focus_out(self, _event) -> None:
        self._focused = False
        self._draw()


class PortalSegmentedControl(tk.Canvas):
    """Always-visible SK/KT switch without a platform-native dropdown."""

    def __init__(self, master, *, textvariable: tk.StringVar, width: int = 108):
        self._textvariable = textvariable
        self._state = "normal"
        self._font = tkfont.Font(root=master, family="Segoe UI", size=9, weight="bold")
        super().__init__(
            master, width=width, height=34, background="#1A4178",
            highlightthickness=0, borderwidth=0, takefocus=True,
        )
        self._textvariable.trace_add("write", self._draw)
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._choose)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self._hover_index = -1
        self._draw()

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf) if isinstance(cnf, dict) else {}
        options.update(kwargs)
        if not hasattr(self, "_textvariable"):
            return super().configure(cnf, **kwargs)
        state = options.pop("state", None)
        if state is not None:
            self._state = state
        if options:
            super().configure(**options)
        self._draw()

    config = configure

    def cget(self, key):
        if key == "state":
            return self._state
        return super().cget(key)

    def _choose(self, event) -> None:
        if self._state == "disabled":
            return
        midpoint = self.winfo_width() / 2
        self._textvariable.set("SK" if event.x < midpoint else "KT")

    def _motion(self, event) -> None:
        self._hover_index = 0 if event.x < self.winfo_width() / 2 else 1
        self._draw()

    def _leave(self, _event=None) -> None:
        self._hover_index = -1
        self._draw()

    def _draw(self, *_args) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        self.delete("portal")

        def rounded_box(x0, y0, x1, y1, radius, *, fill, outline):
            radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
            points = (
                x0 + radius, y0, x1 - radius, y0, x1, y0, x1, y0 + radius,
                x1, y1 - radius, x1, y1, x1 - radius, y1, x0 + radius, y1,
                x0, y1, x0, y1 - radius, x0, y0 + radius, x0, y0,
            )
            self.create_polygon(
                *points, smooth=True, splinesteps=12, fill=fill, outline=outline,
                width=1, tags="portal",
            )

        disabled = self._state == "disabled"
        rounded_box(
            0.5, 0.5, width - 0.5, height - 0.5, 9,
            fill="#143764", outline="#59769A",
        )
        selected = self._textvariable.get()
        half = width / 2
        for index, value in enumerate(("SK", "KT")):
            x0 = 3 if index == 0 else half
            x1 = half if index == 0 else width - 3
            is_selected = value == selected
            if is_selected:
                rounded_box(
                    x0, 3, x1, height - 3, 7,
                    fill="#FFFFFF" if not disabled else "#E8EDF4",
                    outline="#FFFFFF" if not disabled else "#E8EDF4",
                )
            elif self._hover_index == index and not disabled:
                rounded_box(x0 + 2, 4, x1 - 2, height - 4, 6,
                            fill="#244B7B", outline="#244B7B")
            self.create_text(
                (x0 + x1) / 2, height / 2, text=value,
                fill=("#194A86" if is_selected else
                      ("#A6B9D2" if disabled else "#E5EDF8")),
                font=self._font, tags="portal",
            )


class RoundedProgressBar(tk.Canvas):
    """Rounded progress track for document conversion cards."""

    _COLORS = {
        "Success.Horizontal.TProgressbar": "#1EA85A",
        "Active.Horizontal.TProgressbar": "#2374EA",
        "Error.Horizontal.TProgressbar": "#D04444",
    }

    def __init__(self, master, *, length: int = 138, height: int = 12):
        self._maximum = 100.0
        self._value = 0.0
        self._mode = "determinate"
        self._style_name = "Active.Horizontal.TProgressbar"
        self._animation_phase = 0.0
        self._animation_job = None
        super().__init__(
            master, width=length, height=height, background="#FFFFFF",
            borderwidth=0, highlightthickness=0,
        )
        self.bind("<Configure>", lambda _event: self._draw())
        self._draw()

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf) if isinstance(cnf, dict) else {}
        options.update(kwargs)
        maximum = options.pop("maximum", None)
        value = options.pop("value", None)
        mode = options.pop("mode", None)
        style = options.pop("style", None)
        if maximum is not None:
            self._maximum = max(1.0, float(maximum))
        if value is not None:
            self._value = min(max(float(value), 0.0), self._maximum)
        if mode is not None:
            self._mode = mode
        if style is not None:
            self._style_name = style
        if options:
            super().configure(**options)
        self._draw()

    config = configure

    def start(self, interval: int = 50) -> None:
        self.stop()
        self._mode = "indeterminate"
        self._tick_ms = max(16, interval)
        self._animate()

    def stop(self) -> None:
        if self._animation_job is not None:
            try:
                self.after_cancel(self._animation_job)
            except tk.TclError:
                pass
            self._animation_job = None

    def _animate(self) -> None:
        self._animation_phase = (self._animation_phase + 0.035) % 1.0
        self._draw()
        self._animation_job = self.after(self._tick_ms, self._animate)

    def _draw(self) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        self.delete("progress")
        radius = max(2, height // 2)
        self.create_rectangle(
            radius, 1, width - radius, height - 1, fill="#E9F1F8", outline="",
            tags="progress",
        )
        self.create_oval(1, 1, height - 1, height - 1, fill="#E9F1F8", outline="",
                         tags="progress")
        self.create_oval(width - height + 1, 1, width - 1, height - 1,
                         fill="#E9F1F8", outline="", tags="progress")
        color = self._COLORS.get(self._style_name, "#2374EA")
        inner_width = max(0, width - 2)
        if self._mode == "indeterminate":
            segment_width = max(height * 2, round(inner_width * 0.34))
            travel = max(0, inner_width - segment_width)
            left = 1 + round(travel * self._animation_phase)
            segment_width = min(segment_width, width - left - 1)
        else:
            segment_width = round(inner_width * self._value / self._maximum)
            left = 1
        if segment_width <= 0:
            return
        right = left + segment_width
        cap = min(height // 2, segment_width // 2)
        if segment_width <= height:
            self.create_oval(left, 1, right, height - 1, fill=color, outline="", tags="progress")
        else:
            self.create_rectangle(left + cap, 1, right - cap, height - 1,
                                  fill=color, outline="", tags="progress")
            self.create_oval(left, 1, left + cap * 2, height - 1,
                             fill=color, outline="", tags="progress")
            self.create_oval(right - cap * 2, 1, right, height - 1,
                             fill=color, outline="", tags="progress")
from new_work.stores import Store, load_stores
from new_work.gui.stores_dialog import StoreManagerDialog
from new_work.relay_config import load_relay_config, save_relay_config
from new_work.relay_client import (
    DeliveryPendingError,
    TransferCancelledError,
    cancel_work_package,
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
        self._id_crop_preview_is_fallback = False
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
        self._pending_auto_send: tuple[Store, str] | None = None
        self._transfer_cancel_event: Event | None = None
        self._reset_requested = False
        self._selected_store_index = -1
        self._store_card_frames: list[tuple[int | None, RoundedActionButton]] = []
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
        root.title("신분증 확인" if version == "v1" else "신분증 확인 송신기")
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
        self.ocr_status.set("모델 준비 중" if self.version == "v2"
                            else "OCR 모델 준비 중 · 백그라운드에서 불러오는 중")
        self._show_ocr_loading()
        Thread(target=self._warmup_ocr, daemon=True).start()

    def _show_ocr_loading(self) -> None:
        if self.version == "v2":
            self.ocr_status.set("모델 준비 중")
            return
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
        if hasattr(self, "ocr_run_button"):
            self.ocr_run_button.configure(state="normal")
        self.ocr_status.set("OCR 준비 완료" if self.version == "v2"
                            else f"OCR 준비됨 · {self.ocr.status}")
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
        if hasattr(self, "ocr_run_button"):
            self.ocr_run_button.configure(state="normal")
        self.ocr_status.set("모델 로드 실패" if self.version == "v2"
                            else f"OCR 모델 준비 실패 · 다시 실행하면 재시도합니다: {error}")
        self._pending_ocr_path = None
        if self._pending_auto_send is not None:
            self._cancel_pending_auto_send("OCR 모델 준비에 실패해 자동 전송 예약을 취소했습니다.")

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
        self.crop_reset_button = ttk.Button(
            buttons, text="자동 영역 복원", command=self._reset_crop_bounds,
            state="disabled",
        )
        self.crop_reset_button.pack(side="left", padx=3)

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
        self.image_label = tk.Canvas(
            preview_area, background="#FFFFFF", highlightthickness=0, borderwidth=0,
        )
        self.image_label.grid(row=0, column=0, sticky="nsew")
        self.image_label.bind("<Configure>", self._schedule_preview_resize, add="+")
        self.image_label.bind("<ButtonPress-1>", self._crop_pointer_down)
        self.image_label.bind("<B1-Motion>", self._crop_pointer_move)
        self.image_label.bind("<ButtonRelease-1>", self._crop_pointer_up)
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
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        if sys.platform == "darwin":
            window_x, window_y = 20, 40
            window_width = max(1120, screen_width - 40)
            window_height = max(700, screen_height - 150)
        else:
            window_x, window_y = 20, 20
            window_width = max(1120, screen_width - 40)
            window_height = max(700, screen_height - 60)
        self.root.geometry(
            f"{window_width}x{window_height}+{window_x}+{window_y}"
        )
        self.root.minsize(min(1120, window_width), min(700, window_height))
        self.root.resizable(True, True)
        shell = ttk.Frame(self.root, style="App.TFrame", padding=(8, 8, 8, 0))
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        receiver_height = max(190, min(204, round(window_height * 0.195)))
        footer_height = max(84, min(94, round(window_height * 0.095)))
        work_height = max(
            300, min(680, window_height - receiver_height - footer_height - 94)
        )
        shell.rowconfigure(1, weight=1, minsize=work_height)
        shell.rowconfigure(2, weight=0, minsize=receiver_height)
        shell.rowconfigure(3, weight=0, minsize=footer_height)

        header = tk.Frame(shell, bg="#1A4178", height=64)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        header.grid_propagate(False)
        header.columnconfigure(1, weight=1)
        header_gradient = tk.Canvas(
            header, background="#1A4178", highlightthickness=0, borderwidth=0,
        )
        header_gradient.place(x=0, y=0, relwidth=1, relheight=1)

        def paint_header_gradient(event) -> None:
            header_gradient.delete("gradient")
            steps = max(1, min(240, event.width))
            left = (34, 78, 139)
            right = (20, 48, 91)
            for index in range(steps):
                ratio = index / max(1, steps - 1)
                color = "#%02x%02x%02x" % tuple(
                    round(a + (b - a) * ratio) for a, b in zip(left, right)
                )
                x0 = round(event.width * index / steps)
                x1 = round(event.width * (index + 1) / steps) + 1
                header_gradient.create_rectangle(
                    x0, 0, x1, event.height, fill=color, outline=color, tags="gradient",
                )
            def match_child_surfaces() -> None:
                current_width = max(1, header.winfo_width())
                for child in header.winfo_children():
                    if child is header_gradient or child is brand_mark:
                        continue
                    if not isinstance(child, (tk.Label, tk.Button)):
                        continue
                    ratio = min(1.0, max(0.0, (child.winfo_x() + child.winfo_width() / 2)
                                         / current_width))
                    sampled = "#%02x%02x%02x" % tuple(
                        round(a + (b - a) * ratio) for a, b in zip(left, right)
                    )
                    if isinstance(child, tk.Button) and child is not None:
                        child.configure(bg=sampled)
                    else:
                        child.configure(bg=sampled)
            header.after_idle(match_child_surfaces)

        header.bind("<Configure>", paint_header_gradient)
        brand_mark = tk.Canvas(
            header, width=40, height=40, bg="#2563EB", highlightthickness=0,
        )
        brand_mark.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(12, 10), pady=(8, 16))
        brand_mark.create_rectangle(11, 7, 29, 33, fill="", outline="white", width=2)
        brand_mark.create_line(15, 14, 25, 14, fill="white", width=2)
        brand_mark.create_line(15, 20, 25, 20, fill="white", width=2)
        brand_mark.create_line(15, 26, 23, 26, fill="white", width=2)
        tk.Label(
            header, text="신분증 확인 송신기", bg="#1A4178", fg="white",
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=1, sticky="sw", pady=(8, 0))
        tk.Label(
            header, text="신분증과 서류를 넣으면 동시에 처리한 뒤 선택한 자리로 전송합니다.",
            bg="#1A4178", fg="#D5E4FA", font=("Segoe UI", 9),
        ).grid(row=1, column=1, sticky="nw", pady=(0, 8))
        self.relay_header_var = tk.StringVar()
        self.relay_header_label = tk.Label(
            header, textvariable=self.relay_header_var, bg="#1A4178", fg="#9AE6B4",
            font=("Segoe UI", 10, "bold"),
        )
        self.relay_header_label.grid(row=0, column=2, rowspan=2, padx=(8, 18))
        self.relay_header_label.configure(cursor="hand2")
        self.relay_header_label.bind("<Button-1>", lambda _event: self._open_relay_settings())
        tk.Frame(header, bg="#7891B2", width=1, height=28).grid(
            row=0, column=3, rowspan=2, pady=16, sticky="ns"
        )
        tk.Label(
            header, text="포탈", bg="#1A4178", fg="white", font=("Segoe UI", 9),
        ).grid(row=0, column=4, rowspan=2, sticky="e", padx=(14, 7))
        self.portal_box = PortalSegmentedControl(
            header, width=108, textvariable=self.portal_var,
        )
        self.portal_box.grid(row=0, column=5, rowspan=2, padx=(0, 14), pady=15)
        self.portal_buttons = [self.portal_box]

        work = ttk.Frame(shell, style="App.TFrame")
        work.grid(row=1, column=0, sticky="nsew")
        work.columnconfigure(0, weight=11)
        work.columnconfigure(1, weight=9)
        work.rowconfigure(0, weight=1)
        work.configure(height=work_height)
        work.grid_propagate(False)

        ocr_panel_shell, ocr_panel = self._make_v2_rounded_panel(work)
        ocr_panel_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ocr_panel.columnconfigure(0, weight=1)
        ocr_panel.rowconfigure(3, weight=1)
        self.ocr_status = tk.StringVar(value="OCR 대기")
        ocr_heading = ttk.Frame(ocr_panel, style="PanelContent.TFrame")
        ocr_heading.grid(row=0, column=0, rowspan=2, sticky="ew", pady=(0, 7))
        ocr_heading.columnconfigure(1, weight=1)
        ocr_icon = self._make_v2_icon_tile(ocr_heading, "identity", size=40)
        ocr_icon.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10))
        ttk.Label(ocr_heading, text="신분증 OCR", style="PanelTitle.TLabel").grid(
            row=0, column=1, sticky="sw"
        )
        ttk.Label(
            ocr_heading, text="신분증 이미지를 선택하거나 드래그하면 바로 OCR을 실행합니다.",
            style="PanelMuted.TLabel",
        ).grid(row=1, column=1, sticky="nw", pady=(1, 0))
        self.ocr_status_badge = RoundedStatusBadge(
            ocr_heading, textvariable=self.ocr_status, bg="#EEF3FA", fg="#607696",
            padx=10, pady=5,
        )
        self.ocr_status_badge.grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 0))
        id_drop_shell, id_drop = self._make_v2_drop_zone(ocr_panel)
        id_drop_shell.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        id_drop.columnconfigure(1, weight=1)
        self.ocr_button = RoundedActionButton(
            id_drop, text="신분증 이미지 선택", command=self.import_and_ocr,
            style="Primary.TButton", icon="folder",
        )
        self.ocr_button.grid(row=0, column=0, sticky="ns", padx=(0, 9))
        ttk.Label(
            id_drop, text="신분증 이미지 드래그 앤 드롭\n(JPG, JPEG, PNG)",
            anchor="center", justify="center", style="DropZoneMuted.TLabel",
        ).grid(row=0, column=1, sticky="ew")
        self.ocr_run_button = RoundedActionButton(
            id_drop, text="OCR 다시 실행", command=self.run_ocr,
            style="Secondary.TButton",
        )
        self.ocr_run_button.grid(row=0, column=2, sticky="ns", padx=(9, 0))

        identity_body = ttk.Frame(ocr_panel, style="PanelContent.TFrame")
        identity_body.grid(row=3, column=0, sticky="nsew")
        identity_body.columnconfigure(0, weight=11)
        identity_body.columnconfigure(1, weight=9)
        identity_body.rowconfigure(0, weight=1)
        preview_shell, preview_column = self._make_v2_rounded_panel(
            identity_body, radius=9, outside_color="#FFFFFF",
        )
        preview_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        preview_column.columnconfigure(0, weight=1)
        preview_column.rowconfigure(1, weight=1)
        ttk.Label(
            preview_column, text="신분증 이미지 · idCrop 조정",
            style="PanelFieldName.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.image_label = tk.Canvas(
            preview_column, background="#FFFFFF", highlightthickness=0, borderwidth=0,
        )
        empty_identity_icon = Image.new("RGBA", (72, 72), (255, 255, 255, 0))
        empty_identity_draw = ImageDraw.Draw(empty_identity_icon)
        empty_identity_draw.ellipse((4, 4, 68, 68), fill="#EAF2FF")
        empty_identity_draw.rounded_rectangle(
            (16, 22, 56, 50), radius=6, fill="#2374EA",
        )
        empty_identity_draw.ellipse((21, 28, 31, 38), fill="#FFFFFF")
        empty_identity_draw.arc((19, 34, 33, 48), start=180, end=360, fill="#FFFFFF", width=2)
        empty_identity_draw.line((38, 30, 50, 30), fill="#FFFFFF", width=2)
        empty_identity_draw.line((38, 36, 50, 36), fill="#FFFFFF", width=2)
        empty_identity_draw.line((38, 42, 47, 42), fill="#FFFFFF", width=2)
        self._identity_empty_photo = ImageTk.PhotoImage(empty_identity_icon, master=self.root)
        self.image_label.create_image(
            0, 0, anchor="center", image=self._identity_empty_photo, tags="empty-state",
        )
        self.image_label.create_text(
            0, 0, text="신분증 사진 놓기\nJPG · PNG · OCR 자동 시작",
            anchor="n", justify="center", fill="#667085", font=("Segoe UI", 10),
            tags="empty-state",
        )
        self.image_label.grid(row=1, column=0, sticky="nsew")
        self.image_label.bind("<Configure>", self._draw_empty_preview)
        self.image_label.bind("<Configure>", self._schedule_preview_resize, add="+")
        self.image_label.bind("<ButtonPress-1>", self._crop_pointer_down)
        self.image_label.bind("<B1-Motion>", self._crop_pointer_move)
        self.image_label.bind("<ButtonRelease-1>", self._crop_pointer_up)
        self.id_image_name = tk.StringVar(value="선택된 신분증 없음")
        ttk.Label(preview_column, textvariable=self.id_image_name,
                  style="PanelMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        self.id_crop_value = tk.StringVar(value="신분증 종류를 선택하면 크롭 영역을 표시합니다")
        ttk.Label(
            preview_column, textvariable=self.id_crop_value, style="PanelStatus.TLabel",
            wraplength=400,
        ).grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.crop_reset_button = RoundedActionButton(
            preview_column, text="자동 idCrop 복원", command=self._reset_crop_bounds,
            state="disabled", style="Secondary.TButton",
        )
        self.crop_reset_button.grid(row=4, column=0, sticky="w", pady=(4, 0))

        result_shell, result_column = self._make_v2_rounded_panel(
            identity_body, radius=9, outside_color="#FFFFFF",
        )
        result_shell.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        result_column.columnconfigure(0, weight=1)
        result_column.rowconfigure(1, weight=1)
        ttk.Label(
            result_column, text="OCR 결과 · 항목별 확인 및 수정",
            style="PanelFieldName.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.review_status = tk.StringVar(value="")
        result_scroll_frame = ttk.Frame(result_column, style="PanelContent.TFrame")
        result_scroll_frame.grid(row=1, column=0, sticky="nsew")
        result_scroll_frame.columnconfigure(0, weight=1)
        result_scroll_frame.rowconfigure(0, weight=1)
        self.result_canvas = tk.Canvas(
            result_scroll_frame, background="#FFFFFF", highlightthickness=0,
        )
        self.result_canvas.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(
            result_scroll_frame, orient="vertical", command=self.result_canvas.yview,
        )
        result_scroll.grid(row=0, column=1, sticky="ns")

        def update_result_scroll(first: str, last: str) -> None:
            result_scroll.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                result_scroll.grid_remove()
            else:
                result_scroll.grid()

        self.result_canvas.configure(yscrollcommand=update_result_scroll)
        self.result_table = ttk.Frame(self.result_canvas, style="Card.TFrame", padding=(4, 3))
        self.result_window = self.result_canvas.create_window(
            (0, 0), window=self.result_table, anchor="nw",
        )
        self.result_table.columnconfigure(1, weight=1)
        self.result_table.bind(
            "<Configure>",
            lambda _event: self.result_canvas.configure(
                scrollregion=self.result_canvas.bbox("all")
            ),
        )
        self.result_canvas.bind(
            "<Configure>",
            lambda event: self.result_canvas.itemconfigure(self.result_window, width=event.width),
        )
        self.raw_ocr_button = RoundedActionButton(
            result_column, text="OCR 원문 모두 보기", command=self._show_raw_ocr,
            style="Secondary.TButton",
        )
        self.raw_ocr_button.grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            result_column, textvariable=self.review_status, style="Status.TLabel",
            wraplength=450,
        ).grid(row=3, column=0, sticky="ew", pady=(4, 0))

        ocr_footer = tk.Frame(ocr_panel, bg="#EAF3FF", padx=10, pady=7)
        ocr_footer.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        ocr_footer.columnconfigure(1, weight=1)
        lightning = tk.Canvas(ocr_footer, width=17, height=21, bg="#EAF3FF",
                              highlightthickness=0)
        lightning.grid(row=0, column=0, sticky="w", padx=(0, 7))
        lightning.create_polygon(10, 1, 3, 12, 8, 12, 6, 20, 15, 8, 10, 8,
                                fill="#1769E8", outline="#1769E8")
        self.ocr_footer_var = tk.StringVar(value="이미지 선택 즉시 OCR이 자동으로 실행됩니다.")
        ttk.Label(ocr_footer, textvariable=self.ocr_footer_var,
                  style="PanelNote.TLabel").grid(row=0, column=1, sticky="w")
        self.ocr_elapsed_var = tk.StringVar(value="처리 시간 —")
        ttk.Label(ocr_footer, textvariable=self.ocr_elapsed_var,
                  style="PanelNote.TLabel").grid(row=0, column=2, sticky="e", padx=(8, 0))

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

        documents_shell, documents = self._make_v2_rounded_panel(work)
        documents_shell.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        documents.columnconfigure(0, weight=1)
        documents.rowconfigure(4, weight=1)
        self.pdf_status_var = tk.StringVar(value="변환 대기")
        pdf_heading = ttk.Frame(documents, style="PanelContent.TFrame")
        pdf_heading.grid(row=0, column=0, columnspan=2, rowspan=2,
                         sticky="ew", pady=(0, 7))
        pdf_heading.columnconfigure(1, weight=1)
        pdf_icon = self._make_v2_icon_tile(pdf_heading, "document", size=40)
        pdf_icon.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10))
        ttk.Label(pdf_heading, text="서류 PDF → JPG 변환", style="PanelTitle.TLabel").grid(
            row=0, column=1, sticky="sw"
        )
        ttk.Label(
            pdf_heading,
            text="PDF를 추가하면 페이지 이미지로 변환합니다. 여러 파일을 함께 넣을 수 있습니다.",
            style="PanelMuted.TLabel",
        ).grid(row=1, column=1, sticky="nw", pady=(1, 0))
        self.pdf_status_badge = RoundedStatusBadge(
            pdf_heading, textvariable=self.pdf_status_var, bg="#EEF3FA", fg="#607696",
            padx=10, pady=5,
        )
        self.pdf_status_badge.grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 0))
        pdf_drop_shell, pdf_drop = self._make_v2_drop_zone(documents)
        pdf_drop_shell.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        pdf_drop.columnconfigure(1, weight=1)
        self.pdf_button = RoundedActionButton(
            pdf_drop, text="PDF 추가", command=self.add_pdf_documents,
            style="Primary.TButton", icon="folder",
        )
        self.pdf_button.grid(row=0, column=0, sticky="ns", padx=(0, 9))
        ttk.Label(
            pdf_drop, text="PDF를 여기에 드래그 앤 드롭\n(PDF 파일 여러 개 선택 가능)",
            anchor="center", justify="center", style="DropZoneMuted.TLabel",
        ).grid(row=0, column=1, sticky="ew")
        document_list_header = ttk.Frame(documents, style="PanelContent.TFrame")
        document_list_header.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        document_list_header.columnconfigure(0, weight=1)
        self.document_count_var = tk.StringVar(value="변환 목록 (0건)")
        ttk.Label(
            document_list_header, textvariable=self.document_count_var,
            style="PanelFieldName.TLabel",
        ).grid(row=0, column=0, sticky="w")
        RoundedActionButton(
            document_list_header, text="목록 비우기", command=self._clear_pdf_list,
            style="Secondary.TButton",
        ).grid(row=0, column=1, sticky="e")
        pdf_list_shell, pdf_list_panel = self._make_v2_rounded_panel(
            documents, radius=9, outside_color="#FFFFFF",
        )
        pdf_list_shell.grid(row=4, column=0, sticky="nsew")
        pdf_list_panel.columnconfigure(0, weight=1)
        pdf_list_panel.rowconfigure(0, weight=1)
        self.document_canvas = tk.Canvas(
            pdf_list_panel, background="#FFFFFF", highlightthickness=0,
        )
        self.document_canvas.grid(row=0, column=0, sticky="nsew")
        document_vscroll = ttk.Scrollbar(
            pdf_list_panel, orient="vertical", command=self.document_canvas.yview,
        )
        document_vscroll.grid(row=0, column=1, sticky="ns")

        def update_document_scroll(first: str, last: str) -> None:
            document_vscroll.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                document_vscroll.grid_remove()
            else:
                document_vscroll.grid()

        self.document_canvas.configure(yscrollcommand=update_document_scroll)
        self.document_rows = tk.Frame(self.document_canvas, bg="#FFFFFF")
        self.document_rows.columnconfigure(0, weight=1)
        self.document_rows_window = self.document_canvas.create_window(
            (0, 0), window=self.document_rows, anchor="nw",
        )
        self._pdf_empty_state_window = None
        self._pdf_empty_state_widget = None
        self.document_rows.bind(
            "<Configure>",
            lambda _event: self.document_canvas.configure(
                scrollregion=self.document_canvas.bbox("all")
            ),
        )
        self.document_canvas.bind(
            "<Configure>",
            lambda event: self.document_canvas.itemconfigure(
                self.document_rows_window, width=event.width
            ),
        )
        self.document_canvas.bind(
            "<Configure>", self._position_pdf_empty_state, add="+",
        )
        pdf_footer = tk.Frame(documents, bg="#EAF3FF", padx=10, pady=7)
        pdf_footer.grid(row=5, column=0, sticky="ew", pady=(7, 0))
        pdf_footer.columnconfigure(1, weight=1)
        pdf_lightning = tk.Canvas(pdf_footer, width=17, height=21, bg="#EAF3FF",
                                  highlightthickness=0)
        pdf_lightning.grid(row=0, column=0, sticky="w", padx=(0, 7))
        pdf_lightning.create_polygon(10, 1, 3, 12, 8, 12, 6, 20, 15, 8, 10, 8,
                                     fill="#1769E8", outline="#1769E8")
        self.pdf_footer_var = tk.StringVar(value="PDF 추가 즉시 변환을 시작합니다.")
        ttk.Label(pdf_footer, textvariable=self.pdf_footer_var,
                  style="PanelNote.TLabel").grid(row=0, column=1, sticky="w")
        self.pdf_elapsed_var = tk.StringVar(value="총 처리 시간 —")
        ttk.Label(pdf_footer, textvariable=self.pdf_elapsed_var,
                  style="PanelNote.TLabel").grid(row=0, column=2, sticky="e", padx=(8, 0))

        receiver_shell, receiver_board = self._make_v2_rounded_panel(shell, radius=10)
        receiver_shell.grid(row=2, column=0, sticky="nsew", pady=(7, 0))
        receiver_board.columnconfigure(0, weight=1)
        receiver_board.rowconfigure(1, weight=1)
        receiver_heading = ttk.Frame(receiver_board, style="PanelContent.TFrame")
        receiver_heading.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        receiver_heading.columnconfigure(3, weight=1)
        self.station_count_var = tk.StringVar(value="(0개)")
        receiver_icon = self._make_v2_icon_tile(
            receiver_heading, "monitor", size=26, background="#EAF2FF",
            foreground="#1769E8",
        )
        receiver_icon.grid(row=0, column=0, sticky="w", padx=(2, 7))
        ttk.Label(
            receiver_heading,
            text="전송 대상 자리",
            style="PanelFieldName.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(0, 5))
        ttk.Label(
            receiver_heading, textvariable=self.station_count_var, style="PanelMuted.TLabel",
        ).grid(row=0, column=2, sticky="w")
        ttk.Label(
            receiver_heading,
            text="선택한 자리는 파란 테두리로 표시됩니다",
            style="PanelMuted.TLabel",
        ).grid(row=0, column=4, sticky="e")
        self.station_settings_button = RoundedActionButton(
            receiver_heading, text="접점 설정", command=self._open_store_manager,
            style="Secondary.TButton", width=8,
        )
        self.station_settings_button.grid(
            row=0, column=5, sticky="e", padx=(8, 0),
        )
        self.relay_settings_button = RoundedActionButton(
            receiver_heading, text="Relay 설정", command=self._open_relay_settings,
            style="Secondary.TButton", width=9,
        )
        self.relay_settings_button.grid(
            row=0, column=6, sticky="e", padx=(5, 0),
        )
        self.receiver_cards_frame = ttk.Frame(receiver_board, style="PanelContent.TFrame")
        self.receiver_cards_frame.grid(row=1, column=0, sticky="nsew")

        footer_shell, footer = self._make_v2_rounded_panel(
            shell, radius=12, fill_color="#F3F7FC", content_style="FooterContent.TFrame",
        )
        footer_shell.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        footer.columnconfigure(6, weight=1)
        footer.rowconfigure(0, weight=1)
        self.package_summary = tk.StringVar(value="신분증 없음 · PDF 0건")
        self.package_id_var = tk.StringVar(value="신분증 없음")
        self.package_pdf_var = tk.StringVar(value="PDF 없음")
        self.send_target_var = tk.StringVar(value="전송 대상을 선택하세요")
        footer_icon = self._make_v2_icon_tile(
            footer, "document", size=32, background="#EAF2FF",
            foreground="#1769E8",
        )
        footer_icon.grid(row=0, column=0, sticky="w", padx=(2, 7))
        tk.Label(
            footer, text="전송할 내용", bg="#F3F7FC", fg="#173B70",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(0, 10))
        id_summary_shell, self.id_summary_card = self._make_v2_rounded_panel(
            footer, radius=9, outside_color="#F3F7FC",
        )
        id_summary_shell.grid(row=0, column=2, sticky="ew", padx=(0, 7))
        id_tile = self._make_v2_icon_tile(
            self.id_summary_card, "identity", size=38, background="#EAF2FF",
            foreground="#1769E8",
        )
        id_tile.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 9))
        tk.Label(self.id_summary_card, text="신분증", bg="#FFFFFF", fg="#7084A4",
                 font=("Segoe UI", 8, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(self.id_summary_card, textvariable=self.package_id_var, bg="#FFFFFF",
                 fg="#162D55", font=("Segoe UI", 9, "bold")).grid(
                     row=1, column=1, sticky="w", pady=(2, 0)
                 )
        pdf_summary_shell, self.pdf_summary_card = self._make_v2_rounded_panel(
            footer, radius=9, outside_color="#F3F7FC",
        )
        pdf_summary_shell.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        pdf_tile = self._make_v2_icon_tile(
            self.pdf_summary_card, "pdf", size=38, background="#FFF0F0",
            foreground="#D04444",
        )
        pdf_tile.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 9))
        tk.Label(self.pdf_summary_card, text="서류 PDF", bg="#FFFFFF", fg="#7084A4",
                 font=("Segoe UI", 8, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(self.pdf_summary_card, textvariable=self.package_pdf_var, bg="#FFFFFF",
                 fg="#162D55", font=("Segoe UI", 9, "bold")).grid(
                     row=1, column=1, sticky="w", pady=(2, 0)
                 )
        ttk.Separator(footer, orient="vertical").grid(row=0, column=4, sticky="ns", padx=4)
        attach_mode = ttk.Frame(footer, style="App.TFrame")
        attach_mode.grid(row=0, column=5, sticky="w", padx=(8, 10))
        ttk.Label(
            attach_mode, text="첨부 방식  (선택 가능)", style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            attach_mode, text="신분증만 / PDF만 / 함께 전송 가능",
            bg="#E6F0FF", fg="#1769E8", padx=9, pady=4,
            font=("Segoe UI", 8, "bold"),
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(footer, textvariable=self.send_target_var, style="Muted.TLabel").grid(
            row=0, column=6, sticky="e", padx=(0, 8)
        )
        footer_actions = ttk.Frame(footer, style="App.TFrame")
        footer_actions.grid(row=0, column=7, rowspan=2, sticky="e", padx=(0, 2))
        self.save_button = RoundedActionButton(
            footer_actions, text="선택한 자리로 전송", command=self.mark_reviewed,
            style="Primary.TButton", width=30, icon="send",
        )
        self.save_button.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            footer_actions, text="OCR · PDF 변환 후 선택한 자리로 전송합니다.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="n", pady=(3, 0))
        footer_actions.columnconfigure(0, weight=1)
        self.sender_actions_menu = tk.Menu(
            self.root, tearoff=False, background="#FFFFFF", foreground="#162D55",
            activebackground="#E6F0FF", activeforeground="#1456C0",
        )
        self.sender_actions_menu.add_command(
            label="전송 상태 확인", command=self._check_pending_status,
        )
        self.sender_actions_menu.add_separator()
        self.sender_actions_menu.add_command(
            label="전송 취소 · 초기화", command=self._reset_sender_session,
        )
        self.save_button.bind("<Button-3>", self._show_sender_actions_menu)
        self.save_button.bind("<Button-2>", self._show_sender_actions_menu)
        self.save_button.bind("<Shift-F10>", self._show_sender_actions_menu)
        self.status_button = ttk.Button(
            footer_actions, text="상태 확인", command=self._check_pending_status,
            state="disabled", style="Secondary.TButton",
        )
        self.reset_button = ttk.Button(
            footer_actions, text="취소·초기화", command=self._reset_sender_session,
            style="Secondary.TButton",
        )

        self._refresh_result_table()
        self._refresh_stores()
        self.relay_header_var.trace_add("write", self._refresh_relay_header_badge)
        self._refresh_relay_header_badge()
        self.ocr_status.trace_add("write", self._refresh_ocr_status_badge)
        self._refresh_ocr_status_badge()
        self._refresh_document_table()
        self._register_drop_targets(ocr_panel, self._handle_id_drop)
        self._register_drop_targets(id_drop, self._handle_id_drop)
        self._register_drop_targets(preview_column, self._handle_id_drop)
        self._register_drop_targets(documents, self._handle_pdf_drop)
        self._register_drop_targets(pdf_drop, self._handle_pdf_drop)

    def _make_v2_rounded_panel(
        self, parent: tk.Misc, *, radius: int = 12,
        outside_color: str | None = None,
        fill_color: str | None = None,
        content_style: str = "PanelContent.TFrame",
    ) -> tuple[tk.Frame, ttk.Frame]:
        colors = self._ui_colors
        outside = outside_color or colors["canvas"]
        fill = fill_color or colors["white"]
        shell = tk.Frame(parent, background=outside, borderwidth=0)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            shell, background=outside, highlightthickness=0, borderwidth=0,
        )
        canvas.place(x=0, y=0, relwidth=1, relheight=1)
        content = ttk.Frame(shell, style=content_style)
        content.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        def draw_border(event) -> None:
            width = max(1, event.width - 1)
            height = max(1, event.height - 1)
            corner = min(radius, width // 2, height // 2)
            canvas.delete("rounded-panel")
            points = (
                corner, 0, width - corner, 0, width, 0, width, corner,
                width, height - corner, width, height, width - corner, height,
                corner, height, 0, height, 0, height - corner, 0, corner, 0, 0,
            )
            canvas.create_polygon(
                *points, smooth=True, splinesteps=16,
                fill=fill, outline=colors["border"], width=1,
                tags="rounded-panel",
            )
            canvas.tag_lower("rounded-panel")

        shell.bind("<Configure>", draw_border)
        return shell, content

    @staticmethod
    def _make_v2_icon_tile(
        parent: tk.Misc, icon: str, *, size: int = 40,
        background: str = "#2374EA", foreground: str = "#FFFFFF",
    ) -> tk.Canvas:
        tile = tk.Canvas(
            parent, width=size, height=size, background="#FFFFFF",
            highlightthickness=0, borderwidth=0,
        )
        radius = 8
        points = (
            radius, 0, size - radius, 0, size, 0, size, radius,
            size, size - radius, size, size, size - radius, size,
            radius, size, 0, size, 0, size - radius, 0, radius, 0, 0,
        )
        tile.create_polygon(
            *points, smooth=True, splinesteps=10, fill=background, outline=background,
        )
        icon_size = size * 0.62
        _draw_ui_icon(
            tile, icon, (size - icon_size) / 2, (size - icon_size) / 2,
            icon_size, foreground,
        )
        return tile

    def _make_v2_drop_zone(self, parent: tk.Misc) -> tuple[tk.Frame, ttk.Frame]:
        shell = tk.Frame(parent, background="#FFFFFF", borderwidth=0)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        canvas = tk.Canvas(
            shell, background="#FFFFFF", highlightthickness=0, borderwidth=0,
        )
        canvas.place(x=0, y=0, relwidth=1, relheight=1)
        content = ttk.Frame(shell, style="DropZone.TFrame")
        content.grid(row=0, column=0, sticky="nsew", padx=7, pady=7)

        def draw_outline(event) -> None:
            width = max(1, event.width - 1)
            height = max(1, event.height - 1)
            corner = min(9, width // 2, height // 2)
            canvas.delete("drop-zone")
            points = (
                corner, 0, width - corner, 0, width, 0, width, corner,
                width, height - corner, width, height, width - corner, height,
                corner, height, 0, height, 0, height - corner, 0, corner, 0, 0,
            )
            canvas.create_polygon(
                *points, smooth=True, splinesteps=12, fill="#F8FBFF",
                outline="#78A6FF", dash=(4, 3), width=1, tags="drop-zone",
            )
            canvas.tag_lower("drop-zone")

        shell.bind("<Configure>", draw_outline)
        return shell, content

    def _register_drop_targets(self, widget: tk.Misc, handler) -> None:
        if not getattr(widget, "_new_work_drop_target", False):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", handler)
            widget._new_work_drop_target = True
        for child in widget.winfo_children():
            self._register_drop_targets(child, handler)

    def _refresh_stores(self) -> None:
        if not hasattr(self, "receiver_cards_frame"):
            return
        selected_id = (
            self.stores[self._selected_store_index].store_id
            if 0 <= self._selected_store_index < len(self.stores) else None
        )
        for child in self.receiver_cards_frame.winfo_children():
            child.destroy()
        self._store_card_frames.clear()
        if selected_id is not None:
            self._selected_store_index = next(
                (index for index, store in enumerate(self.stores)
                 if store.store_id == selected_id), -1,
            )
        if self._selected_store_index < 0 and self.stores:
            self._selected_store_index = 0
        if not self.stores:
            self._selected_store_index = -1
        self.station_count_var.set(f"({len(self.stores)}개 등록)")
        if self.relay_config:
            relay_text = "Relay 설정됨"
        else:
            relay_text = "Relay 설정 필요"
        self.relay_header_var.set(relay_text)
        columns = 9
        for column in range(columns):
            self.receiver_cards_frame.columnconfigure(
                column, weight=1, uniform="store_cards",
            )

        # Keep the 17 desk positions visible. Empty positions are explicitly
        # marked as unregistered and cannot be selected for a transfer.
        slots: list[int | None] = [None] * max(17, len(self.stores))
        unassigned: list[int] = []
        for index, store in enumerate(self.stores):
            station_id = (store.relay_station_id or "").strip().lower()
            if station_id.startswith("desk-"):
                try:
                    slot = int(station_id[5:]) - 1
                except ValueError:
                    slot = -1
                if 0 <= slot < len(slots) and slots[slot] is None:
                    slots[slot] = index
                    continue
            unassigned.append(index)
        next_slot = 0
        for index in unassigned:
            while next_slot < len(slots) and slots[next_slot] is not None:
                next_slot += 1
            if next_slot == len(slots):
                slots.append(None)
            slots[next_slot] = index

        self._store_display_names: dict[str, str] = {}
        for slot, store_index in enumerate(slots):
            row, column = divmod(slot, columns)
            station_name = (
                RECEIVER_STATION_NAMES[slot]
                if slot < len(RECEIVER_STATION_NAMES) else f"자리 {slot + 1}"
            )
            if store_index is None:
                button = RoundedActionButton(
                    self.receiver_cards_frame,
                    text=f"{station_name}\n접점 설정 필요",
                    style="UnregisteredStore.TButton", icon="monitor", state="disabled",
                )
            else:
                store = self.stores[store_index]
                store_station_id = (store.relay_station_id or "").strip().lower()
                try:
                    configured_number = int(store_station_id.removeprefix("desk-")) \
                        if store_station_id.startswith("desk-") else 0
                except ValueError:
                    configured_number = 0
                display_name = (
                    RECEIVER_STATION_NAMES[configured_number - 1]
                    if 1 <= configured_number <= len(RECEIVER_STATION_NAMES)
                    else store.name
                )
                self._store_display_names[store.store_id] = display_name
                detail = "전송 설정 완료" if store.relay_station_id else "접점 설정 필요"
                button = RoundedActionButton(
                    self.receiver_cards_frame,
                    text=f"{display_name}\n{detail}",
                    command=lambda selected=store_index: self._select_store(selected),
                    style=(
                        "SelectedStore.TButton"
                        if store_index == self._selected_store_index else "Store.TButton"
                    ),
                    icon="monitor",
                )
            button.grid(row=row, column=column, sticky="ew", padx=6, pady=6)
            self._store_card_frames.append((store_index, button))
        self._update_selected_store_label()

    def _select_store(self, index: int) -> None:
        if not 0 <= index < len(self.stores):
            return
        self._selected_store_index = index
        for store_index, button in self._store_card_frames:
            if store_index is None:
                continue
            button.configure(
                style="SelectedStore.TButton" if store_index == index else "Store.TButton"
            )
        self._update_selected_store_label()

    def _update_selected_store_label(self) -> None:
        if not hasattr(self, "send_target_var"):
            return
        store = self._selected_store()
        if store is None:
            self.send_target_var.set("전송 대상을 선택하세요")
            self.save_button.configure(text="전송 대상 선택")
        else:
            display_name = getattr(self, "_store_display_names", {}).get(
                store.store_id, store.name,
            )
            self.send_target_var.set(f"전송 대상: {display_name}")
            self.save_button.configure(text=f"{display_name}로 전송")

    def _refresh_ocr_status_badge(self, *_args) -> None:
        badge = getattr(self, "ocr_status_badge", None)
        if badge is None:
            return
        value = self.ocr_status.get()
        if "오류" in value or "실패" in value:
            background, foreground = "#FFF0F0", "#B42332"
        elif "대기" in value:
            background, foreground = "#EEF3FA", "#607696"
        elif "중" in value or "다시 시도" in value:
            background, foreground = "#EAF2FF", "#2056A8"
        else:
            background, foreground = "#E8F7EE", "#16834A"
        badge.configure(bg=background, fg=foreground)

    def _refresh_relay_header_badge(self, *_args) -> None:
        label = getattr(self, "relay_header_label", None)
        if label is None:
            return
        value = self.relay_header_var.get()
        label.configure(fg="#F3B54A" if "필요" in value else "#9AE6B4")

    def _show_sender_actions_menu(self, event) -> str:
        menu = getattr(self, "sender_actions_menu", None)
        if menu is None:
            return "break"
        menu.entryconfigure(0, state=self.status_button.cget("state"))
        menu.entryconfigure(2, state=self.reset_button.cget("state"))
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()
        return "break"

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
            self._refresh_stores()
            messagebox.showinfo("Relay 설정", "중앙 Relay 설정을 저장했습니다.", parent=self.root)

        ttk.Button(dialog, text="저장", command=save).grid(
            row=3, column=1, sticky="e", padx=10, pady=10
        )
        dialog.columnconfigure(1, weight=1)

    def _stores_changed(self, stores: list[Store]) -> None:
        selected = self._selected_store()
        self.stores = stores
        self._selected_store_index = next(
            (index for index, store in enumerate(stores)
             if selected is not None and store.store_id == selected.store_id), -1,
        )
        self._refresh_stores()

    def _selected_store(self) -> Store | None:
        index = self._selected_store_index
        return self.stores[index] if 0 <= index < len(self.stores) else None

    def _set_transfer_controls(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        widgets = [self.ocr_button, self.pdf_button, *self.portal_buttons]
        for name in ("station_settings_button", "relay_settings_button"):
            if hasattr(self, name):
                widgets.append(getattr(self, name))
        if hasattr(self, "ocr_run_button"):
            widgets.append(self.ocr_run_button)
        if hasattr(self, "crop_reset_button"):
            widgets.append(self.crop_reset_button)
        if hasattr(self, "raw_ocr_button"):
            widgets.append(self.raw_ocr_button)
        widgets.extend(
            button for store_index, button in self._store_card_frames
            if store_index is not None
        )
        for widget in widgets:
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
        if self.version == "v1":
            self.root.configure(bg="#F2F4F7")
            style.configure("App.TFrame", background="#F2F4F7")
            style.configure("Card.TFrame", background="#FFFFFF")
            style.configure("TLabel", background="#F2F4F7", foreground="#1D2939",
                            font=("Segoe UI", 10))
            style.configure("Muted.TLabel", background="#F2F4F7", foreground="#667085",
                            font=("Segoe UI", 9))
            style.configure("Title.TLabel", background="#F2F4F7", foreground="#101828",
                            font=("Segoe UI", 13, "bold"))
            style.configure("Status.TLabel", background="#FFFFFF", foreground="#344054",
                            font=("Segoe UI", 9, "bold"))
            style.configure("TableHead.TLabel", background="#FFFFFF", foreground="#667085",
                            font=("Segoe UI", 9, "bold"))
            style.configure("FieldName.TLabel", background="#FFFFFF", foreground="#344054",
                            font=("Segoe UI", 10, "bold"))
            style.configure("Original.TLabel", background="#FFFFFF", foreground="#475467",
                            font=("Segoe UI", 10))
            style.configure("TLabelframe", background="#FFFFFF", bordercolor="#D8DEE8",
                            relief="solid")
            style.configure("TLabelframe.Label", background="#F2F4F7", foreground="#344054",
                            font=("Segoe UI", 10, "bold"))
            style.configure("TEntry", padding=(4, 2), fieldbackground="#FFFFFF")
            style.configure("Field.TEntry", padding=(4, 2), fieldbackground="#FFFFFF",
                            font=("Segoe UI", 9))
            style.configure("TCombobox", padding=(3, 1), fieldbackground="#FFFFFF")
            style.configure("TButton", padding=(5, 2), font=("Segoe UI", 9))
            style.configure("Primary.TButton", background="#2864DC", foreground="#FFFFFF",
                            padding=(7, 3), font=("Segoe UI", 9, "bold"))
            style.map("Primary.TButton", background=[("active", "#1D4FB5"),
                                                       ("disabled", "#AEBAD0")],
                      foreground=[("disabled", "#F5F7FA")])
            style.configure("Secondary.TButton", background="#EAF0FF", foreground="#214FAF",
                            padding=(7, 3), font=("Segoe UI", 9, "bold"))
            style.map("Secondary.TButton", background=[("active", "#D8E4FF")])
            return
        palette = {
            "navy": "#163B70",
            "blue": "#1769E8",
            "blue_hover": "#0F57C8",
            "blue_soft": "#EDF4FF",
            "canvas": "#F3F7FC",
            "white": "#FFFFFF",
            "border": "#DCE7F4",
            "text": "#162D55",
            "muted": "#7084A4",
            "green": "#16834A",
        }
        self._ui_colors = palette
        self.root.configure(bg=palette["canvas"])
        style.configure("App.TFrame", background=palette["canvas"])
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("PanelContent.TFrame", background=palette["white"])
        style.configure("FooterContent.TFrame", background=palette["canvas"])
        style.configure("DropZone.TFrame", background="#F8FBFF")
        style.configure(
            "DropZoneMuted.TLabel", background="#F8FBFF", foreground=palette["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "PanelTitle.TLabel", background=palette["white"], foreground=palette["navy"],
            font=("Segoe UI", 16, "bold"),
        )
        style.configure(
            "PanelMuted.TLabel", background=palette["white"], foreground=palette["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "PanelStatus.TLabel", background=palette["white"], foreground=palette["text"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "PanelFieldName.TLabel", background=palette["white"], foreground="#344F7A",
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "PanelNote.TLabel", background="#EAF3FF", foreground=palette["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "TLabel", background=palette["canvas"], foreground=palette["text"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Muted.TLabel", background=palette["canvas"], foreground=palette["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Title.TLabel", background=palette["canvas"], foreground=palette["navy"],
            font=("Segoe UI", 15, "bold"),
        )
        style.configure(
            "Status.TLabel", background=palette["white"], foreground=palette["text"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "TableHead.TLabel", background=palette["white"], foreground=palette["muted"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "FieldName.TLabel", background=palette["white"], foreground="#344F7A",
            font=("Segoe UI", 9, "bold"),
        )
        style.configure("Original.TLabel", background=palette["white"], foreground="#475467",
                        font=("Segoe UI", 10))
        style.configure("TLabelframe", background=palette["white"], bordercolor=palette["border"],
                        relief="solid", borderwidth=1)
        style.configure(
            "Panel.TLabelframe", background=palette["white"], bordercolor=palette["border"],
            relief="solid", borderwidth=1, padding=10,
        )
        style.configure(
            "TLabelframe.Label", background=palette["white"], foreground=palette["navy"],
            font=("Segoe UI", 11, "bold"), padding=(2, 0),
        )
        style.configure("TEntry", padding=(8, 6), fieldbackground=palette["white"])
        style.configure(
            "Field.TEntry", padding=(8, 6), fieldbackground=palette["white"],
            foreground=palette["text"], bordercolor="#D8E3F0", lightcolor="#D8E3F0",
            darkcolor="#D8E3F0", insertcolor=palette["blue"], font=("Segoe UI", 10),
        )
        style.map(
            "Field.TEntry", bordercolor=[("focus", palette["blue"])],
            lightcolor=[("focus", palette["blue"])], darkcolor=[("focus", palette["blue"])],
        )
        style.configure(
            "TCombobox", padding=(7, 5), fieldbackground=palette["white"],
            foreground=palette["text"], arrowsize=14,
        )
        style.map(
            "TCombobox", fieldbackground=[("readonly", palette["white"])],
            foreground=[("readonly", palette["text"])],
        )
        style.configure("TButton", padding=(10, 7), font=("Segoe UI", 9, "bold"),
                        background=palette["white"], foreground=palette["text"],
                        bordercolor=palette["border"], lightcolor=palette["border"],
                        darkcolor=palette["border"])
        style.map("TButton", background=[("active", "#F2F6FC"), ("disabled", "#EEF2F7")],
                  foreground=[("disabled", "#A0AEC0")])
        style.configure("Primary.TButton", background=palette["blue"],
                        foreground=palette["white"], padding=(14, 9),
                        font=("Segoe UI", 10, "bold"), bordercolor=palette["blue"],
                        lightcolor=palette["blue"], darkcolor=palette["blue"])
        style.map(
            "Primary.TButton", background=[("active", palette["blue_hover"]),
                                           ("disabled", "#AFC6EA")],
            foreground=[("disabled", "#F7FAFF")],
            bordercolor=[("active", palette["blue_hover"])],
        )
        style.configure("Secondary.TButton", background=palette["blue_soft"],
                        foreground="#2056A8", padding=(10, 7),
                        bordercolor="#D5E4FB", lightcolor="#D5E4FB", darkcolor="#D5E4FB")
        style.map("Secondary.TButton", background=[("active", "#DCEAFF")])
        style.configure("Header.TButton", background="#204B84", foreground="#FFFFFF",
                        padding=(10, 6), bordercolor="#4771A5", lightcolor="#4771A5",
                        darkcolor="#4771A5", font=("Segoe UI", 9, "bold"))
        style.map("Header.TButton", background=[("active", "#2C5D9C")],
                  foreground=[("disabled", "#C9D7E9")])
        style.configure("Store.TButton", anchor="w", padding=(9, 8),
                        font=("Segoe UI", 9), background=palette["white"],
                        foreground=palette["text"], bordercolor=palette["border"],
                        lightcolor=palette["border"], darkcolor=palette["border"])
        style.map("Store.TButton", background=[("active", "#F4F8FE")])
        style.configure("SelectedStore.TButton", anchor="w", padding=(9, 8),
                        font=("Segoe UI", 9, "bold"), background="#EAF2FF",
                        foreground="#1456C0", bordercolor=palette["blue"],
                        lightcolor=palette["blue"], darkcolor=palette["blue"])
        style.map("SelectedStore.TButton", background=[("active", "#DCEAFF")])
        style.configure("Treeview", background=palette["white"], fieldbackground=palette["white"],
                        foreground=palette["text"], rowheight=42, borderwidth=0,
                        font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#F4F8FD", foreground="#5D7397",
                        relief="flat", borderwidth=0, padding=(8, 8),
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#E8F1FF")],
                  foreground=[("selected", palette["navy"])])
        style.configure("Vertical.TScrollbar", background="#E5EDF7", troughcolor="#F6F9FD",
                        bordercolor="#F6F9FD", arrowsize=12)
        style.configure("Success.Horizontal.TProgressbar", troughcolor="#E9F1F8",
                        background="#1EA85A", bordercolor="#E9F1F8",
                        lightcolor="#1EA85A", darkcolor="#1EA85A")
        style.configure("Active.Horizontal.TProgressbar", troughcolor="#E9F1F8",
                        background="#2374EA", bordercolor="#E9F1F8",
                        lightcolor="#2374EA", darkcolor="#2374EA")
        style.configure("Error.Horizontal.TProgressbar", troughcolor="#E9F1F8",
                        background="#D04444", bordercolor="#E9F1F8",
                        lightcolor="#D04444", darkcolor="#D04444")

    def _review_specs(self) -> tuple[tuple[str, str], ...]:
        code = ID_TYPE_LABEL_TO_CODE.get(self.id_type_choice.get())
        group = FOCUS_GROUP_BY_CODE.get(code or "", "resident")
        fields = list(FOCUS_FIELDS[group])
        for field, label in (("isuePlc", "발급기관"), ("addr", "주소")):
            if self._value_vars[field].get().strip() and field not in {key for key, _ in fields}:
                fields.append((field, label))
        return tuple(fields)

    def _show_raw_ocr(self) -> None:
        lines = self.draft.result.lines if self.draft is not None else ()
        if not lines:
            messagebox.showinfo("OCR 원문", "먼저 신분증 OCR을 완료하세요.", parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("OCR 원문 전체 결과")
        dialog.geometry("680x520")
        dialog.transient(self.root)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(
            dialog, text="모델이 읽은 모든 문자열입니다. 아래 구조화된 항목을 수정해 전송하세요.",
            style="Muted.TLabel", wraplength=640,
        ).grid(row=0, column=0, sticky="w", padx=10, pady=8)
        text = tk.Text(dialog, wrap="word", font=("Segoe UI", 10), height=20)
        text.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 10))
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=text.yview)
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 10), pady=(0, 10))
        text.configure(yscrollcommand=scroll.set)
        for line in lines:
            confidence = (
                f"  ({line.confidence:.1%})" if line.confidence is not None else ""
            )
            text.insert("end", f"{line.text}{confidence}\n")
        text.configure(state="disabled")

    def _current_values(self) -> dict[str, str]:
        values = {field: value.get() for field, value in self._value_vars.items()}
        if (
            self._id_crop_preview_bounds is not None
            and (not self._id_crop_preview_is_fallback or self._id_crop_manually_adjusted)
        ):
            values["_idCropBounds"] = ",".join(
                str(value) for value in self._id_crop_preview_bounds
            )
        return values

    def _write_selected_portal_report(self, result: OcrResult) -> Path | None:
        values = self._current_values()
        if self.portal_var.get() == "KT":
            return None
        if self._id_type_detection.code in {
            "1001", "1101", "1102", "1201", "1301", "1302", "1303",
        }:
            resident_number = "".join(
                char for char in values.get("ctzCorpNum", "") if char.isdecimal()
            )
            if resident_number and not is_valid_resident_number(resident_number):
                # Keep an unverified OCR candidate visible in the UI, but do
                # not publish it to the legacy SK report before correction.
                values["ctzCorpNum"] = ""
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
        if self.version == "v2":
            self.id_type_choice = RoundedComboBox(
                self.result_table, state="readonly", width=220,
                textvariable=self._id_type_var,
                values=("유형 선택…", *ID_TYPE_LABEL_TO_CODE.keys()),
            )
        else:
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
            if self.version == "v2":
                entry = RoundedEntry(
                    self.result_table, textvariable=self._value_vars[field],
                )
            else:
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
        if hasattr(self, "result_canvas"):
            self.root.after_idle(
                lambda: self.result_canvas.configure(
                    scrollregion=self.result_canvas.bbox("all")
                )
            )
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
        self._display_preview(preview)
        if self._scan_active:
            self._render_scan_frame()

    def _draw_crop_overlay(self) -> None:
        """Update only the vector crop outline; do not resample the ID during drag."""
        if not isinstance(self.image_label, tk.Canvas):
            return
        canvas = self.image_label
        canvas.delete("crop-overlay")
        if self._source_image is None or self._preview_base is None:
            return
        bounds = self._id_crop_preview_bounds
        if bounds is None:
            return
        pw, ph = self._preview_base.size
        sw, sh = self._source_image.size
        if min(pw, ph, sw, sh) < 1:
            return
        offset_x = (canvas.winfo_width() - pw) / 2
        offset_y = (canvas.winfo_height() - ph) / 2
        sx, sy = pw / sw, ph / sh
        left, top, right, bottom = bounds
        x0, y0 = offset_x + left * sx, offset_y + top * sy
        x1, y1 = offset_x + right * sx, offset_y + bottom * sy
        canvas.create_rectangle(
            x0, y0, x1, y1, outline="#0878FF", width=2, tags="crop-overlay",
        )
        label = "idCrop" if self.portal_var.get() == "SK" else "CR.jpg"
        label_y = max(offset_y, y0 - 19)
        text_id = canvas.create_text(
            x0 + 5, label_y + 2, text=label, anchor="nw", fill="white",
            font=("Segoe UI", 9, "bold"), tags="crop-overlay",
        )
        box = canvas.bbox(text_id)
        if box:
            background = canvas.create_rectangle(
                box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2,
                fill="#075FD4", outline="#075FD4", tags="crop-overlay",
            )
            canvas.tag_lower(background, text_id)

    def _draw_empty_preview(self, _event=None) -> None:
        if self._source_image is not None or not isinstance(self.image_label, tk.Canvas):
            return
        canvas = self.image_label
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 2 or height < 2:
            return
        icon_y = height / 2 - 28
        canvas.coords("empty-state", width / 2, icon_y)
        items = canvas.find_withtag("empty-state")
        if len(items) > 1:
            canvas.coords(items[1], width / 2, icon_y + 44)

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
        code = self._id_type_detection.code
        if code is None or (
            self.portal_var.get() == "SK" and code not in SK_ID_CROP_CODES
        ):
            return
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
                self._id_type_detection.code, self._source_image.size
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
            self._draw_crop_overlay()
            self._update_id_crop_value()
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
        self._draw_crop_overlay()

    def _crop_pointer_up(self, _event=None) -> None:
        if self._id_crop_drag is None:
            return
        self._id_crop_drag = None
        self._id_crop_manually_adjusted = True
        self.image_label.configure(cursor="")
        if self.crop_reset_button.winfo_exists():
            self.crop_reset_button.configure(state="normal")
        self._update_id_crop_value()
        self.review_status.set("크롭 영역 직접 조정됨 · 저장 시 이 영역을 사용합니다.")

    def _reset_crop_bounds(self) -> None:
        self._id_crop_manually_adjusted = False
        self._refresh_id_crop_preview_bounds(reset=True)
        self.review_status.set("자동 크롭 영역으로 복원했습니다.")
        self._resize_source_preview()

    def _display_preview(self, preview: Image.Image) -> None:
        self._preview = ImageTk.PhotoImage(preview, master=self.root)
        if isinstance(self.image_label, tk.Canvas):
            canvas = self.image_label
            canvas.delete("preview-image", "empty-state")
            x = max(0, (canvas.winfo_width() - preview.width) / 2)
            y = max(0, (canvas.winfo_height() - preview.height) / 2)
            self._preview_item = canvas.create_image(
                x, y, anchor="nw", image=self._preview, tags="preview-image",
            )
            self._draw_crop_overlay()
        else:
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
            title="신분증 이미지 선택",
            filetypes=(("신분증 이미지", "*.jpg *.jpeg *.png"), ("모든 파일", "*.*")),
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
        if hasattr(self, "ocr_footer_var"):
            self.ocr_footer_var.set("이미지 선택 즉시 OCR을 자동으로 시작합니다.")
            self.ocr_elapsed_var.set("처리 시간 —")
        if hasattr(self, "id_image_name"):
            self.id_image_name.set(path.name)
        if hasattr(self, "raw_ocr_button"):
            self.raw_ocr_button.configure(state="disabled")
        self._id_crop_preview_bounds = None
        self._id_crop_preview_is_fallback = False
        self._id_crop_manually_adjusted = False
        self._id_crop_drag = None
        self.crop_reset_button.configure(state="disabled")
        self._update_id_crop_value()
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
        if len(paths) != 1 or paths[0].suffix.casefold() not in {".jpg", ".jpeg", ".png"}:
            messagebox.showinfo(
                "신분증 이미지",
                "신분증 OCR 영역에는 JPG 또는 PNG 이미지 한 장을 놓아 주세요.",
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
            title="신분증 이미지 선택",
            filetypes=(("신분증 이미지", "*.jpg *.jpeg *.png"), ("모든 파일", "*.*")),
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
        if self._pending_auto_send is not None:
            messagebox.showinfo(
                "자동 전송 예약 중",
                "예약된 OCR/PDF 준비와 자동 전송이 끝난 뒤 새 신분증을 불러오세요.",
                parent=self.root,
            )
            return
        if self._transfer_cancel_event is not None or self._pending_confirmation is not None:
            messagebox.showinfo(
                "전송 처리 중",
                "Receiver 수신 확인이 끝난 뒤 새 신분증을 추가할 수 있습니다.",
                parent=self.root,
            )
            return
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
            state="normal", text="선택 PC로 전송" if self.version == "v2" else "저장하기"
        )
        if hasattr(self, "pdf_button"):
            self.pdf_button.configure(state="normal")
        if not reuse_job:
            self._document_jobs.clear()
            self._refresh_document_table()
        self._refresh_package_summary()
        # Begin OCR immediately; adding PDFs remains available while it runs.
        self.run_ocr()

    def _queue_pdf_paths(self, paths: list[Path]) -> None:
        if self._pending_auto_send is not None:
            messagebox.showinfo(
                "자동 전송 예약 중",
                "전송 예약 이후에는 PDF를 추가할 수 없습니다. 기존 예약을 완료하거나 초기화하세요.",
                parent=self.root,
            )
            return
        if self._transfer_cancel_event is not None or self._pending_confirmation is not None:
            messagebox.showinfo(
                "전송 처리 중",
                "Receiver 수신 확인이 끝난 뒤 새 PDF를 추가할 수 있습니다.",
                parent=self.root,
            )
            return
        if self._job_sent:
            messagebox.showinfo(
                "새 작업 필요", "전송을 마친 작업은 초기화 중입니다. 잠시 후 PDF를 추가하세요.",
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
                "pages": "변환 중…",
                "page_count": 0,
                "started_at": perf_counter(),
                "elapsed_seconds": 0.0,
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
        item["page_count"] = len(pages)
        item["elapsed_seconds"] = perf_counter() - float(item.get("started_at", perf_counter()))
        item["pages"] = f"{len(pages)}페이지 · 100%"
        self._refresh_document_table()
        self._try_start_pending_auto_send()

    def _pdf_conversion_failed(self, key: str, error: str) -> None:
        item = next((row for row in self._document_jobs if row["key"] == key), None)
        if item is None:
            return
        item["status"] = "변환 실패"
        item["elapsed_seconds"] = perf_counter() - float(item.get("started_at", perf_counter()))
        item["pages"] = error
        self._refresh_document_table()
        if self._pending_auto_send is not None:
            self._cancel_pending_auto_send(f"PDF 변환 실패 · {item['source'].name}: {error}")

    def _refresh_document_table(self) -> None:
        if not hasattr(self, "document_rows"):
            return
        for row in self.document_rows.winfo_children():
            progress = getattr(row, "_conversion_progress", None)
            if progress is not None:
                progress.stop()
            row.destroy()
        for index, item in enumerate(self._document_jobs):
            source = item["source"]
            status = item["status"]
            row_shell, row = self._make_v2_rounded_panel(
                self.document_rows, radius=9, outside_color="#FFFFFF",
            )
            row_shell.grid(row=index, column=0, sticky="ew", padx=7, pady=(7, 0))
            row.columnconfigure(1, weight=1)
            pdf_icon = self._make_v2_icon_tile(
                row, "pdf", size=38, background="#FFF0F0", foreground="#D04444",
            )
            pdf_icon.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 9))
            tk.Label(
                row, text=source.name, bg="#FFFFFF", fg="#18315D", anchor="w",
                font=("Segoe UI", 10, "bold"),
            ).grid(row=0, column=1, sticky="ew")
            try:
                size_mb = source.stat().st_size / (1024 * 1024)
                size_text = f"{size_mb:.1f} MB"
            except OSError:
                size_text = "크기 확인 불가"
            page_count = int(item.get("page_count", 0))
            if status == "변환 실패":
                detail = str(item.get("pages", "변환 오류"))[:90]
            else:
                detail = f"{page_count}페이지  |  {size_text}" if page_count else size_text
            tk.Label(
                row, text=detail, bg="#FFFFFF", fg="#7185A4", anchor="w",
                font=("Segoe UI", 8),
            ).grid(row=1, column=1, sticky="ew", pady=(2, 0))

            bar = RoundedProgressBar(row, length=138, height=12)
            bar.grid(row=0, column=2, rowspan=2, sticky="ew", padx=(12, 7))
            if status == "변환 완료":
                bar.configure(value=100, mode="determinate", style="Success.Horizontal.TProgressbar")
            elif status == "변환 중":
                bar.configure(mode="indeterminate", style="Active.Horizontal.TProgressbar")
                bar.start(14)
                row_shell._conversion_progress = bar
            else:
                bar.configure(value=0, mode="determinate", style="Error.Horizontal.TProgressbar")
            progress_text = "100%" if status == "변환 완료" else ("…" if status == "변환 중" else "!")
            tk.Label(
                row, text=progress_text, bg="#FFFFFF", fg="#536B90",
                font=("Segoe UI", 9, "bold"), width=4,
            ).grid(row=0, column=3, rowspan=2, sticky="e", padx=(0, 8))

            if status == "변환 완료":
                badge_bg, badge_fg = "#E4F8EC", "#16834A"
            elif status == "변환 중":
                badge_bg, badge_fg = "#EAF2FF", "#2056A8"
            else:
                badge_bg, badge_fg = "#FFF0F0", "#B42332"
            status_var = tk.StringVar(value=status)
            RoundedStatusBadge(
                row, textvariable=status_var, bg=badge_bg, fg=badge_fg,
                padx=8, pady=5, font=("Segoe UI", 8, "bold"),
            ).grid(row=0, column=4, rowspan=2, sticky="ew", padx=(0, 7))
            tk.Button(
                row, text="×", command=lambda key=item["key"]: self._remove_pdf_item(key),
                relief="flat", borderwidth=0, bg="#FFFFFF", fg="#7084A4",
                activebackground="#F3F7FC", activeforeground="#344F7A",
                font=("Segoe UI", 13), cursor="hand2", padx=3,
            ).grid(row=0, column=5, rowspan=2, sticky="ns")
            row.columnconfigure(2, weight=1)
        self._update_pdf_empty_state()
        if hasattr(self, "document_count_var"):
            self.document_count_var.set(f"변환 목록 ({len(self._document_jobs)}건)")
        if hasattr(self, "pdf_status_var"):
            total = len(self._document_jobs)
            done = sum(item["status"] == "변환 완료" for item in self._document_jobs)
            failed = sum(item["status"] == "변환 실패" for item in self._document_jobs)
            converting = sum(item["status"] == "변환 중" for item in self._document_jobs)
            if not total:
                label, background, foreground = "변환 대기", "#EEF3FA", "#607696"
            elif converting:
                label = f"변환 중 · {done}/{total}"
                background, foreground = "#EAF2FF", "#2056A8"
            elif failed:
                label = f"변환 실패 · {failed}건"
                background, foreground = "#FFF0F0", "#B42332"
            else:
                label = f"변환 완료 · {done}/{total}"
                background, foreground = "#E8F7EE", "#16834A"
            self.pdf_status_var.set(label)
            self.pdf_status_badge.configure(bg=background, fg=foreground)
            if not total:
                self.pdf_footer_var.set("PDF 추가 즉시 변환이 자동으로 시작됩니다.")
                self.pdf_elapsed_var.set("총 처리 시간 —")
            elif converting:
                self.pdf_footer_var.set("PDF 페이지 이미지를 변환하고 있습니다…")
                self.pdf_elapsed_var.set(f"변환 중 ({done}/{total})")
            else:
                self.pdf_footer_var.set("PDF 추가 즉시 변환이 완료되었습니다.")
                elapsed = max(
                    float(item.get("elapsed_seconds", 0.0)) for item in self._document_jobs
                )
                self.pdf_elapsed_var.set(f"총 처리 시간 {elapsed:.1f}초 ({done}/{total}건)")
        self._refresh_package_summary()

    def _update_pdf_empty_state(self) -> None:
        if not hasattr(self, "document_canvas"):
            return
        if self._document_jobs:
            if self._pdf_empty_state_window is not None:
                self.document_canvas.delete(self._pdf_empty_state_window)
                self._pdf_empty_state_window = None
            if self._pdf_empty_state_widget is not None:
                self._pdf_empty_state_widget.destroy()
                self._pdf_empty_state_widget = None
            return
        if self._pdf_empty_state_window is not None:
            return
        empty = tk.Frame(self.document_canvas, background="#FFFFFF")
        icon = self._make_v2_icon_tile(
            empty, "document", size=48, background="#EAF2FF",
        )
        empty.pack_propagate(False)
        empty.configure(width=340, height=124)
        icon.pack(pady=(3, 8))
        tk.Label(
            empty, text="추가한 PDF가 없습니다", bg="#FFFFFF", fg="#18315D",
            font=("Segoe UI", 11, "bold"),
        ).pack()
        tk.Label(
            empty, text="PDF를 선택하거나 위쪽 영역에 끌어 놓으세요",
            bg="#FFFFFF", fg="#8293AE", font=("Segoe UI", 9),
        ).pack(pady=(4, 0))
        self._pdf_empty_state_widget = empty
        self._pdf_empty_state_window = self.document_canvas.create_window(
            max(1, self.document_canvas.winfo_width() / 2),
            max(1, self.document_canvas.winfo_height() / 2),
            window=empty, anchor="center", tags="empty-state",
        )
        self.document_canvas.tag_raise(self._pdf_empty_state_window)
        self._register_drop_targets(empty, self._handle_pdf_drop)

    def _position_pdf_empty_state(self, event) -> None:
        window = getattr(self, "_pdf_empty_state_window", None)
        if window is not None:
            self.document_canvas.coords(window, event.width / 2, event.height / 2)

    def _remove_pdf_item(self, key: str) -> None:
        if self._pending_auto_send is not None or self._transfer_cancel_event is not None:
            messagebox.showinfo(
                "PDF 처리 중", "자동 전송 또는 전송 중에는 PDF를 제거할 수 없습니다.",
                parent=self.root,
            )
            return
        item = next((row for row in self._document_jobs if row["key"] == key), None)
        if item is None:
            return
        if item["status"] == "변환 중":
            messagebox.showinfo(
                "PDF 변환 중", "변환이 끝난 뒤 이 PDF를 목록에서 제거할 수 있습니다.",
                parent=self.root,
            )
            return
        shutil.rmtree(Path(item["output"]), ignore_errors=True)
        self._document_jobs.remove(item)
        if self.draft is None and not self._document_jobs and self._document_job_id:
            shutil.rmtree(default_job_directory() / self._document_job_id, ignore_errors=True)
            self._document_job_id = None
        self._refresh_document_table()

    def _clear_pdf_list(self) -> None:
        if not self._document_jobs:
            return
        if self._pending_auto_send is not None or self._transfer_cancel_event is not None:
            messagebox.showinfo(
                "PDF 처리 중", "전송 예약 또는 전송이 끝난 뒤 목록을 비워 주세요.",
                parent=self.root,
            )
            return
        if any(item["status"] == "변환 중" for item in self._document_jobs):
            messagebox.showinfo(
                "PDF 변환 중", "변환 중인 PDF가 끝난 뒤 목록을 비워 주세요.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "PDF 목록 비우기",
            "변환 목록과 이 프로그램이 만든 PDF 페이지 이미지를 삭제할까요?\n"
            "원본 PDF 파일은 삭제하지 않습니다.",
            parent=self.root,
        ):
            return
        for item in self._document_jobs:
            shutil.rmtree(Path(item["output"]), ignore_errors=True)
        self._document_jobs.clear()
        if self.draft is None and self._document_job_id:
            shutil.rmtree(default_job_directory() / self._document_job_id, ignore_errors=True)
            self._document_job_id = None
        self._refresh_document_table()

    def _refresh_package_summary(self) -> None:
        if not hasattr(self, "package_summary"):
            return
        identity = "신분증 1장" if self.draft is not None else "신분증 없음"
        pdf_total = len(self._document_jobs)
        converting = sum(item["status"] == "변환 중" for item in self._document_jobs)
        failed = sum(item["status"] == "변환 실패" for item in self._document_jobs)
        pages = sum(int(item.get("page_count", 0)) for item in self._document_jobs)
        pdf_text = f"PDF {pdf_total}건"
        if pages:
            pdf_text += f" · {pages}페이지"
        if converting:
            pdf_text += f" · 변환 중 {converting}"
        if failed:
            pdf_text += f" · 실패 {failed}"
        self.package_summary.set(f"현재 묶음  |  {identity}  ·  {pdf_text}")
        if hasattr(self, "package_id_var"):
            self.package_id_var.set(
                self.id_image_name.get() if self.draft is not None else "신분증 없음"
            )
            if pdf_total:
                detail = f"PDF {pdf_total}건"
                if pages:
                    detail += f" · {pages}페이지"
                if converting:
                    detail += f" · 변환 중 {converting}"
                if failed:
                    detail += f" · 실패 {failed}"
                self.package_pdf_var.set(detail)
            else:
                self.package_pdf_var.set("PDF 없음")
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
        if hasattr(self, "ocr_run_button"):
            self.ocr_run_button.configure(state="disabled")
        self._start_scan_animation(image_path)
        if self._ocr_models_ready:
            self.ocr_status.set("OCR 인식 중")
            self.review_status.set("OCR 실행 중…")
        else:
            self.ocr_status.set("모델 다시 준비 중")
            self.review_status.set("OCR 모델 준비를 다시 시도하는 중…")
        if hasattr(self, "ocr_footer_var"):
            self.ocr_footer_var.set("신분증 이미지를 분석하고 있습니다…")
            self.ocr_elapsed_var.set("처리 중")
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
                _issuer_linked_dates,
            )

            full_card_issue_date = full_card_fields.get("isueDt", "")
            source_date_is_linked_to_issuer = (
                bool(full_card_issue_date)
                and full_card_issue_date in _issuer_linked_dates(result.lines)
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
        if hasattr(self, "ocr_run_button"):
            self.ocr_run_button.configure(state="normal")
        if isinstance(error, OcrUnavailableError):
            self.ocr_status.set("OCR 모델 오류")
            if hasattr(self, "ocr_footer_var"):
                self.ocr_footer_var.set("OCR 모델을 불러오지 못했습니다.")
                self.ocr_elapsed_var.set("처리 실패")
            messagebox.showerror("PaddleOCR 실행 실패", str(error))
            if self._pending_auto_send is not None:
                self._cancel_pending_auto_send("OCR에 실패해 자동 전송 예약을 취소했습니다.")
            return
        self.ocr_status.set("OCR 오류")
        if hasattr(self, "ocr_footer_var"):
            self.ocr_footer_var.set("OCR 처리 중 오류가 발생했습니다.")
            self.ocr_elapsed_var.set("처리 실패")
        messagebox.showerror("PaddleOCR 실행 실패", str(error))
        self.review_status.set("OCR 실행에 실패했습니다.")
        if self._pending_auto_send is not None:
            self._cancel_pending_auto_send("OCR에 실패해 자동 전송 예약을 취소했습니다.")

    def _ocr_succeeded(self, image_path: Path, result: OcrResult) -> None:
        self._stop_scan_animation(image_path)
        self.ocr_button.configure(state="normal")
        if hasattr(self, "ocr_run_button"):
            self.ocr_run_button.configure(state="normal")
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
            resident_number = merged_values.get("ctzCorpNum", "")
            if resident_number and not is_valid_resident_number(resident_number):
                # Preserve the OCR candidate for human correction. The save
                # action rejects it until the operator fixes the number.
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
        if hasattr(self, "raw_ocr_button"):
            self.raw_ocr_button.configure(state="normal")
        self.ocr_status.set(
            f"OCR 완료 · {len(result.lines)}개 인식 항목 · {result.elapsed_seconds:.1f}초"
        )
        if hasattr(self, "ocr_footer_var"):
            self.ocr_footer_var.set("이미지 선택 즉시 OCR이 자동으로 실행되었습니다.")
            self.ocr_elapsed_var.set(f"처리 시간 {result.elapsed_seconds:.1f}초")
        self._refresh_package_summary()
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
        if self._id_crop_preview_is_fallback:
            detection_status += " · 얼굴 자동 위치 미검출, 파란 영역을 직접 옮겨 저장하세요"
        elif self._id_crop_preview_bounds is not None:
            detection_status += " · 파란 크롭 영역을 드래그해 조정한 뒤 저장"
        elif (
            self.portal_var.get() == "KT"
            or self._id_type_detection.code in SK_ID_CROP_CODES
        ) and self._id_type_detection.code:
            detection_status += " · 사진에서 클릭해 고정 크롭 영역 지정 가능"
        self.review_status.set(f"OCR 완료 · {detection_status}")
        self._refresh_result_table()
        self._try_start_pending_auto_send()

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
            self._id_crop_preview_is_fallback = False
            if hasattr(self, "crop_reset_button"):
                self.crop_reset_button.configure(state="disabled")
            self._update_id_crop_value()
            return
        if (
            self.portal_var.get() == "SK"
            and self._id_type_detection.code not in SK_ID_CROP_CODES
        ):
            self._id_crop_preview_bounds = None
            self._id_crop_preview_is_fallback = False
            if hasattr(self, "crop_reset_button"):
                self.crop_reset_button.configure(state="disabled")
            self._update_id_crop_value()
            return
        bounds = id_portrait_bounds(
            self._source_image,
            recog_sub_cd=self._id_type_detection.code,
            require_face=True,
        )
        self._id_crop_preview_is_fallback = bounds is None
        self._id_crop_preview_bounds = bounds or id_portrait_fallback_bounds(
            self._source_image,
            recog_sub_cd=self._id_type_detection.code,
        )
        if hasattr(self, "crop_reset_button"):
            self.crop_reset_button.configure(
                state=("normal" if self._id_crop_preview_bounds is not None else "disabled")
            )
        self._update_id_crop_value()

    def _update_id_crop_value(self) -> None:
        if not hasattr(self, "id_crop_value"):
            return
        bounds = self._id_crop_preview_bounds
        if bounds is None:
            if self._source_image is not None and not self._id_type_detection.code:
                self.id_crop_value.set("신분증 종류를 선택하면 크롭 영역을 표시합니다")
            else:
                self.id_crop_value.set("이 신분증 종류에는 크롭 영역이 없습니다")
            return
        if self._id_crop_preview_is_fallback and not self._id_crop_manually_adjusted:
            self.id_crop_value.set("자동 영역 확인이 필요합니다 · 파란 영역을 드래그해 조정하세요")
        elif self._id_crop_manually_adjusted:
            self.id_crop_value.set("크롭 영역을 직접 조정했습니다 · 저장 시 반영됩니다")
        else:
            self.id_crop_value.set("자동 크롭 영역입니다 · 파란 영역을 드래그해 조정할 수 있습니다")

    def _portal_changed(self, *_args) -> None:
        self._id_crop_manually_adjusted = False
        self._refresh_id_crop_preview_bounds(reset=True)
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
        if self.version == "v1" and self.draft is None:
            messagebox.showinfo("JPG 선택", "먼저 JPG 이미지를 선택하세요.")
            return
        if self.draft is not None and self._id_type_detection.code in {
            "1001", "1101", "1102", "1201", "1301", "1302", "1303",
        }:
            resident_number = "".join(
                char for char in self._value_vars["ctzCorpNum"].get()
                if char.isdecimal()
            )
            if resident_number and not is_valid_resident_number(resident_number):
                messagebox.showwarning(
                    "주민번호 확인",
                    "OCR에서 읽은 주민번호가 검증되지 않았습니다. 화면에 남긴 후보를 "
                    "신분증과 대조해 수정한 뒤 다시 저장하거나 전송하세요.",
                    parent=self.root,
                )
                self._entries["ctzCorpNum"].focus_set()
                return
        if self.version == "v1":
            if self.draft is None or not self.draft.result.lines:
                messagebox.showinfo("OCR 결과 확인", "먼저 OCR을 실행해 결과를 확인하세요.")
                return
            self._save_locally()
            return
        if self.draft is None and not self._document_jobs:
            messagebox.showinfo(
                "전송할 자료 없음",
                "신분증 JPG 또는 PDF 서류를 하나 이상 추가하세요.",
                parent=self.root,
            )
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
        failed = [item for item in self._document_jobs if item["status"] == "변환 실패"]
        if failed:
            messagebox.showwarning(
                "PDF 변환 실패",
                "변환에 실패한 PDF가 있어 전송할 수 없습니다:\n"
                + "\n".join(str(item["source"].name) for item in failed),
                parent=self.root,
            )
            return
        ocr_pending = self.draft is not None and not self.draft.result.lines
        documents_pending = any(
            item["status"] != "변환 완료" for item in self._document_jobs
        )
        if ocr_pending or documents_pending:
            self._pending_auto_send = (store, self.portal_var.get())
            self.save_button.configure(state="disabled", text="준비되면 자동 전송")
            self._set_transfer_controls(False)
            self.review_status.set("신분증 OCR 및 PDF 변환이 끝나면 선택 PC로 전송합니다…")
            self._try_start_pending_auto_send()
            return
        self._start_v2_transfer(store, self.portal_var.get())

    def _try_start_pending_auto_send(self) -> None:
        pending = self._pending_auto_send
        if pending is None:
            return
        if self.draft is None and not self._document_jobs:
            return
        if self.draft is not None and not self.draft.result.lines:
            return
        failed = [item for item in self._document_jobs if item["status"] == "변환 실패"]
        if failed:
            names = ", ".join(str(item["source"].name) for item in failed)
            self._cancel_pending_auto_send(f"PDF 변환 실패 · 자동 전송 취소 · {names}")
            return
        if any(item["status"] != "변환 완료" for item in self._document_jobs):
            return
        self._pending_auto_send = None
        self._set_transfer_controls(True)
        self.save_button.configure(state="normal", text="선택 PC로 전송")
        store, carrier = pending
        self.review_status.set("OCR/PDF 준비 완료 · 자동 전송 시작")
        self._start_v2_transfer(store, carrier)

    def _cancel_pending_auto_send(self, message: str) -> None:
        self._pending_auto_send = None
        self._set_transfer_controls(True)
        self.save_button.configure(state="normal", text="선택 PC로 전송")
        self.review_status.set(message)

    def _start_v2_transfer(self, store: Store, carrier: str) -> None:
        result = None
        if self.draft is not None:
            if not self.draft.result.lines:
                messagebox.showinfo(
                    "OCR 결과 확인", "신분증 OCR이 끝난 뒤 전송할 수 있습니다.",
                    parent=self.root,
                )
                return
            result = self.draft.result
        if result is None and not self._document_jobs:
            messagebox.showinfo(
                "전송할 자료 없음",
                "신분증 JPG 또는 PDF 서류를 하나 이상 추가하세요.",
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
        values = self._current_values() if result is not None else {}
        if result is not None and values.get("kaitIdType", "").strip() not in ID_TYPES:
            messagebox.showinfo(
                "신분증 종류 확인",
                "전송 전에 신분증 종류를 선택하고 OCR 항목을 확인하세요.",
                parent=self.root,
            )
            return
        detection = (
            self._id_type_detection
            if result is not None else IdTypeDetection(None, None)
        )
        documents = list(self._document_jobs)
        session_id = self._document_job_id or str(uuid4())
        self._document_job_id = session_id
        relay_config = dict(self.relay_config)
        transfer_job_id = str(uuid4())
        cancel_event = Event()
        self._transfer_cancel_event = cancel_event
        self._reset_requested = False
        self.save_button.configure(state="disabled", text="전송 중…")
        self._set_transfer_controls(False)
        self.reset_button.configure(state="normal", text="전송 취소·초기화")
        self.review_status.set(f"Render Relay로 {carrier} 작업 전송 중…")
        Thread(
            target=self._send_work_package,
            args=(store, carrier, session_id, result, detection, values, documents,
                  relay_config, transfer_job_id, cancel_event),
            daemon=True,
        ).start()

    def _save_locally(self) -> None:
        """Apply reviewed OCR directly on this PC, matching the original V1 flow."""
        assert self.draft is not None
        if self._id_crop_preview_is_fallback and not self._id_crop_manually_adjusted:
            messagebox.showwarning(
                "크롭 영역 확인",
                "얼굴 사진 위치를 자동으로 찾지 못했습니다. 파란 고정 영역을 "
                "사진 위로 옮긴 뒤 저장하세요.",
                parent=self.root,
            )
            return
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
        session_id: str,
        result: OcrResult | None,
        detection: IdTypeDetection,
        values: dict[str, str],
        documents: list[dict[str, object]],
        relay_config: dict[str, str],
        transfer_job_id: str,
        cancel_event: Event,
    ) -> None:
        try:
            job_root = default_job_directory() / session_id
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
            if cancel_event.is_set():
                raise TransferCancelledError("작업 ZIP 생성 중 송신기 초기화가 요청됐습니다.")
            def progress(message: str) -> None:
                self.root.after(0, self._send_progress, cancel_event, message)

            job_id = send_work_package(
                relay_config, store.relay_station_id, package, progress,
                cancel_event=cancel_event,
            )
        except DeliveryPendingError as exc:
            if cancel_event.is_set():
                self.root.after(
                    0, self._work_package_cancelled,
                    "취소 요청 후 Relay 상태 확인이 끊겼습니다. Receiver 상태도 확인해 주세요.",
                )
                return
            self.root.after(
                0, self._work_package_pending, store.name, carrier,
                exc.job_id, values, str(exc),
            )
            return
        except TransferCancelledError as exc:
            self.root.after(0, self._work_package_cancelled, str(exc))
            return
        except Exception as exc:
            if cancel_event.is_set():
                self.root.after(
                    0, self._work_package_cancelled,
                    f"취소 요청은 했지만 Relay 결과를 확인하지 못했습니다: {exc}",
                )
                return
            self.root.after(0, self._work_package_failed, store.name, str(exc))
            return
        finally:
            if cancel_event.is_set():
                shutil.rmtree(default_job_directory() / session_id, ignore_errors=True)
        if cancel_event.is_set():
            self.root.after(
                0, self._work_package_cancelled,
                "취소 요청 전에 Receiver가 자료 수신을 완료했습니다. 송신기 자료만 초기화합니다.",
            )
            return
        self.root.after(
            0, self._work_package_sent, store.name, carrier, job_id, values
        )

    def _send_progress(self, cancel_event: Event, message: str) -> None:
        if self._transfer_cancel_event is cancel_event and not cancel_event.is_set():
            self.review_status.set(message)

    def _reset_sender_session(self) -> None:
        if self.version != "v2":
            return
        prompt = (
            "현재 송신 작업을 취소하고 화면과 임시 작업 자료를 초기화할까요?\n\n"
            "Relay 전송 중이면 취소를 요청합니다. Receiver가 이미 받은 자료는 Receiver에서 별도로 초기화해야 합니다.\n"
            "이 앱이 만든 임시 신분증 사본·PDF 페이지·전송 ZIP만 정리하며, 원본 파일은 삭제하지 않습니다."
        )
        if not messagebox.askyesno("전송 취소·초기화", prompt, parent=self.root):
            return

        if self._transfer_cancel_event is not None:
            self._reset_requested = True
            self._transfer_cancel_event.set()
            self.reset_button.configure(state="disabled", text="취소 요청 중…")
            self.review_status.set(
                "취소 요청 중 · 진행 중인 Relay 요청이 끝나면 작업 자료를 정리합니다."
            )
            return

        pending = self._pending_confirmation
        if pending is not None and self.relay_config is not None:
            _, _, job_id, _ = pending
            self._reset_requested = True
            self.reset_button.configure(state="disabled", text="Relay 취소 중…")
            self._set_transfer_controls(False)
            self.save_button.configure(state="disabled")
            self.review_status.set("Receiver 수신 확인 대기 작업을 Relay에서 취소하는 중…")
            Thread(
                target=self._cancel_pending_for_reset,
                args=(dict(self.relay_config), job_id),
                daemon=True,
            ).start()
            return

        self._clear_sender_session()

    def _cancel_pending_for_reset(
        self, relay_config: dict[str, str], job_id: str,
    ) -> None:
        status = "unknown"
        error = ""
        try:
            status = cancel_work_package(relay_config, job_id)
        except Exception as exc:
            error = str(exc)
        self.root.after(0, self._finish_pending_reset, status, error)

    def _finish_pending_reset(self, status: str, error: str) -> None:
        self._clear_sender_session()
        if status == "delivered":
            message = "Receiver가 이미 자료를 수신했습니다. 송신기 자료는 초기화했지만 Receiver 자료는 별도로 초기화해야 합니다."
            self.review_status.set(message)
            messagebox.showwarning("초기화 완료 · Receiver 수신됨", message, parent=self.root)
        elif error:
            message = f"송신기 자료는 초기화했지만 Relay 취소 여부를 확인하지 못했습니다: {error}"
            self.review_status.set(message)
            messagebox.showwarning("Relay 취소 확인 필요", message, parent=self.root)
        else:
            self.review_status.set("송신 작업 취소 및 화면 초기화 완료")

    def _work_package_cancelled(self, message: str) -> None:
        self._clear_sender_session()
        if "완료됐습니다" in message or "확인하지 못했습니다" in message or "확인해 주세요" in message:
            self.review_status.set(f"초기화 완료 · {message}")
            messagebox.showwarning("전송 취소 결과", message, parent=self.root)
        else:
            self.review_status.set("전송 취소 및 작업 초기화 완료")

    def _clear_sender_session(self) -> None:
        job_id = self._document_job_id
        if job_id:
            shutil.rmtree(default_job_directory() / job_id, ignore_errors=True)
        self._transfer_cancel_event = None
        self._reset_requested = False
        self._pending_auto_send = None
        self._pending_confirmation = None
        self._job_sent = False
        self._document_job_id = None
        self._document_jobs.clear()
        self.draft = None
        self._source_image = None
        self._preview_base = None
        self._preview = None
        self._id_crop_preview_bounds = None
        self._id_crop_preview_is_fallback = False
        self._id_crop_manually_adjusted = False
        self._id_crop_drag = None
        self._mask_preview_position = None
        self._id_type_detection = IdTypeDetection(None, None)
        self._update_id_crop_value()
        self._stop_scan_animation(restore=False)
        for field in REVIEW_TEXT_FIELDS:
            self._value_vars[field].set("")
        self._id_type_var.set("유형 선택…")
        if isinstance(self.image_label, tk.Canvas):
            self.image_label.delete("all")
            self.image_label.create_text(
                self.image_label.winfo_width() / 2,
                self.image_label.winfo_height() / 2,
                text="신분증 이미지를 선택하거나 이 영역에 끌어다 놓으세요",
                fill="#667085", font=("Segoe UI", 10), justify="center",
            )
        else:
            self.image_label.configure(
                image="", text="신분증 이미지를 선택하거나 이 영역에 끌어다 놓으세요"
            )
        self.id_image_name.set("선택된 신분증 없음")
        self.raw_ocr_button.configure(state="disabled")
        self.crop_reset_button.configure(state="disabled")
        self.save_button.configure(state="normal", text="선택 PC로 전송")
        self.pdf_button.configure(state="normal")
        self.status_button.configure(state="disabled", text="전송 상태 확인")
        self.reset_button.configure(state="normal", text="전송 취소·초기화")
        self._set_transfer_controls(True)
        self._refresh_result_table()
        self._refresh_document_table()
        self.review_status.set("작업 초기화 완료")

    def _work_package_failed(self, store_name: str, error: str) -> None:
        if self._reset_requested:
            self._clear_sender_session()
            self.review_status.set("작업 초기화 완료 · Relay 전송은 시작되지 않았습니다.")
            return
        self._transfer_cancel_event = None
        self._pending_auto_send = None
        self._pending_confirmation = None
        self.status_button.configure(state="disabled")
        self.save_button.configure(state="normal", text="선택 PC로 전송")
        self._set_transfer_controls(True)
        self.review_status.set("Relay 전송 또는 Receiver 수신 실패")
        explanation = ""
        if "Relay HTTP 401" in error or '"error":"unauthorized"' in error:
            explanation = (
                "\n\nRelay 중앙 토큰 인증이 거부됐습니다. 송신기 Relay 설정의 중앙 송신 토큰이 "
                "Render 서비스의 KYEONGWAN_RELAY_CENTRAL_TOKEN과 일치하는지 확인하세요. "
                "desk-01 같은 자리별 Receiver 토큰은 여기에 사용할 수 없습니다."
            )
        messagebox.showerror(
            "Receiver 전송 실패",
            f"{store_name} Receiver에 작업을 저장하지 못했습니다.\n{error}{explanation}",
            parent=self.root,
        )

    def _work_package_pending(
        self, store_name: str, carrier: str, job_id: str,
        values: dict[str, str], error: str,
    ) -> None:
        self._transfer_cancel_event = None
        if self._reset_requested and self.relay_config is not None:
            self._pending_confirmation = (store_name, carrier, job_id, values)
            self.reset_button.configure(state="disabled", text="Relay 취소 중…")
            Thread(
                target=self._cancel_pending_for_reset,
                args=(dict(self.relay_config), job_id),
                daemon=True,
            ).start()
            return
        self._set_transfer_controls(True)
        self.save_button.configure(state="disabled", text="Receiver 확인 대기")
        self.pdf_button.configure(state="disabled")
        self._pending_confirmation = (store_name, carrier, job_id, values)
        self.status_button.configure(state="normal")
        self._job_sent = True
        self.review_status.set(f"{store_name} Receiver 수신 확인 대기 · {job_id}")
        messagebox.showwarning("Receiver 수신 확인 대기", error, parent=self.root)

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
        if self._reset_requested or self._pending_confirmation is None:
            return
        self.status_button.configure(state="normal", text="전송 상태 확인")
        self.review_status.set(f"전송 상태 확인 실패 · {error}")

    def _pending_status_updated(
        self, store_name: str, carrier: str, job_id: str,
        values: dict[str, str], status: str,
    ) -> None:
        if self._reset_requested or self._pending_confirmation is None:
            return
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
        if self._reset_requested:
            self._transfer_cancel_event = None
            self._clear_sender_session()
            message = "취소 요청 전에 Receiver가 자료 수신을 완료했습니다. 송신기 자료만 초기화했습니다."
            self.review_status.set(message)
            messagebox.showwarning("전송 취소 결과", message, parent=self.root)
            return
        self._clear_sender_session()
        self.review_status.set(
            f"{store_name} Receiver 수신 완료 · 신분증은 Receiver 확인 후 {carrier}에 저장됩니다 · 다음 자료를 끌어다 놓으세요"
        )

def main(version: str = "v2") -> None:
    if version == "v2":
        from new_work.gui.web_sender import main as web_sender_main

        web_sender_main()
        return
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


def recognize_identity_for_review(ocr: OcrAdapter, image_path: Path) -> tuple[OcrResult, IdTypeDetection, dict[str, str]]:
    """Run the exact sender OCR/retry pipeline without constructing its UI."""
    callbacks: list[tuple[object, tuple[object, ...]]] = []

    class _CallbackCapture:
        @staticmethod
        def after(_delay: int, callback, *args) -> None:
            callbacks.append((callback, args))

    worker = NewWorkApp.__new__(NewWorkApp)
    worker.ocr = ocr
    worker.root = _CallbackCapture()
    worker._recognize_in_background(Path(image_path))
    if not callbacks:
        raise RuntimeError("OCR 결과를 받지 못했습니다.")
    callback, args = callbacks[-1]
    if getattr(callback, "__name__", "") == "_ocr_failed":
        error = args[-1] if args else RuntimeError("OCR에 실패했습니다.")
        raise error
    if getattr(callback, "__name__", "") != "_ocr_succeeded" or len(args) < 2:
        raise RuntimeError("OCR 결과 형식이 올바르지 않습니다.")
    result = args[-1]
    if not isinstance(result, OcrResult):
        raise RuntimeError("OCR 결과 데이터가 올바르지 않습니다.")
    detection = detect_id_type(result.lines)
    values = extract_identity_fields(result.lines, id_type_code=detection.code)
    values.update({key: str(value) for key, value in result.values.items()})
    for key in REVIEW_TEXT_FIELDS:
        values.setdefault(key, "")
    if detection.code == "1401":
        mask_position = result.values.get("mskPos", "")
    else:
        mask_position = result.values.get("mskPos") or detect_mask_position(
            result.lines, image_path=result.image_path,
        )
    values["mskPos"] = str(mask_position or "")
    if detection.code:
        values.update({
            "kaitIdType": detection.code,
            "recogSubCd": detection.code,
            "idType": detection.type_code or "",
            "nationClCd": "F" if detection.code == "1401" else "I",
        })
    return result, detection, values
