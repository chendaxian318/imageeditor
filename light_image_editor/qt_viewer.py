"""Viewer-only PySide6 application shell.

The image/session services remain toolkit independent.  This module owns only
the native window, Qt widgets, presentation state and keyboard dispatch.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import cast

from PIL import Image
from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QCursor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QEnterEvent,
    QFont,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPen,
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

from .document import metadata_from_image
from .image_loader import ImageLoader, decode_image
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
    qimage_to_pil,
    set_viewer_theme,
    viewer_theme_colors,
)
from .settings import SettingsStore
from .thumbnails import ThumbnailLoader
from .vector_support import VECTOR_EXTENSIONS

READ_ONLY_EDIT_SOURCE_EXTENSIONS = {
    PDF_EXTENSION,
    *PSD_EXTENSIONS,
    *VECTOR_EXTENSIONS,
}
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


def _validate_filename_affix(label: str, value: str) -> str:
    cleaned = value.strip()
    invalid = sorted(
        {
            character
            for character in cleaned
            if character in INVALID_FILENAME_CHARS or ord(character) < 32
        }
    )
    if invalid:
        raise ValueError(f"{label}不能包含这些字符：{' '.join(invalid)}")
    return cleaned


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
    conversionProgress = Signal(int, int, str)
    conversionFinished = Signal(object)
    imageEditProgress = Signal(int, int, str)
    imageEditFinished = Signal(object)
    localImageToolFinished = Signal(object)
    creationFinished = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.scheduleRequested.connect(self._schedule)

    @staticmethod
    def _schedule(delay_ms: int, callback: object) -> None:
        if callable(callback):
            QTimer.singleShot(delay_ms, callback)


class SidePanelResizeHandle(QWidget):
    """Narrow visible divider that resizes the docked right-side tools."""

    dragged = Signal(int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._dragging = False
        self._last_global_x = 0
        self.setObjectName("sidePanelResizeHandle")
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self.setAccessibleName("调整右侧面板宽度")
        self.setToolTip("左右拖动，调整右侧面板宽度")

    def paintEvent(self, event: QEvent) -> None:
        del event
        colors = viewer_theme_colors()
        active = self._dragging or self.underMouse()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(
            QPen(QColor(colors["accent"] if active else colors["border"]), 2 if active else 1)
        )
        center = self.width() // 2
        painter.drawLine(center, 0, center, self.height())

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_global_x = round(event.globalPosition().x())
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            current_x = round(event.globalPosition().x())
            delta = current_x - self._last_global_x
            if delta:
                self._last_global_x = current_x
                self.dragged.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)


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
        self._conversion_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="image-conversion",
        )
        self._creation_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="image-creation",
        )
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
        self._image_tools_menu: QMenu | None = None
        self._image_edit_workspace: QWidget | None = None
        self._conversion_panel: QWidget | None = None
        self._creation_panel: QWidget | None = None
        self._side_panel_width: int | None = None
        self._creation_result: Image.Image | None = None
        self._creation_result_target: Path | None = None
        self._creation_preview_active = False

        self.bridge = _UiBridge()
        self.bridge.imageLoaded.connect(self._finish_image_load)
        self.bridge.documentPageLoaded.connect(self._finish_document_page_load)
        self.bridge.thumbnailLoaded.connect(self._finish_thumbnail_load)
        self.bridge.progressivePreviewLoaded.connect(self._finish_progressive_preview)
        self.bridge.progressiveTilesLoaded.connect(self._finish_progressive_tiles)
        self.bridge.fullImageLoaded.connect(self._finish_full_image_for_markup)
        self.bridge.fullImageActionLoaded.connect(self._finish_full_image_action)
        self.bridge.conversionProgress.connect(self._update_conversion_progress)
        self.bridge.conversionFinished.connect(self._finish_conversion_request)
        self.bridge.imageEditProgress.connect(self._update_image_edit_progress)
        self.bridge.imageEditFinished.connect(self._finish_image_edit_batch)
        self.bridge.localImageToolFinished.connect(self._finish_local_image_tool)
        self.bridge.creationFinished.connect(self._finish_creation_request)

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
        self.side_panel_resize_handle = SidePanelResizeHandle(self.root)
        self.side_panel_resize_handle.dragged.connect(self._resize_side_panel)
        self.side_panel_resize_handle.hide()
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
            "点击按钮选择图片、矢量图、PSD/PSDT 或 PDF，也可以直接拖放到这里",
            self.empty_state,
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
        self.zoom_hud = QLabel("100%", self.root)
        self.zoom_hud.setObjectName("zoomHud")
        self.zoom_hud.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_hud.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.zoom_hud.setMinimumWidth(88)
        self.zoom_hud.hide()
        self._zoom_hud_timer = QTimer(self)
        self._zoom_hud_timer.setSingleShot(True)
        self._zoom_hud_timer.setInterval(850)
        self._zoom_hud_timer.timeout.connect(self.zoom_hud.hide)
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

        self.scale_mode_button = ViewerIconButton(
            text="1:1",
            tooltip="切换到原图比例",
            parent=self.toolbar,
        )
        self.scale_mode_button.clicked.connect(self._toggle_fit_actual)
        self.toolbar.add_button(self.scale_mode_button)
        self._sync_scale_mode_button()
        self.edit_button = ViewerIconButton("edit", tooltip="在副本上标注图片", parent=self.toolbar)
        self.edit_button.clicked.connect(self.toggle_markup)
        self.toolbar.add_button(self.edit_button)
        self.image_tools_button = ViewerIconButton(
            text="工具",
            tooltip="处理图片与 AI 制图",
            parent=self.toolbar,
            width=52,
        )
        self.image_tools_button.clicked.connect(self.show_image_tools_menu)
        self.toolbar.add_button(self.image_tools_button)

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
        side_panel = self._creation_panel or self._conversion_panel
        side_panel_width = 0
        if side_panel is not None and not side_panel.isHidden():
            minimum_panel_width = max(320, side_panel.minimumWidth())
            maximum_panel_width = max(minimum_panel_width, width - 320)
            default_panel_width = min(460, max(minimum_panel_width, round(width * 0.32)))
            requested_panel_width = self._side_panel_width or default_panel_width
            side_panel_width = max(
                minimum_panel_width,
                min(maximum_panel_width, requested_panel_width),
            )
            self._side_panel_width = side_panel_width
        view_width = max(320, width - side_panel_width)
        self.title_bar.setGeometry(0, 0, width, title_height)
        self.image_view.setGeometry(0, title_height, view_width, max(1, height - title_height))
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
        toolbar_x = max(12, (view_width - self.toolbar.width()) // 2)
        self.toolbar.move(toolbar_x, title_height + 12)
        self._layout_zoom_hud()
        side_width = max(72, min(132, round(width * 0.075)))
        side_height = max(150, min(330, height - title_height - 150))
        side_y = title_height + max(42, (height - title_height - side_height) // 2)
        self.previous_button.setGeometry(0, side_y, side_width, side_height)
        self.next_button.setGeometry(view_width - side_width, side_y, side_width, side_height)
        self.info_label.adjustSize()
        self.info_label.move(16, height - self.info_label.height() - 12)
        self.folder_button.adjustSize()
        self.folder_button.move(
            view_width - self.folder_button.width() - 12,
            height - self.folder_button.height() - 7,
        )
        panel_width = min(1000, max(520, view_width - 64))
        self.filmstrip.setGeometry(
            max(0, (view_width - panel_width) // 2),
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
            self.zoom_hud,
        ):
            widget.raise_()
        if self._image_edit_workspace is not None:
            self._image_edit_workspace.setGeometry(
                0,
                title_height,
                width,
                max(1, height - title_height),
            )
            self._image_edit_workspace.raise_()
        if side_panel is not None and side_panel_width:
            side_panel.setGeometry(
                view_width,
                title_height,
                side_panel_width,
                max(1, height - title_height),
            )
            side_panel.raise_()
            handle_width = 10
            self.side_panel_resize_handle.setGeometry(
                max(0, view_width - handle_width // 2),
                title_height,
                handle_width,
                max(1, height - title_height),
            )
            self.side_panel_resize_handle.show()
            self.side_panel_resize_handle.raise_()
        else:
            self.side_panel_resize_handle.hide()

    def _resize_side_panel(self, delta_x: int) -> None:
        side_panel = self._creation_panel or self._conversion_panel
        if side_panel is None or side_panel.isHidden():
            return
        minimum_panel_width = max(320, side_panel.minimumWidth())
        maximum_panel_width = max(minimum_panel_width, self.root.width() - 320)
        current_width = self._side_panel_width or side_panel.width()
        self._side_panel_width = max(
            minimum_panel_width,
            min(maximum_panel_width, current_width - int(delta_x)),
        )
        self._layout_overlays()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._layout_overlays()

    def choose_images(self) -> None:
        image_patterns = (
            "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.avif *.heic *.heif *.psd *.psdt"
        )
        vector_patterns = "*.svg *.ai *.eps *.emf *.wmf"
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "打开图片或文档",
            str(self.navigation.current.parent if self.navigation.current else Path.home()),
            f"支持的文件 ({image_patterns} {vector_patterns} *.pdf);;"
            f"图片 ({image_patterns});;矢量图 ({vector_patterns});;PDF 文档 (*.pdf)",
        )
        if paths:
            self.open_paths(paths)

    def open_paths(self, paths: Sequence[str | Path], confirm: bool = True) -> None:
        if any(
            panel is not None and bool(getattr(panel, "_busy", False))
            for panel in (self._conversion_panel, self._creation_panel)
        ):
            return
        if self._image_edit_workspace is not None:
            if bool(getattr(self._image_edit_workspace, "_busy", False)):
                return
            if confirm:
                self._request_close_image_edit_workspace()
                if self._image_edit_workspace is not None:
                    return
            else:
                self._close_image_edit_workspace()
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
        if self._image_edit_workspace is not None:
            self._close_image_edit_workspace()
        if self._conversion_panel is not None:
            self._close_conversion_panel()
        if self._creation_panel is not None:
            self._close_creation_panel(restore_view=False)
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
        if self._closing:
            return
        path = self.navigation.current
        if path is None or path.suffix.casefold() == PDF_EXTENSION or not isinstance(rects, tuple):
            return
        requested = cast(tuple[tuple[int, int, int, int], ...], rects)
        if not requested:
            return
        generation = self._load_generation
        try:
            future = self.progressive_loader.load_tiles(
                path,
                requested,
                quarter_turns=self._preview_rotation_quarters,
            )
        except RuntimeError:
            if self._closing:
                return
            raise

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
        if self._conversion_panel is not None or self._creation_panel is not None:
            return
        if self._image_edit_workspace is not None:
            return
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
        self.config.toggle_favorite(path)
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
        self._sync_scale_mode_button()
        if self.image_view.has_image and self._image_edit_workspace is None:
            self._show_zoom_hud(percent)

    def _toggle_fit_actual(self) -> None:
        if self.image_view.is_fit_mode():
            self.image_view.actual_size()
        else:
            self.image_view.fit_to_window()

    def _sync_scale_mode_button(self) -> None:
        button = getattr(self, "scale_mode_button", None)
        if button is None:
            return
        if self.image_view.is_fit_mode():
            button.icon_name = ""
            button.setText("1:1")
            tooltip = "切换到原图比例"
        else:
            button.icon_name = "fit"
            button.setText("")
            tooltip = "适应当前窗口"
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.update()

    def _show_zoom_hud(self, percent: int) -> None:
        self.zoom_hud.setText(f"{percent}%")
        self.zoom_hud.adjustSize()
        self.zoom_hud.show()
        self.zoom_hud.raise_()
        self._layout_zoom_hud()
        self._zoom_hud_timer.start()

    def _layout_zoom_hud(self) -> None:
        title_height = self.title_bar.height()
        content_height = max(1, self.root.height() - title_height)
        self.zoom_hud.move(
            max(0, (self.root.width() - self.zoom_hud.width()) // 2),
            title_height + max(0, (content_height - self.zoom_hud.height()) // 2),
        )

    def _save_navigator_position(self, x: float, y: float) -> None:
        self.config.navigator_position = (x, y)
        self.config.save(self.store, self._settings_base)

    @staticmethod
    def _style_tool_menu(menu: QMenu) -> None:
        colors = viewer_theme_colors()
        menu.setStyleSheet(
            f"QMenu {{color: {colors['text']}; background: {colors['surface_high']}; "
            f"border: 1px solid {colors['border']}; padding: 7px; "
            "font: 12px 'Microsoft YaHei UI';}"
            "QMenu::item {padding: 8px 30px 8px 11px; border-radius: 7px;}"
            f"QMenu::item:selected {{background: {colors['selected']};}}"
            f"QMenu::item:disabled {{color: {colors['disabled']};}}"
            f"QMenu::separator {{height: 1px; background: {colors['border']}; margin: 5px;}}"
        )

    def _populate_image_tools_menu(self, menu: QMenu) -> None:
        path = self.navigation.current
        can_process = path is not None and self.current_image is not None
        can_edit = can_process and path is not None and path.suffix.casefold() != PDF_EXTENSION
        process_action = menu.addAction(
            "处理图片  ·  裁剪、尺寸与格式",
            self.open_image_edit_workspace,
        )
        process_action.setEnabled(can_edit)
        create_action = menu.addAction("制图", self.open_creation_panel)
        create_action.setEnabled(can_edit)

    def show_image_tools_menu(self) -> None:
        if self._image_edit_workspace is not None:
            return
        menu = QMenu(self)
        menu.setObjectName("imageToolsMenu")
        self._style_tool_menu(menu)
        self._populate_image_tools_menu(menu)
        self._image_tools_menu = menu
        menu.aboutToHide.connect(lambda: setattr(self, "_image_tools_menu", None))
        position = self.image_tools_button.mapToGlobal(
            QPoint(0, self.image_tools_button.height() + 6)
        )
        menu.popup(position)

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
        menu.addAction("打开…", self.choose_images)
        menu.addAction("顺时针旋转 90°", self.rotate_preview)

        favorite_action = menu.addAction("收藏当前图片")
        favorite_action.setCheckable(True)
        favorite_action.setChecked(self.config.is_favorite(path))
        favorite_action.triggered.connect(lambda _checked=False: self.toggle_favorite())

        topmost_action = menu.addAction("窗口置顶")
        topmost_action.setCheckable(True)
        topmost_action.setChecked(self.config.topmost)
        topmost_action.triggered.connect(lambda checked=False: self.apply_topmost(bool(checked)))

        theme_label = "切换到明亮模式" if self.config.theme == "dark" else "切换到黑暗模式"
        menu.addAction(theme_label, self.toggle_theme)
        menu.addAction("看图设置…", self.show_settings)
        menu.addSeparator()
        image_tools_menu = menu.addMenu("图片工具")
        self._style_tool_menu(image_tools_menu)
        self._populate_image_tools_menu(image_tools_menu)
        menu.addSeparator()
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

    def open_conversion_dialog(self) -> None:
        """Compatibility entry that now opens the unified image workspace."""

        self.open_image_edit_workspace()

    def _open_legacy_conversion_dialog(self) -> None:
        source = self.navigation.current
        if source is None or self.current_image is None:
            return
        if source.suffix.casefold() == PDF_EXTENSION:
            QMessageBox.information(self, "暂不支持", "当前版本暂不转换 PDF 文档。")
            return
        if self.markup_toolbar.isVisible():
            self.leave_markup()
        if self._image_edit_workspace is not None:
            self._request_close_image_edit_workspace()
            if self._image_edit_workspace is not None:
                return
        if self._creation_panel is not None:
            self._close_creation_panel()
            if self._creation_panel is not None:
                return
        self._close_conversion_panel()

        from .qt_image_tools import ImageConversionPanel

        source_size = self._progressive_source_size or self.current_image.size
        current_format = self._canonical_image_format(source.suffix.lstrip("."))
        panel = ImageConversionPanel(
            source,
            source_size,
            current_format,
            has_alpha=self.current_image.mode in {"RGBA", "LA", "P"},
            estimate_image=self.current_image,
            colors=viewer_theme_colors(),
            parent=self.root,
        )
        panel.closeRequested.connect(self._close_conversion_panel)
        panel.convertRequested.connect(self._start_conversion_request)
        self._conversion_panel = panel
        panel.show()
        self._layout_overlays()

    def _close_conversion_panel(self) -> None:
        panel = self._conversion_panel
        if panel is None:
            return
        if bool(getattr(panel, "_busy", False)) and not self._closing:
            return
        self._conversion_panel = None
        panel.close()
        panel.deleteLater()
        self._layout_overlays()

    @staticmethod
    def _conversion_target(source: Path, request: object) -> Path:
        output_format = str(getattr(request, "output_format"))
        extension = extension_for_format(output_format)
        output_directory = getattr(request, "output_directory", None)
        directory = Path(output_directory) if output_directory is not None else source.parent
        suffix = str(getattr(request, "filename_suffix", "_转换后"))
        if not suffix and source.suffix.casefold() == extension.casefold():
            suffix = "_副本"
        return directory / f"{source.stem}{suffix}{extension}"

    def _start_conversion_request(self, request: object) -> None:
        self._conversion_executor.submit(self._run_conversion_request, request)

    def _run_conversion_request(self, request: object) -> None:
        source_paths = tuple(Path(path) for path in getattr(request, "source_paths", ()))
        if not source_paths:
            source_paths = (self.navigation.current,) if self.navigation.current is not None else ()
        saved: list[Path] = []
        skipped: list[Path] = []
        failures: list[tuple[Path, str]] = []
        output_format = str(getattr(request, "output_format"))
        conflict_name = str(getattr(request, "conflict_policy", "unique"))
        conflict_policy = {
            "overwrite": ConflictPolicy.OVERWRITE,
            "unique": ConflictPolicy.UNIQUE,
        }.get(conflict_name, ConflictPolicy.UNIQUE)
        options = ExportOptions(
            output_format,
            quality=int(getattr(request, "quality", 88)),
            target_size_kb=getattr(request, "target_size_kb", None),
            webp_lossless=bool(getattr(request, "webp_lossless", False)),
            png_palette_colors=getattr(request, "png_palette_colors", None),
            tiff_compression=str(getattr(request, "tiff_compression", "raw")),
            preserve_metadata=bool(getattr(request, "preserve_metadata", True)),
            animation_mode=str(getattr(request, "animation_mode", "preserve")),
            tga_rle=bool(getattr(request, "tga_rle", True)),
            dds_pixel_format=str(getattr(request, "dds_pixel_format", "DXT5")),
            generate_mipmaps=bool(getattr(request, "generate_mipmaps", True)),
            ico_sizes=tuple(getattr(request, "ico_sizes", (16, 24, 32, 48, 64, 128, 256))),
            ktx2_mode=str(getattr(request, "ktx2_mode", "uastc")),
        )
        total = len(source_paths)
        for index, source in enumerate(source_paths):
            self.bridge.conversionProgress.emit(index, total, source.name)
            target = self._conversion_target(source, request)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    same_as_source = target.resolve() == source.resolve()
                except OSError:
                    same_as_source = False
                if same_as_source:
                    target = target.with_name(f"{target.stem}_副本{target.suffix}")
                if conflict_name == "skip" and target.exists():
                    skipped.append(target)
                    continue

                decoded = decode_image(source)
                working = decoded
                flattened: Image.Image | None = None
                try:
                    metadata = metadata_from_image(decoded)
                    if metadata.has_alpha and output_format in {
                        "JPEG",
                        "BMP",
                        "HDR",
                        "AI",
                        "EPS",
                        "EMF",
                        "WMF",
                    }:
                        rgba = decoded.convert("RGBA")
                        try:
                            background = Image.new(
                                "RGBA",
                                rgba.size,
                                str(getattr(request, "background_color", "#ffffff")),
                            )
                            try:
                                background.alpha_composite(rgba)
                                flattened = background.convert("RGB")
                            finally:
                                background.close()
                        finally:
                            rgba.close()
                        working = flattened
                    saved_path = save_image(
                        working,
                        target,
                        options,
                        metadata=metadata,
                        source_path=source,
                        conflict_policy=conflict_policy,
                        allow_animation_flatten=True,
                        allow_transparency_flatten=True,
                    ).resolve()
                    saved.append(saved_path)
                finally:
                    if flattened is not None:
                        flattened.close()
                    decoded.close()
            except Exception as error:
                failures.append((source, str(error)))
        self.bridge.conversionFinished.emit(
            {
                "request": request,
                "saved": tuple(saved),
                "skipped": tuple(skipped),
                "failures": tuple(failures),
            }
        )

    def _update_conversion_progress(self, completed: int, total: int, filename: str) -> None:
        panel = self._conversion_panel
        if panel is not None:
            getattr(panel, "set_progress")(completed, total, filename)

    def _finish_conversion_request(self, result: object) -> None:
        payload = cast(dict[str, object], result)
        request = payload["request"]
        saved = cast(tuple[Path, ...], payload["saved"])
        skipped = cast(tuple[Path, ...], payload["skipped"])
        failures = cast(tuple[tuple[Path, str], ...], payload["failures"])
        for path in saved:
            self.image_loader.invalidate(path)
            self.progressive_loader.invalidate(path)
            self.thumbnail_loader.invalidate(path)
        panel = self._conversion_panel
        if panel is not None:
            getattr(panel, "set_result_summary")(len(saved), len(skipped), len(failures))
            if failures:
                details = "\n".join(f"{path.name}：{message}" for path, message in failures)
                getattr(panel, "status_label").setToolTip(details)
        if bool(getattr(request, "open_after", False)) and len(saved) == 1:
            self._close_conversion_panel()
            QTimer.singleShot(0, lambda path=saved[0]: self.open_path(path))

    def open_creation_panel(self) -> None:
        source = self.navigation.current
        if source is None or self.current_image is None:
            return
        if source.suffix.casefold() == PDF_EXTENSION:
            QMessageBox.information(self, "暂不支持", "当前版本暂不对 PDF 页面调用制图模型。")
            return
        if self.markup_toolbar.isVisible():
            self.leave_markup()
        if self._image_edit_workspace is not None:
            self._request_close_image_edit_workspace()
            if self._image_edit_workspace is not None:
                return
        if self._conversion_panel is not None:
            self._close_conversion_panel()
            if self._conversion_panel is not None:
                return
        self._close_creation_panel()
        if self._creation_panel is not None:
            return

        from .qt_creation_tools import ImageTextCreationPanel
        from .secure_image_api_key import SecureImageApiKeyError, SecureImageApiKeyStore

        source_size = self._progressive_source_size or self.current_image.size
        stored_api_key = ""
        credential_error = ""
        try:
            stored_api_key = SecureImageApiKeyStore().read() or ""
        except SecureImageApiKeyError as error:
            credential_error = str(error)
        company_default = False
        provider_display_name = ""
        endpoint = self.config.image_edit_api_endpoint
        model = self.config.image_edit_api_model
        active_api_key = stored_api_key
        try:
            from .internal_default_image_api import (
                IMAGE_API_DISPLAY_NAME,
                IMAGE_API_ENDPOINT,
                IMAGE_API_KEY,
                IMAGE_API_MODEL,
            )
        except ImportError:
            pass
        else:
            company_default = bool(IMAGE_API_KEY.strip())
            if company_default:
                endpoint = IMAGE_API_ENDPOINT
                model = IMAGE_API_MODEL
                active_api_key = IMAGE_API_KEY
                provider_display_name = IMAGE_API_DISPLAY_NAME
        panel = ImageTextCreationPanel(
            source,
            source_size,
            endpoint=endpoint,
            model=model,
            api_key=active_api_key,
            user_api_key=stored_api_key,
            credential_saved=bool(stored_api_key),
            provider_display_name=provider_display_name,
            company_default=company_default,
            colors=viewer_theme_colors(),
            parent=self.root,
        )
        panel.closeRequested.connect(self._close_creation_panel)
        panel.generateRequested.connect(self._start_creation_request)
        panel.previewRequested.connect(self._preview_creation_result)
        panel.saveRequested.connect(self._save_creation_result)
        panel.settingsChanged.connect(self._remember_creation_settings)
        panel.credentialSaveRequested.connect(self._save_creation_credential)
        panel.credentialDeleteRequested.connect(self._delete_creation_credential)
        panel.localToolRequested.connect(self._request_local_image_tool)
        self._creation_panel = panel
        if credential_error:
            panel.status_label.setText(credential_error)
        panel.show()
        self._layout_overlays()

    def _remember_creation_settings(self, endpoint: str, model: str) -> None:
        self.config.image_edit_api_endpoint = endpoint.strip()[:2048]
        self.config.image_edit_api_model = model.strip()[:160]
        self.config.save(self.store, self._settings_base)

    def _save_creation_credential(self, api_key: str) -> None:
        from .secure_image_api_key import SecureImageApiKeyError, SecureImageApiKeyStore

        panel = self._creation_panel
        try:
            SecureImageApiKeyStore().save(api_key)
        except (SecureImageApiKeyError, ValueError, TypeError) as error:
            if panel is not None:
                getattr(panel, "status_label").setText(str(error))

    def _delete_creation_credential(self) -> None:
        from .secure_image_api_key import SecureImageApiKeyError, SecureImageApiKeyStore

        panel = self._creation_panel
        try:
            SecureImageApiKeyStore().delete()
        except SecureImageApiKeyError as error:
            if panel is not None:
                getattr(panel, "status_label").setText(str(error))

    def _close_creation_panel(self, restore_view: bool = True) -> None:
        panel = self._creation_panel
        if panel is None:
            return
        if bool(getattr(panel, "_busy", False)) and not self._closing:
            return
        was_previewing = self._creation_preview_active
        self._creation_preview_active = False
        self._creation_panel = None
        panel.close()
        panel.deleteLater()
        if self._creation_result is not None:
            self._creation_result.close()
            self._creation_result = None
        self._creation_result_target = None
        self._layout_overlays()
        if restore_view and was_previewing and self.navigation.current is not None:
            self.load_current()

    def _start_creation_request(self, request: object) -> None:
        self._creation_executor.submit(self._run_creation_request, request)

    def _run_creation_request(self, request: object) -> None:
        from .image_model_api import ImageTextEditApiRequest, edit_image_text

        try:
            if not isinstance(request, ImageTextEditApiRequest):
                raise TypeError("图片文字改写请求无效。")
            result = edit_image_text(request)
            payload: dict[str, object] = {"result": result, "error": None}
        except Exception as error:
            payload = {"result": None, "error": str(error)}
        self.bridge.creationFinished.emit(payload)

    def _finish_creation_request(self, payload: object) -> None:
        panel = self._creation_panel
        if panel is None:
            return
        result_payload = cast(dict[str, object], payload)
        error = result_payload.get("error")
        if isinstance(error, str) and error:
            getattr(panel, "set_error")(error)
            return
        result = result_payload.get("result")
        png_bytes = getattr(result, "png_bytes", None)
        if not isinstance(png_bytes, bytes):
            getattr(panel, "set_error")("模型没有返回可用图片。")
            return
        try:
            with Image.open(BytesIO(png_bytes)) as decoded:
                decoded.load()
                image = decoded.copy()
        except Exception:
            getattr(panel, "set_error")("模型返回的图片无法解码。")
            return
        if self._creation_result is not None:
            self._creation_result.close()
        self._creation_result = image
        source = self.navigation.current
        self._creation_result_target = (
            source.with_name(f"{source.stem}_文字改写.png") if source is not None else None
        )
        getattr(panel, "set_result")(png_bytes)
        self._preview_creation_result(True)

    def _preview_creation_result(self, show_result: bool) -> None:
        if show_result and self._creation_result is not None:
            self.image_view.set_image(pil_to_qimage(self._creation_result), fit=True)
            self._creation_preview_active = True
            return
        if self.current_image is not None:
            self.image_view.set_image(pil_to_qimage(self.current_image), fit=True)
        self._creation_preview_active = False

    def _save_creation_result(self) -> None:
        source = self.navigation.current
        result = self._creation_result
        if source is None or result is None:
            return
        suggested_target = self._creation_result_target or source.with_name(
            f"{source.stem}_制图结果.png"
        )
        target_text, _ = QFileDialog.getSaveFileName(
            self,
            "另存制图结果",
            str(suggested_target),
            "PNG 图片 (*.png)",
        )
        if not target_text:
            return
        target = Path(target_text)
        if target.suffix.casefold() != ".png":
            target = target.with_suffix(".png")
        try:
            if target.resolve() == source.resolve():
                raise ValueError("制图结果不能覆盖当前原图，请选择新的文件名。")
            saved = save_image(
                result,
                target,
                ExportOptions("PNG"),
                conflict_policy=ConflictPolicy.OVERWRITE,
                backup_on_overwrite=True,
            ).resolve()
        except Exception as error:
            QMessageBox.warning(self, "保存失败", str(error))
            return
        self.image_loader.invalidate(saved)
        self.progressive_loader.invalidate(saved)
        self.thumbnail_loader.invalidate(saved)
        self._creation_preview_active = False
        self._close_creation_panel(restore_view=False)
        QTimer.singleShot(0, lambda path=saved: self.open_path(path))

    def _save_conversion_request(
        self,
        image: Image.Image,
        source: Path,
        request: object,
    ) -> None:
        output_format = str(getattr(request, "output_format"))
        target_path = Path(getattr(request, "target_path"))
        quality = int(getattr(request, "quality"))
        open_after = bool(getattr(request, "open_after"))
        try:
            saved_path = save_image(
                image,
                target_path,
                ExportOptions(
                    output_format,
                    quality=quality,
                    target_size_kb=getattr(request, "target_size_kb", None),
                    webp_lossless=bool(getattr(request, "webp_lossless", False)),
                    png_palette_colors=getattr(request, "png_palette_colors", None),
                    tiff_compression=str(getattr(request, "tiff_compression", "raw")),
                    preserve_metadata=bool(getattr(request, "preserve_metadata", True)),
                ),
                conflict_policy=ConflictPolicy.UNIQUE,
                allow_animation_flatten=True,
                allow_transparency_flatten=True,
            ).resolve()
        except Exception as error:
            QMessageBox.warning(self, "转换失败", str(error))
            return

        self.image_loader.invalidate(saved_path)
        self.progressive_loader.invalidate(saved_path)
        self.thumbnail_loader.invalidate(saved_path)
        if open_after:
            QTimer.singleShot(0, lambda path=saved_path: self.open_path(path))
        else:
            QMessageBox.information(self, "转换完成", f"已保存到：\n{saved_path}")

    def open_image_edit_workspace(self) -> None:
        if self._image_edit_workspace is not None:
            return
        source = self.navigation.current
        if source is None or self.current_image is None:
            return
        if source.suffix.casefold() == PDF_EXTENSION:
            QMessageBox.information(self, "暂不支持", "当前版本暂不调整或裁剪 PDF 文档。")
            return
        if self.markup_toolbar.isVisible():
            self.leave_markup()
        if self._creation_panel is not None:
            self._close_creation_panel()
            if self._creation_panel is not None:
                return
        if self._conversion_panel is not None:
            self._close_conversion_panel()
            if self._conversion_panel is not None:
                return

        def show_workspace(image: Image.Image) -> None:
            self._show_image_edit_workspace(source, image)

        if (
            self._progressive_preview is not None
            and not self._full_image_active
            and self._progressive_source_size is not None
            and self._progressive_preview.size != self._progressive_source_size
        ):
            self._load_full_for_action(show_workspace)
            return
        show_workspace(self.current_image)

    def _show_image_edit_workspace(self, source: Path, image: Image.Image) -> None:
        from .qt_image_tools import ImageEditWorkspace

        self._close_image_edit_workspace(restore_view=False)
        workspace = ImageEditWorkspace(
            source,
            pil_to_qimage(image),
            colors=viewer_theme_colors(),
            parent=self.root,
        )
        workspace.cancelRequested.connect(self._request_close_image_edit_workspace)
        workspace.saveRequested.connect(self._save_image_edit_request)
        workspace.quickExportRequested.connect(self._quick_export_image_edit_request)
        self._image_edit_workspace = workspace
        self.image_view.hide()
        for widget in (
            self.toolbar,
            self.previous_button,
            self.next_button,
            self.info_label,
            self.folder_button,
            self.filmstrip,
            self.markup_toolbar,
            self.empty_state,
            self.loading_state,
        ):
            widget.hide()
        self._layout_overlays()
        workspace.show()
        workspace.raise_()

    def _close_image_edit_workspace(self, restore_view: bool = True) -> None:
        workspace = self._image_edit_workspace
        if workspace is None:
            return
        self._image_edit_workspace = None
        workspace.hide()
        workspace.deleteLater()
        if not restore_view:
            return
        self.image_view.show()
        path = self.navigation.current
        if path is not None:
            self._update_chrome(path)
        self._apply_toolbar_mode()
        self._layout_overlays()

    def _request_close_image_edit_workspace(self) -> None:
        workspace = self._image_edit_workspace
        if workspace is None:
            return
        if bool(getattr(workspace, "_busy", False)):
            return
        if bool(getattr(workspace, "is_dirty")()):
            decision = QMessageBox.question(
                self,
                "退出图片处理",
                "当前裁剪、尺寸、格式或输出设置尚未导出，确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if decision != QMessageBox.StandardButton.Yes:
                return
        self._close_image_edit_workspace()

    def _request_local_image_tool(self, request: object) -> None:
        panel = self._creation_panel
        source = self.navigation.current
        if panel is None or source is None or bool(getattr(panel, "_busy", False)):
            return
        from .local_ai_tools import (
            BIREFNET_DOWNLOAD_NOTE,
            UPSCAYL_DOWNLOAD_NOTE,
            birefnet_status,
            upscayl_status,
        )

        operation = str(getattr(request, "operation", ""))
        resolution = str(getattr(request, "resolution", "2K")).upper()
        if operation == "upscale":
            status = upscayl_status()
            title = "首次准备高清优化引擎"
            prompt = (
                "首次使用需要下载官方 Upscayl 本地引擎与标准 4× 模型"
                f"（{UPSCAYL_DOWNLOAD_NOTE}）。\n\n"
                "处理完全在本机完成，需要支持 Vulkan 的显卡。Upscayl 引擎采用 AGPL-3.0 许可，"
                "运行文件会保存到当前 Windows 用户的 CSImage 数据目录。是否继续？"
            )
            output_stem = f"{source.stem}_高清{resolution}"
            working_message = f"正在使用 Upscayl 生成 {resolution} 高清副本…"
        elif operation == "remove-background":
            status = birefnet_status()
            title = "首次准备智能去背景模型"
            prompt = (
                f"首次使用需要下载 BiRefNet Lite fp32 模型（{BIREFNET_DOWNLOAD_NOTE}）。\n\n"
                "模型只在本机运行，只生成透明度蒙版并保留原图 RGB；模型文件会保存到当前 "
                "Windows 用户的 CSImage 数据目录。是否继续？"
            )
            output_stem = f"{source.stem}_去背景"
            working_message = "正在使用 BiRefNet Lite 识别前景并生成透明 PNG…"
        else:
            QMessageBox.warning(self, "无法处理", "收到未知的本地图片处理操作。")
            return
        if not status.available:
            decision = QMessageBox.question(
                self,
                title,
                prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if decision != QMessageBox.StandardButton.Yes:
                return

        def prepare(image: Image.Image) -> None:
            self._prepare_local_image_tool(
                panel,
                source,
                request,
                image,
                operation=operation,
                resolution=resolution,
                output_stem=output_stem,
                working_message=working_message,
            )

        if (
            self._progressive_preview is not None
            and not self._full_image_active
            and self._progressive_source_size is not None
            and self._progressive_preview.size != self._progressive_source_size
        ):
            self._load_full_for_action(prepare)
            return
        if self.current_image is not None:
            prepare(self.current_image)

    def _prepare_local_image_tool(
        self,
        panel: QWidget,
        source: Path,
        request: object,
        image: Image.Image,
        *,
        operation: str,
        resolution: str,
        output_stem: str,
        working_message: str,
    ) -> None:
        if self._creation_panel is not panel or bool(getattr(panel, "_busy", False)):
            return
        from .local_ai_tools import unique_output_path

        temporary_directory = Path(tempfile.mkdtemp(prefix="csimage-local-tool-"))
        input_path = temporary_directory / "input.png"
        try:
            prepared = image.convert("RGBA")
            prepared.save(input_path, "PNG")
            prepared.close()
            output_directory = Path(getattr(request, "output_directory", source.parent)).resolve()
            suggested_output_path = unique_output_path(output_directory, output_stem)
            preview_output_path = temporary_directory / "result.png"
        except Exception as error:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            QMessageBox.warning(self, "无法准备图片", str(error))
            return

        getattr(panel, "set_busy")(True)
        getattr(panel, "set_local_tool_status")(working_message)
        self._conversion_executor.submit(
            self._run_local_image_tool,
            operation,
            input_path,
            preview_output_path,
            suggested_output_path,
            resolution,
            temporary_directory,
        )

    def _run_local_image_tool(
        self,
        operation: str,
        input_path: Path,
        preview_output_path: Path,
        suggested_output_path: Path,
        resolution: str,
        temporary_directory: Path,
    ) -> None:
        from .local_ai_tools import run_worker_process

        payload: dict[str, object]
        try:
            result = run_worker_process(
                operation,
                input_path,
                preview_output_path,
                resolution=resolution,
            )
            png_bytes = preview_output_path.read_bytes()
            payload = {
                "output": suggested_output_path.resolve(),
                "png_bytes": png_bytes,
                "operation": operation,
                "result": result,
                "error": "",
            }
        except Exception as error:
            payload = {
                "output": suggested_output_path.resolve(),
                "png_bytes": b"",
                "operation": operation,
                "result": {},
                "error": str(error),
            }
        finally:
            shutil.rmtree(temporary_directory, ignore_errors=True)
        self.bridge.localImageToolFinished.emit(payload)

    def _finish_local_image_tool(self, result: object) -> None:
        payload = cast(dict[str, object], result)
        panel = self._creation_panel
        if panel is not None:
            getattr(panel, "set_busy")(False)
        error = str(payload.get("error") or "")
        if error:
            if panel is not None:
                getattr(panel, "set_local_tool_status")("本地处理失败，可检查提示后重试")
            QMessageBox.warning(self, "本地图片处理失败", error)
            return
        if panel is None:
            return
        png_bytes = payload.get("png_bytes")
        if not isinstance(png_bytes, bytes) or not png_bytes:
            getattr(panel, "set_local_tool_status")("本地工具没有返回可预览的图片")
            return
        try:
            with Image.open(BytesIO(png_bytes)) as decoded:
                decoded.load()
                image = decoded.copy()
        except Exception:
            getattr(panel, "set_local_tool_status")("本地工具返回的图片无法预览")
            return
        if self._creation_result is not None:
            self._creation_result.close()
        self._creation_result = image
        self._creation_result_target = Path(str(payload["output"])).resolve()
        details = cast(dict[str, object], payload.get("result") or {})
        model = str(details.get("model") or "本地模型")
        operation = str(payload.get("operation") or "")
        result_name = "透明背景候选图" if operation == "remove-background" else "高清候选图"
        getattr(panel, "set_result")(
            png_bytes,
            title=result_name,
            status="处理完成但尚未保存；可先对比原图与结果，确认后再另存为。",
            conversation_message=f"{result_name}已生成（{model}），当前仅供预览。",
        )

    @staticmethod
    def _edit_output_format(path: Path) -> str:
        return {
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
            ".png": "PNG",
            ".webp": "WEBP",
            ".bmp": "BMP",
            ".tif": "TIFF",
            ".tiff": "TIFF",
            ".gif": "GIF",
            ".apng": "APNG",
            ".tga": "TGA",
            ".dds": "DDS",
            ".exr": "EXR",
            ".hdr": "HDR",
            ".ico": "ICO",
            ".ktx2": "KTX2",
            ".svg": "SVG",
            ".ai": "AI",
            ".eps": "EPS",
            ".emf": "EMF",
            ".wmf": "WMF",
        }.get(path.suffix.casefold(), "PNG")

    def _save_image_edit_request(self, request: object) -> None:
        self._export_image_edit_request(request, open_after=True)

    def _quick_export_image_edit_request(self, request: object) -> None:
        self._export_image_edit_request(request, open_after=False)

    def _export_image_edit_request(self, request: object, *, open_after: bool) -> None:
        workspace = self._image_edit_workspace
        source = self.navigation.current
        if workspace is None or source is None:
            return
        if bool(getattr(workspace, "_busy", False)):
            return
        try:
            _validate_filename_affix("文件名前缀", str(getattr(request, "filename_prefix", "")))
            _validate_filename_affix(
                "文件名后缀", str(getattr(request, "filename_suffix", "_edited"))
            )
        except ValueError as error:
            QMessageBox.warning(self, "文件名设置无效", str(error))
            return
        image = qimage_to_pil(getattr(workspace, "source_image")())
        sources = tuple(Path(path) for path in getattr(request, "source_paths", ()))
        if not sources:
            sources = (source.resolve(),)
        if len(sources) > 1:
            getattr(workspace, "set_busy")(True)
            self._conversion_executor.submit(
                self._run_image_edit_batch,
                request,
                source.resolve(),
                image,
                open_after,
            )
            return
        self._run_image_edit_batch(request, source.resolve(), image, open_after)

    @staticmethod
    def _center_crop_box(
        size: tuple[int, int], ratio: float | None
    ) -> tuple[int, int, int, int] | None:
        if ratio is None or ratio <= 0:
            return None
        image_width, image_height = size
        width = image_width
        height = round(width / ratio)
        if height > image_height:
            height = image_height
            width = round(height * ratio)
        left = max(0, (image_width - width) // 2)
        top = max(0, (image_height - height) // 2)
        return left, top, left + width, top + height

    @staticmethod
    def _image_edit_target(source: Path, request: object) -> Path:
        output_format = str(getattr(request, "output_format", "PNG"))
        extension = extension_for_format(output_format)
        directory = Path(getattr(request, "output_directory", source.parent)).resolve()
        prefix = str(getattr(request, "filename_prefix", "")).strip()
        suffix = str(getattr(request, "filename_suffix", "_edited")).strip()
        if not prefix and not suffix and source.suffix.casefold() == extension.casefold():
            suffix = "_副本"
        return directory / f"{prefix}{source.stem}{suffix}{extension}"

    def _run_image_edit_batch(
        self,
        request: object,
        current_source: Path,
        current_image: Image.Image,
        open_after: bool,
    ) -> None:
        sources = tuple(Path(path).resolve() for path in getattr(request, "source_paths", ()))
        if not sources:
            sources = (current_source,)
        output_format = str(getattr(request, "output_format", "PNG"))
        resize_enabled = bool(getattr(request, "resize_enabled", False))
        width = int(getattr(request, "width"))
        height = int(getattr(request, "height"))
        conflict_name = str(getattr(request, "conflict_policy", "unique"))
        conflict_policy = {
            "overwrite": ConflictPolicy.OVERWRITE,
            "unique": ConflictPolicy.UNIQUE,
        }.get(conflict_name, ConflictPolicy.UNIQUE)
        saved: list[Path] = []
        skipped: list[Path] = []
        failures: list[tuple[Path, str]] = []
        try:
            for index, source in enumerate(sources):
                self.bridge.imageEditProgress.emit(index, len(sources), source.name)
                decoded: Image.Image | None = None
                working: Image.Image | None = None
                try:
                    is_current = source == current_source
                    if is_current:
                        working = current_image.copy()
                        crop_box = getattr(request, "crop_box", None)
                    else:
                        decoded = decode_image(source)
                        working = decoded
                        crop_box = self._center_crop_box(
                            working.size,
                            getattr(request, "crop_aspect_ratio", None),
                        )
                    target = self._image_edit_target(source, request)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        same_as_source = target.resolve() == source.resolve()
                    except OSError:
                        same_as_source = False
                    if same_as_source:
                        target = target.with_name(f"{target.stem}_副本{target.suffix}")
                    if conflict_name == "skip" and target.exists():
                        skipped.append(target)
                        continue
                    saved_path = save_image(
                        working,
                        target,
                        ExportOptions(
                            output_format,
                            quality=int(getattr(request, "quality", 92)),
                            target_size_kb=getattr(request, "target_size_kb", None),
                            crop_box=crop_box,
                            exact_width=width if resize_enabled else None,
                            exact_height=height if resize_enabled else None,
                            animation_mode=str(getattr(request, "animation_mode", "preserve")),
                            tga_rle=bool(getattr(request, "tga_rle", True)),
                            dds_pixel_format=str(getattr(request, "dds_pixel_format", "DXT5")),
                            generate_mipmaps=bool(getattr(request, "generate_mipmaps", True)),
                            ico_sizes=tuple(
                                getattr(
                                    request,
                                    "ico_sizes",
                                    (16, 24, 32, 48, 64, 128, 256),
                                )
                            ),
                            ktx2_mode=str(getattr(request, "ktx2_mode", "uastc")),
                        ),
                        metadata=metadata_from_image(working),
                        source_path=source,
                        conflict_policy=conflict_policy,
                        allow_animation_flatten=True,
                        allow_transparency_flatten=True,
                    ).resolve()
                    saved.append(saved_path)
                except Exception as error:
                    failures.append((source, str(error)))
                finally:
                    if working is not None and working is not decoded:
                        working.close()
                    if decoded is not None:
                        decoded.close()
        finally:
            current_image.close()
        self.bridge.imageEditFinished.emit(
            {
                "saved": tuple(saved),
                "skipped": tuple(skipped),
                "failures": tuple(failures),
                "open_after": open_after,
            }
        )

    def _update_image_edit_progress(self, completed: int, total: int, filename: str) -> None:
        workspace = self._image_edit_workspace
        if workspace is not None:
            getattr(workspace, "set_progress")(completed, total, filename)

    def _finish_image_edit_batch(self, result: object) -> None:
        payload = cast(dict[str, object], result)
        saved = cast(tuple[Path, ...], payload["saved"])
        skipped = cast(tuple[Path, ...], payload["skipped"])
        failures = cast(tuple[tuple[Path, str], ...], payload["failures"])
        for path in saved:
            self.image_loader.invalidate(path)
            self.progressive_loader.invalidate(path)
            self.thumbnail_loader.invalidate(path)
        workspace = self._image_edit_workspace
        if workspace is not None:
            getattr(workspace, "set_result_summary")(len(saved), len(skipped), len(failures))
            if failures:
                details = "\n".join(f"{path.name}：{message}" for path, message in failures)
                getattr(workspace, "status_label").setToolTip(details)
        if bool(payload.get("open_after")) and len(saved) == 1 and not failures:
            self._close_image_edit_workspace()
            QTimer.singleShot(0, lambda path=saved[0]: self.open_path(path))

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
        self.zoom_hud.setStyleSheet(
            "QLabel#zoomHud {color: #ffffff; background: rgba(16, 20, 26, 218); "
            "border: 1px solid rgba(255, 255, 255, 42); border-radius: 13px; "
            "padding: 9px 16px; font: 700 20px 'Microsoft YaHei UI';}"
        )
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

    def apply_topmost(self, enabled: bool, *, show_again: bool = True) -> None:
        self.config.topmost = enabled

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
        if self._image_edit_workspace is not None:
            return
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
        if self._image_edit_workspace is not None:
            return
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

    def toggle_markup(self) -> None:
        if self.markup_toolbar.isVisible():
            self.leave_markup()
            return
        self.enter_markup()

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
        self.edit_button.set_selected(True)
        self.edit_button.setToolTip("退出标注模式")
        self.edit_button.setAccessibleName("退出标注模式")
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
        self.edit_button.set_selected(False)
        self.edit_button.setToolTip("在副本上标注图片")
        self.edit_button.setAccessibleName("在副本上标注图片")
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
        if self._image_edit_workspace is not None:
            self._request_close_image_edit_workspace()
            return
        if self._creation_panel is not None:
            self._close_creation_panel()
            return
        if self._conversion_panel is not None:
            self._close_conversion_panel()
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
        workspace = self._image_edit_workspace
        if workspace is not None and bool(getattr(workspace, "is_dirty")()):
            decision = QMessageBox.question(
                self,
                "退出图片处理",
                "当前裁剪、尺寸或格式设置尚未导出，确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if decision != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        self._close_image_edit_workspace(restore_view=False)
        self._close_conversion_panel()
        self._close_creation_panel(restore_view=False)
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
        self._conversion_executor.shutdown(wait=True, cancel_futures=True)
        self._creation_executor.shutdown(wait=True, cancel_futures=True)
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
