"""Virtualized thumbnail strip used by the main image viewer."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageTk

from ..thumbnails import ThumbnailLoader

ROW_HEIGHT = 94
BUFFER_ROWS = 4


class ThumbnailSidebar(ttk.Frame):
    """Scrollable directory preview list that decodes only visible rows."""

    def __init__(
        self,
        master: tk.Misc,
        on_activate: Callable[[Path], bool],
        *,
        loader: ThumbnailLoader | None = None,
        title: str = "当前目录",
    ) -> None:
        super().__init__(master, padding=(8, 0, 0, 12), width=220)
        self.on_activate = on_activate
        self.loader = loader or ThumbnailLoader()
        self._base_title = title
        self.paths: list[Path] = []
        self.selected_index: int | None = None
        self._generation = 0
        self._pending: dict[int, tuple[int, Path, Future[Image.Image]]] = {}
        self._photos: dict[int, ImageTk.PhotoImage] = {}
        self._image_items: dict[int, int] = {}
        self._row_items: dict[int, int] = {}
        self._text_items: dict[int, int] = {}
        self._poll_job: str | None = None
        self._schedule_job: str | None = None
        self._surface = "#ffffff"
        self._surface_alt = "#eef2f7"
        self._foreground = "#172033"
        self._muted = "#586174"
        self._accent = "#2563eb"
        self._border = "#cbd5e1"

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.title_var = tk.StringVar(value=title)
        ttk.Label(self, textvariable=self.title_var, padding=(5, 8), anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew"
        )
        self.canvas = tk.Canvas(
            self,
            width=204,
            highlightthickness=1,
            takefocus=True,
            bg=self._surface,
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        self.scrollbar.grid(row=1, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind("<Configure>", lambda _event: self._schedule_visible())
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.canvas.bind("<Button-1>", self._clicked)
        self.canvas.bind("<Return>", self._activate_selected)
        self.canvas.bind("<space>", self._activate_selected)

    def apply_palette(
        self,
        *,
        surface: str,
        surface_alt: str,
        foreground: str,
        muted: str,
        accent: str,
        border: str,
    ) -> None:
        self._surface = surface
        self._surface_alt = surface_alt
        self._foreground = foreground
        self._muted = muted
        self._accent = accent
        self._border = border
        self.canvas.configure(bg=surface, highlightbackground=border, highlightcolor=accent)
        self._redraw_colors()

    def set_paths(self, paths: Sequence[str | Path], selected: str | Path | None = None) -> None:
        self._generation += 1
        self.paths = [Path(path) for path in paths]
        self._pending.clear()
        self._photos.clear()
        self._image_items.clear()
        self._row_items.clear()
        self._text_items.clear()
        self.canvas.delete("all")
        width = max(190, self.canvas.winfo_width())
        total_height = max(1, len(self.paths) * ROW_HEIGHT)
        self.canvas.configure(scrollregion=(0, 0, width, total_height))
        self.title_var.set(f"{self._base_title} · {len(self.paths)}")
        self.selected_index = None
        if selected is not None:
            self.select(selected, reveal=False)
        self._redraw_colors()
        self._schedule_visible()

    def set_title(self, title: str) -> None:
        self._base_title = title
        self.title_var.set(f"{title} · {len(self.paths)}")

    @staticmethod
    def _short_name(name: str, limit: int = 22) -> str:
        if len(name) <= limit:
            return name
        stem = Path(name).stem
        suffix = Path(name).suffix
        keep = max(6, limit - len(suffix) - 1)
        return f"{stem[:keep]}…{suffix}"

    def select(self, path: str | Path | None, *, reveal: bool = True) -> bool:
        if path is None:
            self.selected_index = None
            self._redraw_colors()
            return False
        key = self._path_key(path)
        index = next((i for i, item in enumerate(self.paths) if self._path_key(item) == key), None)
        if index is None:
            return False
        self.selected_index = index
        self._redraw_colors()
        if reveal:
            self._reveal(index)
        return True

    @staticmethod
    def _path_key(path: str | Path) -> str:
        try:
            return str(Path(path).resolve()).casefold()
        except OSError:
            return str(Path(path).absolute()).casefold()

    def _redraw_colors(self) -> None:
        for index, row in self._row_items.items():
            selected = index == self.selected_index
            self.canvas.itemconfigure(
                row,
                fill=self._surface_alt if selected else self._surface,
                outline=self._accent if selected else self._border,
                width=2 if selected else 1,
            )
            self.canvas.itemconfigure(
                self._text_items[index], fill=self._foreground if selected else self._muted
            )

    def _ensure_row(self, index: int) -> None:
        if index in self._row_items:
            return
        path = self.paths[index]
        width = max(190, self.canvas.winfo_width())
        top = index * ROW_HEIGHT
        self._row_items[index] = self.canvas.create_rectangle(
            3, top + 3, width - 4, top + ROW_HEIGHT - 3, width=1
        )
        self._image_items[index] = self.canvas.create_image(51, top + ROW_HEIGHT / 2)
        self._text_items[index] = self.canvas.create_text(
            101,
            top + ROW_HEIGHT / 2,
            text=self._short_name(path.name),
            anchor="w",
            width=max(70, width - 108),
            justify="left",
            font=("Microsoft YaHei UI", 9),
        )

    def _reveal(self, index: int) -> None:
        height = max(1, self.canvas.winfo_height())
        top = self.canvas.canvasy(0)
        row_top = index * ROW_HEIGHT
        row_bottom = row_top + ROW_HEIGHT
        total = max(height, len(self.paths) * ROW_HEIGHT)
        if row_top < top:
            self.canvas.yview_moveto(row_top / total)
        elif row_bottom > top + height:
            self.canvas.yview_moveto(max(0.0, (row_bottom - height) / total))
        self._schedule_visible()

    def _clicked(self, event: tk.Event) -> str:
        index = int(self.canvas.canvasy(event.y) // ROW_HEIGHT)
        if 0 <= index < len(self.paths) and self.on_activate(self.paths[index]):
            self.select(self.paths[index])
        self.canvas.focus_set()
        return "break"

    def _activate_selected(self, _event: tk.Event) -> str:
        if self.selected_index is not None and self.on_activate(self.paths[self.selected_index]):
            self.select(self.paths[self.selected_index])
        return "break"

    def _mousewheel(self, event: tk.Event) -> str:
        delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta * 3, "units")
        self._schedule_visible()
        return "break"

    def _yview(self, *args: object) -> None:
        self.canvas.yview(*args)
        self._schedule_visible()

    def _visible_range(self) -> range:
        if not self.paths:
            return range(0)
        top = max(0, int(self.canvas.canvasy(0) // ROW_HEIGHT) - BUFFER_ROWS)
        bottom_y = self.canvas.canvasy(max(1, self.canvas.winfo_height()))
        bottom = min(len(self.paths), int(bottom_y // ROW_HEIGHT) + BUFFER_ROWS + 2)
        return range(top, bottom)

    def _schedule_visible(self) -> None:
        if self._schedule_job is not None:
            return
        self._schedule_job = self.after_idle(self._load_visible)

    def _load_visible(self) -> None:
        self._schedule_job = None
        visible = set(self._visible_range())
        for index in list(self._row_items):
            if index not in visible:
                self.canvas.delete(self._row_items.pop(index))
                self.canvas.delete(self._image_items.pop(index))
                self.canvas.delete(self._text_items.pop(index))
                self._photos.pop(index, None)
        for index in visible:
            self._ensure_row(index)
            if index in self._photos or index in self._pending:
                continue
            path = self.paths[index]
            self._pending[index] = (self._generation, path, self.loader.load(path))
        self._redraw_colors()
        if self._pending and self._poll_job is None:
            self._poll_job = self.after(30, self._poll_pending)

    def _poll_pending(self) -> None:
        self._poll_job = None
        for index, (generation, path, future) in list(self._pending.items()):
            if not future.done():
                continue
            self._pending.pop(index, None)
            if generation != self._generation or index >= len(self.paths):
                continue
            if self._path_key(self.paths[index]) != self._path_key(path):
                continue
            try:
                thumbnail = future.result()
            except Exception:
                if index in self._text_items:
                    self.canvas.itemconfigure(
                        self._text_items[index], text=f"⚠ {self._short_name(path.name)}"
                    )
                continue
            if index not in self._image_items:
                thumbnail.close()
                continue
            try:
                photo = ImageTk.PhotoImage(thumbnail, master=self)
            finally:
                thumbnail.close()
            self._photos[index] = photo
            self.canvas.itemconfigure(self._image_items[index], image=photo)
        if self._pending:
            self._poll_job = self.after(30, self._poll_pending)

    def invalidate(self, path: str | Path) -> None:
        self.loader.invalidate(path)

    def shutdown(self) -> None:
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._schedule_job is not None:
            self.after_cancel(self._schedule_job)
            self._schedule_job = None
        self.loader.shutdown()
