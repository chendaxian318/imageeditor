"""Compact horizontal thumbnail strip for the viewer-only shell."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from pathlib import Path

from PIL import Image, ImageTk

from ..thumbnails import ThumbnailLoader

TILE_WIDTH = 82
TILE_HEIGHT = 62
BUFFER_TILES = 3


class ThumbnailFilmstrip(tk.Canvas):
    """Virtualized, keyboard-friendly folder preview used at the canvas bottom."""

    def __init__(
        self,
        master: tk.Misc,
        on_activate: Callable[[Path], bool],
        *,
        loader: ThumbnailLoader | None = None,
    ) -> None:
        super().__init__(
            master,
            height=76,
            borderwidth=0,
            highlightthickness=0,
            takefocus=False,
        )
        self.on_activate = on_activate
        self.loader = loader or ThumbnailLoader(max_workers=2, cache_size=96)
        self.paths: list[Path] = []
        self.selected_index: int | None = None
        self._path_indices: dict[str, int] = {}
        self._generation = 0
        self._pending: dict[int, tuple[int, Path, Future[Image.Image]]] = {}
        self._photos: dict[int, ImageTk.PhotoImage] = {}
        self._backgrounds: dict[int, ImageTk.PhotoImage] = {}
        self._background_items: dict[int, int] = {}
        self._image_items: dict[int, int] = {}
        self._schedule_job: str | None = None
        self._poll_job: str | None = None
        self._surface = "#181a1e"
        self._surface_alt = "#30343a"
        self._accent = "#f1f3f5"
        self._border = "#30343a"

        self.configure(background=self._surface)
        self.bind("<Configure>", lambda _event: self._schedule_visible())
        self.bind("<Button-1>", self._clicked)
        self.bind("<MouseWheel>", self._mousewheel)
        self.bind("<Shift-MouseWheel>", self._mousewheel)
        self.bind("<Left>", lambda _event: self._move_selection(-1))
        self.bind("<Right>", lambda _event: self._move_selection(1))
        self.bind("<Return>", self._activate_selected)
        self.bind("<space>", self._activate_selected)

    def apply_palette(
        self,
        *,
        surface: str,
        surface_alt: str,
        accent: str,
        border: str,
    ) -> None:
        self._surface = surface
        self._surface_alt = surface_alt
        self._accent = accent
        self._border = border
        self.configure(
            background=surface,
        )
        self._redraw_backgrounds()

    def set_paths(
        self,
        paths: Sequence[str | Path],
        selected: str | Path | None = None,
    ) -> None:
        self._generation += 1
        self.paths = [Path(path) for path in paths]
        self._path_indices = {self._path_key(path): index for index, path in enumerate(self.paths)}
        self.selected_index = None
        self._pending.clear()
        self._photos.clear()
        self._backgrounds.clear()
        self._background_items.clear()
        self._image_items.clear()
        self.delete("all")
        self.configure(scrollregion=(0, 0, max(1, len(self.paths) * TILE_WIDTH), TILE_HEIGHT))
        if selected is not None:
            self.select(selected, reveal=False)
        self._schedule_visible()

    def select(self, path: str | Path | None, *, reveal: bool = True) -> bool:
        previous = self.selected_index
        if path is None:
            self.selected_index = None
            self._redraw_selection(previous)
            return False
        index = self._path_indices.get(self._path_key(path))
        if index is None:
            return False
        self.selected_index = index
        self._redraw_selection(previous, index)
        if reveal:
            self._reveal(index)
        return True

    def invalidate(self, path: str | Path) -> None:
        self.loader.invalidate(path)

    @staticmethod
    def _path_key(path: str | Path) -> str:
        try:
            return str(Path(path).resolve()).casefold()
        except OSError:
            return str(Path(path).absolute()).casefold()

    def _visible_range(self) -> range:
        if not self.paths:
            return range(0)
        left = max(0, int(self.canvasx(0) // TILE_WIDTH) - BUFFER_TILES)
        right_x = self.canvasx(max(1, self.winfo_width()))
        right = min(
            len(self.paths),
            int(right_x // TILE_WIDTH) + BUFFER_TILES + 2,
        )
        return range(left, right)

    def _schedule_visible(self) -> None:
        if self._schedule_job is None:
            self._schedule_job = self.after_idle(self._load_visible)

    def _load_visible(self) -> None:
        self._schedule_job = None
        visible = set(self._visible_range())
        for index in list(self._background_items):
            if index in visible:
                continue
            self.delete(self._background_items.pop(index))
            self.delete(self._image_items.pop(index))
            self._photos.pop(index, None)
            self._backgrounds.pop(index, None)
        for index in visible:
            self._ensure_tile(index)
            if index in self._photos or index in self._pending:
                continue
            path = self.paths[index]
            self._pending[index] = (self._generation, path, self.loader.load(path))
        self._redraw_backgrounds()
        if self._pending and self._poll_job is None:
            self._poll_job = self.after(30, self._poll_pending)

    def _ensure_tile(self, index: int) -> None:
        if index in self._background_items:
            return
        left = index * TILE_WIDTH
        self._background_items[index] = self.create_image(
            left,
            0,
            anchor="nw",
        )
        self._image_items[index] = self.create_image(
            left + TILE_WIDTH / 2,
            TILE_HEIGHT / 2,
        )
        self._render_background(index)

    def _render_background(self, index: int) -> None:
        item = self._background_items.get(index)
        if item is None:
            return
        # The filmstrip reads as images floating over the viewer.  Keeping an
        # empty canvas item preserves the virtualized tile structure without
        # generating transparent bitmap backgrounds during every resize.
        self._backgrounds.pop(index, None)
        self.itemconfigure(item, image="")

    def _redraw_backgrounds(self) -> None:
        for index in self._background_items:
            self._render_background(index)

    def _redraw_selection(self, *indices: int | None) -> None:
        for index in {value for value in indices if value is not None}:
            self._render_background(index)

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
                continue
            if index not in self._image_items:
                thumbnail.close()
                continue
            try:
                photo = ImageTk.PhotoImage(thumbnail, master=self)
            finally:
                thumbnail.close()
            self._photos[index] = photo
            self.itemconfigure(self._image_items[index], image=photo)
        if self._pending:
            self._poll_job = self.after(30, self._poll_pending)

    def _clicked(self, event: tk.Event) -> str:
        index = int(self.canvasx(event.x) // TILE_WIDTH)
        if 0 <= index < len(self.paths) and self.on_activate(self.paths[index]):
            self.select(self.paths[index])
        return "break"

    def _move_selection(self, direction: int) -> str:
        if not self.paths:
            return "break"
        current = self.selected_index if self.selected_index is not None else 0
        self.selected_index = max(0, min(len(self.paths) - 1, current + direction))
        self._redraw_selection(current, self.selected_index)
        self._reveal(self.selected_index)
        return "break"

    def _activate_selected(self, _event: tk.Event) -> str:
        if self.selected_index is not None:
            path = self.paths[self.selected_index]
            if self.on_activate(path):
                self.select(path)
        return "break"

    def _mousewheel(self, event: tk.Event) -> str:
        self.xview_scroll(-3 if event.delta > 0 else 3, "units")
        self._schedule_visible()
        return "break"

    def _reveal(self, index: int) -> None:
        width = max(1, self.winfo_width())
        left = self.canvasx(0)
        tile_left = index * TILE_WIDTH
        tile_right = tile_left + TILE_WIDTH
        total = max(width, len(self.paths) * TILE_WIDTH)
        if tile_left < left:
            self.xview_moveto(tile_left / total)
        elif tile_right > left + width:
            self.xview_moveto(max(0.0, (tile_right - width) / total))
        self._schedule_visible()

    def shutdown(self) -> None:
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._schedule_job is not None:
            self.after_cancel(self._schedule_job)
            self._schedule_job = None
        self.loader.shutdown()
