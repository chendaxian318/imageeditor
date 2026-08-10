from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageChops
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QFrame, QMessageBox

from light_image_editor.qt_viewer import ViewerWindow
from light_image_editor.qt_viewer_widgets import (
    AnnotationSpec,
    ImageView,
    MarkupToolbar,
    pil_to_qimage,
    qimage_to_pil,
)
from light_image_editor.settings import SettingsStore


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
                self.assertIsNotNone(window.title_bar.findChild(QFrame, "windowControls"))
                self.assertTrue(window.empty_state.isVisible())
                self.assertEqual(window.empty_open_button.text(), "打开文件")
                self.assertEqual(
                    window.empty_hint.text(),
                    "点击按钮选择图片、PSD 或 PDF，也可以直接拖放到这里",
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
                self.assertFalse(window.empty_state.isVisible())
                self.assertTrue(window.info_label.isVisible())
                self.assertTrue(window.folder_button.isVisible())
            finally:
                window.close()
                self.application.processEvents()


class QtViewerDocumentFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication(["qt-document-tests"])

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
            first_page = QImage(600, 800, QImage.Format.Format_RGB32)
            first_page.fill(Qt.GlobalColor.white)
            view.set_document([(600, 800)] * 20, {0: first_page})
            self.application.processEvents()

            self.assertGreater(view.verticalScrollBar().maximum(), 0)
            self.assertTrue(requests)
            self.assertLess(len(set(requests)), 20)

            view.scroll_to_document_page(10)
            self.application.processEvents()
            self.assertEqual(view.current_document_page, 10)
            self.assertIn(10, requests)
            self.assertIn(0, releases)
        finally:
            view.close()

    def test_pdf_continuous_scroll_and_markup_export_keep_source_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "pages.pdf"
            first = Image.new("RGB", (120, 80), "#245680")
            second = Image.new("RGB", (120, 80), "#a03040")
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
                self.assertGreater(window.image_view.verticalScrollBar().maximum(), 0)
                self.assertFalse(window.previous_button.isVisible())
                self.assertFalse(window.next_button.isVisible())
                window.image_view.scroll_to_document_page(1)
                self._wait_for_pdf_page(window, 1)
                self.assertIn("第 2 / 2 页", window.info_label.text())

                window.enter_markup()
                self.assertFalse(window.image_view.document_mode)
                self.assertEqual(window.markup_toolbar.replace_button.text(), "导出副本")
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
            self.assertEqual(toolbar.size_spin.prefix(), "大小 ")
            self.assertEqual((toolbar.size_spin.minimum(), toolbar.size_spin.maximum()), (2, 32))
            self.assertNotIn(
                "仅复制",
                {button.text() for button in toolbar.findChildren(type(toolbar.color_button))},
            )
            self.assertEqual(toolbar.size_spin.value(), 6)
            toolbar.sync_annotation("text", "#22cc88", 18)
            self.assertTrue(toolbar.tool_buttons["select"].isChecked())
            self.assertEqual(toolbar._property_kind, "text")
            self.assertEqual(toolbar.size_spin.value(), 18)
            self.assertEqual(toolbar._color.name(), "#22cc88")
            toolbar.clear_annotation_selection()
            self.assertFalse(toolbar.color_button.isEnabled())
            self.assertFalse(toolbar.size_spin.isEnabled())
            toolbar.select_tool("rectangle")
            self.assertTrue(toolbar.color_button.isEnabled())
            self.assertTrue(toolbar.size_spin.isEnabled())
            self.assertFalse(hasattr(toolbar, "text_edit"))
        finally:
            toolbar.close()

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
            self.assertNotEqual(output.getpixel((60, 45))[:3], base.getpixel((60, 45)))
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
            self.assertEqual(live.getpixel((20, 60))[:3], expected.getpixel((20, 60))[:3])
            self.assertEqual(live.getpixel((85, 60))[:3], expected.getpixel((85, 60))[:3])
        finally:
            live.close()
            expected.close()
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
            self.assertTrue(view._annotation_items[0].isSelected())

            QTest.keyClick(view, Qt.Key.Key_Escape)
            self.assertFalse(view._annotation_items[0].isSelected())
            center = view.mapFromScene(QPointF(90, 75))
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
            self.assertTrue(item.isSelected())
            self.assertEqual(set(item._handle_positions()), {"nw", "ne", "se", "sw"})

            center = view.mapFromScene(QPointF(90, 70))
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

    def test_drawing_mode_can_overlap_an_existing_selected_annotation(self) -> None:
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
            self.assertEqual(view.annotation_tool, "select")
            self.assertTrue(view._annotation_items[0].isSelected())

            view.set_annotation_tool("rectangle")
            view.set_annotation_size(9)
            self.assertFalse(view._annotation_items[0].isSelected())
            self.assertEqual(view.annotations[0].size, 6)
            second_start = view.mapFromScene(QPointF(70, 60))
            second_end = view.mapFromScene(QPointF(140, 110))
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=second_start)
            QTest.mouseMove(view.viewport(), second_end)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=second_end)
            self.application.processEvents()

            self.assertEqual(len(view.annotations), 2)
            self.assertEqual(view.annotations[0].start, QPointF(30, 30))
            self.assertEqual(view.annotations[0].end, QPointF(200, 150))
            self.assertEqual(view.annotations[1].size, 9)
            self.assertTrue(view._annotation_items[1].isSelected())
            self.assertEqual(view.annotation_tool, "select")

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

    def test_viewer_escape_and_replace_finish_the_markup_lifecycle(self) -> None:
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
                with patch.object(
                    QMessageBox,
                    "question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    window.replace_original()
                with Image.open(source) as replaced:
                    self.assertEqual(replaced.convert("RGB").getpixel((110, 50)), (17, 19, 21))
                self.assertFalse(window.markup_toolbar.isVisible())
                self.assertEqual(len(window.image_view.annotations), 0)
            finally:
                window.close()
                self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
