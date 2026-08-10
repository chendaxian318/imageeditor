"""Viewer-only PySide6 application shell.

The image/session services remain toolkit independent.  This module owns only
the native window, Qt widgets, presentation state and keyboard dispatch.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from pathlib import Path
from typing import cast

from PIL import Image
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from image_ops import ConflictPolicy, ExportOptions, save_image

from .image_loader import ImageLoader
from .navigation import NavigationSession
from .pdf_support import PDF_EXTENSION
from .psd_support import PSD_EXTENSIONS
from .qt_viewer_config import QtViewerConfig, parse_window_geometry
from .qt_viewer_widgets import (
    CANVAS,
    MUTED,
    FilmstripPanel,
    ImageView,
    MarkupToolbar,
    NativeResizeFilter,
    NavigationButton,
    ViewerIconButton,
    ViewerSettingsDialog,
    ViewerTitleBar,
    ViewerToolbar,
    make_shortcut,
    pil_to_qimage,
)
from .settings import SettingsStore
from .thumbnails import ThumbnailLoader

READ_ONLY_EDIT_SOURCE_EXTENSIONS = {PDF_EXTENSION, *PSD_EXTENSIONS}


class _UiBridge(QObject):
    scheduleRequested = Signal(int, object)
    imageLoaded = Signal(str, object)
    documentPageLoaded = Signal(str, int, object)
    thumbnailLoaded = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self.scheduleRequested.connect(self._schedule)

    @staticmethod
    def _schedule(delay_ms: int, callback: object) -> None:
        if callable(callback):
            QTimer.singleShot(delay_ms, callback)


class ViewerWindow(QMainWindow):
    """Frameless viewer whose move/resize loops are owned by Windows."""

    def __init__(self, store: SettingsStore | None = None) -> None:
        super().__init__()
        self.store = store or SettingsStore()
        self.config, self._settings_base = QtViewerConfig.load(self.store)
        self.navigation = NavigationSession([])
        self.image_loader = ImageLoader()
        self.thumbnail_loader = ThumbnailLoader()
        self.current_image: Image.Image | None = None
        self._document_path: Path | None = None
        self._document_page_index = 0
        self._document_page_count = 1
        self._document_page_sizes: list[tuple[int, int]] = []
        self._document_images: dict[int, Image.Image] = {}
        self._document_pending: set[int] = set()
        self._pdf_edit_mode = False
        self._load_generation = 0
        self._filmstrip_scope = "folder"
        self._shortcut_actions: list[QAction] = []
        self._clean_mode = False
        self._closing = False

        self.bridge = _UiBridge()
        self.bridge.imageLoaded.connect(self._finish_image_load)
        self.bridge.documentPageLoaded.connect(self._finish_document_page_load)
        self.bridge.thumbnailLoaded.connect(self._finish_thumbnail_load)

        self.setWindowTitle("CSImage")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAcceptDrops(True)
        self.setMinimumSize(720, 480)
        width, height, x, y = parse_window_geometry(self.config.window_geometry)
        self.resize(width, height)
        if x is not None and y is not None:
            self.move(x, y)

        self.root = QWidget(self)
        self.root.setObjectName("viewerRoot")
        self.root.setStyleSheet(f"#viewerRoot {{background: {CANVAS};}}")
        self.setCentralWidget(self.root)
        self.title_bar = ViewerTitleBar(self)
        self.title_bar.setParent(self.root)
        self.image_view = ImageView(self.root)
        self.image_view.zoomChanged.connect(self._sync_zoom_text)
        self.image_view.pointerMoved.connect(self._viewer_pointer_moved)
        self.image_view.documentPageChanged.connect(self._document_visible_page_changed)
        self.image_view.documentPagesRequested.connect(self._load_document_pages)
        self.image_view.documentPagesReleased.connect(self._release_document_pages)

        self.empty_state = QWidget(self.root)
        self.empty_state.setObjectName("viewerEmptyState")
        self.empty_state.setAccessibleName("未打开图片或文档")
        self.empty_state.setStyleSheet(
            """
            #viewerEmptyState {
                background: #15181d;
                border: 1px solid #343a43;
                border-radius: 20px;
            }
            QLabel {background: transparent; border: 0;}
            QPushButton {
                color: #07131d;
                background: #89c8ff;
                border: 0;
                border-radius: 12px;
                padding: 0 28px;
                font: 600 15px 'Microsoft YaHei UI';
            }
            QPushButton:hover {background: #acd9ff;}
            QPushButton:pressed {background: #6fb8f5;}
            """
        )
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(36, 28, 36, 30)
        empty_layout.setSpacing(10)
        self.empty_icon = QLabel("＋", self.empty_state)
        self.empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_icon.setStyleSheet("color: #89c8ff; font: 36px 'Microsoft YaHei UI';")
        self.empty_title = QLabel("还没有打开图片或文档", self.empty_state)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setStyleSheet("color: #f5f7fa; font: 600 20px 'Microsoft YaHei UI';")
        self.empty_hint = QLabel(
            "点击按钮选择图片、PSD/PSDT 或 PDF，也可以直接拖放到这里", self.empty_state
        )
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setStyleSheet(f"color: {MUTED}; font: 13px 'Microsoft YaHei UI';")
        self.empty_open_button = QPushButton("打开文件", self.empty_state)
        self.empty_open_button.setAccessibleName("打开图片或文档")
        self.empty_open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.empty_open_button.setFixedSize(168, 44)
        self.empty_open_button.clicked.connect(self.choose_images)
        empty_layout.addWidget(self.empty_icon)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_hint)
        empty_layout.addSpacing(4)
        empty_layout.addWidget(self.empty_open_button, 0, Qt.AlignmentFlag.AlignHCenter)

        self.toolbar = ViewerToolbar(self.root)
        self._build_toolbar()
        self.previous_button = NavigationButton(-1, self.root)
        self.next_button = NavigationButton(1, self.root)
        self.previous_button.clicked.connect(lambda: self.navigate(-1))
        self.next_button.clicked.connect(lambda: self.navigate(1))

        self.info_label = QLabel("", self.root)
        self.info_label.setStyleSheet(
            f"color: {MUTED}; background: transparent; font: 12px 'Microsoft YaHei UI';"
        )
        self.folder_button = ViewerIconButton(
            text="▦ 此文件夹", tooltip="预览当前文件夹或收藏的图片", parent=self.root, width=94
        )
        self.folder_button.clicked.connect(self.toggle_filmstrip)
        self.filmstrip = FilmstripPanel(self.root)
        self.filmstrip.hide()
        self.filmstrip.pathSelected.connect(self.select_path)
        self.filmstrip.scopeChanged.connect(self.set_filmstrip_scope)
        self.markup_toolbar = MarkupToolbar(self.root)
        self.markup_toolbar.hide()
        self.markup_toolbar.toolChanged.connect(self.image_view.set_annotation_tool)
        self.markup_toolbar.colorChanged.connect(self.image_view.set_annotation_color)
        self.markup_toolbar.sizeChanged.connect(self.image_view.set_annotation_size)
        self.image_view.annotationSizeSelected.connect(self.markup_toolbar.set_size)
        self.image_view.annotationSelected.connect(self.markup_toolbar.sync_annotation)
        self.image_view.annotationSelectionCleared.connect(
            self.markup_toolbar.clear_annotation_selection
        )
        self.markup_toolbar.undoRequested.connect(self.image_view.undo_annotation)
        self.markup_toolbar.cancelRequested.connect(self.leave_markup)
        self.markup_toolbar.replaceRequested.connect(self.replace_original)

        self._resize_filter = NativeResizeFilter(self)
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self._resize_filter)
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(1300)
        self._auto_hide_timer.timeout.connect(lambda: self.toolbar.animate_visible(False))
        self._install_shortcuts()
        self.apply_topmost(self.config.topmost, show_again=False)
        self._apply_toolbar_mode()
        self._set_empty_state_visible(True)
        self._layout_overlays()

    def _build_toolbar(self) -> None:
        self.open_button = ViewerIconButton(
            text="打开",
            tooltip="打开图片、PSD/PSDT 或 PDF（Ctrl+O）",
            parent=self.toolbar,
            width=58,
        )
        self.open_button.clicked.connect(self.choose_images)
        self.toolbar.add_button(self.open_button)
        self.toolbar.add_separator()

        minus = ViewerIconButton("minus", tooltip="缩小图片", parent=self.toolbar)
        minus.clicked.connect(lambda: self.image_view.step_zoom(-self.config.zoom_step))
        self.toolbar.add_button(minus)
        self.zoom_edit = QLineEdit("100%", self.toolbar)
        self.zoom_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_edit.setFixedWidth(58)
        self.zoom_edit.setToolTip("输入缩放比例")
        self.zoom_edit.editingFinished.connect(self._apply_zoom_text)
        self.toolbar.button_layout.addWidget(self.zoom_edit)
        plus = ViewerIconButton("plus", tooltip="放大图片", parent=self.toolbar)
        plus.clicked.connect(lambda: self.image_view.step_zoom(self.config.zoom_step))
        self.toolbar.add_button(plus)

        actual = ViewerIconButton(text="1:1", tooltip="回到 100% 原图比例", parent=self.toolbar)
        actual.clicked.connect(self.image_view.actual_size)
        self.toolbar.add_button(actual)
        fit = ViewerIconButton("fit", tooltip="适应当前窗口", parent=self.toolbar)
        fit.clicked.connect(self.image_view.fit_to_window)
        self.toolbar.add_button(fit)
        rotate = ViewerIconButton("rotate", tooltip="顺时针旋转预览", parent=self.toolbar)
        rotate.clicked.connect(self.rotate_preview)
        self.toolbar.add_button(rotate)
        self.toolbar.add_separator()

        self.favorite_button = ViewerIconButton(
            "favorite", tooltip="收藏当前图片", parent=self.toolbar
        )
        self.favorite_button.clicked.connect(self.toggle_favorite)
        self.toolbar.add_button(self.favorite_button)
        self.pin_button = ViewerIconButton("pin", tooltip="窗口置顶", parent=self.toolbar)
        self.pin_button.clicked.connect(lambda: self.apply_topmost(not self.config.topmost))
        self.toolbar.add_button(self.pin_button)
        edit = ViewerIconButton("edit", tooltip="在副本上标注图片", parent=self.toolbar)
        edit.clicked.connect(self.enter_markup)
        self.toolbar.add_button(edit)
        settings = ViewerIconButton("settings", tooltip="看图设置", parent=self.toolbar)
        settings.clicked.connect(self.show_settings)
        self.toolbar.add_button(settings)

    def _install_shortcuts(self) -> None:
        for action in self._shortcut_actions:
            self.removeAction(action)
        self._shortcut_actions.clear()
        callbacks: dict[str, Callable[[], object]] = {
            "previous": lambda: self.navigate(-1),
            "next": lambda: self.navigate(1),
            "zoom_in": lambda: self.image_view.step_zoom(self.config.zoom_step),
            "zoom_out": lambda: self.image_view.step_zoom(-self.config.zoom_step),
            "toggle_toolbar": self.toggle_toolbar,
            "toggle_clean": self.toggle_clean_mode,
            "toggle_topmost": lambda: self.apply_topmost(not self.config.topmost),
            "toggle_favorite": self.toggle_favorite,
            "fit": self.image_view.fit_to_window,
            "actual_size": self.image_view.actual_size,
            "fullscreen": self.toggle_fullscreen,
            "gallery": self.toggle_filmstrip,
        }
        for name, callback in callbacks.items():
            for sequence in self.config.shortcuts.get(name, []):
                self._shortcut_actions.append(make_shortcut(sequence, self, callback))
        self._shortcut_actions.extend(
            [
                make_shortcut("Ctrl+O", self, self.choose_images),
                make_shortcut("Ctrl+Z", self, self._undo_in_viewer),
                make_shortcut("Esc", self, self._escape),
            ]
        )

    def _layout_overlays(self) -> None:
        width, height = self.root.width(), self.root.height()
        title_height = self.title_bar.height()
        self.title_bar.setGeometry(0, 0, width, title_height)
        self.image_view.setGeometry(0, title_height, width, max(1, height - title_height))
        empty_width = min(520, max(340, width - 80))
        empty_height = 244
        content_height = max(1, height - title_height)
        self.empty_state.setGeometry(
            max(0, (width - empty_width) // 2),
            title_height + max(56, (content_height - empty_height) // 2),
            empty_width,
            empty_height,
        )
        self.toolbar.adjustSize()
        toolbar_x = max(12, (width - self.toolbar.width()) // 2)
        self.toolbar.move(toolbar_x, title_height + 12)
        side_width = max(72, min(132, round(width * 0.075)))
        side_height = max(150, min(330, height - title_height - 150))
        side_y = title_height + max(42, (height - title_height - side_height) // 2)
        self.previous_button.setGeometry(0, side_y, side_width, side_height)
        self.next_button.setGeometry(width - side_width, side_y, side_width, side_height)
        self.info_label.adjustSize()
        self.info_label.move(16, height - self.info_label.height() - 12)
        self.folder_button.adjustSize()
        self.folder_button.move(
            width - self.folder_button.width() - 12,
            height - self.folder_button.height() - 7,
        )
        panel_width = min(1000, max(520, width - 64))
        self.filmstrip.setGeometry(
            (width - panel_width) // 2,
            max(title_height + 60, height - 158),
            panel_width,
            142,
        )
        self.markup_toolbar.adjustSize()
        self.markup_toolbar.move(
            max(12, (width - self.markup_toolbar.width()) // 2),
            max(title_height + 60, height - self.markup_toolbar.height() - 22),
        )
        for widget in (
            self.toolbar,
            self.empty_state,
            self.previous_button,
            self.next_button,
            self.info_label,
            self.folder_button,
            self.filmstrip,
            self.markup_toolbar,
        ):
            widget.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._layout_overlays()

    def choose_images(self) -> None:
        image_patterns = (
            "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.avif *.heic *.heif *.psd *.psdt"
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "打开图片或文档",
            str(self.navigation.current.parent if self.navigation.current else Path.home()),
            f"支持的文件 ({image_patterns} *.pdf);;图片 ({image_patterns});;PDF 文档 (*.pdf)",
        )
        if paths:
            self.open_paths(paths)

    def open_paths(self, paths: Sequence[str | Path], confirm: bool = True) -> None:
        del confirm
        session = NavigationSession.from_activation(paths)
        if not session.files:
            return
        self.navigation = session
        self._filmstrip_scope = "folder"
        self.filmstrip.set_scope("folder")
        self.load_current()

    def open_path(self, path: str | Path, confirm: bool = True) -> None:
        self.open_paths([path], confirm=confirm)

    def handle_activation(self, paths: Sequence[str | Path]) -> None:
        if paths:
            self.open_paths(paths)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def load_current(self) -> None:
        path = self.navigation.current
        if path is None:
            self._set_empty_state_visible(True)
            return
        if self.markup_toolbar.isVisible():
            self.leave_markup()
        if path.suffix.casefold() == PDF_EXTENSION:
            self._close_loaded_content()
            self._document_path = path
            self._document_page_index = 0
            self._document_page_count = 1
            self._document_page_sizes.clear()
            self._document_pending.clear()
            page_index: int | None = 0
        else:
            self._close_loaded_content()
            self._document_path = None
            self._document_page_index = 0
            self._document_page_count = 1
            self._document_page_sizes.clear()
            self._document_pending.clear()
            page_index = None
        self._set_empty_state_visible(False)
        self._load_generation += 1
        generation = self._load_generation
        preview = (
            None
            if path.suffix.casefold() == PDF_EXTENSION
            else self.image_loader.cached_preview(path, page_index)
        )
        if preview is not None:
            try:
                self.image_view.set_image(pil_to_qimage(preview), fit=True)
            finally:
                preview.close()

        def completed(future: Future[Image.Image]) -> None:
            self.bridge.imageLoaded.emit(str(path), (generation, future))

        self.image_loader.load(path, completed, take_cached=True, page_index=page_index)
        self.image_loader.prefetch(self.navigation.neighbors())
        self._update_chrome(path)

    def _finish_image_load(self, path_text: str, payload: object) -> None:
        generation, future = cast(tuple[int, Future[Image.Image]], payload)
        if generation != self._load_generation or self.navigation.current != Path(path_text):
            if future.done() and future.exception() is None:
                future.result().close()
            return
        try:
            image = future.result()
        except Exception as error:
            self.image_view.clear_image()
            self._set_empty_state_visible(True)
            QMessageBox.warning(self, "无法打开图片", str(error))
            return
        path = Path(path_text)
        if path.suffix.casefold() == PDF_EXTENSION:
            self._close_loaded_content()
            self._document_page_index = 0
            self._document_page_count = max(1, int(image.info.get("pdf_page_count", 1)))
            raw_sizes = image.info.get("pdf_page_sizes", ())
            self._document_page_sizes = [
                (max(1, int(width)), max(1, int(height)))
                for width, height in raw_sizes
                if isinstance(width, (int, float)) and isinstance(height, (int, float))
            ]
            if len(self._document_page_sizes) != self._document_page_count:
                self._document_page_sizes = [image.size] * self._document_page_count
            self._document_images[0] = image
            self.current_image = image
            self.image_view.set_document(
                self._document_page_sizes,
                {0: pil_to_qimage(image)},
                current_index=0,
            )
        else:
            if self.current_image is not None:
                self.current_image.close()
            self.current_image = image
            self.image_view.set_image(pil_to_qimage(image), fit=True)
        self._set_empty_state_visible(False)
        self._update_chrome(path)

    def _close_loaded_content(self) -> None:
        document_image_ids = {id(image) for image in self._document_images.values()}
        if self.current_image is not None and id(self.current_image) not in document_image_ids:
            self.current_image.close()
        for image in self._document_images.values():
            image.close()
        self._document_images.clear()
        self.current_image = None

    def _load_document_pages(self, page_indices: object) -> None:
        path = self.navigation.current
        if (
            self._pdf_edit_mode
            or path is None
            or path.suffix.casefold() != PDF_EXTENSION
            or self._document_path != path
        ):
            return
        if not isinstance(page_indices, tuple) or not all(
            isinstance(index, int) for index in page_indices
        ):
            return
        indices = cast(tuple[int, ...], page_indices)
        generation = self._load_generation
        for page_index in indices:
            if (
                not 0 <= page_index < self._document_page_count
                or page_index in self._document_images
                or page_index in self._document_pending
            ):
                continue
            self._document_pending.add(page_index)
            future = self.image_loader.load(path, page_index=page_index)

            def completed(
                result: Future[Image.Image],
                index: int = page_index,
                document_path: Path = path,
                load_generation: int = generation,
            ) -> None:
                self.bridge.documentPageLoaded.emit(
                    str(document_path), index, (load_generation, result)
                )

            future.add_done_callback(completed)

    def _finish_document_page_load(
        self,
        path_text: str,
        page_index: int,
        payload: object,
    ) -> None:
        generation, future = cast(tuple[int, Future[Image.Image]], payload)
        current_path = self.navigation.current
        if (
            generation != self._load_generation
            or current_path != Path(path_text)
            or self._document_path != current_path
        ):
            if future.done() and future.exception() is None:
                future.result().close()
            return
        self._document_pending.discard(page_index)
        try:
            image = future.result()
        except Exception:
            return
        old_image = self._document_images.pop(page_index, None)
        if old_image is not None:
            old_image.close()
        self._document_images[page_index] = image
        self.image_view.set_document_page(page_index, pil_to_qimage(image))
        if page_index == self._document_page_index:
            self.current_image = image
            self._update_chrome(Path(path_text))

    def _release_document_pages(self, page_indices: object) -> None:
        if not isinstance(page_indices, tuple) or not all(
            isinstance(index, int) for index in page_indices
        ):
            return
        indices = cast(tuple[int, ...], page_indices)
        for page_index in indices:
            if page_index == self._document_page_index:
                continue
            image = self._document_images.pop(page_index, None)
            if image is not None:
                image.close()

    def _document_visible_page_changed(self, page_index: int) -> None:
        path = self.navigation.current
        if (
            self._pdf_edit_mode
            or path is None
            or path.suffix.casefold() != PDF_EXTENSION
            or not 0 <= page_index < self._document_page_count
        ):
            return
        self._document_page_index = page_index
        self.current_image = self._document_images.get(page_index)
        self._update_chrome(path)

    def _update_chrome(self, path: Path) -> None:
        is_pdf = path.suffix.casefold() == PDF_EXTENSION
        if is_pdf:
            page_position = f"第 {self._document_page_index + 1} / {self._document_page_count} 页"
            title = f"{path.name} · {page_position}"
        else:
            page_position = ""
            title = path.name
        self.setWindowTitle(title)
        self.title_bar.set_title(title)
        position = self.navigation.position or (1, 1)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if is_pdf and 0 <= self._document_page_index < len(self._document_page_sizes):
            width, height = self._document_page_sizes[self._document_page_index]
            fmt = "PDF"
        elif self.current_image is not None:
            width, height = self.current_image.size
            fmt = (self.current_image.format or path.suffix.lstrip(".")).upper()
        else:
            width = height = 0
            fmt = path.suffix.lstrip(".").upper()
        size_text = self._human_size(size)
        if is_pdf:
            file_position = f"  ·  文件 {position[0]} / {position[1]}" if position[1] > 1 else ""
            info = f"PDF  ·  {width} × {height}  ·  {size_text}  ·  {page_position}{file_position}"
        elif path.suffix.casefold() in PSD_EXTENSIONS:
            photoshop_format = path.suffix.lstrip(".").upper()
            info = (
                f"{photoshop_format} 合成预览  ·  {width} × {height}  ·  {size_text}"
                f"  ·  第 {position[0]} / {position[1]} 张"
            )
        else:
            info = (
                f"{fmt}  ·  {width} × {height}  ·  {size_text}"
                f"  ·  第 {position[0]} / {position[1]} 张"
            )
        self.info_label.setText(info)
        self.info_label.adjustSize()
        has_many = self._has_navigation_targets()
        self.previous_button.setEnabled(has_many)
        self.next_button.setEnabled(has_many)
        self.previous_button.setVisible(has_many)
        self.next_button.setVisible(has_many)
        chrome_visible = not self._clean_mode
        self.info_label.setVisible(chrome_visible)
        self.folder_button.setVisible(chrome_visible)
        self.favorite_button.set_selected(self.config.is_favorite(path))
        if self.filmstrip.isVisible():
            self.refresh_filmstrip()
        self._layout_overlays()

    def _set_empty_state_visible(self, visible: bool) -> None:
        """Keep the no-document canvas actionable and remove stale image controls."""

        self.empty_state.setVisible(visible)
        if visible:
            self.previous_button.hide()
            self.next_button.hide()
            self.info_label.hide()
            self.folder_button.hide()
            self.filmstrip.hide()
            self.markup_toolbar.hide()
        else:
            has_many = self._has_navigation_targets()
            self.previous_button.setVisible(has_many)
            self.next_button.setVisible(has_many)
            chrome_visible = not self._clean_mode
            self.info_label.setVisible(chrome_visible)
            self.folder_button.setVisible(chrome_visible)
        self._layout_overlays()

    @staticmethod
    def _human_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return "0 B"

    def navigate(self, direction: int) -> None:
        if self.markup_toolbar.isVisible():
            return
        if len(self.navigation.files) <= 1:
            return
        if self.navigation.target(direction) is not None:
            self.load_current()

    def _has_navigation_targets(self) -> bool:
        return len(self.navigation.files) > 1

    def select_path(self, path_text: str) -> None:
        path = Path(path_text)
        if not self.navigation.select(path):
            favorites = [
                Path(value) for value in self.config.favorite_paths if Path(value).is_file()
            ]
            if path in favorites:
                self.navigation = NavigationSession(favorites, favorites.index(path), "我的收藏")
            else:
                self.navigation = NavigationSession.from_activation([path])
        self._document_path = None
        self.load_current()

    def toggle_filmstrip(self) -> None:
        self.filmstrip.setVisible(not self.filmstrip.isVisible())
        if self.filmstrip.isVisible():
            self.refresh_filmstrip()
        self._layout_overlays()

    def set_filmstrip_scope(self, scope: str) -> None:
        self._filmstrip_scope = scope
        self.refresh_filmstrip()

    def refresh_filmstrip(self) -> None:
        paths = (
            self.navigation.files
            if self._filmstrip_scope == "folder"
            else [Path(value) for value in self.config.favorite_paths if Path(value).is_file()]
        )
        self.filmstrip.set_paths(paths, self.navigation.current)
        for path in paths:
            future = self.thumbnail_loader.load(path)

            def completed(result: Future[Image.Image], item_path: Path = path) -> None:
                self.bridge.thumbnailLoaded.emit(str(item_path), result)

            future.add_done_callback(completed)

    def _finish_thumbnail_load(self, path_text: str, future: object) -> None:
        thumbnail_future = cast(Future[Image.Image], future)
        try:
            image = thumbnail_future.result()
        except Exception:
            return
        try:
            self.filmstrip.set_thumbnail(Path(path_text), pil_to_qimage(image))
        finally:
            image.close()

    def toggle_favorite(self) -> None:
        path = self.navigation.current
        if path is None:
            return
        selected = self.config.toggle_favorite(path)
        self.favorite_button.set_selected(selected)
        self.config.save(self.store, self._settings_base)
        if self.filmstrip.isVisible() and self._filmstrip_scope == "favorites":
            self.refresh_filmstrip()

    def rotate_preview(self) -> None:
        path = self.navigation.current
        if path is not None and path.suffix.casefold() == PDF_EXTENSION:
            return
        if self.current_image is None:
            return
        zoom_percent = self.image_view.zoom_percent()
        rotated = self.current_image.transpose(Image.Transpose.ROTATE_270)
        self.current_image.close()
        self.current_image = rotated
        self.image_view.set_image(pil_to_qimage(rotated), fit=False)
        self.image_view.set_zoom_percent(zoom_percent)

    def _sync_zoom_text(self, percent: int) -> None:
        if not self.zoom_edit.hasFocus():
            self.zoom_edit.setText(f"{percent}%")

    def _apply_zoom_text(self) -> None:
        text = self.zoom_edit.text().strip().removesuffix("%")
        try:
            percent = int(float(text))
        except ValueError:
            percent = self.image_view.zoom_percent()
        self.image_view.set_zoom_percent(percent)

    def apply_topmost(self, enabled: bool, *, show_again: bool = True) -> None:
        self.config.topmost = enabled
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.pin_button.set_selected(enabled)
        if show_again:
            self.show()
            self.raise_()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def toggle_toolbar(self) -> None:
        self.config.toolbar_visible = not self.config.toolbar_visible
        self._apply_toolbar_mode()

    def _apply_toolbar_mode(self) -> None:
        if not self.config.toolbar_visible or self._clean_mode:
            self.toolbar.hide()
            return
        self.toolbar.show()
        self.toolbar.opacity_effect.setOpacity(1.0)
        if self.config.toolbar_mode == "auto":
            self._auto_hide_timer.start()

    def _viewer_pointer_moved(self, y: int) -> None:
        if (
            self.config.toolbar_visible
            and self.config.toolbar_mode == "auto"
            and not self._clean_mode
            and y < 150
        ):
            self.toolbar.animate_visible(True)
            self._auto_hide_timer.start()

    def toggle_clean_mode(self) -> None:
        self._clean_mode = not self._clean_mode
        visible = not self._clean_mode
        self.title_bar.setVisible(visible)
        self.info_label.setVisible(visible)
        self.folder_button.setVisible(visible)
        if self._clean_mode:
            self.toolbar.hide()
            self.filmstrip.hide()
        else:
            self._apply_toolbar_mode()

    def enter_markup(self) -> None:
        path = self.navigation.current
        if self.current_image is None or path is None:
            return
        if path.suffix.casefold() == PDF_EXTENSION:
            self._pdf_edit_mode = True
            self.image_view.set_image(pil_to_qimage(self.current_image), fit=True)
        else:
            self._pdf_edit_mode = False
        self.markup_toolbar.set_read_only_source(
            path.suffix.casefold() in READ_ONLY_EDIT_SOURCE_EXTENSIONS
        )
        self.markup_toolbar.show()
        self.markup_toolbar.select_tool("rectangle")
        self.previous_button.hide()
        self.next_button.hide()
        self._layout_overlays()

    def leave_markup(self) -> None:
        self.image_view.clear_annotations()
        self.image_view.set_annotation_tool("")
        self.markup_toolbar.hide()
        restore_pdf = self._pdf_edit_mode
        self._pdf_edit_mode = False
        if restore_pdf:
            self._restore_pdf_document()
        path = self.navigation.current
        if path is not None:
            self._update_chrome(path)

    def _restore_pdf_document(self) -> None:
        path = self.navigation.current
        if path is None or path.suffix.casefold() != PDF_EXTENSION or not self._document_page_sizes:
            return
        page_images = {
            page_index: pil_to_qimage(image) for page_index, image in self._document_images.items()
        }
        self.image_view.set_document(
            self._document_page_sizes,
            page_images,
            current_index=self._document_page_index,
        )
        self.current_image = self._document_images.get(self._document_page_index)

    def _undo_in_viewer(self) -> None:
        if self.markup_toolbar.isVisible():
            if self.image_view.undo_inline_text():
                return
            self.image_view.undo_annotation()

    def replace_original(self) -> None:
        path = self.navigation.current
        if self.current_image is None or path is None:
            return
        if path.suffix.casefold() in READ_ONLY_EDIT_SOURCE_EXTENSIONS:
            self._export_markup_copy(path)
            return
        answer = QMessageBox.question(
            self,
            "替换原图",
            "这会用当前标注结果替换原文件。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.image_view.commit_pending_text()
        composited = self.image_view.composited_pil(self.current_image)
        try:
            save_image(
                composited,
                path,
                ExportOptions(path.suffix.lstrip(".") or "PNG"),
                conflict_policy=ConflictPolicy.OVERWRITE,
                allow_animation_flatten=True,
                allow_transparency_flatten=True,
            )
        except Exception as error:
            QMessageBox.warning(self, "无法替换原图", str(error))
            composited.close()
            return
        composited.close()
        self.thumbnail_loader.invalidate(path)
        self.leave_markup()
        self.load_current()

    def _export_markup_copy(self, source_path: Path) -> None:
        if self.current_image is None:
            return
        if source_path.suffix.casefold() == PDF_EXTENSION:
            stem = f"{source_path.stem}-第{self._document_page_index + 1}页-已编辑"
        else:
            stem = f"{source_path.stem}-已编辑"
        output_text, _ = QFileDialog.getSaveFileName(
            self,
            "导出编辑结果",
            str(source_path.with_name(f"{stem}.png")),
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg *.jpeg);;WebP 图片 (*.webp)",
        )
        if not output_text:
            return
        output_path = Path(output_text)
        format_by_suffix = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
        output_format = format_by_suffix.get(output_path.suffix.casefold())
        if output_format is None:
            output_path = output_path.with_suffix(".png")
            output_format = "PNG"
        try:
            if output_path.resolve() == source_path.resolve():
                QMessageBox.warning(
                    self, "无法导出", "PSD、PSDT 和 PDF 源文件只读，请选择新的图片文件名。"
                )
                return
        except OSError:
            pass

        self.image_view.commit_pending_text()
        composited = self.image_view.composited_pil(self.current_image)
        try:
            save_image(
                composited,
                output_path,
                ExportOptions(output_format),
                conflict_policy=ConflictPolicy.OVERWRITE,
                allow_animation_flatten=True,
                allow_transparency_flatten=True,
            )
        except Exception as error:
            QMessageBox.warning(self, "无法导出", str(error))
            composited.close()
            return
        composited.close()
        self.leave_markup()
        QMessageBox.information(self, "导出完成", f"编辑结果已保存到：\n{output_path}")

    def show_settings(self) -> None:
        dialog = ViewerSettingsDialog(self.config, self)
        if dialog.exec() != ViewerSettingsDialog.DialogCode.Accepted:
            return
        dialog.apply_to(self.config)
        self.apply_topmost(self.config.topmost)
        self._install_shortcuts()
        self._apply_toolbar_mode()
        self.config.save(self.store, self._settings_base)

    def _escape(self) -> None:
        if self.markup_toolbar.isVisible():
            if not self.image_view.cancel_markup_interaction():
                self.leave_markup()
        elif self.filmstrip.isVisible():
            self.filmstrip.hide()
        elif self.isFullScreen():
            self.showNormal()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.open_paths(paths)
            event.acceptProposedAction()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        self._closing = True
        geometry = (
            self.normalGeometry() if self.isMaximized() or self.isFullScreen() else self.geometry()
        )
        self.config.window_geometry = (
            f"{geometry.width()}x{geometry.height()}+{geometry.x()}+{geometry.y()}"
        )
        self.config.save(self.store, self._settings_base)
        self.image_loader.shutdown(wait=False)
        self.thumbnail_loader.shutdown(wait=False)
        self._close_loaded_content()
        event.accept()


class QtViewerApplication:
    """Adapter used by the existing composition and single-instance layer."""

    def __init__(self) -> None:
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
        existing = QApplication.instance()
        self.qt_app = existing if isinstance(existing, QApplication) else QApplication(["CSImage"])
        self.qt_app.setStyle("Fusion")
        self.qt_app.setFont(QFont("Microsoft YaHei UI", 10))
        self.window = ViewerWindow()
        self.bridge = self.window.bridge

    def after(self, delay_ms: int, callback: Callable[[], object]) -> object:
        self.bridge.scheduleRequested.emit(delay_ms, callback)
        return callback

    def open_path(self, path: str | Path, confirm: bool = True) -> None:
        self.window.open_path(path, confirm=confirm)

    def open_paths(self, paths: Sequence[str | Path], confirm: bool = True) -> None:
        self.window.open_paths(paths, confirm=confirm)

    def handle_activation(self, paths: Sequence[str | Path]) -> None:
        self.window.handle_activation(paths)

    def request_exit_for_upgrade(self) -> bool:
        self.after(0, self.window.close)
        return True

    def mainloop(self) -> None:
        self.window.show()
        self.qt_app.exec()


def run_viewer(paths: Sequence[str | Path] = ()) -> int:
    app = QtViewerApplication()
    if paths:
        app.after(0, lambda: app.open_paths(paths))
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_viewer(sys.argv[1:]))
