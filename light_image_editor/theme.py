"""Application colour, spacing and focus design tokens."""

from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    background: str
    surface: str
    surface_alt: str
    foreground: str
    muted: str
    accent: str
    accent_hover: str
    border: str
    focus: str
    danger: str
    spacing_small: int = 6
    spacing_medium: int = 10
    spacing_large: int = 16


LIGHT_TOKENS = ThemeTokens(
    "light",
    "#f5f7fa",
    "#ffffff",
    "#eef2f7",
    "#172033",
    "#586174",
    "#2563eb",
    "#1d4ed8",
    "#cbd5e1",
    "#f59e0b",
    "#dc2626",
)
DARK_TOKENS = ThemeTokens(
    "dark",
    "#111827",
    "#1f2937",
    "#273449",
    "#f3f4f6",
    "#aab4c4",
    "#60a5fa",
    "#3b82f6",
    "#46546a",
    "#fbbf24",
    "#f87171",
)


def system_theme() -> str:
    if os.name != "nt":
        return "light"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if int(value) else "dark"
    except (OSError, ValueError):
        return "light"


def resolve_theme(mode: str) -> ThemeTokens:
    selected = system_theme() if mode == "system" else mode
    return DARK_TOKENS if selected == "dark" else LIGHT_TOKENS


class ThemeManager:
    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.mode = "system"
        self.tokens = resolve_theme(self.mode)

    def apply(self, mode: str) -> ThemeTokens:
        if mode not in {"system", "light", "dark"}:
            raise ValueError(f"unsupported theme: {mode}")
        self.mode = mode
        self.tokens = resolve_theme(mode)
        tokens = self.tokens
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=tokens.background, foreground=tokens.foreground)
        style.configure("TFrame", background=tokens.background)
        style.configure("TLabel", background=tokens.background, foreground=tokens.foreground)
        style.configure(
            "Accent.TLabel",
            background=tokens.background,
            foreground=tokens.accent,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "TButton",
            background=tokens.surface,
            foreground=tokens.foreground,
            bordercolor=tokens.border,
            focusthickness=2,
            focuscolor=tokens.focus,
            padding=(tokens.spacing_medium, 7),
        )
        style.map(
            "TButton",
            background=[("active", tokens.surface_alt), ("pressed", tokens.accent)],
            foreground=[("pressed", "#ffffff")],
        )
        style.configure(
            "Accent.TButton",
            background=tokens.accent,
            foreground="#ffffff",
            bordercolor=tokens.accent,
            focusthickness=2,
            focuscolor=tokens.focus,
            padding=(12, 8),
        )
        style.map("Accent.TButton", background=[("active", tokens.accent_hover)])
        style.configure(
            "TEntry",
            fieldbackground=tokens.surface,
            foreground=tokens.foreground,
            bordercolor=tokens.border,
            insertcolor=tokens.foreground,
        )
        style.configure(
            "TCombobox",
            fieldbackground=tokens.surface,
            background=tokens.surface,
            foreground=tokens.foreground,
            arrowcolor=tokens.foreground,
        )
        for control in ("TCheckbutton", "TRadiobutton"):
            style.configure(
                control,
                background=tokens.background,
                foreground=tokens.foreground,
                focusthickness=2,
                focuscolor=tokens.focus,
            )
            style.map(
                control,
                background=[("active", tokens.surface_alt)],
                foreground=[("disabled", tokens.muted)],
            )
        style.configure(
            "TScale",
            background=tokens.background,
            troughcolor=tokens.surface_alt,
            bordercolor=tokens.border,
        )
        style.configure(
            "TScrollbar",
            background=tokens.surface_alt,
            troughcolor=tokens.background,
            bordercolor=tokens.border,
            arrowcolor=tokens.foreground,
        )
        style.configure("TSeparator", background=tokens.border)
        style.configure(
            "TLabelframe",
            background=tokens.background,
            foreground=tokens.foreground,
            bordercolor=tokens.border,
        )
        style.configure(
            "TLabelframe.Label", background=tokens.background, foreground=tokens.foreground
        )
        style.configure("Panel.TLabelframe", padding=tokens.spacing_large)
        style.configure("TNotebook", background=tokens.background, bordercolor=tokens.border)
        style.configure(
            "TNotebook.Tab",
            background=tokens.surface_alt,
            foreground=tokens.foreground,
            padding=(12, 7),
        )
        style.map("TNotebook.Tab", background=[("selected", tokens.surface)])
        style.configure(
            "Treeview",
            background=tokens.surface,
            fieldbackground=tokens.surface,
            foreground=tokens.foreground,
        )
        style.configure(
            "Treeview.Heading",
            background=tokens.surface_alt,
            foreground=tokens.foreground,
        )
        style.map(
            "Treeview",
            background=[("selected", tokens.accent)],
            foreground=[("selected", "#ffffff")],
        )
        try:
            self.root.configure({"background": tokens.background})
        except tk.TclError:
            pass
        self._apply_classic_widgets(self.root)
        return tokens

    def _apply_classic_widgets(self, widget: tk.Misc) -> None:
        tokens = self.tokens
        try:
            if isinstance(widget, tk.Canvas):
                widget.configure(
                    background=tokens.surface,
                    highlightbackground=tokens.border,
                    highlightcolor=tokens.focus,
                )
            elif isinstance(widget, tk.Listbox):
                widget.configure(
                    background=tokens.surface,
                    foreground=tokens.foreground,
                    selectbackground=tokens.accent,
                    selectforeground="#ffffff",
                    highlightcolor=tokens.focus,
                )
            elif isinstance(widget, tk.Menu):
                widget.configure(
                    background=tokens.surface,
                    foreground=tokens.foreground,
                    activebackground=tokens.accent,
                    activeforeground="#ffffff",
                )
        except tk.TclError:
            pass
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            self._apply_classic_widgets(child)
