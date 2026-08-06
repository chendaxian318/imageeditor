from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import cast

from PIL import ImageDraw

from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.shell import ModeId
from light_image_editor.theme import DARK_TOKENS, LIGHT_TOKENS
from light_image_editor.ui.antialias import render_antialiased
from light_image_editor.ui.main_window import ImageEditorApp
from light_image_editor.ui.material import rounded_rectangle_points


class MaterialYouTokenTests(unittest.TestCase):
    def test_material_roles_are_complete_for_light_and_dark_modes(self) -> None:
        self.assertEqual(LIGHT_TOKENS.accent, "#67507a")
        self.assertEqual(DARK_TOKENS.accent, "#d0bcff")
        for tokens in (LIGHT_TOKENS, DARK_TOKENS):
            self.assertTrue(tokens.canvas.startswith("#"))
            self.assertTrue(tokens.surface_container.startswith("#"))
            self.assertTrue(tokens.surface_high.startswith("#"))
            self.assertNotEqual(tokens.accent, tokens.background)
            self.assertNotEqual(tokens.on_accent, tokens.accent)

    def test_capsule_geometry_has_bounded_symmetric_points(self) -> None:
        points = rounded_rectangle_points(10, 5, 210, 65, 30)
        self.assertEqual(len(points), 24)
        xs = points[::2]
        ys = points[1::2]
        self.assertEqual((min(xs), max(xs)), (10, 210))
        self.assertEqual((min(ys), max(ys)), (5, 65))

    def test_supersampled_rounding_keeps_transitional_edge_pixels(self) -> None:
        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            draw.rounded_rectangle(
                (2 * scale, 2 * scale, 22 * scale, 22 * scale),
                radius=8 * scale,
                fill="#67507a",
            )

        image = render_antialiased(24, 24, paint)
        alpha_values = {
            cast(tuple[int, int, int, int], image.getpixel((x, y)))[3]
            for y in range(image.height)
            for x in range(image.width)
        }

        self.assertIn(0, alpha_values)
        self.assertIn(255, alpha_values)
        self.assertTrue(any(0 < alpha < 255 for alpha in alpha_values))


class MaterialYouLayoutTests(unittest.TestCase):
    def test_primary_window_uses_material_workbench_and_bottom_dock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.save(
                {
                    "schema_version": 1,
                    "onboarding_completed": True,
                    "theme_mode": "light",
                }
            )
            app = ImageEditorApp(
                settings_store=settings,
                session_store=SessionStore(root / "session.json"),
            )
            try:
                for job in app._startup_jobs:
                    app.after_cancel(job)
                app._startup_jobs.clear()
                app.update_idletasks()
                app.update()

                self.assertEqual(str(app.command_dock.place_info()["in"]), str(app))
                self.assertEqual(app.rounded_window_effect.radius, 28)
                if app.window_controller is not None:
                    self.assertTrue(bool(app.overrideredirect()))
                    self.assertIsNotNone(app.window_titlebar)
                    assert app.window_titlebar is not None
                    self.assertTrue(app.window_titlebar.winfo_ismapped())
                    self.assertEqual(
                        app.window_titlebar.close_button.accessible_name,
                        "关闭窗口",
                    )
                self.assertEqual(app.command_dock.place_info()["anchor"], "s")
                self.assertEqual(app.status_label.master, app.canvas_frame)
                self.assertEqual(app.status_label.place_info()["anchor"], "sw")
                self.assertEqual(int(app.thumbnail_sidebar.grid_info()["column"]), 0)
                self.assertEqual(int(app.thumbnail_sidebar.grid_info()["row"]), 2)
                self.assertTrue(app.edit_panel_visible_var.get())
                self.assertFalse(app.panel_shell.winfo_ismapped())
                self.assertTrue(app.mode_bar.winfo_ismapped())
                self.assertEqual(app.mode_router.active_mode, ModeId.VIEWER)
                self.assertEqual(set(app.mode_buttons), set(ModeId))
                self.assertTrue(app.mode_buttons[ModeId.VIEWER].selected)
                app._activate_mode(ModeId.EDIT)
                app.update_idletasks()
                self.assertEqual(int(app.panel_shell.grid_info()["column"]), 2)
                self.assertEqual(int(app.panel_shell.grid_info()["row"]), 2)
                self.assertTrue(app.panel_shell.winfo_ismapped())
                app._activate_mode(ModeId.VIEWER)
                app.update_idletasks()
                self.assertFalse(app.panel_shell.winfo_ismapped())
                self.assertFalse(app.toolbar.winfo_ismapped())
                self.assertLessEqual(abs(app.thumbnail_sidebar.winfo_width() - 220), 3)
                self.assertLessEqual(abs(app.panel_shell.winfo_width() - 296), 3)
                dock_center = app.command_dock.winfo_x() + app.command_dock.winfo_width() / 2
                self.assertLessEqual(abs(dock_center - app.winfo_width() / 2), 3)
                self.assertEqual(app.canvas.cget("background"), LIGHT_TOKENS.canvas)
                self.assertFalse(hasattr(app, "edit_toolbar"))
                self.assertEqual(app.active_inspector_tab, "adjust")
                self.assertEqual(app.inspector_tabs.selected, "adjust")
                self.assertEqual(app.thumbnail_sidebar.title_var.get(), "资源库")
                self.assertTrue(app.thumbnail_sidebar.search_entry.winfo_ismapped())

                labels = {
                    str(child.text)
                    for child in app.command_dock.content.winfo_children()
                    if hasattr(child, "text")
                }
                self.assertTrue(
                    {"打开", "保存", "导出", "↶", "↷", "⌗", "分屏", "⛶", "全部功能"} <= labels
                )
                self.assertEqual(app.dock_undo_button.accessible_name, "撤销")
                self.assertEqual(app.dock_redo_button.accessible_name, "重做")
                self.assertEqual(app.dock_crop_button.accessible_name, "裁剪")
                self.assertEqual(app.dock_fullscreen_button.accessible_name, "全屏")
                self.assertEqual(app.export_footer_button.text, "导出图像  Ctrl+E")
                self.assertTrue(
                    all(not widget.winfo_ismapped() for widget in app.detail_advanced_widgets)
                )
                capsule = app.command_dock.bbox("dock_capsule")
                self.assertIsNotNone(capsule)
                assert capsule is not None
                self.assertLessEqual(capsule[2] - capsule[0], 946)
                capsule_items = app.command_dock.find_withtag("dock_capsule")
                self.assertEqual(len(capsule_items), 1)
                self.assertEqual(app.command_dock.type(capsule_items[0]), "image")

                current = app.annotation_tool_var.get()
                self.assertEqual(
                    app.annotation_tool_buttons[current].cget("style"),
                    "ToolShelfActive.TButton",
                )
                app._set_annotation_tool("text")
                self.assertEqual(
                    app.annotation_tool_buttons["text"].cget("style"),
                    "ToolShelfActive.TButton",
                )
                app._show_inspector_tab("annotation")
                app.update_idletasks()
                self.assertEqual(app.mode_router.active_mode, ModeId.EDIT)
                self.assertTrue(app.panel_shell.winfo_ismapped())
                self.assertEqual(app.active_inspector_tab, "annotation")
                self.assertEqual(app.inspector_tabs.selected, "annotation")
                self.assertEqual(
                    app.annotation_tool_buttons[current].cget("style"),
                    "ToolShelf.TButton",
                )

                dark_tokens = app.theme_manager.apply("dark")
                app._apply_thumbnail_palette(dark_tokens)
                app.update_idletasks()
                self.assertEqual(app.canvas.cget("background"), DARK_TOKENS.canvas)
                self.assertEqual(
                    app.command_dock.cget("background"),
                    DARK_TOKENS.surface_container,
                )
                if app.window_controller is not None:
                    assert app.window_titlebar is not None
                    self.assertEqual(
                        app.window_titlebar.cget("background"),
                        DARK_TOKENS.surface,
                    )
                    normal_geometry = app.geometry()
                    app.rounded_window_effect.apply()
                    self.assertIsNotNone(app.rounded_window_effect._last_region)

                    app.window_controller.maximize()
                    app.update_idletasks()
                    app.update()
                    app.rounded_window_effect.apply()
                    self.assertTrue(app.window_controller.maximized)
                    self.assertEqual(app.window_titlebar.maximize_button.kind, "restore")
                    self.assertIsNone(app.rounded_window_effect._last_region)

                    app.window_controller.restore()
                    app.update_idletasks()
                    app.update()
                    app.rounded_window_effect.apply()
                    self.assertFalse(app.window_controller.maximized)
                    self.assertEqual(app.window_titlebar.maximize_button.kind, "maximize")
                    self.assertEqual(app.geometry(), normal_geometry)
                    self.assertIsNotNone(app.rounded_window_effect._last_region)

                    self.assertTrue(app._set_fullscreen_state(True))
                    app.update_idletasks()
                    app.update()
                    self.assertTrue(app.window_controller.fullscreen)
                    self.assertFalse(app.window_titlebar.winfo_ismapped())
                    self.assertTrue(app._set_fullscreen_state(False))
                    app.update_idletasks()
                    app.update()
                    self.assertFalse(app.window_controller.fullscreen)
                    self.assertTrue(app.window_titlebar.winfo_ismapped())
            finally:
                if app.winfo_exists():
                    app._shutdown_services()
                    app.destroy()


if __name__ == "__main__":
    unittest.main()
