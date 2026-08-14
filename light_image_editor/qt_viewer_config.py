"""Persistent settings used by the PySide6 viewer shell."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .settings import SettingsStore

DEFAULT_SHORTCUTS: dict[str, list[str]] = {
    "previous": ["A", "Left"],
    "next": ["D", "Right"],
    "zoom_in": ["W", "Up"],
    "zoom_out": ["S", "Down"],
    "toggle_toolbar": ["H"],
    "toggle_clean": ["Tab"],
    "toggle_topmost": ["P"],
    "toggle_favorite": ["F"],
    "fit": ["0"],
    "actual_size": ["1"],
    "fullscreen": ["F11"],
    "gallery": ["G"],
    "markup_arrow": ["Alt+A"],
    "markup_brush": ["Alt+B"],
}

SHORTCUT_LABELS: dict[str, str] = {
    "previous": "上一张图片",
    "next": "下一张图片",
    "zoom_in": "放大图片",
    "zoom_out": "缩小图片",
    "toggle_toolbar": "显示或隐藏工具栏",
    "toggle_clean": "纯净阅图模式",
    "toggle_topmost": "窗口置顶",
    "toggle_favorite": "收藏当前图片",
    "fit": "适应窗口",
    "actual_size": "原图比例",
    "fullscreen": "全屏",
    "gallery": "图片预览栏",
    "markup_arrow": "编辑：箭头工具",
    "markup_brush": "编辑：画笔工具",
}


def _legacy_shortcut(value: str) -> str:
    token = value.strip()
    if token.startswith("<") and token.endswith(">"):
        token = token[1:-1]
    if token.startswith("Key-"):
        token = token[4:]
    if token.startswith("KP_"):
        token = token[3:]
    aliases = {
        "Control-o": "Ctrl+O",
        "Control-c": "Ctrl+C",
        "Control-z": "Ctrl+Z",
        "Control-y": "Ctrl+Y",
        "Escape": "Esc",
    }
    return aliases.get(token, token)


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        if not isinstance(value, (str, bytes, bytearray, int, float)):
            raise TypeError
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _hex_color(value: object, default: str) -> str:
    text = str(value).strip().lower()
    return text if re.fullmatch(r"#[0-9a-f]{6}", text) else default


def _glow_color(value: object) -> str:
    text = str(value).strip().lower()
    return text if text == "auto" or re.fullmatch(r"#[0-9a-f]{6}", text) else "auto"


def _normalized_position(value: object) -> tuple[float, float] | None:
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    try:
        x = float(value[0])
        y = float(value[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return x, y


@dataclass
class QtViewerConfig:
    """Viewer-only configuration layered over the existing JSON store."""

    window_geometry: str = "1240x780"
    toolbar_visible: bool = True
    toolbar_mode: str = "fixed"
    theme: str = "dark"
    transparency_background: str = "checkerboard"
    transparency_color: str = "#ffffff"
    checker_light: str = "#d9dde3"
    checker_dark: str = "#aeb4bd"
    image_edge_glow: bool = True
    image_edge_glow_color: str = "auto"
    image_edge_glow_intensity: int = 50
    image_edge_glow_spread: int = 7
    navigator_position: tuple[float, float] | None = None
    topmost: bool = False
    zoom_step: int = 10
    zoom_presets: list[int] = field(default_factory=lambda: [25, 50, 75, 100, 125, 150, 200])
    favorite_paths: list[str] = field(default_factory=list)
    shortcuts: dict[str, list[str]] = field(
        default_factory=lambda: {key: list(values) for key, values in DEFAULT_SHORTCUTS.items()}
    )

    @classmethod
    def load(cls, store: SettingsStore) -> tuple[QtViewerConfig, dict[str, Any]]:
        raw = store.load_migrated()
        config = cls()
        config.window_geometry = str(raw.get("window_geometry", config.window_geometry) or "")
        config.toolbar_visible = bool(raw.get("viewer_toolbar_visible", True))
        toolbar_mode = str(raw.get("viewer_toolbar_mode", "fixed"))
        config.toolbar_mode = toolbar_mode if toolbar_mode in {"fixed", "auto"} else "fixed"
        theme = str(raw.get("viewer_theme", "dark"))
        config.theme = theme if theme in {"dark", "light"} else "dark"
        transparency_background = str(raw.get("viewer_transparency_background", "checkerboard"))
        config.transparency_background = (
            transparency_background
            if transparency_background in {"checkerboard", "solid"}
            else "checkerboard"
        )
        config.transparency_color = _hex_color(raw.get("viewer_transparency_color"), "#ffffff")
        config.checker_light = _hex_color(raw.get("viewer_checker_light"), "#d9dde3")
        config.checker_dark = _hex_color(raw.get("viewer_checker_dark"), "#aeb4bd")
        config.image_edge_glow = bool(raw.get("viewer_image_edge_glow", True))
        config.image_edge_glow_color = _glow_color(raw.get("viewer_image_edge_glow_color", "auto"))
        config.image_edge_glow_intensity = _bounded_int(
            raw.get("viewer_image_edge_glow_intensity"), 50, 10, 100
        )
        config.image_edge_glow_spread = _bounded_int(
            raw.get("viewer_image_edge_glow_spread"), 7, 2, 20
        )
        config.navigator_position = _normalized_position(raw.get("viewer_navigator_position"))
        config.topmost = bool(raw.get("viewer_topmost", False))
        config.zoom_step = _bounded_int(raw.get("viewer_zoom_step_percent"), 10, 1, 100)

        presets = raw.get("viewer_zoom_presets")
        if isinstance(presets, list):
            parsed: list[int] = []
            for value in presets:
                text = str(value).strip().removesuffix("%")
                if text in {"适应", "閫傚簲"}:
                    continue
                try:
                    percent = int(text)
                except ValueError:
                    continue
                if 10 <= percent <= 800 and percent not in parsed:
                    parsed.append(percent)
            if parsed:
                config.zoom_presets = parsed[:12]

        favorites = raw.get("viewer_favorite_paths")
        if isinstance(favorites, list):
            config.favorite_paths = list(
                dict.fromkeys(
                    os.path.abspath(str(path))
                    for path in favorites
                    if isinstance(path, str) and path.strip()
                )
            )[:2000]

        source_shortcuts = raw.get("qt_viewer_shortcuts")
        if not isinstance(source_shortcuts, dict):
            source_shortcuts = raw.get("viewer_shortcuts")
        if isinstance(source_shortcuts, dict):
            for action in DEFAULT_SHORTCUTS:
                values = source_shortcuts.get(action)
                if not isinstance(values, list):
                    continue
                normalized = [
                    _legacy_shortcut(str(value))
                    for value in values
                    if isinstance(value, str) and value.strip()
                ]
                if normalized:
                    config.shortcuts[action] = list(dict.fromkeys(normalized))[:6]
                elif not values:
                    config.shortcuts[action] = []
        return config, raw

    def save(self, store: SettingsStore, base: dict[str, Any]) -> None:
        data = dict(base)
        data.update(
            {
                "window_geometry": self.window_geometry,
                "viewer_toolbar_visible": self.toolbar_visible,
                "viewer_toolbar_mode": self.toolbar_mode,
                "viewer_theme": self.theme,
                "viewer_transparency_background": self.transparency_background,
                "viewer_transparency_color": self.transparency_color,
                "viewer_checker_light": self.checker_light,
                "viewer_checker_dark": self.checker_dark,
                "viewer_image_edge_glow": self.image_edge_glow,
                "viewer_image_edge_glow_color": self.image_edge_glow_color,
                "viewer_image_edge_glow_intensity": self.image_edge_glow_intensity,
                "viewer_image_edge_glow_spread": self.image_edge_glow_spread,
                "viewer_navigator_position": (
                    list(self.navigator_position) if self.navigator_position is not None else None
                ),
                "viewer_topmost": self.topmost,
                "viewer_zoom_step_percent": self.zoom_step,
                "viewer_zoom_presets": [f"{value}%" for value in self.zoom_presets],
                "viewer_favorite_paths": self.favorite_paths[:2000],
                "qt_viewer_shortcuts": self.shortcuts,
            }
        )
        store.save(data)

    def is_favorite(self, path: str | Path | None) -> bool:
        if path is None:
            return False
        key = os.path.normcase(os.path.abspath(os.fspath(path)))
        return any(os.path.normcase(os.path.abspath(item)) == key for item in self.favorite_paths)

    def toggle_favorite(self, path: str | Path) -> bool:
        absolute = os.path.abspath(os.fspath(path))
        key = os.path.normcase(absolute)
        if self.is_favorite(absolute):
            self.favorite_paths = [
                item
                for item in self.favorite_paths
                if os.path.normcase(os.path.abspath(item)) != key
            ]
            return False
        self.favorite_paths.insert(0, absolute)
        del self.favorite_paths[2000:]
        return True


def parse_window_geometry(value: str) -> tuple[int, int, int | None, int | None]:
    """Parse Tk-compatible ``WIDTHxHEIGHT+X+Y`` geometry strings."""

    text = value.strip()
    try:
        size, *position = text.split("+")
        width_text, height_text = size.split("x", 1)
        width = _bounded_int(width_text, 1240, 720, 7680)
        height = _bounded_int(height_text, 780, 480, 4320)
        if len(position) >= 2:
            return width, height, int(position[0]), int(position[1])
        return width, height, None, None
    except (TypeError, ValueError):
        return 1240, 780, None, None
