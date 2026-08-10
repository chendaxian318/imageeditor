"""PySide6 widgets for the viewer-only desktop shell."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QEnterEvent,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .qt_viewer_config import DEFAULT_SHORTCUTS, SHORTCUT_LABELS, QtViewerConfig

CANVAS = "#050607"
SURFACE = "#15181d"
SURFACE_HIGH = "#20242b"
BORDER = "#343a43"
TEXT = "#f5f7fa"
MUTED = "#aeb6c2"
ACCENT = "#89c8ff"
DANGER = "#f4aaaa"


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
        width: int = 38,
    ) -> None:
        super().__init__(text, parent)
        self.icon_name = icon_name
        self._selected = False
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.setMinimumWidth(width if not text else max(width, 58))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            f"""
            QPushButton {{
                color: {TEXT}; background: transparent; border: 0;
                border-radius: 10px; padding: 0 10px;
                font: 500 14px 'Microsoft YaHei UI';
            }}
            QPushButton:hover {{ background: #2b3038; }}
            QPushButton:pressed {{ background: #353c46; }}
            QPushButton:disabled {{ color: #68717e; }}
            """
        )

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.icon_name:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#ffffff" if self._selected else TEXT)
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        rect = self.rect()
        cx, cy = rect.center().x(), rect.center().y()
        name = self.icon_name
        if name == "minus":
            painter.drawLine(cx - 6, cy, cx + 6, cy)
        elif name == "plus":
            painter.drawLine(cx - 6, cy, cx + 6, cy)
            painter.drawLine(cx, cy - 6, cx, cy + 6)
        elif name == "rotate":
            path = QPainterPath()
            path.arcMoveTo(QRectF(cx - 7, cy - 7, 14, 14), 35)
            path.arcTo(QRectF(cx - 7, cy - 7, 14, 14), 35, 285)
            painter.drawPath(path)
            painter.drawLine(cx + 5, cy - 7, cx + 8, cy - 7)
            painter.drawLine(cx + 8, cy - 7, cx + 8, cy - 4)
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
            painter.setFont(QFont("Segoe UI Symbol", 18))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "★" if self._selected else "☆")
        elif name == "edit":
            painter.drawLine(cx - 6, cy + 6, cx + 5, cy - 5)
            painter.drawLine(cx - 7, cy + 7, cx - 3, cy + 6)
            painter.drawLine(cx + 4, cy - 6, cx + 7, cy - 3)
        elif name == "settings":
            painter.drawEllipse(QPointF(cx, cy), 6, 6)
            painter.drawEllipse(QPointF(cx, cy), 2, 2)
            for angle in range(0, 360, 45):
                rad = math.radians(angle)
                painter.drawLine(
                    QPointF(cx + math.cos(rad) * 7, cy + math.sin(rad) * 7),
                    QPointF(cx + math.cos(rad) * 9, cy + math.sin(rad) * 9),
                )
        painter.end()


class WindowControlButton(QPushButton):
    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        labels = {"minimize": "最小化", "maximize": "最大化", "close": "关闭"}
        self.setToolTip(labels[kind])
        self.setAccessibleName(labels[kind])
        self.setFixedSize(46, 32)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hover = "#b64149" if kind == "close" else "#2a2e35"
        self.setStyleSheet(
            f"QPushButton {{background: transparent; border: 0; border-radius: 8px;}}"
            f"QPushButton:hover {{background: {hover};}}"
        )

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
            painter.drawRect(cx - 5, cy - 5, 10, 10)
        else:
            painter.drawLine(cx - 5, cy - 5, cx + 5, cy + 5)
            painter.drawLine(cx + 5, cy - 5, cx - 5, cy + 5)
        painter.end()


class ViewerTitleBar(QFrame):
    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.host_window = window
        self.setFixedHeight(42)
        self.setObjectName("viewerTitleBar")
        self.setStyleSheet(
            f"#viewerTitleBar {{background: {SURFACE}; border-bottom: 1px solid {BORDER};}}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 6, 4)
        layout.setSpacing(6)
        mark = QLabel("●", self)
        mark.setStyleSheet(f"color: {TEXT}; font-size: 18px; background: transparent;")
        mark.setFixedWidth(22)
        self.title_label = QLabel("轻量图片查看器", self)
        self.title_label.setStyleSheet(f"color: {TEXT}; font: 600 13px 'Microsoft YaHei UI';")
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(mark)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        self.minimize_button = WindowControlButton("minimize", self)
        self.maximize_button = WindowControlButton("maximize", self)
        self.close_button = WindowControlButton("close", self)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.toggle_maximize)
        self.close_button.clicked.connect(window.close)
        layout.addWidget(self.minimize_button)
        layout.addWidget(self.maximize_button)
        layout.addWidget(self.close_button)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title or "轻量图片查看器")

    def toggle_maximize(self) -> None:
        if self.host_window.isMaximized():
            self.host_window.showNormal()
        else:
            self.host_window.showMaximized()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.host_window.isFullScreen():
            handle = self.host_window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        widget = watched if isinstance(watched, QWidget) else None
        if widget is None or widget.window() is not self.window:
            return False
        if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
            edges = self._edges(event.globalPosition().toPoint())
            self.window.setCursor(QCursor(self._cursor(edges)))
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


class MarkupItem(QGraphicsItem):
    def __init__(self, spec: AnnotationSpec) -> None:
        super().__init__()
        self.spec = spec
        self.setZValue(20)

    def boundingRect(self) -> QRectF:
        return QRectF(self.spec.start, self.spec.end).normalized().adjusted(-14, -14, 14, 14)

    def set_end(self, point: QPointF) -> None:
        self.prepareGeometryChange()
        self.spec.end = point
        self.update()

    def paint(self, painter: QPainter, _option: object, _widget: QWidget | None = None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#ff4c48"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        if self.spec.kind == "rectangle":
            painter.drawRect(QRectF(self.spec.start, self.spec.end).normalized())
            return
        start, end = self.spec.start, self.spec.end
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        length = 16.0
        wing = math.radians(28)
        left = QPointF(
            end.x() - math.cos(angle - wing) * length, end.y() - math.sin(angle - wing) * length
        )
        right = QPointF(
            end.x() - math.cos(angle + wing) * length, end.y() - math.sin(angle + wing) * length
        )
        painter.setBrush(QColor("#ff4c48"))
        painter.drawPolygon(QPolygonF([end, left, right]))


class ImageView(QGraphicsView):
    zoomChanged = Signal(int)
    pointerMoved = Signal(int)

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
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._source_qimage: QImage | None = None
        self._fit_mode = True
        self._annotation_tool = ""
        self._annotation_start: QPointF | None = None
        self._draft_item: MarkupItem | None = None
        self._annotations: list[AnnotationSpec] = []
        self._annotation_items: list[MarkupItem] = []
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    @property
    def has_image(self) -> bool:
        return self._pixmap_item is not None

    @property
    def annotation_tool(self) -> str:
        return self._annotation_tool

    @property
    def annotations(self) -> tuple[AnnotationSpec, ...]:
        return tuple(self._annotations)

    def set_image(self, image: QImage, *, fit: bool = True) -> None:
        self.scene().clear()
        self._source_qimage = image.copy()
        self._pixmap_item = self.scene().addPixmap(QPixmap.fromImage(image))
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().setSceneRect(self._pixmap_item.boundingRect())
        self._annotations.clear()
        self._annotation_items.clear()
        self._draft_item = None
        self._annotation_start = None
        if fit:
            self.fit_to_window()
        else:
            self.actual_size()

    def clear_image(self) -> None:
        self.scene().clear()
        self._pixmap_item = None
        self._source_qimage = None
        self._annotations.clear()
        self.zoomChanged.emit(100)

    def fit_to_window(self) -> None:
        if self._pixmap_item is None:
            return
        self._fit_mode = True
        self.resetTransform()
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoomChanged.emit(self.zoom_percent())

    def actual_size(self) -> None:
        if self._pixmap_item is None:
            return
        self._fit_mode = False
        self.resetTransform()
        self.centerOn(self._pixmap_item)
        self.zoomChanged.emit(100)

    def zoom_percent(self) -> int:
        return max(1, min(800, round(self.transform().m11() * 100)))

    def set_zoom_percent(self, percent: int) -> None:
        if self._pixmap_item is None:
            return
        bounded = max(10, min(800, int(percent)))
        self._fit_mode = False
        self.resetTransform()
        self.scale(bounded / 100.0, bounded / 100.0)
        self.zoomChanged.emit(bounded)

    def step_zoom(self, delta: int) -> None:
        self.set_zoom_percent(self.zoom_percent() + delta)

    def set_annotation_tool(self, tool: str) -> None:
        self._annotation_tool = tool if tool in {"rectangle", "arrow"} else ""
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag
            if self._annotation_tool
            else QGraphicsView.DragMode.ScrollHandDrag
        )
        self.viewport().setCursor(
            Qt.CursorShape.CrossCursor if self._annotation_tool else Qt.CursorShape.ArrowCursor
        )

    def undo_annotation(self) -> None:
        if not self._annotations:
            return
        self._annotations.pop()
        item = self._annotation_items.pop()
        self.scene().removeItem(item)

    def clear_annotations(self) -> None:
        for item in self._annotation_items:
            self.scene().removeItem(item)
        self._annotations.clear()
        self._annotation_items.clear()
        if self._draft_item is not None:
            self.scene().removeItem(self._draft_item)
            self._draft_item = None
        self._annotation_start = None

    def composited_pil(self, base: Image.Image) -> Image.Image:
        output = base.convert("RGBA")
        draw = ImageDraw.Draw(output)
        for annotation in self._annotations:
            start = (round(annotation.start.x()), round(annotation.start.y()))
            end = (round(annotation.end.x()), round(annotation.end.y()))
            if annotation.kind == "rectangle":
                draw.rectangle((*start, *end), outline="#ff4c48", width=4)
                continue
            draw.line((*start, *end), fill="#ff4c48", width=4)
            angle = math.atan2(end[1] - start[1], end[0] - start[0])
            length = 16
            wing = math.radians(28)
            left = (
                end[0] - math.cos(angle - wing) * length,
                end[1] - math.sin(angle - wing) * length,
            )
            right = (
                end[0] - math.cos(angle + wing) * length,
                end[1] - math.sin(angle + wing) * length,
            )
            draw.polygon([end, left, right], fill="#ff4c48")
        return output

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fit_mode and self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self.zoomChanged.emit(self.zoom_percent())

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap_item is None:
            return
        direction = 1 if event.angleDelta().y() > 0 else -1
        self.step_zoom(direction * 10)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.pointerMoved.emit(round(event.position().y()))
        if self._annotation_tool and self._draft_item is not None:
            point = self._bounded_scene_point(event.position().toPoint())
            self._draft_item.set_end(point)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if (
            self._annotation_tool
            and self._pixmap_item is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            point = self._bounded_scene_point(event.position().toPoint())
            self._annotation_start = point
            spec = AnnotationSpec(self._annotation_tool, point, point)
            self._draft_item = MarkupItem(spec)
            self.scene().addItem(self._draft_item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._draft_item is not None and event.button() == Qt.MouseButton.LeftButton:
            spec = self._draft_item.spec
            if abs(spec.end.x() - spec.start.x()) + abs(spec.end.y() - spec.start.y()) >= 4:
                self._annotations.append(spec)
                self._annotation_items.append(self._draft_item)
            else:
                self.scene().removeItem(self._draft_item)
            self._draft_item = None
            self._annotation_start = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _bounded_scene_point(self, viewport_point: QPoint) -> QPointF:
        point = self.mapToScene(viewport_point)
        rect = self.sceneRect()
        return QPointF(
            max(rect.left(), min(rect.right(), point.x())),
            max(rect.top(), min(rect.bottom(), point.y())),
        )


class NavigationButton(QPushButton):
    def __init__(self, direction: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.direction = direction
        label = "上一张图片" if direction < 0 else "下一张图片"
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QPushButton {background: transparent; border: 0;} QToolTip {color: white;}"
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse() and self.isEnabled()
        if hovered:
            painter.setBrush(QColor(24, 29, 36, 165))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(self.rect().adjusted(10, 18, -10, -18), 24, 24)
        pen = QPen(QColor("#ffffff" if hovered else ACCENT), 3.2)
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
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("viewerToolbar")
        self.setStyleSheet(
            f"""
            #viewerToolbar {{background: {SURFACE_HIGH}; border: 1px solid {BORDER};
                            border-radius: 18px;}}
            QLineEdit {{background: transparent; color: {TEXT}; border: 0; padding: 0 4px;
                        font: 600 13px 'Microsoft YaHei UI'; selection-background-color: #41596f;}}
            QFrame#separator {{background: #4a515c; border: 0;}}
            """
        )
        self.button_layout = QHBoxLayout(self)
        self.button_layout.setContentsMargins(8, 4, 8, 4)
        self.button_layout.setSpacing(1)
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_animation = QPropertyAnimation(self.opacity_effect, b"opacity", self)
        self.opacity_animation.setDuration(180)
        self.opacity_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def add_button(self, button: QWidget) -> QWidget:
        self.button_layout.addWidget(button)
        return button

    def add_separator(self) -> None:
        separator = QFrame(self)
        separator.setObjectName("separator")
        separator.setFixedSize(1, 24)
        self.button_layout.addWidget(separator)

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
        self.setStyleSheet(
            f"""
            #filmstrip {{background: rgba(26, 30, 36, 225); border: 0; border-radius: 14px;}}
            QPushButton {{color: {MUTED}; background: transparent; border: 0; padding: 5px 12px;
                          border-radius: 9px; font: 12px 'Microsoft YaHei UI';}}
            QPushButton:checked {{color: {TEXT}; background: #353b45;}}
            QListWidget {{background: transparent; border: 0; outline: 0;}}
            QListWidget::item {{background: transparent; border: 0; padding: 3px;}}
            QListWidget::item:selected {{background: transparent; border: 0;}}
            """
        )
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
    undoRequested = Signal()
    cancelRequested = Signal()
    copyRequested = Signal()
    replaceRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("markupToolbar")
        self.setStyleSheet(
            f"""
            #markupToolbar {{background: {SURFACE_HIGH}; border: 1px solid {BORDER};
                            border-radius: 15px;}}
            QPushButton {{color: {TEXT}; background: transparent; border: 0; border-radius: 9px;
                          padding: 6px 11px; font: 12px 'Microsoft YaHei UI';}}
            QPushButton:hover {{background: #303640;}}
            QPushButton:checked {{background: #3d5266; color: white;}}
            """
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(2)
        self.rect_button = QPushButton("矩形", self)
        self.arrow_button = QPushButton("箭头", self)
        for tool, button in (("rectangle", self.rect_button), ("arrow", self.arrow_button)):
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked, value=tool: self._select_tool(value) if checked else None
            )
            layout.addWidget(button)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_group.addButton(self.rect_button)
        self.tool_group.addButton(self.arrow_button)
        for text, signal in (
            ("撤销", self.undoRequested),
            ("取消修改", self.cancelRequested),
            ("仅复制", self.copyRequested),
            ("替换原图", self.replaceRequested),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(signal.emit)
            layout.addWidget(button)

    def _select_tool(self, tool: str) -> None:
        self.toolChanged.emit(tool)


class ViewerSettingsDialog(QDialog):
    def __init__(self, config: QtViewerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("看图设置")
        self.setModal(True)
        self.resize(560, 620)
        self.setStyleSheet(
            f"""
            QDialog {{background: {SURFACE}; color: {TEXT};}}
            QLabel {{color: {TEXT}; font: 12px 'Microsoft YaHei UI';}}
            QLineEdit, QComboBox, QSpinBox {{background: {SURFACE_HIGH}; color: {TEXT};
                border: 1px solid {BORDER}; border-radius: 8px; padding: 7px;}}
            QCheckBox {{color: {TEXT}; spacing: 8px;}}
            QPushButton {{background: {SURFACE_HIGH}; color: {TEXT}; border: 1px solid {BORDER};
                border-radius: 9px; padding: 7px 16px;}}
            QPushButton:hover {{background: #303640;}}
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
        self.toolbar_visible = QCheckBox("启用顶部工具栏", self)
        self.toolbar_visible.setChecked(config.toolbar_visible)
        self.topmost = QCheckBox("启动时窗口置顶", self)
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

        shortcut_title = QLabel("快捷键（多个按键使用英文逗号分隔）", self)
        shortcut_title.setStyleSheet(f"color: {MUTED}; font-weight: 600;")
        root.addWidget(shortcut_title)
        shortcut_form = QFormLayout()
        shortcut_form.setSpacing(8)
        self.shortcut_edits: dict[str, QLineEdit] = {}
        for action, defaults in DEFAULT_SHORTCUTS.items():
            edit = QLineEdit(", ".join(config.shortcuts.get(action, defaults)), self)
            self.shortcut_edits[action] = edit
            shortcut_form.addRow(SHORTCUT_LABELS[action], edit)
        root.addLayout(shortcut_form)
        root.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def apply_to(self, config: QtViewerConfig) -> None:
        config.toolbar_mode = str(self.toolbar_mode.currentData())
        config.toolbar_visible = self.toolbar_visible.isChecked()
        config.topmost = self.topmost.isChecked()
        config.zoom_step = self.zoom_step.value()
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
        for action, edit in self.shortcut_edits.items():
            values = [
                token.strip()
                for token in edit.text().replace("，", ",").split(",")
                if token.strip()
            ]
            config.shortcuts[action] = list(dict.fromkeys(values))[:6] or list(
                DEFAULT_SHORTCUTS[action]
            )


def make_shortcut(sequence: str, parent: QWidget, callback: Callable[[], object]) -> QAction:
    action = QAction(parent)
    action.setShortcut(QKeySequence(sequence))
    action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
    action.triggered.connect(callback)
    parent.addAction(action)
    return action
