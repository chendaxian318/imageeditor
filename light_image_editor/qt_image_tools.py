"""Lazy-loaded Qt tools for single-image conversion, resizing and cropping."""

from __future__ import annotations

from dataclasses import dataclass
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
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
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
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from image_ops import ExportOptions, estimate_save_size, extension_for_format

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
SIZE_PRESET_GROUPS: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    ("正方形", ((512, 512), (108, 108), (28, 28))),
    ("横屏", ((1440, 1440), (1920, 1080))),
    ("竖屏", ((1080, 1920), (1080, 2160), (1080, 2280), (1080, 2400))),
)


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


def _dialog_style(colors: dict[str, str]) -> str:
    return f"""
        QFrame#conversionPanel, QFrame#creationPanel, QFrame#imageEditWorkspace {{
            color: {colors["text"]}; background: {colors["surface"]};
            font: 13px 'Microsoft YaHei UI';
        }}
        QWidget#conversionContent {{ background: transparent; }}
        QWidget#imageToolSettings {{ background: transparent; }}
        QLabel {{ color: {colors["text"]}; background: transparent; }}
        QLabel#mutedLabel {{ color: {colors["muted"]}; }}
        QFrame#toolCard, QFrame#toolPanel, QFrame#outputBar, QFrame#ratioLockCard {{
            background: {colors["surface_high"]}; border: 1px solid {colors["border"]};
            border-radius: 12px;
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
        QPushButton#primaryButton {{
            color: {colors["accent_text"]}; background: {colors["accent"]};
            border-color: {colors["accent"]}; font-weight: 600;
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
        QCheckBox#ratioLockSwitch::indicator {{
            width: 38px; height: 20px; border-radius: 10px;
            background: {colors["border"]}; border: 1px solid {colors["border"]};
        }}
        QCheckBox#ratioLockSwitch::indicator:checked {{
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

    def __init__(
        self,
        source_path: Path,
        image: QImage,
        *,
        colors: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("imageEditWorkspace")
        self._colors = dict(colors)
        self.source_path = source_path.resolve()
        self._original_image = image.copy()
        self._initial_source_size = (image.width(), image.height())
        self._source_size = self._initial_source_size
        self._output_directory = source_path.parent.resolve()
        self._sources = [self.source_path]
        self._ratio_buttons: dict[str, QPushButton] = {}
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
        subtitle = QLabel(
            f"{source_path.name}  ·  {image.width()} × {image.height()}  ·  "
            "裁剪、调整尺寸或转换格式，原图不会被覆盖",
            self,
        )
        subtitle.setObjectName("mutedLabel")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
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
        canvas_layout.addWidget(self._build_output_bar(canvas_column))
        self.body_splitter.addWidget(canvas_column)

        panel = QFrame(self.body_splitter)
        panel.setObjectName("toolPanel")
        panel.setMinimumWidth(320)
        panel.setMaximumWidth(760)
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
        panel_layout.addWidget(self.output_summary)

        self.progress = QProgressBar(panel)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        panel_layout.addWidget(self.progress)
        self.status_label = QLabel("", panel)
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        panel_layout.addWidget(self.status_label)

        footer = QHBoxLayout()
        self.reset_button = QPushButton("重置", panel)
        self.reset_button.clicked.connect(self._reset_all)
        self.quick_export_button = QPushButton("快速导出", panel)
        self.quick_export_button.clicked.connect(
            lambda: self.quickExportRequested.emit(self.request())
        )
        self.save_button = QPushButton("导出并打开", panel)
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(lambda: self.saveRequested.emit(self.request()))
        footer.addWidget(self.reset_button)
        footer.addWidget(self.quick_export_button)
        footer.addWidget(self.save_button, 1)
        panel_layout.addLayout(footer)
        self.body_splitter.addWidget(panel)
        self.body_splitter.setStretchFactor(0, 1)
        self.body_splitter.setStretchFactor(1, 0)
        self.body_splitter.setSizes([900, 350])
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
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 4, 4)
        layout.setSpacing(12)

        source_section = QLabel("处理范围", page)
        source_section.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(source_section)
        source_card = QFrame(page)
        source_card.setObjectName("toolCard")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(11, 9, 11, 9)
        source_layout.setSpacing(7)
        self.source_summary = QLabel(source_card)
        self.source_summary.setWordWrap(True)
        source_layout.addWidget(self.source_summary)
        self.source_list = QListWidget(source_card)
        self.source_list.setFixedHeight(92)
        self.source_list.setVisible(False)
        source_layout.addWidget(self.source_list)
        source_actions = QHBoxLayout()
        self.add_sources_button = QPushButton("＋ 添加图片", source_card)
        self.add_sources_button.setObjectName("linkButton")
        self.remove_source_button = QPushButton("移除所选", source_card)
        self.remove_source_button.setObjectName("linkButton")
        self.remove_source_button.setVisible(False)
        source_actions.addWidget(self.add_sources_button)
        source_actions.addStretch(1)
        source_actions.addWidget(self.remove_source_button)
        source_layout.addLayout(source_actions)
        layout.addWidget(source_card)
        self._update_source_ui()

        ratio_section = QLabel("裁剪比例", page)
        ratio_section.setStyleSheet("font-weight: 600; font-size: 14px;")
        layout.addWidget(ratio_section)
        description = QLabel("选择比例后，可在图片上拖动边角或移动裁剪区域。", page)
        description.setObjectName("mutedLabel")
        description.setWordWrap(True)
        layout.addWidget(description)

        options: tuple[tuple[str, float | None], ...] = (
            ("自由", None),
            ("原图", self._source_size[0] / self._source_size[1]),
            ("1:1", 1.0),
            ("5:3", 5 / 3),
            ("16:9", 16 / 9),
            ("9:16", 9 / 16),
            ("4:3", 4 / 3),
            ("3:4", 3 / 4),
            ("3:2", 3 / 2),
            ("2:3", 2 / 3),
            ("18:9", 2.0),
            ("9:18", 0.5),
        )
        ratio_grid = QGridLayout()
        ratio_grid.setHorizontalSpacing(8)
        ratio_grid.setVerticalSpacing(8)
        for index, (label, ratio) in enumerate(options):
            button = QPushButton(label, page)
            button.setObjectName("ratioOption")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, name=label, value=ratio: self._apply_crop_ratio(name, value)
            )
            self._ratio_buttons[label] = button
            ratio_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(ratio_grid)

        self.crop_info = QLabel("", page)
        self.crop_info.setObjectName("mutedLabel")
        self.crop_info.setWordWrap(True)
        layout.addWidget(self.crop_info)

        crop_actions = QHBoxLayout()
        self.center_crop_button = QPushButton("居中裁剪", page)
        self.apply_crop_button = QPushButton("应用本次裁剪", page)
        self.center_crop_button.clicked.connect(self._center_crop)
        self.apply_crop_button.clicked.connect(self._apply_current_crop)
        crop_actions.addWidget(self.center_crop_button)
        crop_actions.addWidget(self.apply_crop_button, 1)
        layout.addLayout(crop_actions)
        compare_hint = QLabel("按住空格可临时对比打开时的原图。", page)
        compare_hint.setObjectName("mutedLabel")
        layout.addWidget(compare_hint)

        size_section = QLabel("输出尺寸", page)
        size_section.setStyleSheet("font-weight: 600; font-size: 14px; margin-top: 5px;")
        layout.addWidget(size_section)
        size_description = QLabel("保持原尺寸即可只转换格式。", page)
        size_description.setObjectName("mutedLabel")
        layout.addWidget(size_description)

        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.width_spin = QSpinBox(page)
        self.height_spin = QSpinBox(page)
        for spin in (self.width_spin, self.height_spin):
            spin.setRange(1, 100000)
        self.width_spin.setValue(self._source_size[0])
        self.height_spin.setValue(self._source_size[1])
        form.addRow("宽度", self.width_spin)
        form.addRow("高度", self.height_spin)
        layout.addLayout(form)

        self.ratio_lock_card = QFrame(page)
        self.ratio_lock_card.setObjectName("ratioLockCard")
        lock_layout = QHBoxLayout(self.ratio_lock_card)
        lock_layout.setContentsMargins(11, 8, 11, 8)
        self.lock_ratio = QCheckBox("锁定宽高比例", self.ratio_lock_card)
        self.lock_ratio.setObjectName("ratioLockSwitch")
        self.lock_ratio.setChecked(True)
        self.lock_ratio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lock_ratio_status = QLabel("● 已开启", self.ratio_lock_card)
        lock_layout.addWidget(self.lock_ratio)
        lock_layout.addStretch(1)
        lock_layout.addWidget(self.lock_ratio_status)
        layout.addWidget(self.ratio_lock_card)

        preset_label = QLabel("尺寸预设", page)
        preset_label.setStyleSheet("font-weight: 600; margin-top: 3px;")
        layout.addWidget(preset_label)
        preset_hint = QLabel("锁定宽高比时，选择预设会同步裁剪比例，避免画面拉伸。", page)
        preset_hint.setObjectName("mutedLabel")
        preset_hint.setWordWrap(True)
        layout.addWidget(preset_hint)
        for group_name, sizes in SIZE_PRESET_GROUPS:
            group_label = QLabel(group_name, page)
            group_label.setObjectName("mutedLabel")
            group_label.setStyleSheet("font-weight: 600;")
            layout.addWidget(group_label)
            preset_grid = QGridLayout()
            preset_grid.setHorizontalSpacing(7)
            preset_grid.setVerticalSpacing(7)
            for index, (width, height) in enumerate(sizes):
                button = QPushButton(f"{width} × {height}", page)
                button.setObjectName("ratioOption")
                button.clicked.connect(
                    lambda _checked=False, w=width, h=height: self._apply_size_preset(w, h)
                )
                self.size_preset_buttons[(width, height)] = button
                preset_grid.addWidget(button, index // 2, index % 2)
            layout.addLayout(preset_grid)

        format_section = QLabel("输出格式", page)
        format_section.setStyleSheet("font-weight: 600; font-size: 14px; margin-top: 5px;")
        layout.addWidget(format_section)
        self.edit_format_group = QButtonGroup(self)
        self.edit_format_group.setExclusive(True)
        for group_name, output_formats in FORMAT_GROUPS:
            group_label = QLabel(group_name, page)
            group_label.setObjectName("mutedLabel")
            group_label.setStyleSheet("font-weight: 600;")
            layout.addWidget(group_label)
            format_grid = QGridLayout()
            format_grid.setHorizontalSpacing(7)
            format_grid.setVerticalSpacing(7)
            for index, output_format in enumerate(output_formats):
                button = QPushButton(FORMAT_LABELS[output_format], page)
                button.setObjectName("formatCard")
                button.setCheckable(True)
                button.clicked.connect(
                    lambda _checked=False, value=output_format: self._select_output_format(value)
                )
                self.edit_format_group.addButton(button)
                self.format_buttons[output_format] = button
                format_grid.addWidget(button, index // 2, index % 2)
            layout.addLayout(format_grid)

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
        self.target_size_enabled = QCheckBox("限制文件大小", self.target_size_row)
        self.target_size_spin = QSpinBox(self.target_size_row)
        self.target_size_spin.setRange(10, 999_999)
        self.target_size_spin.setValue(500)
        self.target_size_spin.setSuffix(" KB")
        self.target_size_spin.setEnabled(False)
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

        naming_section = QLabel("文件命名", page)
        naming_section.setStyleSheet("font-weight: 600; font-size: 14px; margin-top: 5px;")
        layout.addWidget(naming_section)
        naming_form = QFormLayout()
        naming_form.setVerticalSpacing(9)
        self.filename_prefix = QLineEdit("", page)
        self.filename_suffix = QLineEdit("_edited", page)
        self.conflict_combo = QComboBox(page)
        self.conflict_combo.addItem("自动编号，不覆盖", "unique")
        self.conflict_combo.addItem("已存在则跳过", "skip")
        self.conflict_combo.addItem("覆盖已有结果", "overwrite")
        _style_combo_popup(self.conflict_combo, self._colors)
        naming_form.addRow("前缀", self.filename_prefix)
        naming_form.addRow("后缀", self.filename_suffix)
        naming_form.addRow("重名时", self.conflict_combo)
        layout.addLayout(naming_form)
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
        self._update_ratio_lock_state(True)
        return page

    def _apply_crop_ratio(self, label: str, ratio: float | None) -> None:
        for name, button in self._ratio_buttons.items():
            button.setChecked(name == label)
        self.canvas.set_aspect_ratio(ratio)

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
        self._source_size = (cropped.width(), cropped.height())
        self._working_revision += 1
        self._size_dirty = False
        self.canvas.set_image(cropped)
        self._apply_crop_ratio("自由", None)
        self._apply_size_percent(100)
        self.status_label.setText("已应用本次裁剪，可继续设置下一次裁剪")

    @property
    def sources(self) -> tuple[Path, ...]:
        return tuple(self._sources)

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
        self._sources = [path for path in self._sources if path not in selected]
        self._update_source_ui()
        self._update_summary()

    def _update_source_ui(self) -> None:
        count = len(self._sources)
        self.source_summary.setText(
            self.source_path.name if count == 1 else f"批量处理 · 共 {count} 张图片"
        )
        self.source_list.clear()
        for path in self._sources:
            item = QListWidgetItem(path.name, self.source_list)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            if path == self.source_path:
                item.setToolTip("当前预览图片，不能从队列移除")
        self.source_list.setVisible(count > 1)
        self.remove_source_button.setVisible(count > 1)

    def _update_ratio_lock_state(self, checked: bool) -> None:
        self.ratio_lock_card.setProperty("active", checked)
        self.lock_ratio_status.setText("● 已开启" if checked else "○ 已关闭")
        self.lock_ratio_status.setStyleSheet(
            "font-weight: 600;" if checked else "font-weight: 400;"
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

    def _reset_all(self) -> None:
        self._source_size = self._initial_source_size
        self._working_revision = 0
        self._size_dirty = False
        self.canvas.set_image(self._original_image)
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

    def _select_output_format(self, output_format: str) -> None:
        if output_format not in self.format_buttons:
            output_format = "PNG"
        self.format_buttons[output_format].setChecked(True)
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
        source = source or self.source_path
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
            known_ratios = {
                "1:1": 1.0,
                "16:9": 16 / 9,
                "9:16": 9 / 16,
                "9:18": 0.5,
            }
            matching_label = next(
                (
                    label
                    for label, known_ratio in known_ratios.items()
                    if abs(known_ratio - ratio) < 0.0001
                ),
                None,
            )
            if matching_label is None:
                for button in self._ratio_buttons.values():
                    button.setChecked(False)
                self.canvas.set_aspect_ratio(ratio)
            else:
                self._apply_crop_ratio(matching_label, ratio)
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
        self.output_summary.setText(
            f"输出：{self.width_spin.value()} × {self.height_spin.value()}  ·  "
            f"{self.output_format}{target_note}{batch_note}\n"
            f"文件：{self._output_filename()}（重名时自动编号）"
        )

    def request(self) -> ImageEditRequest:
        crop_box = self.canvas.crop_box()
        full_box = (0, 0, self._source_size[0], self._source_size[1])
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
            crop_aspect_ratio=(
                (crop_box[2] - crop_box[0]) / (crop_box[3] - crop_box[1])
                if (crop_box != full_box or self._working_revision > 0)
                else None
            ),
            resize_enabled=self._size_dirty,
            animation_mode=str(self.edit_animation_mode.currentData()),
            tga_rle=self.edit_tga_rle.isChecked(),
            dds_pixel_format=str(self.edit_dds_pixel_format.currentData()),
            generate_mipmaps=self.edit_mipmap_enabled.isChecked(),
            ktx2_mode=str(self.edit_ktx2_mode.currentData()),
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
        )

    def is_dirty(self) -> bool:
        return self._state_signature() != self._clean_signature

    def mark_exported(self, message: str = "已导出") -> None:
        self._clean_signature = self._state_signature()
        self.status_label.setText(message)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
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
        self.status_label.setText("处理完成 · " + "，".join(parts))
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
