"""Reusable candidate grid for the generation workspace."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk


@dataclass(frozen=True, slots=True)
class GenerationCandidateItem:
    revision_id: str
    asset_id: str
    path: Path
    label: str
    prompt: str = ""
    favorite: bool = False


class GenerationCandidateGrid(ttk.Frame):
    """Small bounded grid that owns thumbnail references and selection state."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        on_select: Callable[[str], None],
        columns: int = 2,
        thumbnail_size: tuple[int, int] = (168, 104),
    ) -> None:
        super().__init__(master, style="Surface.TFrame")
        if columns < 1:
            raise ValueError("候选网格列数必须至少为 1")
        self._on_select = on_select
        self._columns = columns
        self._thumbnail_size = thumbnail_size
        self._items: tuple[GenerationCandidateItem, ...] = ()
        self._selected_revision_id: str | None = None
        self._photo_refs: dict[str, ImageTk.PhotoImage] = {}
        self._cards: dict[str, ttk.LabelFrame] = {}
        for column in range(columns):
            self.columnconfigure(column, weight=1, uniform="generation-candidate")
        self.empty_label = ttk.Label(
            self,
            text="生成完成后，候选图片会显示在这里。",
            style="Muted.Toolbar.TLabel",
            justify="center",
        )
        self.empty_label.grid(row=0, column=0, columnspan=columns, pady=24)

    @property
    def items(self) -> tuple[GenerationCandidateItem, ...]:
        return self._items

    @property
    def selected_revision_id(self) -> str | None:
        return self._selected_revision_id

    def set_candidates(
        self,
        items: Sequence[GenerationCandidateItem],
        *,
        selected_revision_id: str | None = None,
    ) -> None:
        normalized = tuple(items)
        if len({item.revision_id for item in normalized}) != len(normalized):
            raise ValueError("候选修订编号不能重复")
        self._items = normalized
        available = {item.revision_id for item in normalized}
        self._selected_revision_id = (
            selected_revision_id
            if selected_revision_id in available
            else normalized[0].revision_id
            if normalized
            else None
        )
        self._rebuild()

    def clear(self) -> None:
        self.set_candidates(())

    def select(self, revision_id: str, *, notify: bool = True) -> bool:
        if revision_id not in {item.revision_id for item in self._items}:
            return False
        changed = revision_id != self._selected_revision_id
        self._selected_revision_id = revision_id
        self._refresh_card_titles()
        if notify and (changed or self._items):
            self._on_select(revision_id)
        return True

    def _rebuild(self) -> None:
        for child in self.winfo_children():
            child.grid_forget()
            if child is not self.empty_label:
                child.destroy()
        self._photo_refs.clear()
        self._cards.clear()
        if not self._items:
            self.empty_label.grid(row=0, column=0, columnspan=self._columns, pady=24)
            return
        self.empty_label.grid_remove()
        for index, item in enumerate(self._items):
            row, column = divmod(index, self._columns)
            card = ttk.LabelFrame(
                self,
                text=self._card_title(index, item),
                style="Panel.TLabelframe",
                padding=(8, 7),
            )
            card.grid(row=row, column=column, sticky="nsew", padx=5, pady=5)
            card.columnconfigure(0, weight=1)
            self._cards[item.revision_id] = card
            photo = self._thumbnail(item.path)
            self._photo_refs[item.revision_id] = photo
            preview = ttk.Label(card, image=photo, anchor="center")
            preview.grid(row=0, column=0, sticky="nsew")
            ttk.Label(
                card,
                text=item.label,
                style="Muted.Toolbar.TLabel",
                anchor="center",
            ).grid(row=1, column=0, sticky="ew", pady=(6, 2))
            choose = ttk.Button(
                card,
                text="选择此候选",
                command=lambda value=item.revision_id: self.select(value),
            )
            choose.grid(row=2, column=0, sticky="ew", pady=(4, 0))
            for widget in (card, preview):
                widget.bind(
                    "<Button-1>",
                    lambda _event, value=item.revision_id: self.select(value),
                )

    def _refresh_card_titles(self) -> None:
        for index, item in enumerate(self._items):
            card = self._cards.get(item.revision_id)
            if card is not None:
                card.configure(text=self._card_title(index, item))

    def _card_title(self, index: int, item: GenerationCandidateItem) -> str:
        favorite = "★ " if item.favorite else ""
        selected = " · 已选择" if item.revision_id == self._selected_revision_id else ""
        return f"{favorite}候选 {index + 1}{selected}"

    def _thumbnail(self, path: Path) -> ImageTk.PhotoImage:
        target_width, target_height = self._thumbnail_size
        background = Image.new("RGB", self._thumbnail_size, (235, 231, 238))
        try:
            with Image.open(path) as opened:
                opened.load()
                oriented = ImageOps.exif_transpose(opened)
                try:
                    rgba = oriented.convert("RGBA")
                    try:
                        preview = ImageOps.contain(
                            rgba,
                            self._thumbnail_size,
                            Image.Resampling.LANCZOS,
                        )
                    finally:
                        rgba.close()
                finally:
                    if oriented is not opened:
                        oriented.close()
            try:
                x = (target_width - preview.width) // 2
                y = (target_height - preview.height) // 2
                background.paste(preview, (x, y), preview.getchannel("A"))
            finally:
                preview.close()
        except (OSError, ValueError):
            pass
        try:
            return ImageTk.PhotoImage(background, master=self)
        finally:
            background.close()


__all__ = ["GenerationCandidateGrid", "GenerationCandidateItem"]
