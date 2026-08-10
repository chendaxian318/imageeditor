from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw

from light_image_editor.navigation import NavigationSession
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
        self.assertEqual(DARK_TOKENS.accent, "#f1f3f5")
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
    def test_primary_window_uses_dark_viewer_and_top_command_bar(self) -> None:
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
                self.assertEqual(app.rounded_window_effect.radius, 8)
                if app.window_controller is not None:
                    self.assertTrue(bool(app.overrideredirect()))
                    self.assertIsNotNone(app.window_titlebar)
                    assert app.window_titlebar is not None
                    self.assertTrue(app.window_titlebar.winfo_ismapped())
                    self.assertEqual(
                        set(app.window_controller._resize_handles),
                        {"n", "s", "e", "w", "nw", "ne", "sw", "se"},
                    )
                    self.assertTrue(
                        all(
                            handle.winfo_ismapped()
                            for handle in app.window_controller._resize_handles.values()
                        )
                    )
                    for direction in ("n", "nw", "ne"):
                        self.assertEqual(
                            app.window_controller._resize_handles[direction].cget("background"),
                            DARK_TOKENS.surface,
                        )
                    for direction in ("s", "e", "w", "sw", "se"):
                        self.assertEqual(
                            app.window_controller._resize_handles[direction].cget("background"),
                            DARK_TOKENS.canvas,
                        )
                    self.assertEqual(
                        app.window_titlebar.close_button.accessible_name,
                        "关闭窗口",
                    )
                self.assertEqual(app.command_dock.profile, "top")
                self.assertEqual(app.command_dock.place_info()["anchor"], "n")
                self.assertEqual(
                    app.command_dock.winfo_y(),
                    (app.window_titlebar.winfo_reqheight() if app.window_titlebar else 0) + 6,
                )
                self.assertLessEqual(app.command_dock.winfo_width(), 560)
                self.assertLess(app.command_dock.winfo_width(), app.winfo_width())
                self.assertEqual(app.command_dock.winfo_height(), 56)
                self.assertLessEqual(
                    abs(
                        app.command_dock.winfo_x()
                        + app.command_dock.winfo_width() / 2
                        - app.winfo_width() / 2
                    ),
                    3,
                )
                self.assertEqual(app.status_label.master, app.canvas_frame)
                self.assertFalse(app.status_label.winfo_ismapped())
                self.assertEqual(app.thumbnail_sidebar.master, app)
                self.assertTrue(app.edit_panel_visible_var.get())
                self.assertFalse(app.panel_shell.winfo_ismapped())
                self.assertFalse(app.mode_bar.winfo_ismapped())
                self.assertEqual(app.mode_router.active_mode, ModeId.VIEWER)
                self.assertEqual(set(app.mode_buttons), set(ModeId))
                self.assertTrue(app.mode_buttons[ModeId.VIEWER].selected)
                app._activate_mode(ModeId.EDIT)
                app.update_idletasks()
                self.assertEqual(int(app.panel_shell.grid_info()["column"]), 2)
                self.assertEqual(int(app.panel_shell.grid_info()["row"]), 2)
                self.assertTrue(app.panel_shell.winfo_ismapped())
                self.assertEqual(app.command_dock.profile, "capsule")
                self.assertIsNotNone(app.command_dock._native_region_bounds)
                assert app.command_dock._native_region_bounds is not None
                region_left, _top, region_right, _bottom, _radius = (
                    app.command_dock._native_region_bounds
                )
                self.assertGreater(region_left, 0)
                self.assertLess(region_right, app.command_dock.winfo_width())
                app._activate_mode(ModeId.VIEWER)
                app.update_idletasks()
                self.assertIsNone(app.command_dock._native_region_bounds)
                self.assertFalse(app.panel_shell.winfo_ismapped())
                self.assertFalse(app.toolbar.winfo_ismapped())
                self.assertFalse(app.thumbnail_sidebar.winfo_ismapped())
                self.assertEqual(int(app.grid_columnconfigure(0)["minsize"]), 0)
                self.assertEqual(int(app.grid_columnconfigure(2)["minsize"]), 0)
                self.assertTrue(app.viewer_file_details.winfo_ismapped())
                self.assertTrue(app.viewer_gallery_button.winfo_ismapped())
                self.assertTrue(app.viewer_settings_button.winfo_ismapped())
                self.assertEqual(app.viewer_settings_button.accessible_name, "看图设置")
                self.assertFalse(app.thumbnail_filmstrip.winfo_ismapped())
                self.assertFalse(app._viewer_navigation_visible)
                self.assertEqual(app.viewer_gallery_button.text, "▦ 此文件夹")
                self.assertEqual(app.dock_open_button.variant, "ghost")
                app.viewer_gallery_button.command()
                app.update_idletasks()
                self.assertFalse(app.thumbnail_sidebar.winfo_ismapped())
                self.assertTrue(app.thumbnail_filmstrip.winfo_ismapped())
                self.assertTrue(app.viewer_gallery_button.selected)
                self.assertEqual(
                    app.thumbnail_filmstrip.cget("background"),
                    DARK_TOKENS.canvas,
                )
                self.assertEqual(
                    int(app.thumbnail_filmstrip.cget("highlightthickness")),
                    0,
                )
                app.viewer_gallery_button.command()
                app.update_idletasks()
                self.assertFalse(app.thumbnail_sidebar.winfo_ismapped())
                self.assertFalse(app.thumbnail_filmstrip.winfo_ismapped())
                self.assertFalse(app.viewer_gallery_button.selected)
                dock_center = app.command_dock.winfo_x() + app.command_dock.winfo_width() / 2
                self.assertLessEqual(abs(dock_center - app.winfo_width() / 2), 3)
                self.assertEqual(app.canvas.cget("background"), DARK_TOKENS.canvas)
                self.assertEqual(int(app.canvas.cget("highlightthickness")), 0)
                self.assertFalse(hasattr(app, "edit_toolbar"))
                self.assertEqual(app.active_inspector_tab, "adjust")
                self.assertEqual(app.inspector_tabs.selected, "adjust")
                self.assertEqual(app.thumbnail_sidebar.title_var.get(), "资源库")
                self.assertFalse(app.thumbnail_sidebar.search_entry.winfo_ismapped())
                self.assertFalse(app.favorite_toolbar.winfo_ismapped())
                self.assertFalse(app.viewer_open_folder_button.winfo_ismapped())
                self.assertTrue(
                    all(
                        not widget.winfo_ismapped()
                        for widget in app._viewer_secondary_action_widgets
                    )
                )

                labels = {
                    str(child.text)
                    for child in app.command_dock.content.winfo_children()
                    if hasattr(child, "text") and child.winfo_ismapped()
                }
                self.assertEqual(
                    labels,
                    {"打开", "−", "100%", "＋", "适窗", "↻", "☆", "✎", "⛶"},
                )
                self.assertEqual(
                    app.dock_markup_button.accessible_name,
                    "轻标注：矩形框与指向箭头",
                )
                self.assertEqual(app.dock_actual_size_button.text, "100%")
                self.assertEqual(
                    app.dock_actual_size_button.accessible_name,
                    "立即恢复到 100% 缩放",
                )
                for sequence in ("<Key-a>", "<Key-d>", "<Key-w>", "<Key-s>"):
                    self.assertTrue(app.bind(sequence))
                for sequence in ("<Left>", "<Right>", "<Up>", "<Down>"):
                    self.assertTrue(app.bind(sequence))
                for sequence in ("<KP_Left>", "<KP_Right>"):
                    self.assertTrue(app.bind(sequence))
                self.assertTrue(app.bind("<Tab>"))
                self.assertTrue(app.bind("<Key-p>"))
                self.assertTrue(app.bind("<Key-f>"))
                self.assertEqual(
                    app._parse_viewer_shortcut_text("A，←，小键盘←"),
                    ["<Key-a>", "<Left>", "<KP_Left>"],
                )
                self.assertEqual(
                    app._parse_viewer_zoom_presets("50，100%，150"),
                    ["适应", "50%", "100%", "150%"],
                )
                self.assertLessEqual(
                    app.dock_fullscreen_button.winfo_rootx()
                    + app.dock_fullscreen_button.winfo_width(),
                    app.command_dock.winfo_rootx() + app.command_dock.winfo_width(),
                )
                app.dock_zoom_percent_button.command()
                app.update_idletasks()
                self.assertTrue(app.dock_zoom_percent_entry.winfo_ismapped())
                self.assertFalse(app.dock_zoom_percent_button.winfo_ismapped())
                app.viewer_zoom_input_var.set("137")
                app._commit_inline_zoom_edit()
                app.update_idletasks()
                self.assertEqual(app.zoom_mode.get(), "137%")
                self.assertFalse(app.dock_zoom_percent_entry.winfo_ismapped())
                self.assertTrue(app.dock_zoom_percent_button.winfo_ismapped())
                app.dock_actual_size_button.command()
                self.assertEqual(app.zoom_mode.get(), "100%")
                app.dock_fit_button.command()
                self.assertEqual(app.zoom_mode.get(), "适应")
                self.assertTrue(app.viewer_toolbar_visible_var.get())
                self.assertEqual(app.viewer_toolbar_mode_var.get(), "fixed")
                app.toggle_viewer_clean_mode()
                app.update_idletasks()
                self.assertTrue(app._viewer_clean_mode)
                self.assertFalse(app.command_dock.winfo_ismapped())
                self.assertFalse(app.viewer_file_details.winfo_ismapped())
                self.assertFalse(app.viewer_gallery_button.winfo_ismapped())
                self.assertFalse(app.viewer_settings_button.winfo_ismapped())
                self.assertFalse(app.viewer_topmost_button.winfo_ismapped())
                app._handle_escape()
                app.update_idletasks()
                self.assertFalse(app._viewer_clean_mode)
                self.assertTrue(app.command_dock.winfo_ismapped())
                self.assertTrue(app.viewer_settings_button.winfo_ismapped())
                self.assertTrue(app.viewer_topmost_button.winfo_ismapped())

                self.assertFalse(app.viewer_topmost_var.get())
                app.toggle_viewer_topmost()
                self.assertTrue(app.viewer_topmost_var.get())
                self.assertTrue(app.viewer_topmost_button.selected)
                self.assertEqual(app.viewer_topmost_button.text, "")
                self.assertEqual(app.viewer_topmost_button.icon_name, "pin")
                self.assertEqual(
                    app.viewer_topmost_button._colors(),
                    (
                        DARK_TOKENS.surface,
                        "#ffffff",
                        None,
                    ),
                )
                self.assertEqual(app.attributes("-topmost"), 1)
                app._invoke_viewer_shortcut(
                    "toggle_topmost",
                    "<Key-p>",
                    cast(object, object()),
                )
                self.assertFalse(app.viewer_topmost_var.get())
                self.assertFalse(app.viewer_topmost_button.selected)
                self.assertEqual(app.viewer_topmost_button.text, "")
                self.assertEqual(app.viewer_topmost_button.icon_name, "pin")
                self.assertEqual(
                    app.viewer_topmost_button._colors(),
                    (DARK_TOKENS.surface, "#737b86", None),
                )
                self.assertEqual(app.attributes("-topmost"), 0)

                app.viewer_toolbar_mode_var.set("auto")
                app._viewer_toolbar_auto_visible = False
                app._update_command_dock_for_mode(ModeId.VIEWER)
                app.update_idletasks()
                self.assertFalse(app.command_dock.winfo_ismapped())
                app._update_viewer_toolbar_auto_visibility(12)
                app.update_idletasks()
                self.assertTrue(app.command_dock.winfo_ismapped())
                fade_job = app._viewer_toolbar_fade_job
                app._update_viewer_toolbar_auto_visibility(12)
                self.assertEqual(app._viewer_toolbar_fade_job, fade_job)
                app._hide_auto_viewer_toolbar()
                fade_deadline = time.monotonic() + 0.5
                while app.command_dock.winfo_ismapped() and time.monotonic() < fade_deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertFalse(app.command_dock.winfo_ismapped())
                self.assertEqual(app._viewer_toolbar_opacity, 0.0)
                self.assertEqual(app.command_dock.visual_opacity, 0.0)
                app.viewer_toolbar_mode_var.set("fixed")
                app._update_command_dock_for_mode(ModeId.VIEWER)
                app.update_idletasks()
                self.assertTrue(app.command_dock.winfo_ismapped())
                self.assertTrue(
                    all(
                        button.visual_opacity == 1.0
                        for button in (
                            app.dock_open_button,
                            app.dock_zoom_out_button,
                            app.dock_zoom_percent_button,
                            app.dock_zoom_in_button,
                            app.dock_fit_button,
                            app.dock_actual_size_button,
                            app.dock_rotate_button,
                            app.dock_favorite_button,
                            app.dock_markup_button,
                            app.dock_fullscreen_button,
                        )
                    )
                )
                app._invoke_viewer_shortcut(
                    "toggle_toolbar",
                    "<Key-h>",
                    cast(object, object()),
                )
                app.update_idletasks()
                self.assertFalse(app.command_dock.winfo_ismapped())
                self.assertTrue(app.viewer_settings_button.winfo_ismapped())
                app.toggle_viewer_toolbar()
                app.update_idletasks()
                self.assertTrue(app.command_dock.winfo_ismapped())

                app._apply_viewer_preferences(
                    shortcuts={
                        "previous": ["<Key-j>", "<Left>"],
                        "next": ["<Key-l>", "<Right>"],
                        "zoom_in": ["<Key-i>", "<Up>"],
                        "zoom_out": ["<Key-k>", "<Down>"],
                        "toggle_toolbar": ["<Key-t>"],
                        "toggle_clean": ["<Tab>"],
                        "toggle_topmost": ["<Key-p>"],
                        "toggle_favorite": ["<Key-f>"],
                    },
                    zoom_step_percent=20,
                    zoom_presets=["适应", "80%", "100%", "160%"],
                    toolbar_visible=True,
                )
                self.assertEqual(
                    set(app.zoom_preset_buttons),
                    {"适应", "80%", "100%", "160%"},
                )
                saved_viewer_settings = settings.load_migrated()
                self.assertEqual(saved_viewer_settings["viewer_zoom_step_percent"], 20)
                self.assertEqual(
                    saved_viewer_settings["viewer_shortcuts"]["previous"],
                    ["<Key-j>", "<Left>"],
                )
                app.dock_rotate_button.command()
                self.assertEqual(app.mode_router.active_mode, ModeId.VIEWER)

                source = root / "山谷清晨.png"
                Image.new("RGB", (32, 24), "#67507a").save(source)
                Image.new("RGB", (24, 32), "#d0bcff").save(root / "树林.png")
                with Image.open(source) as loaded:
                    app.original = loaded.copy()
                app.image_path = source
                app.navigation = NavigationSession.from_activation([source])
                app._update_viewer_favorite_button()
                self.assertEqual(app.dock_favorite_button.text, "☆")
                app.toggle_viewer_favorite()
                self.assertEqual(app.dock_favorite_button.text, "★")
                self.assertTrue(app.dock_favorite_button.selected)
                self.assertTrue(app._viewer_image_is_favorite(source))
                self.assertIn(str(source), settings.load_migrated()["viewer_favorite_paths"])
                app.toggle_viewer_favorite()
                self.assertEqual(app.dock_favorite_button.text, "☆")
                self.assertFalse(app._viewer_image_is_favorite(source))
                details = app._image_info_text()
                self.assertIn("山谷清晨.png", details)
                self.assertIn("PNG", details)
                self.assertIn("32 × 24", details)
                self.assertRegex(details, r"\d+(?:\.\d+)? (?:B|KB|MB)")
                app._update_viewer_footer_info()
                self.assertTrue(app.viewer_footer_var.get().startswith("PNG  ·  32 × 24"))
                self.assertNotIn("山谷清晨.png", app.viewer_footer_var.get())
                self.assertRegex(app.viewer_footer_var.get(), r"\d+(?:\.\d+)? (?:B|KB|MB)")
                app._update_viewer_gallery_button()
                app.update_idletasks()
                self.assertEqual(app.viewer_gallery_button.text, "▦ 此文件夹")
                self.assertEqual(len(app.thumbnail_filmstrip.paths), 2)
                self.assertTrue(app._viewer_navigation_visible)
                self.assertEqual(len(app.canvas.find_withtag("viewer-navigation")), 2)
                expected_navigation_geometry = app._viewer_navigation_dimensions(
                    app.canvas_frame.winfo_width(),
                    app.canvas_frame.winfo_height(),
                )
                self.assertEqual(app._viewer_navigation_geometry, expected_navigation_geometry)
                self.assertGreater(
                    app._viewer_navigation_dimensions(1600, 1000)[2],
                    app._viewer_navigation_dimensions(800, 500)[2],
                )
                self.assertGreaterEqual(expected_navigation_geometry[2], 76)
                center_y = app.canvas.winfo_height() // 2
                self.assertEqual(app._viewer_navigation_direction_at(2, center_y), -1)
                self.assertEqual(
                    app._viewer_navigation_direction_at(app.canvas.winfo_width() - 2, center_y),
                    1,
                )
                self.assertIsNone(
                    app._viewer_navigation_direction_at(app.canvas.winfo_width() // 2, center_y)
                )
                app._set_viewer_navigation_hover(-1)
                previous_arrow = app.canvas.find_withtag("viewer-navigation--1")
                self.assertEqual(len(previous_arrow), 1)
                self.assertEqual(app.canvas.type(previous_arrow[0]), "image")
                self.assertGreaterEqual(app._viewer_navigation_photos[-1].height(), 80)
                app._set_viewer_navigation_hover(None)
                app._animate_viewer_navigation_opacity(0.0)
                fade_deadline = time.monotonic() + 0.5
                while (
                    app._viewer_navigation_fade_job is not None and time.monotonic() < fade_deadline
                ):
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(app._viewer_navigation_opacity, 0.0)
                self.assertFalse(app.canvas.find_withtag("viewer-navigation"))
                app._set_viewer_navigation_hover(-1)
                fade_deadline = time.monotonic() + 0.5
                while (
                    app._viewer_navigation_fade_job is not None and time.monotonic() < fade_deadline
                ):
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(app._viewer_navigation_opacity, 1.0)
                self.assertTrue(app.canvas.find_withtag("viewer-navigation"))
                fit_width, fit_height = app._viewer_fit_render_size(1600, 1000)
                self.assertGreaterEqual(fit_width, 1552)
                self.assertGreaterEqual(fit_height, 904)
                self.assertEqual(app.dock_undo_button.accessible_name, "撤销")
                self.assertEqual(app.dock_redo_button.accessible_name, "重做")
                self.assertEqual(app.dock_crop_button.accessible_name, "裁剪")
                self.assertEqual(app.dock_fullscreen_button.accessible_name, "全屏")
                self.assertEqual(
                    app.dock_fullscreen_button.icon_name,
                    "overlapping_rectangles",
                )
                self.assertEqual(app.export_footer_button.text, "导出图像  Ctrl+E")
                self.assertTrue(
                    all(not widget.winfo_ismapped() for widget in app.detail_advanced_widgets)
                )
                capsule = app.command_dock.bbox("dock_capsule")
                self.assertIsNone(capsule)
                capsule_items = app.command_dock.find_withtag("dock_capsule")
                self.assertEqual(len(capsule_items), 0)
                self.assertEqual(app.command_dock.content.cget("style"), "ViewerDock.TFrame")

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
                    DARK_TOKENS.canvas,
                )
                if app.window_controller is not None:
                    assert app.window_titlebar is not None
                    self.assertEqual(
                        app.window_titlebar.cget("background"),
                        DARK_TOKENS.surface,
                    )
                    for direction in ("s", "e", "w", "sw", "se"):
                        self.assertEqual(
                            app.window_controller._resize_handles[direction].cget("background"),
                            DARK_TOKENS.canvas,
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
