"""Lazy-loaded Qt tools for single-image conversion, resizing and cropping."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from image_ops import ExportOptions, estimate_save_size, extension_for_format

from .crop_presets import (
    MAX_DIMENSION,
    CropPresetCatalog,
    CropRatioPreset,
    load_crop_preset_catalog,
    save_crop_preset_catalog,
)
from .qt_viewer_widgets import ViewerCheckBox

FORMAT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("常用格式", ("PNG", "JPEG", "WEBP", "AVIF", "TIFF", "BMP")),
    ("游戏与 HDR", ("TGA", "DDS", "EXR", "HDR", "KTX2")),
    ("动画与图标", ("GIF", "APNG", "ICO")),
    ("位图外观容器", ("SVG", "AI", "EPS", "EMF", "WMF")),
)
TOOL_OUTPUT_FORMATS = tuple(
    output_format for _group_name, formats in FORMAT_GROUPS for output_format in formats
)
FORMAT_LABELS = {
    "PNG": "PNG\n透明与无损",
    "JPEG": "JPG\n照片与分享",
    "WEBP": "WebP\n小体积网页",
    "AVIF": "AVIF\n高压缩率",
    "BMP": "BMP\n兼容位图",
    "TIFF": "TIFF\n归档与印刷",
    "TGA": "TGA\n游戏贴图",
    "DDS": "DDS\nBC 压缩贴图",
    "EXR": "EXR\n线性高动态",
    "HDR": "HDR\n环境与灯光",
    "KTX2": "KTX2\n现代纹理",
    "GIF": "GIF\n动画与兼容",
    "APNG": "APNG\n透明动画",
    "ICO": "ICO\n多尺寸图标",
    "SVG": "SVG\n位图容器",
    "AI": "AI\n位图容器",
    "EPS": "EPS\n位图容器",
    "EMF": "EMF\nWindows 容器",
    "WMF": "WMF\nWindows 容器",
}


@dataclass(frozen=True)
class ConversionRequest:
    output_format: str
    target_path: Path
    quality: int
    open_after: bool
    source_paths: tuple[Path, ...] = ()
    output_directory: Path | None = None
    filename_suffix: str = "_转换后"
    conflict_policy: str = "unique"
    target_size_kb: int | None = None
    webp_lossless: bool = False
    png_palette_colors: int | None = None
    tiff_compression: str = "raw"
    preserve_metadata: bool = True
    background_color: str = "#ffffff"
    animation_mode: str = "preserve"
    tga_rle: bool = True
    dds_pixel_format: str = "DXT5"
    generate_mipmaps: bool = True
    ico_sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
    ktx2_mode: str = "uastc"


@dataclass(frozen=True)
class ImageEditRequest:
    crop_box: tuple[int, int, int, int] | None
    width: int
    height: int
    output_directory: Path
    output_format: str = "PNG"
    quality: int = 92
    source_paths: tuple[Path, ...] = ()
    filename_prefix: str = ""
    filename_suffix: str = "_edited"
    conflict_policy: str = "unique"
    target_size_kb: int | None = None
    crop_aspect_ratio: float | None = None
    resize_enabled: bool = False
    animation_mode: str = "preserve"
    tga_rle: bool = True
    dds_pixel_format: str = "DXT5"
    generate_mipmaps: bool = True
    ico_sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
    ktx2_mode: str = "uastc"
    source_crop_boxes: tuple[tuple[Path, tuple[int, int, int, int] | None], ...] = ()


@dataclass
class _BatchSourceState:
    original_size: tuple[int, int]
    base_box: tuple[int, int, int, int]
    crop_box: tuple[int, int, int, int] | None = None
    working_revision: int = 0


def _dialog_style(colors: dict[str, str]) -> str:
    return f"""
        QDialog {{
            color: {colors["text"]}; background: {colors["surface"]};
            font: 13px 'Microsoft YaHei UI';
        }}
        QFrame#conversionPanel, QFrame#creationPanel, QFrame#imageEditWorkspace {{
            color: {colors["text"]}; background: {colors["surface"]};
            font: 13px 'Microsoft YaHei UI';
        }}
        QWidget#conversionContent {{ background: transparent; }}
        QWidget#imageToolSettings {{ background: transparent; }}
        QLabel {{ color: {colors["text"]}; background: transparent; }}
        QLabel#mutedLabel {{ color: {colors["muted"]}; }}
        QFrame#toolCard, QFrame#toolPanel, QFrame#outputBar, QFrame#ratioLockCard,
        QFrame#workflowCard, QFrame#customRatioCard, QFrame#ratioValueCard,
        QFrame#defaultConfigCard, QFrame#compactSection {{
            background: {colors["surface_high"]}; border: 1px solid {colors["border"]};
            border-radius: 12px;
        }}
        QLabel#sectionTitle {{
            color: {colors["text"]}; font: 700 14px 'Microsoft YaHei UI';
            padding-top: 3px;
        }}
        QFrame#ratioPresetPanel {{
            background: {colors["selected"]}; border: 1px solid {colors["accent"]};
            border-radius: 11px;
        }}
        QFrame#defaultConfigCard[active="true"] {{
            background: {colors["selected"]}; border-color: {colors["accent"]};
        }}
        QLabel#customRatioPreview {{
            color: {colors["accent"]}; background: {colors["selected"]};
            border: 1px solid {colors["accent"]}; border-radius: 9px;
            padding: 7px 10px; font-weight: 600;
        }}
        QLabel#defaultConfigStatus {{
            color: {colors["muted"]}; background: {colors["surface"]};
            border: 1px solid {colors["border"]}; border-radius: 8px;
            padding: 3px 8px; font-size: 11px;
        }}
        QLabel#defaultConfigStatus[active="true"] {{
            color: {colors["accent"]}; background: {colors["selected"]};
            border-color: {colors["accent"]};
        }}
        QFrame#apiSummaryCard {{
            background: {colors["surface_high"]}; border: 1px solid {colors["border"]};
            border-radius: 12px;
        }}
        QLabel#apiBadge {{
            color: {colors["accent"]}; background: {colors["selected"]};
            border: 1px solid {colors["accent"]}; border-radius: 10px;
            font: 700 12px 'Microsoft YaHei UI';
        }}
        QLabel#apiStatusBadge {{
            color: {colors["muted"]}; background: {colors["surface"]};
            border: 1px solid {colors["border"]}; border-radius: 8px;
            padding: 2px 7px; font-size: 11px;
        }}
        QLabel#apiStatusBadge[ready="true"] {{
            color: {colors["accent"]}; background: {colors["selected"]};
            border-color: {colors["accent"]};
        }}
        QPushButton#apiSummaryToggle {{
            color: {colors["text"]}; background: transparent;
            border: 1px solid {colors["border"]}; border-radius: 8px;
            min-height: 30px; padding: 0 10px;
        }}
        QPushButton#apiSummaryToggle:hover {{
            color: {colors["accent"]}; background: {colors["hover"]};
            border-color: {colors["accent"]};
        }}
        QSplitter#imageEditSplitter::handle {{
            background: {colors["border"]}; border-radius: 2px;
            margin: 6px 2px;
        }}
        QSplitter#imageEditSplitter::handle:hover,
        QSplitter#imageEditSplitter::handle:pressed {{
            background: {colors["accent"]};
        }}
        QFrame#ratioLockCard[active="true"] {{
            background: {colors["selected"]}; border-color: {colors["accent"]};
        }}
        QLineEdit, QTextEdit, QComboBox, QSpinBox {{
            color: {colors["text"]}; background: {colors["surface_high"]};
            border: 1px solid {colors["border"]}; border-radius: 8px;
            min-height: 32px; padding: 0 9px;
            selection-background-color: {colors["selected"]};
        }}
        QTextEdit {{ padding: 7px 9px; }}
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border-color: {colors["accent"]};
        }}
        QComboBox::drop-down {{ border: 0; width: 24px; }}
        QComboBox QAbstractItemView, QAbstractItemView#csimageComboPopup {{
            color: {colors["text"]}; background: {colors["surface_high"]};
            border: 1px solid {colors["border"]}; outline: 0;
            selection-color: {colors["text"]};
            selection-background-color: {colors["selected"]};
        }}
        QPushButton {{
            color: {colors["text"]}; background: {colors["surface_high"]};
            border: 1px solid {colors["border"]}; border-radius: 9px;
            min-height: 32px; padding: 0 14px;
        }}
        QPushButton:hover {{ background: {colors["hover"]}; }}
        QPushButton:pressed {{ background: {colors["pressed"]}; }}
        QPushButton:disabled {{
            color: {colors["muted"]}; background: {colors["surface"]};
            border-color: {colors["border"]};
        }}
        QPushButton#primaryButton {{
            color: {colors["accent_text"]}; background: {colors["accent"]};
            border-color: {colors["accent"]}; font-weight: 600;
        }}
        QPushButton#primaryButton:disabled {{
            color: {colors["muted"]}; background: {colors["surface"]};
            border-color: {colors["border"]};
        }}
        QPushButton#tabButton:checked {{
            color: {colors["accent_text"]}; background: {colors["accent"]};
            border-color: {colors["accent"]};
        }}
        QPushButton#ratioOption {{ min-height: 36px; padding: 0 8px; }}
        QPushButton#ratioOption:checked {{
            color: {colors["accent"]}; background: {colors["selected"]};
            border-color: {colors["accent"]}; font-weight: 600;
        }}
        QPushButton#sizePresetButton {{
            min-height: 44px; padding: 0 12px; border-radius: 11px;
            font-weight: 600;
        }}
        QPushButton#sizePresetButton:hover {{
            color: {colors["accent"]}; border-color: {colors["accent"]};
            background: {colors["hover"]};
        }}
        QPushButton#formatCard {{
            min-height: 52px; padding: 5px 8px; text-align: left;
            font-weight: 600; line-height: 1.25;
        }}
        QPushButton#formatCard:checked {{
            color: {colors["accent"]}; background: {colors["selected"]};
            border-color: {colors["accent"]};
        }}
        QPushButton#linkButton {{
            color: {colors["accent"]}; background: transparent; border: 0;
            padding: 4px 2px; text-align: left;
        }}
        QPushButton#sectionToggle {{
            color: {colors["text"]}; background: {colors["surface_high"]};
            border: 1px solid {colors["border"]}; border-radius: 10px;
            min-height: 38px; padding: 0 12px; text-align: left; font-weight: 600;
        }}
        QPushButton#sectionToggle:hover {{
            background: {colors["hover"]}; border-color: {colors["accent"]};
        }}
        QFrame#localAiCard {{
            background: {colors["surface_high"]}; border: 1px solid {colors["border"]};
            border-radius: 12px;
        }}
        QLabel#localAiBadge {{
            color: {colors["accent"]}; background: {colors["selected"]};
            border: 1px solid {colors["accent"]}; border-radius: 8px;
            padding: 2px 7px; font: 700 11px 'Microsoft YaHei UI';
        }}
        QPushButton#localAiAction {{ min-height: 36px; font-weight: 600; }}
        QPushButton#resolutionPreset {{
            min-height: 34px; padding: 0 8px; font-weight: 600;
        }}
        QPushButton#resolutionPreset:checked {{
            color: {colors["accent"]}; background: {colors["selected"]};
            border-color: {colors["accent"]};
        }}
        QListWidget {{
            color: {colors["text"]}; background: {colors["surface"]};
            border: 1px solid {colors["border"]}; border-radius: 8px;
            padding: 4px;
        }}
        QListWidget::item {{padding: 5px 7px; border-radius: 5px;}}
        QListWidget::item:selected {{background: {colors["selected"]};}}
        QRadioButton {{color: {colors["text"]}; spacing: 7px;}}
        QProgressBar {{
            color: {colors["text"]}; background: {colors["border"]}; border: 0;
            border-radius: 3px; min-height: 6px; max-height: 6px; text-align: center;
        }}
        QProgressBar::chunk {{background: {colors["accent"]}; border-radius: 3px;}}
        QCheckBox {{ color: {colors["text"]}; spacing: 8px; }}
        QCheckBox#ratioLockSwitch {{ font-weight: 600; spacing: 10px; }}
        QCheckBox#defaultConfigSwitch {{ font-weight: 700; spacing: 10px; }}
        QCheckBox#ratioLockSwitch::indicator,
        QCheckBox#defaultConfigSwitch::indicator {{
            width: 38px; height: 20px; border-radius: 10px;
            background: {colors["border"]}; border: 1px solid {colors["border"]};
        }}
        QCheckBox#ratioLockSwitch::indicator:checked,
        QCheckBox#defaultConfigSwitch::indicator:checked {{
            background: {colors["accent"]}; border-color: {colors["accent"]};
        }}
        QSlider::groove:horizontal {{
            background: {colors["border"]}; height: 4px; border-radius: 2px;
        }}
        QSlider::sub-page:horizontal {{
            background: {colors["accent"]}; border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {colors["text"]}; border: 2px solid {colors["accent"]};
            width: 15px; height: 15px; margin: -6px 0; border-radius: 8px;
        }}
    """


def _style_combo_popup(combo: QComboBox, colors: dict[str, str]) -> None:
    """Keep combo popups readable when Windows applies its native light palette."""

    view = combo.view()
    view.setObjectName("csimageComboPopup")
    view.setAlternatingRowColors(False)
    view.setStyleSheet(
        f"""
        QAbstractItemView {{
            color: {colors["text"]};
            background-color: {colors["surface_high"]};
            border: 1px solid {colors["border"]};
            outline: 0;
            padding: 4px;
            selection-color: {colors["text"]};
            selection-background-color: {colors["selected"]};
        }}
        QAbstractItemView::item {{
            color: {colors["text"]};
            background-color: {colors["surface_high"]};
            min-height: 30px;
            padding: 0 8px;
            border-radius: 6px;
        }}
        QAbstractItemView::item:hover,
        QAbstractItemView::item:selected {{
            color: {colors["text"]};
            background-color: {colors["selected"]};
        }}
        QScrollBar:vertical {{
            background: {colors["surface_high"]}; width: 9px; margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {colors["border"]}; min-height: 24px; border-radius: 4px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height: 0;}}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{background: transparent;}}
        """
    )
    palette = view.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface_high"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["surface_high"]))
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["surface_high"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["selected"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["text"]))
    view.setPalette(palette)
    view.viewport().setAutoFillBackground(True)
    view.viewport().setPalette(palette)


def _configure_direct_number_input(spin: QSpinBox) -> None:
    """Use a clean text-first number field instead of ambiguous tiny steppers."""

    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spin.setAccelerated(True)
    spin.setToolTip("直接输入数值；也可使用上下方向键或鼠标滚轮调整。")


class CustomCropRatioDialog(QDialog):
    """Focused numeric editor for a custom width-to-height crop ratio."""

    def __init__(
        self,
        width_ratio: int,
        height_ratio: int,
        colors: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("自定义裁剪比例")
        self.setModal(True)
        self.setFixedWidth(420)
        self.setStyleSheet(_dialog_style(colors))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        title = QLabel("自定义裁剪比例", self)
        title.setStyleSheet("font: 700 18px 'Microsoft YaHei UI';")
        hint = QLabel("输入宽与高的比例数值，系统会自动约分。", self)
        hint.setObjectName("mutedLabel")
        layout.addWidget(title)
        layout.addWidget(hint)

        ratio_card = QFrame(self)
        ratio_card.setObjectName("customRatioCard")
        card_layout = QVBoxLayout(ratio_card)
        card_layout.setContentsMargins(14, 13, 14, 13)
        card_layout.setSpacing(10)
        ratio_row = QHBoxLayout()
        ratio_row.setSpacing(10)
        self.width_ratio_spin = QSpinBox(self)
        self.height_ratio_spin = QSpinBox(self)
        for spin in (self.width_ratio_spin, self.height_ratio_spin):
            _configure_direct_number_input(spin)
            spin.setRange(1, 10000)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setMinimumHeight(42)
        self.width_ratio_spin.setValue(width_ratio)
        self.height_ratio_spin.setValue(height_ratio)

        width_box = QVBoxLayout()
        width_label = QLabel("宽", ratio_card)
        width_label.setObjectName("mutedLabel")
        width_box.addWidget(width_label)
        width_box.addWidget(self.width_ratio_spin)
        separator = QLabel(":", self)
        separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        separator.setStyleSheet("font: 700 22px 'Microsoft YaHei UI'; padding-top: 19px;")
        height_box = QVBoxLayout()
        height_label = QLabel("高", ratio_card)
        height_label.setObjectName("mutedLabel")
        height_box.addWidget(height_label)
        height_box.addWidget(self.height_ratio_spin)
        ratio_row.addLayout(width_box, 1)
        ratio_row.addWidget(separator)
        ratio_row.addLayout(height_box, 1)
        card_layout.addLayout(ratio_row)

        self.ratio_preview = QLabel("", ratio_card)
        self.ratio_preview.setObjectName("customRatioPreview")
        self.ratio_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.ratio_preview)
        layout.addWidget(ratio_card)

        preset_label = QLabel("常用比例", self)
        preset_label.setObjectName("mutedLabel")
        layout.addWidget(preset_label)
        preset_row = QHBoxLayout()
        preset_row.setSpacing(7)
        for label, width, height in (
            ("1:1", 1, 1),
            ("4:3", 4, 3),
            ("16:9", 16, 9),
            ("9:16", 9, 16),
        ):
            button = QPushButton(label, self)
            button.clicked.connect(lambda _checked=False, w=width, h=height: self._set_ratio(w, h))
            preset_row.addWidget(button, 1)
        layout.addLayout(preset_row)

        actions = QHBoxLayout()
        cancel_button = QPushButton("取消", self)
        apply_button = QPushButton("应用比例", self)
        apply_button.setObjectName("primaryButton")
        cancel_button.clicked.connect(self.reject)
        apply_button.clicked.connect(self.accept)
        actions.addWidget(cancel_button, 1)
        actions.addWidget(apply_button, 1)
        layout.addLayout(actions)
        self.width_ratio_spin.valueChanged.connect(self._update_ratio_preview)
        self.height_ratio_spin.valueChanged.connect(self._update_ratio_preview)
        self._update_ratio_preview()

    def _set_ratio(self, width_ratio: int, height_ratio: int) -> None:
        self.width_ratio_spin.setValue(width_ratio)
        self.height_ratio_spin.setValue(height_ratio)

    def _update_ratio_preview(self, *_args: object) -> None:
        width_ratio, height_ratio = self.ratio_values()
        divisor = gcd(width_ratio, height_ratio)
        width_ratio //= divisor
        height_ratio //= divisor
        orientation = (
            "横向"
            if width_ratio > height_ratio
            else "竖向"
            if width_ratio < height_ratio
            else "正方形"
        )
        self.ratio_preview.setText(f"最终比例  {width_ratio}:{height_ratio}  ·  {orientation}")

    def ratio_values(self) -> tuple[int, int]:
        return self.width_ratio_spin.value(), self.height_ratio_spin.value()


class OutputSizePresetDialog(QDialog):
    """Editor for one exact output size constrained to a crop ratio."""

    def __init__(
        self,
        preset: CropRatioPreset,
        size: tuple[int, int] | None,
        colors: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._preset = preset
        self._syncing = False
        self.setWindowTitle("编辑输出尺寸" if size else "新增输出尺寸")
        self.setModal(True)
        self.setFixedWidth(420)
        self.setStyleSheet(_dialog_style(colors))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
        title = QLabel(self.windowTitle(), self)
        title.setStyleSheet("font: 700 18px 'Microsoft YaHei UI';")
        hint = QLabel(f"此尺寸归属于 {preset.label}，宽高会按比例联动。", self)
        hint.setObjectName("mutedLabel")
        layout.addWidget(title)
        layout.addWidget(hint)

        card = QFrame(self)
        card.setObjectName("customRatioCard")
        card_layout = QFormLayout(card)
        card_layout.setContentsMargins(14, 13, 14, 13)
        card_layout.setHorizontalSpacing(12)
        card_layout.setVerticalSpacing(10)
        self.width_spin = QSpinBox(card)
        self.height_spin = QSpinBox(card)
        for spin in (self.width_spin, self.height_spin):
            _configure_direct_number_input(spin)
            spin.setRange(1, MAX_DIMENSION)
            spin.setMinimumHeight(40)
        if size is None:
            width = 1920 if preset.width >= preset.height else 1080
            height = max(1, round(width * preset.height / preset.width))
        else:
            width, height = size
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)
        card_layout.addRow("宽度", self.width_spin)
        card_layout.addRow("高度", self.height_spin)
        layout.addWidget(card)

        actions = QHBoxLayout()
        cancel_button = QPushButton("取消", self)
        save_button = QPushButton("保存尺寸", self)
        save_button.setObjectName("primaryButton")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)
        actions.addWidget(cancel_button, 1)
        actions.addWidget(save_button, 1)
        layout.addLayout(actions)
        self.width_spin.valueChanged.connect(self._width_changed)
        self.height_spin.valueChanged.connect(self._height_changed)

    def _width_changed(self, width: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.height_spin.setValue(max(1, round(width * self._preset.height / self._preset.width)))
        self._syncing = False

    def _height_changed(self, height: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self.width_spin.setValue(max(1, round(height * self._preset.width / self._preset.height)))
        self._syncing = False

    def size_values(self) -> tuple[int, int]:
        return self.width_spin.value(), self.height_spin.value()

    def accept(self) -> None:
        width, height = self.size_values()
        if width * self._preset.height != height * self._preset.width:
            QMessageBox.warning(
                self,
                "尺寸不符合比例",
                f"{width} × {height} 不是精确的 {self._preset.label} 比例。",
            )
            return
        super().accept()


class CropPresetManagerDialog(QDialog):
    """Edit and exchange a portable crop-ratio/output-size catalog."""

    def __init__(
        self,
        catalog: CropPresetCatalog,
        colors: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._colors = dict(colors)
        self._presets = list(catalog.presets)
        self.setWindowTitle("比例与尺寸预设")
        self.setModal(True)
        self.resize(760, 540)
        self.setMinimumSize(680, 480)
        self.setStyleSheet(_dialog_style(colors))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)
        title = QLabel("比例与尺寸预设", self)
        title.setStyleSheet("font: 700 19px 'Microsoft YaHei UI';")
        hint = QLabel(
            "自由添加裁剪比例，并为每个比例维护常用输出尺寸。配置可导出到其他电脑。",
            self,
        )
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        ratio_card = QFrame(splitter)
        ratio_card.setObjectName("toolCard")
        ratio_layout = QVBoxLayout(ratio_card)
        ratio_layout.setContentsMargins(12, 12, 12, 12)
        ratio_layout.setSpacing(8)
        ratio_heading = QLabel("裁剪比例", ratio_card)
        ratio_heading.setStyleSheet("font-weight: 700;")
        ratio_layout.addWidget(ratio_heading)
        self.ratio_list = QListWidget(ratio_card)
        ratio_layout.addWidget(self.ratio_list, 1)
        ratio_actions = QHBoxLayout()
        add_ratio = QPushButton("＋ 新增", ratio_card)
        edit_ratio = QPushButton("编辑", ratio_card)
        delete_ratio = QPushButton("删除", ratio_card)
        add_ratio.clicked.connect(self._add_ratio)
        edit_ratio.clicked.connect(self._edit_ratio)
        delete_ratio.clicked.connect(self._delete_ratio)
        ratio_actions.addWidget(add_ratio)
        ratio_actions.addWidget(edit_ratio)
        ratio_actions.addWidget(delete_ratio)
        ratio_layout.addLayout(ratio_actions)

        size_card = QFrame(splitter)
        size_card.setObjectName("toolCard")
        size_layout = QVBoxLayout(size_card)
        size_layout.setContentsMargins(12, 12, 12, 12)
        size_layout.setSpacing(8)
        self.size_heading = QLabel("输出尺寸", size_card)
        self.size_heading.setStyleSheet("font-weight: 700;")
        size_layout.addWidget(self.size_heading)
        self.size_list = QListWidget(size_card)
        size_layout.addWidget(self.size_list, 1)
        size_actions = QHBoxLayout()
        add_size = QPushButton("＋ 新增", size_card)
        edit_size = QPushButton("编辑", size_card)
        delete_size = QPushButton("删除", size_card)
        add_size.clicked.connect(self._add_size)
        edit_size.clicked.connect(self._edit_size)
        delete_size.clicked.connect(self._delete_size)
        size_actions.addWidget(add_size)
        size_actions.addWidget(edit_size)
        size_actions.addWidget(delete_size)
        size_layout.addLayout(size_actions)
        splitter.addWidget(ratio_card)
        splitter.addWidget(size_card)
        splitter.setSizes([290, 390])
        outer.addWidget(splitter, 1)

        exchange_row = QHBoxLayout()
        import_button = QPushButton("导入配置", self)
        export_button = QPushButton("导出配置", self)
        reset_button = QPushButton("恢复默认", self)
        import_button.clicked.connect(self._import_catalog)
        export_button.clicked.connect(self._export_catalog)
        reset_button.clicked.connect(self._reset_catalog)
        exchange_row.addWidget(import_button)
        exchange_row.addWidget(export_button)
        exchange_row.addWidget(reset_button)
        exchange_row.addStretch(1)
        outer.addLayout(exchange_row)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QPushButton("取消", self)
        save_button = QPushButton("保存配置", self)
        save_button.setObjectName("primaryButton")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self.accept)
        actions.addWidget(cancel_button)
        actions.addWidget(save_button)
        outer.addLayout(actions)

        self.ratio_list.currentRowChanged.connect(self._refresh_sizes)
        self._refresh_ratios()

    def catalog(self) -> CropPresetCatalog:
        return CropPresetCatalog(tuple(self._presets))

    def _refresh_ratios(self, selected: int | None = None) -> None:
        previous = self.ratio_list.currentRow() if selected is None else selected
        self.ratio_list.clear()
        for preset in self._presets:
            count = f"{len(preset.sizes)} 个尺寸" if preset.sizes else "暂无尺寸"
            item = QListWidgetItem(f"{preset.label}    ·    {count}")
            item.setData(Qt.ItemDataRole.UserRole, preset.label)
            self.ratio_list.addItem(item)
        if self._presets:
            self.ratio_list.setCurrentRow(min(max(0, previous), len(self._presets) - 1))
        else:
            self._refresh_sizes(-1)

    def _refresh_sizes(self, row: int) -> None:
        self.size_list.clear()
        if row < 0 or row >= len(self._presets):
            self.size_heading.setText("输出尺寸")
            return
        preset = self._presets[row]
        self.size_heading.setText(f"{preset.label} 的输出尺寸")
        for width, height in preset.sizes:
            item = QListWidgetItem(f"{width} × {height}")
            item.setData(Qt.ItemDataRole.UserRole, (width, height))
            self.size_list.addItem(item)
        if preset.sizes:
            self.size_list.setCurrentRow(0)

    def _add_ratio(self) -> None:
        dialog = CustomCropRatioDialog(7, 5, self._colors, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        width, height = dialog.ratio_values()
        preset = CropRatioPreset(width, height)
        if any(item.label == preset.label for item in self._presets):
            QMessageBox.warning(self, "比例已存在", f"比例 {preset.label} 已在配置中。")
            return
        self._presets.append(preset)
        self._refresh_ratios(len(self._presets) - 1)

    def _edit_ratio(self) -> None:
        row = self.ratio_list.currentRow()
        if row < 0:
            return
        current = self._presets[row]
        dialog = CustomCropRatioDialog(current.width, current.height, self._colors, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        width, height = dialog.ratio_values()
        updated = CropRatioPreset(width, height)
        if updated.label != current.label and any(
            item.label == updated.label for index, item in enumerate(self._presets) if index != row
        ):
            QMessageBox.warning(self, "比例已存在", f"比例 {updated.label} 已在配置中。")
            return
        if updated.label != current.label and current.sizes:
            answer = QMessageBox.question(
                self,
                "更改比例",
                "现有尺寸与新比例不一致，更改后将清空这些尺寸。是否继续？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        sizes = current.sizes if updated.label == current.label else ()
        self._presets[row] = CropRatioPreset(updated.width, updated.height, sizes)
        self._refresh_ratios(row)

    def _delete_ratio(self) -> None:
        row = self.ratio_list.currentRow()
        if row < 0:
            return
        preset = self._presets[row]
        answer = QMessageBox.question(
            self,
            "删除比例",
            f"确定删除比例 {preset.label} 及其 {len(preset.sizes)} 个尺寸吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._presets[row]
        self._refresh_ratios(min(row, len(self._presets) - 1))

    def _add_size(self) -> None:
        row = self.ratio_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "先选择比例", "请先选择或新增一个裁剪比例。")
            return
        preset = self._presets[row]
        dialog = OutputSizePresetDialog(preset, None, self._colors, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        size = dialog.size_values()
        if size in preset.sizes:
            QMessageBox.warning(self, "尺寸已存在", f"尺寸 {size[0]} × {size[1]} 已存在。")
            return
        self._presets[row] = CropRatioPreset(preset.width, preset.height, (*preset.sizes, size))
        self._refresh_ratios(row)
        self.size_list.setCurrentRow(len(self._presets[row].sizes) - 1)

    def _edit_size(self) -> None:
        ratio_row = self.ratio_list.currentRow()
        size_row = self.size_list.currentRow()
        if ratio_row < 0 or size_row < 0:
            return
        preset = self._presets[ratio_row]
        current = preset.sizes[size_row]
        dialog = OutputSizePresetDialog(preset, current, self._colors, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        size = dialog.size_values()
        if size != current and size in preset.sizes:
            QMessageBox.warning(self, "尺寸已存在", f"尺寸 {size[0]} × {size[1]} 已存在。")
            return
        sizes = list(preset.sizes)
        sizes[size_row] = size
        self._presets[ratio_row] = CropRatioPreset(preset.width, preset.height, tuple(sizes))
        self._refresh_ratios(ratio_row)
        self.size_list.setCurrentRow(size_row)

    def _delete_size(self) -> None:
        ratio_row = self.ratio_list.currentRow()
        size_row = self.size_list.currentRow()
        if ratio_row < 0 or size_row < 0:
            return
        preset = self._presets[ratio_row]
        sizes = list(preset.sizes)
        del sizes[size_row]
        self._presets[ratio_row] = CropRatioPreset(preset.width, preset.height, tuple(sizes))
        self._refresh_ratios(ratio_row)

    def _import_catalog(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入比例与尺寸配置",
            "",
            "CSImage 配置 (*.csimage-presets.json *.json);;JSON 文件 (*.json)",
        )
        if not path:
            return
        try:
            catalog = load_crop_preset_catalog(path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法导入", str(error))
            return
        self._presets = list(catalog.presets)
        self._refresh_ratios(0)
        QMessageBox.information(self, "导入完成", f"已载入 {len(self._presets)} 个比例。")

    def _export_catalog(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出比例与尺寸配置",
            "CSImage-比例尺寸预设.csimage-presets.json",
            "CSImage 配置 (*.csimage-presets.json);;JSON 文件 (*.json)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.casefold() != ".json":
            target = target.with_name(f"{target.name}.csimage-presets.json")
        try:
            save_crop_preset_catalog(target, self.catalog())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "无法导出", str(error))
            return
        QMessageBox.information(self, "导出完成", f"配置已保存到：\n{target}")

    def _reset_catalog(self) -> None:
        answer = QMessageBox.question(
            self,
            "恢复默认配置",
            "将丢弃当前窗口中的自定义比例和尺寸，是否继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._presets = list(CropPresetCatalog.defaults().presets)
        self._refresh_ratios(0)


class ImageConversionPanel(QFrame):
    """Progressive single-image panel that can expand into a small batch queue."""

    closeRequested = Signal()
    convertRequested = Signal(object)

    def __init__(
        self,
        source_path: Path,
        source_size: tuple[int, int],
        current_format: str,
        *,
        has_alpha: bool,
        estimate_image: Image.Image,
        colors: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.source_path = source_path.resolve()
        self.source_size = source_size
        self.current_format = current_format.upper()
        self.has_alpha = has_alpha
        self._sources = [self.source_path]
        self._custom_background = "#ffffff"
        self._estimate_image = estimate_image.copy()
        self._estimate_image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        self._busy = False
        self.setObjectName("conversionPanel")
        self.setMinimumWidth(390)
        self.setStyleSheet(_dialog_style(colors))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("转换图片", self)
        title.setStyleSheet("font: 700 19px 'Microsoft YaHei UI';")
        subtitle = QLabel("快速设置；添加更多图片后自动切换为批量转换", self)
        subtitle.setObjectName("mutedLabel")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        close_button = QPushButton("×", self)
        close_button.setAccessibleName("关闭转图面板")
        close_button.setFixedSize(32, 32)
        close_button.clicked.connect(self.closeRequested)
        header.addWidget(close_button)
        outer.addLayout(header)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea {background: transparent; border: 0;}")
        content = QWidget(scroll)
        content.setObjectName("conversionContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 4, 0)
        content_layout.setSpacing(11)

        self.source_card = QFrame(content)
        self.source_card.setObjectName("toolCard")
        source_layout = QVBoxLayout(self.source_card)
        source_layout.setContentsMargins(13, 11, 13, 11)
        source_layout.setSpacing(7)
        self.source_summary = QLabel(self.source_card)
        self.source_summary.setWordWrap(True)
        self.source_detail = QLabel(self.source_card)
        self.source_detail.setObjectName("mutedLabel")
        self.source_list = QListWidget(self.source_card)
        self.source_list.setFixedHeight(112)
        self.source_list.setVisible(False)
        source_actions = QHBoxLayout()
        self.add_sources_button = QPushButton("＋ 添加更多图片", self.source_card)
        self.add_sources_button.setObjectName("linkButton")
        self.remove_source_button = QPushButton("移除所选", self.source_card)
        self.remove_source_button.setObjectName("linkButton")
        self.remove_source_button.setVisible(False)
        source_actions.addWidget(self.add_sources_button)
        source_actions.addStretch(1)
        source_actions.addWidget(self.remove_source_button)
        source_layout.addWidget(self.source_summary)
        source_layout.addWidget(self.source_detail)
        source_layout.addWidget(self.source_list)
        source_layout.addLayout(source_actions)
        content_layout.addWidget(self.source_card)

        format_title = QLabel("输出格式", content)
        format_title.setStyleSheet("font-weight: 600;")
        content_layout.addWidget(format_title)
        self.format_group = QButtonGroup(self)
        self.format_group.setExclusive(True)
        self.format_buttons: dict[str, QPushButton] = {}
        for group_name, output_formats in FORMAT_GROUPS:
            group_label = QLabel(group_name, content)
            group_label.setObjectName("mutedLabel")
            group_label.setStyleSheet("font-weight: 600;")
            content_layout.addWidget(group_label)
            format_grid = QGridLayout()
            format_grid.setHorizontalSpacing(7)
            format_grid.setVerticalSpacing(7)
            for index, output_format in enumerate(output_formats):
                button = QPushButton(FORMAT_LABELS[output_format], content)
                button.setObjectName("formatCard")
                button.setCheckable(True)
                button.setProperty("outputFormat", output_format)
                button.clicked.connect(
                    lambda _checked=False, value=output_format: self._select_format(value)
                )
                self.format_group.addButton(button)
                self.format_buttons[output_format] = button
                format_grid.addWidget(button, index // 2, index % 2)
            content_layout.addLayout(format_grid)

        self.options_card = QFrame(content)
        self.options_card.setObjectName("toolCard")
        options_layout = QVBoxLayout(self.options_card)
        options_layout.setContentsMargins(13, 11, 13, 11)
        options_layout.setSpacing(9)

        self.quality_panel = QWidget(self.options_card)
        quality_layout = QVBoxLayout(self.quality_panel)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.setSpacing(6)
        quality_heading = QHBoxLayout()
        self.quality_label = QLabel("输出质量", self.quality_panel)
        self.quality_value = QLabel("88", self.quality_panel)
        self.quality_value.setStyleSheet("font-weight: 600;")
        quality_heading.addWidget(self.quality_label)
        quality_heading.addStretch(1)
        quality_heading.addWidget(self.quality_value)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal, self.quality_panel)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(88)
        preset_row = QHBoxLayout()
        for label, value in (("较小 65", 65), ("平衡 82", 82), ("高质量 92", 92)):
            preset = QPushButton(label, self.quality_panel)
            preset.clicked.connect(
                lambda _checked=False, amount=value: self.quality_slider.setValue(amount)
            )
            preset_row.addWidget(preset)
        quality_layout.addLayout(quality_heading)
        quality_layout.addWidget(self.quality_slider)
        quality_layout.addLayout(preset_row)
        options_layout.addWidget(self.quality_panel)

        self.webp_lossless = QCheckBox("WebP 无损模式", self.options_card)
        options_layout.addWidget(self.webp_lossless)

        self.target_size_row = QWidget(self.options_card)
        target_size_layout = QHBoxLayout(self.target_size_row)
        target_size_layout.setContentsMargins(0, 0, 0, 0)
        self.target_size_enabled = QCheckBox("限制文件大小", self.target_size_row)
        self.target_size_spin = QSpinBox(self.target_size_row)
        _configure_direct_number_input(self.target_size_spin)
        self.target_size_spin.setRange(10, 999_999)
        self.target_size_spin.setValue(500)
        self.target_size_spin.setSuffix(" KB")
        self.target_size_spin.setEnabled(False)
        target_size_layout.addWidget(self.target_size_enabled)
        target_size_layout.addStretch(1)
        target_size_layout.addWidget(self.target_size_spin)
        options_layout.addWidget(self.target_size_row)

        self.png_palette_row = QWidget(self.options_card)
        png_layout = QHBoxLayout(self.png_palette_row)
        png_layout.setContentsMargins(0, 0, 0, 0)
        png_layout.addWidget(QLabel("PNG 色彩", self.png_palette_row))
        self.png_palette = QComboBox(self.png_palette_row)
        palette_options: tuple[tuple[str, int | None], ...] = (
            ("真彩色", None),
            ("256 色", 256),
            ("128 色", 128),
            ("64 色", 64),
            ("32 色", 32),
        )
        for label, palette_value in palette_options:
            self.png_palette.addItem(label, palette_value)
        _style_combo_popup(self.png_palette, colors)
        png_layout.addWidget(self.png_palette, 1)
        options_layout.addWidget(self.png_palette_row)

        self.tiff_compression_row = QWidget(self.options_card)
        tiff_layout = QHBoxLayout(self.tiff_compression_row)
        tiff_layout.setContentsMargins(0, 0, 0, 0)
        tiff_layout.addWidget(QLabel("TIFF 压缩", self.tiff_compression_row))
        self.tiff_compression = QComboBox(self.tiff_compression_row)
        self.tiff_compression.addItem("无压缩", "raw")
        self.tiff_compression.addItem("LZW 无损", "tiff_lzw")
        self.tiff_compression.addItem("Deflate 无损", "tiff_adobe_deflate")
        _style_combo_popup(self.tiff_compression, colors)
        tiff_layout.addWidget(self.tiff_compression, 1)
        options_layout.addWidget(self.tiff_compression_row)

        self.animation_row = QWidget(self.options_card)
        animation_layout = QHBoxLayout(self.animation_row)
        animation_layout.setContentsMargins(0, 0, 0, 0)
        animation_layout.addWidget(QLabel("动画导出", self.animation_row))
        self.animation_mode = QComboBox(self.animation_row)
        self.animation_mode.addItem("保留全部动画帧", "preserve")
        self.animation_mode.addItem("仅导出当前帧", "current")
        _style_combo_popup(self.animation_mode, colors)
        animation_layout.addWidget(self.animation_mode, 1)
        options_layout.addWidget(self.animation_row)

        self.tga_rle = QCheckBox("TGA 使用 RLE 无损压缩", self.options_card)
        self.tga_rle.setChecked(True)
        options_layout.addWidget(self.tga_rle)

        self.dds_row = QWidget(self.options_card)
        dds_layout = QHBoxLayout(self.dds_row)
        dds_layout.setContentsMargins(0, 0, 0, 0)
        dds_layout.addWidget(QLabel("DDS 像素格式", self.dds_row))
        self.dds_pixel_format = QComboBox(self.dds_row)
        for label, encoding_value in (
            ("BC3 / DXT5（含透明）", "DXT5"),
            ("BC1 / DXT1（小体积）", "DXT1"),
            ("BC5（法线贴图）", "BC5"),
            ("RGBA8（不压缩）", "RGBA8"),
        ):
            self.dds_pixel_format.addItem(label, encoding_value)
        _style_combo_popup(self.dds_pixel_format, colors)
        dds_layout.addWidget(self.dds_pixel_format, 1)
        options_layout.addWidget(self.dds_row)

        self.ktx2_row = QWidget(self.options_card)
        ktx2_layout = QHBoxLayout(self.ktx2_row)
        ktx2_layout.setContentsMargins(0, 0, 0, 0)
        ktx2_layout.addWidget(QLabel("KTX2 编码", self.ktx2_row))
        self.ktx2_mode = QComboBox(self.ktx2_row)
        self.ktx2_mode.addItem("UASTC（高质量游戏纹理）", "uastc")
        self.ktx2_mode.addItem("ETC1S（更小体积）", "etc1s")
        self.ktx2_mode.addItem("RGBA8（不压缩）", "rgba8")
        _style_combo_popup(self.ktx2_mode, colors)
        ktx2_layout.addWidget(self.ktx2_mode, 1)
        options_layout.addWidget(self.ktx2_row)

        self.mipmap_enabled = QCheckBox("生成完整 Mipmap 链", self.options_card)
        self.mipmap_enabled.setChecked(True)
        options_layout.addWidget(self.mipmap_enabled)

        self.format_notice = QLabel("", self.options_card)
        self.format_notice.setObjectName("mutedLabel")
        self.format_notice.setWordWrap(True)
        options_layout.addWidget(self.format_notice)
        content_layout.addWidget(self.options_card)

        self.advanced_toggle = QPushButton("高级设置  ▾", content)
        self.advanced_toggle.setObjectName("linkButton")
        self.advanced_toggle.setCheckable(True)
        content_layout.addWidget(self.advanced_toggle)
        self.advanced_card = QFrame(content)
        self.advanced_card.setObjectName("toolCard")
        advanced_layout = QVBoxLayout(self.advanced_card)
        advanced_layout.setContentsMargins(13, 11, 13, 11)
        advanced_layout.setSpacing(9)
        self.preserve_metadata = QCheckBox("保留 EXIF、ICC 等元数据", self.advanced_card)
        self.preserve_metadata.setChecked(True)
        advanced_layout.addWidget(self.preserve_metadata)
        self.background_row = QWidget(self.advanced_card)
        background_layout = QHBoxLayout(self.background_row)
        background_layout.setContentsMargins(0, 0, 0, 0)
        background_layout.addWidget(QLabel("透明区域底色", self.background_row))
        self.background_combo = QComboBox(self.background_row)
        for label, color_value in (
            ("白色", "#ffffff"),
            ("黑色", "#000000"),
            ("绿幕色", "#00ff00"),
            ("纸色", "#f4f0e6"),
            ("自定义…", "custom"),
        ):
            self.background_combo.addItem(label, color_value)
        _style_combo_popup(self.background_combo, colors)
        background_layout.addWidget(self.background_combo, 1)
        advanced_layout.addWidget(self.background_row)
        self.advanced_card.setVisible(False)
        content_layout.addWidget(self.advanced_card)

        output_title = QLabel("输出", content)
        output_title.setStyleSheet("font-weight: 600;")
        content_layout.addWidget(output_title)
        output_card = QFrame(content)
        output_card.setObjectName("toolCard")
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(13, 11, 13, 11)
        output_layout.setSpacing(9)
        folder_choices = QHBoxLayout()
        self.same_folder = QRadioButton("原文件夹", output_card)
        self.custom_folder = QRadioButton("指定文件夹", output_card)
        self.same_folder.setChecked(True)
        folder_choices.addWidget(self.same_folder)
        folder_choices.addWidget(self.custom_folder)
        folder_choices.addStretch(1)
        output_layout.addLayout(folder_choices)
        folder_row = QHBoxLayout()
        self.output_directory_edit = QLineEdit(str(self.source_path.parent), output_card)
        self.output_directory_edit.setReadOnly(True)
        self.output_directory_edit.setEnabled(False)
        self.choose_directory_button = QPushButton("选择…", output_card)
        self.choose_directory_button.setEnabled(False)
        folder_row.addWidget(self.output_directory_edit, 1)
        folder_row.addWidget(self.choose_directory_button)
        output_layout.addLayout(folder_row)
        naming_form = QFormLayout()
        naming_form.setContentsMargins(0, 0, 0, 0)
        naming_form.setSpacing(8)
        self.filename_suffix = QLineEdit("_转换后", output_card)
        self.conflict_combo = QComboBox(output_card)
        self.conflict_combo.addItem("自动编号，不覆盖", "unique")
        self.conflict_combo.addItem("已存在则跳过", "skip")
        self.conflict_combo.addItem("覆盖已有结果", "overwrite")
        _style_combo_popup(self.conflict_combo, colors)
        naming_form.addRow("文件名后缀", self.filename_suffix)
        naming_form.addRow("文件冲突", self.conflict_combo)
        output_layout.addLayout(naming_form)
        self.open_after = QCheckBox("转换完成后打开结果", output_card)
        self.open_after.setChecked(True)
        output_layout.addWidget(self.open_after)
        content_layout.addWidget(output_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        estimate_card = QFrame(self)
        estimate_card.setObjectName("toolCard")
        estimate_layout = QVBoxLayout(estimate_card)
        estimate_layout.setContentsMargins(12, 9, 12, 9)
        estimate_layout.setSpacing(3)
        self.estimate_label = QLabel("正在估算输出体积…", estimate_card)
        self.estimate_label.setStyleSheet("font-weight: 600;")
        self.estimate_detail = QLabel("", estimate_card)
        self.estimate_detail.setObjectName("mutedLabel")
        estimate_layout.addWidget(self.estimate_label)
        estimate_layout.addWidget(self.estimate_detail)
        outer.addWidget(estimate_card)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)
        footer = QHBoxLayout()
        self.status_label = QLabel("", self)
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.cancel_button = QPushButton("关闭", self)
        self.convert_button = QPushButton("开始转换", self)
        self.convert_button.setObjectName("primaryButton")
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.convert_button)
        outer.addLayout(footer)

        self._estimate_timer = QTimer(self)
        self._estimate_timer.setSingleShot(True)
        self._estimate_timer.setInterval(180)
        self._estimate_timer.timeout.connect(self._update_estimate)
        self.add_sources_button.clicked.connect(self._choose_more_sources)
        self.remove_source_button.clicked.connect(self._remove_selected_sources)
        self.quality_slider.valueChanged.connect(self._settings_changed)
        self.target_size_enabled.toggled.connect(self._settings_changed)
        self.target_size_enabled.toggled.connect(self.target_size_spin.setEnabled)
        self.target_size_spin.valueChanged.connect(self._settings_changed)
        self.webp_lossless.toggled.connect(self._settings_changed)
        self.png_palette.currentIndexChanged.connect(self._settings_changed)
        self.tiff_compression.currentIndexChanged.connect(self._settings_changed)
        self.animation_mode.currentIndexChanged.connect(self._settings_changed)
        self.tga_rle.toggled.connect(self._settings_changed)
        self.dds_pixel_format.currentIndexChanged.connect(self._settings_changed)
        self.ktx2_mode.currentIndexChanged.connect(self._settings_changed)
        self.mipmap_enabled.toggled.connect(self._settings_changed)
        self.preserve_metadata.toggled.connect(self._settings_changed)
        self.background_combo.currentIndexChanged.connect(self._background_changed)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        self.same_folder.toggled.connect(self._sync_output_directory)
        self.choose_directory_button.clicked.connect(self._choose_output_directory)
        self.filename_suffix.textChanged.connect(self._settings_changed)
        self.cancel_button.clicked.connect(self.closeRequested)
        self.convert_button.clicked.connect(self._submit)

        initial_format = "JPEG" if self.current_format != "JPEG" else "PNG"
        self.format_buttons[initial_format].setChecked(True)
        self._update_source_ui()
        self._select_format(initial_format)
        self._sync_output_directory()

    @property
    def sources(self) -> tuple[Path, ...]:
        return tuple(self._sources)

    @property
    def output_format(self) -> str:
        for output_format, button in self.format_buttons.items():
            if button.isChecked():
                return output_format
        return "PNG"

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value >= 1024 * 1024:
            return f"{value / (1024 * 1024):.1f} MB"
        if value >= 1024:
            return f"{value / 1024:.0f} KB"
        return f"{value} B"

    def add_sources(self, paths: list[Path]) -> None:
        known = {str(path).casefold() for path in self._sources}
        for path in paths:
            try:
                normalized = path.resolve()
            except OSError:
                normalized = path.absolute()
            key = str(normalized).casefold()
            if normalized.is_file() and key not in known:
                known.add(key)
                self._sources.append(normalized)
        self._update_source_ui()

    def _choose_more_sources(self) -> None:
        patterns = (
            "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff *.avif "
            "*.heic *.heif *.psd *.psdt *.svg *.ai *.eps *.emf *.wmf"
            " *.tga *.dds *.ico *.exr *.hdr *.ktx2"
        )
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "添加待转换图片",
            str(self.source_path.parent),
            f"支持的图片 ({patterns})",
        )
        if selected:
            self.add_sources([Path(path) for path in selected])

    def _remove_selected_sources(self) -> None:
        selected = {
            Path(item.data(Qt.ItemDataRole.UserRole)) for item in self.source_list.selectedItems()
        }
        selected.discard(self.source_path)
        self._sources = [path for path in self._sources if path not in selected]
        self._update_source_ui()

    def _update_source_ui(self) -> None:
        count = len(self._sources)
        total_bytes = sum(path.stat().st_size for path in self._sources if path.is_file())
        if count == 1:
            self.source_summary.setText(self.source_path.name)
            self.source_detail.setText(
                f"{self.current_format or '图片'}  ·  {self.source_size[0]} × "
                f"{self.source_size[1]}  ·  {self._format_bytes(total_bytes)}"
            )
        else:
            self.source_summary.setText(f"批量转换 · {count} 张图片")
            self.source_detail.setText(f"输入文件合计 {self._format_bytes(total_bytes)}")
        self.source_list.clear()
        for path in self._sources:
            item = QListWidgetItem(path.name, self.source_list)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            if path == self.source_path:
                item.setToolTip("当前正在查看的图片；不能从队列移除")
        self.source_list.setVisible(count > 1)
        self.remove_source_button.setVisible(count > 1)
        self.open_after.setVisible(count == 1)
        self.convert_button.setText("开始批量转换" if count > 1 else "开始转换")
        self._queue_estimate()

    def _select_format(self, output_format: str) -> None:
        self.format_buttons[output_format].setChecked(True)
        uses_quality = output_format in {"JPEG", "WEBP", "AVIF", "KTX2"}
        webp_is_lossless = output_format == "WEBP" and self.webp_lossless.isChecked()
        self.quality_panel.setVisible(uses_quality and not webp_is_lossless)
        self.webp_lossless.setVisible(output_format == "WEBP")
        supports_target = output_format in {"JPEG", "WEBP"} and not webp_is_lossless
        self.target_size_row.setVisible(supports_target)
        self.png_palette_row.setVisible(output_format == "PNG")
        self.tiff_compression_row.setVisible(output_format == "TIFF")
        self.animation_row.setVisible(output_format in {"GIF", "APNG"})
        self.tga_rle.setVisible(output_format == "TGA")
        self.dds_row.setVisible(output_format == "DDS")
        self.ktx2_row.setVisible(output_format == "KTX2")
        self.mipmap_enabled.setVisible(output_format in {"DDS", "KTX2"})
        notices = {
            "ICO": "将一次写入 16、24、32、48、64、128、256 七种图标尺寸。",
            "EXR": "导出线性 RGB、半精度浮点 RGBA 和 ZIP 无损压缩。",
            "HDR": "导出 Radiance RGBE 高动态范围；透明区域会合成到底色。",
            "SVG": "保留位图外观并嵌入 SVG，不会自动描摹为矢量路径。",
            "AI": "写入 Illustrator 可打开的 EPS 位图容器，不会自动矢量化。",
            "EPS": "写入 PostScript 位图容器，不会自动矢量化。",
            "EMF": "写入 Windows 图元文件位图记录，不会自动矢量化。",
            "WMF": "写入 Windows 图元文件位图记录，不会自动矢量化。",
        }
        notice = notices.get(output_format, "")
        self.format_notice.setText(notice)
        self.format_notice.setVisible(bool(notice))
        self.background_row.setVisible(
            self.has_alpha and output_format in {"JPEG", "BMP", "HDR", "AI", "EPS", "EMF", "WMF"}
        )
        self._queue_estimate()

    def _settings_changed(self, *_args: object) -> None:
        self.quality_value.setText(str(self.quality_slider.value()))
        self._select_format(self.output_format)

    def _toggle_advanced(self, visible: bool) -> None:
        self.advanced_card.setVisible(visible)
        self.advanced_toggle.setText("高级设置  ▴" if visible else "高级设置  ▾")

    def _background_changed(self, _index: int) -> None:
        value = str(self.background_combo.currentData())
        if value == "custom":
            selected = QColorDialog.getColor(QColor(self._custom_background), self, "透明区域底色")
            if selected.isValid():
                self._custom_background = selected.name()
            else:
                self.background_combo.setCurrentIndex(0)
        self._queue_estimate()

    def _sync_output_directory(self, *_args: object) -> None:
        custom = self.custom_folder.isChecked()
        self.output_directory_edit.setEnabled(custom)
        self.choose_directory_button.setEnabled(custom)
        self._queue_estimate()

    def _choose_output_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择输出文件夹",
            self.output_directory_edit.text() or str(self.source_path.parent),
        )
        if selected:
            self.custom_folder.setChecked(True)
            self.output_directory_edit.setText(str(Path(selected).resolve()))

    def _queue_estimate(self, *_args: object) -> None:
        if not self._busy:
            self.estimate_label.setText("正在估算输出体积…")
            self._estimate_timer.start()

    def _export_options(self) -> ExportOptions:
        output_format = self.output_format
        target_size = (
            self.target_size_spin.value()
            if not self.target_size_row.isHidden() and self.target_size_enabled.isChecked()
            else None
        )
        palette_value = self.png_palette.currentData() if output_format == "PNG" else None
        return ExportOptions(
            output_format,
            quality=self.quality_slider.value(),
            target_size_kb=target_size,
            webp_lossless=output_format == "WEBP" and self.webp_lossless.isChecked(),
            png_palette_colors=(int(palette_value) if palette_value is not None else None),
            tiff_compression=str(self.tiff_compression.currentData()),
            preserve_metadata=self.preserve_metadata.isChecked(),
            animation_mode=str(self.animation_mode.currentData()),
            tga_rle=self.tga_rle.isChecked(),
            dds_pixel_format=str(self.dds_pixel_format.currentData()),
            generate_mipmaps=self.mipmap_enabled.isChecked(),
            ktx2_mode=str(self.ktx2_mode.currentData()),
        )

    def _update_estimate(self) -> None:
        try:
            sample_bytes = estimate_save_size(self._estimate_image, self._export_options())
            sample_pixels = max(1, self._estimate_image.width * self._estimate_image.height)
            source_pixels = max(1, self.source_size[0] * self.source_size[1])
            linear_formats = self.output_format in {
                "BMP",
                "TIFF",
                "PNG",
                "TGA",
                "EXR",
                "HDR",
                "ICO",
                "SVG",
                "AI",
                "EPS",
                "EMF",
                "WMF",
            }
            exponent = 1.0 if linear_formats else 0.88
            estimated = max(1, round(sample_bytes * (source_pixels / sample_pixels) ** exponent))
            target_size_kb = self._export_options().target_size_kb
            if target_size_kb is not None:
                estimated = min(estimated, target_size_kb * 1024)
            source_bytes = self.source_path.stat().st_size
            difference = estimated - source_bytes
            if source_bytes > 0:
                percentage = abs(difference) * 100 / source_bytes
                change = (
                    f"预计减少 {percentage:.0f}%"
                    if difference < 0
                    else f"预计增加 {percentage:.0f}%"
                )
            else:
                change = "输出大小仅供参考"
            self.estimate_label.setText(
                f"{self._format_bytes(source_bytes)}  →  约 {self._format_bytes(estimated)}"
            )
            batch_note = f" · 共 {len(self._sources)} 张" if len(self._sources) > 1 else ""
            self.estimate_detail.setText(f"{change}{batch_note}；实际大小以转换结果为准")
        except Exception:
            self.estimate_label.setText("输出大小将在转换时计算")
            self.estimate_detail.setText("当前格式或编码器暂不支持快速估算")

    def _output_directory(self) -> Path | None:
        if self.same_folder.isChecked():
            return None
        text = self.output_directory_edit.text().strip()
        return Path(text).resolve() if text else None

    def _target_for(self, source: Path) -> Path:
        output_directory = self._output_directory() or source.parent
        suffix = self.filename_suffix.text().strip()
        extension = extension_for_format(self.output_format)
        if not suffix and source.suffix.casefold() == extension.casefold():
            suffix = "_副本"
        return output_directory / f"{source.stem}{suffix}{extension}"

    def request(self) -> ConversionRequest:
        first_target = self._target_for(self._sources[0])
        options = self._export_options()
        background = str(self.background_combo.currentData())
        if background == "custom":
            background = self._custom_background
        return ConversionRequest(
            output_format=self.output_format,
            target_path=first_target,
            quality=options.quality,
            open_after=self.open_after.isChecked() and len(self._sources) == 1,
            source_paths=tuple(self._sources),
            output_directory=self._output_directory(),
            filename_suffix=self.filename_suffix.text().strip(),
            conflict_policy=str(self.conflict_combo.currentData()),
            target_size_kb=options.target_size_kb,
            webp_lossless=options.webp_lossless,
            png_palette_colors=options.png_palette_colors,
            tiff_compression=options.tiff_compression,
            preserve_metadata=options.preserve_metadata,
            background_color=background,
            animation_mode=options.animation_mode,
            tga_rle=options.tga_rle,
            dds_pixel_format=options.dds_pixel_format,
            generate_mipmaps=options.generate_mipmaps,
            ico_sizes=options.ico_sizes,
            ktx2_mode=options.ktx2_mode,
        )

    def _submit(self) -> None:
        if self._busy or not self._sources:
            return
        output_directory = self._output_directory()
        if self.custom_folder.isChecked() and output_directory is None:
            self.status_label.setText("请先选择输出文件夹")
            return
        self.set_busy(True)
        self.convertRequested.emit(self.request())

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.convert_button.setEnabled(not busy)
        self.add_sources_button.setEnabled(not busy)
        self.remove_source_button.setEnabled(not busy)
        self.cancel_button.setText("后台转换中" if busy else "关闭")
        self.cancel_button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, len(self._sources))
            self.progress.setValue(0)
            self.status_label.setText("正在准备转换…")

    def set_progress(self, completed: int, total: int, filename: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(completed)
        self.status_label.setText(f"正在处理 {completed + 1}/{total} · {filename}")

    def set_result_summary(self, success: int, skipped: int, failed: int) -> None:
        self.set_busy(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, max(1, success + skipped + failed))
        self.progress.setValue(success + skipped + failed)
        parts = [f"成功 {success}"]
        if skipped:
            parts.append(f"跳过 {skipped}")
        if failed:
            parts.append(f"失败 {failed}")
        self.status_label.setText("转换完成 · " + "，".join(parts))
        self.cancel_button.setText("完成")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._estimate_timer.stop()
        self._estimate_image.close()
        super().closeEvent(event)


class CropCanvas(QGraphicsView):
    """Small crop canvas with draw, move and eight-handle resize interaction."""

    cropChanged = Signal(object)
    _HANDLE_NAMES = ("tl", "t", "tr", "r", "br", "b", "bl", "l")

    def __init__(
        self,
        image: QImage,
        colors: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor(colors["canvas"]))
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        self._image = image.copy()
        pixmap = QPixmap.fromImage(self._image)
        self._pixmap_item = QGraphicsPixmapItem(pixmap)
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._pixmap_item)
        self._image_rect = QRectF(0, 0, image.width(), image.height())
        self._scene.setSceneRect(self._image_rect)

        self._shade = QGraphicsPathItem()
        self._shade.setBrush(QColor(0, 0, 0, 145))
        self._shade.setPen(Qt.PenStyle.NoPen)
        self._shade.setZValue(2)
        self._scene.addItem(self._shade)

        selection_pen = QPen(QColor(colors["accent"]), 2)
        selection_pen.setCosmetic(True)
        self._selection = QGraphicsRectItem()
        self._selection.setPen(selection_pen)
        self._selection.setBrush(Qt.BrushStyle.NoBrush)
        self._selection.setZValue(3)
        self._scene.addItem(self._selection)

        handle_pen = QPen(QColor(colors["accent"]), 1.5)
        handle_pen.setCosmetic(True)
        self._handles: dict[str, QGraphicsEllipseItem] = {}
        for name in self._HANDLE_NAMES:
            handle = QGraphicsEllipseItem()
            handle.setPen(handle_pen)
            handle.setBrush(QColor("#ffffff"))
            handle.setZValue(4)
            self._scene.addItem(handle)
            self._handles[name] = handle

        self._crop_rect = QRectF(self._image_rect)
        self._aspect_ratio: float | None = None
        self._interaction = ""
        self._active_handle = ""
        self._press_point = QPointF()
        self._press_rect = QRectF()
        self._overlay_visible = False
        self._update_overlay()

    def image(self) -> QImage:
        return self._image.copy()

    def set_image(self, image: QImage) -> None:
        """Replace the working image and reset the crop to its full bounds."""

        self._image = image.copy()
        self._pixmap_item.setPixmap(QPixmap.fromImage(self._image))
        self._pixmap_item.setPos(0, 0)
        self._pixmap_item.resetTransform()
        self._image_rect = QRectF(0, 0, image.width(), image.height())
        self._scene.setSceneRect(self._image_rect)
        self._crop_rect = QRectF(self._image_rect)
        self.fitInView(self._image_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_overlay()
        self.cropChanged.emit(self.crop_box())

    def show_comparison_image(self, image: QImage) -> None:
        """Temporarily show another image without changing crop coordinates."""

        pixmap = QPixmap.fromImage(image)
        self._pixmap_item.setPixmap(pixmap)
        self._pixmap_item.resetTransform()
        if pixmap.width() > 0 and pixmap.height() > 0:
            scale = min(
                self._image_rect.width() / pixmap.width(),
                self._image_rect.height() / pixmap.height(),
            )
            displayed_width = pixmap.width() * scale
            displayed_height = pixmap.height() * scale
            self._pixmap_item.setScale(scale)
            self._pixmap_item.setPos(
                self._image_rect.left() + (self._image_rect.width() - displayed_width) / 2,
                self._image_rect.top() + (self._image_rect.height() - displayed_height) / 2,
            )

    def end_comparison(self) -> None:
        self._pixmap_item.setPixmap(QPixmap.fromImage(self._image))
        self._pixmap_item.setPos(0, 0)
        self._pixmap_item.resetTransform()

    def center_crop(self) -> None:
        """Center the current crop, using the largest fitting selected ratio."""

        bounds = self._image_rect
        if self._aspect_ratio is not None:
            width = bounds.width()
            height = width / self._aspect_ratio
            if height > bounds.height():
                height = bounds.height()
                width = height * self._aspect_ratio
        else:
            width = min(bounds.width(), self._crop_rect.width())
            height = min(bounds.height(), self._crop_rect.height())
        rect = QRectF(0, 0, width, height)
        rect.moveCenter(bounds.center())
        self._set_crop_rect(rect)

    def set_crop_visible(self, visible: bool) -> None:
        self._overlay_visible = bool(visible)
        self._shade.setVisible(visible)
        self._selection.setVisible(visible)
        for handle in self._handles.values():
            handle.setVisible(visible)

    def crop_rect(self) -> QRectF:
        return QRectF(self._crop_rect)

    def crop_box(self) -> tuple[int, int, int, int]:
        rect = self._crop_rect.normalized().intersected(self._image_rect)
        left = max(0, round(rect.left()))
        top = max(0, round(rect.top()))
        right = min(self._image.width(), round(rect.right()))
        bottom = min(self._image.height(), round(rect.bottom()))
        return left, top, max(left + 1, right), max(top + 1, bottom)

    def reset_crop(self) -> None:
        self._set_crop_rect(QRectF(self._image_rect))

    def set_aspect_ratio(self, ratio: float | None) -> None:
        self._aspect_ratio = ratio if ratio and ratio > 0 else None
        if self._aspect_ratio is None:
            return
        bounds = self._image_rect
        width = bounds.width()
        height = width / self._aspect_ratio
        if height > bounds.height():
            height = bounds.height()
            width = height * self._aspect_ratio
        rect = QRectF(0, 0, width, height)
        rect.moveCenter(bounds.center())
        self._set_crop_rect(rect)

    def _set_crop_rect(self, rect: QRectF) -> None:
        normalized = rect.normalized().intersected(self._image_rect)
        if normalized.width() < 1 or normalized.height() < 1:
            return
        self._crop_rect = normalized
        self._update_overlay()
        self.cropChanged.emit(self.crop_box())

    def _handle_points(self) -> dict[str, QPointF]:
        rect = self._crop_rect
        center = rect.center()
        return {
            "tl": rect.topLeft(),
            "t": QPointF(center.x(), rect.top()),
            "tr": rect.topRight(),
            "r": QPointF(rect.right(), center.y()),
            "br": rect.bottomRight(),
            "b": QPointF(center.x(), rect.bottom()),
            "bl": rect.bottomLeft(),
            "l": QPointF(rect.left(), center.y()),
        }

    def _scene_tolerance(self) -> float:
        scale = max(0.001, abs(self.transform().m11()))
        return 7.0 / scale

    def _update_overlay(self) -> None:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        path.addRect(self._image_rect)
        path.addRect(self._crop_rect)
        self._shade.setPath(path)
        self._selection.setRect(self._crop_rect)
        radius = self._scene_tolerance() * 0.65
        for name, point in self._handle_points().items():
            self._handles[name].setRect(
                point.x() - radius,
                point.y() - radius,
                radius * 2,
                radius * 2,
            )

    def _hit_handle(self, point: QPointF) -> str:
        tolerance = self._scene_tolerance() * 1.5
        for name, handle_point in self._handle_points().items():
            if (
                abs(point.x() - handle_point.x()) <= tolerance
                and abs(point.y() - handle_point.y()) <= tolerance
            ):
                return name
        return ""

    @staticmethod
    def _cursor_for_handle(handle: str) -> Qt.CursorShape:
        if handle in {"tl", "br"}:
            return Qt.CursorShape.SizeFDiagCursor
        if handle in {"tr", "bl"}:
            return Qt.CursorShape.SizeBDiagCursor
        if handle in {"l", "r"}:
            return Qt.CursorShape.SizeHorCursor
        if handle in {"t", "b"}:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _clamp_point(self, point: QPointF) -> QPointF:
        return QPointF(
            max(self._image_rect.left(), min(point.x(), self._image_rect.right())),
            max(self._image_rect.top(), min(point.y(), self._image_rect.bottom())),
        )

    def _rect_from_drag(self, anchor: QPointF, point: QPointF) -> QRectF:
        point = self._clamp_point(point)
        if self._aspect_ratio is None:
            return QRectF(anchor, point).normalized()
        dx = point.x() - anchor.x()
        dy = point.y() - anchor.y()
        width = abs(dx)
        height = abs(dy)
        if height <= 0 or width / max(height, 0.001) > self._aspect_ratio:
            height = width / self._aspect_ratio
        else:
            width = height * self._aspect_ratio
        point = QPointF(
            anchor.x() + width * (1 if dx >= 0 else -1),
            anchor.y() + height * (1 if dy >= 0 else -1),
        )
        rect = QRectF(anchor, point).normalized()
        if not self._image_rect.contains(rect):
            width_scale = self._image_rect.width() / max(rect.width(), 1)
            height_scale = self._image_rect.height() / max(rect.height(), 1)
            scale = min(1.0, width_scale, height_scale)
            width = rect.width() * scale
            height = rect.height() * scale
            rect = QRectF(0, 0, width, height)
            rect.moveCenter(QRectF(anchor, point).normalized().center())
            if rect.left() < self._image_rect.left():
                rect.moveLeft(self._image_rect.left())
            if rect.top() < self._image_rect.top():
                rect.moveTop(self._image_rect.top())
            if rect.right() > self._image_rect.right():
                rect.moveRight(self._image_rect.right())
            if rect.bottom() > self._image_rect.bottom():
                rect.moveBottom(self._image_rect.bottom())
        return rect

    def _resize_from_handle(self, handle: str, point: QPointF) -> QRectF:
        rect = QRectF(self._press_rect)
        point = self._clamp_point(point)
        opposite = {
            "tl": rect.bottomRight(),
            "tr": rect.bottomLeft(),
            "br": rect.topLeft(),
            "bl": rect.topRight(),
        }
        if self._aspect_ratio is not None and handle in opposite:
            return self._rect_from_drag(opposite[handle], point)
        if "l" in handle:
            rect.setLeft(min(point.x(), rect.right() - 1))
        if "r" in handle:
            rect.setRight(max(point.x(), rect.left() + 1))
        if "t" in handle:
            rect.setTop(min(point.y(), rect.bottom() - 1))
        if "b" in handle:
            rect.setBottom(max(point.y(), rect.top() + 1))
        return rect.normalized().intersected(self._image_rect)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._overlay_visible or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        point = self._clamp_point(self.mapToScene(event.position().toPoint()))
        self._active_handle = self._hit_handle(point)
        self._press_point = point
        self._press_rect = QRectF(self._crop_rect)
        if self._active_handle:
            self._interaction = "resize"
        elif self._crop_rect.contains(point):
            self._interaction = "move"
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self._interaction = "new"
            self._set_crop_rect(QRectF(point, point + QPointF(1, 1)))
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = self._clamp_point(self.mapToScene(event.position().toPoint()))
        if not self._interaction:
            handle = self._hit_handle(point) if self._overlay_visible else ""
            if handle:
                self.viewport().setCursor(self._cursor_for_handle(handle))
            elif self._overlay_visible and self._crop_rect.contains(point):
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            super().mouseMoveEvent(event)
            return
        if self._interaction == "new":
            self._set_crop_rect(self._rect_from_drag(self._press_point, point))
        elif self._interaction == "resize":
            self._set_crop_rect(self._resize_from_handle(self._active_handle, point))
        elif self._interaction == "move":
            delta = point - self._press_point
            moved = QRectF(self._press_rect)
            moved.translate(delta)
            if moved.left() < self._image_rect.left():
                moved.moveLeft(self._image_rect.left())
            if moved.top() < self._image_rect.top():
                moved.moveTop(self._image_rect.top())
            if moved.right() > self._image_rect.right():
                moved.moveRight(self._image_rect.right())
            if moved.bottom() > self._image_rect.bottom():
                moved.moveBottom(self._image_rect.bottom())
            self._set_crop_rect(moved)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._interaction and event.button() == Qt.MouseButton.LeftButton:
            self._interaction = ""
            self._active_handle = ""
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.fitInView(self._image_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._update_overlay()


class ImageEditWorkspace(QFrame):
    """Unified workspace for crop, resize and format conversion."""

    cancelRequested = Signal()
    saveRequested = Signal(object)
    quickExportRequested = Signal(object)
    sourcePreviewRequested = Signal(object)
    presetCatalogChanged = Signal(object)

    def __init__(
        self,
        source_path: Path,
        image: QImage,
        *,
        colors: dict[str, str],
        preset_catalog: CropPresetCatalog | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("imageEditWorkspace")
        self._colors = dict(colors)
        self.source_path = source_path.resolve()
        self._active_source_path = self.source_path
        self._requested_source_path = self.source_path
        self._original_image = image.copy()
        self._primary_original_image = image.copy()
        self._initial_source_size = (image.width(), image.height())
        self._primary_source_size = self._initial_source_size
        self._source_size = self._initial_source_size
        initial_box = (0, 0, image.width(), image.height())
        self._active_base_box = initial_box
        self._source_states: dict[Path, _BatchSourceState] = {
            self.source_path: _BatchSourceState(
                self._initial_source_size,
                initial_box,
                initial_box,
            )
        }
        self._output_directory = source_path.parent.resolve()
        self._sources = [self.source_path]
        self._ratio_buttons: dict[str, QPushButton] = {}
        self._active_ratio_label = "自由"
        self._active_ratio_value: float | None = None
        self._custom_ratio = (1, 1)
        self._preset_catalog = preset_catalog or CropPresetCatalog.defaults()
        self.size_preset_buttons: dict[tuple[int, int], QPushButton] = {}
        self.format_buttons: dict[str, QPushButton] = {}
        source_format = {
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
            ".png": "PNG",
            ".webp": "WEBP",
            ".avif": "AVIF",
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
        }.get(source_path.suffix.casefold(), "PNG")
        self._initial_output_format = source_format
        self._syncing_size = False
        self._size_dirty = False
        self._working_revision = 0
        self._comparing = False
        self._busy = False
        self._clean_signature: tuple[object, ...] = ()
        self.setStyleSheet(_dialog_style(colors))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 18)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        title = QLabel("处理图片", self)
        title.setStyleSheet("font: 700 19px 'Microsoft YaHei UI';")
        self.subtitle = QLabel(
            f"{source_path.name}  ·  {image.width()} × {image.height()}  ·  "
            "裁剪、调整尺寸或转换格式，原图不会被覆盖",
            self,
        )
        self.subtitle.setObjectName("mutedLabel")
        title_block.addWidget(title)
        title_block.addWidget(self.subtitle)
        header.addLayout(title_block)
        header.addStretch(1)
        cancel = QPushButton("退出处理", self)
        cancel.clicked.connect(self.cancelRequested)
        header.addWidget(cancel)
        outer.addLayout(header)

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.body_splitter.setObjectName("imageEditSplitter")
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.setHandleWidth(8)
        self.body_splitter.setOpaqueResize(True)

        canvas_column = QWidget(self.body_splitter)
        canvas_column.setMinimumWidth(320)
        canvas_layout = QVBoxLayout(canvas_column)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(10)
        self.canvas = CropCanvas(image, colors, self)
        canvas_layout.addWidget(self.canvas, 1)
        canvas_layout.addWidget(self._build_output_preview_card(canvas_column))
        canvas_layout.addWidget(self._build_output_bar(canvas_column))
        self.body_splitter.addWidget(canvas_column)

        panel = QFrame(self.body_splitter)
        panel.setObjectName("toolPanel")
        panel.setMinimumWidth(320)
        panel.setMaximumWidth(16_777_215)
        self.settings_panel = panel
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(10)

        self.settings_scroll = QScrollArea(panel)
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setStyleSheet("QScrollArea {background: transparent; border: 0;}")
        self.settings_scroll.viewport().setStyleSheet("background: transparent;")
        self.settings_scroll.setWidget(self._build_settings_page(self.settings_scroll))
        panel_layout.addWidget(self.settings_scroll, 1)

        self.output_summary = QLabel("", panel)
        self.output_summary.setObjectName("mutedLabel")
        self.output_summary.setWordWrap(True)
        self.output_summary.setMinimumWidth(0)
        self.output_summary.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        panel_layout.addWidget(self.output_summary)

        self.progress = QProgressBar(panel)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        panel_layout.addWidget(self.progress)
        self.status_label = QLabel("", panel)
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        panel_layout.addWidget(self.status_label)

        footer = QHBoxLayout()
        self.reset_button = QPushButton("重置", panel)
        self.reset_button.clicked.connect(self._confirm_reset)
        self.quick_export_button = QPushButton("导出并打开", panel)
        self.quick_export_button.clicked.connect(lambda: self.saveRequested.emit(self.request()))
        self.save_button = QPushButton("导出", panel)
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(lambda: self.quickExportRequested.emit(self.request()))
        footer.addWidget(self.reset_button)
        footer.addWidget(self.quick_export_button)
        footer.addWidget(self.save_button, 1)
        panel_layout.addLayout(footer)
        self.body_splitter.addWidget(panel)
        self.body_splitter.setStretchFactor(0, 3)
        self.body_splitter.setStretchFactor(1, 2)
        self.body_splitter.setSizes([860, 390])
        outer.addWidget(self.body_splitter, 1)

        self.canvas.cropChanged.connect(self._crop_changed)
        self.canvas.set_crop_visible(True)
        self._apply_crop_ratio("自由", None)
        self._select_output_format(self._initial_output_format)
        self._update_summary()
        self._clean_signature = self._state_signature()

        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.cancelRequested)
        quick_export = QShortcut(QKeySequence("Ctrl+E"), self)
        quick_export.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        quick_export.activated.connect(lambda: self.quickExportRequested.emit(self.request()))
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

    def _build_output_preview_card(self, parent: QWidget) -> QWidget:
        card = QFrame(parent)
        card.setObjectName("toolCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(12)

        heading = QLabel("应用后预览", card)
        heading.setStyleSheet("font-weight: 700;")
        layout.addWidget(heading)

        self.output_preview_line = QLabel("", card)
        self.output_preview_line.setStyleSheet("font-weight: 600;")
        self.output_preview_line.setMinimumWidth(0)
        self.output_preview_line.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.output_preview_line.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.output_preview_line, 1)
        return card

    def _build_output_bar(self, parent: QWidget) -> QWidget:
        bar = QFrame(parent)
        bar.setObjectName("outputBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(8)

        label = QLabel("输出目录", bar)
        label.setStyleSheet("font-weight: 600;")
        layout.addWidget(label)

        self.output_path_edit = QLineEdit(str(self._output_directory), bar)
        self.output_path_edit.setReadOnly(True)
        self.output_path_edit.setToolTip(str(self._output_directory))
        layout.addWidget(self.output_path_edit, 1)

        self.change_output_button = QPushButton("更改目录", bar)
        self.open_output_button = QPushButton("打开文件夹", bar)
        self.change_output_button.clicked.connect(self._choose_output_directory)
        self.open_output_button.clicked.connect(self._open_output_directory)
        layout.addWidget(self.change_output_button)
        layout.addWidget(self.open_output_button)
        return bar

    def _build_settings_page(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        page.setObjectName("imageToolSettings")
        page.setMinimumWidth(0)
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 4, 4)
        layout.setSpacing(10)

        self.source_section = QLabel("处理图片", page)
        self.source_section.setObjectName("sectionTitle")
        layout.addWidget(self.source_section)
        self.source_card = QFrame(page)
        self.source_card.setObjectName("toolCard")
        source_layout = QVBoxLayout(self.source_card)
        source_layout.setContentsMargins(11, 9, 11, 9)
        source_layout.setSpacing(7)
        self.source_summary = QLabel(self.source_card)
        self.source_summary.setWordWrap(True)
        source_layout.addWidget(self.source_summary)
        self.source_list = QListWidget(self.source_card)
        self.source_list.setFixedHeight(118)
        self.source_list.setVisible(False)
        source_layout.addWidget(self.source_list)
        self.source_hint = QLabel(
            "单击图片切换预览；每张图片会分别记住裁剪位置。", self.source_card
        )
        self.source_hint.setObjectName("mutedLabel")
        self.source_hint.setWordWrap(True)
        self.source_hint.setVisible(False)
        source_layout.addWidget(self.source_hint)
        source_actions = QHBoxLayout()
        self.add_sources_button = QPushButton("＋ 添加图片", self.source_card)
        self.add_sources_button.setObjectName("linkButton")
        self.remove_source_button = QPushButton("移除所选", self.source_card)
        self.remove_source_button.setObjectName("linkButton")
        self.remove_source_button.setVisible(False)
        source_actions.addWidget(self.add_sources_button)
        source_actions.addStretch(1)
        source_actions.addWidget(self.remove_source_button)
        source_layout.addLayout(source_actions)
        layout.addWidget(self.source_card)
        self._update_source_ui()
        # The current release intentionally exposes a focused single-image flow.
        # Keep the queue implementation dormant for later product work.
        self.source_section.hide()
        self.source_card.hide()

        ratio_section = QLabel("1  裁剪", page)
        ratio_section.setObjectName("sectionTitle")
        layout.addWidget(ratio_section)
        ratio_card = QFrame(page)
        ratio_card.setObjectName("compactSection")
        ratio_layout = QVBoxLayout(ratio_card)
        ratio_layout.setContentsMargins(11, 10, 11, 11)
        ratio_layout.setSpacing(8)
        description = QLabel("选择比例后，在预览图上拖动或缩放裁剪框。", ratio_card)
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        ratio_layout.addWidget(description)

        self.ratio_card = ratio_card
        self.ratio_grid = QGridLayout()
        self.ratio_grid.setHorizontalSpacing(8)
        self.ratio_grid.setVerticalSpacing(8)
        ratio_layout.addLayout(self.ratio_grid)

        self.ratio_more_combo = QComboBox(ratio_card)
        self.ratio_more_combo.setObjectName("ratioMoreCombo")
        self.ratio_more_combo.setMinimumWidth(0)
        self.ratio_more_combo.setMinimumContentsLength(8)
        self.ratio_more_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        _style_combo_popup(self.ratio_more_combo, self._colors)
        self.ratio_more_combo.currentIndexChanged.connect(self._select_more_crop_ratio)
        self.custom_ratio_button = QPushButton("自定义…", ratio_card)
        self.custom_ratio_button.setObjectName("ratioOption")
        self.custom_ratio_button.setCheckable(True)
        self.custom_ratio_button.clicked.connect(self._choose_custom_crop_ratio)
        self._ratio_buttons["自定义"] = self.custom_ratio_button
        ratio_layout.addWidget(self.ratio_more_combo)
        self.manage_presets_button = QPushButton("管理比例与尺寸预设…", ratio_card)
        self.manage_presets_button.setObjectName("linkButton")
        self.manage_presets_button.clicked.connect(self._manage_crop_presets)
        ratio_actions = QGridLayout()
        ratio_actions.setHorizontalSpacing(8)
        ratio_actions.addWidget(self.custom_ratio_button, 0, 0)
        ratio_actions.addWidget(self.manage_presets_button, 0, 1)
        ratio_actions.setColumnStretch(0, 1)
        ratio_actions.setColumnStretch(1, 1)
        ratio_layout.addLayout(ratio_actions)

        self.size_preset_panel = QFrame(page)
        self.size_preset_panel.setObjectName("ratioPresetPanel")
        preset_layout = QVBoxLayout(self.size_preset_panel)
        preset_layout.setContentsMargins(11, 9, 11, 10)
        preset_layout.setSpacing(7)
        self.size_preset_heading = QLabel("推荐输出尺寸", self.size_preset_panel)
        self.size_preset_heading.setStyleSheet("font-weight: 700;")
        preset_layout.addWidget(self.size_preset_heading)
        self.size_preset_hint = QLabel(
            "已按当前裁剪比例筛选，选择后会直接更新下方宽高。",
            self.size_preset_panel,
        )
        self.size_preset_hint.setObjectName("mutedLabel")
        self.size_preset_hint.setWordWrap(True)
        preset_layout.addWidget(self.size_preset_hint)
        self.size_preset_grid = QGridLayout()
        self.size_preset_grid.setHorizontalSpacing(7)
        self.size_preset_grid.setVerticalSpacing(7)
        preset_layout.addLayout(self.size_preset_grid)
        self.size_preset_panel.hide()
        ratio_layout.addWidget(self.size_preset_panel)
        self._rebuild_preset_controls()

        self.crop_info = QLabel("", page)
        self.crop_info.setObjectName("mutedLabel")
        self.crop_info.setWordWrap(True)
        ratio_layout.addWidget(self.crop_info)

        crop_actions = QHBoxLayout()
        self.center_crop_button = QPushButton("居中裁剪", page)
        self.apply_crop_button = QPushButton("应用本次裁剪", page)
        self.center_crop_button.clicked.connect(self._center_crop)
        self.apply_crop_button.clicked.connect(self._apply_current_crop)
        crop_actions.addWidget(self.center_crop_button)
        crop_actions.addWidget(self.apply_crop_button, 1)
        ratio_layout.addLayout(crop_actions)
        compare_hint = QLabel("按住空格可临时对比打开时的原图。", page)
        compare_hint.setObjectName("mutedLabel")
        ratio_layout.addWidget(compare_hint)
        layout.addWidget(ratio_card)

        size_section = QLabel("2  输出尺寸", page)
        size_section.setObjectName("sectionTitle")
        layout.addWidget(size_section)
        size_card = QFrame(page)
        size_card.setObjectName("compactSection")
        size_layout = QVBoxLayout(size_card)
        size_layout.setContentsMargins(11, 10, 11, 11)
        size_layout.setSpacing(8)
        size_description = QLabel("不调整尺寸时，将按裁剪区域的大小导出。", size_card)
        size_description.setObjectName("mutedLabel")
        size_layout.addWidget(size_description)

        size_grid = QGridLayout()
        size_grid.setHorizontalSpacing(8)
        size_grid.setVerticalSpacing(5)
        self.width_spin = QSpinBox(size_card)
        self.height_spin = QSpinBox(size_card)
        for spin in (self.width_spin, self.height_spin):
            _configure_direct_number_input(spin)
            spin.setRange(1, 100000)
        self.width_spin.setValue(self._source_size[0])
        self.height_spin.setValue(self._source_size[1])
        width_label = QLabel("宽度", size_card)
        width_label.setObjectName("mutedLabel")
        height_label = QLabel("高度", size_card)
        height_label.setObjectName("mutedLabel")
        size_grid.addWidget(width_label, 0, 0)
        size_grid.addWidget(height_label, 0, 2)
        size_grid.addWidget(self.width_spin, 1, 0)
        separator = QLabel("×", size_card)
        separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        separator.setStyleSheet("font-weight: 700;")
        size_grid.addWidget(separator, 1, 1)
        size_grid.addWidget(self.height_spin, 1, 2)
        size_grid.setColumnStretch(0, 1)
        size_grid.setColumnStretch(2, 1)
        size_layout.addLayout(size_grid)

        self.ratio_lock_card = QFrame(page)
        self.ratio_lock_card.setObjectName("ratioLockCard")
        lock_layout = QHBoxLayout(self.ratio_lock_card)
        lock_layout.setContentsMargins(11, 8, 11, 8)
        self.lock_ratio = ViewerCheckBox("锁定宽高比例", self.ratio_lock_card)
        self.lock_ratio.setObjectName("clearCheckBox")
        self.lock_ratio.setChecked(True)
        self.lock_ratio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_ratio_status = QLabel("已锁定", self.ratio_lock_card)
        lock_layout.addWidget(self.lock_ratio)
        lock_layout.addStretch(1)
        lock_layout.addWidget(self.lock_ratio_status)
        size_layout.addWidget(self.ratio_lock_card)
        layout.addWidget(size_card)

        format_section = QLabel("3  导出格式", page)
        format_section.setObjectName("sectionTitle")
        layout.addWidget(format_section)
        self.edit_format_group = QButtonGroup(self)
        self.edit_format_group.setExclusive(True)
        common_format_grid = QGridLayout()
        common_format_grid.setHorizontalSpacing(7)
        for index, output_format in enumerate(("PNG", "JPEG")):
            button = QPushButton(FORMAT_LABELS[output_format], page)
            button.setObjectName("formatCard")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=output_format: self._select_output_format(value)
            )
            self.edit_format_group.addButton(button)
            self.format_buttons[output_format] = button
            common_format_grid.addWidget(button, 0, index)
        layout.addLayout(common_format_grid)

        self.other_format_combo = QComboBox(page)
        self.other_format_combo.addItem("更多输出格式…", "")
        for group_name, output_formats in FORMAT_GROUPS:
            uncommon_formats = tuple(
                output_format
                for output_format in output_formats
                if output_format not in {"PNG", "JPEG"}
            )
            if not uncommon_formats:
                continue
            for output_format in uncommon_formats:
                label = FORMAT_LABELS[output_format].replace("\n", " · ")
                self.other_format_combo.addItem(f"{group_name} · {label}", output_format)
                hidden_button = QPushButton(page)
                hidden_button.setCheckable(True)
                hidden_button.clicked.connect(
                    lambda _checked=False, value=output_format: self._select_output_format(value)
                )
                hidden_button.hide()
                self.edit_format_group.addButton(hidden_button)
                self.format_buttons[output_format] = hidden_button
        _style_combo_popup(self.other_format_combo, self._colors)
        self.other_format_combo.currentIndexChanged.connect(self._select_other_output_format)
        layout.addWidget(self.other_format_combo)

        self.edit_quality_panel = QWidget(page)
        quality_layout = QVBoxLayout(self.edit_quality_panel)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.setSpacing(5)
        quality_heading = QHBoxLayout()
        quality_heading.addWidget(QLabel("输出质量", self.edit_quality_panel))
        quality_heading.addStretch(1)
        self.edit_quality_value = QLabel("92", self.edit_quality_panel)
        self.edit_quality_value.setStyleSheet("font-weight: 600;")
        quality_heading.addWidget(self.edit_quality_value)
        self.edit_quality_slider = QSlider(Qt.Orientation.Horizontal, self.edit_quality_panel)
        self.edit_quality_slider.setRange(1, 100)
        self.edit_quality_slider.setValue(92)
        quality_layout.addLayout(quality_heading)
        quality_layout.addWidget(self.edit_quality_slider)
        layout.addWidget(self.edit_quality_panel)

        self.target_size_row = QWidget(page)
        target_size_layout = QHBoxLayout(self.target_size_row)
        target_size_layout.setContentsMargins(0, 0, 0, 0)
        self.target_size_enabled = ViewerCheckBox("限制文件大小", self.target_size_row)
        self.target_size_enabled.setToolTip(
            "仅用于 JPEG 和 WebP。启用后会自动降低品质，确保文件不超过目标大小。"
        )
        self.target_size_spin = QSpinBox(self.target_size_row)
        _configure_direct_number_input(self.target_size_spin)
        self.target_size_spin.setRange(10, 999_999)
        self.target_size_spin.setValue(500)
        self.target_size_spin.setSuffix(" KB")
        self.target_size_spin.setEnabled(False)
        self.target_size_spin.setToolTip("目标文件大小上限；无法达到时会明确提示失败。")
        target_size_layout.addWidget(self.target_size_enabled)
        target_size_layout.addStretch(1)
        target_size_layout.addWidget(self.target_size_spin)
        layout.addWidget(self.target_size_row)

        self.edit_animation_row = QWidget(page)
        edit_animation_layout = QHBoxLayout(self.edit_animation_row)
        edit_animation_layout.setContentsMargins(0, 0, 0, 0)
        edit_animation_layout.addWidget(QLabel("动画导出", self.edit_animation_row))
        self.edit_animation_mode = QComboBox(self.edit_animation_row)
        self.edit_animation_mode.addItem("保留全部动画帧", "preserve")
        self.edit_animation_mode.addItem("仅导出当前帧", "current")
        _style_combo_popup(self.edit_animation_mode, self._colors)
        edit_animation_layout.addWidget(self.edit_animation_mode, 1)
        layout.addWidget(self.edit_animation_row)

        self.edit_tga_rle = QCheckBox("TGA 使用 RLE 无损压缩", page)
        self.edit_tga_rle.setChecked(True)
        layout.addWidget(self.edit_tga_rle)

        self.edit_dds_row = QWidget(page)
        edit_dds_layout = QHBoxLayout(self.edit_dds_row)
        edit_dds_layout.setContentsMargins(0, 0, 0, 0)
        edit_dds_layout.addWidget(QLabel("DDS 像素格式", self.edit_dds_row))
        self.edit_dds_pixel_format = QComboBox(self.edit_dds_row)
        for label, encoding_value in (
            ("BC3 / DXT5（含透明）", "DXT5"),
            ("BC1 / DXT1（小体积）", "DXT1"),
            ("BC5（法线贴图）", "BC5"),
            ("RGBA8（不压缩）", "RGBA8"),
        ):
            self.edit_dds_pixel_format.addItem(label, encoding_value)
        _style_combo_popup(self.edit_dds_pixel_format, self._colors)
        edit_dds_layout.addWidget(self.edit_dds_pixel_format, 1)
        layout.addWidget(self.edit_dds_row)

        self.edit_ktx2_row = QWidget(page)
        edit_ktx2_layout = QHBoxLayout(self.edit_ktx2_row)
        edit_ktx2_layout.setContentsMargins(0, 0, 0, 0)
        edit_ktx2_layout.addWidget(QLabel("KTX2 编码", self.edit_ktx2_row))
        self.edit_ktx2_mode = QComboBox(self.edit_ktx2_row)
        self.edit_ktx2_mode.addItem("UASTC（高质量）", "uastc")
        self.edit_ktx2_mode.addItem("ETC1S（小体积）", "etc1s")
        self.edit_ktx2_mode.addItem("RGBA8（不压缩）", "rgba8")
        _style_combo_popup(self.edit_ktx2_mode, self._colors)
        edit_ktx2_layout.addWidget(self.edit_ktx2_mode, 1)
        layout.addWidget(self.edit_ktx2_row)

        self.edit_mipmap_enabled = QCheckBox("生成完整 Mipmap 链", page)
        self.edit_mipmap_enabled.setChecked(True)
        layout.addWidget(self.edit_mipmap_enabled)

        self.edit_format_notice = QLabel("", page)
        self.edit_format_notice.setObjectName("mutedLabel")
        self.edit_format_notice.setWordWrap(True)
        layout.addWidget(self.edit_format_notice)

        self.naming_toggle = QPushButton("文件命名", page)
        self.naming_toggle.setObjectName("sectionToggle")
        self.naming_toggle.setCheckable(True)
        self.naming_toggle.setMinimumWidth(0)
        self.naming_toggle.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self.naming_toggle)
        self.naming_panel = QFrame(page)
        self.naming_panel.setObjectName("compactSection")
        naming_panel_layout = QVBoxLayout(self.naming_panel)
        naming_panel_layout.setContentsMargins(11, 10, 11, 11)
        naming_panel_layout.setSpacing(8)
        naming_hint = QLabel("需要添加前后缀或调整重名规则时再展开设置。", self.naming_panel)
        naming_hint.setObjectName("mutedLabel")
        naming_hint.setWordWrap(True)
        naming_panel_layout.addWidget(naming_hint)
        naming_form = QFormLayout()
        naming_form.setVerticalSpacing(9)
        self.filename_prefix = QLineEdit("", self.naming_panel)
        self.filename_suffix = QLineEdit("_edited", self.naming_panel)
        self.conflict_combo = QComboBox(self.naming_panel)
        self.conflict_combo.addItem("自动编号，不覆盖", "unique")
        self.conflict_combo.addItem("已存在则跳过", "skip")
        self.conflict_combo.addItem("覆盖已有结果", "overwrite")
        _style_combo_popup(self.conflict_combo, self._colors)
        naming_form.addRow("前缀", self.filename_prefix)
        naming_form.addRow("后缀", self.filename_suffix)
        naming_form.addRow("重名时", self.conflict_combo)
        naming_panel_layout.addLayout(naming_form)
        self.naming_panel.hide()
        layout.addWidget(self.naming_panel)
        layout.addStretch(1)

        self.width_spin.valueChanged.connect(lambda value: self._size_changed("width", value))
        self.height_spin.valueChanged.connect(lambda value: self._size_changed("height", value))
        self.lock_ratio.toggled.connect(self._update_ratio_lock_state)
        self.edit_quality_slider.valueChanged.connect(
            lambda value: self.edit_quality_value.setText(str(value))
        )
        self.edit_quality_slider.valueChanged.connect(self._update_summary)
        self.target_size_enabled.toggled.connect(self.target_size_spin.setEnabled)
        self.target_size_enabled.toggled.connect(self._update_summary)
        self.target_size_spin.valueChanged.connect(self._update_summary)
        self.edit_animation_mode.currentIndexChanged.connect(self._update_summary)
        self.edit_tga_rle.toggled.connect(self._update_summary)
        self.edit_dds_pixel_format.currentIndexChanged.connect(self._update_summary)
        self.edit_ktx2_mode.currentIndexChanged.connect(self._update_summary)
        self.edit_mipmap_enabled.toggled.connect(self._update_summary)
        self.filename_prefix.textChanged.connect(self._update_summary)
        self.filename_suffix.textChanged.connect(self._update_summary)
        self.conflict_combo.currentIndexChanged.connect(self._update_summary)
        self.add_sources_button.clicked.connect(self._choose_more_sources)
        self.remove_source_button.clicked.connect(self._remove_selected_sources)
        self.source_list.currentItemChanged.connect(self._source_selection_changed)
        self.naming_toggle.toggled.connect(self._toggle_naming_panel)
        self._update_ratio_lock_state(True)
        return page

    @property
    def preset_catalog(self) -> CropPresetCatalog:
        return self._preset_catalog

    def _rebuild_preset_controls(self) -> None:
        """Recreate ratio/size buttons from the validated portable catalog."""

        for label, button in tuple(self._ratio_buttons.items()):
            if button is self.custom_ratio_button:
                continue
            self._ratio_buttons.pop(label, None)
            self.ratio_grid.removeWidget(button)
            button.deleteLater()
        while self.ratio_grid.count():
            self.ratio_grid.takeAt(0)

        for button in self.size_preset_buttons.values():
            self.size_preset_grid.removeWidget(button)
            button.deleteLater()
        self.size_preset_buttons.clear()

        options: list[tuple[str, float | None]] = [
            ("自由", None),
            ("原图", self._source_size[0] / max(1, self._source_size[1])),
        ]
        options.extend((preset.label, preset.value) for preset in self._preset_catalog.presets)
        quick_labels = {"自由", "原图", "1:1", "16:9"}
        quick_index = 0
        for label, ratio in options:
            button = QPushButton(label, self.ratio_card)
            button.setObjectName("ratioOption")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, name=label, value=ratio: self._apply_crop_ratio(name, value)
            )
            self._ratio_buttons[label] = button
            if label in quick_labels:
                self.ratio_grid.addWidget(button, quick_index // 2, quick_index % 2)
                quick_index += 1
            else:
                button.hide()
        self._ratio_buttons["自定义"] = self.custom_ratio_button

        blocked = self.ratio_more_combo.blockSignals(True)
        try:
            self.ratio_more_combo.clear()
            self.ratio_more_combo.addItem("更多比例", "")
            for preset in self._preset_catalog.presets:
                if preset.label not in quick_labels:
                    self.ratio_more_combo.addItem(preset.label, preset.label)
        finally:
            self.ratio_more_combo.blockSignals(blocked)

        for preset in self._preset_catalog.presets:
            for width, height in preset.sizes:
                button = QPushButton(f"{width} × {height}", self.size_preset_panel)
                button.setObjectName("sizePresetButton")
                button.clicked.connect(
                    lambda _checked=False, w=width, h=height: self._apply_size_preset(w, h)
                )
                button.hide()
                self.size_preset_buttons[(width, height)] = button

        if self._active_ratio_label not in self._ratio_buttons:
            self._active_ratio_label = "自由"
            self._active_ratio_value = None
        for label, button in self._ratio_buttons.items():
            button.setChecked(label == self._active_ratio_label)
        self._update_size_preset_visibility(self._active_ratio_value)

    def _manage_crop_presets(self) -> None:
        dialog = CropPresetManagerDialog(self._preset_catalog, self._colors, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._replace_preset_catalog(dialog.catalog())

    def _replace_preset_catalog(self, catalog: CropPresetCatalog) -> None:
        self._preset_catalog = catalog
        active = catalog.preset(self._active_ratio_label)
        if self._active_ratio_label not in {"自由", "原图", "自定义"} and active is None:
            self._active_ratio_label = "自由"
            self._active_ratio_value = None
        elif active is not None:
            self._active_ratio_value = active.value
        self._rebuild_preset_controls()
        self._apply_crop_ratio(
            self._active_ratio_label,
            self._active_ratio_value,
            preserve_inactive=True,
        )
        self.presetCatalogChanged.emit(catalog)
        self.status_label.setText("比例与尺寸预设已保存")

    def _select_more_crop_ratio(self, _index: int) -> None:
        label = str(self.ratio_more_combo.currentData() or "")
        if not label:
            return
        button = self._ratio_buttons.get(label)
        if button is not None:
            button.click()

    def _toggle_naming_panel(self, visible: bool) -> None:
        self.naming_panel.setVisible(visible)
        self._update_naming_toggle()

    def _update_naming_toggle(self) -> None:
        if not hasattr(self, "naming_toggle"):
            return
        marker = "▴" if self.naming_toggle.isChecked() else "▾"
        self.naming_toggle.setText(f"文件命名  ·  {self._output_filename()}   {marker}")

    def _apply_crop_ratio(
        self,
        label: str,
        ratio: float | None,
        *,
        preserve_inactive: bool = False,
    ) -> None:
        if label == "原图" and self._source_size[1] > 0:
            ratio = self._source_size[0] / self._source_size[1]
        if not preserve_inactive:
            for path, state in self._source_states.items():
                if path != self._active_source_path:
                    state.crop_box = None
        for name, button in self._ratio_buttons.items():
            button.setChecked(name == label)
        combo_blocked = self.ratio_more_combo.blockSignals(True)
        try:
            combo_index = self.ratio_more_combo.findData(label)
            self.ratio_more_combo.setCurrentIndex(max(0, combo_index))
        finally:
            self.ratio_more_combo.blockSignals(combo_blocked)
        self._active_ratio_label = label
        self._active_ratio_value = ratio
        self.canvas.set_aspect_ratio(ratio)
        self._update_size_preset_visibility(ratio)

    def _update_size_preset_visibility(self, ratio: float | None) -> None:
        if not hasattr(self, "size_preset_panel"):
            return
        for button in self.size_preset_buttons.values():
            self.size_preset_grid.removeWidget(button)
            button.hide()
        matching = (
            []
            if ratio is None or ratio <= 0
            else [
                (size, button)
                for size, button in self.size_preset_buttons.items()
                if abs((size[0] / size[1]) - ratio) < 0.0001
            ]
        )
        for index, (_size, button) in enumerate(matching):
            self.size_preset_grid.addWidget(button, index // 2, index % 2)
            button.show()
        orientation = (
            "横屏"
            if ratio is not None and ratio > 1.0001
            else "竖屏"
            if ratio is not None and ratio < 0.9999
            else "正方形"
        )
        self.size_preset_heading.setText(f"{self._active_ratio_label} · {orientation}输出尺寸")
        self.size_preset_hint.setText(
            f"当前为{orientation}比例；选择尺寸后会直接更新下方宽度和高度。"
        )
        self.size_preset_panel.setVisible(bool(matching))

    def _choose_custom_crop_ratio(self) -> None:
        dialog = CustomCropRatioDialog(
            self._custom_ratio[0],
            self._custom_ratio[1],
            self._colors,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            for name, button in self._ratio_buttons.items():
                button.setChecked(name == self._active_ratio_label)
            return
        self._apply_custom_crop_ratio(*dialog.ratio_values())

    def _apply_custom_crop_ratio(self, width_ratio: int, height_ratio: int) -> None:
        divisor = gcd(max(1, width_ratio), max(1, height_ratio))
        normalized_width = max(1, width_ratio) // divisor
        normalized_height = max(1, height_ratio) // divisor
        self._custom_ratio = (normalized_width, normalized_height)
        self.custom_ratio_button.setText(f"自定义  {normalized_width}:{normalized_height}")
        self._apply_crop_ratio("自定义", normalized_width / normalized_height)
        self.status_label.setText(f"已应用自定义裁剪比例 {normalized_width}:{normalized_height}")

    def _center_crop(self) -> None:
        self.canvas.center_crop()
        self.status_label.setText("裁剪区域已居中")

    def _apply_current_crop(self) -> None:
        crop_box = self.canvas.crop_box()
        full_box = (0, 0, self._source_size[0], self._source_size[1])
        if crop_box == full_box:
            self.status_label.setText("当前已经是完整画面，无需应用裁剪")
            return
        left, top, right, bottom = crop_box
        current = self.canvas.image()
        cropped = current.copy(left, top, right - left, bottom - top)
        base_left, base_top, _base_right, _base_bottom = self._active_base_box
        self._active_base_box = (
            base_left + left,
            base_top + top,
            base_left + right,
            base_top + bottom,
        )
        self._source_size = (cropped.width(), cropped.height())
        self._working_revision += 1
        self._size_dirty = False
        self.canvas.set_image(cropped)
        self._apply_crop_ratio("自由", None, preserve_inactive=True)
        self._apply_size_percent(100)
        self._snapshot_active_source_state()
        self.status_label.setText("已应用本次裁剪，可继续设置下一次裁剪")

    @property
    def sources(self) -> tuple[Path, ...]:
        return tuple(self._sources)

    @property
    def active_source_path(self) -> Path:
        return self._active_source_path

    @property
    def requested_source_path(self) -> Path:
        return self._requested_source_path

    def _snapshot_active_source_state(self) -> None:
        state = self._source_states.get(self._active_source_path)
        if state is None:
            state = _BatchSourceState(
                self._initial_source_size,
                self._active_base_box,
            )
            self._source_states[self._active_source_path] = state
        state.base_box = self._active_base_box
        state.crop_box = self.canvas.crop_box()
        state.working_revision = self._working_revision

    @staticmethod
    def _compose_source_crop_box(
        state: _BatchSourceState,
        fallback_ratio: float | None,
    ) -> tuple[int, int, int, int] | None:
        base_left, base_top, base_right, base_bottom = state.base_box
        crop = state.crop_box
        if crop is None:
            base_width = base_right - base_left
            base_height = base_bottom - base_top
            if fallback_ratio is None or fallback_ratio <= 0:
                crop = (0, 0, base_width, base_height)
            else:
                crop_width = base_width
                crop_height = round(crop_width / fallback_ratio)
                if crop_height > base_height:
                    crop_height = base_height
                    crop_width = round(crop_height * fallback_ratio)
                crop_left = max(0, (base_width - crop_width) // 2)
                crop_top = max(0, (base_height - crop_height) // 2)
                crop = (
                    crop_left,
                    crop_top,
                    crop_left + crop_width,
                    crop_top + crop_height,
                )
        left, top, right, bottom = crop
        result = (
            base_left + left,
            base_top + top,
            base_left + right,
            base_top + bottom,
        )
        full = (0, 0, state.original_size[0], state.original_size[1])
        return None if result == full else result

    def set_source_preview(self, path: Path, image: QImage) -> None:
        """Display a loaded batch source and restore its own crop state."""

        normalized = path.resolve()
        if normalized != self._requested_source_path or normalized not in self._sources:
            return
        self._snapshot_active_source_state()
        self._source_states = {
            source: state
            for source, state in self._source_states.items()
            if source in self._sources
        }
        width, height = image.width(), image.height()
        if width < 1 or height < 1:
            self.set_source_preview_error(normalized, "图片没有可显示的像素")
            return
        state = self._source_states.get(normalized)
        if state is None or state.original_size != (width, height):
            full_box = (0, 0, width, height)
            state = _BatchSourceState((width, height), full_box)
            self._source_states[normalized] = state

        base_left, base_top, base_right, base_bottom = state.base_box
        base_left = max(0, min(base_left, width - 1))
        base_top = max(0, min(base_top, height - 1))
        base_right = max(base_left + 1, min(base_right, width))
        base_bottom = max(base_top + 1, min(base_bottom, height))
        self._active_base_box = (base_left, base_top, base_right, base_bottom)
        working = image.copy(
            base_left,
            base_top,
            base_right - base_left,
            base_bottom - base_top,
        )

        self._active_source_path = normalized
        self._requested_source_path = normalized
        self._original_image = image.copy()
        self._initial_source_size = (width, height)
        self._source_size = (working.width(), working.height())
        self._working_revision = state.working_revision
        ratio = self._active_ratio_value
        if self._active_ratio_label == "原图" and self._source_size[1] > 0:
            ratio = self._source_size[0] / self._source_size[1]
            self._active_ratio_value = ratio

        signals_blocked = self.canvas.blockSignals(True)
        try:
            self.canvas.set_image(working)
            self.canvas._aspect_ratio = ratio
            if state.crop_box is None:
                if ratio is None:
                    self.canvas.reset_crop()
                else:
                    self.canvas.set_aspect_ratio(ratio)
            else:
                left, top, right, bottom = state.crop_box
                self.canvas._set_crop_rect(QRectF(left, top, right - left, bottom - top))
        finally:
            self.canvas.blockSignals(signals_blocked)
        self._crop_changed(self.canvas.crop_box())
        self.subtitle.setText(
            f"{normalized.name}  ·  {width} × {height}  ·  "
            "裁剪位置按图片分别记忆，输出设置应用到整个批次"
        )
        self.status_label.setText(f"当前处理：{normalized.name}")
        self._update_source_ui()
        self._update_summary()

    def set_source_preview_error(self, path: Path, message: str) -> None:
        if path.resolve() != self._requested_source_path:
            return
        self._requested_source_path = self._active_source_path
        self.status_label.setText(f"无法切换到 {path.name}：{message}")
        self._update_source_ui()

    def _source_selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None or self._busy:
            return
        raw_path = current.data(Qt.ItemDataRole.UserRole)
        if not raw_path:
            return
        path = Path(str(raw_path)).resolve()
        self.remove_source_button.setEnabled(path != self.source_path)
        if path == self._active_source_path:
            self._requested_source_path = path
            return
        self._requested_source_path = path
        self.status_label.setText(f"正在载入 {path.name}…")
        self.sourcePreviewRequested.emit(path)

    def add_sources(self, paths: list[Path]) -> None:
        known = {str(path).casefold() for path in self._sources}
        for path in paths:
            try:
                normalized = path.resolve()
            except OSError:
                normalized = path.absolute()
            key = str(normalized).casefold()
            if normalized.is_file() and key not in known:
                known.add(key)
                self._sources.append(normalized)
        self._update_source_ui()
        self._update_summary()

    def _choose_more_sources(self) -> None:
        patterns = (
            "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.apng *.tif *.tiff *.avif "
            "*.heic *.heif *.psd *.psdt *.svg *.ai *.eps *.emf *.wmf"
        )
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "添加待处理图片",
            str(self.source_path.parent),
            f"支持的图片 ({patterns})",
        )
        if selected:
            self.add_sources([Path(path) for path in selected])

    def _remove_selected_sources(self) -> None:
        selected = {
            Path(str(item.data(Qt.ItemDataRole.UserRole))).resolve()
            for item in self.source_list.selectedItems()
        }
        selected.discard(self.source_path)
        if not selected:
            self.status_label.setText("初始图片会保留在处理队列中")
            return
        active_removed = self._active_source_path in selected
        self._sources = [path for path in self._sources if path not in selected]
        for path in selected:
            self._source_states.pop(path, None)
        if self._requested_source_path in selected:
            self._requested_source_path = self.source_path
        self._update_source_ui()
        self._update_summary()
        if active_removed:
            self.status_label.setText(f"正在返回 {self.source_path.name}…")
            self.sourcePreviewRequested.emit(self.source_path)

    def _update_source_ui(self) -> None:
        count = len(self._sources)
        if count == 1:
            self.source_summary.setText(self.source_path.name)
        else:
            self.source_summary.setText(
                f"批量处理 · 共 {count} 张图片\n当前：{self._active_source_path.name}"
            )
        previous_block = self.source_list.blockSignals(True)
        try:
            self.source_list.clear()
            selected_item: QListWidgetItem | None = None
            selection_path = (
                self._requested_source_path
                if self._requested_source_path in self._sources
                else self._active_source_path
            )
            for path in self._sources:
                item = QListWidgetItem(path.name, self.source_list)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                if path == self.source_path:
                    item.setToolTip("初始图片 · 单击切换预览 · 始终保留在队列中")
                else:
                    item.setToolTip("单击切换预览与裁剪；可从队列移除")
                if path == selection_path:
                    selected_item = item
            if selected_item is not None:
                self.source_list.setCurrentItem(selected_item)
        finally:
            self.source_list.blockSignals(previous_block)
        self.source_list.setVisible(count > 1)
        self.source_hint.setVisible(count > 1)
        self.remove_source_button.setVisible(count > 1)
        self.remove_source_button.setEnabled(selection_path != self.source_path)

    def _update_ratio_lock_state(self, checked: bool) -> None:
        self.ratio_lock_card.setProperty("active", checked)
        self.lock_ratio_status.setText("已锁定" if checked else "未锁定")
        self.lock_ratio_status.setProperty("active", checked)
        self.lock_ratio_status.setStyleSheet(
            f"font-weight: 600; color: {self._colors['accent']};"
            if checked
            else f"font-weight: 400; color: {self._colors['muted']};"
        )
        style = self.ratio_lock_card.style()
        style.unpolish(self.ratio_lock_card)
        style.polish(self.ratio_lock_card)
        self.ratio_lock_card.update()

    def _choose_output_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录",
            str(self._output_directory),
        )
        if not selected:
            return
        self.set_output_directory(Path(selected))

    def set_output_directory(self, directory: Path) -> None:
        self._output_directory = directory.resolve()
        self.output_path_edit.setText(str(self._output_directory))
        self.output_path_edit.setToolTip(str(self._output_directory))
        self._update_summary()

    def _open_output_directory(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_directory)))

    def _confirm_reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "重置处理设置",
            "确定恢复图片、裁剪、尺寸、格式和文件命名的初始设置吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._reset_all()

    def _reset_all(self) -> None:
        self._active_source_path = self.source_path
        self._requested_source_path = self.source_path
        self._original_image = self._primary_original_image.copy()
        self._initial_source_size = self._primary_source_size
        self._source_size = self._primary_source_size
        self._active_base_box = (0, 0, *self._primary_source_size)
        self._working_revision = 0
        self._size_dirty = False
        self.canvas.set_image(self._primary_original_image)
        self._apply_crop_ratio("自由", None)
        self._apply_size_percent(100)
        self._select_output_format(self._initial_output_format)
        self.edit_quality_slider.setValue(92)
        self.target_size_enabled.setChecked(False)
        self.target_size_spin.setValue(500)
        self.edit_animation_mode.setCurrentIndex(0)
        self.edit_tga_rle.setChecked(True)
        self.edit_dds_pixel_format.setCurrentIndex(0)
        self.edit_ktx2_mode.setCurrentIndex(0)
        self.edit_mipmap_enabled.setChecked(True)
        self.filename_prefix.setText("")
        self.filename_suffix.setText("_edited")
        self.conflict_combo.setCurrentIndex(0)
        self._sources = [self.source_path]
        self._source_states = {
            self.source_path: _BatchSourceState(
                self._primary_source_size,
                self._active_base_box,
                self._active_base_box,
            )
        }
        self._output_directory = self.source_path.parent.resolve()
        self.output_path_edit.setText(str(self._output_directory))
        self.output_path_edit.setToolTip(str(self._output_directory))
        self._update_source_ui()
        self.status_label.setText("已恢复初始设置")
        self._update_summary()

    @property
    def output_format(self) -> str:
        for output_format, button in self.format_buttons.items():
            if button.isChecked():
                return output_format
        return self._initial_output_format

    def _select_other_output_format(self, _index: int) -> None:
        output_format = self.other_format_combo.currentData()
        if isinstance(output_format, str) and output_format:
            self._select_output_format(output_format)

    def _select_output_format(self, output_format: str) -> None:
        if output_format not in self.format_buttons:
            output_format = "PNG"
        self.format_buttons[output_format].setChecked(True)
        combo_blocked = self.other_format_combo.blockSignals(True)
        try:
            combo_index = (
                self.other_format_combo.findData(output_format)
                if output_format not in {"PNG", "JPEG"}
                else 0
            )
            self.other_format_combo.setCurrentIndex(max(0, combo_index))
        finally:
            self.other_format_combo.blockSignals(combo_blocked)
        self.edit_quality_panel.setVisible(output_format in {"JPEG", "WEBP", "AVIF", "KTX2"})
        self.target_size_row.setVisible(output_format in {"JPEG", "WEBP"})
        self.edit_animation_row.setVisible(output_format in {"GIF", "APNG"})
        self.edit_tga_rle.setVisible(output_format == "TGA")
        self.edit_dds_row.setVisible(output_format == "DDS")
        self.edit_ktx2_row.setVisible(output_format == "KTX2")
        self.edit_mipmap_enabled.setVisible(output_format in {"DDS", "KTX2"})
        notices = {
            "ICO": "一次写入 16、24、32、48、64、128、256 七种尺寸。",
            "EXR": "线性 RGB、半精度浮点 RGBA、ZIP 无损压缩。",
            "HDR": "Radiance RGBE 高动态范围；不保留透明通道。",
            "SVG": "嵌入位图以保留外观，不会自动描摹矢量路径。",
            "AI": "Illustrator 可打开的 EPS 位图容器，不会自动矢量化。",
            "EPS": "PostScript 位图容器，不会自动矢量化。",
            "EMF": "Windows 图元文件位图记录，不会自动矢量化。",
            "WMF": "Windows 图元文件位图记录，不会自动矢量化。",
        }
        notice = notices.get(output_format, "")
        self.edit_format_notice.setText(notice)
        self.edit_format_notice.setVisible(bool(notice))
        self._update_summary()

    def _output_filename(self, source: Path | None = None) -> str:
        source = source or self._active_source_path
        prefix = self.filename_prefix.text().strip()
        suffix = self.filename_suffix.text().strip()
        extension = extension_for_format(self.output_format)
        if not prefix and not suffix and source.suffix.casefold() == extension.casefold():
            suffix = "_副本"
        return f"{prefix}{source.stem}{suffix}{extension}"

    def _base_size(self) -> tuple[int, int]:
        left, top, right, bottom = self.canvas.crop_box()
        return right - left, bottom - top

    def _size_changed(self, source: str, value: int) -> None:
        if self._syncing_size:
            return
        self._size_dirty = True
        if self.lock_ratio.isChecked():
            base_width, base_height = self._base_size()
            if base_width > 0 and base_height > 0:
                self._syncing_size = True
                if source == "width":
                    self.height_spin.setValue(max(1, round(value * base_height / base_width)))
                else:
                    self.width_spin.setValue(max(1, round(value * base_width / base_height)))
                self._syncing_size = False
        self._update_summary()

    def _apply_size_percent(self, percent: int) -> None:
        base_width, base_height = self._base_size()
        self._syncing_size = True
        self.width_spin.setValue(max(1, round(base_width * percent / 100)))
        self.height_spin.setValue(max(1, round(base_height * percent / 100)))
        self._syncing_size = False
        self._size_dirty = percent != 100
        self._update_summary()

    def _apply_size_preset(self, width: int, height: int) -> None:
        if self.lock_ratio.isChecked():
            ratio = width / height
            matching_preset = next(
                (
                    preset
                    for preset in self._preset_catalog.presets
                    if (width, height) in preset.sizes or abs(preset.value - ratio) < 0.0001
                ),
                None,
            )
            if matching_preset is None:
                self._apply_custom_crop_ratio(width, height)
            else:
                self._apply_crop_ratio(matching_preset.label, matching_preset.value)
        self._syncing_size = True
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)
        self._syncing_size = False
        self._size_dirty = (width, height) != self._base_size()
        self._update_summary()

    def _crop_changed(self, box: object) -> None:
        if self._comparing:
            return
        if not isinstance(box, tuple) or len(box) != 4:
            return
        left, top, right, bottom = (int(value) for value in box)
        crop_width, crop_height = right - left, bottom - top
        self.crop_info.setText(f"区域：{crop_width} × {crop_height}  ·  位置：{left}, {top}")
        if not self._size_dirty:
            self._syncing_size = True
            self.width_spin.setValue(max(1, crop_width))
            self.height_spin.setValue(max(1, crop_height))
            self._syncing_size = False
        self._update_summary()

    def _update_summary(self) -> None:
        if not hasattr(self, "output_summary"):
            return
        base_width, base_height = self._base_size()
        self.crop_info.setText(
            f"区域：{base_width} × {base_height}  ·  "
            f"输出：{self.width_spin.value()} × {self.height_spin.value()}"
        )
        target_note = ""
        if self.output_format in {"JPEG", "WEBP"} and self.target_size_enabled.isChecked():
            target_note = f"  ·  ≤ {self.target_size_spin.value()} KB"
        batch_note = f"  ·  {len(self._sources)} 张" if len(self._sources) > 1 else ""
        display_format = "JPG" if self.output_format == "JPEG" else self.output_format
        self.output_summary.setText(
            f"{self._output_filename()}\n"
            f"{self.width_spin.value()} × {self.height_spin.value()}  ·  "
            f"{display_format}{target_note}{batch_note}"
        )
        active_source = self._active_source_path
        preview_text = (
            f"{self._output_filename(active_source)}  ·  "
            f"{self.width_spin.value()} × {self.height_spin.value()}  ·  {display_format}"
        )
        batch_preview = f" · 批量共 {len(self._sources)} 张" if len(self._sources) > 1 else ""
        self.output_preview_line.setText(preview_text + batch_preview)
        self.output_preview_line.setToolTip(preview_text + batch_preview)
        self._update_naming_toggle()

    def request(self) -> ImageEditRequest:
        self._snapshot_active_source_state()
        crop_box = self.canvas.crop_box()
        full_box = (0, 0, self._source_size[0], self._source_size[1])
        crop_aspect_ratio = (
            (crop_box[2] - crop_box[0]) / (crop_box[3] - crop_box[1])
            if (crop_box != full_box or self._working_revision > 0)
            else self._active_ratio_value
        )
        source_crop_boxes = tuple(
            (path, self._compose_source_crop_box(state, crop_aspect_ratio))
            for path in self._sources
            if (state := self._source_states.get(path)) is not None
        )
        return ImageEditRequest(
            crop_box=None if crop_box == full_box else crop_box,
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            output_directory=self._output_directory,
            output_format=self.output_format,
            quality=self.edit_quality_slider.value(),
            source_paths=tuple(self._sources),
            filename_prefix=self.filename_prefix.text().strip(),
            filename_suffix=self.filename_suffix.text().strip(),
            conflict_policy=str(self.conflict_combo.currentData()),
            target_size_kb=(
                self.target_size_spin.value()
                if self.output_format in {"JPEG", "WEBP"} and self.target_size_enabled.isChecked()
                else None
            ),
            crop_aspect_ratio=crop_aspect_ratio,
            resize_enabled=self._size_dirty,
            animation_mode=str(self.edit_animation_mode.currentData()),
            tga_rle=self.edit_tga_rle.isChecked(),
            dds_pixel_format=str(self.edit_dds_pixel_format.currentData()),
            generate_mipmaps=self.edit_mipmap_enabled.isChecked(),
            ktx2_mode=str(self.edit_ktx2_mode.currentData()),
            source_crop_boxes=source_crop_boxes,
        )

    def _state_signature(self) -> tuple[object, ...]:
        request = self.request()
        return (
            self._working_revision,
            request.crop_box,
            request.width,
            request.height,
            request.resize_enabled,
            request.output_format,
            request.quality,
            request.target_size_kb,
            request.output_directory,
            request.source_paths,
            request.filename_prefix,
            request.filename_suffix,
            request.conflict_policy,
            request.animation_mode,
            request.tga_rle,
            request.dds_pixel_format,
            request.generate_mipmaps,
            request.ico_sizes,
            request.ktx2_mode,
            request.source_crop_boxes,
        )

    def is_dirty(self) -> bool:
        return self._state_signature() != self._clean_signature

    def mark_exported(self, message: str = "已导出") -> None:
        self._clean_signature = self._state_signature()
        self.status_label.setText(message)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.source_list.setEnabled(not busy)
        for button in (
            self.reset_button,
            self.quick_export_button,
            self.save_button,
            self.add_sources_button,
            self.remove_source_button,
        ):
            button.setEnabled(not busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
            self.status_label.setText("正在准备本地图片处理…")

    def set_progress(self, completed: int, total: int, filename: str) -> None:
        self.progress.setVisible(True)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(completed)
        self.status_label.setText(f"正在处理 {completed + 1}/{total} · {filename}")

    def set_result_summary(self, success: int, skipped: int, failed: int) -> None:
        self.set_busy(False)
        total = success + skipped + failed
        self.progress.setVisible(total > 1)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(total)
        parts = [f"成功 {success}"]
        if skipped:
            parts.append(f"跳过 {skipped}")
        if failed:
            parts.append(f"失败 {failed}")
        prefix = "已导出，当前设置已保留" if success and not failed else "处理完成"
        self.status_label.setText(prefix + " · " + "，".join(parts))
        if failed == 0:
            self._clean_signature = self._state_signature()

    def _set_compare(self, enabled: bool) -> None:
        if enabled == self._comparing:
            return
        self._comparing = enabled
        if enabled:
            self.canvas.set_crop_visible(False)
            self.canvas.show_comparison_image(self._original_image)
            self.status_label.setText("正在对比打开时的原图")
        else:
            self.canvas.end_comparison()
            self.canvas.set_crop_visible(True)
            self.status_label.setText("")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self.isVisible() and event.type() in {
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
        }:
            key = getattr(event, "key", lambda: None)()
            auto_repeat = bool(getattr(event, "isAutoRepeat", lambda: False)())
            focus = QApplication.focusWidget()
            editing_text = isinstance(focus, QLineEdit)
            if key == Qt.Key.Key_Space and not auto_repeat and not editing_text:
                self._set_compare(event.type() == QEvent.Type.KeyPress)
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        super().closeEvent(event)

    def source_image(self) -> QImage:
        return self.canvas.image()
