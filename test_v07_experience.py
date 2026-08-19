from __future__ import annotations

import ctypes
import json
import os
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch

from light_image_editor.dpi import (
    WorkArea,
    clamp_geometry,
    dpi_scale,
    fit_geometry_to_work_area,
    monitor_work_area,
    parse_geometry,
)
from light_image_editor.i18n import SUPPORTED_LOCALES, Localizer
from light_image_editor.privacy import (
    BACKGROUND_UPLOADS_ENABLED,
    NETWORK_POLICY,
    UPDATE_CHECK_MODE,
)
from light_image_editor.settings import SettingsStore
from light_image_editor.theme import DARK_TOKENS, LIGHT_TOKENS, resolve_theme
from light_image_editor.ui.main_window import ImageEditorApp
from light_image_editor.windows_drop import WindowsFileDrop

ROOT = Path(__file__).resolve().parent


def descendants(widget: tk.Misc) -> list[tk.Misc]:
    result: list[tk.Misc] = []
    for child in widget.winfo_children():
        result.append(child)
        result.extend(descendants(child))
    return result


class LocalisationTests(unittest.TestCase):
    def test_only_simplified_chinese_catalog_is_shipped(self) -> None:
        locale_dir = ROOT / "resources" / "locales"
        catalog = json.loads((locale_dir / "zh-CN.json").read_text(encoding="utf-8"))
        self.assertEqual(SUPPORTED_LOCALES, ("zh-CN",))
        self.assertEqual(catalog["locale"], "zh-CN")
        self.assertFalse((locale_dir / "en-US.json").exists())

    def test_unsupported_locale_falls_back_and_cannot_be_selected(self) -> None:
        localizer = Localizer("en-US", ROOT / "resources" / "locales")
        self.assertEqual(localizer.locale, "zh-CN")
        self.assertEqual(localizer.translate_text("打开"), "打开")
        self.assertEqual(localizer.gettext("settings_title"), "设置")
        with self.assertRaises(ValueError):
            localizer.set_locale("en-US")


class ThemeAndDpiTests(unittest.TestCase):
    def test_light_dark_and_system_theme_tokens_are_complete(self) -> None:
        self.assertEqual(resolve_theme("light"), LIGHT_TOKENS)
        self.assertEqual(resolve_theme("dark"), DARK_TOKENS)
        self.assertIn(resolve_theme("system"), {LIGHT_TOKENS, DARK_TOKENS})
        self.assertNotEqual(LIGHT_TOKENS.background, DARK_TOKENS.background)
        self.assertNotEqual(LIGHT_TOKENS.focus, LIGHT_TOKENS.background)
        self.assertNotEqual(DARK_TOKENS.focus, DARK_TOKENS.background)

    def test_supported_dpi_scales_and_multimonitor_geometry(self) -> None:
        self.assertEqual([dpi_scale(value) for value in (96, 120, 144, 192)], [1, 1.25, 1.5, 2])
        left_monitor = WorkArea(-1920, 0, 0, 1080)
        self.assertEqual(
            clamp_geometry(1240, 780, -1800, 80, left_monitor),
            (1240, 780, -1800, 80),
        )
        width, height, x, y = clamp_geometry(2400, 1400, 5000, -900, left_monitor)
        self.assertLessEqual(width, left_monitor.width - 32)
        self.assertLessEqual(height, left_monitor.height - 32)
        self.assertGreaterEqual(x, left_monitor.left + 16)
        self.assertGreaterEqual(y, left_monitor.top + 16)
        self.assertEqual(parse_geometry("1240x780+-1800+80"), (1240, 780, -1800, 80))
        self.assertEqual(
            fit_geometry_to_work_area("2400x1400+5000+-900", left_monitor),
            "1888x1048+-1904+16",
        )
        self.assertIsNone(fit_geometry_to_work_area("invalid", left_monitor))


class OfflinePrivacyTests(unittest.TestCase):
    def test_application_policy_is_explicitly_offline_and_manual(self) -> None:
        self.assertEqual(NETWORK_POLICY, "offline")
        self.assertFalse(BACKGROUND_UPLOADS_ENABLED)
        self.assertEqual(UPDATE_CHECK_MODE, "manual")

    def test_application_has_no_background_network_client(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "light_image_editor").rglob("*.py")
            # Updater, image-model calls and local-AI engine/model downloads are
            # explicit user actions, not background clients. Their own tests
            # cover consent, dispatch and lazy loading.
            if path.name not in {"updater.py", "image_model_api.py", "local_ai_tools.py"}
        )
        for forbidden in ("import requests", "from requests", "urllib.request", "http.client"):
            self.assertNotIn(forbidden, source)


@unittest.skipUnless(os.name == "nt", "real Tk integration is Windows-specific")
class ExperienceUiTests(unittest.TestCase):
    def test_chinese_only_settings_keyboard_and_accessible_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(Path(directory) / "settings.json")
            settings.save({"onboarding_completed": True, "locale": "en-US"})
            app = ImageEditorApp(settings_store=settings)
            try:
                app.update_idletasks()
                self.assertEqual(app.localizer.locale, "zh-CN")
                self.assertFalse(hasattr(app, "locale_var"))
                self.assertEqual(app.title(), "CSImage")
                self.assertEqual(str(app.canvas.cget("takefocus")), "1")
                self.assertEqual(
                    getattr(app.canvas, "accessible_name"),
                    "图片预览与编辑画布",
                )
                with patch.object(app, "open_file") as open_file:
                    app.apply_current_tool()
                    open_file.assert_called_once_with()

                app.open_settings()
                app.update_idletasks()
                settings_window = next(
                    child for child in app.winfo_children() if isinstance(child, tk.Toplevel)
                )
                widgets = descendants(settings_window)
                labels = [
                    str(widget.cget("text")) for widget in widgets if isinstance(widget, ttk.Label)
                ]
                self.assertNotIn("界面语言", labels)
                self.assertFalse(
                    any(
                        "English" in tuple(widget.cget("values"))
                        for widget in widgets
                        if isinstance(widget, ttk.Combobox)
                    )
                )
                apply_button = next(
                    widget
                    for widget in widgets
                    if isinstance(widget, ttk.Button) and widget.cget("text") == "应用"
                )
                apply_button.invoke()
                app.update_idletasks()
                self.assertEqual(app.localizer.locale, "zh-CN")
                self.assertEqual(app.title(), "CSImage")
                self.assertEqual(
                    getattr(app.canvas, "accessible_name"),
                    "图片预览与编辑画布",
                )
                self.assertNotIn("locale", settings.load())
                self.assertEqual(app.focus_navigator.move(), "break")

                app.show_shortcut_help()
                app.update_idletasks()
                help_window = next(
                    child
                    for child in app.winfo_children()
                    if isinstance(child, tk.Toplevel) and child.title() == "快捷键帮助"
                )
                geometry = parse_geometry(help_window.geometry())
                self.assertIsNotNone(geometry)
                assert geometry is not None
                work = monitor_work_area(app)
                self.assertLessEqual(geometry[0], work.width)
                self.assertLessEqual(geometry[1], work.height)
                help_window.destroy()
            finally:
                if app.winfo_exists():
                    app._shutdown_services()
                    app.destroy()


class PackagingTests(unittest.TestCase):
    def test_both_windows_builds_ship_chinese_locale_resource(self) -> None:
        for name in ("image_editor.spec", "image_editor_portable.spec"):
            source = (ROOT / "packaging" / "windows" / name).read_text(encoding="utf-8")
            self.assertIn('"resources/locales"', source)
            self.assertIn("(str(LOCALES)", source)


@unittest.skipUnless(os.name == "nt", "WM_DROPFILES is Windows-specific")
class NativeDropTests(unittest.TestCase):
    def test_native_drop_is_marshaled_back_to_the_tk_event_loop(self) -> None:
        class Point(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class DropFiles(ctypes.Structure):
            _fields_ = [
                ("pFiles", ctypes.c_uint32),
                ("point", Point),
                ("non_client", ctypes.c_int32),
                ("wide", ctypes.c_int32),
            ]

        root = tk.Tk()
        root.withdraw()
        received: list[list[Path]] = []
        target = WindowsFileDrop(root, received.append)
        try:
            self.assertTrue(target.enable())
            dropped = Path("D:/包含空格/拖放测试.png")
            names = (str(dropped) + "\0\0").encode("utf-16le")
            header = DropFiles(ctypes.sizeof(DropFiles), Point(), 0, 1)
            total = ctypes.sizeof(header) + len(names)
            kernel32 = ctypes.windll.kernel32
            kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            kernel32.GlobalAlloc.restype = ctypes.c_void_p
            kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            handle = kernel32.GlobalAlloc(0x42, total)
            memory = kernel32.GlobalLock(handle)
            ctypes.memmove(memory, ctypes.byref(header), ctypes.sizeof(header))
            ctypes.memmove(memory + ctypes.sizeof(header), names, len(names))
            kernel32.GlobalUnlock(ctypes.c_void_p(handle))

            user32 = ctypes.windll.user32
            user32.PostMessageW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
            ]
            self.assertTrue(
                user32.PostMessageW(
                    ctypes.c_void_p(target.hwnd),
                    0x0233,
                    ctypes.c_size_t(handle),
                    0,
                )
            )
            deadline = time.monotonic() + 2
            while not received and time.monotonic() < deadline:
                root.update()
                time.sleep(0.01)
            self.assertEqual(received, [[dropped]])
        finally:
            target.disable()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
