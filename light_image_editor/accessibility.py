"""Keyboard focus and accessible-name helpers for Tk widgets."""

from __future__ import annotations

import ctypes
import os
import tkinter as tk
from collections.abc import Iterable


def set_accessible_name(widget: tk.Misc, name: str) -> None:
    setattr(widget, "accessible_name", name)
    try:
        if "takefocus" in widget.keys():
            widget.configure({"takefocus": True})
    except tk.TclError:
        pass
    if os.name == "nt":

        def expose_name() -> None:
            try:
                ctypes.windll.user32.SetWindowTextW(widget.winfo_id(), name)
            except (AttributeError, OSError, tk.TclError):
                pass

        try:
            widget.after_idle(expose_name)
        except tk.TclError:
            pass


def focusable_widgets(groups: Iterable[tk.Misc]) -> list[tk.Misc]:
    result: list[tk.Misc] = []
    for group in groups:
        try:
            if group.winfo_viewable():
                result.append(group)
        except tk.TclError:
            continue
    return result


class FocusNavigator:
    def __init__(self, root: tk.Misc, groups: Iterable[tk.Misc]) -> None:
        self.root = root
        self.groups = list(groups)

    def move(self, reverse: bool = False) -> str:
        available = focusable_widgets(self.groups)
        if not available:
            return "break"
        current = self.root.focus_get()
        try:
            index = available.index(current) if current in available else (-1 if not reverse else 0)
        except ValueError:
            index = -1
        step = -1 if reverse else 1
        target = available[(index + step) % len(available)]
        target.focus_set()
        return "break"
