"""PySide6 widgets for the viewer-only desktop shell."""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QMimeData,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QCloseEvent,
    QColor,
    QContextMenuEvent,
    QCursor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QEnterEvent,
    QFont,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QGraphicsView,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .qt_viewer_config import DEFAULT_SHORTCUTS, SHORTCUT_LABELS, QtViewerConfig

THEME_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "canvas": "#050607",
        "surface": "#15181d",
        "surface_high": "#20242b",
        "border": "#343a43",
        "text": "#f5f7fa",
        "muted": "#aeb6c2",
        "accent": "#89c8ff",
        "accent_text": "#07131d",
        "accent_hover": "#acd9ff",
        "accent_pressed": "#6fb8f5",
        "danger": "#f4aaaa",
        "hover": "#2b3038",
        "pressed": "#353c46",
        "selected": "#3d5266",
        "disabled": "#68717e",
        "panel_overlay": "rgba(26, 30, 36, 225)",
    },
    "light": {
        "canvas": "#e8ebef",
        "surface": "#f7f8fa",
        "surface_high": "#ffffff",
        "border": "#cbd1d9",
        "text": "#1d232b",
        "muted": "#5f6975",
        "accent": "#1677c8",
        "accent_text": "#ffffff",
        "accent_hover": "#2b88d1",
        "accent_pressed": "#0f65ad",
        "danger": "#b4232c",
        "hover": "#e7ebf0",
        "pressed": "#d9dee6",
        "selected": "#d7e9f8",
        "disabled": "#9aa3ad",
        "panel_overlay": "rgba(255, 255, 255, 238)",
    },
}

_ACTIVE_THEME = "dark"
CANVAS = ""
SURFACE = ""
SURFACE_HIGH = ""
BORDER = ""
TEXT = ""
MUTED = ""
ACCENT = ""
DANGER = ""
HOVER = ""
PRESSED = ""
SELECTED = ""
DISABLED = ""
PANEL_OVERLAY = ""


def set_viewer_theme(theme: str) -> str:
    """Select the active viewer palette used by widgets and custom painters."""

    global _ACTIVE_THEME
    global CANVAS, SURFACE, SURFACE_HIGH, BORDER, TEXT, MUTED, ACCENT, DANGER
    global HOVER, PRESSED, SELECTED, DISABLED, PANEL_OVERLAY

    _ACTIVE_THEME = theme if theme in THEME_PALETTES else "dark"
    colors = THEME_PALETTES[_ACTIVE_THEME]
    CANVAS = colors["canvas"]
    SURFACE = colors["surface"]
    SURFACE_HIGH = colors["surface_high"]
    BORDER = colors["border"]
    TEXT = colors["text"]
    MUTED = colors["muted"]
    ACCENT = colors["accent"]
    DANGER = colors["danger"]
    HOVER = colors["hover"]
    PRESSED = colors["pressed"]
    SELECTED = colors["selected"]
    DISABLED = colors["disabled"]
    PANEL_OVERLAY = colors["panel_overlay"]
    return _ACTIVE_THEME


def viewer_theme_colors() -> dict[str, str]:
    return dict(THEME_PALETTES[_ACTIVE_THEME])


set_viewer_theme("dark")
EFFECT_KINDS = frozenset({"mosaic", "blur"})
COLOR_PRESETS = (
    ("红色", "#ff4c48"),
    ("橙色", "#ff9f0a"),
    ("黄色", "#ffd60a"),
    ("绿色", "#30d158"),
    ("蓝色", "#38bdf8"),
    ("紫色", "#a855f7"),
    ("白色", "#ffffff"),
    ("黑色", "#111111"),
)
SHAPE_SIZE_PRESETS = (2, 4, 6, 8, 12, 16)
TEXT_SIZE_PRESETS = (12, 16, 20, 24, 32, 48, 64)
EFFECT_SIZE_PRESETS = (4, 8, 12, 16, 24, 32)


def _effect_radius(size: int) -> int:
    """Map the toolbar strength to a visibly useful Gaussian radius."""

    return max(3, round(size * 1.5))


def _effect_sample_box(
    image_size: tuple[int, int],
    target_box: tuple[int, int, int, int],
    kind: str,
    size: int,
) -> tuple[int, int, int, int]:
    if kind != "blur":
        return target_box
    left, top, right, bottom = target_box
    padding = _effect_radius(size) * 3
    return (
        max(0, left - padding),
        max(0, top - padding),
        min(image_size[0], right + padding),
        min(image_size[1], bottom + padding),
    )


def _render_effect_sample(
    sample: Image.Image,
    kind: str,
    size: int,
    target_offset: tuple[int, int],
    target_size: tuple[int, int],
) -> Image.Image:
    """Render a crisp mosaic or a padded blur from a cropped source sample."""

    offset_x, offset_y = target_offset
    target_width, target_height = target_size
    target_box = (
        offset_x,
        offset_y,
        offset_x + target_width,
        offset_y + target_height,
    )
    region = sample.crop(target_box)
    if kind == "mosaic":
        try:
            cell = max(4, int(size) * 2)
            small = region.resize(
                (max(1, region.width // cell), max(1, region.height // cell)),
                Image.Resampling.BOX,
            )
            try:
                return small.resize(region.size, Image.Resampling.NEAREST)
            finally:
                small.close()
        finally:
            region.close()

    region.close()
    blurred = sample.filter(ImageFilter.GaussianBlur(radius=_effect_radius(size)))
    try:
        return blurred.crop(target_box)
    finally:
        blurred.close()


def pil_to_qimage(image: Image.Image) -> QImage:
    rgba = image.convert("RGBA")
    try:
        data = rgba.tobytes("raw", "RGBA")
        return QImage(
            data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888
        ).copy()
    finally:
        rgba.close()


def qimage_to_pil(image: QImage) -> Image.Image:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pointer = converted.bits()
    return Image.frombytes(
        "RGBA",
        (converted.width(), converted.height()),
        bytes(pointer[: converted.sizeInBytes()]),
        "raw",
        "RGBA",
    )


class ViewerIconButton(QPushButton):
    """Small, antialiased viewer control with an optional painted icon."""

    def __init__(
        self,
        icon_name: str = "",
        *,
        text: str = "",
        tooltip: str,
        parent: QWidget | None = None,
        width: int = 40,
    ) -> None:
        super().__init__(text, parent)
        self.icon_name = icon_name
        self._selected = False
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(width, 36)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QPushButton {{
                color: {TEXT}; background: transparent; border: 0;
                border-radius: 10px; padding: 0;
                font: 500 14px 'Microsoft YaHei UI';
            }}
            QPushButton:hover {{ background: {HOVER}; }}
            QPushButton:pressed {{ background: {PRESSED}; }}
            QPushButton:disabled {{ color: {DISABLED}; }}
            """
        )
        self.update()

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.icon_name:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(ACCENT if self._selected else TEXT)
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        rect = self.rect()
        cx, cy = rect.center().x(), rect.center().y()
        name = self.icon_name
        if name == "minus":
            painter.drawLine(cx - 6, cy, cx + 6, cy)
        elif name == "plus":
            painter.drawLine(cx - 6, cy, cx + 6, cy)
            painter.drawLine(cx, cy - 6, cx, cy + 6)
        elif name == "rotate-cw":
            path = QPainterPath()
            icon_bounds = QRectF(cx - 7, cy - 7, 14, 14)
            path.arcMoveTo(icon_bounds, 0)
            path.arcTo(icon_bounds, 0, -315)
            arrow_tip = QPointF(cx + 7, cy - 4)
            path.lineTo(arrow_tip)
            painter.drawPath(path)
            painter.drawLine(arrow_tip, QPointF(cx + 7, cy - 8))
            painter.drawLine(arrow_tip, QPointF(cx + 3, cy - 4))
        elif name == "sun":
            painter.drawEllipse(QPointF(cx, cy), 4.2, 4.2)
            for index in range(8):
                angle = index * math.pi / 4
                inner = QPointF(cx + math.cos(angle) * 7, cy + math.sin(angle) * 7)
                outer = QPointF(cx + math.cos(angle) * 9, cy + math.sin(angle) * 9)
                painter.drawLine(inner, outer)
        elif name == "moon":
            moon = QPainterPath()
            moon.addEllipse(QPointF(cx, cy), 8, 8)
            cutout = QPainterPath()
            cutout.addEllipse(QPointF(cx + 4, cy - 3), 7, 7)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPath(moon.subtracted(cutout))
        elif name == "fit":
            painter.drawLine(cx - 7, cy - 3, cx - 7, cy - 7)
            painter.drawLine(cx - 7, cy - 7, cx - 3, cy - 7)
            painter.drawLine(cx + 3, cy - 7, cx + 7, cy - 7)
            painter.drawLine(cx + 7, cy - 7, cx + 7, cy - 3)
            painter.drawLine(cx - 7, cy + 3, cx - 7, cy + 7)
            painter.drawLine(cx - 7, cy + 7, cx - 3, cy + 7)
            painter.drawLine(cx + 3, cy + 7, cx + 7, cy + 7)
            painter.drawLine(cx + 7, cy + 7, cx + 7, cy + 3)
        elif name == "fullscreen":
            painter.drawRect(cx - 7, cy - 5, 10, 10)
            painter.drawRect(cx - 2, cy - 8, 10, 10)
        elif name == "pin":
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(cx - 4, cy - 7),
                        QPointF(cx + 4, cy - 7),
                        QPointF(cx + 2, cy),
                        QPointF(cx + 5, cy + 3),
                        QPointF(cx - 5, cy + 3),
                        QPointF(cx - 2, cy),
                    ]
                )
            )
            painter.drawLine(cx, cy + 3, cx, cy + 8)
            if self._selected:
                painter.setBrush(color)
                painter.drawPolygon(
                    QPolygonF(
                        [
                            QPointF(cx - 4, cy - 7),
                            QPointF(cx + 4, cy - 7),
                            QPointF(cx + 2, cy),
                            QPointF(cx + 5, cy + 3),
                            QPointF(cx - 5, cy + 3),
                            QPointF(cx - 2, cy),
                        ]
                    )
                )
        elif name == "favorite":
            star = QPainterPath()
            for index in range(10):
                angle = -math.pi / 2 + index * math.pi / 5
                radius = 8.4 if index % 2 == 0 else 3.9
                point = QPointF(
                    cx + math.cos(angle) * radius,
                    cy + math.sin(angle) * radius,
                )
                if index == 0:
                    star.moveTo(point)
                else:
                    star.lineTo(point)
            star.closeSubpath()
            painter.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(color if self._selected else Qt.BrushStyle.NoBrush)
            painter.drawPath(star)
        elif name == "edit":
            painter.drawLine(cx - 6, cy + 6, cx + 5, cy - 5)
            painter.drawLine(cx - 7, cy + 7, cx - 3, cy + 6)
            painter.drawLine(cx + 4, cy - 6, cx + 7, cy - 3)
        elif name == "settings":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for y_offset, knob_offset in ((-6, -3), (0, 4), (6, -1)):
                y = cy + y_offset
                knob_x = cx + knob_offset
                painter.drawLine(QPointF(cx - 8, y), QPointF(knob_x - 3, y))
                painter.drawLine(QPointF(knob_x + 3, y), QPointF(cx + 8, y))
                painter.drawEllipse(QPointF(knob_x, y), 2.6, 2.6)
        painter.end()


class ViewerCheckBox(QCheckBox):
    """High-contrast checkbox matching the viewer toolbar palette."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(26)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(hint.width() + 8, max(26, hint.height()))

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        checked = self.checkState() == Qt.CheckState.Checked
        partial = self.checkState() == Qt.CheckState.PartiallyChecked
        hovered = self.underMouse()
        focused = self.hasFocus()

        indicator = QRectF(1.0, (self.height() - 18.0) / 2, 18.0, 18.0)
        if checked or partial:
            fill = QColor(ACCENT if enabled else DISABLED)
            border = QColor(ACCENT if enabled else DISABLED)
        else:
            fill = QColor(HOVER if hovered and enabled else SURFACE_HIGH)
            border = QColor(ACCENT if (hovered or focused) and enabled else BORDER)
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(indicator, 5.0, 5.0)

        if checked:
            check_pen = QPen(QColor(viewer_theme_colors()["accent_text"]), 2.2)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            check = QPainterPath()
            check.moveTo(indicator.left() + 4.5, indicator.center().y())
            check.lineTo(indicator.left() + 7.7, indicator.bottom() - 4.2)
            check.lineTo(indicator.right() - 3.8, indicator.top() + 4.5)
            painter.drawPath(check)
        elif partial:
            dash_pen = QPen(QColor(viewer_theme_colors()["accent_text"]), 2.2)
            dash_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(dash_pen)
            painter.drawLine(
                QPointF(indicator.left() + 4.5, indicator.center().y()),
                QPointF(indicator.right() - 4.5, indicator.center().y()),
            )

        font = painter.font()
        font.setFamily("Microsoft YaHei UI")
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor(TEXT if enabled else DISABLED))
        text_rect = QRectF(
            indicator.right() + 9,
            0,
            max(0, self.width() - int(indicator.right()) - 9),
            self.height(),
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )
        painter.end()

    def enterEvent(self, event: QEnterEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class WindowControlButton(QPushButton):
    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self._maximized = False
        labels = {"minimize": "最小化", "maximize": "最大化", "close": "关闭"}
        self.setToolTip(labels[kind])
        self.setAccessibleName(labels[kind])
        self.setFixedSize(46, 41)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.apply_theme()

    def apply_theme(self) -> None:
        hover = "#b64149" if self.kind == "close" else HOVER
        self.setStyleSheet(
            "QPushButton {background: transparent; border: 0; border-radius: 0;}"
            f"QPushButton:hover {{background: {hover};}}"
            f"QPushButton:pressed {{background: {'#96343b' if self.kind == 'close' else PRESSED};}}"
        )
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(TEXT), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        cx, cy = self.rect().center().x(), self.rect().center().y()
        if self.kind == "minimize":
            painter.drawLine(cx - 5, cy, cx + 5, cy)
        elif self.kind == "maximize":
            if self._maximized:
                painter.drawRect(cx - 3, cy - 5, 8, 8)
                painter.drawRect(cx - 6, cy - 2, 8, 8)
            else:
                painter.drawRect(cx - 5, cy - 5, 10, 10)
        else:
            painter.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
            painter.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)
        painter.end()

    def set_maximized(self, maximized: bool) -> None:
        if self.kind != "maximize":
            return
        self._maximized = bool(maximized)
        label = "还原窗口" if self._maximized else "最大化"
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.update()


class InlineFileNameEditor(QLineEdit):
    cancelRequested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ViewerTitleBar(QFrame):
    renameSubmitted = Signal(str)

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.host_window = window
        self._restore_geometry: QRect | None = None
        self._drag_start_global: QPoint | None = None
        self._drag_start_local = QPoint()
        self.setFixedHeight(42)
        self.setObjectName("viewerTitleBar")
        self.setStyleSheet(
            f"#viewerTitleBar {{background: {SURFACE}; border: 0; "
            f"border-bottom: 1px solid {BORDER}; border-radius: 0;}}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(0)
        self.mark = QLabel("●", self)
        self.mark.setFixedWidth(22)
        self.title_label = QPushButton("CSImage", self)
        self.title_label.setObjectName("titleNameButton")
        self.title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_label.setToolTip("点击修改图片名称")
        self.title_label.clicked.connect(self.begin_name_edit)
        self._name_editing = False
        self.name_editor = InlineFileNameEditor(self)
        self.name_editor.setObjectName("titleNameEditor")
        self.name_editor.setToolTip("按 Enter 保存，按 Esc 取消")
        self.name_editor.returnPressed.connect(self._submit_name_edit)
        self.name_editor.editingFinished.connect(self._submit_name_edit)
        self.name_editor.cancelRequested.connect(self._cancel_name_edit)
        self.name_editor.hide()
        self.format_button = QPushButton("", self)
        self.format_button.setObjectName("titleFormatButton")
        self.format_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.format_button.setToolTip("点击转换图片格式（原文件会保留）")
        self.format_button.hide()
        self.context_label = QLabel("", self)
        self.context_label.setObjectName("titleContextLabel")
        self.context_label.hide()
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        layout.addWidget(self.mark)
        layout.addWidget(self.title_label)
        layout.addWidget(self.name_editor)
        layout.addWidget(self.format_button)
        layout.addWidget(self.context_label)
        layout.addStretch(1)
        self.window_controls = QFrame(self)
        self.window_controls.setObjectName("windowControls")
        self.window_controls.setStyleSheet(
            "#windowControls {background: transparent; border: 0; border-radius: 0;}"
        )
        controls_layout = QHBoxLayout(self.window_controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(0)
        self.minimize_button = WindowControlButton("minimize", self.window_controls)
        self.maximize_button = WindowControlButton("maximize", self.window_controls)
        self.close_button = WindowControlButton("close", self.window_controls)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximize)
        self.close_button.clicked.connect(window.close)
        controls_layout.addWidget(self.minimize_button)
        controls_layout.addWidget(self.maximize_button)
        controls_layout.addWidget(self.close_button)
        layout.addWidget(self.window_controls)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"#viewerTitleBar {{background: {SURFACE}; border: 0; "
            f"border-bottom: 1px solid {BORDER}; border-radius: 0;}}"
        )
        self.mark.setStyleSheet(f"color: {TEXT}; font-size: 18px; background: transparent;")
        self.title_label.setStyleSheet(
            f"#titleNameButton {{color: {TEXT}; background: transparent; border: 0; "
            "border-radius: 6px; padding: 4px 3px; font: 600 13px 'Microsoft YaHei UI';}}"
            f"#titleNameButton:hover {{background: {HOVER};}}"
            f"#titleNameButton:pressed {{background: {PRESSED};}}"
        )
        self.name_editor.setStyleSheet(
            f"#titleNameEditor {{color: {TEXT}; background: {SURFACE_HIGH}; "
            f"border: 1px solid {ACCENT}; border-radius: 6px; padding: 3px 7px; "
            "font: 600 13px 'Microsoft YaHei UI'; selection-background-color: "
            f"{SELECTED};}}"
        )
        self.format_button.setStyleSheet(
            f"#titleFormatButton {{color: {MUTED}; background: {SURFACE_HIGH}; "
            f"border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 7px; "
            "font: 600 11px 'Microsoft YaHei UI';}}"
            f"#titleFormatButton:hover {{color: {TEXT}; border-color: {ACCENT};}}"
            f"#titleFormatButton::menu-indicator {{image: none; width: 0;}}"
        )
        self.context_label.setStyleSheet(
            f"color: {MUTED}; background: transparent; padding-left: 5px; "
            "font: 12px 'Microsoft YaHei UI';"
        )
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.apply_theme()

    def set_title(self, title: str) -> None:
        self._cancel_name_edit()
        self.title_label.setText(title or "CSImage")
        self.title_label.setToolTip("")
        self.title_label.setCursor(Qt.CursorShape.ArrowCursor)
        self.format_button.hide()
        self.context_label.hide()

    def set_file_title(self, path: Path, context: str = "") -> None:
        self._cancel_name_edit()
        self.title_label.setText(path.stem)
        self.title_label.setToolTip(f"{path.name}\n点击修改图片名称")
        self.title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.format_button.setText(path.suffix.upper() or "格式")
        self.format_button.show()
        self.context_label.setText(context)
        self.context_label.setVisible(bool(context))

    def begin_name_edit(self) -> None:
        if self.format_button.isHidden() or self._name_editing:
            return
        self._name_editing = True
        self.name_editor.setText(self.title_label.text())
        self.name_editor.setFixedWidth(max(130, min(420, self.title_label.width() + 46)))
        self.title_label.hide()
        self.name_editor.show()
        self.name_editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self.name_editor.selectAll()

    def _submit_name_edit(self) -> None:
        if not self._name_editing:
            return
        value = self.name_editor.text().strip()
        self._name_editing = False
        self.name_editor.hide()
        self.title_label.show()
        if value and value != self.title_label.text():
            self.renameSubmitted.emit(value)

    def _cancel_name_edit(self) -> None:
        if not self._name_editing:
            return
        self._name_editing = False
        self.name_editor.hide()
        self.title_label.show()

    def cancel_name_edit(self) -> bool:
        if not self._name_editing:
            return False
        self._cancel_name_edit()
        return True

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self._name_editing and event.type() == QEvent.Type.MouseButtonPress:
            clicked_editor = watched is self.name_editor or (
                isinstance(watched, QWidget) and self.name_editor.isAncestorOf(watched)
            )
            if not clicked_editor:
                self._submit_name_edit()
        return super().eventFilter(watched, event)

    def toggle_maximize(self) -> None:
        self._clear_pending_drag()
        maximized = self.host_window.isMaximized()
        current_geometry = self.host_window.geometry()
        if (
            self._restore_geometry is not None
            and not maximized
            and current_geometry == self._restore_geometry
        ):
            # The OS may have restored the frameless window independently.
            self._restore_geometry = None
        if maximized or self._restore_geometry is not None:
            restore_geometry = self._restore_geometry or self.host_window.normalGeometry()
            self.host_window.showNormal()
            if restore_geometry.isValid() and not restore_geometry.isEmpty():
                self.host_window.setGeometry(restore_geometry)
            self._restore_geometry = None
            self.maximize_button.set_maximized(False)
        else:
            self._restore_geometry = current_geometry
            self.host_window.showMaximized()
            self.maximize_button.set_maximized(True)

    def _clear_pending_drag(self) -> None:
        self._drag_start_global = None

    def _restore_for_drag(self, global_position: QPoint) -> None:
        maximized_geometry = self.host_window.geometry()
        restore_geometry = self._restore_geometry or self.host_window.normalGeometry()
        if not restore_geometry.isValid() or restore_geometry.isEmpty():
            restore_geometry = QRect(0, 0, 960, 640)

        maximized_width = max(1, maximized_geometry.width())
        horizontal_ratio = min(
            1.0,
            max(0.0, self._drag_start_local.x() / maximized_width),
        )
        restored_width = restore_geometry.width()
        restored_height = restore_geometry.height()
        restored_x = global_position.x() - round(restored_width * horizontal_ratio)
        restored_y = global_position.y() - min(
            max(0, self._drag_start_local.y()),
            self.height() - 1,
        )

        screen = QApplication.screenAt(global_position) or self.host_window.screen()
        if screen is not None:
            available = screen.availableGeometry()
            restored_x = min(
                max(available.left(), restored_x),
                max(available.left(), available.right() - restored_width + 1),
            )
            restored_y = min(
                max(available.top(), restored_y),
                max(available.top(), available.bottom() - restored_height + 1),
            )

        self.host_window.showNormal()
        self.host_window.setGeometry(
            restored_x,
            restored_y,
            restored_width,
            restored_height,
        )
        self._restore_geometry = None
        self.maximize_button.set_maximized(False)
        self._clear_pending_drag()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.host_window.isFullScreen():
            if self.host_window.isMaximized():
                self._drag_start_global = event.globalPosition().toPoint()
                self._drag_start_local = event.position().toPoint()
                event.accept()
                return
            handle = self.host_window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._drag_start_global is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.globalPosition().toPoint() - self._drag_start_global).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._restore_for_drag(event.globalPosition().toPoint())
            handle = self.host_window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._clear_pending_drag()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class NativeResizeFilter(QObject):
    """Delegate frameless edge resizing to ``QWindow.startSystemResize``."""

    def __init__(self, window: QWidget, margin: int = 8) -> None:
        super().__init__(window)
        self.window = window
        self.margin = margin
        self._resize_cursor_active = False
        # Mouse moves without a pressed button are only delivered to widgets
        # with mouse tracking enabled.  A frameless edge can sit above any of
        # the viewer's child widgets, so every existing child must participate
        # in resize-hover detection.
        for widget in (window, *window.findChildren(QWidget)):
            widget.setMouseTracking(True)

    def _edges(self, global_pos: QPoint) -> Qt.Edge:
        if self.window.isMaximized() or self.window.isFullScreen():
            return Qt.Edge(0)
        rect = self.window.frameGeometry()
        left = global_pos.x() <= rect.left() + self.margin
        right = global_pos.x() >= rect.right() - self.margin
        top = global_pos.y() <= rect.top() + self.margin
        bottom = global_pos.y() >= rect.bottom() - self.margin
        edges = Qt.Edge(0)
        if left:
            edges |= Qt.Edge.LeftEdge
        if right:
            edges |= Qt.Edge.RightEdge
        if top:
            edges |= Qt.Edge.TopEdge
        if bottom:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor(edges: Qt.Edge) -> Qt.CursorShape:
        if edges in {Qt.Edge.LeftEdge, Qt.Edge.RightEdge}:
            return Qt.CursorShape.SizeHorCursor
        if edges in {Qt.Edge.TopEdge, Qt.Edge.BottomEdge}:
            return Qt.CursorShape.SizeVerCursor
        if edges in {
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
        }:
            return Qt.CursorShape.SizeFDiagCursor
        if edges in {
            Qt.Edge.RightEdge | Qt.Edge.TopEdge,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
        }:
            return Qt.CursorShape.SizeBDiagCursor
        return Qt.CursorShape.ArrowCursor

    def _restore_resize_cursor(self) -> None:
        if not self._resize_cursor_active:
            return
        QApplication.restoreOverrideCursor()
        self._resize_cursor_active = False

    def _show_resize_cursor(self, edges: Qt.Edge) -> None:
        if not edges:
            self._restore_resize_cursor()
            return
        cursor = QCursor(self._cursor(edges))
        if self._resize_cursor_active:
            QApplication.changeOverrideCursor(cursor)
        else:
            QApplication.setOverrideCursor(cursor)
            self._resize_cursor_active = True

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        widget = watched if isinstance(watched, QWidget) else None
        if widget is None or widget.window() is not self.window:
            return False
        if event.type() in {QEvent.Type.MouseMove, QEvent.Type.Enter} and isinstance(
            event, (QMouseEvent, QEnterEvent)
        ):
            global_pos = event.globalPosition().toPoint()
            edges = self._edges(global_pos)
            self._show_resize_cursor(edges)
        elif widget is self.window and event.type() in {
            QEvent.Type.Leave,
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.WindowStateChange,
        }:
            self._restore_resize_cursor()
        elif event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            if event.button() == Qt.MouseButton.LeftButton:
                edges = self._edges(event.globalPosition().toPoint())
                handle = self.window.windowHandle()
                if edges and handle is not None and handle.startSystemResize(edges):
                    event.accept()
                    return True
        return False


@dataclass
class AnnotationSpec:
    kind: str
    start: QPointF
    end: QPointF
    color: str = "#ff4c48"
    size: int = 6
    text: str = ""
    number: int = 0
    points: tuple[QPointF, ...] = ()
    control: QPointF | None = None


@dataclass(frozen=True)
class _ImageViewState:
    transform: QTransform
    center_ratio: QPointF
    fit_mode: bool


def _clone_annotation_spec(spec: AnnotationSpec) -> AnnotationSpec:
    return AnnotationSpec(
        spec.kind,
        QPointF(spec.start),
        QPointF(spec.end),
        color=spec.color,
        size=spec.size,
        text=spec.text,
        number=spec.number,
        points=tuple(QPointF(point) for point in spec.points),
        control=QPointF(spec.control) if spec.control is not None else None,
    )


def _annotation_signature(spec: AnnotationSpec) -> tuple[object, ...]:
    return (
        spec.kind,
        spec.start.x(),
        spec.start.y(),
        spec.end.x(),
        spec.end.y(),
        spec.color,
        spec.size,
        spec.text,
        spec.number,
        tuple((point.x(), point.y()) for point in spec.points),
        (spec.control.x(), spec.control.y()) if spec.control is not None else None,
    )


class MarkupItem(QGraphicsItem):
    def __init__(
        self,
        spec: AnnotationSpec,
        source_image: QImage | None = None,
        *,
        draft: bool = False,
        changed: Callable[[MarkupItem], None] | None = None,
        change_started: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.spec = spec
        self._source_image = source_image
        self._draft = draft
        self._changed = changed
        self._change_started = change_started
        self._resize_handle: str | None = None
        self._resize_snapshot: AnnotationSpec | None = None
        self._effect_cache_key: tuple[str, int, int, int, int, int] | None = None
        self._effect_cache: QPixmap | None = None
        self.set_interactive(not draft)
        self.setZValue(20)

    def set_interactive(self, interactive: bool) -> None:
        """Enable post-draw editing except for destructive brush effects."""

        interactive = interactive and self.spec.kind != "mosaic"
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, interactive)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, interactive)
        self.setAcceptHoverEvents(interactive)
        self.setAcceptedMouseButtons(
            Qt.MouseButton.LeftButton if interactive else Qt.MouseButton.NoButton
        )
        if not interactive:
            self.setSelected(False)
            self.unsetCursor()

    def boundingRect(self) -> QRectF:
        points = [self.spec.start, self.spec.end, *self.spec.points]
        if self.spec.control is not None:
            points.append(self.spec.control)
        left = min(point.x() for point in points)
        top = min(point.y() for point in points)
        right = max(point.x() for point in points)
        bottom = max(point.y() for point in points)
        if self.spec.kind == "text":
            padding = max(4.0, self.spec.size * 0.15)
            return QRectF(left, top, right - left, bottom - top).adjusted(
                -padding, -padding, padding, padding
            )
        padding = max(14.0, float(self.spec.size * 3))
        if self.spec.kind == "number":
            radius = max(15.0, float(self.spec.size * 3))
            return QRectF(
                self.spec.start.x() - radius,
                self.spec.start.y() - radius,
                radius * 2,
                radius * 2,
            ).adjusted(-3, -3, 3, 3)
        return QRectF(left, top, right - left, bottom - top).adjusted(
            -padding, -padding, padding, padding
        )

    def shape(self) -> QPainterPath:
        if self.spec.kind in {"freehand", "mosaic"}:
            points = self.spec.points or (self.spec.start, self.spec.end)
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            if len(points) == 1:
                radius = max(8.0, self.spec.size * 1.5)
                path.addEllipse(points[0], radius, radius)
                return path
            stroker = QPainterPathStroker()
            width = (
                max(12.0, float(self.spec.size * 3))
                if self.spec.kind == "mosaic"
                else max(float(self.spec.size + 8), 12.0 / self._view_scale())
            )
            stroker.setWidth(width)
            stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            return stroker.createStroke(path)
        if self.spec.kind not in {"line", "arrow"}:
            return super().shape()
        path = QPainterPath(self.spec.start)
        path.quadTo(self._curve_control(), self.spec.end)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(float(self.spec.size + 8), 12.0 / self._view_scale()))
        hit_shape = stroker.createStroke(path)
        radius = 9.0 / self._view_scale()
        for position in self._handle_positions().values():
            hit_shape.addEllipse(position, radius, radius)
        return hit_shape

    def set_end(self, point: QPointF) -> None:
        self.prepareGeometryChange()
        self.spec.end = point
        if self.spec.kind in {"line", "arrow"} and self._draft:
            self.spec.control = QPointF(
                (self.spec.start.x() + point.x()) / 2,
                (self.spec.start.y() + point.y()) / 2,
            )
        self._effect_cache_key = None
        self._effect_cache = None
        self.update()

    def finish_draft(self) -> None:
        self._draft = False
        self.set_interactive(True)
        self.setSelected(False)
        self.update()

    def set_color(self, color: str) -> None:
        candidate = QColor(color)
        if not candidate.isValid():
            return
        self.spec.color = candidate.name()
        self.update()
        self._notify_changed()

    def set_size(self, size: int) -> None:
        self.prepareGeometryChange()
        self.spec.size = max(1, int(size))
        self._invalidate_effect_cache()
        self.update()
        self._notify_changed()

    def add_point(self, point: QPointF) -> None:
        self.prepareGeometryChange()
        self.spec.points = (*self.spec.points, point)
        self.spec.end = point
        self.update()

    def paint(self, painter: QPainter, _option: object, _widget: QWidget | None = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.spec.color)
        pen = QPen(
            color,
            self.spec.size,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        bounds = QRectF(self.spec.start, self.spec.end).normalized()
        if self.spec.kind == "rectangle":
            painter.drawRect(bounds)
            self._paint_selection(painter)
            return
        if self.spec.kind == "ellipse":
            painter.drawEllipse(bounds)
            self._paint_selection(painter)
            return
        if self.spec.kind == "freehand":
            points = self.spec.points or (self.spec.start, self.spec.end)
            if len(points) >= 2:
                path = QPainterPath(points[0])
                for point in points[1:]:
                    path.lineTo(point)
                painter.drawPath(path)
            self._paint_selection(painter)
            return
        if self.spec.kind == "text":
            font = QFont("Microsoft YaHei UI")
            font.setPixelSize(max(1, self.spec.size))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.drawText(
                bounds,
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextSingleLine,
                self.spec.text or "文字",
            )
            self._paint_selection(painter)
            return
        if self.spec.kind == "number":
            radius = max(15.0, float(self.spec.size * 3))
            circle = QRectF(
                self.spec.start.x() - radius,
                self.spec.start.y() - radius,
                radius * 2,
                radius * 2,
            )
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(circle)
            painter.setPen(QColor("#ffffff"))
            font = QFont("Microsoft YaHei UI", max(11, self.spec.size * 3))
            font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(self.spec.number))
            self._paint_selection(painter)
            return
        if self.spec.kind == "redact":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#111315"))
            painter.drawRect(bounds)
            self._paint_selection(painter)
            return
        if self.spec.kind == "mosaic":
            points = self.spec.points or (self.spec.start, self.spec.end)
            brush_width = max(12.0, float(self.spec.size * 3))
            clip = QPainterPath(points[0])
            for point in points[1:]:
                clip.lineTo(point)
            if len(points) == 1:
                clip.addEllipse(points[0], brush_width / 2, brush_width / 2)
            else:
                stroker = QPainterPathStroker()
                stroker.setWidth(brush_width)
                stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
                stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                clip = stroker.createStroke(clip)
            stroke_bounds = self._selection_rect().adjusted(
                -brush_width / 2,
                -brush_width / 2,
                brush_width / 2,
                brush_width / 2,
            )
            preview = self._effect_preview(stroke_bounds)
            if preview is not None:
                pixmap, target = preview
                painter.save()
                painter.setClipPath(clip)
                painter.drawPixmap(
                    target,
                    pixmap,
                    QRectF(0, 0, pixmap.width(), pixmap.height()),
                )
                painter.restore()
            # The pixelated preview already shows the brush footprint while drawing.
            # Outlining the stroked clipping path exposes every sampled segment and
            # produces a distracting nest of coloured circles and dashed boxes.
            self._paint_selection(painter)
            return
        if self.spec.kind == "blur":
            preview = self._effect_preview(bounds)
            if preview is not None:
                pixmap, target = preview
                painter.drawPixmap(
                    target,
                    pixmap,
                    QRectF(0, 0, pixmap.width(), pixmap.height()),
                )
            if self._draft:
                painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(bounds)
            self._paint_selection(painter)
            return
        start, end = self.spec.start, self.spec.end
        control = self._curve_control()
        path = QPainterPath(start)
        path.quadTo(control, end)
        painter.drawPath(path)
        if self.spec.kind == "line":
            self._paint_selection(painter)
            return
        tangent_start = control
        if math.hypot(end.x() - control.x(), end.y() - control.y()) < 0.01:
            tangent_start = start
        angle = math.atan2(end.y() - tangent_start.y(), end.x() - tangent_start.x())
        length = max(16.0, float(self.spec.size * 4))
        wing = math.radians(28)
        left = QPointF(
            end.x() - math.cos(angle - wing) * length, end.y() - math.sin(angle - wing) * length
        )
        right = QPointF(
            end.x() - math.cos(angle + wing) * length, end.y() - math.sin(angle + wing) * length
        )
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([end, left, right]))
        self._paint_selection(painter)

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:
        handle = self._handle_at(event.pos()) if self.isSelected() else None
        if handle in {"nw", "se"}:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle in {"ne", "sw"}:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif handle is not None:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._change_started is not None:
                self._change_started()
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            handle = self._handle_at(event.pos())
            if handle is not None:
                self._resize_handle = handle
                self._resize_snapshot = self._copy_spec()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._resize_handle is not None and self._resize_snapshot is not None:
            self._resize_from_snapshot(event.scenePos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._resize_handle is not None:
            self._resize_handle = None
            self._resize_snapshot = None
            self._notify_changed()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        delta = self.pos()
        if abs(delta.x()) > 0.01 or abs(delta.y()) > 0.01:
            self.prepareGeometryChange()
            # Draft brush strokes intentionally reuse the first QPointF for
            # ``start`` and ``points[0]``.  In-place ``+=`` mutates that shared
            # object and used to apply the drag delta twice to mosaic/freehand
            # points.  Rebuild every coordinate so the whole mark moves once.
            self.spec.start = QPointF(
                self.spec.start.x() + delta.x(),
                self.spec.start.y() + delta.y(),
            )
            self.spec.end = QPointF(
                self.spec.end.x() + delta.x(),
                self.spec.end.y() + delta.y(),
            )
            self.spec.points = tuple(
                QPointF(point.x() + delta.x(), point.y() + delta.y()) for point in self.spec.points
            )
            if self.spec.control is not None:
                self.spec.control = QPointF(
                    self.spec.control.x() + delta.x(),
                    self.spec.control.y() + delta.y(),
                )
            self.setPos(0, 0)
            self._invalidate_effect_cache()
            self.update()
            self._notify_changed()
        else:
            self._notify_changed()

    def _copy_spec(self) -> AnnotationSpec:
        return _clone_annotation_spec(self.spec)

    def _resize_from_snapshot(self, scene_point: QPointF) -> None:
        snapshot = self._resize_snapshot
        handle = self._resize_handle
        if snapshot is None or handle is None:
            return
        point = self._bounded_point(scene_point)
        self.prepareGeometryChange()
        if snapshot.kind in {"line", "arrow"}:
            if handle == "start":
                self.spec.start = point
            elif handle == "control":
                self.spec.control = QPointF(
                    2 * point.x() - (self.spec.start.x() + self.spec.end.x()) / 2,
                    2 * point.y() - (self.spec.start.y() + self.spec.end.y()) / 2,
                )
            else:
                self.spec.end = point
        else:
            original = self._selection_rect_for(snapshot)
            opposite = {
                "nw": original.bottomRight(),
                "ne": original.bottomLeft(),
                "se": original.topLeft(),
                "sw": original.topRight(),
            }.get(handle)
            if opposite is None:
                return
            resized = QRectF(opposite, point).normalized()
            if resized.width() < 2 or resized.height() < 2:
                return
            self.spec.start = resized.topLeft()
            self.spec.end = resized.bottomRight()
            if snapshot.kind in {"freehand", "mosaic"} and snapshot.points:
                source = self._selection_rect_for(snapshot)
                width = max(1.0, source.width())
                height = max(1.0, source.height())
                self.spec.points = tuple(
                    QPointF(
                        resized.left() + (value.x() - source.left()) / width * resized.width(),
                        resized.top() + (value.y() - source.top()) / height * resized.height(),
                    )
                    for value in snapshot.points
                )
            if snapshot.kind == "text" and original.height() > 0:
                self.spec.size = max(
                    1,
                    round(snapshot.size * resized.height() / original.height()),
                )
        self._invalidate_effect_cache()
        self.update()

    def _bounded_point(self, point: QPointF) -> QPointF:
        return QPointF(point)

    def _selection_rect(self) -> QRectF:
        return self._selection_rect_for(self.spec)

    @staticmethod
    def _selection_rect_for(spec: AnnotationSpec) -> QRectF:
        if spec.kind in {"freehand", "mosaic"} and spec.points:
            left = min(point.x() for point in spec.points)
            top = min(point.y() for point in spec.points)
            right = max(point.x() for point in spec.points)
            bottom = max(point.y() for point in spec.points)
            return QRectF(left, top, right - left, bottom - top)
        if spec.kind == "number":
            radius = max(15.0, float(spec.size * 3))
            return QRectF(
                spec.start.x() - radius,
                spec.start.y() - radius,
                radius * 2,
                radius * 2,
            )
        return QRectF(spec.start, spec.end).normalized()

    def _handle_positions(self) -> dict[str, QPointF]:
        if self.spec.kind in {"line", "arrow"}:
            return {
                "start": self.spec.start,
                "control": self._curve_midpoint(),
                "end": self.spec.end,
            }
        if self.spec.kind == "number":
            return {}
        rect = self._selection_rect()
        return {
            "nw": rect.topLeft(),
            "ne": rect.topRight(),
            "se": rect.bottomRight(),
            "sw": rect.bottomLeft(),
        }

    def _view_scale(self) -> float:
        if self.scene() is not None and self.scene().views():
            return max(0.01, abs(self.scene().views()[0].transform().m11()))
        return 1.0

    def _handle_at(self, point: QPointF) -> str | None:
        radius = 8.0 / self._view_scale()
        for name, position in self._handle_positions().items():
            if math.hypot(point.x() - position.x(), point.y() - position.y()) <= radius:
                return name
        return None

    def _paint_selection(self, painter: QPainter) -> None:
        if not self.isSelected() or self._draft:
            return
        scale = self._view_scale()
        pen = QPen(QColor("#2f88ff"), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.spec.kind not in {"line", "arrow"}:
            painter.drawRect(self._selection_rect())
        radius = 4.5 / scale
        painter.setPen(QPen(QColor("#2f88ff"), 1 / scale))
        painter.setBrush(QColor("#ffffff"))
        for position in self._handle_positions().values():
            painter.drawEllipse(position, radius, radius)

    def _curve_control(self) -> QPointF:
        if self.spec.control is not None:
            return self.spec.control
        return QPointF(
            (self.spec.start.x() + self.spec.end.x()) / 2,
            (self.spec.start.y() + self.spec.end.y()) / 2,
        )

    def _curve_midpoint(self) -> QPointF:
        control = self._curve_control()
        return QPointF(
            0.25 * self.spec.start.x() + 0.5 * control.x() + 0.25 * self.spec.end.x(),
            0.25 * self.spec.start.y() + 0.5 * control.y() + 0.25 * self.spec.end.y(),
        )

    def _invalidate_effect_cache(self) -> None:
        self._effect_cache_key = None
        self._effect_cache = None

    def _notify_changed(self) -> None:
        if self._changed is not None:
            self._changed(self)

    def _effect_preview(self, bounds: QRectF) -> tuple[QPixmap, QRectF] | None:
        source = self._source_image
        if source is None or bounds.width() < 1 or bounds.height() < 1:
            return None
        left = max(0, math.floor(bounds.left()))
        top = max(0, math.floor(bounds.top()))
        right = min(source.width(), math.ceil(bounds.right()))
        bottom = min(source.height(), math.ceil(bounds.bottom()))
        if right <= left or bottom <= top:
            return None
        key = (self.spec.kind, left, top, right, bottom, self.spec.size)
        if self._effect_cache_key != key or self._effect_cache is None:
            target_box = (left, top, right, bottom)
            sample_box = _effect_sample_box(
                (source.width(), source.height()), target_box, self.spec.kind, self.spec.size
            )
            sample_left, sample_top, sample_right, sample_bottom = sample_box
            sample = qimage_to_pil(
                source.copy(
                    sample_left,
                    sample_top,
                    sample_right - sample_left,
                    sample_bottom - sample_top,
                )
            )
            try:
                effect = _render_effect_sample(
                    sample,
                    self.spec.kind,
                    self.spec.size,
                    (left - sample_left, top - sample_top),
                    (right - left, bottom - top),
                )
                try:
                    self._effect_cache = QPixmap.fromImage(pil_to_qimage(effect))
                finally:
                    effect.close()
            finally:
                sample.close()
            self._effect_cache_key = key
        return self._effect_cache, QRectF(left, top, right - left, bottom - top)


class NavigatorOverview(QWidget):
    """Compact image overview with a draggable visible-area indicator."""

    navigateRequested = Signal(QPointF)
    positionChanged = Signal(QPointF)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navigatorOverview")
        self.setFixedSize(196, 144)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName("图片鸟瞰图")
        self.setToolTip("拖动蓝色矩形快速查看图片的其他区域")
        self._thumbnail = QPixmap()
        self._image_rect = QRectF()
        self._visible_rect = QRectF()
        self._preview_rect = QRectF()
        self._viewport_rect = QRectF()
        self._drag_offset: QPointF | None = None
        self._panel_drag_offset: QPoint | None = None
        self.hide()

    @property
    def viewport_indicator_rect(self) -> QRectF:
        return QRectF(self._viewport_rect)

    def set_source_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self._thumbnail = QPixmap()
            self._preview_rect = QRectF()
            self._viewport_rect = QRectF()
            self.hide()
            return
        self._thumbnail = pixmap.scaled(
            QSize(self.width() - 20, self.height() - 38),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_rect = QRectF(
            (self.width() - self._thumbnail.width()) / 2,
            28 + (self.height() - 36 - self._thumbnail.height()) / 2,
            self._thumbnail.width(),
            self._thumbnail.height(),
        )
        self._sync_viewport_indicator()
        self.update()

    def set_view_state(
        self,
        image_rect: QRectF,
        visible_rect: QRectF,
        *,
        visible: bool,
    ) -> None:
        self._image_rect = QRectF(image_rect)
        self._visible_rect = QRectF(visible_rect).intersected(self._image_rect)
        self._sync_viewport_indicator()
        self.setVisible(bool(visible and not self._thumbnail.isNull()))
        if self.isVisible():
            self.raise_()
            self.update()

    def _sync_viewport_indicator(self) -> None:
        if (
            self._image_rect.isEmpty()
            or self._visible_rect.isEmpty()
            or self._preview_rect.isEmpty()
        ):
            self._viewport_rect = QRectF()
            return
        image = self._image_rect
        preview = self._preview_rect
        center_x = (
            preview.left()
            + ((self._visible_rect.center().x() - image.left()) / image.width()) * preview.width()
        )
        center_y = (
            preview.top()
            + ((self._visible_rect.center().y() - image.top()) / image.height()) * preview.height()
        )
        indicator_width = max(
            8.0, min(preview.width(), self._visible_rect.width() / image.width() * preview.width())
        )
        indicator_height = max(
            8.0,
            min(preview.height(), self._visible_rect.height() / image.height() * preview.height()),
        )
        center_x = max(
            preview.left() + indicator_width / 2,
            min(preview.right() - indicator_width / 2, center_x),
        )
        center_y = max(
            preview.top() + indicator_height / 2,
            min(preview.bottom() - indicator_height / 2, center_y),
        )
        self._viewport_rect = QRectF(
            center_x - indicator_width / 2,
            center_y - indicator_height / 2,
            indicator_width,
            indicator_height,
        )

    def _request_navigation(self, indicator_center: QPointF) -> None:
        if self._preview_rect.isEmpty() or self._image_rect.isEmpty():
            return
        preview = self._preview_rect
        center_x = max(preview.left(), min(preview.right(), indicator_center.x()))
        center_y = max(preview.top(), min(preview.bottom(), indicator_center.y()))
        image_x = (
            self._image_rect.left()
            + ((center_x - preview.left()) / preview.width()) * self._image_rect.width()
        )
        image_y = (
            self._image_rect.top()
            + ((center_y - preview.top()) / preview.height()) * self._image_rect.height()
        )
        self.navigateRequested.emit(QPointF(image_x, image_y))

    def _move_panel(self, requested: QPoint) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        maximum_x = max(0, parent.width() - self.width())
        maximum_y = max(0, parent.height() - self.height())
        self.move(
            max(0, min(maximum_x, requested.x())),
            max(0, min(maximum_y, requested.y())),
        )

    def _normalized_position(self) -> QPointF:
        parent = self.parentWidget()
        if parent is None:
            return QPointF(1.0, 1.0)
        available_x = max(1, parent.width() - self.width())
        available_y = max(1, parent.height() - self.height())
        return QPointF(self.x() / available_x, self.y() / available_y)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        panel = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(QColor(PANEL_OVERLAY))
        painter.drawRoundedRect(panel, 11, 11)
        painter.setPen(QPen(QColor(MUTED), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(self.width() / 2 - 18, 13), QPointF(self.width() / 2 + 18, 13))
        if self._thumbnail.isNull() or self._preview_rect.isEmpty():
            return

        painter.save()
        painter.setClipRect(self._preview_rect)
        checker_light = QColor("#d9dde3")
        checker_dark = QColor("#aeb4bd")
        cell = 8
        preview = self._preview_rect.toAlignedRect()
        painter.fillRect(preview, checker_light)
        for y in range(preview.top(), preview.bottom() + 1, cell):
            for x in range(preview.left(), preview.right() + 1, cell):
                if ((x - preview.left()) // cell + (y - preview.top()) // cell) % 2:
                    painter.fillRect(x, y, cell, cell, checker_dark)
        painter.drawPixmap(self._preview_rect.topLeft(), self._thumbnail)
        painter.restore()

        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._preview_rect)
        if not self._viewport_rect.isEmpty():
            accent = QColor(ACCENT)
            fill = QColor(accent)
            fill.setAlpha(48)
            painter.setBrush(fill)
            painter.setPen(QPen(accent, 2))
            painter.drawRect(self._viewport_rect)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        point = event.position()
        if point.y() <= 26:
            self._panel_drag_offset = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._preview_rect.isEmpty():
            super().mousePressEvent(event)
            return
        if self._viewport_rect.contains(point):
            self._drag_offset = point - self._viewport_rect.center()
        elif self._preview_rect.contains(point):
            self._drag_offset = QPointF()
            self._request_navigation(point)
        else:
            return
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panel_drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            parent = self.parentWidget()
            if parent is not None:
                parent_point = parent.mapFromGlobal(event.globalPosition().toPoint())
                self._move_panel(parent_point - self._panel_drag_offset)
            event.accept()
            return
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._request_navigation(event.position() - self._drag_offset)
            event.accept()
            return
        self.setCursor(
            Qt.CursorShape.SizeAllCursor
            if event.position().y() <= 26
            else Qt.CursorShape.ArrowCursor
        )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._panel_drag_offset is not None:
            self._panel_drag_offset = None
            self.setCursor(
                Qt.CursorShape.SizeAllCursor
                if event.position().y() <= 26
                else Qt.CursorShape.ArrowCursor
            )
            self.positionChanged.emit(self._normalized_position())
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._request_navigation(event.position() - self._drag_offset)
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ImageView(QGraphicsView):
    zoomChanged = Signal(int)
    pointerMoved = Signal(int)
    annotationSizeSelected = Signal(int)
    annotationSelected = Signal(str, str, int)
    annotationSelectionCleared = Signal()
    documentPageChanged = Signal(int)
    documentPagesRequested = Signal(object)
    documentPagesReleased = Signal(object)
    contextMenuRequested = Signal(QPoint)
    navigatorPositionChanged = Signal(float, float)
    externalFilesDropped = Signal(object)
    progressiveTilesRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor(CANVAS))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._source_qimage: QImage | None = None
        self._progressive_source_size: tuple[int, int] | None = None
        self._progressive_preview_scale = 1.0
        self._progressive_tile_size = 1024
        self._progressive_tile_items: dict[tuple[int, int, int, int], QGraphicsPixmapItem] = {}
        self._progressive_requested_tiles: set[tuple[int, int, int, int]] = set()
        self._progressive_desired_tiles: set[tuple[int, int, int, int]] = set()
        self._document_mode = False
        self._document_page_rects: list[QRectF] = []
        self._document_page_items: dict[int, QGraphicsPixmapItem] = {}
        self._document_requested_pages: set[int] = set()
        self._document_background_items: list[QGraphicsRectItem] = []
        self._current_document_page = 0
        self._fit_mode = True
        self._annotation_tool = ""
        self._annotation_start: QPointF | None = None
        self._draft_item: MarkupItem | None = None
        self._annotations: list[AnnotationSpec] = []
        self._annotation_items: list[MarkupItem] = []
        self._annotation_history: list[list[AnnotationSpec]] = []
        self._annotation_redo: list[list[AnnotationSpec]] = []
        self._pending_annotation_history: list[AnnotationSpec] | None = None
        self._annotation_color = "#ff4c48"
        self._annotation_size = 6
        self._annotation_font_size = 20
        self._text_editor: QLineEdit | None = None
        self._text_editor_scene_point: QPointF | None = None
        self._text_editor_annotation_index: int | None = None
        self._text_editor_color = self._annotation_color
        self._text_editor_size = self._annotation_font_size
        self._drag_source_path: Path | None = None
        self._file_drag_press: QPoint | None = None
        self._drag_temp_paths: list[Path] = []
        self._transparency_background_item: QGraphicsRectItem | None = None
        self._image_halo_item: QGraphicsRectItem | None = None
        self._image_glow_items: list[QGraphicsRectItem] = []
        self._image_outline_item: QGraphicsRectItem | None = None
        self._image_edge_glow_enabled = True
        self._image_edge_glow_color = "auto"
        self._image_edge_glow_intensity = 50
        self._image_edge_glow_spread = 7
        self._transparency_background = "checkerboard"
        self._transparency_color = "#ffffff"
        self._checker_light = "#d9dde3"
        self._checker_dark = "#aeb4bd"
        self._navigator = NavigatorOverview(self.viewport())
        self._navigator.navigateRequested.connect(self._navigate_from_overview)
        self._navigator.positionChanged.connect(self._navigator_position_changed)
        self._navigator_custom_position: QPointF | None = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        self.verticalScrollBar().valueChanged.connect(self._document_viewport_changed)
        self.horizontalScrollBar().valueChanged.connect(self._update_navigator)
        self.verticalScrollBar().valueChanged.connect(self._update_navigator)

    def apply_theme(self) -> None:
        self.setBackgroundBrush(QColor(CANVAS))
        for background in self._document_background_items:
            background.setPen(QPen(QColor(BORDER), 1))
        for item, pen in zip(self._image_glow_items, self._image_glow_pens()):
            item.setPen(pen)
        if self._image_outline_item is not None:
            self._image_outline_item.setPen(self._image_outline_pen())
        self._navigator.update()
        self.viewport().update()

    @property
    def navigator_visible(self) -> bool:
        return self._navigator.isVisible()

    def _position_navigator(self) -> None:
        available_x = max(0, self.viewport().width() - self._navigator.width())
        available_y = max(0, self.viewport().height() - self._navigator.height())
        if self._navigator_custom_position is not None:
            self._navigator.move(
                round(available_x * self._navigator_custom_position.x()),
                round(available_y * self._navigator_custom_position.y()),
            )
            return

        side_height = max(150, min(330, self.viewport().height() - 150))
        side_y = max(42, (self.viewport().height() - side_height) // 2)
        default_y = min(available_y - 18, side_y + side_height + 12)
        self._navigator.move(max(0, available_x - 18), max(18, default_y))

    def set_navigator_position(self, position: tuple[float, float] | None) -> None:
        if position is None:
            self._navigator_custom_position = None
        else:
            self._navigator_custom_position = QPointF(
                max(0.0, min(1.0, float(position[0]))),
                max(0.0, min(1.0, float(position[1]))),
            )
        self._position_navigator()

    def _navigator_position_changed(self, position: QPointF) -> None:
        self._navigator_custom_position = QPointF(position)
        self.navigatorPositionChanged.emit(position.x(), position.y())

    def _navigate_from_overview(self, scene_center: QPointF) -> None:
        if self._document_mode or self._pixmap_item is None:
            return
        self.centerOn(scene_center)
        self._update_navigator()

    def _update_navigator(self, _value: int | None = None) -> None:
        self._position_navigator()
        if self._document_mode or self._pixmap_item is None:
            self._navigator.set_view_state(QRectF(), QRectF(), visible=False)
            return
        image_rect = self._pixmap_item.sceneBoundingRect()
        scale_x = abs(self.transform().m11())
        scale_y = abs(self.transform().m22())
        has_overflow = (
            image_rect.width() * scale_x > self.viewport().width() + 1
            or image_rect.height() * scale_y > self.viewport().height() + 1
        )
        visible_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        self._navigator.set_view_state(image_rect, visible_rect, visible=has_overflow)
        self._update_progressive_tiles()

    @property
    def progressive_source_size(self) -> tuple[int, int] | None:
        return self._progressive_source_size

    @property
    def loaded_progressive_tiles(self) -> tuple[tuple[int, int, int, int], ...]:
        return tuple(sorted(self._progressive_tile_items))

    def _clear_progressive_state(self) -> None:
        self._progressive_source_size = None
        self._progressive_preview_scale = 1.0
        self._progressive_tile_items.clear()
        self._progressive_requested_tiles.clear()
        self._progressive_desired_tiles.clear()

    def _update_progressive_tiles(self) -> None:
        source_size = self._progressive_source_size
        if source_size is None or self._pixmap_item is None or self._document_mode:
            return
        view_scale = max(0.01, abs(self.transform().m11()))
        if view_scale <= self._progressive_preview_scale * 1.05:
            desired: set[tuple[int, int, int, int]] = set()
        else:
            source_rect = QRectF(0, 0, source_size[0], source_size[1])
            visible = (
                self.mapToScene(self.viewport().rect()).boundingRect().intersected(source_rect)
            )
            margin = self._progressive_tile_size * 0.35
            visible = visible.adjusted(-margin, -margin, margin, margin).intersected(source_rect)
            tile = self._progressive_tile_size
            first_x = max(0, int(visible.left()) // tile)
            last_x = max(first_x, int(max(0.0, visible.right() - 1)) // tile)
            first_y = max(0, int(visible.top()) // tile)
            last_y = max(first_y, int(max(0.0, visible.bottom() - 1)) // tile)
            desired = {
                (
                    x * tile,
                    y * tile,
                    min(source_size[0], (x + 1) * tile),
                    min(source_size[1], (y + 1) * tile),
                )
                for y in range(first_y, last_y + 1)
                for x in range(first_x, last_x + 1)
            }
        self._progressive_desired_tiles = desired
        for rect in set(self._progressive_tile_items) - desired:
            item = self._progressive_tile_items.pop(rect)
            self.scene().removeItem(item)
        self._progressive_requested_tiles.intersection_update(desired)
        requests = tuple(
            sorted(desired - set(self._progressive_tile_items) - self._progressive_requested_tiles)
        )
        if requests:
            self._progressive_requested_tiles.update(requests)
            self.progressiveTilesRequested.emit(requests)

    def set_progressive_tiles(
        self, tiles: Sequence[tuple[tuple[int, int, int, int], QImage]]
    ) -> None:
        for rect, image in tiles:
            self._progressive_requested_tiles.discard(rect)
            if rect not in self._progressive_desired_tiles or image.isNull():
                continue
            old = self._progressive_tile_items.pop(rect, None)
            if old is not None:
                self.scene().removeItem(old)
            item = self.scene().addPixmap(QPixmap.fromImage(image))
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            item.setPos(rect[0], rect[1])
            item.setZValue(1)
            self._progressive_tile_items[rect] = item
        self.viewport().update()

    def progressive_tiles_failed(self, rects: Sequence[tuple[int, int, int, int]]) -> None:
        self._progressive_requested_tiles.difference_update(rects)

    def set_drag_source(self, path: str | Path | None) -> None:
        candidate = Path(path).resolve() if path is not None else None
        self._drag_source_path = (
            candidate if candidate is not None and candidate.is_file() else None
        )
        self._file_drag_press = None

    def set_transparency_background(
        self,
        mode: str,
        *,
        color: str | None = None,
        checker_light: str | None = None,
        checker_dark: str | None = None,
    ) -> None:
        self._transparency_background = (
            mode if mode in {"checkerboard", "solid"} else "checkerboard"
        )
        if color is not None and QColor(color).isValid():
            self._transparency_color = QColor(color).name()
        if checker_light is not None and QColor(checker_light).isValid():
            self._checker_light = QColor(checker_light).name()
        if checker_dark is not None and QColor(checker_dark).isValid():
            self._checker_dark = QColor(checker_dark).name()
        self._refresh_transparency_background()

    def set_image_edge_glow(self, enabled: bool) -> None:
        self._image_edge_glow_enabled = bool(enabled)
        for item in (*self._image_glow_items, self._image_outline_item):
            if item is not None:
                item.setVisible(self._image_edge_glow_enabled)
        self.viewport().update()

    def set_image_edge_glow_style(
        self,
        *,
        color: str,
        intensity: int,
        spread: int,
    ) -> None:
        candidate = str(color).strip().lower()
        self._image_edge_glow_color = (
            candidate if candidate == "auto" or QColor(candidate).isValid() else "auto"
        )
        self._image_edge_glow_intensity = max(10, min(100, int(intensity)))
        self._image_edge_glow_spread = max(2, min(20, int(spread)))
        for item, pen in zip(self._image_glow_items, self._image_glow_pens()):
            item.setPen(pen)
        if self._image_outline_item is not None:
            self._image_outline_item.setPen(self._image_outline_pen())
        self.viewport().update()

    def _transparency_brush(self) -> QBrush:
        if self._transparency_background == "solid":
            return QBrush(QColor(self._transparency_color))
        tile = QPixmap(24, 24)
        tile.fill(QColor(self._checker_light))
        painter = QPainter(tile)
        painter.fillRect(12, 0, 12, 12, QColor(self._checker_dark))
        painter.fillRect(0, 12, 12, 12, QColor(self._checker_dark))
        painter.end()
        return QBrush(tile)

    def _refresh_transparency_background(self) -> None:
        if self._transparency_background_item is not None:
            self.scene().removeItem(self._transparency_background_item)
            self._transparency_background_item = None
        if self._document_mode or self._pixmap_item is None:
            return
        self._transparency_background_item = self.scene().addRect(
            self._pixmap_item.sceneBoundingRect(),
            QPen(Qt.PenStyle.NoPen),
            self._transparency_brush(),
        )
        self._transparency_background_item.setZValue(-100)

    def _resolved_glow_color(self) -> QColor:
        if self._image_edge_glow_color != "auto":
            return QColor(self._image_edge_glow_color)
        return QColor("#b7d5eb" if _ACTIVE_THEME == "dark" else "#263849")

    def _image_outline_pen(self) -> QPen:
        if self._image_edge_glow_color == "auto":
            color = QColor("#a8b5c3" if _ACTIVE_THEME == "dark" else "#596675")
        else:
            color = self._resolved_glow_color()
        default_alpha = 180 if _ACTIVE_THEME == "dark" else 165
        color.setAlpha(min(235, round(default_alpha * self._image_edge_glow_intensity / 50)))
        pen = QPen(color, 1.0)
        pen.setCosmetic(True)
        return pen

    def _image_glow_pens(self) -> list[QPen]:
        base_alphas = (
            (3, 4, 5, 7, 9, 12, 18, 30) if _ACTIVE_THEME == "dark" else (2, 3, 4, 5, 7, 10, 15, 25)
        )
        alpha_scale = self._image_edge_glow_intensity / 50
        spread = self._image_edge_glow_spread
        widths = tuple(
            float(max(2, round(spread * factor)))
            for factor in (2.0, 1.72, 1.44, 1.16, 0.9, 0.7, 0.5, 0.3)
        )
        pens: list[QPen] = []
        for width, base_alpha in zip(widths, base_alphas):
            color = self._resolved_glow_color()
            color.setAlpha(min(180, max(1, round(base_alpha * alpha_scale))))
            pen = QPen(color, width)
            pen.setCosmetic(True)
            pens.append(pen)
        return pens

    def _refresh_image_outline(self) -> None:
        for item in self._image_glow_items:
            self.scene().removeItem(item)
        self._image_glow_items = []
        self._image_halo_item = None
        if self._image_outline_item is not None:
            self.scene().removeItem(self._image_outline_item)
            self._image_outline_item = None
        if self._document_mode or self._pixmap_item is None:
            return
        image_rect = self._pixmap_item.sceneBoundingRect()
        for pen in self._image_glow_pens():
            glow_item = self.scene().addRect(
                image_rect,
                pen,
                QBrush(Qt.BrushStyle.NoBrush),
            )
            glow_item.setZValue(-20)
            self._image_glow_items.append(glow_item)
        self._image_halo_item = self._image_glow_items[-1]
        self._image_outline_item = self.scene().addRect(
            image_rect,
            self._image_outline_pen(),
            QBrush(Qt.BrushStyle.NoBrush),
        )
        self._image_outline_item.setZValue(10)
        self.set_image_edge_glow(self._image_edge_glow_enabled)

    def _pointer_is_over_content(self, viewport_point: QPoint) -> bool:
        scene_point = self.mapToScene(viewport_point)
        if self._document_mode:
            return any(rect.contains(scene_point) for rect in self._document_page_rects)
        return bool(
            self._pixmap_item is not None
            and self._pixmap_item.sceneBoundingRect().contains(scene_point)
        )

    def _start_file_drag(self) -> None:
        path = self._drag_source_path
        preview = QPixmap()
        mime_data = QMimeData()
        if self._annotation_tool and self._source_qimage is not None:
            base = qimage_to_pil(self._source_qimage)
            try:
                composited = self.composited_pil(base)
            finally:
                base.close()
            try:
                temporary = tempfile.NamedTemporaryFile(
                    prefix="CSImage-edited-",
                    suffix=".png",
                    delete=False,
                )
                temporary.close()
                path = Path(temporary.name)
                composited.save(path, format="PNG")
                drag_image = pil_to_qimage(composited)
                mime_data.setImageData(drag_image)
                preview = QPixmap.fromImage(drag_image).scaled(
                    160,
                    120,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._drag_temp_paths.append(path)
                while len(self._drag_temp_paths) > 8:
                    retired = self._drag_temp_paths.pop(0)
                    try:
                        retired.unlink(missing_ok=True)
                    except OSError:
                        pass
            finally:
                composited.close()
        if path is None or not path.is_file():
            return
        mime_data.setUrls([QUrl.fromLocalFile(str(path))])
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        if preview.isNull() and self._pixmap_item is not None:
            preview = self._pixmap_item.pixmap().scaled(
                160,
                120,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        if not preview.isNull():
            drag.setPixmap(preview)
            drag.setHotSpot(preview.rect().center())
        try:
            drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)
        finally:
            self.setDragMode(
                QGraphicsView.DragMode.NoDrag
                if self._annotation_tool
                else QGraphicsView.DragMode.ScrollHandDrag
            )
            cursor = Qt.CursorShape.OpenHandCursor
            if self._annotation_tool == "select":
                cursor = Qt.CursorShape.ArrowCursor
            elif self._annotation_tool == "text":
                cursor = Qt.CursorShape.IBeamCursor
            elif self._annotation_tool:
                cursor = Qt.CursorShape.CrossCursor
            self.viewport().setCursor(cursor)

    @staticmethod
    def _local_drop_paths(mime_data: QMimeData) -> list[str]:
        if not mime_data.hasUrls():
            return []
        return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._local_drop_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._local_drop_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._local_drop_paths(event.mimeData())
        if paths:
            self.externalFilesDropped.emit(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        super().dropEvent(event)

    @property
    def has_image(self) -> bool:
        return self._pixmap_item is not None or bool(self._document_page_rects)

    @property
    def document_mode(self) -> bool:
        return self._document_mode

    @property
    def current_document_page(self) -> int:
        return self._current_document_page

    @property
    def loaded_document_pages(self) -> tuple[int, ...]:
        return tuple(sorted(self._document_page_items))

    @property
    def annotation_tool(self) -> str:
        return self._annotation_tool

    @property
    def annotations(self) -> tuple[AnnotationSpec, ...]:
        return tuple(self._annotations)

    @property
    def has_inline_text_editor(self) -> bool:
        return self._text_editor is not None

    def _view_reference_rect(self) -> QRectF | None:
        if self._document_mode and self._document_page_rects:
            return QRectF(self._document_page_rects[self._current_document_page])
        if self._pixmap_item is not None:
            return self._pixmap_item.sceneBoundingRect()
        return None

    def _capture_image_view_state(self) -> _ImageViewState | None:
        reference = self._view_reference_rect()
        if reference is None or reference.width() <= 0 or reference.height() <= 0:
            return None
        center = self.mapToScene(self.viewport().rect().center())
        return _ImageViewState(
            QTransform(self.transform()),
            QPointF(
                (center.x() - reference.left()) / reference.width(),
                (center.y() - reference.top()) / reference.height(),
            ),
            self._fit_mode,
        )

    def _restore_image_view_state(self, state: _ImageViewState | None) -> None:
        if state is None:
            return
        reference = self._view_reference_rect()
        if reference is None or reference.width() <= 0 or reference.height() <= 0:
            return
        self.setTransform(state.transform)
        self._fit_mode = state.fit_mode
        self.centerOn(
            reference.left() + reference.width() * state.center_ratio.x(),
            reference.top() + reference.height() * state.center_ratio.y(),
        )
        if self._document_mode:
            self._update_visible_document_pages()
        self._refresh_inline_text_editor()
        self.zoomChanged.emit(self.zoom_percent())
        self._update_navigator()

    def set_image_preserving_view(self, image: QImage) -> None:
        """Replace the current pixels without changing zoom or viewed image region."""

        state = self._capture_image_view_state()
        self.set_image(image, fit=False)
        self._restore_image_view_state(state)

    def set_image(self, image: QImage, *, fit: bool = True) -> None:
        self._cancel_inline_text_editor()
        self._pixmap_item = None
        self._transparency_background_item = None
        self._image_halo_item = None
        self._image_glow_items = []
        self._image_outline_item = None
        self.scene().clear()
        self._clear_progressive_state()
        self._document_mode = False
        self._document_page_rects.clear()
        self._document_page_items.clear()
        self._document_requested_pages.clear()
        self._document_background_items.clear()
        self._current_document_page = 0
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._source_qimage = image.copy()
        self._pixmap_item = self.scene().addPixmap(QPixmap.fromImage(image))
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._navigator.set_source_pixmap(self._pixmap_item.pixmap())
        self.scene().setSceneRect(self._pixmap_item.boundingRect())
        self._refresh_transparency_background()
        self._refresh_image_outline()
        self._annotations.clear()
        self._annotation_items.clear()
        self._annotation_history.clear()
        self._pending_annotation_history = None
        self._draft_item = None
        self._annotation_start = None
        if fit:
            self.fit_to_window()
        else:
            self.actual_size()
        self._update_navigator()

    def set_progressive_image(
        self,
        image: QImage,
        source_size: tuple[int, int],
        *,
        tile_size: int = 1024,
        fit: bool = True,
    ) -> None:
        """Display a screen preview in full-resolution scene coordinates."""

        width = max(1, int(source_size[0]))
        height = max(1, int(source_size[1]))
        self._cancel_inline_text_editor()
        self._pixmap_item = None
        self._transparency_background_item = None
        self._image_halo_item = None
        self._image_glow_items = []
        self._image_outline_item = None
        self.scene().clear()
        self._clear_progressive_state()
        self._document_mode = False
        self._document_page_rects.clear()
        self._document_page_items.clear()
        self._document_requested_pages.clear()
        self._document_background_items.clear()
        self._current_document_page = 0
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._source_qimage = None
        pixmap = QPixmap.fromImage(image)
        self._pixmap_item = self.scene().addPixmap(pixmap)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._pixmap_item.setScale(width / max(1, image.width()))
        self._navigator.set_source_pixmap(pixmap)
        self._progressive_source_size = (width, height)
        self._progressive_preview_scale = min(
            image.width() / width,
            image.height() / height,
        )
        self._progressive_tile_size = max(256, min(2048, int(tile_size)))
        self.scene().setSceneRect(QRectF(0, 0, width, height))
        self._refresh_transparency_background()
        self._refresh_image_outline()
        self._clear_annotation_state()
        if fit:
            self.fit_to_window()
        else:
            self.actual_size()
        self._update_navigator()

    def set_progressive_image_preserving_view(
        self,
        image: QImage,
        source_size: tuple[int, int],
        *,
        tile_size: int = 1024,
    ) -> None:
        state = self._capture_image_view_state()
        self.set_progressive_image(
            image,
            source_size,
            tile_size=tile_size,
            fit=False,
        )
        self._restore_image_view_state(state)

    def set_rotated_image(self, image: QImage, *, clockwise: bool = True) -> None:
        """Replace the bitmap while preserving the exact view transform.

        Rotation must not pass through either fit-to-window or actual-size,
        because both paths rebuild the transform and can change the displayed
        zoom.  The viewport center is rotated with the image so a panned view
        remains focused on the corresponding content.
        """

        if self._pixmap_item is None or self._document_mode:
            self.set_image(image, fit=False)
            return

        old_rect = self.sceneRect()
        old_center = self.mapToScene(self.viewport().rect().center())
        old_transform = self.transform()

        self._cancel_inline_text_editor()
        self._pixmap_item = None
        self._transparency_background_item = None
        self._image_halo_item = None
        self._image_glow_items = []
        self._image_outline_item = None
        self.scene().clear()
        self._clear_progressive_state()
        self._document_mode = False
        self._document_page_rects.clear()
        self._document_page_items.clear()
        self._document_requested_pages.clear()
        self._document_background_items.clear()
        self._current_document_page = 0
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._source_qimage = image.copy()
        self._pixmap_item = self.scene().addPixmap(QPixmap.fromImage(image))
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._navigator.set_source_pixmap(self._pixmap_item.pixmap())
        new_rect = self._pixmap_item.boundingRect()
        self.scene().setSceneRect(new_rect)
        self._refresh_transparency_background()
        self._refresh_image_outline()
        self._clear_annotation_state()

        relative_x = old_center.x() - old_rect.left()
        relative_y = old_center.y() - old_rect.top()
        if clockwise:
            center = QPointF(
                new_rect.left() + old_rect.height() - relative_y,
                new_rect.top() + relative_x,
            )
        else:
            center = QPointF(
                new_rect.left() + relative_y,
                new_rect.top() + old_rect.width() - relative_x,
            )
        center.setX(max(new_rect.left(), min(new_rect.right(), center.x())))
        center.setY(max(new_rect.top(), min(new_rect.bottom(), center.y())))

        self._fit_mode = False
        self.setTransform(old_transform)
        self.centerOn(center)
        self.zoomChanged.emit(self.zoom_percent())
        self._update_navigator()

    def set_document(
        self,
        page_sizes: Sequence[tuple[int, int]],
        page_images: Mapping[int, QImage] | None = None,
        *,
        current_index: int = 0,
        fit: bool = True,
    ) -> None:
        """Display PDF pages in one vertically scrolling, lazily populated scene."""

        normalized = [(max(1, int(width)), max(1, int(height))) for width, height in page_sizes]
        if not normalized:
            self.clear_image()
            return
        self._cancel_inline_text_editor()
        self._pixmap_item = None
        self._transparency_background_item = None
        self._image_halo_item = None
        self._image_glow_items = []
        self._image_outline_item = None
        self.scene().clear()
        self._clear_progressive_state()
        self._document_mode = True
        self._document_page_rects.clear()
        self._document_page_items.clear()
        self._document_requested_pages.clear()
        self._document_background_items.clear()
        self._current_document_page = max(0, min(len(normalized) - 1, int(current_index)))
        self._pixmap_item = None
        self._source_qimage = None
        self._navigator.set_source_pixmap(None)
        self._clear_annotation_state()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        margin = 32.0
        gap = 24.0
        maximum_width = float(max(width for width, _height in normalized))
        y = margin
        for width, height in normalized:
            x = margin + (maximum_width - width) / 2
            rect = QRectF(x, y, float(width), float(height))
            self._document_page_rects.append(rect)
            background = self.scene().addRect(rect, QPen(QColor(BORDER), 1), QColor("#ffffff"))
            background.setZValue(0)
            self._document_background_items.append(background)
            y += height + gap
        self.scene().setSceneRect(0, 0, maximum_width + margin * 2, y - gap + margin)

        for page_index, image in (page_images or {}).items():
            self.set_document_page(page_index, image)
        if fit:
            self.fit_to_window()
        else:
            self.actual_size()
        self.scroll_to_document_page(self._current_document_page)
        self._update_visible_document_pages()

    def set_document_preserving_view(
        self,
        page_sizes: Sequence[tuple[int, int]],
        page_images: Mapping[int, QImage] | None = None,
        *,
        current_index: int = 0,
    ) -> None:
        state = self._capture_image_view_state()
        self.set_document(
            page_sizes,
            page_images,
            current_index=current_index,
            fit=False,
        )
        self._restore_image_view_state(state)

    def set_document_page(self, page_index: int, image: QImage) -> None:
        if not self._document_mode or not 0 <= page_index < len(self._document_page_rects):
            return
        self._document_requested_pages.discard(page_index)
        old_item = self._document_page_items.pop(page_index, None)
        if old_item is not None:
            self.scene().removeItem(old_item)
        rect = self._document_page_rects[page_index]
        item = self.scene().addPixmap(QPixmap.fromImage(image))
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setPos(rect.topLeft())
        if image.width() > 0:
            item.setScale(rect.width() / image.width())
        item.setZValue(1)
        self._document_page_items[page_index] = item
        if self._pixmap_item is None:
            self._pixmap_item = item

    def clear_document_page(self, page_index: int) -> None:
        item = self._document_page_items.pop(page_index, None)
        if item is None:
            return
        self.scene().removeItem(item)
        if self._pixmap_item is item:
            self._pixmap_item = next(iter(self._document_page_items.values()), None)

    def scroll_to_document_page(self, page_index: int) -> None:
        if not self._document_mode or not 0 <= page_index < len(self._document_page_rects):
            return
        changed = page_index != self._current_document_page
        self._current_document_page = page_index
        rect = self._document_page_rects[page_index]
        scale = max(0.01, abs(self.transform().m11()))
        visible_height = self.viewport().height() / scale
        self.centerOn(rect.center().x(), rect.top() + visible_height / 2)
        if changed:
            self.documentPageChanged.emit(page_index)
        self._update_visible_document_pages()

    def _clear_annotation_state(self) -> None:
        self._annotations.clear()
        self._annotation_items.clear()
        self._annotation_history.clear()
        self._pending_annotation_history = None
        self._draft_item = None
        self._annotation_start = None

    def clear_image(self) -> None:
        self._cancel_inline_text_editor()
        self._pixmap_item = None
        self._transparency_background_item = None
        self._image_halo_item = None
        self._image_glow_items = []
        self._image_outline_item = None
        self.scene().clear()
        self._clear_progressive_state()
        self._source_qimage = None
        self._navigator.set_source_pixmap(None)
        self._document_mode = False
        self._document_page_rects.clear()
        self._document_page_items.clear()
        self._document_requested_pages.clear()
        self._document_background_items.clear()
        self._current_document_page = 0
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._clear_annotation_state()
        self.zoomChanged.emit(100)

    def fit_to_window(self) -> None:
        if self._document_mode and self._document_page_rects:
            self._fit_mode = True
            current_page = self._current_document_page
            maximum_width = max(rect.width() for rect in self._document_page_rects)
            current_page_height = self._document_page_rects[current_page].height()
            available_width = max(1.0, self.viewport().width() - 64.0)
            available_height = max(1.0, self.viewport().height() - 64.0)
            width_scale = available_width / maximum_width
            height_scale = available_height / current_page_height
            scale = max(0.01, min(8.0, width_scale, height_scale))
            self.resetTransform()
            self.scale(scale, scale)
            self.scroll_to_document_page(current_page)
            self.zoomChanged.emit(self.zoom_percent())
            self._update_navigator()
            return
        if self._pixmap_item is None:
            return
        self._fit_mode = True
        self.resetTransform()
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._refresh_inline_text_editor()
        self.zoomChanged.emit(self.zoom_percent())
        self._update_navigator()

    def actual_size(self) -> None:
        if self._document_mode and self._document_page_rects:
            self._fit_mode = False
            current_page = self._current_document_page
            self.resetTransform()
            self.scroll_to_document_page(current_page)
            self.zoomChanged.emit(100)
            self._update_navigator()
            return
        if self._pixmap_item is None:
            return
        self._fit_mode = False
        self.resetTransform()
        self.centerOn(self._pixmap_item)
        self._refresh_inline_text_editor()
        self.zoomChanged.emit(100)
        self._update_navigator()

    def zoom_percent(self) -> int:
        return max(1, min(800, round(self.transform().m11() * 100)))

    def set_zoom_percent(self, percent: int) -> None:
        if self._pixmap_item is None and not self._document_page_rects:
            return
        bounded = max(10, min(800, int(percent)))
        self._fit_mode = False
        center = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.scale(bounded / 100.0, bounded / 100.0)
        if self._document_mode:
            self.centerOn(center)
            self._update_visible_document_pages()
        self._refresh_inline_text_editor()
        self.zoomChanged.emit(bounded)
        self._update_navigator()

    def step_zoom(self, delta: int) -> None:
        self.set_zoom_percent(self.zoom_percent() + delta)

    def set_annotation_tool(self, tool: str) -> None:
        supported = {
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
        }
        if self._text_editor is not None:
            self._commit_inline_text_editor()
        self.scene().clearSelection()
        self._annotation_tool = tool if tool in supported else ""
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag
            if self._annotation_tool
            else QGraphicsView.DragMode.ScrollHandDrag
        )
        for item in self._annotation_items:
            item.set_interactive(bool(self._annotation_tool))
        cursor = Qt.CursorShape.OpenHandCursor
        if self._annotation_tool == "select":
            cursor = Qt.CursorShape.ArrowCursor
        elif self._annotation_tool == "text":
            cursor = Qt.CursorShape.IBeamCursor
        elif self._annotation_tool:
            cursor = Qt.CursorShape.CrossCursor
        self.viewport().setCursor(cursor)
        self._refresh_markup_scene_rect()

    def set_annotation_color(self, color: str) -> None:
        candidate = QColor(color)
        if candidate.isValid():
            self._annotation_color = candidate.name()
            if self._text_editor is not None:
                self._text_editor_color = self._annotation_color
                self._refresh_inline_text_editor()
                return
            selected = self._selected_markup_item()
            if selected is not None and selected.spec.color != self._annotation_color:
                self._record_annotation_history()
                selected.set_color(self._annotation_color)

    def set_annotation_size(self, size: int) -> None:
        selected = self._selected_markup_item()
        if selected is not None and self._text_editor is None:
            if selected.spec.kind == "text":
                self._annotation_font_size = max(8, min(96, int(size)))
                scale = max(0.01, abs(self.transform().m11()))
                scene_size = max(1, round(self._annotation_font_size / scale))
                if selected.spec.size != scene_size:
                    self._record_annotation_history()
                    selected.set_size(scene_size)
            else:
                self._annotation_size = max(2, min(32, int(size)))
                if selected.spec.size != self._annotation_size:
                    self._record_annotation_history()
                    selected.set_size(self._annotation_size)
            return
        if self._annotation_tool == "text" or self._text_editor is not None:
            self._annotation_font_size = max(8, min(96, int(size)))
        else:
            self._annotation_size = max(2, min(32, int(size)))
        if self._text_editor is not None:
            self._text_editor_size = self._annotation_font_size
            self._refresh_inline_text_editor()

    def add_annotation(self, spec: AnnotationSpec) -> None:
        item = MarkupItem(
            spec,
            self._source_qimage,
            changed=self._annotation_item_changed,
            change_started=self._begin_annotation_change,
        )
        item.set_interactive(bool(self._annotation_tool))
        self.scene().addItem(item)
        self._annotations.append(spec)
        self._annotation_items.append(item)
        self._refresh_markup_scene_rect()

    def undo_annotation(self) -> None:
        if not self._annotation_history:
            return
        self._annotation_redo.append(self._snapshot_annotations())
        snapshot = self._annotation_history.pop()
        self._restore_annotations(snapshot)

    def redo_annotation(self) -> None:
        if not self._annotation_redo:
            return
        self._annotation_history.append(self._snapshot_annotations())
        snapshot = self._annotation_redo.pop()
        self._restore_annotations(snapshot)

    def clear_annotations(self) -> None:
        self._cancel_inline_text_editor()
        for item in self._annotation_items:
            self.scene().removeItem(item)
        self._annotations.clear()
        self._annotation_items.clear()
        self._annotation_history.clear()
        self._annotation_redo.clear()
        self._pending_annotation_history = None
        if self._draft_item is not None:
            self.scene().removeItem(self._draft_item)
            self._draft_item = None
        self._annotation_start = None
        self._refresh_markup_scene_rect()

    def commit_pending_text(self) -> None:
        if self._text_editor is not None:
            self._commit_inline_text_editor()

    def undo_inline_text(self) -> bool:
        if self._text_editor is None:
            return False
        self._text_editor.undo()
        return True

    def redo_inline_text(self) -> bool:
        if self._text_editor is None:
            return False
        self._text_editor.redo()
        return True

    def copy_inline_text(self) -> bool:
        if self._text_editor is None or not self._text_editor.hasFocus():
            return False
        self._text_editor.copy()
        return True

    def select_all_inline_text(self) -> bool:
        if self._text_editor is None or not self._text_editor.hasFocus():
            return False
        self._text_editor.selectAll()
        return True

    def cancel_markup_interaction(self) -> bool:
        if self._text_editor is not None:
            self._cancel_inline_text_editor()
            return True
        if self._draft_item is not None:
            self.scene().removeItem(self._draft_item)
            self._draft_item = None
            self._annotation_start = None
            return True
        if self._selected_markup_item() is not None:
            self.scene().clearSelection()
            self.annotationSelectionCleared.emit()
            return True
        return False

    def select_all_annotations(self) -> None:
        if not self._annotation_items:
            return
        self.set_annotation_tool("select")
        for item in self._annotation_items:
            item.setSelected(True)
        self._annotation_item_changed(self._annotation_items[-1])

    def clear_annotation_selection(self) -> None:
        if not self.scene().selectedItems():
            return
        self.scene().clearSelection()
        self.annotationSelectionCleared.emit()

    @staticmethod
    def _annotation_visual_rect(annotation: AnnotationSpec) -> QRectF:
        rect = MarkupItem._selection_rect_for(annotation)
        if annotation.kind == "number":
            return rect
        if annotation.kind == "mosaic":
            padding = max(6.0, annotation.size * 1.5)
        elif annotation.kind == "arrow":
            padding = max(10.0, annotation.size * 4.0)
        else:
            padding = max(2.0, annotation.size / 2.0 + 2.0)
        if annotation.control is not None:
            rect = rect.united(QRectF(annotation.control.x(), annotation.control.y(), 0.01, 0.01))
        return rect.adjusted(-padding, -padding, padding, padding)

    def _markup_output_bounds(self, base_size: tuple[int, int]) -> tuple[int, int, int, int]:
        bounds = QRectF(0, 0, base_size[0], base_size[1])
        for annotation in self._annotations:
            core = MarkupItem._selection_rect_for(annotation)
            if annotation.control is not None:
                core = core.united(
                    QRectF(annotation.control.x(), annotation.control.y(), 0.01, 0.01)
                )
            if (
                core.left() < 0
                or core.top() < 0
                or core.right() > base_size[0]
                or core.bottom() > base_size[1]
            ):
                bounds = bounds.united(self._annotation_visual_rect(annotation))
        return (
            min(0, math.floor(bounds.left())),
            min(0, math.floor(bounds.top())),
            max(base_size[0], math.ceil(bounds.right())),
            max(base_size[1], math.ceil(bounds.bottom())),
        )

    def composited_pil(self, base: Image.Image) -> Image.Image:
        canvas_left, canvas_top, canvas_right, canvas_bottom = self._markup_output_bounds(base.size)
        offset_x = -canvas_left
        offset_y = -canvas_top
        output = Image.new(
            "RGBA",
            (max(1, canvas_right - canvas_left), max(1, canvas_bottom - canvas_top)),
            (0, 0, 0, 0),
        )
        source = base.convert("RGBA")
        try:
            output.alpha_composite(source, (offset_x, offset_y))
        finally:
            source.close()
        draw = ImageDraw.Draw(output)
        for annotation in self._annotations:
            start = (
                round(annotation.start.x()) + offset_x,
                round(annotation.start.y()) + offset_y,
            )
            end = (
                round(annotation.end.x()) + offset_x,
                round(annotation.end.y()) + offset_y,
            )
            left, right = sorted((start[0], end[0]))
            top, bottom = sorted((start[1], end[1]))
            box = (left, top, right, bottom)
            width = max(2, annotation.size)
            if annotation.kind == "rectangle":
                draw.rectangle(box, outline=annotation.color, width=width)
                continue
            if annotation.kind == "ellipse":
                draw.ellipse(box, outline=annotation.color, width=width)
                continue
            if annotation.kind == "freehand":
                points = annotation.points or (annotation.start, annotation.end)
                draw.line(
                    [
                        (round(point.x()) + offset_x, round(point.y()) + offset_y)
                        for point in points
                    ],
                    fill=annotation.color,
                    width=width,
                    joint="curve",
                )
                continue
            if annotation.kind == "mosaic":
                points = annotation.points or (annotation.start, annotation.end)
                shifted = [
                    (round(point.x()) + offset_x, round(point.y()) + offset_y) for point in points
                ]
                brush_width = max(12, annotation.size * 3)
                half = brush_width // 2 + 2
                stroke_box = (
                    max(0, min(point[0] for point in shifted) - half),
                    max(0, min(point[1] for point in shifted) - half),
                    min(output.width, max(point[0] for point in shifted) + half + 1),
                    min(output.height, max(point[1] for point in shifted) + half + 1),
                )
                if stroke_box[2] <= stroke_box[0] or stroke_box[3] <= stroke_box[1]:
                    continue
                sample_box = _effect_sample_box(
                    output.size,
                    stroke_box,
                    "mosaic",
                    annotation.size,
                )
                sample = output.crop(sample_box)
                try:
                    effect = _render_effect_sample(
                        sample,
                        "mosaic",
                        annotation.size,
                        (stroke_box[0] - sample_box[0], stroke_box[1] - sample_box[1]),
                        (stroke_box[2] - stroke_box[0], stroke_box[3] - stroke_box[1]),
                    )
                    mask = Image.new(
                        "L",
                        (stroke_box[2] - stroke_box[0], stroke_box[3] - stroke_box[1]),
                        0,
                    )
                    try:
                        mask_draw = ImageDraw.Draw(mask)
                        local_points = [
                            (point[0] - stroke_box[0], point[1] - stroke_box[1])
                            for point in shifted
                        ]
                        if len(local_points) == 1:
                            x, y = local_points[0]
                            radius = brush_width / 2
                            mask_draw.ellipse(
                                (x - radius, y - radius, x + radius, y + radius),
                                fill=255,
                            )
                        else:
                            mask_draw.line(
                                local_points,
                                fill=255,
                                width=brush_width,
                                joint="curve",
                            )
                            radius = brush_width / 2
                            for x, y in (local_points[0], local_points[-1]):
                                mask_draw.ellipse(
                                    (x - radius, y - radius, x + radius, y + radius),
                                    fill=255,
                                )
                        output.paste(effect, (stroke_box[0], stroke_box[1]), mask)
                    finally:
                        mask.close()
                        effect.close()
                finally:
                    sample.close()
                continue
            if annotation.kind == "text":
                font = _markup_font(max(1, annotation.size))
                draw.multiline_text(
                    start,
                    annotation.text or "文字",
                    fill=annotation.color,
                    font=font,
                    spacing=max(1, annotation.size),
                    stroke_width=max(0, round(annotation.size / 24)),
                    stroke_fill="#101214",
                )
                continue
            if annotation.kind == "number":
                radius = max(15, annotation.size * 3)
                circle = (
                    start[0] - radius,
                    start[1] - radius,
                    start[0] + radius,
                    start[1] + radius,
                )
                draw.ellipse(circle, fill=annotation.color)
                font = _markup_font(max(12, annotation.size * 3))
                label = str(annotation.number)
                label_box = draw.textbbox((0, 0), label, font=font)
                label_width = label_box[2] - label_box[0]
                label_height = label_box[3] - label_box[1]
                draw.text(
                    (start[0] - label_width / 2, start[1] - label_height / 2 - label_box[1]),
                    label,
                    fill="#ffffff",
                    font=font,
                )
                continue
            if annotation.kind == "redact":
                draw.rectangle(box, fill="#111315")
                continue
            if annotation.kind == "blur" and right > left and bottom > top:
                sample_box = _effect_sample_box(output.size, box, annotation.kind, annotation.size)
                sample = output.crop(sample_box)
                sample_left, sample_top, _, _ = sample_box
                try:
                    effect = _render_effect_sample(
                        sample,
                        annotation.kind,
                        annotation.size,
                        (left - sample_left, top - sample_top),
                        (right - left, bottom - top),
                    )
                    try:
                        output.paste(effect, (left, top))
                    finally:
                        effect.close()
                finally:
                    sample.close()
                continue
            control = annotation.control or QPointF(
                (annotation.start.x() + annotation.end.x()) / 2,
                (annotation.start.y() + annotation.end.y()) / 2,
            )
            distance = math.hypot(
                annotation.end.x() - annotation.start.x(),
                annotation.end.y() - annotation.start.y(),
            )
            segments = max(24, min(160, round(distance / 4)))
            curve: list[tuple[float, float]] = []
            for step in range(segments + 1):
                t = step / segments
                inverse = 1.0 - t
                curve.append(
                    (
                        inverse * inverse * annotation.start.x()
                        + 2 * inverse * t * control.x()
                        + t * t * annotation.end.x()
                        + offset_x,
                        inverse * inverse * annotation.start.y()
                        + 2 * inverse * t * control.y()
                        + t * t * annotation.end.y()
                        + offset_y,
                    )
                )
            draw.line(curve, fill=annotation.color, width=width, joint="curve")
            if annotation.kind == "line":
                continue
            tangent_x = annotation.end.x() - control.x()
            tangent_y = annotation.end.y() - control.y()
            if math.hypot(tangent_x, tangent_y) < 0.01:
                tangent_x = annotation.end.x() - annotation.start.x()
                tangent_y = annotation.end.y() - annotation.start.y()
            angle = math.atan2(tangent_y, tangent_x)
            length = max(16, annotation.size * 4)
            wing = math.radians(28)
            arrow_left = (
                end[0] - math.cos(angle - wing) * length,
                end[1] - math.sin(angle - wing) * length,
            )
            arrow_right = (
                end[0] - math.cos(angle + wing) * length,
                end[1] - math.sin(angle + wing) * length,
            )
            draw.polygon([end, arrow_left, arrow_right], fill=annotation.color)
        return output

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fit_mode and self._document_mode and self._document_page_rects:
            self.fit_to_window()
        elif self._fit_mode and self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._refresh_inline_text_editor()
            self.zoomChanged.emit(self.zoom_percent())
        if self._document_mode:
            self._update_visible_document_pages()
        self._update_navigator()

    def closeEvent(self, event: QCloseEvent) -> None:
        for path in self._drag_temp_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._drag_temp_paths.clear()
        super().closeEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._document_mode:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                direction = 1 if event.angleDelta().y() > 0 else -1
                self.step_zoom(direction * 10)
                event.accept()
                return
            super().wheelEvent(event)
            self._update_visible_document_pages()
            return
        if self._pixmap_item is None:
            return
        direction = 1 if event.angleDelta().y() > 0 else -1
        self.step_zoom(direction * 10)
        event.accept()

    def _document_viewport_changed(self, _value: int) -> None:
        if self._document_mode:
            self._update_visible_document_pages()

    def _update_visible_document_pages(self) -> None:
        if not self._document_mode or not self._document_page_rects:
            return
        viewport_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        if viewport_rect.height() <= 0:
            desired = {self._current_document_page}
        else:
            center_y = viewport_rect.center().y()
            page_index = min(
                range(len(self._document_page_rects)),
                key=lambda index: abs(self._document_page_rects[index].center().y() - center_y),
            )
            if page_index != self._current_document_page:
                self._current_document_page = page_index
                self.documentPageChanged.emit(page_index)
            preload_distance = viewport_rect.height() * 0.25
            preload_rect = viewport_rect.adjusted(
                0,
                -preload_distance,
                0,
                preload_distance,
            )
            desired = {
                index
                for index, rect in enumerate(self._document_page_rects)
                if rect.intersects(preload_rect)
            }
            desired.add(self._current_document_page)

        loaded = set(self._document_page_items)
        releases = tuple(sorted((loaded | self._document_requested_pages) - desired))
        for page_index in releases:
            self.clear_document_page(page_index)
        self._document_requested_pages.difference_update(releases)
        if releases:
            self.documentPagesReleased.emit(releases)
        requests = tuple(sorted(desired - loaded - self._document_requested_pages))
        if requests:
            self._document_requested_pages.update(requests)
            self.documentPagesRequested.emit(requests)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.pointerMoved.emit(round(event.position().y()))
        if self._annotation_tool and self._draft_item is not None:
            point = self._bounded_scene_point(event.position().toPoint())
            if self._annotation_tool in {"freehand", "mosaic"}:
                self._draft_item.add_point(point)
            else:
                self._draft_item.set_end(point)
            self._refresh_markup_scene_rect()
            event.accept()
            return
        if (
            self._file_drag_press is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._file_drag_press).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            has_overflow = (
                self.horizontalScrollBar().maximum() > 0 or self.verticalScrollBar().maximum() > 0
            )
            outside_viewport = not self.viewport().rect().contains(event.position().toPoint())
            force_copy = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            markup_drag = self._annotation_tool == "select"
            if (markup_drag and (outside_viewport or force_copy)) or (
                not markup_drag and (not has_overflow or outside_viewport or force_copy)
            ):
                self._file_drag_press = None
                self._start_file_drag()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if self.has_image and self._pointer_is_over_content(event.pos()):
            self.contextMenuRequested.emit(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._file_drag_press = None
        if self._text_editor is not None and event.button() == Qt.MouseButton.LeftButton:
            self._commit_inline_text_editor()
        if (
            self._annotation_tool
            and self._pixmap_item is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            point = self._bounded_scene_point(event.position().toPoint())
            target = self._markup_item_at(point)
            if target is not None:
                self.scene().clearSelection()
                target.setSelected(True)
                self._annotation_item_changed(target)
                super().mousePressEvent(event)
                return
            if self._annotation_tool == "select":
                self.scene().clearSelection()
                self.annotationSelectionCleared.emit()
                if self._pointer_is_over_content(event.position().toPoint()):
                    self._file_drag_press = event.position().toPoint()
                event.accept()
                return
            self.scene().clearSelection()
            if self._annotation_tool == "text":
                self._annotation_start = point
                self._text_editor_annotation_index = self._text_annotation_at(point)
                if self._text_editor_annotation_index is not None:
                    self._annotation_start = self._annotations[
                        self._text_editor_annotation_index
                    ].start
                event.accept()
                return
            self._annotation_start = point
            number = 1 + sum(spec.kind == "number" for spec in self._annotations)
            spec = AnnotationSpec(
                self._annotation_tool,
                point,
                point,
                color=self._annotation_color,
                size=self._annotation_size,
                number=number,
                points=(point,) if self._annotation_tool in {"freehand", "mosaic"} else (),
            )
            self._draft_item = MarkupItem(
                spec,
                self._source_qimage,
                draft=True,
                changed=self._annotation_item_changed,
                change_started=self._begin_annotation_change,
            )
            self.scene().addItem(self._draft_item)
            self._refresh_markup_scene_rect()
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._annotation_tool
            and self._drag_source_path is not None
            and self._pointer_is_over_content(event.position().toPoint())
        ):
            self._file_drag_press = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._file_drag_press = None
        if (
            self._annotation_tool == "text"
            and self._annotation_start is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            point = self._annotation_start
            index = self._text_editor_annotation_index
            self._annotation_start = None
            self._begin_inline_text_editor(point, index)
            event.accept()
            return
        if self._draft_item is not None and event.button() == Qt.MouseButton.LeftButton:
            spec = self._draft_item.spec
            point_tool = spec.kind == "number"
            if (
                point_tool
                and abs(spec.end.x() - spec.start.x()) + abs(spec.end.y() - spec.start.y()) < 4
            ):
                self._draft_item.set_end(
                    QPointF(
                        spec.start.x() + max(120, spec.size * 24),
                        spec.start.y() + max(42, spec.size * 7),
                    )
                )
            if (
                point_tool
                or abs(spec.end.x() - spec.start.x()) + abs(spec.end.y() - spec.start.y()) >= 4
            ):
                self._record_annotation_history()
                self.scene().clearSelection()
                self._draft_item.finish_draft()
                self._annotations.append(spec)
                self._annotation_items.append(self._draft_item)
            else:
                self.scene().removeItem(self._draft_item)
            self._draft_item = None
            self._annotation_start = None
            self._refresh_markup_scene_rect()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._pixmap_item is not None and event.button() == Qt.MouseButton.LeftButton:
            point = self._bounded_scene_point(event.position().toPoint())
            target = self._markup_item_at(point)
            if target is not None and target.spec.kind == "text":
                try:
                    index = self._annotation_items.index(target)
                except ValueError:
                    index = None
                if index is not None:
                    self._begin_inline_text_editor(target.spec.start, index)
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        selected = self._selected_markup_item()
        if selected is not None and event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            try:
                index = self._annotation_items.index(selected)
            except ValueError:
                index = None
            if index is not None:
                self._record_annotation_history()
                self.scene().removeItem(selected)
                self._annotation_items.pop(index)
                self._annotations.pop(index)
                self.annotationSelectionCleared.emit()
                event.accept()
                return
        if selected is not None and event.key() == Qt.Key.Key_Escape:
            self.scene().clearSelection()
            self.annotationSelectionCleared.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._text_editor and isinstance(event, QKeyEvent):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._cancel_inline_text_editor()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _text_annotation_at(self, point: QPointF) -> int | None:
        for index in range(len(self._annotation_items) - 1, -1, -1):
            spec = self._annotations[index]
            item = self._annotation_items[index]
            if spec.kind == "text" and item.boundingRect().contains(point):
                return index
        return None

    def _markup_item_at(self, point: QPointF) -> MarkupItem | None:
        for candidate in self.scene().items(point):
            if (
                isinstance(candidate, MarkupItem)
                and candidate is not self._draft_item
                and candidate.spec.kind != "mosaic"
            ):
                return candidate
        return None

    def _selected_markup_item(self) -> MarkupItem | None:
        for candidate in self.scene().selectedItems():
            if isinstance(candidate, MarkupItem):
                return candidate
        return None

    def _select_annotation_item(self, item: MarkupItem) -> None:
        self.scene().clearSelection()
        item.setSelected(True)
        self._annotation_item_changed(item)

    def _annotation_item_changed(self, item: MarkupItem) -> None:
        self._commit_pending_annotation_history()
        self._refresh_markup_scene_rect()
        if not item.isSelected():
            return
        size = item.spec.size
        if item.spec.kind == "text":
            scale = max(0.01, abs(self.transform().m11()))
            size = max(8, min(96, round(size * scale)))
            self._annotation_font_size = size
        else:
            size = max(2, min(32, size))
            self._annotation_size = size
        self._annotation_color = item.spec.color
        self.annotationSelected.emit(item.spec.kind, item.spec.color, size)

    def _snapshot_annotations(self) -> list[AnnotationSpec]:
        return [_clone_annotation_spec(spec) for spec in self._annotations]

    @staticmethod
    def _snapshot_signature(snapshot: list[AnnotationSpec]) -> tuple[tuple[object, ...], ...]:
        return tuple(_annotation_signature(spec) for spec in snapshot)

    def _record_annotation_history(self) -> None:
        self._pending_annotation_history = None
        self._annotation_redo.clear()
        snapshot = self._snapshot_annotations()
        if self._annotation_history and self._snapshot_signature(
            self._annotation_history[-1]
        ) == self._snapshot_signature(snapshot):
            return
        self._annotation_history.append(snapshot)

    def _begin_annotation_change(self) -> None:
        self._pending_annotation_history = self._snapshot_annotations()

    def _commit_pending_annotation_history(self) -> None:
        pending = self._pending_annotation_history
        self._pending_annotation_history = None
        if pending is None:
            return
        if self._snapshot_signature(pending) == self._snapshot_signature(
            self._snapshot_annotations()
        ):
            return
        if self._annotation_history and self._snapshot_signature(
            self._annotation_history[-1]
        ) == self._snapshot_signature(pending):
            return
        self._annotation_redo.clear()
        self._annotation_history.append(pending)

    def _restore_annotations(self, snapshot: list[AnnotationSpec]) -> None:
        self._cancel_inline_text_editor()
        for item in self._annotation_items:
            self.scene().removeItem(item)
        self._annotations.clear()
        self._annotation_items.clear()
        self._pending_annotation_history = None
        for spec in snapshot:
            self.add_annotation(_clone_annotation_spec(spec))
        self.scene().clearSelection()
        self.annotationSelectionCleared.emit()
        self._refresh_markup_scene_rect()

    def _begin_inline_text_editor(self, point: QPointF, index: int | None) -> None:
        self._cancel_inline_text_editor()
        initial_text = ""
        color = self._annotation_color
        size = self._annotation_font_size
        if index is not None:
            spec = self._annotations[index]
            initial_text = spec.text
            color = spec.color
            scale = max(0.01, abs(self.transform().m11()))
            size = max(8, min(96, round(spec.size * scale)))
            self._annotation_font_size = size
            self._annotation_items[index].setVisible(False)
            self.annotationSizeSelected.emit(size)

        editor = QLineEdit(self.viewport())
        editor.setAccessibleName("图片上的文字标注输入框")
        editor.setPlaceholderText("输入文字，Enter 确认，Esc 取消")
        editor.setText(initial_text)
        editor.installEventFilter(self)
        editor.returnPressed.connect(self._commit_inline_text_editor)
        editor.textChanged.connect(self._refresh_inline_text_editor)

        self._text_editor = editor
        self._text_editor_scene_point = point
        self._text_editor_annotation_index = index
        self._text_editor_color = color
        self._text_editor_size = size
        self._refresh_inline_text_editor()
        editor.show()
        editor.raise_()
        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        if initial_text:
            editor.selectAll()

    def _refresh_inline_text_editor(self) -> None:
        editor = self._text_editor
        point = self._text_editor_scene_point
        if editor is None or point is None:
            return
        color = self._text_editor_color
        size = self._text_editor_size
        editor.setStyleSheet(
            f"QLineEdit {{color: {color}; background: rgba(18, 21, 25, 178);"
            f"border: 1px solid {color}; border-radius: 4px; padding: 2px 5px;}}"
        )
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(max(8, size))
        font.setWeight(QFont.Weight.DemiBold)
        editor.setFont(font)
        metrics = editor.fontMetrics()
        desired_width = max(
            104,
            min(480, metrics.horizontalAdvance(editor.text() or "输入文字") + 24),
        )
        desired_height = max(30, metrics.height() + 8)
        viewport_point = self.mapFromScene(point)
        x = max(4, min(self.viewport().width() - desired_width - 4, viewport_point.x()))
        y = max(4, min(self.viewport().height() - desired_height - 4, viewport_point.y()))
        editor.setGeometry(x, y, desired_width, desired_height)

    def _commit_inline_text_editor(self) -> None:
        editor = self._text_editor
        point = self._text_editor_scene_point
        if editor is None or point is None:
            return
        text = editor.text().strip()
        index = self._text_editor_annotation_index
        if not text:
            self._cancel_inline_text_editor()
            return

        scale = max(0.01, abs(self.transform().m11()))
        metrics = editor.fontMetrics()
        scene_width = (metrics.horizontalAdvance(text) + 12) / scale
        scene_height = (metrics.height() + 8) / scale
        end = QPointF(
            point.x() + scene_width,
            point.y() + scene_height,
        )
        color = self._text_editor_color
        view_size = self._text_editor_size
        scene_size = max(1, round(view_size / scale))
        self._remove_inline_text_editor()
        if index is None:
            self._record_annotation_history()
            self.add_annotation(
                AnnotationSpec("text", point, end, color=color, size=scene_size, text=text)
            )
            return

        spec = self._annotations[index]
        item = self._annotation_items[index]
        if (
            spec.start != point
            or spec.end != end
            or spec.text != text
            or spec.color != color
            or spec.size != scene_size
        ):
            self._record_annotation_history()
        item.prepareGeometryChange()
        spec.start = point
        spec.end = end
        spec.text = text
        spec.color = color
        spec.size = scene_size
        item.setVisible(True)
        item.update()
        self._refresh_markup_scene_rect()

    def _cancel_inline_text_editor(self) -> None:
        index = self._text_editor_annotation_index
        self._remove_inline_text_editor()
        if index is not None and 0 <= index < len(self._annotation_items):
            self._annotation_items[index].setVisible(True)

    def _remove_inline_text_editor(self) -> None:
        editor = self._text_editor
        self._text_editor = None
        self._text_editor_scene_point = None
        self._text_editor_annotation_index = None
        if editor is not None:
            editor.removeEventFilter(self)
            editor.hide()
            editor.deleteLater()

    def _refresh_markup_scene_rect(self) -> None:
        if self._pixmap_item is None or self._document_mode:
            return
        image_rect = self._pixmap_item.sceneBoundingRect()
        if not self._annotation_tool:
            self.scene().setSceneRect(image_rect)
            return
        content_rect = QRectF(image_rect)
        for item in self._annotation_items:
            content_rect = content_rect.united(item.sceneBoundingRect())
        if self._draft_item is not None:
            content_rect = content_rect.united(self._draft_item.sceneBoundingRect())
        workspace = max(96.0, min(image_rect.width(), image_rect.height()) * 0.12)
        self.scene().setSceneRect(
            content_rect.adjusted(-workspace, -workspace, workspace, workspace)
        )

    def _bounded_scene_point(self, viewport_point: QPoint) -> QPointF:
        point = self.mapToScene(viewport_point)
        if self._pixmap_item is None:
            return point
        rect = self._pixmap_item.sceneBoundingRect()
        safety_margin = max(2048.0, max(rect.width(), rect.height()) * 4.0)
        return QPointF(
            max(rect.left() - safety_margin, min(rect.right() + safety_margin, point.x())),
            max(rect.top() - safety_margin, min(rect.bottom() + safety_margin, point.y())),
        )


def _markup_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


class NavigationButton(QPushButton):
    def __init__(self, direction: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.direction = direction
        label = "上一张图片" if direction < 0 else "下一张图片"
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{background: transparent; border: 0;}} QToolTip {{color: {TEXT};}}"
        )
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse() and self.isEnabled()
        if hovered:
            painter.setBrush(QColor(HOVER))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(self.rect().adjusted(10, 18, -10, -18), 24, 24)
        pen = QPen(QColor(TEXT if hovered else ACCENT), 3.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        cx, cy = self.rect().center().x(), self.rect().center().y()
        offset = 8 if self.direction > 0 else -8
        painter.drawLine(cx - offset // 2, cy - 12, cx + offset // 2, cy)
        painter.drawLine(cx + offset // 2, cy, cx - offset // 2, cy + 12)
        painter.end()

    def enterEvent(self, event: QEnterEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class ViewerToolbar(QFrame):
    hoverChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("viewerToolbar")
        self.apply_theme()

        self.button_layout = QHBoxLayout(self)
        self.button_layout.setContentsMargins(8, 4, 8, 4)
        self.button_layout.setSpacing(2)
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_animation = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self.opacity_animation.setDuration(180)
        self.opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            #viewerToolbar {{background: {SURFACE_HIGH}; border: 1px solid {BORDER};
                            border-radius: 18px;}}
            QLineEdit {{background: transparent; color: {TEXT}; border: 0; padding: 0 4px;
                        font: 600 13px 'Microsoft YaHei UI';
                        selection-background-color: {SELECTED};}}
            QFrame#separator {{background: {BORDER}; border: 0;}}
            """
        )

    def add_button(self, button: QWidget) -> QWidget:
        self.button_layout.addWidget(button)
        return button

    def add_separator(self) -> None:
        separator = QFrame(self)
        separator.setObjectName("separator")
        separator.setFixedSize(1, 24)
        self.button_layout.addWidget(separator)

    def enterEvent(self, event: QEnterEvent) -> None:
        self.hoverChanged.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.hoverChanged.emit(False)
        super().leaveEvent(event)

    def animate_visible(self, visible: bool) -> None:
        self.opacity_animation.stop()
        start = float(self.opacity_effect.opacity())
        self.opacity_animation.setStartValue(start)
        self.opacity_animation.setEndValue(1.0 if visible else 0.0)
        if visible:
            self.show()
        else:
            self.opacity_animation.finished.connect(self._hide_after_animation)
        self.opacity_animation.start()

    def _hide_after_animation(self) -> None:
        try:
            self.opacity_animation.finished.disconnect(self._hide_after_animation)
        except RuntimeError:
            pass
        if self.opacity_effect.opacity() <= 0.01:
            self.hide()


class FilmstripPanel(QFrame):
    pathSelected = Signal(str)
    scopeChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("filmstrip")
        self.apply_theme()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 8)
        layout.setSpacing(4)
        scope_layout = QHBoxLayout()
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.setSpacing(4)
        self.folder_button = QPushButton("当前文件夹", self)
        self.favorite_button = QPushButton("我的收藏", self)
        for button in (self.folder_button, self.favorite_button):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scope_group = QButtonGroup(self)
        self.scope_group.setExclusive(True)
        self.scope_group.addButton(self.folder_button)
        self.scope_group.addButton(self.favorite_button)
        self.folder_button.setChecked(True)
        self.folder_button.clicked.connect(lambda: self.scopeChanged.emit("folder"))
        self.favorite_button.clicked.connect(lambda: self.scopeChanged.emit("favorites"))
        scope_layout.addStretch(1)
        scope_layout.addWidget(self.folder_button)
        scope_layout.addWidget(self.favorite_button)
        scope_layout.addStretch(1)
        layout.addLayout(scope_layout)
        self.list = QListWidget(self)
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setFlow(QListWidget.Flow.LeftToRight)
        self.list.setWrapping(False)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setIconSize(QSize(74, 54))
        self.list.setGridSize(QSize(86, 62))
        self.list.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._item_clicked)
        layout.addWidget(self.list)

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            #filmstrip {{background: {PANEL_OVERLAY}; border: 1px solid {BORDER};
                        border-radius: 14px;}}
            QPushButton {{color: {MUTED}; background: transparent; border: 0; padding: 5px 12px;
                          border-radius: 9px; font: 12px 'Microsoft YaHei UI';}}
            QPushButton:checked {{color: {TEXT}; background: {SELECTED};}}
            QListWidget {{background: transparent; border: 0; outline: 0;}}
            QListWidget::item {{background: transparent; border: 0; padding: 3px;}}
            QListWidget::item:selected {{background: transparent; border: 0;}}
            """
        )

    def set_scope(self, scope: str) -> None:
        self.folder_button.setChecked(scope == "folder")
        self.favorite_button.setChecked(scope == "favorites")

    def set_paths(self, paths: list[Path], current: Path | None) -> None:
        self.list.clear()
        current_key = str(current.resolve()).casefold() if current is not None else ""
        for path in paths:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(path.name)
            item.setSizeHint(QSize(86, 62))
            self.list.addItem(item)
            try:
                key = str(path.resolve()).casefold()
            except OSError:
                key = str(path.absolute()).casefold()
            if key == current_key:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is not None:
            self.list.scrollToItem(
                self.list.currentItem(), QAbstractItemView.ScrollHint.PositionAtCenter
            )

    def item_for_path(self, path: Path) -> QListWidgetItem | None:
        key = str(path.absolute()).casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            if str(Path(str(item.data(Qt.ItemDataRole.UserRole))).absolute()).casefold() == key:
                return item
        return None

    def set_thumbnail(self, path: Path, image: QImage) -> None:
        item = self.item_for_path(path)
        if item is not None:
            item.setIcon(QIcon(QPixmap.fromImage(image)))

    def _item_clicked(self, item: QListWidgetItem) -> None:
        self.pathSelected.emit(str(item.data(Qt.ItemDataRole.UserRole)))


class MarkupToolbar(QFrame):
    toolChanged = Signal(str)
    colorChanged = Signal(str)
    sizeChanged = Signal(int)
    undoRequested = Signal()
    cancelRequested = Signal()
    copyRequested = Signal()
    replaceRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("markupToolbar")
        self._active_tool = "rectangle"
        self._last_drawing_tool = "rectangle"
        self._property_kind = "rectangle"
        self._shape_size = 6
        self._font_size = 20
        self._effect_sizes = {"mosaic": 12, "blur": 16}
        self.apply_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(0)
        tools_layout = QHBoxLayout()
        tools_layout.setSpacing(2)
        self.tool_buttons: dict[str, QPushButton] = {}
        tools = (
            ("select", "选择"),
            ("rectangle", "矩形"),
            ("arrow", "箭头"),
            ("line", "直线"),
            ("ellipse", "椭圆"),
            ("freehand", "画笔"),
            ("text", "文字"),
            ("number", "序号"),
            ("mosaic", "马赛克"),
            ("blur", "模糊"),
            ("redact", "遮挡"),
        )
        for tool, label in tools:
            button = QPushButton(label, self)
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked, value=tool: self._tool_button_clicked(value, checked)
            )
            if tool == "select":
                button.setToolTip("智能选择：再次点击可返回上一个绘图工具")
            tools_layout.addWidget(button)
            self.tool_buttons[tool] = button
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        for button in self.tool_buttons.values():
            self.tool_group.addButton(button)
        self.rect_button = self.tool_buttons["rectangle"]
        self.arrow_button = self.tool_buttons["arrow"]

        action_layout = QHBoxLayout()
        action_layout.setSpacing(2)
        self.color_button = QPushButton("颜色", self)
        self.color_button.setToolTip("选择常用颜色或打开自定义颜色")
        self.color_menu = QMenu(self.color_button)
        self._build_color_menu()
        self.color_button.setMenu(self.color_menu)
        action_layout.addWidget(self.color_button)
        self.size_spin = QSpinBox(self)
        self.size_spin.setRange(2, 32)
        self.size_spin.setValue(6)
        self.size_spin.setPrefix("大小 ")
        self.size_spin.setToolTip("调整线宽、字号或效果强度")
        self.size_spin.valueChanged.connect(self._size_changed)
        self.size_spin.hide()
        self.size_preset_button = QPushButton("预设", self)
        self.size_preset_button.setToolTip("选择常用线宽、字号或效果强度")
        self.size_preset_menu = QMenu(self.size_preset_button)
        self.size_preset_button.setMenu(self.size_preset_menu)
        self._refresh_size_preset_menu()
        self.size_preset_button.hide()
        self.size_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.size_slider.setRange(2, 32)
        self.size_slider.setValue(6)
        self.size_slider.setFixedWidth(112)
        self.size_slider.setToolTip("拖动调整当前工具大小；文字工具对应字号")
        self.size_slider.valueChanged.connect(self.size_spin.setValue)
        action_layout.addWidget(self.size_slider)
        self.size_value_label = QLabel("6", self)
        self.size_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.size_value_label.setFixedWidth(58)
        action_layout.addWidget(self.size_value_label)
        for text, signal in (
            ("撤销", self.undoRequested),
            ("取消修改", self.cancelRequested),
            ("仅复制", self.copyRequested),
            ("另存为", self.replaceRequested),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(signal.emit)
            action_layout.addWidget(button)
            if text == "另存为":
                self.replace_button = button
            elif text == "仅复制":
                self.copy_button = button
        tools_layout.addSpacing(4)
        tools_layout.addLayout(action_layout)
        layout.addLayout(tools_layout)

        self._color = QColor("#ff4c48")
        self._update_color_button()

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            #markupToolbar {{background: {SURFACE_HIGH}; border: 1px solid {BORDER};
                            border-radius: 15px;}}
            QPushButton {{color: {TEXT}; background: transparent; border: 0; border-radius: 9px;
                          padding: 5px 8px; font: 12px 'Microsoft YaHei UI';}}
            QPushButton:hover {{background: {HOVER};}}
            QPushButton:checked {{background: {SELECTED}; color: {TEXT};}}
            QLineEdit, QSpinBox {{color: {TEXT}; background: {SURFACE};
                                  border: 1px solid {BORDER}; border-radius: 8px;
                                  padding: 5px 8px; font: 12px 'Microsoft YaHei UI';}}
            QMenu {{color: {TEXT}; background: {SURFACE_HIGH}; border: 1px solid {BORDER};
                    padding: 5px; font: 12px 'Microsoft YaHei UI';}}
            QMenu::item {{padding: 6px 22px 6px 8px; border-radius: 6px;}}
            QMenu::item:selected {{background: {SELECTED};}}
            QSlider::groove:horizontal {{height: 4px; background: {BORDER};
                                         border-radius: 2px;}}
            QSlider::sub-page:horizontal {{background: {ACCENT}; border-radius: 2px;}}
            QSlider::handle:horizontal {{width: 14px; margin: -5px 0;
                                         background: {TEXT}; border: 2px solid {ACCENT};
                                         border-radius: 7px;}}
            QLabel {{color: {MUTED}; font: 11px 'Microsoft YaHei UI';}}
            """
        )

    def set_read_only_source(self, read_only: bool) -> None:
        """All formats save edits to a new file; source pixels are never overwritten."""

        self.replace_button.setText("另存为")
        self.replace_button.setToolTip(
            "源格式只读，将编辑结果导出为普通图片"
            if read_only
            else "将编辑结果保存为新图片，不修改原文件"
        )
        self.replace_button.setAccessibleName("另存编辑结果")

    def _tool_button_clicked(self, tool: str, checked: bool) -> None:
        if not checked:
            return
        if tool == "select" and self._active_tool == "select":
            self._select_tool(self._last_drawing_tool)
            return
        self._select_tool(tool)

    def _build_color_menu(self) -> None:
        self.color_menu.clear()
        for label, value in COLOR_PRESETS:
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(value))
            action = self.color_menu.addAction(QIcon(swatch), label)
            action.setData(value)
            action.triggered.connect(lambda _checked=False, color=value: self._apply_color(color))
        self.color_menu.addSeparator()
        self.color_menu.addAction("自定义颜色…", self._choose_color)

    def _size_presets(self) -> tuple[int, ...]:
        if self._property_kind == "text":
            return TEXT_SIZE_PRESETS
        if self._property_kind in EFFECT_KINDS:
            return EFFECT_SIZE_PRESETS
        return SHAPE_SIZE_PRESETS

    def _refresh_size_preset_menu(self) -> None:
        self.size_preset_menu.clear()
        current = self.size_spin.value()
        suffix = " px" if self._property_kind == "text" else ""
        for value in self._size_presets():
            action = self.size_preset_menu.addAction(f"{value}{suffix}")
            action.setData(value)
            action.setCheckable(True)
            action.setChecked(value == current)
            action.triggered.connect(lambda _checked=False, size=value: self.set_size(size))

    def _sync_size_preset_checks(self, current: int) -> None:
        for action in self.size_preset_menu.actions():
            value = action.data()
            if isinstance(value, int):
                action.setChecked(value == current)

    def _select_tool(self, tool: str) -> None:
        button = self.tool_buttons.get(tool)
        if button is None:
            return
        if self._property_kind == "text":
            self._font_size = self.size_spin.value()
        elif self._property_kind in EFFECT_KINDS:
            self._effect_sizes[self._property_kind] = self.size_spin.value()
        else:
            self._shape_size = self.size_spin.value()
        self._active_tool = tool
        if tool != "select":
            self._last_drawing_tool = tool
            self._property_kind = tool
            self.tool_buttons["select"].setToolTip(f"智能选择：再次点击返回“{button.text()}”")
        button.setChecked(True)
        self.size_spin.blockSignals(True)
        if self._property_kind == "text":
            self.size_spin.setRange(8, 96)
            self.size_spin.setPrefix("字号 ")
            self.size_spin.setSuffix(" px")
            self.size_spin.setValue(self._font_size)
        elif self._property_kind in EFFECT_KINDS:
            self.size_spin.setRange(2, 32)
            self.size_spin.setPrefix(
                "画笔大小 " if self._property_kind == "mosaic" else "模糊强度 "
            )
            self.size_spin.setSuffix("")
            self.size_spin.setValue(self._effect_sizes[self._property_kind])
        else:
            self.size_spin.setRange(2, 32)
            self.size_spin.setPrefix("大小 ")
            self.size_spin.setSuffix("")
            self.size_spin.setValue(self._shape_size)
        self.size_spin.blockSignals(False)
        self._refresh_size_preset_menu()
        self._sync_size_slider()
        controls_enabled = tool != "select"
        self.color_button.setEnabled(controls_enabled and self._property_kind not in EFFECT_KINDS)
        self.size_spin.setEnabled(controls_enabled)
        self.size_preset_button.setEnabled(controls_enabled)
        self.size_slider.setEnabled(controls_enabled)
        self.size_value_label.setEnabled(controls_enabled)
        self.toolChanged.emit(tool)
        self.sizeChanged.emit(self.size_spin.value())

    def select_tool(self, tool: str) -> None:
        self._select_tool(tool)

    def sync_annotation(self, tool: str, color: str, size: int) -> None:
        if tool not in self.tool_buttons:
            return
        self._property_kind = tool
        if self._active_tool == "select":
            self.tool_buttons["select"].setChecked(True)
        self.size_spin.blockSignals(True)
        if tool == "text":
            self._font_size = max(8, min(96, int(size)))
            self.size_spin.setRange(8, 96)
            self.size_spin.setPrefix("字号 ")
            self.size_spin.setSuffix(" px")
            self.size_spin.setValue(self._font_size)
        elif tool in EFFECT_KINDS:
            self._effect_sizes[tool] = max(2, min(32, int(size)))
            self.size_spin.setRange(2, 32)
            self.size_spin.setPrefix("画笔大小 " if tool == "mosaic" else "模糊强度 ")
            self.size_spin.setSuffix("")
            self.size_spin.setValue(self._effect_sizes[tool])
        else:
            self._shape_size = max(2, min(32, int(size)))
            self.size_spin.setRange(2, 32)
            self.size_spin.setPrefix("大小 ")
            self.size_spin.setSuffix("")
            self.size_spin.setValue(self._shape_size)
        self.size_spin.blockSignals(False)
        self._refresh_size_preset_menu()
        self._sync_size_slider()
        self.color_button.setEnabled(tool not in EFFECT_KINDS)
        self.size_spin.setEnabled(True)
        self.size_preset_button.setEnabled(True)
        self.size_slider.setEnabled(True)
        self.size_value_label.setEnabled(True)
        candidate = QColor(color)
        if candidate.isValid():
            self._color = candidate
            self._update_color_button()

    def clear_annotation_selection(self) -> None:
        if self._active_tool != "select":
            self._property_kind = self._active_tool
            self.size_spin.blockSignals(True)
            if self._property_kind == "text":
                self.size_spin.setRange(8, 96)
                self.size_spin.setPrefix("字号 ")
                self.size_spin.setSuffix(" px")
                self.size_spin.setValue(self._font_size)
            elif self._property_kind in EFFECT_KINDS:
                self.size_spin.setRange(2, 32)
                self.size_spin.setPrefix(
                    "画笔大小 " if self._property_kind == "mosaic" else "模糊强度 "
                )
                self.size_spin.setSuffix("")
                self.size_spin.setValue(self._effect_sizes[self._property_kind])
            else:
                self.size_spin.setRange(2, 32)
                self.size_spin.setPrefix("大小 ")
                self.size_spin.setSuffix("")
                self.size_spin.setValue(self._shape_size)
            self.size_spin.blockSignals(False)
            self._refresh_size_preset_menu()
            self._sync_size_slider()
            self.color_button.setEnabled(self._property_kind not in EFFECT_KINDS)
            self.size_spin.setEnabled(True)
            self.size_preset_button.setEnabled(True)
            self.size_slider.setEnabled(True)
            self.size_value_label.setEnabled(True)
            self.sizeChanged.emit(self.size_spin.value())
            if self._property_kind not in EFFECT_KINDS:
                self.colorChanged.emit(self._color.name())
            return
        self.color_button.setEnabled(False)
        self.size_spin.setEnabled(False)
        self.size_preset_button.setEnabled(False)
        self.size_slider.setEnabled(False)
        self.size_value_label.setEnabled(False)

    def set_size(self, size: int) -> None:
        bounded = max(self.size_spin.minimum(), min(self.size_spin.maximum(), int(size)))
        if self._property_kind == "text":
            self._font_size = bounded
        elif self._property_kind in EFFECT_KINDS:
            self._effect_sizes[self._property_kind] = bounded
        else:
            self._shape_size = bounded
        self.size_spin.setValue(bounded)

    def _size_changed(self, size: int) -> None:
        if self._property_kind == "text":
            self._font_size = int(size)
        elif self._property_kind in EFFECT_KINDS:
            self._effect_sizes[self._property_kind] = int(size)
        else:
            self._shape_size = int(size)
        self._sync_size_preset_checks(int(size))
        self._sync_size_slider()
        self.sizeChanged.emit(int(size))

    def _sync_size_slider(self) -> None:
        self.size_slider.blockSignals(True)
        self.size_slider.setRange(self.size_spin.minimum(), self.size_spin.maximum())
        self.size_slider.setValue(self.size_spin.value())
        self.size_slider.blockSignals(False)
        label = "字号" if self._property_kind == "text" else "大小"
        self.size_value_label.setText(f"{label} {self.size_spin.value()}")

    def _apply_color(self, value: str) -> None:
        color = QColor(value)
        if not color.isValid():
            return
        self._color = color
        self._update_color_button()
        self.colorChanged.emit(color.name())

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, "选择标注颜色")
        if not color.isValid():
            return
        self._apply_color(color.name())

    def _update_color_button(self) -> None:
        self.color_button.setStyleSheet(
            f"QPushButton {{color: white; background: {self._color.name()};}}"
        )


class ShortcutSequenceEditor(QWidget):
    changed = Signal()

    def __init__(self, sequences: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sequences: list[str] = []
        self.set_sequences(sequences)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)
        self.chips_host = QWidget(self)
        self.chips_layout = QHBoxLayout(self.chips_host)
        self.chips_layout.setContentsMargins(0, 0, 0, 0)
        self.chips_layout.setSpacing(6)
        root.addWidget(self.chips_host, 1)

        self.add_button = QPushButton("＋ 添加快捷键", self)
        self.add_button.setObjectName("shortcutAddButton")
        self.add_button.setToolTip("点击后直接按下新的快捷键组合")
        self.add_button.clicked.connect(self._begin_capture)
        root.addWidget(self.add_button)

        self.capture_row = QWidget(self)
        capture_layout = QHBoxLayout(self.capture_row)
        capture_layout.setContentsMargins(0, 0, 0, 0)
        capture_layout.setSpacing(6)
        self.key_capture = QKeySequenceEdit(self.capture_row)
        self.key_capture.setObjectName("shortcutKeyCapture")
        self.key_capture.setMaximumSequenceLength(1)
        self.key_capture.setClearButtonEnabled(True)
        self.key_capture.setToolTip("请按下一个按键或组合键")
        self.capture_confirm = QPushButton("添加", self.capture_row)
        self.capture_confirm.setObjectName("shortcutCaptureConfirm")
        self.capture_confirm.setEnabled(False)
        self.capture_cancel = QPushButton("取消", self.capture_row)
        self.key_capture.keySequenceChanged.connect(
            lambda sequence: self.capture_confirm.setEnabled(not sequence.isEmpty())
        )
        self.capture_confirm.clicked.connect(self._commit_capture)
        self.capture_cancel.clicked.connect(self._cancel_capture)
        capture_layout.addWidget(self.key_capture, 1)
        capture_layout.addWidget(self.capture_confirm)
        capture_layout.addWidget(self.capture_cancel)
        self.capture_row.hide()
        root.addWidget(self.capture_row, 1)
        self._refresh_chips()

    def sequences(self) -> list[str]:
        return list(self._sequences)

    def set_sequences(self, sequences: Sequence[str]) -> None:
        normalized: list[str] = []
        for value in sequences:
            sequence = QKeySequence(str(value)).toString(QKeySequence.SequenceFormat.PortableText)
            if sequence and sequence not in normalized:
                normalized.append(sequence)
        self._sequences = normalized[:6]
        if hasattr(self, "chips_layout"):
            self._refresh_chips()
            self.changed.emit()

    def remove_sequence(self, sequence: str) -> None:
        if sequence not in self._sequences:
            return
        self._sequences.remove(sequence)
        self._refresh_chips()
        self.changed.emit()

    def _begin_capture(self) -> None:
        self.key_capture.clear()
        self.capture_confirm.setEnabled(False)
        self.chips_host.hide()
        self.add_button.hide()
        self.capture_row.show()
        self.key_capture.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _cancel_capture(self) -> None:
        self.capture_row.hide()
        self.chips_host.show()
        self.add_button.show()

    def _commit_capture(self) -> None:
        sequence = self.key_capture.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if sequence and sequence not in self._sequences and len(self._sequences) < 6:
            self._sequences.append(sequence)
            self._refresh_chips()
            self.changed.emit()
        self._cancel_capture()

    def _refresh_chips(self) -> None:
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._sequences:
            empty = QLabel("未设置", self.chips_host)
            empty.setObjectName("shortcutEmptyLabel")
            self.chips_layout.addWidget(empty)
        for sequence in self._sequences:
            chip = QPushButton(f"{sequence}  ×", self.chips_host)
            chip.setObjectName("shortcutChip")
            chip.setToolTip(f"移除快捷键 {sequence}")
            chip.clicked.connect(lambda _checked=False, value=sequence: self.remove_sequence(value))
            self.chips_layout.addWidget(chip)
        self.chips_layout.addStretch(1)
        self.add_button.setEnabled(len(self._sequences) < 6)


class ViewerSettingsDialog(QDialog):
    glowPreviewChanged = Signal(bool, str, int, int)
    defaultAppRequested = Signal()

    def __init__(self, config: QtViewerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("看图设置")
        self.setModal(True)
        self.resize(580, 720)
        self.setStyleSheet(
            f"""
            QDialog {{background: {SURFACE}; color: {TEXT};}}
            QLabel {{color: {TEXT}; font: 12px 'Microsoft YaHei UI';}}
            QLineEdit, QComboBox, QSpinBox {{background: {SURFACE_HIGH}; color: {TEXT};
                border: 1px solid {BORDER}; border-radius: 8px; padding: 7px;}}
            QCheckBox {{color: {TEXT}; spacing: 8px;}}
            QPushButton {{background: {SURFACE_HIGH}; color: {TEXT}; border: 1px solid {BORDER};
                border-radius: 9px; padding: 7px 16px;}}
            QPushButton:hover {{background: {HOVER};}}
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        display_form = QFormLayout()
        display_form.setSpacing(10)
        self.toolbar_mode = QComboBox(self)
        self.toolbar_mode.addItem("固定显示", "fixed")
        self.toolbar_mode.addItem("自动隐藏", "auto")
        self.toolbar_mode.setCurrentIndex(0 if config.toolbar_mode == "fixed" else 1)
        self.toolbar_visible = ViewerCheckBox("启用顶部工具栏", self)
        self.toolbar_visible.setChecked(config.toolbar_visible)
        self.topmost = ViewerCheckBox("启动时窗口置顶", self)
        self.topmost.setChecked(config.topmost)
        self.zoom_step = QSpinBox(self)
        self.zoom_step.setRange(1, 100)
        self.zoom_step.setSuffix("%")
        self.zoom_step.setValue(config.zoom_step)
        self.zoom_presets = QLineEdit(", ".join(str(value) for value in config.zoom_presets), self)
        display_form.addRow("工具栏显示", self.toolbar_mode)
        display_form.addRow("", self.toolbar_visible)
        display_form.addRow("", self.topmost)
        display_form.addRow("缩放步长", self.zoom_step)
        display_form.addRow("缩放预设", self.zoom_presets)
        root.addLayout(display_form)

        default_app_row = QWidget(self)
        default_app_layout = QHBoxLayout(default_app_row)
        default_app_layout.setContentsMargins(0, 0, 0, 0)
        default_app_layout.setSpacing(10)
        default_app_text = QLabel(
            "让 Windows 使用 CSImage 打开常见图片格式。最终选择需要在系统页面确认。",
            default_app_row,
        )
        default_app_text.setWordWrap(True)
        self.default_app_button = QPushButton("设为默认看图软件", default_app_row)
        self.default_app_button.setToolTip("打开 Windows 默认应用页面并定位到 CSImage")
        self.default_app_button.clicked.connect(self.defaultAppRequested.emit)
        default_app_layout.addWidget(default_app_text, 1)
        default_app_layout.addWidget(self.default_app_button)
        root.addWidget(default_app_row)

        glow_title = QLabel("图片边缘效果", self)
        glow_title.setStyleSheet(f"color: {MUTED}; font-weight: 600;")
        root.addWidget(glow_title)
        glow_form = QFormLayout()
        glow_form.setSpacing(10)
        self.edge_glow_enabled = ViewerCheckBox("启用图片边缘微光", self)
        self.edge_glow_enabled.setChecked(config.image_edge_glow)
        self.edge_glow_auto_color = ViewerCheckBox("跟随明暗主题", self)
        self.edge_glow_auto_color.setChecked(config.image_edge_glow_color == "auto")
        self._edge_glow_custom_color = QColor(
            config.image_edge_glow_color if config.image_edge_glow_color != "auto" else "#89c8ff"
        )
        self.edge_glow_color_button = QPushButton("选择颜色", self)
        self.edge_glow_color_button.setFixedWidth(112)
        color_controls = QWidget(self)
        color_layout = QHBoxLayout(color_controls)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(8)
        color_layout.addWidget(self.edge_glow_auto_color)
        color_layout.addWidget(self.edge_glow_color_button)
        color_layout.addStretch(1)
        self.edge_glow_intensity = QSpinBox(self)
        self.edge_glow_intensity.setRange(10, 100)
        self.edge_glow_intensity.setSuffix("%")
        self.edge_glow_intensity.setValue(config.image_edge_glow_intensity)
        self.edge_glow_spread = QSpinBox(self)
        self.edge_glow_spread.setRange(2, 20)
        self.edge_glow_spread.setSuffix(" px")
        self.edge_glow_spread.setValue(config.image_edge_glow_spread)
        glow_form.addRow("", self.edge_glow_enabled)
        glow_form.addRow("微光颜色", color_controls)
        glow_form.addRow("微光强度", self.edge_glow_intensity)
        glow_form.addRow("扩散范围", self.edge_glow_spread)
        root.addLayout(glow_form)

        self.edge_glow_enabled.toggled.connect(self._sync_edge_glow_controls)
        self.edge_glow_auto_color.toggled.connect(self._sync_edge_glow_controls)
        self.edge_glow_color_button.clicked.connect(self._choose_edge_glow_color)
        self.edge_glow_intensity.valueChanged.connect(self._emit_glow_preview)
        self.edge_glow_spread.valueChanged.connect(self._emit_glow_preview)
        self._sync_edge_glow_controls(emit_preview=False)

        shortcut_title = QLabel("快捷键（多个按键使用英文逗号分隔）", self)
        shortcut_title.setStyleSheet(f"color: {MUTED}; font-weight: 600;")
        root.addWidget(shortcut_title)
        shortcut_container = QWidget(self)
        shortcut_form = QFormLayout(shortcut_container)
        shortcut_form.setContentsMargins(0, 0, 6, 0)
        shortcut_form.setSpacing(8)
        self.shortcut_editors: dict[str, ShortcutSequenceEditor] = {}
        for action, defaults in DEFAULT_SHORTCUTS.items():
            editor = ShortcutSequenceEditor(
                config.shortcuts.get(action, defaults),
                shortcut_container,
            )
            self.shortcut_editors[action] = editor
            shortcut_form.addRow(SHORTCUT_LABELS[action], editor)
        shortcut_scroll = QScrollArea(self)
        shortcut_scroll.setWidgetResizable(True)
        shortcut_scroll.setFrameShape(QFrame.Shape.NoFrame)
        shortcut_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        shortcut_scroll.setWidget(shortcut_container)
        shortcut_container.setStyleSheet("background: transparent;")
        shortcut_scroll.viewport().setStyleSheet("background: transparent;")
        shortcut_scroll.setStyleSheet(
            "QScrollArea {background: transparent; border: 0;}"
            f"QScrollBar:vertical {{background: {SURFACE}; width: 10px; margin: 0;}}"
            f"QScrollBar::handle:vertical {{background: {BORDER}; min-height: 28px; "
            "border-radius: 5px;}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {height: 0;}"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical "
            "{background: transparent;}"
        )
        root.addWidget(shortcut_scroll, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._rebuild_settings_layout(
            root,
            display_form,
            default_app_row,
            glow_title,
            glow_form,
            shortcut_title,
            shortcut_scroll,
            buttons,
        )

    def _rebuild_settings_layout(
        self,
        root: QVBoxLayout,
        display_form: QFormLayout,
        default_app_row: QWidget,
        glow_title: QLabel,
        glow_form: QFormLayout,
        shortcut_title: QLabel,
        shortcut_scroll: QScrollArea,
        buttons: QDialogButtonBox,
    ) -> None:
        """Arrange existing controls into compact, navigable settings pages."""

        while root.count():
            root.takeAt(0)
        glow_title.hide()
        shortcut_title.hide()

        self.setObjectName("viewerSettingsDialog")
        self.resize(780, 620)
        self.setMinimumSize(720, 560)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        colors = viewer_theme_colors()
        self.setStyleSheet(
            f"""
            QDialog#viewerSettingsDialog {{background: {SURFACE}; color: {TEXT};}}
            QFrame#settingsHeader {{background: {SURFACE_HIGH}; border-bottom: 1px solid {BORDER};}}
            QLabel#settingsTitle {{color: {TEXT}; font: 600 19px 'Microsoft YaHei UI';}}
            QLabel#settingsSubtitle, QLabel#settingsPageDescription,
            QLabel#settingsCardDescription {{color: {MUTED}; font: 12px 'Microsoft YaHei UI';}}
            QLabel#settingsPageTitle {{color: {TEXT}; font: 600 18px 'Microsoft YaHei UI';}}
            QLabel#settingsCardTitle {{color: {TEXT}; font: 600 13px 'Microsoft YaHei UI';}}
            QFrame#settingsSidebar {{background: {SURFACE_HIGH}; border: 1px solid {BORDER};
                                     border-radius: 12px;}}
            QPushButton#settingsNavButton {{background: transparent; color: {MUTED}; border: 0;
                                            border-radius: 8px; padding: 10px 13px;
                                            text-align: left; font: 600 13px 'Microsoft YaHei UI';}}
            QPushButton#settingsNavButton:hover {{background: {HOVER}; color: {TEXT};}}
            QPushButton#settingsNavButton:checked {{background: {SELECTED}; color: {TEXT};
                                                    border-left: 3px solid {ACCENT};
                                                    padding-left: 10px;}}
            QFrame#settingsCard {{background: {SURFACE_HIGH}; border: 1px solid {BORDER};
                                  border-radius: 12px;}}
            QLabel {{color: {TEXT}; font: 12px 'Microsoft YaHei UI';}}
            QLineEdit, QKeySequenceEdit, QComboBox, QSpinBox {{background: {SURFACE};
                color: {TEXT};
                border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 9px;
                min-height: 18px;}}
            QLineEdit:focus, QKeySequenceEdit:focus, QComboBox:focus,
            QSpinBox:focus {{border-color: {ACCENT};}}
            QPushButton {{background: {SURFACE_HIGH}; color: {TEXT}; border: 1px solid {BORDER};
                border-radius: 8px; padding: 7px 15px;}}
            QPushButton:hover {{background: {HOVER};}}
            QPushButton#settingsPrimaryButton {{background: {ACCENT};
                                                   color: {colors["accent_text"]};
                                                   border-color: {ACCENT}; font-weight: 600;}}
            QPushButton#settingsPrimaryButton:hover {{background: {colors["accent_hover"]};}}
            QPushButton#shortcutChip {{background: {SELECTED}; color: {TEXT}; border: 0;
                                       padding: 5px 9px; border-radius: 8px;}}
            QPushButton#shortcutChip:hover {{background: {HOVER}; color: {DANGER};}}
            QPushButton#shortcutAddButton {{background: transparent; color: {ACCENT};
                                            border: 1px dashed {BORDER}; padding: 5px 10px;}}
            QPushButton#shortcutCaptureConfirm {{background: {ACCENT};
                                                  color: {colors["accent_text"]};
                                                  border-color: {ACCENT};}}
            QLabel#shortcutEmptyLabel {{color: {MUTED};}}
            QFrame#settingsFooter {{background: {SURFACE_HIGH}; border-top: 1px solid {BORDER};}}
            QScrollArea {{background: transparent; border: 0;}}
            QScrollArea > QWidget > QWidget {{background: transparent;}}
            QScrollBar:vertical {{background: transparent; width: 8px; margin: 2px 0;}}
            QScrollBar::handle:vertical {{background: {BORDER}; min-height: 30px;
                                          border-radius: 4px;}}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height: 0;}}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            """
        )

        header = QFrame(self)
        header.setObjectName("settingsHeader")
        header.setFixedHeight(76)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 14, 24, 12)
        header_layout.setSpacing(3)
        title = QLabel("看图设置", header)
        title.setObjectName("settingsTitle")
        subtitle = QLabel("调整 CSImage 的查看体验、显示效果与快捷键", header)
        subtitle.setObjectName("settingsSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(18, 16, 18, 16)
        body_layout.setSpacing(16)

        self.settings_sidebar = QFrame(body)
        self.settings_sidebar.setObjectName("settingsSidebar")
        self.settings_sidebar.setFixedWidth(146)
        sidebar_layout = QVBoxLayout(self.settings_sidebar)
        sidebar_layout.setContentsMargins(8, 8, 8, 8)
        sidebar_layout.setSpacing(4)
        self.settings_nav_group = QButtonGroup(self)
        self.settings_nav_group.setExclusive(True)
        self.settings_nav_buttons: dict[str, QPushButton] = {}

        self.settings_pages = QStackedWidget(body)
        page_specs = (
            ("general", "常规"),
            ("appearance", "显示效果"),
            ("shortcuts", "快捷键"),
        )
        for index, (key, label) in enumerate(page_specs):
            button = QPushButton(label, self.settings_sidebar)
            button.setObjectName("settingsNavButton")
            button.setCheckable(True)
            button.setFixedHeight(42)
            button.clicked.connect(
                lambda _checked=False, page_index=index: self.settings_pages.setCurrentIndex(
                    page_index
                )
            )
            sidebar_layout.addWidget(button)
            self.settings_nav_group.addButton(button)
            self.settings_nav_buttons[key] = button
        sidebar_layout.addStretch(1)
        body_layout.addWidget(self.settings_sidebar)
        body_layout.addWidget(self.settings_pages, 1)
        root.addWidget(body, 1)

        general_page, general_content = self._settings_page(
            "常规",
            "管理工具栏、窗口行为、缩放方式和默认图片应用。",
        )
        display_form.setContentsMargins(0, 0, 0, 0)
        display_form.setHorizontalSpacing(18)
        display_form.setVerticalSpacing(11)
        display_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        display_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        display_card, display_card_layout = self._settings_card(
            "界面与缩放",
            "设置常用查看控件和缩放操作。",
        )
        display_card_layout.addLayout(display_form)
        general_content.addWidget(display_card)

        default_card, default_card_layout = self._settings_card(
            "默认看图软件",
            "在 Windows 设置中将常用图片格式交给 CSImage 打开。",
        )
        default_app_row.setStyleSheet("background: transparent;")
        default_card_layout.addWidget(default_app_row)
        general_content.addWidget(default_card)
        general_content.addStretch(1)
        self.settings_pages.addWidget(general_page)

        appearance_page, appearance_content = self._settings_page(
            "显示效果",
            "自定义图片边界的微光，让透明或深色图片更容易辨认。",
        )
        glow_form.setContentsMargins(0, 0, 0, 0)
        glow_form.setHorizontalSpacing(18)
        glow_form.setVerticalSpacing(12)
        glow_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        glow_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        glow_card, glow_card_layout = self._settings_card(
            "图片边缘微光",
            "效果会即时预览，关闭后不再绘制图片边缘光晕。",
        )
        glow_card_layout.addLayout(glow_form)
        appearance_content.addWidget(glow_card)
        appearance_content.addStretch(1)
        self.settings_pages.addWidget(appearance_page)

        shortcuts_page, shortcuts_content = self._settings_page(
            "快捷键",
            "点击添加后直接按下组合键；点击已有快捷键标签即可移除。",
        )
        shortcut_actions = QHBoxLayout()
        shortcut_actions.addStretch(1)
        self.reset_shortcuts_button = QPushButton("恢复默认快捷键", shortcuts_page)
        self.reset_shortcuts_button.clicked.connect(self._reset_shortcuts)
        shortcut_actions.addWidget(self.reset_shortcuts_button)
        shortcuts_content.addLayout(shortcut_actions)
        self.shortcut_scroll = shortcut_scroll
        shortcut_scroll.setMinimumHeight(300)
        shortcuts_content.addWidget(shortcut_scroll, 1)
        self.settings_pages.addWidget(shortcuts_page)

        self.settings_nav_buttons["general"].setChecked(True)
        self.settings_pages.setCurrentIndex(0)

        footer = QFrame(self)
        footer.setObjectName("settingsFooter")
        footer.setFixedHeight(64)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 12, 18, 12)
        footer_layout.addStretch(1)
        self.settings_button_box = buttons
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setObjectName("settingsPrimaryButton")
        save_button.setMinimumWidth(86)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setMinimumWidth(86)
        footer_layout.addWidget(buttons)
        root.addWidget(footer)

    def _settings_page(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self.settings_pages)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        title_label = QLabel(title, page)
        title_label.setObjectName("settingsPageTitle")
        description_label = QLabel(description, page)
        description_label.setObjectName("settingsPageDescription")
        description_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        return page, layout

    def _settings_card(self, title: str, description: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(self)
        card.setObjectName("settingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(9)
        title_label = QLabel(title, card)
        title_label.setObjectName("settingsCardTitle")
        description_label = QLabel(description, card)
        description_label.setObjectName("settingsCardDescription")
        description_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addSpacing(3)
        return card, layout

    def _reset_shortcuts(self) -> None:
        for action, defaults in DEFAULT_SHORTCUTS.items():
            self.shortcut_editors[action].set_sequences(defaults)

    def _glow_color_value(self) -> str:
        if self.edge_glow_auto_color.isChecked():
            return "auto"
        return self._edge_glow_custom_color.name()

    def _update_edge_glow_color_button(self) -> None:
        color = self._edge_glow_custom_color
        text_color = "#ffffff" if color.lightness() < 145 else "#111111"
        self.edge_glow_color_button.setStyleSheet(
            f"QPushButton {{background: {color.name()}; color: {text_color}; "
            f"border: 1px solid {BORDER}; border-radius: 9px;}}"
        )

    def _sync_edge_glow_controls(
        self, _checked: bool | None = None, *, emit_preview: bool = True
    ) -> None:
        enabled = self.edge_glow_enabled.isChecked()
        self.edge_glow_auto_color.setEnabled(enabled)
        self.edge_glow_color_button.setEnabled(
            enabled and not self.edge_glow_auto_color.isChecked()
        )
        self.edge_glow_intensity.setEnabled(enabled)
        self.edge_glow_spread.setEnabled(enabled)
        self._update_edge_glow_color_button()
        if emit_preview:
            self._emit_glow_preview()

    def _choose_edge_glow_color(self) -> None:
        color = QColorDialog.getColor(self._edge_glow_custom_color, self, "选择图片边缘微光颜色")
        if not color.isValid():
            return
        self._edge_glow_custom_color = color
        self._update_edge_glow_color_button()
        self._emit_glow_preview()

    def _emit_glow_preview(self, _value: object = None) -> None:
        self.glowPreviewChanged.emit(
            self.edge_glow_enabled.isChecked(),
            self._glow_color_value(),
            self.edge_glow_intensity.value(),
            self.edge_glow_spread.value(),
        )

    def apply_to(self, config: QtViewerConfig) -> None:
        config.toolbar_mode = str(self.toolbar_mode.currentData())
        config.toolbar_visible = self.toolbar_visible.isChecked()
        config.topmost = self.topmost.isChecked()
        config.zoom_step = self.zoom_step.value()
        config.image_edge_glow = self.edge_glow_enabled.isChecked()
        config.image_edge_glow_color = self._glow_color_value()
        config.image_edge_glow_intensity = self.edge_glow_intensity.value()
        config.image_edge_glow_spread = self.edge_glow_spread.value()
        parsed_presets: list[int] = []
        for token in self.zoom_presets.text().replace("，", ",").split(","):
            try:
                value = int(token.strip().removesuffix("%"))
            except ValueError:
                continue
            if 10 <= value <= 800 and value not in parsed_presets:
                parsed_presets.append(value)
        if parsed_presets:
            config.zoom_presets = parsed_presets[:12]
        for action, editor in self.shortcut_editors.items():
            config.shortcuts[action] = editor.sequences()


def make_shortcut(sequence: str, parent: QWidget, callback: Callable[[], object]) -> QAction:
    action = QAction(parent)
    action.setShortcut(QKeySequence(sequence))
    action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
    action.triggered.connect(callback)
    parent.addAction(action)
    return action
