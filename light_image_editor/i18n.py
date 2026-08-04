"""External JSON localisation with live Tk widget and variable updates."""

from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path
from typing import Any

SUPPORTED_LOCALES = ("zh-CN", "en-US")
DEFAULT_LOCALE = "zh-CN"


def locale_directory() -> Path:
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return bundled / "resources" / "locales"


class Localizer:
    def __init__(
        self,
        locale: str = DEFAULT_LOCALE,
        resource_dir: str | Path | None = None,
    ) -> None:
        self.resource_dir = Path(resource_dir) if resource_dir else locale_directory()
        self.catalogs = {name: self._load(name) for name in SUPPORTED_LOCALES}
        self.locale = locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE
        self._widget_sources: dict[str, str] = {}
        self._menu_sources: dict[tuple[str, int], str] = {}
        self._variables: dict[str, tk.StringVar] = {}
        self._variable_sources: dict[str, str] = {}
        self._variable_traces: dict[str, str] = {}
        self._updating_variables = False

    def _load(self, locale: str) -> dict[str, Any]:
        path = self.resource_dir / f"{locale}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("locale") != locale or not isinstance(payload.get("strings"), dict):
            raise ValueError(f"无效的语言资源：{path}")
        payload.setdefault("replacements", {})
        return payload

    @property
    def display_name(self) -> str:
        return str(self.catalogs[self.locale].get("display_name", self.locale))

    def gettext(self, key: str, **values: object) -> str:
        strings = self.catalogs[self.locale]["strings"]
        fallback = self.catalogs[DEFAULT_LOCALE]["strings"]
        template = str(strings.get(key, fallback.get(key, key)))
        return template.format(**values)

    def translate_text(self, text: str) -> str:
        if not text:
            return text
        source_locale = "en-US" if self.locale == DEFAULT_LOCALE else DEFAULT_LOCALE
        source_catalog = self.catalogs[source_locale]
        target_catalog = self.catalogs[self.locale]
        source = {
            **source_catalog.get("strings", {}),
            **source_catalog.get("replacements", {}),
        }
        target = {
            **target_catalog.get("strings", {}),
            **target_catalog.get("replacements", {}),
        }
        exact_key = next((key for key, value in source.items() if value == text), None)
        if exact_key is not None:
            return str(target.get(exact_key, text))
        translated = text
        pairs = sorted(
            (
                (str(value), str(target.get(key, value)))
                for key, value in source.items()
                if value and target.get(key) is not None
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for original, replacement in pairs:
            translated = translated.replace(original, replacement)
        return translated

    def set_locale(self, locale: str, root: tk.Misc | None = None) -> None:
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(f"unsupported locale: {locale}")
        self.locale = locale
        self._refresh_variables()
        if root is not None:
            self.apply_widget_tree(root)
            menu = root.cget("menu") if "menu" in root.keys() else ""
            if menu:
                try:
                    self.apply_menu(root.nametowidget(menu))
                except (KeyError, tk.TclError):
                    pass

    def register_variable(self, variable: tk.StringVar) -> None:
        name = str(variable)
        if name in self._variables:
            return
        self._variables[name] = variable
        self._variable_sources[name] = variable.get()

        def changed(*_args: object, variable_name: str = name) -> None:
            if self._updating_variables:
                return
            current = self._variables[variable_name].get()
            self._variable_sources[variable_name] = current
            self._set_variable(variable_name, self.translate_text(current))

        self._variable_traces[name] = variable.trace_add("write", changed)
        self._set_variable(name, self.translate_text(self._variable_sources[name]))

    def _set_variable(self, name: str, value: str) -> None:
        self._updating_variables = True
        try:
            self._variables[name].set(value)
        finally:
            self._updating_variables = False

    def _refresh_variables(self) -> None:
        for name, source in self._variable_sources.items():
            self._set_variable(name, self.translate_text(source))

    def apply_widget_tree(self, widget: tk.Misc) -> None:
        self._apply_widget(widget)
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            if isinstance(child, tk.Menu):
                self.apply_menu(child)
            else:
                self.apply_widget_tree(child)

    def _apply_widget(self, widget: tk.Misc) -> None:
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            try:
                title_key = f"{widget}#title"
                title = widget.title()
                source_title = self._widget_sources.setdefault(title_key, title)
                widget.title(self.translate_text(source_title))
            except tk.TclError:
                pass
        try:
            if "text" not in widget.keys():
                return
            current = str(widget.cget("text"))
        except tk.TclError:
            return
        path = str(widget)
        source = self._widget_sources.setdefault(path, current)
        try:
            widget.configure({"text": self.translate_text(source)})
        except tk.TclError:
            pass

    def apply_menu(self, menu: tk.Menu) -> None:
        try:
            last = menu.index("end")
        except tk.TclError:
            return
        if last is None:
            return
        for index in range(last + 1):
            try:
                entry_type = menu.type(index)
                if entry_type == "separator":
                    continue
                label = str(menu.entrycget(index, "label"))
                key = (str(menu), index)
                source = self._menu_sources.setdefault(key, label)
                menu.entryconfigure(index, label=self.translate_text(source))
                if entry_type == "cascade":
                    child_name = menu.entrycget(index, "menu")
                    if child_name:
                        self.apply_menu(menu.nametowidget(child_name))
            except (KeyError, tk.TclError):
                continue

    def bind_mapped_windows(self, root: tk.Misc) -> None:
        def mapped(event: tk.Event) -> None:
            widget = event.widget
            try:
                widget.after_idle(lambda: self.apply_widget_tree(widget))
            except tk.TclError:
                pass

        root.bind_all("<Map>", mapped, add="+")
