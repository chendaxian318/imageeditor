"""PySide6 widgets for the viewer-only desktop shell."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
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
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
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
            points: list[QPointF] = []
            tooth_count = 8
            for index in range(tooth_count * 4):
                angle = -math.pi / 2 + index * math.pi / (tooth_count * 2)
                radius = 9.6 if index % 4 in {1, 2} else 6.7
                points.append(
                    QPointF(
                        cx + math.cos(angle) * radius,
                        cy + math.sin(angle) * radius,
                    )
                )
            gear = QPainterPath()
            gear.addPolygon(QPolygonF(points))
            gear.closeSubpath()
            gear.addEllipse(QPointF(cx, cy), 2.8, 2.8)
            gear.setFillRule(Qt.FillRule.OddEvenFill)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPath(gear)
        painter.end()


class WindowControlButton(QPushButton):
    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        labels = {"minimize": "最小化", "maximize": "最大化", "close": "关闭"}
        self.setToolTip(labels[kind])
        self.setAccessibleName(labels[kind])
        self.setFixedSize(46, 41)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hover = "#b64149" if kind == "close" else "#2a2e35"
        self.setStyleSheet(
            "QPushButton {background: transparent; border: 0; border-radius: 0;}"
            f"QPushButton:hover {{background: {hover};}}"
            f"QPushButton:pressed {{background: {'#96343b' if kind == 'close' else '#343a43'};}}"
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
            painter.drawRect(cx - 3, cy - 5, 8, 8)
            painter.drawRect(cx - 6, cy - 2, 8, 8)
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
            f"#viewerTitleBar {{background: {SURFACE}; border: 0; "
            f"border-bottom: 1px solid {BORDER}; border-radius: 0;}}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(0)
        mark = QLabel("●", self)
        mark.setStyleSheet(f"color: {TEXT}; font-size: 18px; background: transparent;")
        mark.setFixedWidth(22)
        self.title_label = QLabel("CSImage", self)
        self.title_label.setStyleSheet(f"color: {TEXT}; font: 600 13px 'Microsoft YaHei UI';")
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(mark)
        layout.addWidget(self.title_label)
        layout.addStretch(1)
        self.window_controls = QFrame(self)
        self.window_controls.setObjectName("windowControls")
        self.window_controls.setStyleSheet(
            f"#windowControls {{background: {SURFACE_HIGH}; border: 0; "
            "border-left: 1px solid #343a43; border-bottom-left-radius: 18px;}}"
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

    def set_title(self, title: str) -> None:
        self.title_label.setText(title or "CSImage")

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
    color: str = "#ff4c48"
    size: int = 6
    text: str = ""
    number: int = 0
    points: tuple[QPointF, ...] = ()
    control: QPointF | None = None


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
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(20)

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
        self.setSelected(True)
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
        if self.spec.kind in {"mosaic", "blur"}:
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
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            if self._change_started is not None:
                self._change_started()
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
            self.spec.start += delta
            self.spec.end += delta
            self.spec.points = tuple(point + delta for point in self.spec.points)
            if self.spec.control is not None:
                self.spec.control += delta
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
            if snapshot.kind == "freehand" and snapshot.points:
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
        if self._source_image is None:
            return point
        return QPointF(
            max(0.0, min(float(self._source_image.width()), point.x())),
            max(0.0, min(float(self._source_image.height()), point.y())),
        )

    def _selection_rect(self) -> QRectF:
        return self._selection_rect_for(self.spec)

    @staticmethod
    def _selection_rect_for(spec: AnnotationSpec) -> QRectF:
        if spec.kind == "freehand" and spec.points:
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
            region = qimage_to_pil(source.copy(left, top, right - left, bottom - top))
            try:
                if self.spec.kind == "mosaic":
                    cell = max(4, self.spec.size * 2)
                    small = region.resize(
                        (max(1, region.width // cell), max(1, region.height // cell)),
                        Image.Resampling.BILINEAR,
                    )
                    try:
                        effect = small.resize(region.size, Image.Resampling.NEAREST)
                    finally:
                        small.close()
                else:
                    effect = region.filter(ImageFilter.GaussianBlur(radius=max(2, self.spec.size)))
                try:
                    self._effect_cache = QPixmap.fromImage(pil_to_qimage(effect))
                finally:
                    effect.close()
            finally:
                region.close()
            self._effect_cache_key = key
        return self._effect_cache, QRectF(left, top, right - left, bottom - top)


class ImageView(QGraphicsView):
    zoomChanged = Signal(int)
    pointerMoved = Signal(int)
    annotationSizeSelected = Signal(int)
    annotationSelected = Signal(str, str, int)
    annotationSelectionCleared = Signal()
    documentPageChanged = Signal(int)
    documentPagesRequested = Signal(object)
    documentPagesReleased = Signal(object)

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
        self._document_mode = False
        self._document_page_rects: list[QRectF] = []
        self._document_page_items: dict[int, QGraphicsPixmapItem] = {}
        self._document_background_items: list[QGraphicsRectItem] = []
        self._current_document_page = 0
        self._fit_mode = True
        self._annotation_tool = ""
        self._annotation_start: QPointF | None = None
        self._draft_item: MarkupItem | None = None
        self._annotations: list[AnnotationSpec] = []
        self._annotation_items: list[MarkupItem] = []
        self._annotation_history: list[list[AnnotationSpec]] = []
        self._pending_annotation_history: list[AnnotationSpec] | None = None
        self._annotation_color = "#ff4c48"
        self._annotation_size = 6
        self._annotation_font_size = 20
        self._text_editor: QLineEdit | None = None
        self._text_editor_scene_point: QPointF | None = None
        self._text_editor_annotation_index: int | None = None
        self._text_editor_color = self._annotation_color
        self._text_editor_size = self._annotation_font_size
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.verticalScrollBar().valueChanged.connect(self._document_viewport_changed)

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

    def set_image(self, image: QImage, *, fit: bool = True) -> None:
        self._cancel_inline_text_editor()
        self._pixmap_item = None
        self.scene().clear()
        self._document_mode = False
        self._document_page_rects.clear()
        self._document_page_items.clear()
        self._document_background_items.clear()
        self._current_document_page = 0
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._source_qimage = image.copy()
        self._pixmap_item = self.scene().addPixmap(QPixmap.fromImage(image))
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().setSceneRect(self._pixmap_item.boundingRect())
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

    def set_document(
        self,
        page_sizes: Sequence[tuple[int, int]],
        page_images: Mapping[int, QImage] | None = None,
        *,
        current_index: int = 0,
    ) -> None:
        """Display PDF pages in one vertically scrolling, lazily populated scene."""

        normalized = [(max(1, int(width)), max(1, int(height))) for width, height in page_sizes]
        if not normalized:
            self.clear_image()
            return
        self._cancel_inline_text_editor()
        self._pixmap_item = None
        self.scene().clear()
        self._document_mode = True
        self._document_page_rects.clear()
        self._document_page_items.clear()
        self._document_background_items.clear()
        self._current_document_page = max(0, min(len(normalized) - 1, int(current_index)))
        self._pixmap_item = None
        self._source_qimage = None
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
        self.fit_to_window()
        self.scroll_to_document_page(self._current_document_page)
        self._update_visible_document_pages()

    def set_document_page(self, page_index: int, image: QImage) -> None:
        if not self._document_mode or not 0 <= page_index < len(self._document_page_rects):
            return
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
        self.scene().clear()
        self._source_qimage = None
        self._document_mode = False
        self._document_page_rects.clear()
        self._document_page_items.clear()
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
            available_width = max(1.0, self.viewport().width() - 64.0)
            scale = max(0.1, min(8.0, available_width / maximum_width))
            self.resetTransform()
            self.scale(scale, scale)
            self.scroll_to_document_page(current_page)
            self.zoomChanged.emit(self.zoom_percent())
            return
        if self._pixmap_item is None:
            return
        self._fit_mode = True
        self.resetTransform()
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._refresh_inline_text_editor()
        self.zoomChanged.emit(self.zoom_percent())

    def actual_size(self) -> None:
        if self._document_mode and self._document_page_rects:
            self._fit_mode = False
            current_page = self._current_document_page
            self.resetTransform()
            self.scroll_to_document_page(current_page)
            self.zoomChanged.emit(100)
            return
        if self._pixmap_item is None:
            return
        self._fit_mode = False
        self.resetTransform()
        self.centerOn(self._pixmap_item)
        self._refresh_inline_text_editor()
        self.zoomChanged.emit(100)

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
        self.viewport().setCursor(
            Qt.CursorShape.CrossCursor
            if self._annotation_tool and self._annotation_tool != "select"
            else Qt.CursorShape.ArrowCursor
        )

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
        self.scene().addItem(item)
        self._annotations.append(spec)
        self._annotation_items.append(item)

    def undo_annotation(self) -> None:
        if not self._annotation_history:
            return
        snapshot = self._annotation_history.pop()
        self._restore_annotations(snapshot)

    def clear_annotations(self) -> None:
        self._cancel_inline_text_editor()
        for item in self._annotation_items:
            self.scene().removeItem(item)
        self._annotations.clear()
        self._annotation_items.clear()
        self._annotation_history.clear()
        self._pending_annotation_history = None
        if self._draft_item is not None:
            self.scene().removeItem(self._draft_item)
            self._draft_item = None
        self._annotation_start = None

    def commit_pending_text(self) -> None:
        if self._text_editor is not None:
            self._commit_inline_text_editor()

    def undo_inline_text(self) -> bool:
        if self._text_editor is None:
            return False
        self._text_editor.undo()
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

    def composited_pil(self, base: Image.Image) -> Image.Image:
        output = base.convert("RGBA")
        draw = ImageDraw.Draw(output)
        for annotation in self._annotations:
            start = (round(annotation.start.x()), round(annotation.start.y()))
            end = (round(annotation.end.x()), round(annotation.end.y()))
            left, right = sorted((max(0, start[0]), min(output.width, end[0])))
            top, bottom = sorted((max(0, start[1]), min(output.height, end[1])))
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
                    [(round(point.x()), round(point.y())) for point in points],
                    fill=annotation.color,
                    width=width,
                    joint="curve",
                )
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
            if annotation.kind in {"mosaic", "blur"} and right > left and bottom > top:
                region = output.crop(box)
                try:
                    if annotation.kind == "mosaic":
                        cell = max(4, annotation.size * 2)
                        small = region.resize(
                            (max(1, region.width // cell), max(1, region.height // cell)),
                            Image.Resampling.BILINEAR,
                        )
                        try:
                            effect = small.resize(region.size, Image.Resampling.NEAREST)
                        finally:
                            small.close()
                    else:
                        effect = region.filter(
                            ImageFilter.GaussianBlur(radius=max(2, annotation.size))
                        )
                    try:
                        output.paste(effect, (left, top))
                    finally:
                        effect.close()
                finally:
                    region.close()
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
                        + t * t * annotation.end.x(),
                        inverse * inverse * annotation.start.y()
                        + 2 * inverse * t * control.y()
                        + t * t * annotation.end.y(),
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
            preload_rect = viewport_rect.adjusted(
                0,
                -viewport_rect.height(),
                0,
                viewport_rect.height(),
            )
            desired = {
                index
                for index, rect in enumerate(self._document_page_rects)
                if rect.intersects(preload_rect)
            }
            desired.add(self._current_document_page)

        loaded = set(self._document_page_items)
        releases = tuple(sorted(loaded - desired))
        for page_index in releases:
            self.clear_document_page(page_index)
        if releases:
            self.documentPagesReleased.emit(releases)
        requests = tuple(sorted(desired - set(self._document_page_items)))
        if requests:
            self.documentPagesRequested.emit(requests)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self.pointerMoved.emit(round(event.position().y()))
        if self._annotation_tool and self._draft_item is not None:
            point = self._bounded_scene_point(event.position().toPoint())
            if self._annotation_tool == "freehand":
                self._draft_item.add_point(point)
            else:
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
            if self._annotation_tool == "select":
                target = self._markup_item_at(point)
                if target is not None:
                    self._select_annotation_item(target)
                    super().mousePressEvent(event)
                else:
                    self.scene().clearSelection()
                    self.annotationSelectionCleared.emit()
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
                points=(point,) if self._annotation_tool == "freehand" else (),
            )
            self._draft_item = MarkupItem(
                spec,
                self._source_qimage,
                draft=True,
                changed=self._annotation_item_changed,
                change_started=self._begin_annotation_change,
            )
            self.scene().addItem(self._draft_item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
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
                self._select_annotation_item(self._draft_item)
            else:
                self.scene().removeItem(self._draft_item)
            self._draft_item = None
            self._annotation_start = None
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
                    self._annotation_tool = "select"
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
            if isinstance(candidate, MarkupItem) and candidate is not self._draft_item:
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
        self._annotation_tool = "select"
        self._annotation_item_changed(item)

    def _annotation_item_changed(self, item: MarkupItem) -> None:
        self._commit_pending_annotation_history()
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
        editor.editingFinished.connect(self._commit_inline_text_editor)
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
            f"QLineEdit {{color: {color}; background: rgba(18, 21, 25, 238);"
            f"border: 2px solid {color}; border-radius: 7px; padding: 5px 8px;}}"
        )
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(max(8, size))
        font.setWeight(QFont.Weight.DemiBold)
        editor.setFont(font)
        metrics = editor.fontMetrics()
        desired_width = max(
            220,
            min(520, metrics.horizontalAdvance(editor.text() or "输入文字") + 52),
        )
        desired_height = max(38, metrics.height() + 16)
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
            min(self.sceneRect().right(), point.x() + scene_width),
            min(self.sceneRect().bottom(), point.y() + scene_height),
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
            self._select_annotation_item(self._annotation_items[-1])
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

    def _bounded_scene_point(self, viewport_point: QPoint) -> QPointF:
        point = self.mapToScene(viewport_point)
        rect = self.sceneRect()
        return QPointF(
            max(rect.left(), min(rect.right(), point.x())),
            max(rect.top(), min(rect.bottom(), point.y())),
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
    colorChanged = Signal(str)
    sizeChanged = Signal(int)
    undoRequested = Signal()
    cancelRequested = Signal()
    replaceRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("markupToolbar")
        self._active_tool = "rectangle"
        self._property_kind = "rectangle"
        self._shape_size = 6
        self._font_size = 20
        self.setStyleSheet(
            f"""
            #markupToolbar {{background: {SURFACE_HIGH}; border: 1px solid {BORDER};
                            border-radius: 15px;}}
            QPushButton {{color: {TEXT}; background: transparent; border: 0; border-radius: 9px;
                          padding: 6px 11px; font: 12px 'Microsoft YaHei UI';}}
            QPushButton:hover {{background: #303640;}}
            QPushButton:checked {{background: #3d5266; color: white;}}
            QLineEdit, QSpinBox {{color: {TEXT}; background: {SURFACE};
                                  border: 1px solid {BORDER}; border-radius: 8px;
                                  padding: 5px 8px; font: 12px 'Microsoft YaHei UI';}}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(3)
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
                lambda checked, value=tool: self._select_tool(value) if checked else None
            )
            tools_layout.addWidget(button)
            self.tool_buttons[tool] = button
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        for button in self.tool_buttons.values():
            self.tool_group.addButton(button)
        self.rect_button = self.tool_buttons["rectangle"]
        self.arrow_button = self.tool_buttons["arrow"]
        layout.addLayout(tools_layout)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(2)
        self.color_button = QPushButton("颜色", self)
        self.color_button.setToolTip("选择标注颜色")
        self.color_button.clicked.connect(self._choose_color)
        action_layout.addWidget(self.color_button)
        self.size_spin = QSpinBox(self)
        self.size_spin.setRange(2, 32)
        self.size_spin.setValue(6)
        self.size_spin.setPrefix("大小 ")
        self.size_spin.setToolTip("调整线宽、字号或效果强度")
        self.size_spin.valueChanged.connect(self._size_changed)
        action_layout.addWidget(self.size_spin)
        action_layout.addStretch(1)
        for text, signal in (
            ("撤销", self.undoRequested),
            ("取消修改", self.cancelRequested),
            ("替换原图", self.replaceRequested),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(signal.emit)
            action_layout.addWidget(button)
            if text == "替换原图":
                self.replace_button = button
        layout.addLayout(action_layout)

        self._color = QColor("#ff4c48")
        self._update_color_button()

    def set_read_only_source(self, read_only: bool) -> None:
        """Use an export action for document formats that cannot be overwritten safely."""

        if read_only:
            self.replace_button.setText("导出副本")
            self.replace_button.setToolTip("源格式只读，将当前编辑结果导出为普通图片")
            self.replace_button.setAccessibleName("导出编辑副本")
        else:
            self.replace_button.setText("替换原图")
            self.replace_button.setToolTip("确认后用当前编辑结果替换原文件")
            self.replace_button.setAccessibleName("替换原图")

    def _select_tool(self, tool: str) -> None:
        button = self.tool_buttons.get(tool)
        if button is None:
            return
        if self._property_kind == "text":
            self._font_size = self.size_spin.value()
        else:
            self._shape_size = self.size_spin.value()
        self._active_tool = tool
        if tool != "select":
            self._property_kind = tool
        button.setChecked(True)
        self.size_spin.blockSignals(True)
        if self._property_kind == "text":
            self.size_spin.setRange(8, 96)
            self.size_spin.setPrefix("字号 ")
            self.size_spin.setSuffix(" px")
            self.size_spin.setValue(self._font_size)
        else:
            self.size_spin.setRange(2, 32)
            self.size_spin.setPrefix("大小 ")
            self.size_spin.setSuffix("")
            self.size_spin.setValue(self._shape_size)
        self.size_spin.blockSignals(False)
        controls_enabled = tool != "select"
        self.color_button.setEnabled(controls_enabled)
        self.size_spin.setEnabled(controls_enabled)
        self.toolChanged.emit(tool)
        self.sizeChanged.emit(self.size_spin.value())

    def select_tool(self, tool: str) -> None:
        self._select_tool(tool)

    def sync_annotation(self, tool: str, color: str, size: int) -> None:
        if tool not in self.tool_buttons:
            return
        self._active_tool = "select"
        self._property_kind = tool
        self.tool_buttons["select"].setChecked(True)
        self.size_spin.blockSignals(True)
        if tool == "text":
            self._font_size = max(8, min(96, int(size)))
            self.size_spin.setRange(8, 96)
            self.size_spin.setPrefix("字号 ")
            self.size_spin.setSuffix(" px")
            self.size_spin.setValue(self._font_size)
        else:
            self._shape_size = max(2, min(32, int(size)))
            self.size_spin.setRange(2, 32)
            self.size_spin.setPrefix("大小 ")
            self.size_spin.setSuffix("")
            self.size_spin.setValue(self._shape_size)
        self.size_spin.blockSignals(False)
        self.color_button.setEnabled(True)
        self.size_spin.setEnabled(True)
        candidate = QColor(color)
        if candidate.isValid():
            self._color = candidate
            self._update_color_button()

    def clear_annotation_selection(self) -> None:
        if self._active_tool != "select":
            return
        self.color_button.setEnabled(False)
        self.size_spin.setEnabled(False)

    def set_size(self, size: int) -> None:
        bounded = max(self.size_spin.minimum(), min(self.size_spin.maximum(), int(size)))
        if self._property_kind == "text":
            self._font_size = bounded
        else:
            self._shape_size = bounded
        self.size_spin.setValue(bounded)

    def _size_changed(self, size: int) -> None:
        if self._property_kind == "text":
            self._font_size = int(size)
        else:
            self._shape_size = int(size)
        self.sizeChanged.emit(int(size))

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(self._color, self, "选择标注颜色")
        if not color.isValid():
            return
        self._color = color
        self._update_color_button()
        self.colorChanged.emit(color.name())

    def _update_color_button(self) -> None:
        self.color_button.setStyleSheet(
            f"QPushButton {{color: white; background: {self._color.name()};}}"
        )


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
