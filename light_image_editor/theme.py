"""Application colour, spacing and focus design tokens."""

from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk
from typing import Literal


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
    canvas: str
    surface_container: str
    surface_high: str
    on_accent: str
    spacing_small: int = 6
    spacing_medium: int = 10
    spacing_large: int = 16


LIGHT_TOKENS = ThemeTokens(
    "light",
    "#e6e4ec",
    "#f7f2fa",
    "#eee8f0",
    "#302d33",
    "#68616c",
    "#67507a",
    "#785f8b",
    "#ddd8df",
    "#67507a",
    "#b3261e",
    "#ddd9df",
    "#eee8f0",
    "#e0d7e4",
    "#ffffff",
)
DARK_TOKENS = ThemeTokens(
    "dark",
    "#101114",
    "#15171a",
    "#202329",
    "#f4f5f6",
    "#979da5",
    "#f1f3f5",
    "#ffffff",
    "#30343a",
    "#f1f3f5",
    "#f2b8b5",
    "#060708",
    "#181a1e",
    "#30343a",
    "#141619",
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
        self._configure_named_fonts()
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            ".",
            background=tokens.background,
            foreground=tokens.foreground,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure("TFrame", background=tokens.background)
        style.configure("Surface.TFrame", background=tokens.surface)
        style.configure("CanvasShell.TFrame", background=tokens.canvas)
        style.configure("AppBar.TFrame", background=tokens.surface)
        style.configure("FloatingBar.TFrame", background=tokens.surface)
        style.configure("Toolbar.TFrame", background=tokens.surface)
        style.configure("ToolShelf.TFrame", background=tokens.surface_container)
        style.configure("Dock.TFrame", background=tokens.surface_alt)
        style.configure("InspectorHeader.TFrame", background=tokens.surface)
        style.configure("TLabel", background=tokens.surface, foreground=tokens.foreground)
        style.configure("Surface.TLabel", background=tokens.surface, foreground=tokens.foreground)
        style.configure("Toolbar.TLabel", background=tokens.surface, foreground=tokens.foreground)
        style.configure(
            "BrandMark.TLabel",
            background=tokens.surface,
            foreground=tokens.accent,
            font=("Segoe UI Symbol", 22, "bold"),
        )
        style.configure(
            "Eyebrow.TLabel",
            background=tokens.surface,
            foreground=tokens.muted,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Title.TLabel",
            background=tokens.surface,
            foreground=tokens.foreground,
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        style.configure("Muted.Toolbar.TLabel", background=tokens.surface, foreground=tokens.muted)
        style.configure(
            "InspectorTitle.TLabel",
            background=tokens.surface,
            foreground=tokens.foreground,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure(
            "LibraryTitle.TLabel",
            background=tokens.surface,
            foreground=tokens.foreground,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        style.configure(
            "LibraryMeta.TLabel",
            background=tokens.surface,
            foreground=tokens.muted,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "CanvasMeta.TLabel",
            background=tokens.surface_container,
            foreground=tokens.muted,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "ViewerStatus.TLabel",
            background=tokens.canvas,
            foreground=tokens.muted,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Accent.TLabel",
            background=tokens.background,
            foreground=tokens.accent,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.configure(
            "TButton",
            background=tokens.surface_container,
            foreground=tokens.foreground,
            bordercolor=tokens.surface_container,
            relief="flat",
            borderwidth=0,
            focusthickness=2,
            focuscolor=tokens.focus,
            padding=(12, 8),
        )
        style.map(
            "TButton",
            background=[("active", tokens.surface_high), ("pressed", tokens.surface_alt)],
            foreground=[("pressed", tokens.foreground)],
        )
        style.configure(
            "Accent.TButton",
            background=tokens.accent,
            foreground=tokens.on_accent,
            bordercolor=tokens.accent,
            relief="flat",
            borderwidth=0,
            focusthickness=2,
            focuscolor=tokens.focus,
            padding=(16, 9),
        )
        style.map("Accent.TButton", background=[("active", tokens.accent_hover)])
        style.configure(
            "WideAccent.TButton",
            background=tokens.accent,
            foreground=tokens.on_accent,
            bordercolor=tokens.accent,
            borderwidth=0,
            relief="flat",
            padding=(18, 11),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("WideAccent.TButton", background=[("active", tokens.accent_hover)])
        style.configure(
            "Icon.TButton",
            background=tokens.surface_container,
            foreground=tokens.foreground,
            borderwidth=0,
            relief="flat",
            padding=(11, 7),
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        style.map("Icon.TButton", background=[("active", tokens.surface_high)])
        style.configure(
            "FloatingIcon.TButton",
            background=tokens.surface,
            foreground=tokens.foreground,
            borderwidth=0,
            relief="flat",
            padding=(7, 6),
            font=("Segoe UI Symbol", 11),
        )
        style.map("FloatingIcon.TButton", background=[("active", tokens.surface_high)])
        style.configure(
            "FloatingIconActive.TButton",
            background=tokens.surface_alt,
            foreground=tokens.accent,
            borderwidth=0,
            relief="flat",
            padding=(7, 6),
            font=("Segoe UI Symbol", 11, "bold"),
        )
        style.map(
            "FloatingIconActive.TButton",
            background=[("active", tokens.surface_high)],
        )
        style.configure(
            "Quiet.TButton",
            background=tokens.surface,
            foreground=tokens.foreground,
            borderwidth=0,
            relief="flat",
            padding=(10, 7),
        )
        style.map("Quiet.TButton", background=[("active", tokens.surface_container)])
        style.configure(
            "Dock.TButton",
            background=tokens.surface_container,
            foreground=tokens.foreground,
            borderwidth=0,
            relief="flat",
            padding=(7, 7),
        )
        style.map(
            "Dock.TButton",
            background=[("active", tokens.surface_high), ("pressed", tokens.surface_alt)],
        )
        style.configure(
            "DockAccent.TButton",
            background=tokens.accent,
            foreground=tokens.on_accent,
            borderwidth=0,
            relief="flat",
            padding=(9, 7),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map("DockAccent.TButton", background=[("active", tokens.accent_hover)])
        style.configure(
            "ToolShelf.TButton",
            background=tokens.surface_container,
            foreground=tokens.foreground,
            borderwidth=0,
            relief="flat",
            padding=(9, 7),
        )
        style.map("ToolShelf.TButton", background=[("active", tokens.surface_high)])
        style.configure(
            "ToolShelfActive.TButton",
            background=tokens.surface_alt,
            foreground=tokens.accent,
            bordercolor=tokens.accent,
            borderwidth=1,
            relief="solid",
            padding=(9, 7),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "ToolShelfActive.TButton",
            background=[("active", tokens.surface_high)],
        )
        style.configure(
            "InspectorTab.TButton",
            background=tokens.surface,
            foreground=tokens.muted,
            borderwidth=0,
            relief="flat",
            padding=(10, 7),
        )
        style.map(
            "InspectorTab.TButton",
            background=[("active", tokens.surface_container)],
            foreground=[("active", tokens.foreground)],
        )
        style.configure(
            "InspectorTabActive.TButton",
            background=tokens.surface_alt,
            foreground=tokens.accent,
            bordercolor=tokens.accent,
            borderwidth=0,
            relief="flat",
            padding=(11, 7),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "InspectorTabActive.TButton",
            background=[("active", tokens.surface_high)],
        )
        style.configure(
            "FilterChip.TButton",
            background=tokens.surface_container,
            foreground=tokens.foreground,
            bordercolor=tokens.border,
            borderwidth=1,
            relief="solid",
            padding=(12, 8),
        )
        style.map(
            "FilterChip.TButton",
            background=[("active", tokens.surface_high), ("pressed", tokens.surface_alt)],
            foreground=[("pressed", tokens.accent)],
        )
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
                background=tokens.surface,
                foreground=tokens.foreground,
                focusthickness=2,
                focuscolor=tokens.focus,
            )
            style.map(
                control,
                background=[("active", tokens.surface_alt)],
                foreground=[("disabled", tokens.muted)],
            )
        for control in ("Toolbar.TCheckbutton", "ToolShelf.TCheckbutton", "Dock.TCheckbutton"):
            background = (
                tokens.surface if control == "Toolbar.TCheckbutton" else tokens.surface_container
            )
            style.configure(
                control,
                background=background,
                foreground=tokens.foreground,
                focusthickness=2,
                focuscolor=tokens.focus,
                padding=(7, 6),
            )
            style.map(
                control,
                background=[("active", tokens.surface_high)],
                foreground=[("disabled", tokens.muted)],
            )
        style.configure(
            "TScale",
            background=tokens.surface,
            troughcolor=tokens.surface_high,
            bordercolor=tokens.border,
            lightcolor=tokens.accent,
            darkcolor=tokens.accent,
        )
        style.configure(
            "TScrollbar",
            background=tokens.surface_alt,
            troughcolor=tokens.background,
            bordercolor=tokens.border,
            arrowcolor=tokens.foreground,
        )
        style.configure(
            "Vertical.TScrollbar",
            width=7,
            arrowsize=0,
            background=tokens.surface_high,
            troughcolor=tokens.surface,
            bordercolor=tokens.surface,
            arrowcolor=tokens.surface,
        )
        style.configure("TSeparator", background=tokens.border)
        style.configure(
            "TLabelframe",
            background=tokens.surface,
            foreground=tokens.foreground,
            bordercolor=tokens.border,
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label", background=tokens.surface, foreground=tokens.foreground
        )
        style.configure(
            "Panel.TLabelframe",
            background=tokens.surface,
            bordercolor=tokens.surface,
            relief="flat",
            borderwidth=0,
            padding=(4, tokens.spacing_medium, 4, tokens.spacing_large),
        )
        style.configure(
            "Panel.TLabelframe.Label",
            background=tokens.surface,
            foreground=tokens.foreground,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
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

    def _configure_named_fonts(self) -> None:
        specs: dict[str, tuple[str, int, Literal["normal", "bold"]]] = {
            "TkDefaultFont": ("Microsoft YaHei UI", 10, "normal"),
            "TkTextFont": ("Microsoft YaHei UI", 10, "normal"),
            "TkMenuFont": ("Microsoft YaHei UI", 10, "normal"),
            "TkHeadingFont": ("Microsoft YaHei UI", 10, "bold"),
            "TkCaptionFont": ("Microsoft YaHei UI", 10, "bold"),
            "TkSmallCaptionFont": ("Microsoft YaHei UI", 9, "normal"),
            "TkIconFont": ("Microsoft YaHei UI", 10, "normal"),
            "TkTooltipFont": ("Microsoft YaHei UI", 9, "normal"),
            "TkFixedFont": ("Consolas", 10, "normal"),
        }
        for name, (family, size, weight) in specs.items():
            try:
                tkfont.nametofont(name, root=self.root).configure(
                    family=family,
                    size=size,
                    weight=weight,
                )
            except tk.TclError:
                pass

    def _apply_classic_widgets(self, widget: tk.Misc) -> None:
        tokens = self.tokens
        try:
            widget_type = widget.__class__.__name__
            if widget_type == "MaterialTitleBar":
                getattr(widget, "apply_palette")(
                    surface=tokens.surface,
                    foreground=tokens.foreground,
                    muted=tokens.muted,
                    accent=tokens.accent,
                    hover=tokens.surface_high,
                    border=tokens.border,
                    danger=tokens.danger,
                    on_danger=tokens.on_accent,
                )
            elif widget_type == "MaterialWindowButton":
                getattr(widget, "apply_palette")(
                    surface=tokens.surface,
                    hover=tokens.surface_high,
                    foreground=tokens.foreground,
                    danger=tokens.danger,
                    on_danger=tokens.on_accent,
                )
            elif widget_type == "MaterialSlider":
                getattr(widget, "apply_palette")(
                    surface=tokens.surface,
                    track=tokens.surface_high,
                    accent=tokens.accent,
                    outline=tokens.muted,
                )
            elif widget_type == "MaterialLookStrip":
                getattr(widget, "apply_palette")(
                    surface=tokens.surface,
                    accent=tokens.accent,
                    muted=tokens.muted,
                    border=tokens.border,
                )
            elif widget_type == "MaterialButton":
                role = str(getattr(widget, "material_role", ""))
                if role == "viewer-navigation-overlay":
                    getattr(widget, "apply_palette")(
                        surface=tokens.canvas,
                        container=tokens.surface_container,
                        hover=tokens.canvas,
                        foreground=tokens.muted,
                        accent=tokens.foreground,
                        on_accent=tokens.on_accent,
                    )
                elif role == "viewer-toolbar":
                    getattr(widget, "apply_palette")(
                        surface=tokens.canvas,
                        container=tokens.surface_high,
                        hover=tokens.surface_high,
                        foreground=tokens.foreground,
                        accent=tokens.accent,
                        on_accent=tokens.on_accent,
                    )
                else:
                    getattr(widget, "apply_palette")(
                        surface=tokens.surface,
                        container=tokens.surface_container,
                        hover=tokens.surface_high,
                        foreground=tokens.foreground,
                        accent=tokens.accent,
                        on_accent=tokens.on_accent,
                    )
            elif widget_type == "MaterialTabStrip":
                getattr(widget, "apply_palette")(
                    surface=tokens.surface,
                    foreground=tokens.foreground,
                    muted=tokens.muted,
                    accent=tokens.accent,
                )
            elif widget_type == "MaterialSearchField":
                getattr(widget, "apply_palette")(
                    background=tokens.surface,
                    surface=tokens.surface_alt,
                    border=tokens.border,
                    foreground=tokens.foreground,
                    muted=tokens.muted,
                )
            elif isinstance(widget, tk.Canvas):
                role = str(getattr(widget, "material_role", ""))
                background = (
                    tokens.canvas if role in {"editor-canvas", "histogram"} else tokens.surface
                )
                widget.configure(
                    background=background,
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
