"""Lazy-loaded Qt workspace for model-assisted text replacement in images."""

from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .image_model_api import (
    DEFAULT_IMAGE_EDIT_ENDPOINT,
    DEFAULT_IMAGE_EDIT_MODEL,
    ImageModelApiError,
    ImageTextEditApiRequest,
    check_image_api_connection,
)
from .qt_image_tools import _dialog_style, _style_combo_popup


def _display_model_name(model: str) -> str:
    labels = {
        DEFAULT_IMAGE_EDIT_MODEL: "GPT Image 2",
        "gpt-image-2-all": "GPT Image 2 All",
    }
    return labels.get(model, model)


@dataclass(frozen=True)
class LocalImageToolRequest:
    operation: str
    output_directory: Path
    resolution: str = "2K"


class ImageTextCreationPanel(QFrame):
    """Right-side creation panel for local tools and AI text replacement."""

    closeRequested = Signal()
    generateRequested = Signal(object)
    previewRequested = Signal(bool)
    saveRequested = Signal()
    settingsChanged = Signal(str, str)
    connectionChecked = Signal(bool, str)
    credentialSaveRequested = Signal(str)
    credentialDeleteRequested = Signal()
    localToolRequested = Signal(object)

    _COMPANY_PROVIDER = "company"
    _OPENAI_PROVIDER = "openai"
    _CUSTOM_PROVIDER = "custom"

    def __init__(
        self,
        source_path: Path,
        source_size: tuple[int, int],
        *,
        endpoint: str,
        model: str,
        api_key: str = "",
        user_api_key: str = "",
        credential_saved: bool = False,
        provider_display_name: str = "",
        company_default: bool = False,
        colors: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.source_path = source_path.resolve()
        self.source_size = source_size
        self._busy = False
        self._local_tool_buttons: list[QPushButton] = []
        self._result_bytes: bytes | None = None
        self._company_default = bool(company_default)
        self._company_endpoint = endpoint.strip()
        self._company_model = model.strip()
        self._company_key = api_key.strip() if company_default else ""
        self._company_display_name = provider_display_name.strip() or "公司默认 AI"
        self._connection_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="csimage-api-check",
        )
        self.setObjectName("creationPanel")
        self.setMinimumWidth(390)
        self.setStyleSheet(_dialog_style(colors))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("制图", self)
        title.setStyleSheet("font: 700 19px 'Microsoft YaHei UI';")
        subtitle = QLabel("本地增强、智能去背景与 AI 文字改写", self)
        subtitle.setObjectName("mutedLabel")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        close_button = QPushButton("×", self)
        close_button.setAccessibleName("关闭制图面板")
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

        source_card = QFrame(content)
        source_card.setObjectName("toolCard")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(13, 11, 13, 11)
        source_layout.setSpacing(4)
        source_name = QLabel(self.source_path.name, source_card)
        source_name.setStyleSheet("font-weight: 600;")
        source_detail = QLabel(
            f"{self.source_size[0]} × {self.source_size[1]} · 原图只读，结果需要另存为",
            source_card,
        )
        source_detail.setObjectName("mutedLabel")
        source_layout.addWidget(source_name)
        source_layout.addWidget(source_detail)
        content_layout.addWidget(source_card)

        local_section = QLabel("本地智能制图", content)
        local_section.setStyleSheet("font-weight: 600;")
        content_layout.addWidget(local_section)
        local_hint = QLabel(
            "图片不会上传；引擎仅在执行时加载，结果会以 PNG 另存到原图所在文件夹。",
            content,
        )
        local_hint.setObjectName("mutedLabel")
        local_hint.setWordWrap(True)
        content_layout.addWidget(local_hint)

        upscale_card = QFrame(content)
        upscale_card.setObjectName("localAiCard")
        upscale_layout = QVBoxLayout(upscale_card)
        upscale_layout.setContentsMargins(12, 10, 12, 10)
        upscale_layout.setSpacing(7)
        upscale_heading = QHBoxLayout()
        upscale_title = QLabel("高清优化", upscale_card)
        upscale_title.setStyleSheet("font-weight: 700;")
        upscale_badge = QLabel("本地", upscale_card)
        upscale_badge.setObjectName("localAiBadge")
        upscale_heading.addWidget(upscale_title)
        upscale_heading.addStretch(1)
        upscale_heading.addWidget(upscale_badge)
        upscale_layout.addLayout(upscale_heading)
        upscale_engine = QLabel(
            "Upscayl NCNN · 标准 4× 模型 · Vulkan",
            upscale_card,
        )
        upscale_engine.setObjectName("mutedLabel")
        upscale_engine.setWordWrap(True)
        upscale_layout.addWidget(upscale_engine)
        resolution_label = QLabel("目标清晰度（按图片长边）", upscale_card)
        resolution_label.setObjectName("mutedLabel")
        upscale_layout.addWidget(resolution_label)
        upscale_presets = QHBoxLayout()
        upscale_presets.setSpacing(7)
        self.upscale_resolution_group = QButtonGroup(self)
        self.upscale_resolution_group.setExclusive(True)
        self.upscale_resolution_buttons: dict[str, QPushButton] = {}
        for resolution in ("2K", "4K", "8K"):
            preset = QPushButton(resolution, upscale_card)
            preset.setObjectName("resolutionPreset")
            preset.setCheckable(True)
            preset.setProperty("resolution", resolution)
            preset.setToolTip(f"输出图片长边约为 {resolution}")
            self.upscale_resolution_group.addButton(preset)
            self.upscale_resolution_buttons[resolution] = preset
            upscale_presets.addWidget(preset, 1)
        self.upscale_resolution_buttons["2K"].setChecked(True)
        upscale_layout.addLayout(upscale_presets)
        self.upscale_button = QPushButton("生成高清副本", upscale_card)
        self.upscale_button.setObjectName("localAiAction")
        self.upscale_button.clicked.connect(self._request_upscale)
        upscale_layout.addWidget(self.upscale_button)
        content_layout.addWidget(upscale_card)

        remove_card = QFrame(content)
        remove_card.setObjectName("localAiCard")
        remove_layout = QVBoxLayout(remove_card)
        remove_layout.setContentsMargins(12, 10, 12, 10)
        remove_layout.setSpacing(7)
        remove_heading = QHBoxLayout()
        remove_title = QLabel("智能去背景", remove_card)
        remove_title.setStyleSheet("font-weight: 700;")
        remove_badge = QLabel("本地", remove_card)
        remove_badge.setObjectName("localAiBadge")
        remove_heading.addWidget(remove_title)
        remove_heading.addStretch(1)
        remove_heading.addWidget(remove_badge)
        remove_layout.addLayout(remove_heading)
        remove_engine = QLabel(
            "BiRefNet Lite · 保留原图 RGB，仅生成透明通道",
            remove_card,
        )
        remove_engine.setObjectName("mutedLabel")
        remove_engine.setWordWrap(True)
        remove_layout.addWidget(remove_engine)
        self.remove_background_button = QPushButton("生成透明背景副本", remove_card)
        self.remove_background_button.setObjectName("localAiAction")
        self.remove_background_button.clicked.connect(self._request_remove_background)
        remove_layout.addWidget(self.remove_background_button)
        content_layout.addWidget(remove_card)
        self._local_tool_buttons.extend((self.upscale_button, self.remove_background_button))

        conversation_title = QLabel("AI 文字改写", content)
        conversation_title.setStyleSheet("font-weight: 600;")
        content_layout.addWidget(conversation_title)
        self.conversation = QListWidget(content)
        self.conversation.setMinimumHeight(102)
        self.conversation.setMaximumHeight(150)
        self.conversation.setWordWrap(True)
        self.conversation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._append_message(
            "CSImage",
            "告诉我原文字和要替换成的内容；原文字不确定时可以留空。",
        )
        content_layout.addWidget(self.conversation)

        edit_card = QFrame(content)
        edit_card.setObjectName("toolCard")
        edit_layout = QVBoxLayout(edit_card)
        edit_layout.setContentsMargins(13, 11, 13, 11)
        edit_layout.setSpacing(8)
        edit_layout.addWidget(QLabel("原文字（可选）", edit_card))
        self.original_text = QLineEdit(edit_card)
        self.original_text.setPlaceholderText("例如：夏日特惠")
        edit_layout.addWidget(self.original_text)
        edit_layout.addWidget(QLabel("替换为", edit_card))
        self.replacement_text = QLineEdit(edit_card)
        self.replacement_text.setPlaceholderText("例如：秋日上新")
        edit_layout.addWidget(self.replacement_text)
        edit_layout.addWidget(QLabel("补充要求（可选）", edit_card))
        self.instruction = QTextEdit(edit_card)
        self.instruction.setPlaceholderText(
            "例如：只修改右上角标题，保持金色立体字、阴影和倾斜角度不变。"
        )
        self.instruction.setFixedHeight(72)
        edit_layout.addWidget(self.instruction)
        content_layout.addWidget(edit_card)

        self.api_summary_card = QFrame(content)
        self.api_summary_card.setObjectName("apiSummaryCard")
        api_summary_layout = QHBoxLayout(self.api_summary_card)
        api_summary_layout.setContentsMargins(12, 10, 10, 10)
        api_summary_layout.setSpacing(10)

        api_badge = QLabel("AI", self.api_summary_card)
        api_badge.setObjectName("apiBadge")
        api_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        api_badge.setFixedSize(34, 34)
        api_summary_layout.addWidget(api_badge)

        api_summary_text = QVBoxLayout()
        api_summary_text.setSpacing(3)
        api_summary_title = QHBoxLayout()
        api_summary_title.setSpacing(7)
        service_label = QLabel("AI 服务", self.api_summary_card)
        service_label.setStyleSheet("font-weight: 600;")
        self.api_status_badge = QLabel("尚未配置", self.api_summary_card)
        self.api_status_badge.setObjectName("apiStatusBadge")
        api_summary_title.addWidget(service_label)
        api_summary_title.addWidget(self.api_status_badge)
        api_summary_title.addStretch(1)
        self.api_service_detail = QLabel("选择服务后即可生成", self.api_summary_card)
        self.api_service_detail.setObjectName("mutedLabel")
        self.api_service_detail.setMinimumWidth(0)
        self.api_service_detail.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.api_service_detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        api_summary_text.addLayout(api_summary_title)
        api_summary_text.addWidget(self.api_service_detail)
        api_summary_layout.addLayout(api_summary_text, 1)

        self.api_toggle = QPushButton("设置  ▾", self.api_summary_card)
        self.api_toggle.setObjectName("apiSummaryToggle")
        self.api_toggle.setCheckable(True)
        self.api_toggle.setFixedWidth(68)
        self.api_toggle.setAccessibleName("展开 AI 服务设置")
        self.api_toggle.setToolTip("展开或收起低频使用的 AI 服务设置")
        api_summary_layout.addWidget(self.api_toggle)
        content_layout.addWidget(self.api_summary_card)
        self.api_card = QFrame(content)
        self.api_card.setObjectName("toolCard")
        api_layout = QVBoxLayout(self.api_card)
        api_layout.setContentsMargins(13, 11, 13, 11)
        api_layout.setSpacing(8)
        service_title = QLabel("1  选择 AI 服务", self.api_card)
        service_title.setStyleSheet("font-weight: 600;")
        api_layout.addWidget(service_title)
        self.provider_combo = QComboBox(self.api_card)
        if self._company_default:
            self.provider_combo.addItem(
                f"公司默认 · {self._company_display_name}", self._COMPANY_PROVIDER
            )
        self.provider_combo.addItem("OpenAI 官方（推荐）", self._OPENAI_PROVIDER)
        self.provider_combo.addItem("自定义兼容接口", self._CUSTOM_PROVIDER)
        _style_combo_popup(self.provider_combo, colors)
        api_layout.addWidget(self.provider_combo)
        self.model_summary = QLabel("", self.api_card)
        self.model_summary.setObjectName("mutedLabel")
        self.model_summary.setStyleSheet("font-weight: 600;")
        api_layout.addWidget(self.model_summary)

        self.key_title = QLabel("2  粘贴 API 密钥", self.api_card)
        self.key_title.setStyleSheet("font-weight: 600; margin-top: 4px;")
        api_layout.addWidget(self.key_title)
        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit(self.api_card)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        environment_key = os.environ.get("CSIMAGE_IMAGE_API_KEY", "") or os.environ.get(
            "OPENAI_API_KEY", ""
        )
        initial_key = (
            user_api_key.strip() or environment_key
            if self._company_default
            else api_key.strip() or environment_key
        )
        self.api_key_edit.setText(initial_key)
        self.api_key_edit.setPlaceholderText("粘贴以 sk- 开头的密钥")
        self.paste_key_button = QPushButton("粘贴", self.api_card)
        self.paste_key_button.setAccessibleName("从剪贴板粘贴 API 密钥")
        self.show_key = QCheckBox("显示", self.api_card)
        self.show_key.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(self.paste_key_button)
        key_row.addWidget(self.show_key)
        api_layout.addLayout(key_row)

        key_help_row = QHBoxLayout()
        self.key_status = QLabel("", self.api_card)
        self.key_status.setObjectName("mutedLabel")
        self.verify_key_button = QPushButton("验证密钥", self.api_card)
        self.key_help_button = QPushButton("获取 API 密钥 ↗", self.api_card)
        self.key_help_button.setObjectName("linkButton")
        key_help_row.addWidget(self.key_status, 1)
        key_help_row.addWidget(self.verify_key_button)
        key_help_row.addWidget(self.key_help_button)
        api_layout.addLayout(key_help_row)

        credential_row = QHBoxLayout()
        self.remember_key = QCheckBox("设为默认配置（Windows 加密保存）", self.api_card)
        self.remember_key.setChecked(credential_saved)
        self.clear_key_button = QPushButton("清除密钥", self.api_card)
        self.clear_key_button.setObjectName("linkButton")
        credential_row.addWidget(self.remember_key, 1)
        credential_row.addWidget(self.clear_key_button)
        api_layout.addLayout(credential_row)

        self.quality_title = QLabel("3  选择生成质量", self.api_card)
        self.quality_title.setStyleSheet("font-weight: 600; margin-top: 4px;")
        api_layout.addWidget(self.quality_title)
        self.quality_combo = QComboBox(self.api_card)
        self.quality_combo.addItem("标准（推荐，兼顾速度与质量）", "medium")
        self.quality_combo.addItem("快速（更省额度）", "low")
        self.quality_combo.addItem("高质量（更慢、费用更高）", "high")
        _style_combo_popup(self.quality_combo, colors)
        api_layout.addWidget(self.quality_combo)

        self.advanced_toggle = QPushButton("高级设置  ▾", self.api_card)
        self.advanced_toggle.setObjectName("linkButton")
        self.advanced_toggle.setCheckable(True)
        api_layout.addWidget(self.advanced_toggle)
        self.advanced_card = QFrame(self.api_card)
        self.advanced_card.setObjectName("toolPanel")
        advanced_layout = QVBoxLayout(self.advanced_card)
        advanced_layout.setContentsMargins(11, 9, 11, 9)
        advanced_layout.setSpacing(7)
        advanced_layout.addWidget(QLabel("图像编辑接口", self.advanced_card))
        self.endpoint_edit = QLineEdit(endpoint, self.advanced_card)
        self.endpoint_edit.setPlaceholderText("https://…/v1/images/edits")
        advanced_layout.addWidget(self.endpoint_edit)
        advanced_layout.addWidget(QLabel("模型名称", self.advanced_card))
        self.model_edit = QLineEdit(model, self.advanced_card)
        advanced_layout.addWidget(self.model_edit)
        advanced_hint = QLabel("只有使用第三方或本机兼容服务时才需要修改。", self.advanced_card)
        advanced_hint.setObjectName("mutedLabel")
        advanced_hint.setWordWrap(True)
        advanced_layout.addWidget(advanced_hint)
        self.advanced_card.setVisible(False)
        api_layout.addWidget(self.advanced_card)
        self.api_card.setVisible(False)
        content_layout.addWidget(self.api_card)

        self.result_card = QFrame(content)
        self.result_card.setObjectName("toolCard")
        result_layout = QVBoxLayout(self.result_card)
        result_layout.setContentsMargins(13, 11, 13, 11)
        result_layout.setSpacing(8)
        self.result_title = QLabel("生成结果", self.result_card)
        self.result_title.setStyleSheet("font-weight: 600;")
        self.result_preview = QLabel(self.result_card)
        self.result_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_preview.setMinimumHeight(150)
        self.result_preview.setStyleSheet(
            f"background: {colors['canvas']}; border: 1px solid {colors['border']}; "
            "border-radius: 9px;"
        )
        result_actions = QHBoxLayout()
        self.original_button = QPushButton("查看原图", self.result_card)
        self.result_button = QPushButton("查看结果", self.result_card)
        self.result_button.setObjectName("primaryButton")
        self.save_button = QPushButton("另存为…", self.result_card)
        result_actions.addWidget(self.original_button)
        result_actions.addWidget(self.result_button)
        result_actions.addWidget(self.save_button)
        result_layout.addWidget(self.result_title)
        result_layout.addWidget(self.result_preview)
        result_layout.addLayout(result_actions)
        self.result_card.setVisible(False)
        content_layout.addWidget(self.result_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        outer.addWidget(self.progress)
        footer = QHBoxLayout()
        self.status_label = QLabel("模型只应改文字，但生成式结果仍需人工核对。", self)
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        self.cancel_button = QPushButton("关闭", self)
        self.generate_button = QPushButton("发送并生成", self)
        self.generate_button.setObjectName("primaryButton")
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.generate_button)
        outer.addLayout(footer)

        self.api_toggle.toggled.connect(self._toggle_api_card)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.advanced_toggle.toggled.connect(self._toggle_advanced_card)
        self.api_key_edit.textChanged.connect(self._update_api_status)
        self.endpoint_edit.textChanged.connect(self._update_api_status)
        self.model_edit.textChanged.connect(self._update_api_status)
        self.paste_key_button.clicked.connect(self._paste_api_key)
        self.key_help_button.clicked.connect(self._open_key_help)
        self.verify_key_button.clicked.connect(self._check_connection)
        self.connectionChecked.connect(self._connection_check_finished)
        self.clear_key_button.clicked.connect(self._clear_api_key)
        self.remember_key.toggled.connect(self._remember_key_toggled)
        self.cancel_button.clicked.connect(self.closeRequested)
        self.generate_button.clicked.connect(self._submit)
        self.original_button.clicked.connect(lambda: self.previewRequested.emit(False))
        self.result_button.clicked.connect(lambda: self.previewRequested.emit(True))
        self.save_button.clicked.connect(self.saveRequested)

        self._custom_endpoint = ""
        self._custom_model = ""
        is_openai = (
            endpoint.strip().rstrip("/") == DEFAULT_IMAGE_EDIT_ENDPOINT.rstrip("/")
            and model.strip() == DEFAULT_IMAGE_EDIT_MODEL
        )
        if self._company_default:
            self.provider_combo.setCurrentIndex(
                self.provider_combo.findData(self._COMPANY_PROVIDER)
            )
        elif not is_openai:
            self._custom_endpoint = endpoint.strip()
            self._custom_model = model.strip()
            self.provider_combo.setCurrentIndex(self.provider_combo.findData(self._CUSTOM_PROVIDER))
        self._apply_provider()
        self._update_api_status()

    def _append_message(self, author: str, text: str) -> None:
        item = QListWidgetItem(f"{author}：{text}", self.conversation)
        item.setToolTip(text)
        self.conversation.scrollToBottom()

    def _toggle_api_card(self, visible: bool) -> None:
        self.api_card.setVisible(visible)
        self.api_toggle.setText("收起  ▴" if visible else "设置  ▾")
        self.api_toggle.setAccessibleName("收起 AI 服务设置" if visible else "展开 AI 服务设置")
        self._update_api_status()

    def _provider_changed(self) -> None:
        if self.provider_combo.currentData() == self._CUSTOM_PROVIDER:
            if self.endpoint_edit.text().strip() != DEFAULT_IMAGE_EDIT_ENDPOINT:
                self._custom_endpoint = self.endpoint_edit.text().strip()
            if self.model_edit.text().strip() != DEFAULT_IMAGE_EDIT_MODEL:
                self._custom_model = self.model_edit.text().strip()
        self._apply_provider()
        self._update_api_status()

    def _apply_provider(self) -> None:
        provider_data = self.provider_combo.currentData()
        company = provider_data == self._COMPANY_PROVIDER
        custom = provider_data == self._CUSTOM_PROVIDER
        if company:
            self.endpoint_edit.setText(self._company_endpoint)
            self.model_edit.setText(self._company_model)
            self.advanced_toggle.setChecked(False)
        elif custom:
            self.endpoint_edit.setText(self._custom_endpoint)
            self.model_edit.setText(self._custom_model)
            self.endpoint_edit.setPlaceholderText("https://你的服务地址/v1/images/edits")
            self.model_edit.setPlaceholderText("模型名称")
            self.advanced_toggle.setChecked(True)
        else:
            current_endpoint = self.endpoint_edit.text().strip()
            current_model = self.model_edit.text().strip()
            if current_endpoint and current_endpoint != DEFAULT_IMAGE_EDIT_ENDPOINT:
                self._custom_endpoint = current_endpoint
            if current_model and current_model != DEFAULT_IMAGE_EDIT_MODEL:
                self._custom_model = current_model
            self.endpoint_edit.setText(DEFAULT_IMAGE_EDIT_ENDPOINT)
            self.model_edit.setText(DEFAULT_IMAGE_EDIT_MODEL)
            self.advanced_toggle.setChecked(False)
        self.advanced_toggle.setVisible(custom)
        self.advanced_card.setVisible(custom and self.advanced_toggle.isChecked())
        self._apply_provider_controls(company)
        self._update_model_summary()

    def _apply_provider_controls(self, company: bool) -> None:
        self.key_title.setText("2  公司默认凭据" if company else "2  粘贴 API 密钥")
        for widget in (
            self.api_key_edit,
            self.paste_key_button,
            self.show_key,
            self.key_help_button,
            self.remember_key,
            self.clear_key_button,
        ):
            widget.setVisible(not company)
        self.verify_key_button.setText("验证默认服务" if company else "验证密钥")
        self.quality_title.setText(
            "3  生成质量（由模型自动决定）" if company else "3  选择生成质量"
        )
        self.quality_combo.setItemText(
            0,
            "模型自动（无需设置）" if company else "标准（推荐，兼顾速度与质量）",
        )
        self.quality_combo.setEnabled(not company)

    def _toggle_advanced_card(self, visible: bool) -> None:
        custom = self.provider_combo.currentData() == self._CUSTOM_PROVIDER
        self.advanced_card.setVisible(custom and visible)
        self.advanced_toggle.setText("高级设置  ▴" if visible else "高级设置  ▾")

    def _paste_api_key(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        value = clipboard.text().strip()
        if not value:
            self.status_label.setText("剪贴板里没有可粘贴的 API 密钥。")
            return
        self.api_key_edit.setText(value)
        self.api_key_edit.setFocus()
        self.status_label.setText("密钥已粘贴，仅在本次运行期间使用。")

    def _open_key_help(self) -> None:
        if self.provider_combo.currentData() == self._OPENAI_PROVIDER:
            QDesktopServices.openUrl(QUrl("https://platform.openai.com/api-keys"))
            return
        self.status_label.setText("请向自定义 AI 服务的提供方获取 API 密钥。")

    def _clear_api_key(self) -> None:
        self.api_key_edit.clear()
        self.remember_key.setChecked(False)
        self.credentialDeleteRequested.emit()
        self.status_label.setText("已清除本机保存的 API 密钥。")

    def _remember_key_toggled(self, remember: bool) -> None:
        if self.provider_combo.currentData() == self._COMPANY_PROVIDER:
            return
        key = self.api_key_edit.text().strip()
        if remember and key:
            self.credentialSaveRequested.emit(key)
        elif not remember:
            self.credentialDeleteRequested.emit()
        self._update_api_status()

    def _update_model_summary(self) -> None:
        provider_data = self.provider_combo.currentData()
        provider = (
            self._company_display_name
            if provider_data == self._COMPANY_PROVIDER
            else "自定义兼容服务"
            if provider_data == self._CUSTOM_PROVIDER
            else "OpenAI 官方"
        )
        model = self.model_edit.text().strip() or "尚未填写模型"
        display_model = _display_model_name(model)
        suffix = "（公司默认）" if provider_data == self._COMPANY_PROVIDER else ""
        self.model_summary.setText(f"当前模型：{provider} · {display_model}{suffix}")

    def _active_api_key(self) -> str:
        if self.provider_combo.currentData() == self._COMPANY_PROVIDER:
            return self._company_key
        return self.api_key_edit.text().strip()

    def _check_connection(self) -> None:
        if not self.verify_key_button.isEnabled():
            return
        endpoint = self.endpoint_edit.text().strip()
        key = self._active_api_key()
        try:
            if not endpoint:
                raise ValueError("请先填写服务地址。")
            if not key and not endpoint.startswith(
                ("http://127.0.0.1", "http://localhost", "http://[::1]")
            ):
                raise ValueError("请先粘贴 API 密钥。")
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        self.verify_key_button.setEnabled(False)
        self.verify_key_button.setText("验证中…")
        self.key_status.setText("正在检查连接，不会上传图片…")
        future = self._connection_executor.submit(check_image_api_connection, endpoint, key)
        future.add_done_callback(self._connection_done)

    def _connection_done(self, future: Future[str]) -> None:
        try:
            message = future.result()
        except (ImageModelApiError, ValueError) as error:
            self.connectionChecked.emit(False, str(error))
        except Exception:
            self.connectionChecked.emit(False, "验证失败，请检查网络和服务设置。")
        else:
            self.connectionChecked.emit(True, message)

    def _connection_check_finished(self, success: bool, message: str) -> None:
        self.verify_key_button.setEnabled(True)
        label = (
            "验证默认服务"
            if self.provider_combo.currentData() == self._COMPANY_PROVIDER
            else "验证密钥"
        )
        self.verify_key_button.setText(label)
        self.key_status.setText(f"✓ {message}" if success else message)
        self.status_label.setText(message)
        if (
            success
            and self.provider_combo.currentData() != self._COMPANY_PROVIDER
            and self.remember_key.isChecked()
        ):
            self.credentialSaveRequested.emit(self.api_key_edit.text().strip())

    def _update_api_status(self) -> None:
        provider_data = self.provider_combo.currentData()
        company = provider_data == self._COMPANY_PROVIDER
        custom = provider_data == self._CUSTOM_PROVIDER
        key = self._active_api_key()
        endpoint = self.endpoint_edit.text().strip()
        local = endpoint.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
        if company and key:
            status = "可直接使用"
            self.key_status.setText("✓ 公司默认 API 已配置")
        elif key:
            status = "已填写密钥"
            storage = "已在本机加密保存" if self.remember_key.isChecked() else "不会保存"
            self.key_status.setText(f"✓ 已填写 · {storage}")
        elif custom and local:
            status = "本机服务"
            self.key_status.setText("✓ 本机服务可不填密钥")
        else:
            status = "需要密钥"
            self.key_status.setText("请先粘贴密钥 · 不会保存")
        provider = (
            self._company_display_name if company else "自定义接口" if custom else "OpenAI 官方"
        )
        model = self.model_edit.text().strip()
        display_model = _display_model_name(model)
        if company:
            provider = provider.split("·", maxsplit=1)[0].strip()
        self.api_service_detail.setText(f"{provider}  ·  {display_model}")
        self.api_status_badge.setText(status)
        self.api_status_badge.setProperty("ready", bool(key or (custom and local)))
        self.api_status_badge.style().unpolish(self.api_status_badge)
        self.api_status_badge.style().polish(self.api_status_badge)
        self._update_model_summary()

    def _toggle_key_visibility(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.api_key_edit.setEchoMode(mode)

    def request(self) -> ImageTextEditApiRequest:
        return ImageTextEditApiRequest(
            source_path=self.source_path,
            endpoint=self.endpoint_edit.text(),
            model=self.model_edit.text(),
            api_key=self._active_api_key(),
            original_text=self.original_text.text(),
            replacement_text=self.replacement_text.text(),
            instruction=self.instruction.toPlainText(),
            quality=str(self.quality_combo.currentData()),
        )

    def _request_upscale(self) -> None:
        if self._busy:
            return
        selected = self.upscale_resolution_group.checkedButton()
        resolution = str(selected.property("resolution") if selected is not None else "2K")
        self.localToolRequested.emit(
            LocalImageToolRequest(
                operation="upscale",
                output_directory=self.source_path.parent,
                resolution=resolution,
            )
        )

    def _request_remove_background(self) -> None:
        if self._busy:
            return
        self.localToolRequested.emit(
            LocalImageToolRequest(
                operation="remove-background",
                output_directory=self.source_path.parent,
            )
        )

    def _submit(self) -> None:
        if self._busy:
            return
        try:
            request = self.request()
        except ValueError as error:
            self.status_label.setText(str(error))
            self.api_toggle.setChecked(True)
            if self.provider_combo.currentData() == self._CUSTOM_PROVIDER:
                self.advanced_toggle.setChecked(True)
            return
        description = (
            f"把“{request.original_text}”改成“{request.replacement_text}”"
            if request.original_text and request.replacement_text
            else request.replacement_text or request.instruction
        )
        self._append_message("你", description)
        self.settingsChanged.emit(request.endpoint, request.model)
        if (
            self.provider_combo.currentData() != self._COMPANY_PROVIDER
            and self.remember_key.isChecked()
        ):
            self.credentialSaveRequested.emit(request.api_key)
        self.set_busy(True)
        self.generateRequested.emit(request)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.generate_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.api_toggle.setEnabled(not busy)
        for button in self._local_tool_buttons:
            button.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setText("模型处理中" if busy else "关闭")
        if busy:
            self.status_label.setText("正在上传并处理图片，请不要关闭窗口…")

    def set_local_tool_status(self, message: str) -> None:
        self.status_label.setText(message)

    def set_error(self, message: str) -> None:
        self.set_busy(False)
        self.status_label.setText(message)
        self._append_message("CSImage", f"处理失败：{message}")

    def set_result(
        self,
        png_bytes: bytes,
        *,
        title: str = "生成结果",
        status: str = "已生成候选图；请放大检查文字边缘，确认后另存为。",
        conversation_message: str = "已生成一版文字改写结果，可以查看原图或结果。",
    ) -> None:
        self.set_busy(False)
        self._result_bytes = bytes(png_bytes)
        pixmap = QPixmap()
        if not pixmap.loadFromData(self._result_bytes):
            self.set_error("模型结果无法预览。")
            return
        self.result_preview.setPixmap(
            pixmap.scaled(
                360,
                220,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.result_title.setText(title)
        self.result_card.setVisible(True)
        self.status_label.setText(status)
        self._append_message("CSImage", conversation_message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.api_key_edit.clear()
        self._result_bytes = None
        self._connection_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)
