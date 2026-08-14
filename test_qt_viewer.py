from __future__ import annotations

import os
import struct
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageChops, ImageFilter, ImageStat
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QKeySequence,
    QPainter,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsView,
    QInputDialog,
    QMessageBox,
)

from image_ops import extension_for_format
from light_image_editor.progressive_image import ProgressivePreview
from light_image_editor.qt_viewer import ViewerWindow
from light_image_editor.qt_viewer_config import QtViewerConfig
from light_image_editor.qt_viewer_widgets import (
    AnnotationSpec,
    ImageView,
    MarkupItem,
    MarkupToolbar,
    NativeResizeFilter,
    ViewerCheckBox,
    ViewerIconButton,
    ViewerSettingsDialog,
    pil_to_qimage,
    qimage_to_pil,
)
from light_image_editor.settings import SettingsStore


def _write_minimal_psd(path: Path) -> None:
    header = b"8BPS" + struct.pack(">H", 1) + b"\0" * 6
    header += struct.pack(">HIIHH", 3, 2, 2, 8, 3)
    sections = struct.pack(">III", 0, 0, 0)
    planes = bytes((255, 0, 0, 255)) + bytes((0, 255, 0, 255)) + bytes((0, 0, 255, 255))
    path.write_bytes(header + sections + struct.pack(">H", 0) + planes)


class QtViewerEmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-viewer-tests"])

    def test_empty_state_opens_an_image_and_hides_stale_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.png"
            Image.new("RGB", (80, 60), "#336699").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.show()
                self.application.processEvents()
                window_controls = window.title_bar.findChild(QFrame, "windowControls")
                self.assertIsNotNone(window_controls)
                self.assertIn("background: transparent", window_controls.styleSheet())
                self.assertNotIn("border-left", window_controls.styleSheet())
                self.assertTrue(window.empty_state.isVisible())
                self.assertEqual(window.empty_open_button.text(), "打开文件")
                self.assertEqual(
                    window.empty_hint.text(),
                    "点击按钮选择图片、PSD/PSDT 或 PDF，也可以直接拖放到这里",
                )
                self.assertFalse(window.previous_button.isVisible())
                self.assertFalse(window.next_button.isVisible())
                self.assertFalse(window.info_label.isVisible())
                self.assertFalse(window.folder_button.isVisible())

                with patch.object(
                    QFileDialog,
                    "getOpenFileNames",
                    return_value=([str(source)], "图片"),
                ):
                    window.empty_open_button.click()

                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                self.assertTrue(window.image_view.has_image)
                self.assertEqual(window.image_view._drag_source_path, source.resolve())
                self.assertFalse(window.empty_state.isVisible())
                self.assertTrue(window.info_label.isVisible())
                self.assertTrue(window.folder_button.isVisible())
            finally:
                window.close()
                self.application.processEvents()

    def test_slow_psd_load_shows_progress_instead_of_an_ambiguous_black_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "slow.psd"
            _write_minimal_psd(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            pending: Future[ProgressivePreview] = Future()

            def delayed_load(_path, _max_size, callback=None, **_kwargs):
                if callback is not None:
                    pending.add_done_callback(callback)
                return pending

            try:
                window.show()
                with (
                    patch.object(
                        window.progressive_loader, "load_preview", side_effect=delayed_load
                    ),
                    patch.object(window.progressive_loader, "prefetch_previews"),
                ):
                    window.open_path(source)
                    self.application.processEvents()
                    self.assertTrue(window.loading_state.isVisible())
                    self.assertEqual(window.loading_title.text(), "正在解析 PSD 合成预览…")
                    self.assertIn("十几秒", window.loading_hint.text())
                    self.assertFalse(window.empty_state.isVisible())

                    pending.set_result(
                        ProgressivePreview(
                            Image.new("RGBA", (80, 60), "#336699ff"),
                            (80, 60),
                            "PSD",
                        )
                    )
                    deadline = time.monotonic() + 2
                    while window.loading_state.isVisible() and time.monotonic() < deadline:
                        self.application.processEvents()
                        time.sleep(0.01)

                    self.assertFalse(window.loading_state.isVisible())
                    self.assertTrue(window.image_view.has_image)
            finally:
                if not pending.done():
                    pending.cancel()
                window.close()
                self.application.processEvents()

    def test_auto_toolbar_does_not_hide_while_pointer_remains_over_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = ViewerWindow(SettingsStore(Path(directory) / "settings.json"))
            try:
                window.config.toolbar_mode = "auto"
                window._apply_toolbar_mode()
                with patch.object(window.toolbar, "animate_visible") as animate_visible:
                    window._toolbar_hover_changed(True)
                    self.assertFalse(window._auto_hide_timer.isActive())
                    animate_visible.assert_called_once_with(True)

                    animate_visible.reset_mock()
                    with patch.object(window, "_toolbar_contains_pointer", return_value=True):
                        window._hide_toolbar_if_inactive()
                    animate_visible.assert_not_called()
                    self.assertTrue(window._auto_hide_timer.isActive())

                    window._auto_hide_timer.stop()
                    with patch.object(window, "_toolbar_contains_pointer", return_value=False):
                        window._hide_toolbar_if_inactive()
                    animate_visible.assert_called_once_with(False)
            finally:
                window.close()
                self.application.processEvents()

    def test_zoom_control_uses_clickable_presets_instead_of_manual_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = ViewerWindow(SettingsStore(Path(directory) / "settings.json"))
            try:
                self.assertFalse(hasattr(window, "zoom_edit"))
                actions = {action.data(): action for action in window.zoom_menu.actions()}
                self.assertEqual(
                    set(actions),
                    {25, 50, 75, 100, 125, 150, 200, 300, 400},
                )
                with patch.object(window.image_view, "set_zoom_percent") as set_zoom:
                    actions[200].trigger()
                set_zoom.assert_called_once_with(200)

                window._sync_zoom_text(137)
                self.assertEqual(window.zoom_button.text(), "137%")
                self.assertFalse(any(action.isChecked() for action in actions.values()))
                window._sync_zoom_text(100)
                self.assertTrue(actions[100].isChecked())
            finally:
                window.close()
                self.application.processEvents()

    def test_theme_button_switches_the_whole_shell_and_persists_the_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            window = ViewerWindow(store)
            try:
                self.assertEqual(window.config.theme, "dark")
                self.assertEqual(window.theme_button.icon_name, "sun")

                window.theme_button.click()
                self.application.processEvents()

                self.assertEqual(window.config.theme, "light")
                self.assertEqual(window.theme_button.icon_name, "moon")
                self.assertEqual(window.theme_button.toolTip(), "切换到黑暗模式")
                self.assertIn("#e8ebef", window.root.styleSheet())
                self.assertIn("#f7f8fa", window.title_bar.styleSheet())
                self.assertIn("#ffffff", window.toolbar.styleSheet())
                self.assertEqual(window.image_view.backgroundBrush().color().name(), "#e8ebef")
                self.assertEqual(store.load().get("viewer_theme"), "light")
            finally:
                window.close()
                self.application.processEvents()

            restored = ViewerWindow(store)
            try:
                self.assertEqual(restored.config.theme, "light")
                self.assertEqual(restored.theme_button.icon_name, "moon")
                restored.theme_button.click()
                self.assertEqual(restored.config.theme, "dark")
                self.assertIn("#050607", restored.root.styleSheet())
            finally:
                restored.close()
                self.application.processEvents()

    def test_custom_navigator_position_persists_between_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            window = ViewerWindow(store)
            try:
                window.image_view.navigatorPositionChanged.emit(0.25, 0.75)
                self.application.processEvents()
                self.assertEqual(window.config.navigator_position, (0.25, 0.75))
                self.assertEqual(store.load().get("viewer_navigator_position"), [0.25, 0.75])
            finally:
                window.close()
                self.application.processEvents()

            restored = ViewerWindow(store)
            try:
                self.assertEqual(restored.config.navigator_position, (0.25, 0.75))
                position = restored.image_view._navigator_custom_position
                self.assertIsNotNone(position)
                assert position is not None
                self.assertAlmostEqual(position.x(), 0.25)
                self.assertAlmostEqual(position.y(), 0.75)
            finally:
                restored.close()
                self.application.processEvents()

    def test_image_context_menu_copies_and_renames_the_current_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (96, 64), "#336699").save(source)
            store = SettingsStore(root / "settings.json")
            window = ViewerWindow(store)
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                menu = window._image_context_menu()
                self.assertIsNotNone(menu)
                assert menu is not None
                self.assertEqual(
                    [action.text() for action in menu.actions() if not action.isSeparator()],
                    [
                        "复制图片",
                        "复制文件",
                        "复制文件路径",
                        "图片边缘微光",
                        "微光设置…",
                        "透明区域背景",
                        "重命名…",
                        "在文件夹中显示",
                    ],
                )
                transparency_action = next(
                    action for action in menu.actions() if action.text() == "透明区域背景"
                )
                transparency_menu = transparency_action.menu()
                self.assertIsNotNone(transparency_menu)
                assert transparency_menu is not None
                self.assertEqual(
                    [
                        action.text()
                        for action in transparency_menu.actions()
                        if not action.isSeparator()
                    ],
                    [
                        "棋盘格",
                        "黑色",
                        "白色",
                        "绿幕色",
                        "纸色",
                        "自定义色…",
                        "自定义棋盘格双色…",
                    ],
                )

                window._set_solid_transparency_background("#00ff00")
                self.assertEqual(window.config.transparency_background, "solid")
                self.assertEqual(window.config.transparency_color, "#00ff00")
                self.assertEqual(
                    window.image_view._transparency_background_item.brush().color().name(),
                    "#00ff00",
                )
                self.assertEqual(store.load().get("viewer_transparency_background"), "solid")

                window._set_checkerboard_background("#ffeecc", "#224466")
                self.assertEqual(window.config.transparency_background, "checkerboard")
                self.assertEqual(window.config.checker_light, "#ffeecc")
                self.assertEqual(window.config.checker_dark, "#224466")
                self.assertEqual(store.load().get("viewer_checker_dark"), "#224466")

                glow_action = next(
                    action for action in menu.actions() if action.text() == "图片边缘微光"
                )
                self.assertTrue(glow_action.isChecked())
                glow_action.trigger()
                self.assertFalse(window.config.image_edge_glow)
                self.assertFalse(window.image_view._image_outline_item.isVisible())
                self.assertFalse(window.image_view._image_halo_item.isVisible())
                self.assertFalse(store.load().get("viewer_image_edge_glow"))
                glow_action.trigger()
                self.assertTrue(window.config.image_edge_glow)
                self.assertTrue(window.image_view._image_outline_item.isVisible())
                self.assertTrue(window.image_view._image_halo_item.isVisible())

                window.copy_current_image()
                copied_image = QApplication.clipboard().image()
                self.assertEqual((copied_image.width(), copied_image.height()), (96, 64))

                window.copy_current_file()
                mime_data = QApplication.clipboard().mimeData()
                self.assertEqual(
                    [Path(url.toLocalFile()) for url in mime_data.urls()],
                    [source.resolve()],
                )
                self.assertEqual(
                    bytes(
                        mime_data.data('application/x-qt-windows-mime;value="Preferred DropEffect"')
                    ),
                    b"\x01\x00\x00\x00",
                )

                window.copy_current_path()
                self.assertEqual(QApplication.clipboard().text(), str(source.resolve()))

                window.config.favorite_paths = [str(source.resolve())]
                with patch.object(QInputDialog, "getText", return_value=("renamed", True)):
                    window.rename_current_file()
                target = root / "renamed.png"
                self.assertTrue(target.is_file())
                self.assertFalse(source.exists())
                self.assertEqual(window.navigation.current, target.resolve())
                self.assertEqual(window.image_view._drag_source_path, target.resolve())
                self.assertEqual(window.config.favorite_paths, [str(target.resolve())])
                self.assertEqual(window.title_bar.title_label.text(), "renamed")
                self.assertEqual(window.title_bar.format_button.text(), ".PNG")

                with patch("light_image_editor.qt_viewer._reveal_viewer_file") as reveal:
                    window.reveal_current_file()
                reveal.assert_called_once_with(target.resolve())
            finally:
                window.close()
                self.application.processEvents()

    def test_title_bar_renames_inline_and_converts_to_a_new_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGBA", (960, 640), (51, 102, 153, 255)).save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.open_path(source)
                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                self.assertEqual(window.title_bar.title_label.text(), "original")
                self.assertEqual(window.title_bar.format_button.text(), ".PNG")
                self.assertTrue(window._title_format_actions["PNG"].isChecked())
                self.assertFalse(window._title_format_actions["PNG"].isEnabled())

                window.title_bar.begin_name_edit()
                self.assertFalse(window.title_bar.name_editor.isHidden())
                window.title_bar.name_editor.setText("renamed-inline")
                window.title_bar._submit_name_edit()
                renamed = root / "renamed-inline.png"
                self.assertTrue(renamed.is_file())
                self.assertFalse(source.exists())
                self.assertEqual(window.navigation.current, renamed.resolve())

                window.change_current_format("JPEG")
                converted = root / "renamed-inline.jpg"
                deadline = time.monotonic() + 5
                while (
                    window.navigation.current != converted.resolve()
                    or not window.image_view.has_image
                ) and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                self.assertTrue(renamed.is_file())
                self.assertTrue(converted.is_file())
                self.assertEqual(window.navigation.current, converted.resolve())
                with Image.open(converted) as result:
                    self.assertEqual(result.format, "JPEG")
                    self.assertEqual(result.size, (960, 640))
                self.assertEqual(window.title_bar.title_label.text(), "renamed-inline")
                self.assertEqual(window.title_bar.format_button.text(), ".JPG")
                self.assertTrue(window._title_format_actions["JPEG"].isChecked())
                self.assertFalse(window._title_format_actions["JPEG"].isEnabled())
            finally:
                window.close()
                self.application.processEvents()

    def test_clicking_outside_the_title_editor_commits_rename_and_escape_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.png"
            Image.new("RGB", (320, 240), (51, 102, 153)).save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.resize(960, 640)
                window.show()
                window.open_path(source)
                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                window.title_bar.begin_name_edit()
                window.title_bar.name_editor.setText("clicked-away")
                QTest.mouseClick(
                    window.image_view.viewport(),
                    Qt.MouseButton.LeftButton,
                    pos=window.image_view.viewport().rect().center(),
                )
                self.application.processEvents()

                renamed = root / "clicked-away.png"
                self.assertTrue(renamed.is_file())
                self.assertFalse(source.exists())
                self.assertEqual(window.navigation.current, renamed.resolve())
                self.assertTrue(window.title_bar.name_editor.isHidden())
                self.assertEqual(window.title_bar.title_label.text(), "clicked-away")

                window.title_bar.begin_name_edit()
                window.title_bar.name_editor.setText("must-not-be-saved")
                QTest.keyClick(window.title_bar.name_editor, Qt.Key.Key_Escape)
                QTest.mouseClick(
                    window.image_view.viewport(),
                    Qt.MouseButton.LeftButton,
                    pos=window.image_view.viewport().rect().center(),
                )
                self.application.processEvents()

                self.assertTrue(renamed.is_file())
                self.assertFalse((root / "must-not-be-saved.png").exists())
                self.assertEqual(window.navigation.current, renamed.resolve())
            finally:
                window.close()
                self.application.processEvents()

    def test_title_quick_format_matrix_preserves_the_source_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for output_format in ("PNG", "JPEG", "WEBP", "BMP", "TIFF"):
                with self.subTest(output_format=output_format):
                    case = root / output_format.casefold()
                    case.mkdir()
                    source = case / "source.avif"
                    with Image.new("RGB", (960, 640), (45, 120, 210)) as image:
                        image.save(source, "AVIF", quality=90)
                    window = ViewerWindow(SettingsStore(case / "settings.json"))
                    try:
                        window.open_path(source)
                        deadline = time.monotonic() + 5
                        while not window.image_view.has_image and time.monotonic() < deadline:
                            self.application.processEvents()
                            time.sleep(0.01)

                        with patch.object(QMessageBox, "warning") as warning:
                            window.change_current_format(output_format)
                            expected_suffix = extension_for_format(output_format)
                            deadline = time.monotonic() + 8
                            while time.monotonic() < deadline:
                                self.application.processEvents()
                                time.sleep(0.01)
                                current = window.navigation.current
                                if (
                                    current is not None
                                    and current != source.resolve()
                                    and current.suffix == expected_suffix
                                    and window.image_view.has_image
                                ):
                                    break
                            warning.assert_not_called()

                        target = window.navigation.current
                        self.assertIsNotNone(target)
                        assert target is not None
                        self.assertNotEqual(target, source.resolve())
                        self.assertTrue(source.is_file())
                        self.assertTrue(target.is_file())
                        with Image.open(target) as converted:
                            self.assertEqual(converted.format, output_format)
                            self.assertEqual(converted.size, (960, 640))
                    finally:
                        window.close()
                        self.application.processEvents()

    def test_rotation_button_uses_the_compact_square_design_without_a_separator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = ViewerWindow(SettingsStore(Path(directory) / "settings.json"))
            try:
                button = window.rotate_button
                self.assertEqual((button.width(), button.height()), (40, 36))
                self.assertEqual((button.minimumWidth(), button.maximumWidth()), (40, 40))
                self.assertEqual(button.icon_name, "rotate-cw")
                self.assertEqual(button.text(), "")
                self.assertEqual(button.toolTip(), "顺时针旋转 90°")
                self.assertEqual(button.objectName(), "rotatePreviewButton")
                self.assertIn("background: transparent", button.styleSheet())
                self.assertIn("border: 0", button.styleSheet())
                self.assertIn("border-radius: 10px", button.styleSheet())
                self.assertNotIn("border-color", button.styleSheet())

                layout = window.toolbar.button_layout
                self.assertEqual(layout.spacing(), 2)
                toolbar_buttons = [
                    layout.itemAt(index).widget()
                    for index in range(layout.count())
                    if isinstance(layout.itemAt(index).widget(), ViewerIconButton)
                ]
                self.assertIs(toolbar_buttons[-1], window.settings_button)
                self.assertEqual(window.settings_button.icon_name, "settings")
                self.assertEqual(
                    (window.settings_button.width(), window.settings_button.height()),
                    (40, 36),
                )
                self.assertIn("background: transparent", window.settings_button.styleSheet())
                compact_buttons = [
                    candidate
                    for candidate in toolbar_buttons
                    if candidate is not window.zoom_button
                ]
                self.assertTrue(compact_buttons)
                self.assertTrue(
                    all(
                        (candidate.width(), candidate.height()) == (40, 36)
                        for candidate in compact_buttons
                    )
                )
                self.assertEqual(
                    (window.zoom_button.width(), window.zoom_button.height()), (52, 36)
                )
                rotate_index = layout.indexOf(button)
                self.assertGreaterEqual(rotate_index, 0)
                self.assertIs(layout.itemAt(rotate_index + 1).widget(), window.favorite_button)
            finally:
                window.close()
                self.application.processEvents()

    def test_visible_topmost_toggle_keeps_the_same_native_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = ViewerWindow(SettingsStore(Path(directory) / "settings.json"))
            try:
                window.show()
                self.application.processEvents()
                original_handle = int(window.winId())

                with (
                    patch(
                        "light_image_editor.qt_viewer._set_windows_topmost",
                        return_value=True,
                    ) as set_native_topmost,
                    patch.object(window, "setWindowFlag") as set_window_flag,
                    patch.object(window, "show") as show_window,
                    patch.object(window, "raise_") as raise_window,
                ):
                    window.apply_topmost(True)

                set_native_topmost.assert_called_once_with(original_handle, True)
                set_window_flag.assert_not_called()
                show_window.assert_not_called()
                raise_window.assert_not_called()
                self.assertEqual(int(window.winId()), original_handle)
                self.assertTrue(window.config.topmost)
                self.assertTrue(window.pin_button._selected)
            finally:
                window.close()
                self.application.processEvents()


class QtViewerSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-viewer-settings-tests"])

    def test_settings_dialog_uses_category_pages_and_a_fixed_footer(self) -> None:
        dialog = ViewerSettingsDialog(QtViewerConfig())
        try:
            dialog.show()
            self.application.processEvents()
            self.assertGreaterEqual(dialog.width(), 720)
            self.assertGreaterEqual(dialog.height(), 560)
            self.assertEqual(
                tuple(dialog.settings_nav_buttons),
                ("general", "appearance", "shortcuts"),
            )
            self.assertEqual(dialog.settings_pages.count(), 3)
            self.assertEqual(dialog.settings_pages.currentIndex(), 0)
            self.assertGreaterEqual(
                len(
                    [
                        frame
                        for frame in dialog.findChildren(QFrame)
                        if frame.objectName() == "settingsCard"
                    ]
                ),
                3,
            )

            for index, key in enumerate(("general", "appearance", "shortcuts")):
                dialog.settings_nav_buttons[key].click()
                self.application.processEvents()
                self.assertEqual(dialog.settings_pages.currentIndex(), index)
                self.assertTrue(dialog.settings_nav_buttons[key].isChecked())
                self.assertTrue(dialog.settings_button_box.isVisible())
                footer_bottom = dialog.settings_button_box.mapTo(
                    dialog,
                    QPoint(0, dialog.settings_button_box.height()),
                ).y()
                self.assertLessEqual(footer_bottom, dialog.height())

            self.assertTrue(dialog.shortcut_scroll.isVisible())
            self.assertTrue(dialog.shortcut_scroll.verticalScrollBar().isVisible())
        finally:
            dialog.close()
            self.application.processEvents()

    def test_edge_glow_controls_preview_apply_and_persist_custom_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            config = QtViewerConfig()
            dialog = ViewerSettingsDialog(config)
            previews: list[tuple[bool, str, int, int]] = []
            dialog.glowPreviewChanged.connect(
                lambda enabled, color, intensity, spread: previews.append(
                    (enabled, color, intensity, spread)
                )
            )
            try:
                self.assertTrue(dialog.edge_glow_enabled.isChecked())
                self.assertTrue(dialog.edge_glow_auto_color.isChecked())
                self.assertFalse(dialog.edge_glow_color_button.isEnabled())
                for checkbox in (
                    dialog.toolbar_visible,
                    dialog.topmost,
                    dialog.edge_glow_enabled,
                    dialog.edge_glow_auto_color,
                ):
                    self.assertIsInstance(checkbox, ViewerCheckBox)
                    self.assertGreaterEqual(checkbox.minimumHeight(), 26)
                    self.assertEqual(checkbox.cursor().shape(), Qt.CursorShape.PointingHandCursor)

                dialog.edge_glow_enabled.setChecked(False)
                self.assertFalse(dialog.edge_glow_intensity.isEnabled())
                dialog.edge_glow_enabled.setChecked(True)
                dialog.edge_glow_auto_color.setChecked(False)
                self.assertTrue(dialog.edge_glow_color_button.isEnabled())
                with patch.object(QColorDialog, "getColor", return_value=QColor("#ff8844")):
                    dialog.edge_glow_color_button.click()
                dialog.edge_glow_intensity.setValue(72)
                dialog.edge_glow_spread.setValue(13)

                dialog.apply_to(config)
                self.assertEqual(
                    (
                        config.image_edge_glow,
                        config.image_edge_glow_color,
                        config.image_edge_glow_intensity,
                        config.image_edge_glow_spread,
                    ),
                    (True, "#ff8844", 72, 13),
                )
                self.assertEqual(previews[-1], (True, "#ff8844", 72, 13))

                config.save(store, {})
                loaded, _base = QtViewerConfig.load(store)
                self.assertEqual(loaded.image_edge_glow_color, "#ff8844")
                self.assertEqual(loaded.image_edge_glow_intensity, 72)
                self.assertEqual(loaded.image_edge_glow_spread, 13)
            finally:
                dialog.close()
                self.application.processEvents()

    def test_default_viewer_button_opens_the_registered_windows_app_page(self) -> None:
        dialog = ViewerSettingsDialog(QtViewerConfig())
        requests: list[bool] = []
        dialog.defaultAppRequested.connect(lambda: requests.append(True))
        try:
            dialog.default_app_button.click()
            self.assertEqual(requests, [True])
        finally:
            dialog.close()

        with tempfile.TemporaryDirectory() as directory:
            window = ViewerWindow(SettingsStore(Path(directory) / "settings.json"))
            try:
                with patch(
                    "light_image_editor.qt_viewer.QDesktopServices.openUrl",
                    return_value=True,
                ) as open_url:
                    window.open_default_app_settings()
                url = open_url.call_args.args[0].toString()
                self.assertEqual(
                    url,
                    "ms-settings:defaultapps?registeredAppUser=LightImageEditor",
                )
            finally:
                window.close()
                self.application.processEvents()

    def test_shortcuts_are_captured_as_removable_keys_without_delimiters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            config = QtViewerConfig()
            dialog = ViewerSettingsDialog(config)
            try:
                dialog.show()
                self.application.processEvents()
                dialog.settings_nav_buttons["shortcuts"].click()
                self.application.processEvents()
                arrow = dialog.shortcut_editors["markup_arrow"]
                brush = dialog.shortcut_editors["markup_brush"]
                self.assertEqual(arrow.sequences(), ["Alt+A"])
                self.assertEqual(brush.sequences(), ["Alt+B"])

                arrow.add_button.click()
                self.assertTrue(arrow.capture_row.isVisible())
                arrow.key_capture.setKeySequence(QKeySequence("Ctrl+Alt+A"))
                arrow.capture_confirm.click()
                self.assertEqual(arrow.sequences(), ["Alt+A", "Ctrl+Alt+A"])

                arrow.remove_sequence("Alt+A")
                brush.remove_sequence("Alt+B")
                dialog.apply_to(config)
                self.assertEqual(config.shortcuts["markup_arrow"], ["Ctrl+Alt+A"])
                self.assertEqual(config.shortcuts["markup_brush"], [])

                config.save(store, {})
                loaded, _base = QtViewerConfig.load(store)
                self.assertEqual(loaded.shortcuts["markup_arrow"], ["Ctrl+Alt+A"])
                self.assertEqual(loaded.shortcuts["markup_brush"], [])

                dialog.reset_shortcuts_button.click()
                self.assertEqual(arrow.sequences(), ["Alt+A"])
                self.assertEqual(brush.sequences(), ["Alt+B"])
            finally:
                dialog.close()


class QtViewerWindowResizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-resize-tests"])

    def test_child_widget_shows_each_resize_cursor_and_restores_its_own(self) -> None:
        window = QFrame()
        window.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        window.resize(420, 260)
        child = QFrame(window)
        child.setGeometry(window.rect())
        child.setCursor(Qt.CursorShape.OpenHandCursor)
        resize_filter = NativeResizeFilter(window)
        self.application.installEventFilter(resize_filter)
        try:
            window.show()
            self.application.processEvents()

            self.assertTrue(child.hasMouseTracking())
            QTest.mouseMove(child, child.rect().center())
            self.application.processEvents()
            self.assertIsNone(QApplication.overrideCursor())
            self.assertEqual(child.cursor().shape(), Qt.CursorShape.OpenHandCursor)

            edge_cases = (
                (QPoint(2, child.height() // 2), Qt.CursorShape.SizeHorCursor),
                (QPoint(child.width() - 2, child.height() // 2), Qt.CursorShape.SizeHorCursor),
                (QPoint(child.width() // 2, 2), Qt.CursorShape.SizeVerCursor),
                (QPoint(child.width() // 2, child.height() - 2), Qt.CursorShape.SizeVerCursor),
                (QPoint(2, 2), Qt.CursorShape.SizeFDiagCursor),
                (
                    QPoint(child.width() - 2, child.height() - 2),
                    Qt.CursorShape.SizeFDiagCursor,
                ),
                (QPoint(child.width() - 2, 2), Qt.CursorShape.SizeBDiagCursor),
                (QPoint(2, child.height() - 2), Qt.CursorShape.SizeBDiagCursor),
            )
            for point, expected_cursor in edge_cases:
                with self.subTest(point=point, expected_cursor=expected_cursor):
                    QTest.mouseMove(child, point)
                    self.application.processEvents()
                    override = QApplication.overrideCursor()
                    self.assertIsNotNone(override)
                    self.assertEqual(
                        override.shape() if override is not None else None, expected_cursor
                    )

            QTest.mouseMove(child, child.rect().center())
            self.application.processEvents()
            self.assertIsNone(QApplication.overrideCursor())
            self.assertEqual(child.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        finally:
            resize_filter._restore_resize_cursor()
            self.application.removeEventFilter(resize_filter)
            window.close()
            self.application.processEvents()

    def test_maximize_button_restores_previous_window_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = ViewerWindow(SettingsStore(Path(directory) / "settings.json"))
            try:
                window.setGeometry(70, 60, 760, 520)
                window.show()
                self.application.processEvents()
                original = window.geometry()

                window.title_bar.maximize_button.click()
                self.application.processEvents()
                self.assertTrue(window.isMaximized())
                self.assertEqual(window.title_bar._restore_geometry, original)
                self.assertTrue(window.title_bar.maximize_button._maximized)

                window.title_bar.maximize_button.click()
                self.application.processEvents()
                self.assertFalse(window.isMaximized())
                self.assertEqual(window.geometry(), original)
                self.assertIsNone(window.title_bar._restore_geometry)
                self.assertFalse(window.title_bar.maximize_button._maximized)
            finally:
                window.close()
                self.application.processEvents()

    def test_dragging_a_maximized_title_bar_restores_and_moves_the_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            window = ViewerWindow(SettingsStore(Path(directory) / "settings.json"))
            try:
                window.setGeometry(70, 60, 760, 520)
                window.show()
                self.application.processEvents()
                original_size = window.size()
                window.title_bar.maximize_button.click()
                self.application.processEvents()
                self.assertTrue(window.isMaximized())

                press = QPoint(window.title_bar.width() * 3 // 4, 20)
                target = press + QPoint(QApplication.startDragDistance() + 30, 12)
                QTest.mousePress(
                    window.title_bar,
                    Qt.MouseButton.LeftButton,
                    pos=press,
                )
                QTest.mouseMove(window.title_bar, target, delay=10)
                self.application.processEvents()
                QTest.mouseRelease(
                    window.title_bar,
                    Qt.MouseButton.LeftButton,
                    pos=target,
                )
                self.application.processEvents()

                self.assertFalse(window.isMaximized())
                self.assertEqual(window.size(), original_size)
                self.assertIsNone(window.title_bar._restore_geometry)
                self.assertFalse(window.title_bar.maximize_button._maximized)
            finally:
                window.close()
                self.application.processEvents()


class QtViewerDocumentFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-document-tests"])

    def test_oversized_image_shows_draggable_navigator_overview(self) -> None:
        view = ImageView()
        try:
            view.resize(1000, 700)
            view.show()
            image = QImage(1600, 1200, QImage.Format.Format_RGB32)
            image.fill(QColor("#334455"))
            view.set_image(image, fit=True)
            self.application.processEvents()

            self.assertFalse(view.navigator_visible)

            view.set_zoom_percent(200)
            self.application.processEvents()
            self.assertTrue(view.navigator_visible)
            self.assertFalse(view._navigator.viewport_indicator_rect.isEmpty())
            self.assertGreater(view._navigator.y(), view.viewport().height() // 2)

            before = view.mapToScene(view.viewport().rect().center())
            start = view._navigator.viewport_indicator_rect.center().toPoint()
            target = view._navigator._preview_rect.bottomRight().toPoint() - QPoint(5, 5)
            QTest.mousePress(view._navigator, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(view._navigator, target, delay=10)
            QTest.mouseRelease(view._navigator, Qt.MouseButton.LeftButton, pos=target)
            self.application.processEvents()

            after = view.mapToScene(view.viewport().rect().center())
            self.assertGreater(after.x(), before.x())
            self.assertGreater(after.y(), before.y())

            panel_positions: list[QPointF] = []
            view._navigator.positionChanged.connect(panel_positions.append)
            panel_before = view._navigator.pos()
            center_before_panel_drag = view.mapToScene(view.viewport().rect().center())
            header_start = QPoint(90, 12)
            header_target = QPoint(35, 38)
            QTest.mousePress(view._navigator, Qt.MouseButton.LeftButton, pos=header_start)
            QTest.mouseMove(view._navigator, header_target, delay=10)
            QTest.mouseRelease(view._navigator, Qt.MouseButton.LeftButton, pos=header_target)
            self.application.processEvents()

            self.assertLess(view._navigator.x(), panel_before.x())
            self.assertTrue(panel_positions)
            self.assertIsNotNone(view._navigator_custom_position)
            self.assertEqual(
                view.mapToScene(view.viewport().rect().center()), center_before_panel_drag
            )

            view.fit_to_window()
            self.application.processEvents()
            self.assertFalse(view.navigator_visible)
        finally:
            view.close()

    def test_progressive_view_requests_only_visible_full_resolution_tiles(self) -> None:
        view = ImageView()
        requests: list[tuple[tuple[int, int, int, int], ...]] = []
        view.progressiveTilesRequested.connect(requests.append)
        preview = QImage(800, 500, QImage.Format.Format_ARGB32)
        preview.fill(QColor("#245680"))
        try:
            view.resize(800, 600)
            view.show()
            view.set_progressive_image(preview, (3200, 2000), fit=True)
            self.application.processEvents()

            self.assertIsNone(view._source_qimage)
            self.assertEqual(view.progressive_source_size, (3200, 2000))
            self.assertEqual(view.sceneRect(), QRectF(0, 0, 3200, 2000))
            self.assertFalse(requests)

            view.set_zoom_percent(100)
            self.application.processEvents()
            self.assertTrue(requests)
            requested = requests[-1]
            self.assertLess(len(requested), 12)
            first = requested[0]
            tile = QImage(
                first[2] - first[0],
                first[3] - first[1],
                QImage.Format.Format_ARGB32,
            )
            tile.fill(QColor("#ff8844"))
            view.set_progressive_tiles([(first, tile)])
            self.assertIn(first, view.loaded_progressive_tiles)

            view.set_zoom_percent(10)
            self.application.processEvents()
            self.assertNotIn(first, view.loaded_progressive_tiles)
        finally:
            view.close()

    def test_large_image_promotes_to_full_pixels_only_during_markup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.png"
            Image.new("RGB", (2400, 1600), "#245680").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.resize(900, 650)
                window.show()
                window.open_path(source)
                deadline = time.monotonic() + 10
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                self.assertIsNotNone(window.current_image)
                assert window.current_image is not None
                self.assertLess(window.current_image.width, 2400)
                self.assertEqual(window._progressive_source_size, (2400, 1600))
                self.assertIsNone(window.image_view._source_qimage)

                window.image_view.set_zoom_percent(175)
                window.image_view.centerOn(QPointF(1500, 900))
                self.application.processEvents()
                expected_scale = window.image_view.transform().m11()
                expected_center = window.image_view.mapToScene(
                    window.image_view.viewport().rect().center()
                )
                window._select_markup_tool("arrow")
                deadline = time.monotonic() + 10
                while not window.markup_toolbar.isVisible() and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                self.assertTrue(window.markup_toolbar.isVisible())
                self.assertEqual(window.image_view.annotation_tool, "arrow")
                self.assertAlmostEqual(
                    window.image_view.transform().m11(), expected_scale, places=4
                )
                edit_center = window.image_view.mapToScene(
                    window.image_view.viewport().rect().center()
                )
                self.assertAlmostEqual(edit_center.x(), expected_center.x(), delta=2)
                self.assertAlmostEqual(edit_center.y(), expected_center.y(), delta=2)
                self.assertEqual(window.current_image.size, (2400, 1600))
                self.assertIsNotNone(window.image_view._source_qimage)
                assert window.image_view._source_qimage is not None
                self.assertEqual(
                    (
                        window.image_view._source_qimage.width(),
                        window.image_view._source_qimage.height(),
                    ),
                    (2400, 1600),
                )

                window.leave_markup()
                self.assertIs(window.current_image, window._progressive_preview)
                self.assertLess(window.current_image.width, 2400)
                self.assertIsNone(window.image_view._source_qimage)
                self.assertAlmostEqual(
                    window.image_view.transform().m11(), expected_scale, places=4
                )
            finally:
                window.close()
                self.application.processEvents()

    def _wait_for_pdf_page(self, window: ViewerWindow, page_index: int) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.application.processEvents()
            image = window.current_image
            if image is not None and image.info.get("pdf_page_index") == page_index:
                return
            time.sleep(0.01)
        self.fail(f"PDF 第 {page_index + 1} 页未在限定时间内加载")

    def test_document_view_requests_only_nearby_pages(self) -> None:
        view = ImageView()
        requests: list[int] = []
        releases: list[int] = []
        view.documentPagesRequested.connect(lambda pages: requests.extend(pages))
        view.documentPagesReleased.connect(lambda pages: releases.extend(pages))
        try:
            view.resize(800, 600)
            view.show()
            first_page = QImage(1200, 800, QImage.Format.Format_RGB32)
            first_page.fill(Qt.GlobalColor.white)
            view.set_document([(1200, 800)] * 20, {0: first_page})
            self.application.processEvents()

            self.assertGreater(view.verticalScrollBar().maximum(), 0)
            self.assertTrue(requests)
            self.assertLess(len(set(requests)), 20)

            initially_requested = set(requests)
            view.actual_size()
            self.application.processEvents()
            self.assertTrue(initially_requested.intersection(releases))

            view.scroll_to_document_page(10)
            self.application.processEvents()
            self.assertEqual(view.current_document_page, 10)
            self.assertIn(10, requests)
            self.assertIn(0, releases)
        finally:
            view.close()

    def test_fit_document_uses_both_viewport_width_and_current_page_height(self) -> None:
        view = ImageView()
        try:
            view.resize(800, 600)
            view.show()
            view.set_document([(500, 8000), (1200, 500)], fit=False)
            self.application.processEvents()

            view.fit_to_window()
            self.application.processEvents()

            available_width = max(1.0, view.viewport().width() - 64.0)
            available_height = max(1.0, view.viewport().height() - 64.0)
            expected_scale = min(available_width / 1200.0, available_height / 8000.0)
            actual_scale = view.transform().m11()
            self.assertAlmostEqual(actual_scale, expected_scale, places=4)
            self.assertLessEqual(500.0 * actual_scale, available_width)
            self.assertLessEqual(8000.0 * actual_scale, available_height)
            self.assertLess(actual_scale, 0.1)
        finally:
            view.close()

    def test_psdt_opens_as_a_read_only_composite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "template.psdt"
            _write_minimal_psd(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.show()
                window.open_path(source)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                self.assertIsNotNone(window.current_image)
                self.assertIn("PSDT 合成预览", window.info_label.text())
                window.enter_markup()
                self.assertEqual(window.markup_toolbar.replace_button.text(), "另存为")
            finally:
                window.close()
                self.application.processEvents()

    def test_pdf_continuous_scroll_and_markup_export_keep_source_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pages.pdf"
            first = Image.new("RGB", (600, 800), "#245680")
            second = Image.new("RGB", (600, 800), "#a03040")
            try:
                first.save(source, save_all=True, append_images=[second])
            finally:
                first.close()
                second.close()
            original_pdf = source.read_bytes()
            output = root / "page-2-edited.png"
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.resize(900, 600)
                window.show()
                window.open_path(source)
                self._wait_for_pdf_page(window, 0)

                self.assertEqual(window._document_page_count, 2)
                self.assertIn("第 1 / 2 页", window.info_label.text())
                self.assertTrue(window.image_view.document_mode)
                self.assertEqual(window.image_view.zoom_percent(), 100)
                self.assertGreater(window.image_view.verticalScrollBar().maximum(), 0)
                self.assertFalse(window.previous_button.isVisible())
                self.assertFalse(window.next_button.isVisible())
                window.image_view.scroll_to_document_page(1)
                self._wait_for_pdf_page(window, 1)
                self.assertIn("第 2 / 2 页", window.info_label.text())

                window.image_view.set_zoom_percent(135)
                expected_scale = window.image_view.transform().m11()
                window.enter_markup()
                self.assertFalse(window.image_view.document_mode)
                self.assertAlmostEqual(
                    window.image_view.transform().m11(), expected_scale, places=4
                )
                self.assertEqual(window.markup_toolbar.replace_button.text(), "另存为")
                window.image_view.add_annotation(
                    AnnotationSpec("redact", QPointF(10, 10), QPointF(40, 35), size=6)
                )
                with (
                    patch.object(
                        QFileDialog,
                        "getSaveFileName",
                        return_value=(str(output), "PNG 图片"),
                    ),
                    patch.object(QMessageBox, "information"),
                ):
                    window.replace_original()

                self.assertTrue(output.is_file())
                self.assertEqual(source.read_bytes(), original_pdf)
                self.assertFalse(window.markup_toolbar.isVisible())
                self.assertTrue(window.image_view.document_mode)
                self.assertEqual(window.image_view.current_document_page, 1)
            finally:
                window.close()
                self.application.processEvents()


class QtViewerMarkupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-viewer-markup-tests"])

    def test_toolbar_exposes_screenshot_style_tools(self) -> None:
        toolbar = MarkupToolbar()
        try:
            self.assertEqual(
                set(toolbar.tool_buttons),
                {
                    "select",
                    "rectangle",
                    "arrow",
                    "line",
                    "ellipse",
                    "freehand",
                    "text",
                    "number",
                    "mosaic",
                    "blur",
                    "redact",
                },
            )
            toolbar.select_tool("text")
            self.assertTrue(toolbar.tool_buttons["text"].isChecked())
            self.assertEqual(toolbar.size_spin.prefix(), "字号 ")
            self.assertEqual(toolbar.size_spin.suffix(), " px")
            self.assertEqual((toolbar.size_spin.minimum(), toolbar.size_spin.maximum()), (8, 96))
            self.assertEqual(toolbar.size_spin.value(), 20)
            toolbar.select_tool("mosaic")
            self.assertTrue(toolbar.tool_buttons["mosaic"].isChecked())
            self.assertEqual(toolbar.size_spin.prefix(), "画笔大小 ")
            self.assertEqual((toolbar.size_spin.minimum(), toolbar.size_spin.maximum()), (2, 32))
            self.assertFalse(toolbar.color_button.isEnabled())
            self.assertIn(
                "仅复制",
                {button.text() for button in toolbar.findChildren(type(toolbar.color_button))},
            )
            self.assertEqual(toolbar.size_spin.value(), 12)
            toolbar.size_spin.setValue(20)
            toolbar.select_tool("blur")
            self.assertEqual(toolbar.size_spin.prefix(), "模糊强度 ")
            self.assertEqual(toolbar.size_spin.value(), 16)
            self.assertFalse(toolbar.color_button.isEnabled())
            toolbar.select_tool("mosaic")
            self.assertEqual(toolbar.size_spin.value(), 20)
            toolbar.sync_annotation("text", "#22cc88", 18)
            self.assertTrue(toolbar.tool_buttons["mosaic"].isChecked())
            self.assertEqual(toolbar._property_kind, "text")
            self.assertEqual(toolbar.size_spin.value(), 18)
            self.assertEqual(toolbar._color.name(), "#22cc88")
            toolbar.clear_annotation_selection()
            self.assertFalse(toolbar.color_button.isEnabled())
            self.assertTrue(toolbar.size_spin.isEnabled())
            self.assertFalse(toolbar.size_spin.isVisible())
            self.assertFalse(toolbar.size_preset_button.isVisible())
            self.assertTrue(toolbar.size_slider.isEnabled())
            toolbar.select_tool("rectangle")
            self.assertTrue(toolbar.color_button.isEnabled())
            self.assertTrue(toolbar.size_spin.isEnabled())
            self.assertFalse(hasattr(toolbar, "text_edit"))
        finally:
            toolbar.close()

    def test_toolbar_smart_selection_copy_and_presets(self) -> None:
        toolbar = MarkupToolbar()
        tools: list[str] = []
        colors: list[str] = []
        copied: list[bool] = []
        toolbar.toolChanged.connect(tools.append)
        toolbar.colorChanged.connect(colors.append)
        toolbar.copyRequested.connect(lambda: copied.append(True))
        try:
            toolbar.select_tool("rectangle")
            toolbar.tool_buttons["select"].click()
            self.assertEqual(toolbar._active_tool, "select")
            self.assertTrue(toolbar.tool_buttons["select"].isChecked())
            toolbar.tool_buttons["select"].click()
            self.assertEqual(toolbar._active_tool, "rectangle")
            self.assertTrue(toolbar.tool_buttons["rectangle"].isChecked())
            self.assertEqual(tools[-2:], ["select", "rectangle"])

            toolbar._apply_color("#38bdf8")
            self.assertEqual(toolbar._color.name(), "#38bdf8")
            self.assertEqual(colors[-1], "#38bdf8")
            self.assertIn(
                "#ff4c48",
                {action.data() for action in toolbar.color_menu.actions()},
            )

            toolbar.select_tool("text")
            self.assertEqual(
                {action.data() for action in toolbar.size_preset_menu.actions()},
                {12, 16, 20, 24, 32, 48, 64},
            )
            next(
                action for action in toolbar.size_preset_menu.actions() if action.data() == 32
            ).trigger()
            self.assertEqual(toolbar.size_spin.value(), 32)

            toolbar.copy_button.click()
            self.assertEqual(copied, [True])
        finally:
            toolbar.close()

    def test_toolbar_uses_one_compact_row_and_a_live_size_slider(self) -> None:
        toolbar = MarkupToolbar()
        try:
            self.assertEqual(toolbar.layout().count(), 1)
            toolbar.select_tool("text")
            self.assertEqual(
                (toolbar.size_slider.minimum(), toolbar.size_slider.maximum()), (8, 96)
            )
            toolbar.size_slider.setValue(36)
            self.assertEqual(toolbar.size_spin.value(), 36)
            self.assertEqual(toolbar.size_value_label.text(), "字号 36")
            self.assertFalse(toolbar.size_spin.isVisible())
            self.assertFalse(toolbar.size_preset_button.isVisible())
            self.assertEqual(toolbar.replace_button.text(), "另存为")
        finally:
            toolbar.close()

    def test_markup_cursor_tracks_tool_state_without_objects_stealing_it(self) -> None:
        view = ImageView()
        try:
            self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.OpenHandCursor)

            view.add_annotation(AnnotationSpec("rectangle", QPointF(20, 20), QPointF(90, 70)))
            item = view._annotation_items[0]

            view.set_annotation_tool("select")
            self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)
            self.assertTrue(item.acceptHoverEvents())
            self.assertTrue(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            self.assertEqual(item.acceptedMouseButtons(), Qt.MouseButton.LeftButton)

            view.set_annotation_tool("rectangle")
            self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.CrossCursor)
            self.assertTrue(item.acceptHoverEvents())
            self.assertTrue(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            self.assertEqual(item.acceptedMouseButtons(), Qt.MouseButton.LeftButton)

            view.set_annotation_tool("text")
            self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.IBeamCursor)
            self.assertTrue(item.acceptHoverEvents())

            view.set_annotation_tool("")
            self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.OpenHandCursor)
            self.assertFalse(item.acceptHoverEvents())
        finally:
            view.close()

    def test_current_image_drag_exposes_the_source_as_a_copy_file_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "drag-copy.png"
            base = Image.new("RGB", (160, 100), "#336699")
            try:
                base.save(source)
                view = ImageView()
                view.set_image(pil_to_qimage(base), fit=False)
                view.set_drag_source(source)
                try:
                    with patch("light_image_editor.qt_viewer_widgets.QDrag") as drag_type:
                        drag = drag_type.return_value
                        view._start_file_drag()

                    mime_data = drag.setMimeData.call_args.args[0]
                    self.assertEqual(
                        [Path(url.toLocalFile()) for url in mime_data.urls()],
                        [source.resolve()],
                    )
                    drag.exec.assert_called_once_with(
                        Qt.DropAction.CopyAction,
                        Qt.DropAction.CopyAction,
                    )
                    drag.setPixmap.assert_called_once()
                    drag.setHotSpot.assert_called_once()
                    self.assertEqual(view.dragMode(), QGraphicsView.DragMode.ScrollHandDrag)
                finally:
                    view.close()
            finally:
                base.close()

    def test_select_drag_exports_the_composited_image_instead_of_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "drag-edited.png"
            base = Image.new("RGB", (160, 100), "#336699")
            try:
                base.save(source)
                view = ImageView()
                view.set_image(pil_to_qimage(base), fit=False)
                view.set_drag_source(source)
                view.set_annotation_tool("select")
                view.add_annotation(
                    AnnotationSpec("redact", QPointF(20, 20), QPointF(80, 70), size=6)
                )
                try:
                    with patch("light_image_editor.qt_viewer_widgets.QDrag") as drag_type:
                        drag = drag_type.return_value
                        view._start_file_drag()

                    mime_data = drag.setMimeData.call_args.args[0]
                    exported = Path(mime_data.urls()[0].toLocalFile())
                    self.assertNotEqual(exported.resolve(), source.resolve())
                    self.assertTrue(mime_data.hasImage())
                    with Image.open(exported) as edited:
                        self.assertEqual(edited.convert("RGB").getpixel((40, 40)), (17, 19, 21))
                    with Image.open(source) as original:
                        self.assertEqual(original.convert("RGB").getpixel((40, 40)), (51, 102, 153))
                finally:
                    view.close()
                    for path in view._drag_temp_paths:
                        path.unlink(missing_ok=True)
            finally:
                base.close()

    def test_dragging_a_fitted_image_starts_the_file_copy_gesture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "gesture.png"
            base = Image.new("RGB", (160, 100), "#336699")
            try:
                base.save(source)
                view = ImageView()
                view.resize(500, 350)
                view.show()
                view.set_image(pil_to_qimage(base), fit=True)
                view.set_drag_source(source)
                self.application.processEvents()
                center = view.viewport().rect().center()
                target = center + QPoint(QApplication.startDragDistance() + 10, 0)
                try:
                    with patch.object(view, "_start_file_drag") as start_drag:
                        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
                        QTest.mouseMove(view.viewport(), target, delay=10)
                        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=target)
                    start_drag.assert_called_once_with()
                finally:
                    view.close()
            finally:
                base.close()

    def test_external_images_can_be_dropped_over_an_already_open_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.jpg"
            Image.new("RGB", (160, 100), "#336699").save(first)
            Image.new("RGB", (120, 90), "#993366").save(second)
            view = ImageView()
            dropped: list[list[str]] = []
            view.externalFilesDropped.connect(dropped.append)
            try:
                view.resize(500, 350)
                view.show()
                view.set_image(QImage(str(first)), fit=True)
                self.application.processEvents()

                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(str(first)), QUrl.fromLocalFile(str(second))])
                enter = QDragEnterEvent(
                    view.viewport().rect().center(),
                    Qt.DropAction.CopyAction,
                    mime_data,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                view.dragEnterEvent(enter)
                self.assertTrue(enter.isAccepted())
                self.assertEqual(enter.dropAction(), Qt.DropAction.CopyAction)

                drop = QDropEvent(
                    QPointF(view.viewport().rect().center()),
                    Qt.DropAction.CopyAction,
                    mime_data,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                view.dropEvent(drop)
                self.assertTrue(drop.isAccepted())
                self.assertEqual(
                    [[Path(path) for path in paths] for paths in dropped],
                    [[first, second]],
                )
            finally:
                view.close()

    def test_window_opens_an_external_image_dropped_on_the_image_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (160, 100), "#336699").save(first)
            Image.new("RGB", (120, 90), "#993366").save(second)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.resize(720, 480)
                window.show()
                window.open_path(first)
                deadline = time.monotonic() + 5
                while window.current_image is None and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)
                self.assertEqual(window.navigation.current, first.resolve())

                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(str(second))])
                drop = QDropEvent(
                    QPointF(window.image_view.viewport().rect().center()),
                    Qt.DropAction.CopyAction,
                    mime_data,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                window.image_view.dropEvent(drop)
                deadline = time.monotonic() + 5
                while window.navigation.current != second.resolve() and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                self.assertTrue(drop.isAccepted())
                self.assertEqual(window.navigation.current, second.resolve())
            finally:
                window.close()
                self.application.processEvents()

    def test_right_click_on_image_content_requests_the_context_menu(self) -> None:
        base = Image.new("RGB", (160, 100), "#336699")
        view = ImageView()
        positions: list[QPoint] = []
        view.contextMenuRequested.connect(positions.append)
        try:
            view.resize(500, 350)
            view.show()
            view.set_image(pil_to_qimage(base), fit=True)
            self.application.processEvents()
            center = view.viewport().rect().center()
            event = QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse,
                center,
                view.viewport().mapToGlobal(center),
            )
            view.contextMenuEvent(event)
            self.assertTrue(event.isAccepted())
            self.assertEqual(positions, [view.viewport().mapToGlobal(center)])
        finally:
            view.close()
            base.close()

    def test_transparent_pixels_support_checkerboard_and_solid_backgrounds(self) -> None:
        image = QImage(40, 30, QImage.Format.Format_RGBA8888)
        image.fill(Qt.GlobalColor.transparent)
        view = ImageView()
        try:
            view.set_transparency_background(
                "checkerboard",
                checker_light="#ffeecc",
                checker_dark="#224466",
            )
            view.set_image(image, fit=False)
            background = view._transparency_background_item
            self.assertIsNotNone(background)
            assert background is not None
            outline = view._image_outline_item
            halo = view._image_halo_item
            self.assertIsNotNone(outline)
            self.assertIsNotNone(halo)
            assert outline is not None
            assert halo is not None
            self.assertEqual(outline.rect(), QRectF(0, 0, 40, 30))
            self.assertTrue(outline.pen().isCosmetic())
            self.assertEqual(outline.pen().widthF(), 1.0)
            self.assertEqual(outline.brush().style(), Qt.BrushStyle.NoBrush)
            self.assertEqual(halo.rect(), outline.rect())
            self.assertTrue(halo.pen().isCosmetic())
            self.assertEqual(halo.pen().widthF(), 2.0)
            self.assertLess(halo.pen().color().alpha(), outline.pen().color().alpha())
            self.assertEqual(len(view._image_glow_items), 8)
            self.assertEqual(
                [item.pen().widthF() for item in view._image_glow_items],
                [14.0, 12.0, 10.0, 8.0, 6.0, 5.0, 4.0, 2.0],
            )
            self.assertEqual(
                sorted(item.pen().color().alpha() for item in view._image_glow_items),
                [3, 4, 5, 7, 9, 12, 18, 30],
            )
            view.set_image_edge_glow_style(color="#ff00aa", intensity=80, spread=12)
            self.assertEqual(
                [item.pen().widthF() for item in view._image_glow_items],
                [24.0, 21.0, 17.0, 14.0, 11.0, 8.0, 6.0, 4.0],
            )
            self.assertTrue(
                all(item.pen().color().name() == "#ff00aa" for item in view._image_glow_items)
            )
            self.assertEqual(outline.pen().color().name(), "#ff00aa")
            texture = background.brush().texture().toImage()
            self.assertEqual(texture.pixelColor(0, 0).name(), "#ffeecc")
            self.assertEqual(texture.pixelColor(13, 0).name(), "#224466")
            self.assertEqual(texture.pixelColor(0, 13).name(), "#224466")

            view.set_transparency_background("solid", color="#a1b2c3")
            background = view._transparency_background_item
            self.assertIsNotNone(background)
            assert background is not None
            self.assertEqual(background.brush().color().name(), "#a1b2c3")
        finally:
            view.close()

    def test_copy_markup_places_composite_on_clipboard_without_replacing_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (80, 60), "white").save(source)
            original = source.read_bytes()
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.resize(640, 480)
                window.show()
                window.open_path(source)
                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                window.enter_markup()
                window.image_view.add_annotation(
                    AnnotationSpec("redact", QPointF(10, 10), QPointF(40, 40), size=6)
                )
                window.copy_markup()

                copied = QApplication.clipboard().image()
                self.assertFalse(copied.isNull())
                self.assertEqual((copied.width(), copied.height()), (80, 60))
                self.assertNotEqual(copied.pixelColor(20, 20).name(), "#ffffff")
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(window.markup_toolbar.isVisible())
            finally:
                window.close()
                self.application.processEvents()

    def test_markup_composite_applies_color_privacy_and_step_tools(self) -> None:
        base = Image.new("RGB", (120, 100))
        pixels = base.load()
        assert pixels is not None
        for y in range(base.height):
            for x in range(base.width):
                pixels[x, y] = (x * 2 % 256, y * 2 % 256, (x + y) % 256)

        view = ImageView()
        view.set_image(pil_to_qimage(base))
        view.add_annotation(
            AnnotationSpec("rectangle", QPointF(2, 2), QPointF(40, 30), color="#22cc88", size=4)
        )
        view.add_annotation(AnnotationSpec("mosaic", QPointF(10, 40), QPointF(50, 80), size=8))
        view.add_annotation(AnnotationSpec("blur", QPointF(55, 40), QPointF(90, 80), size=5))
        view.add_annotation(AnnotationSpec("redact", QPointF(92, 40), QPointF(118, 80), size=6))
        view.add_annotation(
            AnnotationSpec(
                "number",
                QPointF(72, 20),
                QPointF(72, 20),
                color="#ff4c48",
                size=6,
                number=1,
            )
        )

        output = view.composited_pil(base)
        try:
            self.assertEqual(output.getpixel((2, 2))[:3], (34, 204, 136))
            self.assertNotEqual(output.getpixel((13, 43))[:3], base.getpixel((13, 43)))
            self.assertEqual(output.getpixel((100, 55))[:3], (17, 19, 21))
            self.assertEqual(output.getpixel((72, 8))[:3], (255, 76, 72))
        finally:
            output.close()
            base.close()

    def test_mosaic_and_blur_have_real_live_canvas_previews(self) -> None:
        base = Image.new("RGB", (120, 100))
        pixels = base.load()
        assert pixels is not None
        for y in range(base.height):
            for x in range(base.width):
                pixels[x, y] = (
                    255 if (x // 2 + y // 2) % 2 else 0,
                    x * 2 % 256,
                    y * 2 % 256,
                )

        view = ImageView()
        view.set_image(pil_to_qimage(base), fit=False)
        view.add_annotation(AnnotationSpec("mosaic", QPointF(52, 82), QPointF(8, 38), size=7))
        view.add_annotation(AnnotationSpec("blur", QPointF(62, 38), QPointF(112, 82), size=6))
        live_qimage = QImage(120, 100, QImage.Format.Format_RGBA8888)
        live_qimage.fill(Qt.GlobalColor.transparent)
        painter = QPainter(live_qimage)
        view.scene().render(
            painter,
            QRectF(0, 0, 120, 100),
            QRectF(0, 0, 120, 100),
        )
        painter.end()
        live = qimage_to_pil(live_qimage)
        expected = view.composited_pil(base)
        try:
            self.assertNotEqual(live.getpixel((20, 60))[:3], base.getpixel((20, 60)))
            self.assertNotEqual(live.getpixel((85, 60))[:3], base.getpixel((85, 60)))
            self.assertLess(
                sum(
                    abs(left - right)
                    for left, right in zip(
                        live.getpixel((20, 60))[:3],
                        expected.getpixel((20, 60))[:3],
                    )
                ),
                12,
            )
            self.assertLessEqual(
                sum(
                    abs(left - right)
                    for left, right in zip(
                        live.getpixel((85, 60))[:3],
                        expected.getpixel((85, 60))[:3],
                    )
                ),
                3,
            )
            self.assertEqual(expected.getpixel((45, 40))[:3], base.getpixel((45, 40)))

            mosaic_region = expected.crop((16, 48, 32, 64)).convert("RGB")
            blur_region = expected.crop((62, 38, 112, 82)).convert("RGB")
            source_blurred = base.filter(ImageFilter.GaussianBlur(radius=6))
            legacy_blur = source_blurred.crop((62, 38, 112, 82)).convert("RGB")
            try:
                colors = mosaic_region.getcolors(
                    maxcolors=mosaic_region.width * mosaic_region.height
                )
                self.assertIsNotNone(colors)
                self.assertLessEqual(len(colors or []), 16)
                self.assertLess(
                    sum(ImageStat.Stat(blur_region).var),
                    sum(ImageStat.Stat(legacy_blur).var),
                )
            finally:
                mosaic_region.close()
                blur_region.close()
                source_blurred.close()
                legacy_blur.close()
        finally:
            live.close()
            expected.close()
            view.close()
            base.close()

    def test_mosaic_draft_has_no_colored_guide_outline(self) -> None:
        base = Image.new("RGB", (120, 100))
        pixels = base.load()
        assert pixels is not None
        for y in range(base.height):
            for x in range(base.width):
                shade = (x * 2 + y) % 180
                pixels[x, y] = (shade, shade, shade)

        source = pil_to_qimage(base)
        item = MarkupItem(
            AnnotationSpec(
                "mosaic",
                QPointF(18, 45),
                QPointF(102, 55),
                color="#ff4c48",
                size=8,
                points=(
                    QPointF(18, 45),
                    QPointF(40, 35),
                    QPointF(62, 58),
                    QPointF(82, 42),
                    QPointF(102, 55),
                ),
            ),
            source,
            draft=True,
        )
        rendered = QImage(120, 100, QImage.Format.Format_RGBA8888)
        rendered.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rendered)
        painter.drawImage(0, 0, source)
        item.paint(painter, None)
        painter.end()
        preview = qimage_to_pil(rendered).convert("RGB")
        try:
            self.assertNotEqual(preview.getpixel((62, 50)), base.getpixel((62, 50)))
            self.assertFalse(
                any(
                    red > 210 and green < 140 and blue < 140
                    for red, green, blue in preview.getdata()
                )
            )
        finally:
            preview.close()
            base.close()

    def test_mosaic_brush_is_locked_after_drawing_and_new_strokes_can_overlap_it(self) -> None:
        base = Image.new("RGB", (320, 200))
        pixels = base.load()
        assert pixels is not None
        for y in range(base.height):
            for x in range(base.width):
                pixels[x, y] = (x % 256, y % 256, (x + y) % 256)

        view = ImageView()
        try:
            view.resize(640, 480)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("mosaic")
            self.application.processEvents()

            source_points = tuple(QPointF(40 + step * 20, 80 + step * 4) for step in range(8))
            start = view.mapFromScene(source_points[0])
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            for point in source_points[1:]:
                QTest.mouseMove(view.viewport(), view.mapFromScene(point))
            QTest.mouseRelease(
                view.viewport(),
                Qt.MouseButton.LeftButton,
                pos=view.mapFromScene(source_points[-1]),
            )
            self.application.processEvents()

            self.assertEqual(len(view.annotations), 1)
            annotation = view.annotations[0]
            self.assertEqual(annotation.kind, "mosaic")
            self.assertEqual(annotation.points[0], source_points[0])
            self.assertEqual(annotation.points[-1], source_points[-1])
            first_signature = tuple(QPointF(point) for point in annotation.points)
            item = view._annotation_items[0]
            self.assertFalse(bool(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable))
            self.assertFalse(bool(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable))
            self.assertEqual(item.acceptedMouseButtons(), Qt.MouseButton.NoButton)

            press_scene = source_points[3]
            delta = QPointF(40, 30)
            press = view.mapFromScene(press_scene)
            destination = view.mapFromScene(press_scene + delta)
            view.set_annotation_tool("select")
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=press)
            QTest.mouseMove(view.viewport(), destination)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)
            self.application.processEvents()

            self.assertFalse(item.isSelected())
            self.assertEqual(len(view.annotations), 1)
            self.assertEqual(annotation.points, first_signature)

            view.set_annotation_tool("mosaic")
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=press)
            QTest.mouseMove(view.viewport(), destination)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=destination)
            self.application.processEvents()

            self.assertEqual(len(view.annotations), 2)
            self.assertEqual(view.annotations[0].points, first_signature)
            self.assertEqual(view.annotations[1].start, press_scene)
            self.assertEqual(view.annotations[1].end, press_scene + delta)
            self.assertFalse(any(mark.isSelected() for mark in view._annotation_items))
            view.undo_annotation()
            self.assertEqual(len(view.annotations), 1)
            self.assertEqual(view.annotations[0].points, first_signature)
        finally:
            view.close()
            base.close()

    def test_image_view_accepts_every_quick_markup_tool(self) -> None:
        view = ImageView()
        for tool in (
            "select",
            "rectangle",
            "arrow",
            "line",
            "ellipse",
            "freehand",
            "text",
            "number",
            "mosaic",
            "blur",
            "redact",
        ):
            view.set_annotation_tool(tool)
            self.assertEqual(view.annotation_tool, tool)

    def test_selected_annotation_delete_escape_and_undo_are_consistent(self) -> None:
        base = Image.new("RGB", (240, 160), "#334455")
        view = ImageView()
        try:
            view.resize(480, 320)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("ellipse")
            self.application.processEvents()

            start = view.mapFromScene(QPointF(40, 40))
            end = view.mapFromScene(QPointF(140, 110))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(view.viewport(), end)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            self.application.processEvents()
            self.assertEqual(len(view.annotations), 1)
            self.assertFalse(view._annotation_items[0].isSelected())
            self.assertEqual(view.annotation_tool, "ellipse")

            view.set_annotation_tool("select")
            center = view.mapFromScene(QPointF(90, 75))
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
            self.assertTrue(view._annotation_items[0].isSelected())
            QTest.keyClick(view, Qt.Key.Key_Escape)
            self.assertFalse(view._annotation_items[0].isSelected())
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
            self.assertTrue(view._annotation_items[0].isSelected())
            QTest.keyClick(view, Qt.Key.Key_Delete)
            self.assertEqual(len(view.annotations), 0)

            view.undo_annotation()
            self.assertEqual(len(view.annotations), 1)
            self.assertEqual(view.annotations[0].kind, "ellipse")
            view.undo_annotation()
            self.assertEqual(len(view.annotations), 0)
        finally:
            view.close()
            base.close()

    def test_ctrl_y_redo_restores_the_last_undone_annotation(self) -> None:
        base = Image.new("RGB", (240, 160), "#334455")
        view = ImageView()
        try:
            view.resize(480, 320)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("rectangle")
            start = view.mapFromScene(QPointF(30, 30))
            end = view.mapFromScene(QPointF(120, 90))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(view.viewport(), end)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            self.assertEqual(len(view.annotations), 1)

            view.undo_annotation()
            self.assertEqual(len(view.annotations), 0)
            view.redo_annotation()
            self.assertEqual(len(view.annotations), 1)
            self.assertEqual(view.annotations[0].kind, "rectangle")
        finally:
            view.close()
            base.close()

    def test_marks_outside_the_image_expand_the_export_canvas(self) -> None:
        base = Image.new("RGB", (100, 80), "#334455")
        view = ImageView()
        try:
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("rectangle")
            view.add_annotation(
                AnnotationSpec(
                    "rectangle",
                    QPointF(-24, -16),
                    QPointF(28, 24),
                    color="#22cc88",
                    size=4,
                )
            )
            self.assertLess(view.sceneRect().left(), 0)
            self.assertLess(view.sceneRect().top(), 0)
            bounds = view._markup_output_bounds(base.size)
            output = view.composited_pil(base)
            try:
                self.assertGreater(output.width, base.width)
                self.assertGreater(output.height, base.height)
                offset_x, offset_y = -bounds[0], -bounds[1]
                self.assertEqual(output.getpixel((offset_x + 50, offset_y + 40))[:3], (51, 68, 85))
                self.assertEqual(
                    output.getpixel((offset_x - 24, offset_y - 16))[:3],
                    (34, 204, 136),
                )
            finally:
                output.close()
        finally:
            view.close()
            base.close()

    def test_rectangle_can_be_reselected_moved_resized_and_restyled(self) -> None:
        base = Image.new("RGB", (320, 200), "#334455")
        view = ImageView()
        try:
            view.resize(640, 480)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("rectangle")
            self.application.processEvents()

            start = view.mapFromScene(QPointF(40, 40))
            end = view.mapFromScene(QPointF(140, 100))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(view.viewport(), end)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            self.application.processEvents()

            self.assertEqual(len(view.annotations), 1)
            item = view._annotation_items[0]
            self.assertFalse(item.isSelected())
            self.assertEqual(view.annotation_tool, "rectangle")

            view.set_annotation_tool("select")
            center = view.mapFromScene(QPointF(90, 70))
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
            self.assertTrue(item.isSelected())
            self.assertEqual(set(item._handle_positions()), {"nw", "ne", "se", "sw"})

            moved_center = view.mapFromScene(QPointF(115, 85))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=center)
            QTest.mouseMove(view.viewport(), moved_center)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=moved_center)
            self.application.processEvents()
            self.assertAlmostEqual(view.annotations[0].start.x(), 65, delta=2)
            self.assertAlmostEqual(view.annotations[0].start.y(), 55, delta=2)

            old_end = QPointF(view.annotations[0].end)
            handle = view.mapFromScene(old_end)
            resized = view.mapFromScene(QPointF(old_end.x() + 30, old_end.y() + 20))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=handle)
            QTest.mouseMove(view.viewport(), resized)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=resized)
            self.application.processEvents()
            self.assertAlmostEqual(view.annotations[0].end.x(), old_end.x() + 30, delta=2)
            self.assertAlmostEqual(view.annotations[0].end.y(), old_end.y() + 20, delta=2)

            view.set_annotation_color("#22cc88")
            view.set_annotation_size(10)
            self.assertEqual(view.annotations[0].color, "#22cc88")
            self.assertEqual(view.annotations[0].size, 10)

            view.undo_annotation()
            self.assertEqual(view.annotations[0].color, "#22cc88")
            self.assertEqual(view.annotations[0].size, 6)
            view.undo_annotation()
            self.assertEqual(view.annotations[0].color, "#ff4c48")
            view.undo_annotation()
            self.assertAlmostEqual(view.annotations[0].end.x(), old_end.x(), delta=2)
            self.assertAlmostEqual(view.annotations[0].end.y(), old_end.y(), delta=2)
            view.undo_annotation()
            self.assertAlmostEqual(view.annotations[0].start.x(), 40, delta=2)
            self.assertAlmostEqual(view.annotations[0].start.y(), 40, delta=2)
            view.undo_annotation()
            self.assertEqual(len(view.annotations), 0)
        finally:
            view.close()
            base.close()

    def test_drawing_mode_selects_existing_mark_and_continues_on_blank(self) -> None:
        base = Image.new("RGB", (320, 200), "#334455")
        view = ImageView()
        try:
            view.resize(640, 480)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("rectangle")
            self.application.processEvents()

            first_start = view.mapFromScene(QPointF(30, 30))
            first_end = view.mapFromScene(QPointF(200, 150))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first_start)
            QTest.mouseMove(view.viewport(), first_end)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=first_end)
            self.application.processEvents()
            self.assertEqual(view.annotation_tool, "rectangle")
            self.assertFalse(view._annotation_items[0].isSelected())

            existing = view.mapFromScene(QPointF(70, 60))
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=existing)
            self.application.processEvents()
            self.assertTrue(view._annotation_items[0].isSelected())
            self.assertEqual(view.annotation_tool, "rectangle")
            self.assertEqual(len(view.annotations), 1)

            view.set_annotation_size(9)
            self.assertEqual(view.annotations[0].size, 9)
            second_start = view.mapFromScene(QPointF(230, 35))
            second_end = view.mapFromScene(QPointF(290, 105))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=second_start)
            QTest.mouseMove(view.viewport(), second_end)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=second_end)
            self.application.processEvents()

            self.assertEqual(len(view.annotations), 2)
            self.assertEqual(view.annotations[0].start, QPointF(30, 30))
            self.assertEqual(view.annotations[0].end, QPointF(200, 150))
            self.assertEqual(view.annotations[1].size, 9)
            self.assertFalse(view._annotation_items[1].isSelected())
            self.assertEqual(view.annotation_tool, "rectangle")

            empty = view.mapFromScene(QPointF(260, 180))
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=empty)
            self.application.processEvents()
            self.assertEqual(len(view.annotations), 2)
            self.assertFalse(any(item.isSelected() for item in view._annotation_items))
        finally:
            view.close()
            base.close()

    def test_arrow_endpoint_can_be_reselected_and_dragged(self) -> None:
        base = Image.new("RGB", (320, 200), "#334455")
        view = ImageView()
        selected: list[tuple[str, str, int]] = []
        view.annotationSelected.connect(
            lambda kind, color, size: selected.append((kind, color, size))
        )
        try:
            view.resize(640, 480)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.add_annotation(
                AnnotationSpec(
                    "arrow",
                    QPointF(50, 60),
                    QPointF(150, 90),
                    color="#ff4c48",
                    size=6,
                )
            )
            view.set_annotation_tool("select")
            self.application.processEvents()

            endpoint = view.mapFromScene(QPointF(150, 90))
            replacement = view.mapFromScene(QPointF(190, 120))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=endpoint)
            QTest.mouseMove(view.viewport(), replacement)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=replacement)
            self.application.processEvents()

            self.assertTrue(view._annotation_items[0].isSelected())
            self.assertEqual(
                set(view._annotation_items[0]._handle_positions()),
                {"start", "control", "end"},
            )
            self.assertEqual(view.annotation_tool, "select")
            self.assertAlmostEqual(view.annotations[0].end.x(), 190, delta=2)
            self.assertAlmostEqual(view.annotations[0].end.y(), 120, delta=2)

            control = view.mapFromScene(QPointF(120, 90))
            curved_control = view.mapFromScene(QPointF(120, 30))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=control)
            QTest.mouseMove(view.viewport(), curved_control)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=curved_control)
            self.application.processEvents()
            self.assertIsNotNone(view.annotations[0].control)
            assert view.annotations[0].control is not None
            self.assertAlmostEqual(view.annotations[0].control.x(), 120, delta=2)
            self.assertAlmostEqual(view.annotations[0].control.y(), -30, delta=2)
            visible_control = view._annotation_items[0]._handle_positions()["control"]
            self.assertAlmostEqual(visible_control.x(), 120, delta=2)
            self.assertAlmostEqual(visible_control.y(), 30, delta=2)

            output = view.composited_pil(base)
            try:
                self.assertNotEqual(output.getpixel((120, 30))[:3], base.getpixel((120, 30)))
            finally:
                output.close()
            self.assertTrue(selected)
            self.assertEqual(selected[-1], ("arrow", "#ff4c48", 6))
        finally:
            view.close()
            base.close()

    def test_line_control_point_is_rendered_as_a_curve(self) -> None:
        base = Image.new("RGB", (220, 160), "#334455")
        view = ImageView()
        try:
            view.set_image(pil_to_qimage(base), fit=False)
            view.add_annotation(
                AnnotationSpec(
                    "line",
                    QPointF(20, 100),
                    QPointF(180, 100),
                    color="#ff4c48",
                    size=6,
                    control=QPointF(100, 20),
                )
            )
            item = view._annotation_items[0]
            item.setSelected(True)
            self.assertEqual(set(item._handle_positions()), {"start", "control", "end"})
            self.assertAlmostEqual(item._handle_positions()["control"].x(), 100)
            self.assertAlmostEqual(item._handle_positions()["control"].y(), 60)

            output = view.composited_pil(base)
            try:
                self.assertNotEqual(output.getpixel((100, 60))[:3], base.getpixel((100, 60)))
                self.assertEqual(output.getpixel((100, 100))[:3], base.getpixel((100, 100)))
            finally:
                output.close()
        finally:
            view.close()
            base.close()

    def test_text_pixel_size_scales_without_the_old_four_times_multiplier(self) -> None:
        base = Image.new("RGB", (360, 180), "#ffffff")

        def rendered_height(size: int) -> int:
            view = ImageView()
            view.set_image(pil_to_qimage(base), fit=False)
            view.add_annotation(
                AnnotationSpec(
                    "text",
                    QPointF(20, 20),
                    QPointF(300, 120),
                    color="#ff0000",
                    size=size,
                    text="字号测试",
                )
            )
            output = view.composited_pil(base).convert("RGB")
            try:
                difference = ImageChops.difference(output, base)
                box = difference.getbbox()
                assert box is not None
                return box[3] - box[1]
            finally:
                output.close()

        try:
            height_28 = rendered_height(28)
            height_56 = rendered_height(56)
            self.assertGreaterEqual(height_28, 20)
            self.assertLessEqual(height_28, 40)
            self.assertGreater(height_56, height_28 * 1.5)
            self.assertLess(height_56, height_28 * 2.5)
        finally:
            base.close()

    def test_inline_text_size_is_visual_pixels_when_a_small_image_is_enlarged(self) -> None:
        base = Image.new("RGB", (476, 172), "#ffffff")
        view = ImageView()
        try:
            view.resize(1948, 998)
            view.show()
            view.set_image(pil_to_qimage(base), fit=True)
            view.set_annotation_tool("text")
            view.set_annotation_size(20)
            self.application.processEvents()

            scale = view.transform().m11()
            self.assertGreater(scale, 3.0)
            view._begin_inline_text_editor(QPointF(200, 80), None)
            editor = view._text_editor
            self.assertIsNotNone(editor)
            assert editor is not None
            self.assertEqual(editor.font().pixelSize(), 20)
            editor.setText("输入文字")
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.application.processEvents()

            self.assertEqual(len(view.annotations), 1)
            source_size = view.annotations[0].size
            self.assertEqual(source_size, round(20 / scale))
            self.assertLess(source_size, 12)
            self.assertAlmostEqual(source_size * scale, 20, delta=scale / 2)
        finally:
            view.close()
            base.close()

    def test_markup_bar_fits_a_1280_pixel_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.png"
            Image.new("RGB", (640, 400), "#334455").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.resize(1280, 720)
                window.show()
                window.open_path(source)
                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                window.enter_markup()
                window.markup_toolbar.select_tool("text")
                self.application.processEvents()

                self.assertTrue(window.markup_toolbar.isVisible())
                self.assertLessEqual(window.markup_toolbar.width(), window.root.width() - 24)
                self.assertGreaterEqual(window.markup_toolbar.x(), 12)
                self.assertGreaterEqual(window.markup_toolbar.y(), window.title_bar.height())
            finally:
                window.close()
                self.application.processEvents()

    def test_arrow_and_brush_shortcuts_enter_markup_from_normal_viewing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "shortcut-tools.png"
            Image.new("RGB", (640, 400), "#334455").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.resize(900, 650)
                window.show()
                window.open_path(source)
                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                window.image_view.set_zoom_percent(160)
                expected_scale = window.image_view.transform().m11()
                self.assertFalse(window.markup_toolbar.isVisible())
                arrow_action = next(
                    action
                    for action in window._shortcut_actions
                    if action.shortcut().toString() == "Alt+A"
                )
                arrow_action.trigger()
                self.application.processEvents()
                self.assertTrue(window.markup_toolbar.isVisible())
                self.assertEqual(window.image_view.annotation_tool, "arrow")
                self.assertAlmostEqual(
                    window.image_view.transform().m11(), expected_scale, places=4
                )

                window.leave_markup()
                self.assertFalse(window.markup_toolbar.isVisible())
                brush_action = next(
                    action
                    for action in window._shortcut_actions
                    if action.shortcut().toString() == "Alt+B"
                )
                brush_action.trigger()
                self.application.processEvents()
                self.assertTrue(window.markup_toolbar.isVisible())
                self.assertEqual(window.image_view.annotation_tool, "freehand")
            finally:
                window.close()
                self.application.processEvents()

    def test_text_tool_uses_inline_create_edit_and_cancel_flow(self) -> None:
        base = Image.new("RGB", (320, 200), "#334455")
        view = ImageView()
        try:
            view.resize(640, 480)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("text")
            self.application.processEvents()

            position = view.mapFromScene(QPointF(60, 50))
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
            self.application.processEvents()
            editor = view._text_editor
            self.assertIsNotNone(editor)
            assert editor is not None
            self.assertEqual(editor.text(), "")
            editor.setText("第一处文字")
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.application.processEvents()

            self.assertEqual(len(view.annotations), 1)
            self.assertEqual(view.annotations[0].text, "第一处文字")

            position = view.mapFromScene(QPointF(60, 50))
            QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
            self.application.processEvents()
            editor = view._text_editor
            self.assertIsNotNone(editor)
            assert editor is not None
            self.assertEqual(editor.text(), "第一处文字")
            previous_font_size = editor.font().pixelSize()
            view.set_annotation_size(48)
            self.assertGreater(editor.font().pixelSize(), previous_font_size)
            editor.setText("修改后的文字")
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.application.processEvents()

            self.assertEqual(len(view.annotations), 1)
            self.assertEqual(view.annotations[0].text, "修改后的文字")
            self.assertEqual(view.annotations[0].size, 48)

            position = view.mapFromScene(QPointF(60, 50))
            QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
            self.application.processEvents()
            editor = view._text_editor
            self.assertIsNotNone(editor)
            assert editor is not None
            editor.setText("取消的文字修改")
            view.set_annotation_color("#22cc88")
            view.set_annotation_size(24)
            QTest.keyClick(editor, Qt.Key.Key_Escape)
            self.application.processEvents()
            self.assertEqual(view.annotations[0].text, "修改后的文字")
            self.assertEqual(view.annotations[0].color, "#ff4c48")
            self.assertEqual(view.annotations[0].size, 48)

            position = view.mapFromScene(QPointF(220, 150))
            view.set_annotation_tool("text")
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
            self.application.processEvents()
            editor = view._text_editor
            self.assertIsNotNone(editor)
            assert editor is not None
            editor.setText("不应保留")
            QTest.keyClick(editor, Qt.Key.Key_Escape)
            self.application.processEvents()
            self.assertIsNone(view._text_editor)
            self.assertEqual(len(view.annotations), 1)

            view.undo_annotation()
            self.assertEqual(view.annotations[0].text, "第一处文字")
            self.assertEqual(view.annotations[0].size, 20)
            view.undo_annotation()
            self.assertEqual(len(view.annotations), 0)
        finally:
            view.close()
            base.close()

    def test_switching_tools_commits_pending_text_before_drawing(self) -> None:
        base = Image.new("RGB", (320, 200), "#334455")
        view = ImageView()
        try:
            view.resize(640, 480)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("text")
            self.application.processEvents()

            position = view.mapFromScene(QPointF(80, 60))
            QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
            editor = view._text_editor
            self.assertIsNotNone(editor)
            assert editor is not None
            editor.setText("切换工具前提交")
            view.set_annotation_tool("rectangle")
            self.application.processEvents()

            self.assertIsNone(view._text_editor)
            self.assertEqual(len(view.annotations), 1)
            self.assertEqual(view.annotations[0].text, "切换工具前提交")
            self.assertEqual(view.annotation_tool, "rectangle")
            self.assertFalse(view._annotation_items[0].isSelected())
        finally:
            view.close()
            base.close()

    def test_inline_text_box_stays_compact_and_does_not_commit_on_focus_loss(self) -> None:
        base = Image.new("RGB", (320, 200), "#334455")
        view = ImageView()
        try:
            view.resize(640, 480)
            view.show()
            view.set_image(pil_to_qimage(base), fit=False)
            view.set_annotation_tool("text")
            view._begin_inline_text_editor(QPointF(80, 60), None)
            editor = view._text_editor
            self.assertIsNotNone(editor)
            assert editor is not None
            self.assertLess(editor.width(), 220)
            editor.setText("紧凑文字框")
            editor.clearFocus()
            self.application.processEvents()
            self.assertIs(view._text_editor, editor)
            self.assertEqual(len(view.annotations), 0)

            view.set_annotation_size(34)
            self.assertEqual(editor.font().pixelSize(), 34)
            QTest.keyClick(editor, Qt.Key.Key_Return)
            self.application.processEvents()
            self.assertIsNone(view._text_editor)
            self.assertEqual(view.annotations[0].text, "紧凑文字框")
        finally:
            view.close()
            base.close()

    def test_viewer_escape_and_save_as_finish_the_markup_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lifecycle.png"
            Image.new("RGB", (200, 120), "#334455").save(source)
            window = ViewerWindow(SettingsStore(root / "settings.json"))
            try:
                window.resize(800, 520)
                window.show()
                window.open_path(source)
                deadline = time.monotonic() + 5
                while not window.image_view.has_image and time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(0.01)

                window.enter_markup()
                window.image_view.add_annotation(
                    AnnotationSpec("redact", QPointF(20, 20), QPointF(80, 70), size=6)
                )
                window.image_view._select_annotation_item(window.image_view._annotation_items[0])
                window._escape()
                self.assertTrue(window.markup_toolbar.isVisible())
                self.assertFalse(window.image_view._annotation_items[0].isSelected())
                window._escape()
                self.assertFalse(window.markup_toolbar.isVisible())
                self.assertEqual(len(window.image_view.annotations), 0)

                window.enter_markup()
                window.image_view.add_annotation(
                    AnnotationSpec("redact", QPointF(90, 30), QPointF(150, 90), size=6)
                )
                output = root / "lifecycle-edited.png"
                with (
                    patch.object(
                        QFileDialog,
                        "getSaveFileName",
                        return_value=(str(output), "PNG 图片"),
                    ),
                    patch.object(QMessageBox, "information"),
                ):
                    window.replace_original()
                with Image.open(source) as original:
                    self.assertEqual(original.convert("RGB").getpixel((110, 50)), (51, 68, 85))
                with Image.open(output) as saved:
                    self.assertEqual(saved.convert("RGB").getpixel((110, 50)), (17, 19, 21))
                self.assertFalse(window.markup_toolbar.isVisible())
                self.assertEqual(len(window.image_view.annotations), 0)
            finally:
                window.close()
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
