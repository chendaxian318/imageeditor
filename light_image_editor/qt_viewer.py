"""Viewer-only PySide6 application shell.

The image/session services remain toolkit independent.  This module owns only
the native window, Qt widgets, presentation state and keyboard dispatch.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from pathlib import Path
from typing import cast

from PIL import Image
from PySide6.QtCore import QMimeData, QObject, QPoint, QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QCursor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QImage,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from image_ops import ConflictPolicy, ExportOptions, extension_for_format, save_image

from .image_loader import ImageLoader
from .navigation import NavigationSession
from .pdf_support import PDF_EXTENSION
from .progressive_image import ProgressiveImageLoader, ProgressivePreview, ProgressiveTile
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
    set_viewer_theme,
    viewer_theme_colors,
)
from .settings import SettingsStore
from .thumbnails import ThumbnailLoader

READ_ONLY_EDIT_SOURCE_EXTENSIONS = {PDF_EXTENSION, *PSD_EXTENSIONS}
ZOOM_PRESETS = (25, 50, 75, 100, 125, 150, 200, 300, 400)
QUICK_IMAGE_FORMATS = (
    ("PNG", "PNG"),
    ("JPEG", "JPG"),
    ("WEBP", "WebP"),
    ("BMP", "BMP"),
    ("TIFF", "TIFF"),
)
TRANSPARENCY_SOLID_PRESETS = (
    ("黑色", "#000000"),
    ("白色", "#ffffff"),
    ("绿幕色", "#00ff00"),
    ("纸色", "#f4f0e6"),
)
INVALID_FILENAME_CHARS = frozenset('<>:"/\\|?*')
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def _rename_viewer_file(path: Path, new_name: str) -> Path:
    cleaned = new_name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("文件名不能为空。")
    if cleaned.endswith((".", " ")):
        raise ValueError("Windows 文件名不能以句点或空格结尾。")
    if any(character in INVALID_FILENAME_CHARS or ord(character) < 32 for character in cleaned):
        raise ValueError("文件名包含 Windows 不允许的字符。")
    if Path(cleaned).stem.casefold() in WINDOWS_RESERVED_NAMES:
        raise ValueError("该名称是 Windows 保留名称，请使用其他名称。")
    target = path.with_name(cleaned)
    if target.exists() and target.resolve() != path.resolve():
        raise FileExistsError(f"目标文件已存在：{target.name}")
    path.rename(target)
    return target


def _reveal_viewer_file(path: Path) -> None:
    subprocess.Popen(["explorer", "/select,", str(path)])


def _set_windows_topmost(window_handle: int, enabled: bool) -> bool:
    """Change a visible Windows window's z-order without recreating its HWND."""

    if sys.platform != "win32" or window_handle <= 0:
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        set_window_pos = user32.SetWindowPos
        set_window_pos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        set_window_pos.restype = wintypes.BOOL

        hwnd_topmost = -1
        hwnd_not_topmost = -2
        swp_no_size = 0x0001
        swp_no_move = 0x0002
        swp_no_activate = 0x0010
        swp_no_owner_z_order = 0x0200
        flags = swp_no_size | swp_no_move | swp_no_activate | swp_no_owner_z_order
        return bool(
            set_window_pos(
                wintypes.HWND(window_handle),
                wintypes.HWND(hwnd_topmost if enabled else hwnd_not_topmost),
                0,
                0,
                0,
                0,
                flags,
            )
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False


class _UiBridge(QObject):
    scheduleRequested = Signal(int, object)
    imageLoaded = Signal(str, object)
    documentPageLoaded = Signal(str, int, object)
    thumbnailLoaded = Signal(str, object)
    progressivePreviewLoaded = Signal(str, object)
    progressiveTilesLoaded = Signal(str, object)
    fullImageLoaded = Signal(str, object)
    fullImageActionLoaded = Signal(str, object)

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
        set_viewer_theme(self.config.theme)
        self.navigation = NavigationSession([])
        self.image_loader = ImageLoader()
        self.progressive_loader = ProgressiveImageLoader()
        self.thumbnail_loader = ThumbnailLoader()
        self.current_image: Image.Image | None = None
        self._progressive_preview: Image.Image | None = None
        self._progressive_source_size: tuple[int, int] | None = None
        self._progressive_source_format = ""
        self._preview_rotation_quarters = 0
        self._full_image_active = False
        self._full_image_pending = False
        self._pending_markup_tool: str | None = None
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
        self.bridge.progressivePreviewLoaded.connect(self._finish_progressive_preview)
        self.bridge.progressiveTilesLoaded.connect(self._finish_progressive_tiles)
        self.bridge.fullImageLoaded.connect(self._finish_full_image_for_markup)
        self.bridge.fullImageActionLoaded.connect(self._finish_full_image_action)

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
        self.title_bar.renameSubmitted.connect(self._rename_current_file_to)
        self._title_format_actions: dict[str, QAction] = {}
        self._title_format_menu = QMenu(self.title_bar.format_button)
        for output_format, label in QUICK_IMAGE_FORMATS:
            action = self._title_format_menu.addAction(label)
            action.setData(output_format)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, value=output_format: self.change_current_format(value)
            )
            self._title_format_actions[output_format] = action
        self.title_bar.format_button.setMenu(self._title_format_menu)
        self.image_view = ImageView(self.root)
        self.image_view.zoomChanged.connect(self._sync_zoom_text)
        self.image_view.pointerMoved.connect(self._viewer_pointer_moved)
        self.image_view.documentPageChanged.connect(self._document_visible_page_changed)
        self.image_view.documentPagesRequested.connect(self._load_document_pages)
        self.image_view.documentPagesReleased.connect(self._release_document_pages)
        self.image_view.contextMenuRequested.connect(self.show_image_context_menu)
        self.image_view.externalFilesDropped.connect(self.open_paths)
        self.image_view.progressiveTilesRequested.connect(self._request_progressive_tiles)
        self.image_view.navigatorPositionChanged.connect(self._save_navigator_position)
        self.image_view.set_navigator_position(self.config.navigator_position)
        self.image_view.set_image_edge_glow_style(
            color=self.config.image_edge_glow_color,
            intensity=self.config.image_edge_glow_intensity,
            spread=self.config.image_edge_glow_spread,
        )
        self.image_view.set_image_edge_glow(self.config.image_edge_glow)
        self.image_view.set_transparency_background(
            self.config.transparency_background,
            color=self.config.transparency_color,
            checker_light=self.config.checker_light,
            checker_dark=self.config.checker_dark,
        )

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

        self.loading_state = QWidget(self.root)
        self.loading_state.setObjectName("viewerLoadingState")
        self.loading_state.setStyleSheet(
            """
            #viewerLoadingState {
                background: #15181d;
                border: 1px solid #343a43;
                border-radius: 18px;
            }
            QLabel {background: transparent; border: 0;}
            """
        )
        loading_layout = QVBoxLayout(self.loading_state)
        loading_layout.setContentsMargins(32, 24, 32, 24)
        loading_layout.setSpacing(8)
        self.loading_title = QLabel("正在打开图片…", self.loading_state)
        self.loading_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_title.setStyleSheet("color: #f5f7fa; font: 600 18px 'Microsoft YaHei UI';")
        self.loading_hint = QLabel("请稍候", self.loading_state)
        self.loading_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_hint.setStyleSheet(f"color: {MUTED}; font: 13px 'Microsoft YaHei UI';")
        loading_layout.addWidget(self.loading_title)
        loading_layout.addWidget(self.loading_hint)
        self.loading_state.hide()

        self.toolbar = ViewerToolbar(self.root)
        self.toolbar.hoverChanged.connect(self._toolbar_hover_changed)
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
        self.markup_toolbar.copyRequested.connect(self.copy_markup)
        self.markup_toolbar.replaceRequested.connect(self.replace_original)

        self._resize_filter = NativeResizeFilter(self)
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self._resize_filter)
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(1300)
        self._auto_hide_timer.timeout.connect(self._hide_toolbar_if_inactive)
        self._install_shortcuts()
        self.apply_topmost(self.config.topmost, show_again=False)
        self._apply_theme(self.config.theme)
        self._apply_toolbar_mode()
        self._set_empty_state_visible(True)
        self._layout_overlays()

    def _build_toolbar(self) -> None:
        self.open_button = ViewerIconButton(
            text="打开",
            tooltip="打开图片、PSD/PSDT 或 PDF（Ctrl+O）",
            parent=self.toolbar,
        )
        self.open_button.clicked.connect(self.choose_images)
        self.toolbar.add_button(self.open_button)
        self.toolbar.add_separator()

        minus = ViewerIconButton("minus", tooltip="缩小图片", parent=self.toolbar)
        minus.clicked.connect(lambda: self.image_view.step_zoom(-self.config.zoom_step))
        self.toolbar.add_button(minus)
        self.zoom_button = ViewerIconButton(
            text="100%",
            tooltip="选择预设缩放比例",
            parent=self.toolbar,
            width=52,
        )
        self.zoom_menu = QMenu(self.zoom_button)
        self.zoom_menu.setStyleSheet(
            "QMenu {color: #f5f7fa; background: #20242b; border: 1px solid #343a43; "
            "padding: 5px; font: 12px 'Microsoft YaHei UI';}"
            "QMenu::item {padding: 6px 24px 6px 10px; border-radius: 6px;}"
            "QMenu::item:selected {background: #3d5266;}"
        )
        for percent in ZOOM_PRESETS:
            action = self.zoom_menu.addAction(f"{percent}%")
            action.setData(percent)
            action.setCheckable(True)
            action.setChecked(percent == 100)
            action.triggered.connect(
                lambda _checked=False, value=percent: self.image_view.set_zoom_percent(value)
            )
        self.zoom_button.setMenu(self.zoom_menu)
        self.toolbar.add_button(self.zoom_button)
        plus = ViewerIconButton("plus", tooltip="放大图片", parent=self.toolbar)
        plus.clicked.connect(lambda: self.image_view.step_zoom(self.config.zoom_step))
        self.toolbar.add_button(plus)

        actual = ViewerIconButton(text="1:1", tooltip="回到 100% 原图比例", parent=self.toolbar)
        actual.clicked.connect(self.image_view.actual_size)
        self.toolbar.add_button(actual)
        fit = ViewerIconButton("fit", tooltip="适应当前窗口", parent=self.toolbar)
        fit.clicked.connect(self.image_view.fit_to_window)
        self.toolbar.add_button(fit)
        self.rotate_button = ViewerIconButton(
            "rotate-cw",
            tooltip="顺时针旋转 90°",
            parent=self.toolbar,
        )
        self.rotate_button.setObjectName("rotatePreviewButton")
        self.rotate_button.clicked.connect(self.rotate_preview)
        self.toolbar.add_button(self.rotate_button)

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
        self.theme_button = ViewerIconButton("sun", tooltip="切换到明亮模式", parent=self.toolbar)
        self.theme_button.clicked.connect(self.toggle_theme)
        self.toolbar.add_button(self.theme_button)
        self.settings_button = ViewerIconButton("settings", tooltip="看图设置", parent=self.toolbar)
        self.settings_button.clicked.connect(self.show_settings)
        self.toolbar.add_button(self.settings_button)

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
            "markup_arrow": lambda: self._select_markup_tool("arrow"),
            "markup_brush": lambda: self._select_markup_tool("freehand"),
        }
        for name, callback in callbacks.items():
            for sequence in self.config.shortcuts.get(name, []):
                self._shortcut_actions.append(make_shortcut(sequence, self, callback))
        self._shortcut_actions.extend(
            [
                make_shortcut("Ctrl+O", self, self.choose_images),
                make_shortcut("Ctrl+C", self, self.copy_current_image),
                make_shortcut("Ctrl+Z", self, self._undo_in_viewer),
                make_shortcut("Ctrl+Y", self, self._redo_in_viewer),
                make_shortcut("Ctrl+S", self, self._save_markup_shortcut),
                make_shortcut("Ctrl+Shift+S", self, self._save_markup_shortcut),
                make_shortcut("Ctrl+A", self, self._select_all_markup),
                make_shortcut("Ctrl+D", self, self._clear_markup_selection),
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
        loading_width = min(440, max(320, width - 80))
        loading_height = 126
        self.loading_state.setGeometry(
            max(0, (width - loading_width) // 2),
            title_height + max(56, (content_height - loading_height) // 2),
            loading_width,
            loading_height,
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
            self.loading_state,
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
            self.image_view.set_drag_source(None)
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
        self.image_view.clear_image()
        self._set_loading_state(path)
        if path.suffix.casefold() == PDF_EXTENSION:

            def completed(future: Future[Image.Image]) -> None:
                self.bridge.imageLoaded.emit(str(path), (generation, future))

            self.image_loader.load(path, completed, take_cached=True, page_index=page_index)
        else:
            preview_size = self._preview_target_size()

            def preview_completed(future: Future[ProgressivePreview]) -> None:
                self.bridge.progressivePreviewLoaded.emit(str(path), (generation, future))

            self.progressive_loader.load_preview(path, preview_size, preview_completed)
            neighbors = [
                neighbor
                for neighbor in self.navigation.neighbors()
                if neighbor.suffix.casefold() != PDF_EXTENSION
            ]
            self.progressive_loader.prefetch_previews(neighbors, preview_size)
        self._update_chrome(path)

    def _preview_target_size(self) -> tuple[int, int]:
        ratio = max(1.0, float(self.devicePixelRatioF()))
        return (
            max(640, round(self.image_view.viewport().width() * ratio)),
            max(480, round(self.image_view.viewport().height() * ratio)),
        )

    def _finish_progressive_preview(self, path_text: str, payload: object) -> None:
        generation, future = cast(tuple[int, Future[ProgressivePreview]], payload)
        if generation != self._load_generation or self.navigation.current != Path(path_text):
            if future.done() and future.exception() is None:
                future.result().image.close()
            return
        try:
            preview = future.result()
        except Exception as error:
            self._set_loading_state(None)
            self.image_view.clear_image()
            self._set_empty_state_visible(True)
            QMessageBox.warning(self, "无法打开图片", str(error))
            return
        if self._progressive_preview is not None:
            self._progressive_preview.close()
        self._progressive_preview = preview.image
        self.current_image = preview.image
        self._progressive_source_size = preview.source_size
        self._progressive_source_format = preview.source_format
        self._preview_rotation_quarters = 0
        self._full_image_active = False
        self._full_image_pending = False
        self.image_view.set_progressive_image(
            pil_to_qimage(preview.image),
            preview.source_size,
            tile_size=self.progressive_loader.tile_size,
            fit=True,
        )
        self._set_loading_state(None)
        self._set_empty_state_visible(False)
        self._update_chrome(Path(path_text))

    def _request_progressive_tiles(self, rects: object) -> None:
        path = self.navigation.current
        if path is None or path.suffix.casefold() == PDF_EXTENSION or not isinstance(rects, tuple):
            return
        requested = cast(tuple[tuple[int, int, int, int], ...], rects)
        if not requested:
            return
        generation = self._load_generation
        future = self.progressive_loader.load_tiles(
            path,
            requested,
            quarter_turns=self._preview_rotation_quarters,
        )

        def completed(result: Future[tuple[ProgressiveTile, ...]]) -> None:
            self.bridge.progressiveTilesLoaded.emit(str(path), (generation, requested, result))

        future.add_done_callback(completed)

    def _finish_progressive_tiles(self, path_text: str, payload: object) -> None:
        generation, requested, future = cast(
            tuple[
                int,
                tuple[tuple[int, int, int, int], ...],
                Future[tuple[ProgressiveTile, ...]],
            ],
            payload,
        )
        if generation != self._load_generation or self.navigation.current != Path(path_text):
            if future.done() and future.exception() is None:
                for tile in future.result():
                    tile.image.close()
            return
        try:
            tiles = future.result()
        except Exception:
            self.image_view.progressive_tiles_failed(requested)
            return
        rendered: list[tuple[tuple[int, int, int, int], QImage]] = []
        try:
            rendered = [(tile.rect, pil_to_qimage(tile.image)) for tile in tiles]
        finally:
            for tile in tiles:
                tile.image.close()
        self.image_view.set_progressive_tiles(rendered)

    def _finish_image_load(self, path_text: str, payload: object) -> None:
        generation, future = cast(tuple[int, Future[Image.Image]], payload)
        if generation != self._load_generation or self.navigation.current != Path(path_text):
            if future.done() and future.exception() is None:
                future.result().close()
            return
        try:
            image = future.result()
        except Exception as error:
            self._pending_markup_tool = None
            self._set_loading_state(None)
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
                fit=False,
            )
        else:
            if self.current_image is not None:
                self.current_image.close()
            self.current_image = image
            self.image_view.set_image(pil_to_qimage(image), fit=True)
        self._set_loading_state(None)
        self._set_empty_state_visible(False)
        self._update_chrome(path)

    def _close_loaded_content(self) -> None:
        document_image_ids = {id(image) for image in self._document_images.values()}
        images: dict[int, Image.Image] = {}
        if self.current_image is not None and id(self.current_image) not in document_image_ids:
            images[id(self.current_image)] = self.current_image
        if self._progressive_preview is not None:
            images[id(self._progressive_preview)] = self._progressive_preview
        for image in images.values():
            image.close()
        for image in self._document_images.values():
            image.close()
        self._document_images.clear()
        self.current_image = None
        self._progressive_preview = None
        self._progressive_source_size = None
        self._progressive_source_format = ""
        self._preview_rotation_quarters = 0
        self._full_image_active = False
        self._full_image_pending = False
        self._pending_markup_tool = None

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
            future = self.image_loader.load(path, page_index=page_index, take_cached=True)

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
            or page_index not in self._document_pending
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
            self._document_pending.discard(page_index)
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
        self.image_view.set_drag_source(path)
        is_pdf = path.suffix.casefold() == PDF_EXTENSION
        if is_pdf:
            page_position = f"第 {self._document_page_index + 1} / {self._document_page_count} 页"
            title = f"{path.name} · {page_position}"
        else:
            page_position = ""
            title = path.name
        self.setWindowTitle(title)
        self.title_bar.set_file_title(path, page_position)
        self._sync_title_format_menu(path)
        position = self.navigation.position or (1, 1)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if is_pdf and 0 <= self._document_page_index < len(self._document_page_sizes):
            width, height = self._document_page_sizes[self._document_page_index]
            fmt = "PDF"
        elif self._progressive_source_size is not None:
            width, height = self._progressive_source_size
            fmt = self._progressive_source_format or path.suffix.lstrip(".").upper()
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
            self.loading_state.hide()
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

    def _set_loading_state(self, path: Path | None) -> None:
        """Explain slow document decoding instead of presenting an ambiguous black canvas."""

        if path is None:
            self.loading_state.hide()
            return
        suffix = path.suffix.casefold()
        if suffix in PSD_EXTENSIONS:
            title = "正在解析 PSD 合成预览…"
            hint = "大型或多图层 Photoshop 文件可能需要十几秒"
        elif suffix == PDF_EXTENSION:
            title = "正在载入 PDF…"
            hint = "正在准备连续页面预览"
        else:
            title = "正在打开图片…"
            hint = "请稍候"
        self.loading_title.setText(title)
        self.loading_hint.setText(hint)
        self.loading_state.setAccessibleName(title.rstrip("…"))
        self.loading_state.show()
        self.loading_state.raise_()
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
        if path is None or path.suffix.casefold() == PDF_EXTENSION:
            return
        if self.current_image is None:
            return
        if self._progressive_preview is not None and not self._full_image_active:
            old_size = self._progressive_source_size or self._progressive_preview.size
            old_center = self.image_view.mapToScene(self.image_view.viewport().rect().center())
            zoom = self.image_view.zoom_percent()
            rotated = self._progressive_preview.transpose(Image.Transpose.ROTATE_270)
            self._progressive_preview.close()
            self._progressive_preview = rotated
            self.current_image = rotated
            self._preview_rotation_quarters = (self._preview_rotation_quarters + 1) % 4
            new_size = (old_size[1], old_size[0])
            self._progressive_source_size = new_size
            self.image_view.set_progressive_image(
                pil_to_qimage(rotated),
                new_size,
                tile_size=self.progressive_loader.tile_size,
                fit=False,
            )
            self.image_view.set_zoom_percent(zoom)
            new_center = QPointF(old_size[1] - old_center.y(), old_center.x())
            self.image_view.centerOn(new_center)
            self.image_view._update_navigator()
            self._update_chrome(path)
            return
        rotated = self.current_image.transpose(Image.Transpose.ROTATE_270)
        self.current_image.close()
        self.current_image = rotated
        self.image_view.set_rotated_image(pil_to_qimage(rotated), clockwise=True)

    def _sync_zoom_text(self, percent: int) -> None:
        self.zoom_button.setText(f"{percent}%")
        for action in self.zoom_menu.actions():
            value = action.data()
            if isinstance(value, int):
                action.setChecked(value == percent)

    def _save_navigator_position(self, x: float, y: float) -> None:
        self.config.navigator_position = (x, y)
        self.config.save(self.store, self._settings_base)

    def show_image_context_menu(self, global_position: QPoint) -> None:
        menu = self._image_context_menu()
        if menu is not None:
            menu.exec(global_position)

    def _image_context_menu(self) -> QMenu | None:
        path = self.navigation.current
        if path is None or self.current_image is None:
            return None
        colors = viewer_theme_colors()
        menu = QMenu(self)
        menu.setObjectName("imageContextMenu")
        menu.setStyleSheet(
            f"QMenu {{color: {colors['text']}; background: {colors['surface_high']}; "
            f"border: 1px solid {colors['border']}; padding: 6px; "
            "font: 12px 'Microsoft YaHei UI';}"
            "QMenu::item {padding: 7px 28px 7px 10px; border-radius: 7px;}"
            f"QMenu::item:selected {{background: {colors['selected']};}}"
            f"QMenu::separator {{height: 1px; background: {colors['border']}; margin: 5px;}}"
        )
        menu.addAction("复制图片", self.copy_current_image)
        menu.addAction("复制文件", self.copy_current_file)
        menu.addAction("复制文件路径", self.copy_current_path)
        menu.addSeparator()
        glow_action = menu.addAction("图片边缘微光")
        glow_action.setCheckable(True)
        glow_action.setChecked(self.config.image_edge_glow)
        glow_action.toggled.connect(self._set_image_edge_glow)
        menu.addAction("微光设置…", self.show_settings)
        menu.addSeparator()
        self._add_transparency_background_menu(menu)
        menu.addSeparator()
        menu.addAction("重命名…", self.rename_current_file)
        menu.addAction("在文件夹中显示", self.reveal_current_file)
        return menu

    def _set_image_edge_glow(self, enabled: bool) -> None:
        self.config.image_edge_glow = bool(enabled)
        self.image_view.set_image_edge_glow(self.config.image_edge_glow)
        self.config.save(self.store, self._settings_base)

    def _add_transparency_background_menu(self, menu: QMenu) -> None:
        background_menu = menu.addMenu("透明区域背景")
        group = QActionGroup(background_menu)
        group.setExclusive(True)

        checker = background_menu.addAction("棋盘格")
        checker.setCheckable(True)
        checker.setChecked(self.config.transparency_background == "checkerboard")
        checker.triggered.connect(lambda: self._set_checkerboard_background())
        group.addAction(checker)

        preset_colors = {color for _label, color in TRANSPARENCY_SOLID_PRESETS}
        for label, color in TRANSPARENCY_SOLID_PRESETS:
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(color))
            action = background_menu.addAction(QIcon(swatch), label)
            action.setCheckable(True)
            action.setChecked(
                self.config.transparency_background == "solid"
                and self.config.transparency_color == color
            )
            action.triggered.connect(
                lambda _checked=False, value=color: self._set_solid_transparency_background(value)
            )
            group.addAction(action)

        background_menu.addSeparator()
        custom_solid = background_menu.addAction("自定义色…")
        custom_solid.setCheckable(True)
        custom_solid.setChecked(
            self.config.transparency_background == "solid"
            and self.config.transparency_color not in preset_colors
        )
        custom_solid.triggered.connect(self._choose_solid_transparency_background)
        group.addAction(custom_solid)
        background_menu.addAction("自定义棋盘格双色…", self._choose_checkerboard_background)

    def _set_solid_transparency_background(self, color: str) -> None:
        candidate = QColor(color)
        if not candidate.isValid():
            return
        self.config.transparency_background = "solid"
        self.config.transparency_color = candidate.name()
        self.image_view.set_transparency_background("solid", color=self.config.transparency_color)
        self.config.save(self.store, self._settings_base)

    def _set_checkerboard_background(
        self,
        light: str | None = None,
        dark: str | None = None,
    ) -> None:
        light_color = QColor(light or self.config.checker_light)
        dark_color = QColor(dark or self.config.checker_dark)
        if not light_color.isValid() or not dark_color.isValid():
            return
        self.config.transparency_background = "checkerboard"
        self.config.checker_light = light_color.name()
        self.config.checker_dark = dark_color.name()
        self.image_view.set_transparency_background(
            "checkerboard",
            checker_light=self.config.checker_light,
            checker_dark=self.config.checker_dark,
        )
        self.config.save(self.store, self._settings_base)

    def _choose_solid_transparency_background(self) -> None:
        color = QColorDialog.getColor(
            QColor(self.config.transparency_color),
            self,
            "选择透明区域纯色底纹",
        )
        if color.isValid():
            self._set_solid_transparency_background(color.name())

    def _choose_checkerboard_background(self) -> None:
        light = QColorDialog.getColor(
            QColor(self.config.checker_light),
            self,
            "选择棋盘格浅色",
        )
        if not light.isValid():
            return
        dark = QColorDialog.getColor(
            QColor(self.config.checker_dark),
            self,
            "选择棋盘格深色",
        )
        if dark.isValid():
            self._set_checkerboard_background(light.name(), dark.name())

    def copy_current_image(self) -> None:
        if self.current_image is None:
            return
        if self.markup_toolbar.isVisible() and self.image_view.copy_inline_text():
            return
        if self.markup_toolbar.isVisible():
            self.image_view.commit_pending_text()
            composited = self.image_view.composited_pil(self.current_image)
            try:
                QApplication.clipboard().setImage(pil_to_qimage(composited))
            finally:
                composited.close()
            return
        if (
            self._progressive_preview is not None
            and not self._full_image_active
            and self._progressive_source_size is not None
            and self._progressive_preview.size != self._progressive_source_size
        ):
            self._load_full_for_action(
                lambda image: QApplication.clipboard().setImage(pil_to_qimage(image))
            )
            return
        QApplication.clipboard().setImage(pil_to_qimage(self.current_image))

    def _load_full_for_action(self, callback: Callable[[Image.Image], object]) -> None:
        path = self.navigation.current
        if path is None or path.suffix.casefold() == PDF_EXTENSION:
            return
        generation = self._load_generation
        self._set_loading_state(path)
        future = self.progressive_loader.load_full(
            path,
            quarter_turns=self._preview_rotation_quarters,
        )

        def completed(result: Future[Image.Image]) -> None:
            self.bridge.fullImageActionLoaded.emit(str(path), (generation, callback, result))

        future.add_done_callback(completed)

    def _finish_full_image_action(self, path_text: str, payload: object) -> None:
        generation, callback, future = cast(
            tuple[int, Callable[[Image.Image], object], Future[Image.Image]], payload
        )
        if generation != self._load_generation or self.navigation.current != Path(path_text):
            if future.done() and future.exception() is None:
                future.result().close()
            return
        try:
            image = future.result()
        except Exception as error:
            self._set_loading_state(None)
            QMessageBox.warning(self, "无法读取完整图片", str(error))
            return
        try:
            callback(image)
        finally:
            image.close()
            self._set_loading_state(None)

    def copy_current_file(self) -> None:
        path = self.navigation.current
        if path is None or not path.is_file():
            return
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path.resolve()))])
        mime_data.setData(
            'application/x-qt-windows-mime;value="Preferred DropEffect"',
            b"\x01\x00\x00\x00",
        )
        QApplication.clipboard().setMimeData(mime_data)

    def copy_current_path(self) -> None:
        path = self.navigation.current
        if path is not None:
            QApplication.clipboard().setText(str(path.resolve()))

    def rename_current_file(self) -> None:
        source = self.navigation.current
        if source is None or not source.is_file():
            return
        stem, accepted = QInputDialog.getText(
            self,
            "重命名图片",
            f"新文件名（扩展名 {source.suffix} 保持不变）：",
            QLineEdit.EchoMode.Normal,
            source.stem,
        )
        if not accepted:
            return
        self._rename_current_file_to(stem)

    def _rename_current_file_to(self, stem: str) -> None:
        source = self.navigation.current
        if source is None or not source.is_file():
            return
        stem = stem.strip()
        if source.suffix and stem.casefold().endswith(source.suffix.casefold()):
            stem = stem[: -len(source.suffix)].rstrip()
        if not stem:
            QMessageBox.warning(self, "无法重命名", "文件名不能为空。")
            return
        try:
            target = _rename_viewer_file(source, f"{stem}{source.suffix}")
        except (FileExistsError, OSError, ValueError) as error:
            QMessageBox.warning(self, "无法重命名", str(error))
            return
        if target == source:
            return

        old_key = os.path.normcase(str(source.resolve()))
        self.navigation.files[self.navigation.index] = target.resolve()
        if self._document_path == source:
            self._document_path = target
        self.config.favorite_paths = [
            str(target.resolve())
            if os.path.normcase(str(Path(item).resolve())) == old_key
            else item
            for item in self.config.favorite_paths
        ]
        self.image_loader.invalidate(source)
        self.progressive_loader.invalidate(source)
        self.thumbnail_loader.invalidate(source)
        self.thumbnail_loader.invalidate(target)
        self.config.save(self.store, self._settings_base)
        self._update_chrome(target)
        if self.filmstrip.isVisible():
            self.refresh_filmstrip()

    @staticmethod
    def _canonical_image_format(value: str) -> str:
        normalized = value.strip().upper()
        return {"JPG": "JPEG", "JPE": "JPEG", "TIF": "TIFF"}.get(normalized, normalized)

    def _sync_title_format_menu(self, path: Path) -> None:
        current_format = self._canonical_image_format(path.suffix.lstrip("."))
        for output_format, action in self._title_format_actions.items():
            action.setChecked(output_format == current_format)
            action.setEnabled(output_format != current_format)

    def change_current_format(self, output_format: str) -> None:
        source = self.navigation.current
        if source is None or not source.is_file() or self.current_image is None:
            return
        output_format = self._canonical_image_format(output_format)
        if output_format not in self._title_format_actions:
            QMessageBox.warning(self, "无法转换格式", f"不支持的图片格式：{output_format}")
            return
        if self._canonical_image_format(source.suffix.lstrip(".")) == output_format:
            return

        if self.markup_toolbar.isVisible():
            self.image_view.commit_pending_text()
            composited = self.image_view.composited_pil(self.current_image)
            try:
                self._save_quick_format_image(composited, source, output_format)
            finally:
                composited.close()
            return

        if (
            source.suffix.casefold() != PDF_EXTENSION
            and self._progressive_preview is not None
            and not self._full_image_active
            and self._progressive_source_size is not None
            and self._progressive_preview.size != self._progressive_source_size
        ):

            def save_loaded_image(image: Image.Image) -> None:
                self._save_quick_format_image(image, source, output_format)

            self._load_full_for_action(save_loaded_image)
            return
        self._save_quick_format_image(self.current_image, source, output_format)

    def _save_quick_format_image(
        self,
        image: Image.Image,
        source: Path,
        output_format: str,
    ) -> None:
        target = source.with_suffix(extension_for_format(output_format))
        try:
            saved_path = save_image(
                image,
                target,
                ExportOptions(output_format, quality=92),
                conflict_policy=ConflictPolicy.UNIQUE,
                allow_animation_flatten=True,
                allow_transparency_flatten=True,
            ).resolve()
        except Exception as error:
            QMessageBox.warning(self, "无法转换格式", str(error))
            return

        self.image_loader.invalidate(saved_path)
        self.progressive_loader.invalidate(saved_path)
        self.thumbnail_loader.invalidate(saved_path)
        QTimer.singleShot(0, lambda path=saved_path: self.open_path(path))

    def reveal_current_file(self) -> None:
        path = self.navigation.current
        if path is not None and path.is_file():
            _reveal_viewer_file(path)

    def toggle_theme(self) -> None:
        theme = "light" if self.config.theme == "dark" else "dark"
        self._apply_theme(theme)
        self.config.save(self.store, self._settings_base)

    def _apply_theme(self, theme: str) -> None:
        self.config.theme = set_viewer_theme(theme)
        colors = viewer_theme_colors()
        self.root.setStyleSheet(f"#viewerRoot {{background: {colors['canvas']};}}")
        self.title_bar.apply_theme()
        colors = viewer_theme_colors()
        self._title_format_menu.setStyleSheet(
            f"QMenu {{color: {colors['text']}; background: {colors['surface_high']}; "
            f"border: 1px solid {colors['border']}; padding: 5px; "
            "font: 12px 'Microsoft YaHei UI';}"
            "QMenu::item {padding: 6px 26px 6px 10px; border-radius: 6px;}"
            f"QMenu::item:selected {{background: {colors['selected']};}}"
            f"QMenu::item:disabled {{color: {colors['disabled']};}}"
            f"QMenu::indicator:checked {{background: {colors['accent']}; "
            "border-radius: 4px; width: 8px; height: 8px;}}"
        )
        self.image_view.apply_theme()
        self.toolbar.apply_theme()
        self.filmstrip.apply_theme()
        self.markup_toolbar.apply_theme()
        self.previous_button.apply_theme()
        self.next_button.apply_theme()
        for button in self.findChildren(ViewerIconButton):
            button.apply_theme()

        self.empty_state.setStyleSheet(
            f"""
            #viewerEmptyState {{
                background: {colors["surface"]};
                border: 1px solid {colors["border"]};
                border-radius: 20px;
            }}
            QLabel {{background: transparent; border: 0;}}
            QPushButton {{
                color: {colors["accent_text"]};
                background: {colors["accent"]};
                border: 0;
                border-radius: 12px;
                padding: 0 28px;
                font: 600 15px 'Microsoft YaHei UI';
            }}
            QPushButton:hover {{background: {colors["accent_hover"]};}}
            QPushButton:pressed {{background: {colors["accent_pressed"]};}}
            """
        )
        self.empty_icon.setStyleSheet(
            f"color: {colors['accent']}; font: 36px 'Microsoft YaHei UI';"
        )
        self.empty_title.setStyleSheet(
            f"color: {colors['text']}; font: 600 20px 'Microsoft YaHei UI';"
        )
        self.empty_hint.setStyleSheet(f"color: {colors['muted']}; font: 13px 'Microsoft YaHei UI';")
        self.loading_state.setStyleSheet(
            f"""
            #viewerLoadingState {{
                background: {colors["surface"]};
                border: 1px solid {colors["border"]};
                border-radius: 18px;
            }}
            QLabel {{background: transparent; border: 0;}}
            """
        )
        self.loading_title.setStyleSheet(
            f"color: {colors['text']}; font: 600 18px 'Microsoft YaHei UI';"
        )
        self.loading_hint.setStyleSheet(
            f"color: {colors['muted']}; font: 13px 'Microsoft YaHei UI';"
        )
        self.info_label.setStyleSheet(
            f"color: {colors['muted']}; background: transparent; font: 12px 'Microsoft YaHei UI';"
        )
        self.zoom_menu.setStyleSheet(
            f"QMenu {{color: {colors['text']}; background: {colors['surface_high']}; "
            f"border: 1px solid {colors['border']}; padding: 5px; "
            "font: 12px 'Microsoft YaHei UI';}"
            "QMenu::item {padding: 6px 24px 6px 10px; border-radius: 6px;}"
            f"QMenu::item:selected {{background: {colors['selected']};}}"
        )
        if self.config.theme == "dark":
            self.theme_button.icon_name = "sun"
            tooltip = "切换到明亮模式"
        else:
            self.theme_button.icon_name = "moon"
            tooltip = "切换到黑暗模式"
        self.theme_button.setToolTip(tooltip)
        self.theme_button.setAccessibleName(tooltip)
        self.theme_button.update()

    def apply_topmost(self, enabled: bool, *, show_again: bool = True) -> None:
        self.config.topmost = enabled
        self.pin_button.set_selected(enabled)

        # Qt hides and recreates a native top-level window when setWindowFlag()
        # is called after it is visible.  SetWindowPos changes only the z-order,
        # keeping the same HWND, geometry and activation state so the pin button
        # does not make the viewer flash.
        if show_again and self.isVisible() and _set_windows_topmost(int(self.winId()), enabled):
            return

        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if show_again and self.isVisible():
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
        self._auto_hide_timer.stop()
        if not self.config.toolbar_visible or self._clean_mode:
            self.toolbar.hide()
            return
        self.toolbar.show()
        self.toolbar.opacity_effect.setOpacity(1.0)
        if self.config.toolbar_mode == "auto":
            self._auto_hide_timer.start()

    def _toolbar_contains_pointer(self) -> bool:
        position = self.toolbar.mapFromGlobal(QCursor.pos())
        return self.toolbar.rect().contains(position)

    def _toolbar_hover_changed(self, hovered: bool) -> None:
        if (
            not self.config.toolbar_visible
            or self.config.toolbar_mode != "auto"
            or self._clean_mode
        ):
            return
        if hovered:
            self._auto_hide_timer.stop()
            self.toolbar.animate_visible(True)
        else:
            self._auto_hide_timer.start()

    def _hide_toolbar_if_inactive(self) -> None:
        if (
            not self.config.toolbar_visible
            or self.config.toolbar_mode != "auto"
            or self._clean_mode
        ):
            return
        if self._toolbar_contains_pointer():
            self._auto_hide_timer.start()
            return
        self.toolbar.animate_visible(False)

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
            self.image_view.set_image_preserving_view(pil_to_qimage(self.current_image))
            self._show_markup_controls(path)
            return
        self._pdf_edit_mode = False
        if self._full_image_pending:
            return
        if self._progressive_preview is not None and not self._full_image_active:
            source_size = self._progressive_source_size or self._progressive_preview.size
            if self._progressive_preview.size == source_size:
                self._full_image_active = True
                self.current_image = self._progressive_preview
                self.image_view.set_image_preserving_view(pil_to_qimage(self.current_image))
                self._show_markup_controls(path)
                return
            self._full_image_pending = True
            self._set_loading_state(path)
            generation = self._load_generation
            future = self.progressive_loader.load_full(
                path,
                quarter_turns=self._preview_rotation_quarters,
            )

            def completed(result: Future[Image.Image]) -> None:
                self.bridge.fullImageLoaded.emit(str(path), (generation, result))

            future.add_done_callback(completed)
            return
        self._show_markup_controls(path)

    def _finish_full_image_for_markup(self, path_text: str, payload: object) -> None:
        generation, future = cast(tuple[int, Future[Image.Image]], payload)
        self._full_image_pending = False
        if generation != self._load_generation or self.navigation.current != Path(path_text):
            if future.done() and future.exception() is None:
                future.result().close()
            return
        try:
            image = future.result()
        except Exception as error:
            self._set_loading_state(None)
            QMessageBox.warning(self, "无法进入编辑", str(error))
            return
        if self.current_image is not None and self.current_image is not self._progressive_preview:
            self.current_image.close()
        self.current_image = image
        self._full_image_active = True
        self.image_view.set_image_preserving_view(pil_to_qimage(image))
        self._set_loading_state(None)
        self._show_markup_controls(Path(path_text))

    def _show_markup_controls(self, path: Path) -> None:
        self.markup_toolbar.set_read_only_source(
            path.suffix.casefold() in READ_ONLY_EDIT_SOURCE_EXTENSIONS
        )
        self.markup_toolbar.show()
        requested_tool = self._pending_markup_tool or "rectangle"
        self._pending_markup_tool = None
        self.markup_toolbar.select_tool(requested_tool)
        self.previous_button.hide()
        self.next_button.hide()
        self._layout_overlays()

    def leave_markup(self) -> None:
        self._pending_markup_tool = None
        self.image_view.clear_annotations()
        self.image_view.set_annotation_tool("")
        self.markup_toolbar.hide()
        restore_pdf = self._pdf_edit_mode
        self._pdf_edit_mode = False
        if restore_pdf:
            self._restore_pdf_document()
        elif self._full_image_active and self._progressive_preview is not None:
            if (
                self.current_image is not self._progressive_preview
                and self.current_image is not None
            ):
                self.current_image.close()
            self.current_image = self._progressive_preview
            self._full_image_active = False
            source_size = self._progressive_source_size or self._progressive_preview.size
            self.image_view.set_progressive_image_preserving_view(
                pil_to_qimage(self._progressive_preview),
                source_size,
                tile_size=self.progressive_loader.tile_size,
            )
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
        self.image_view.set_document_preserving_view(
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

    def _redo_in_viewer(self) -> None:
        if self.markup_toolbar.isVisible():
            if self.image_view.redo_inline_text():
                return
            self.image_view.redo_annotation()

    def _save_markup_shortcut(self) -> None:
        path = self.navigation.current
        if self.markup_toolbar.isVisible() and path is not None:
            self._export_markup_copy(path)

    def _select_all_markup(self) -> None:
        if self.markup_toolbar.isVisible():
            if self.image_view.select_all_inline_text():
                return
            self.markup_toolbar.select_tool("select")
            self.image_view.select_all_annotations()

    def _clear_markup_selection(self) -> None:
        if self.markup_toolbar.isVisible():
            self.image_view.clear_annotation_selection()

    def _select_markup_tool(self, tool: str) -> None:
        if self.markup_toolbar.isVisible():
            self.markup_toolbar.select_tool(tool)
            return
        if self.current_image is None or self.navigation.current is None:
            return
        self._pending_markup_tool = tool
        self.enter_markup()

    def copy_markup(self) -> None:
        if self.current_image is None:
            return
        self.image_view.commit_pending_text()
        composited = self.image_view.composited_pil(self.current_image)
        try:
            QApplication.clipboard().setImage(pil_to_qimage(composited))
        finally:
            composited.close()
        self.leave_markup()

    def replace_original(self) -> None:
        path = self.navigation.current
        if self.current_image is None or path is None:
            return
        self._export_markup_copy(path)

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
                    self,
                    "无法另存",
                    "另存为不会覆盖当前原图，请选择新的图片文件名。",
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
        dialog.defaultAppRequested.connect(self.open_default_app_settings)
        original_glow = (
            self.config.image_edge_glow,
            self.config.image_edge_glow_color,
            self.config.image_edge_glow_intensity,
            self.config.image_edge_glow_spread,
        )
        dialog.glowPreviewChanged.connect(self._preview_image_edge_glow)
        if dialog.exec() != ViewerSettingsDialog.DialogCode.Accepted:
            self._preview_image_edge_glow(*original_glow)
            return
        dialog.apply_to(self.config)
        self._preview_image_edge_glow(
            self.config.image_edge_glow,
            self.config.image_edge_glow_color,
            self.config.image_edge_glow_intensity,
            self.config.image_edge_glow_spread,
        )
        self.apply_topmost(self.config.topmost)
        self._install_shortcuts()
        self._apply_toolbar_mode()
        self.config.save(self.store, self._settings_base)

    def open_default_app_settings(self) -> None:
        targeted = QUrl("ms-settings:defaultapps?registeredAppUser=LightImageEditor")
        if not QDesktopServices.openUrl(targeted):
            QDesktopServices.openUrl(QUrl("ms-settings:defaultapps"))

    def _preview_image_edge_glow(
        self, enabled: bool, color: str, intensity: int, spread: int
    ) -> None:
        self.image_view.set_image_edge_glow_style(
            color=color,
            intensity=intensity,
            spread=spread,
        )
        self.image_view.set_image_edge_glow(enabled)

    def _escape(self) -> None:
        if self.title_bar.cancel_name_edit():
            return
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
        self.image_loader.shutdown(wait=True)
        self.progressive_loader.shutdown(wait=True)
        self.thumbnail_loader.shutdown(wait=True)
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
