from __future__ import annotations

import copy
import json
import math
import os
import queue
import tempfile
import threading
import time
import tkinter as tk
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from functools import partial
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from urllib.parse import unquote, urlparse

from PIL import Image, ImageColor, ImageGrab, ImageTk

from image_ops import (
    SUPPORTED_INPUTS,
    AnimationFlatteningRequired,
    ConflictPolicy,
    EditState,
    ExportOptions,
    SourceChangedError,
    TransparencyFlatteningRequired,
    apply_edits,
    estimate_save_size,
    extension_for_format,
    iter_image_files,
    open_image,
    prepare_image,
    save_image,
)

from ..accessibility import FocusNavigator, set_accessible_name
from ..annotations import AnnotationObject, hit_radius_px, object_at, parse_crop_ratio
from ..batch import (
    BatchManifest,
    BatchPreset,
    BatchRecipe,
    BatchRunner,
    TaskStatus,
    export_batch_log,
)
from ..commands import EditCommand, EditTimeline
from ..diagnostics import CrashStore, DiagnosticReporter
from ..document import Document, DocumentState
from ..domain import (
    AlphaBrushStroke,
    AssetRevision,
    ContentEditRequest,
    ConversionControlState,
    ConversionDraft,
    ConversionDraftHistory,
    ConversionQueue,
    ConversionRunner,
    ConversionRunResult,
    ConversionSession,
    ConversionSessionCheckpointWriter,
    ConversionSessionError,
    ConversionSessionStore,
    ConversionTaskRecord,
    ConversionTaskStatus,
    ExportProfile,
    GenerationReference,
    GenerationRequest,
    JobRequestKind,
    JobStatus,
    ManagedJobSnapshot,
    NormalizedQuad,
    Provenance,
    RecipeFileError,
    RecipeStore,
    Scalar,
    SelectionMaskStroke,
    TextReplacementDraft,
    TextReplacementHistory,
    TransformRecipe,
    execute_transform_recipe,
    quad_bounds,
    serialize_alpha_strokes,
    validate_normalized_quad,
)
from ..dpi import DpiManager, dpi_scale, enable_process_dpi_awareness, monitor_work_area
from ..files import (
    copy_file,
    copy_image_to_clipboard,
    move_file,
    open_folder,
    open_recycle_bin,
    rename_file,
    reveal_file,
    send_to_recycle_bin,
    unique_export_path,
    validate_filename_affix,
)
from ..formats import AnimationSequence, codec_capabilities, decode_animation, export_animation
from ..i18n import Localizer
from ..identity import PRODUCT_NAME_ZH
from ..image_loader import ImageLoader, ImageLoadFailure
from ..metadata import image_properties
from ..navigation import NavigationSession
from ..presets import EXPORT_PRESETS, apply_export_preset, resolve_size_preset
from ..preview import PreviewFrame, PreviewRenderer, color_manage_for_display
from ..privacy import privacy_summary
from ..projects import (
    ProjectError,
    ProjectSourceMissing,
    ProjectSourceModified,
    load_project,
    save_project,
)
from ..providers import MOCK_MODEL_ID, MockProvider, ProviderCapability
from ..renderer import (
    SizeSpec,
    apply_command_to_plan,
    detect_straighten_angle,
    histogram,
    reduce_timeline,
    render_image,
)
from ..services.ai_authorization import AIAuthorizationGate, AIExecutionRequirements
from ..services.asset_repository import AssetRepository
from ..services.background_compositor import (
    BackgroundCompositionResult,
    BackgroundKind,
    BackgroundSpec,
    compose_background,
)
from ..services.font_matcher import (
    FONT_FILES,
    FontMatchError,
    FontMatchSuggestion,
    match_fonts,
)
from ..services.job_manager import JobManager, JobManagerPersistenceError
from ..services.provider_catalog import (
    GenerationProviderCatalog,
    GenerationProviderProfile,
)
from ..services.smart_selection import (
    SmartSelectionError,
    SmartSelectionSuggestion,
    decode_selection_mask,
    detect_smart_selection,
    refine_selection_mask,
)
from ..services.sqlite_asset_repository import SQLiteAssetRepository
from ..services.text_ocr import (
    TextOcrBackend,
    TextOcrResult,
    TextOcrUnavailableError,
    create_default_text_ocr_backend,
)
from ..services.text_region_detector import TextRegionSuggestion, detect_text_regions
from ..session import SessionStore
from ..settings import SETTINGS_SCHEMA_VERSION, SettingsStore
from ..shell import (
    AssetHandoff,
    ModeId,
    ModeRouter,
    ModeTransition,
    RevisionHandoff,
    create_default_feature_registry,
)
from ..theme import ThemeManager, ThemeTokens, resolve_theme
from ..thumbnails import ThumbnailLoader
from ..update_security import load_trusted_keys, trusted_keys_path
from ..updater import (
    UpdateCache,
    UpdateError,
    UpdateManifest,
    UpdateService,
    launch_installer,
)
from ..version import __version__
from ..windows_drop import WindowsFileDrop
from ..windows_effects import (
    BorderlessWindowController,
    RoundedWindowEffect,
    should_use_borderless_chrome,
)
from .antialias import render_photo
from .conversion_workspace import ConversionWorkspace
from .curve_editor import CurveEditor
from .feature_palette import FeatureCommandPalette, FeaturePaletteScope
from .generation_workspace import GenerationCandidateGrid, GenerationCandidateItem
from .material import (
    MaterialButton,
    MaterialCommandDock,
    MaterialFloatingBar,
    MaterialLookStrip,
    MaterialSlider,
    MaterialTabStrip,
)
from .task_tray import (
    TaskTray,
    UnifiedTaskItem,
    UnifiedTaskKind,
    UnifiedTaskStatus,
)
from .thumbnail_filmstrip import ThumbnailFilmstrip
from .thumbnail_sidebar import ThumbnailSidebar
from .window_chrome import MaterialTitleBar

APP_NAME = PRODUCT_NAME_ZH
ZOOM_LEVELS = ("适应", "50%", "100%", "150%", "200%")
MIN_ZOOM_PERCENT = 10
MAX_ZOOM_PERCENT = 800
ANIMATION_SPEED_FACTORS = {
    "0.5×": 0.5,
    "1×": 1.0,
    "1.5×": 1.5,
    "2×": 2.0,
}
TRANSPARENCY_BACKGROUND_LABELS: dict[str, str | None] = {
    "不自动处理": None,
    "白色背景": "#ffffff",
    "黑色背景": "#000000",
}
PNG_PALETTE_LABELS: dict[str, int | None] = {
    "保留原色": None,
    "256 色": 256,
    "128 色": 128,
    "64 色": 64,
    "32 色": 32,
    "16 色": 16,
}
TIFF_COMPRESSION_LABELS: dict[str, str] = {
    "不压缩": "raw",
    "LZW": "tiff_lzw",
    "Deflate": "tiff_adobe_deflate",
    "PackBits": "packbits",
}
SMART_TEXT_FONTS = tuple(FONT_FILES)
SMART_TEXT_ALIGNMENTS = {
    "左对齐": "left",
    "居中": "center",
    "右对齐": "right",
}
SMART_TEXT_EFFECTS = {
    "普通": "plain",
    "描边": "outline",
    "投影": "shadow",
    "线性渐变": "gradient",
    "发光": "glow",
}
SMART_TEXT_EFFECT_LABELS = {value: label for label, value in SMART_TEXT_EFFECTS.items()}
SMART_TEXT_EFFECT_DEFAULT_COLORS = {
    "plain": "#ffffff",
    "outline": "#ffffff",
    "shadow": "#303030",
    "gradient": "#4285f4",
    "glow": "#80d8ff",
}
GENERATION_REFERENCE_ROLES = {
    "内容": "content",
    "风格": "style",
    "构图": "composition",
}
GENERATION_REFERENCE_ROLE_LABELS = {
    value: label for label, value in GENERATION_REFERENCE_ROLES.items()
}
GENERATION_STYLE_PRESETS = {
    "无": "",
    "摄影质感": "photography",
    "插画": "illustration",
    "水彩": "watercolor",
    "水墨": "ink",
    "像素艺术": "pixel_art",
}


def _local_file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme.casefold() != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("图像服务结果不是受支持的本地文件 URI")
    value = unquote(parsed.path)
    if os.name == "nt" and len(value) >= 3 and value[0] == "/" and value[2] == ":":
        value = value[1:]
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("图像服务结果必须使用绝对本地路径")
    return path.resolve(strict=False)


MODE_LABELS = {
    ModeId.VIEWER: "看图",
    ModeId.CONVERT: "转图",
    ModeId.EDIT: "改图",
    ModeId.GENERATE: "生图",
}
MODE_HINTS = {
    ModeId.VIEWER: "浏览与预览 · 本地",
    ModeId.CONVERT: "裁切、尺寸与格式 · 本地",
    ModeId.EDIT: "调色、标注与智能改图",
    ModeId.GENERATE: "智能生成 · 按需启用",
}
ANNOTATION_TOOL_LABELS = {
    "select": "选择",
    "crop": "裁剪",
    "text": "文字",
    "arrow": "箭头",
    "line": "直线",
    "rectangle": "矩形",
    "ellipse": "椭圆",
    "freehand": "画笔",
    "mosaic": "马赛克",
    "blur": "模糊",
    "redact": "遮挡",
    "number": "序号",
    "perspective": "透视",
}


def _timeline_with_annotation_draft(
    timeline: EditTimeline,
    draft: AnnotationObject,
    original: AnnotationObject | None,
) -> EditTimeline:
    """Create a render-only timeline without mutating undo history."""

    if original is None:
        transient = EditCommand.create(
            "annotation_add",
            {"object": draft.to_json()},
            "预览标注",
        )
    else:
        patch = draft.to_json()
        patch.pop("id", None)
        patch.pop("kind", None)
        transient = EditCommand.create(
            "annotation_update",
            {"id": original.id, "patch": patch},
            "预览调整标注",
        )
    commands = [*timeline.active_commands, transient]
    return EditTimeline(commands=commands, cursor=len(commands))


ACTION_CATALOG = (
    ("open", "打开", "<Control-o>"),
    ("save", "保存图片", "<Control-s>"),
    ("save_project", "保存项目", "<Control-Shift-S>"),
    ("undo", "撤销", "<Control-z>"),
    ("redo", "重做", "<Control-y>"),
    ("rotate_right", "右转", "<Control-r>"),
    ("select", "选择标注", "<Key-v>"),
    ("crop", "裁剪", "<Key-c>"),
    ("text", "文字", "<Key-t>"),
    ("arrow", "箭头", "<Key-a>"),
    ("rectangle", "矩形", "<Key-r>"),
    ("mosaic", "马赛克", "<Key-m>"),
    ("blur", "模糊", "<Key-b>"),
)

VIEWER_SHORTCUT_DEFAULTS: dict[str, tuple[str, ...]] = {
    "previous": ("<Key-a>", "<Left>", "<KP_Left>"),
    "next": ("<Key-d>", "<Right>", "<KP_Right>"),
    "zoom_in": ("<Key-w>", "<Up>"),
    "zoom_out": ("<Key-s>", "<Down>"),
    "toggle_toolbar": ("<Key-h>",),
    "toggle_clean": ("<Tab>",),
    "toggle_topmost": ("<Key-p>",),
    "toggle_favorite": ("<Key-f>",),
}
VIEWER_SHORTCUT_LABELS: dict[str, str] = {
    "previous": "上一张图片",
    "next": "下一张图片",
    "zoom_in": "放大图片",
    "zoom_out": "缩小图片",
    "toggle_toolbar": "显示或隐藏工具栏",
    "toggle_clean": "纯净阅图模式",
    "toggle_topmost": "窗口置顶",
    "toggle_favorite": "收藏当前图片",
}
VIEWER_TOOLBAR_MODE_LABELS = {
    "fixed": "固定显示",
    "auto": "自动隐藏（移到顶部显示）",
}
DEFAULT_VIEWER_ZOOM_PRESETS = (
    "适应",
    "25%",
    "50%",
    "75%",
    "100%",
    "125%",
    "150%",
    "200%",
)


def _bounded_setting_int(
    value: object,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Read a persisted integer without letting a damaged config abort startup."""

    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_setting_float(
    value: object,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Read a finite persisted float and clamp it to the supported range."""

    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if not minimum <= parsed <= maximum:
        parsed = default
    return parsed


def _string_list_setting(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


class ImageEditorApp(tk.Tk):
    def __init__(
        self,
        settings_store: SettingsStore | None = None,
        image_loader: ImageLoader | None = None,
        preview_renderer: PreviewRenderer | None = None,
        session_store: SessionStore | None = None,
        asset_repository: AssetRepository | None = None,
        text_ocr_backend: TextOcrBackend | None = None,
    ) -> None:
        enable_process_dpi_awareness()
        super().__init__()
        self.settings_store = settings_store or SettingsStore()
        initial_settings = self.settings_store.load_migrated()
        self.image_loader = image_loader or ImageLoader(
            max_workers=_bounded_setting_int(initial_settings.get("decode_workers", 2), 2, 1, 8),
            cache_size=_bounded_setting_int(initial_settings.get("preview_cache", 3), 3, 1, 20),
        )
        self.preview_renderer = preview_renderer or PreviewRenderer()
        self.session_store = session_store or SessionStore()
        self.asset_repository = asset_repository or SQLiteAssetRepository(
            self.settings_store.path.with_name(".light_image_editor_assets.sqlite3"),
            self.settings_store.path.with_name(".light_image_editor_assets"),
        )
        self.text_ocr_backend = text_ocr_backend or create_default_text_ocr_backend()
        self._asset_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="asset-register",
        )
        self._analysis_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="image-analysis",
        )
        self._asset_registration_generation = 0
        self._asset_registration_future: Future[AssetRevision] | None = None
        self._asset_registration_path: Path | None = None
        self._current_asset_revision: AssetRevision | None = None
        self._asset_relocation_hint: tuple[Path, AssetRevision] | None = None
        self._asset_identity_open_hint: tuple[Path, AssetRevision] | None = None
        self._last_asset_registration_error: Exception | None = None
        self.diagnostic_reporter = DiagnosticReporter()
        self.crash_store = CrashStore()
        self.localizer = Localizer()
        self.theme_manager = ThemeManager(self)
        self.dpi_manager = DpiManager(self)
        self.navigation = NavigationSession([])
        self.mode_router = ModeRouter()
        self.feature_registry = create_default_feature_registry()
        mock_output_directory = self.settings_store.path.with_name(
            ".light_image_editor_mock_outputs"
        )
        self.mock_provider = MockProvider(
            auto_complete=False,
            auto_run_after_seconds=0.35,
            output_directory=mock_output_directory,
        )
        self.generation_provider_catalog = GenerationProviderCatalog(
            (
                GenerationProviderProfile(
                    manifest=self.mock_provider.manifest,
                    model_id=MOCK_MODEL_ID,
                    display_name="离线测试服务",
                    status_detail="已就绪 · 完全本地 · 0 元",
                ),
            )
        )
        self.ai_authorization_gate = AIAuthorizationGate()
        ai_job_store = self.settings_store.path.with_name(".light_image_editor_ai_jobs.json")
        self._ai_job_recovery_error: Exception | None = None
        try:
            self.ai_job_manager = JobManager(self.mock_provider, ai_job_store)
        except JobManagerPersistenceError as error:
            self._ai_job_recovery_error = error
            quarantine = ai_job_store.with_name(
                f"{ai_job_store.name}.corrupt-{uuid.uuid4().hex[:8]}.bak"
            )
            try:
                ai_job_store.replace(quarantine)
            except OSError:
                ai_job_store = ai_job_store.with_name(
                    f".light_image_editor_ai_jobs-{uuid.uuid4().hex[:8]}.json"
                )
            self.ai_job_manager = JobManager(self.mock_provider, ai_job_store)
        self._mode_unsubscribe: Callable[[], None] | None = None
        self._mock_generation_job_id: str | None = None
        self._mock_generation_after: str | None = None
        self._mock_generation_import_future: Future[tuple[AssetRevision, ...]] | None = None
        self._mock_generation_presented_job_id: str | None = None
        self._generated_candidates_by_revision: dict[str, GenerationCandidateItem] = {}
        self._selected_generated_revision_id: str | None = None
        self._generation_reference_future: Future[AssetRevision] | None = None
        self._generation_reference_revision: AssetRevision | None = None
        self._generation_references: list[GenerationReference] = []
        self._last_generation_request: GenerationRequest | None = None
        self._last_generation_profile: GenerationProviderProfile | None = None
        self._smart_edit_job_id: str | None = None
        self._smart_edit_after: str | None = None
        self._smart_edit_import_future: Future[AssetRevision] | None = None
        self._smart_edit_presented_job_id: str | None = None
        self._smart_edit_source_revision_id: str | None = None
        self._smart_edit_result_revision: AssetRevision | None = None
        self._smart_edit_operation: str | None = None
        self._smart_background_alpha_revision: AssetRevision | None = None
        self._smart_background_compose_generation = 0
        self._smart_background_compose_future: (
            Future[tuple[AssetRevision, BackgroundCompositionResult]] | None
        ) = None
        self._smart_background_compose_after: str | None = None
        self._smart_background_compose_source_revision_id: str | None = None
        self._smart_repair_region_box: tuple[int, int, int, int] | None = None
        self._smart_repair_selecting_region = False
        self._smart_selection_generation = 0
        self._smart_selection_future: Future[tuple[SmartSelectionSuggestion, ...]] | None = None
        self._smart_selection_suggestions: tuple[SmartSelectionSuggestion, ...] = ()
        self._smart_selection_index = -1
        self._smart_selection_mask_box: tuple[int, int, int, int] | None = None
        self._smart_selection_mask_preview: ImageTk.PhotoImage | None = None
        self._smart_mask_brush_active = False
        self._smart_mask_brush_mode = "add"
        self._smart_mask_current_points: list[tuple[float, float]] = []
        self._smart_mask_strokes: list[SelectionMaskStroke] = []
        self._smart_selection_refined_mask_rle: str | None = None
        self._smart_text_region_box: tuple[int, int, int, int] | None = None
        self._smart_text_selecting_region = False
        self._smart_text_adjusting_region = False
        self._smart_text_perspective_quad: NormalizedQuad | None = None
        self._smart_text_detection_generation = 0
        self._smart_text_detection_future: Future[tuple[TextRegionSuggestion, ...]] | None = None
        self._smart_text_suggestions: tuple[TextRegionSuggestion, ...] = ()
        self._smart_text_suggestion_index = -1
        self._smart_text_ocr_generation = 0
        self._smart_text_ocr_future: Future[TextOcrResult | None] | None = None
        self._smart_font_match_generation = 0
        self._smart_font_match_future: Future[tuple[FontMatchSuggestion, ...]] | None = None
        self._smart_alpha_brush_active = False
        self._smart_alpha_brush_mode = "preserve"
        self._smart_alpha_current_points: list[tuple[float, float]] = []
        self._smart_alpha_strokes: list[AlphaBrushStroke] = []
        self._conversion_handoff_override: tuple[Path, AssetHandoff, RevisionHandoff] | None = None
        self.window_title_var = tk.StringVar(value=APP_NAME)
        self._set_window_title(APP_NAME)
        self.geometry("1240x780")
        self.minsize(1040, 680)

        self.image_path: Path | None = None
        self.original: Image.Image | None = None
        self.document: Document | None = None
        self.base_preview_image: Image.Image | None = None
        self._base_preview_owned = False
        self.preview: ImageTk.PhotoImage | None = None
        self.preview_scale = 1.0
        self.preview_offset = (0, 0)
        self.pan_offset = (0, 0)
        self.pan_start: tuple[int, int] | None = None
        self.pan_start_offset = (0, 0)
        self.zoom_mode = tk.StringVar(value="适应")
        self.compare_original = tk.BooleanVar(value=False)
        self.split_compare_var = tk.BooleanVar(value=False)
        self.file_compare_var = tk.BooleanVar(value=False)
        self._comparison_image: Image.Image | None = None
        self._comparison_path: Path | None = None
        self._comparison_load_generation = 0
        self._comparison_ref_lock = threading.Lock()
        self._comparison_render_refs: dict[int, int] = {}
        self._retired_comparison_images: dict[int, Image.Image] = {}
        self.reveal_after_save_var = tk.BooleanVar(value=False)
        self.crop_box: tuple[int, int, int, int] | None = None
        self.drag_start: tuple[int, int] | None = None
        self.crop_drag_start_box: tuple[int, int, int, int] | None = None
        self.crop_action = "new"
        self.batch_cancel_requested = False
        self.batch_runner: BatchRunner | None = None
        self.batch_manifest: BatchManifest | None = None
        self._batch_ui_queue: queue.Queue[tuple[bool, BatchManifest]] = queue.Queue()
        self.batch_manifest_path = self.settings_store.path.with_name(
            ".light_image_editor_batch.json"
        )
        self.recipe_store = RecipeStore()
        initial_conversion_recipe = TransformRecipe(
            name="当前转换设置",
            export_profile=ExportProfile(backup_on_overwrite=True),
        )
        self.conversion_draft_history = ConversionDraftHistory(
            ConversionDraft.from_recipe(initial_conversion_recipe)
        )
        self._conversion_recipe_override: TransformRecipe | None = None
        self._conversion_runner: ConversionRunner | None = None
        self._conversion_thread: threading.Thread | None = None
        self._conversion_poll_job: str | None = None
        self._conversion_ui_queue: queue.Queue[tuple[int, str, object]] = queue.Queue()
        self._conversion_recovery_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._conversion_recovery_thread: threading.Thread | None = None
        self._conversion_recovery_poll_job: str | None = None
        self.conversion_session_store = ConversionSessionStore()
        self.conversion_session_path = self.settings_store.path.with_name(
            ".light_image_editor_conversion.json"
        )
        self.conversion_checkpoint_writer = ConversionSessionCheckpointWriter(
            self.conversion_session_path,
            store=self.conversion_session_store,
        )
        self._conversion_session: ConversionSession | None = None
        self._conversion_run_base_session: ConversionSession | None = None
        self._conversion_run_queue: ConversionQueue | None = None
        self._conversion_generation = 0
        self._active_conversion_generation: int | None = None
        self._conversion_tray_task_id: str | None = None
        self._restoring_conversion_session = False
        self._last_conversion_checkpoint = 0.0
        self._animation_sequence: AnimationSequence | None = None
        self._animation_index = 0
        self._animation_displayed_index = 0
        self._animation_playing = False
        self._animation_job: str | None = None
        self._animation_generation = 0
        self._animation_pending_future: Future[AnimationSequence] | None = None
        self._animation_resume_on_viewer = False
        self._animation_resume_after_file_compare = False
        self.animation_position_var = tk.StringVar(value="动画未载入")
        self.animation_frame_var = tk.IntVar(value=1)
        self.animation_speed_var = tk.StringVar(value="1×")
        self._ratio_lock = False
        self._preview_job: str | None = None
        self._window_resize_idle_job: str | None = None
        self._viewer_resize_preview_pending = False
        self._last_canvas_configure_size = (0, 0)
        self._preview_poll_jobs: set[str] = set()
        self._annotation_preview_job: str | None = None
        self._preview_generation = 0
        self.preview_ready_generation = 0
        self._load_generation = 0
        self._failed_navigation_paths: set[str] = set()
        self._saved_geometry = ""
        self._is_fullscreen = False
        self._is_immersive = False
        self._zoom_preset_visible = False
        self._slideshow_job: str | None = None
        self._autosave_job: str | None = None
        self._source_monitor_job: str | None = None
        self._syncing_document = False
        self._active_slider_merge_key: str | None = None
        self._last_source_status = "unchanged"
        self._last_autosave_signature: tuple[object, ...] | None = None

        self.format_var = tk.StringVar(value="JPEG")
        self.quality_var = tk.IntVar(value=85)
        self.target_size_var = tk.StringVar()
        self.webp_lossless_var = tk.BooleanVar(value=False)
        self.png_palette_var = tk.StringVar(value="保留原色")
        self.tiff_compression_var = tk.StringVar(value="不压缩")
        self.filename_prefix_var = tk.StringVar()
        self.filename_suffix_var = tk.StringVar()
        self.watermark_text_var = tk.StringVar()
        self.watermark_position_var = tk.StringVar(value="右下")
        self.watermark_opacity_var = tk.IntVar(value=35)
        self.watermark_size_var = tk.IntVar(value=5)
        self.keep_ratio_var = tk.BooleanVar(value=True)
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        self.max_side_var = tk.StringVar()
        self.crop_ratio_var = tk.StringVar(value="自由")
        self.crop_action_hint_var = tk.StringVar(value="拖动图片创建裁剪框")
        self.status_var = tk.StringVar(value="打开一张图片开始编辑")
        self.info_var = tk.StringVar(value="未载入图片")
        self.viewer_footer_var = tk.StringVar(value="未载入图片")
        self.crop_var = tk.StringVar(value="未设置裁剪")
        self.size_var = tk.StringVar(value="预计输出大小：-")
        self.mode_hint_var = tk.StringVar(value=MODE_HINTS[ModeId.VIEWER])
        self.generate_prompt_var = tk.StringVar()
        self.generate_provider_var = tk.StringVar(
            value=self.generation_provider_catalog.default().display_name
        )
        self.generate_negative_prompt_var = tk.StringVar()
        self.generate_size_var = tk.StringVar(value="1024 × 1024")
        self.generate_count_var = tk.IntVar(value=1)
        self.generate_style_preset_var = tk.StringVar(value="无")
        self.generate_seed_enabled_var = tk.BooleanVar(value=False)
        self.generate_seed_var = tk.StringVar(value="0")
        self.generate_reference_role_var = tk.StringVar(value="内容")
        self.generate_reference_strength_var = tk.IntVar(value=50)
        self.generate_reference_status_var = tk.StringVar(value="未选择参考图")
        self.generate_provider_capability_var = tk.StringVar(
            value="离线服务能力：文生图 / 负面提示词 / 参考图 · 本地 0 元"
        )
        self.generate_status_var = tk.StringVar(value="离线任务尚未开始")
        self.generate_candidates_var = tk.StringVar(value="尚无候选结果")
        self.smart_edit_status_var = tk.StringVar(value="请选择本地高清或智能去背景")
        self.smart_edit_result_var = tk.StringVar(value="结果将作为当前图片的新修订保存")
        self.smart_background_tolerance_var = tk.IntVar(value=24)
        self.smart_background_feather_var = tk.IntVar(value=32)
        self.smart_background_replace_mode_var = tk.StringVar(value="纯色背景")
        self.smart_background_replace_color_var = tk.StringVar(value="#ffffff")
        self.smart_background_replace_image_var = tk.StringVar(value="尚未选择背景图片")
        self.smart_background_replace_image_path: Path | None = None
        self.smart_alpha_brush_size_var = tk.IntVar(value=18)
        self.smart_alpha_refinement_var = tk.StringVar(value="边缘画笔：等待去背景结果")
        self.smart_repair_feather_var = tk.IntVar(value=8)
        self.smart_repair_region_var = tk.StringVar(value="区域：请在画布框选需要擦除的物体")
        self.smart_mask_brush_size_var = tk.IntVar(value=18)
        self.smart_mask_refinement_var = tk.StringVar(value="蒙版精修：请先智能识别主体")
        self.smart_text_value_var = tk.StringVar(value="新文字")
        self.smart_text_font_var = tk.StringVar(value="微软雅黑")
        self.smart_text_font_size_var = tk.IntVar(value=32)
        self.smart_text_color_var = tk.StringVar(value="#202124")
        self.smart_text_align_var = tk.StringVar(value="居中")
        self.smart_text_rotation_var = tk.DoubleVar(value=0.0)
        self.smart_text_curve_var = tk.DoubleVar(value=0.0)
        self.smart_text_effect_var = tk.StringVar(value="普通")
        self.smart_text_effect_color_var = tk.StringVar(value="#ffffff")
        self.smart_text_effect_size_var = tk.IntVar(value=4)
        self.smart_text_region_var = tk.StringVar(value="区域：请在画布框选原文字")
        self.smart_text_ocr_var = tk.StringVar(
            value=(
                f"框内文字识别：{self.text_ocr_backend.display_name}可用"
                if self.text_ocr_backend.available
                else "框内文字识别：未安装，可继续手工输入"
            )
        )
        self.smart_text_font_match_var = tk.StringVar(
            value="字体推荐：框选文字并填写或识别原文字后可使用"
        )
        self.smart_text_history = TextReplacementHistory()
        self.edit_panel_visible_var = tk.BooleanVar(value=True)
        self.thumbnail_sidebar_visible_var = tk.BooleanVar(value=True)
        self.viewer_toolbar_visible_var = tk.BooleanVar(value=True)
        self.viewer_toolbar_mode_var = tk.StringVar(value="fixed")
        self.viewer_topmost_var = tk.BooleanVar(value=False)
        self.slideshow_interval_var = tk.DoubleVar(value=3.0)
        self.slideshow_loop_var = tk.BooleanVar(value=True)
        self.preserve_metadata_var = tk.BooleanVar(value=True)
        self.backup_on_overwrite_var = tk.BooleanVar(value=True)
        self.export_preset_var = tk.StringVar(value="自定义")
        self.batch_concurrency_var = tk.IntVar(value=2)
        self.batch_memory_var = tk.IntVar(value=512)
        self.batch_rename_template_var = tk.StringVar(value="{stem}_{index:04d}")
        self.batch_conflict_var = tk.StringVar(value="unique")
        self.batch_strip_metadata_var = tk.BooleanVar(value=False)
        self.batch_preserve_animation_var = tk.BooleanVar(value=True)
        self.conversion_output_dir_var = tk.StringVar()
        self.conversion_background_var = tk.StringVar(value="不自动处理")
        self.conversion_recipe_status_var = tk.StringVar(value="使用当前面板设置")
        self.conversion_job_status_var = tk.StringVar(value="尚未开始转换")
        self.theme_mode_var = tk.StringVar(value="system")
        self.decode_workers_var = tk.IntVar(value=2)
        self.preview_cache_var = tk.IntVar(value=3)
        self.update_channel_var = tk.StringVar(value="stable")
        self.crash_reporting_consent_var = tk.BooleanVar(value=False)
        self.onboarding_completed = False
        self._theme_poll_job: str | None = None
        self._config_save_job: str | None = None
        self._startup_jobs: list[str] = []
        self._last_theme_name = ""
        self._last_failed_path: Path | None = None
        self._onboarding_window: tk.Toplevel | None = None
        self._feature_palette_window: FeatureCommandPalette | None = None
        self._viewer_settings_window: tk.Toplevel | None = None
        self._viewer_clean_mode = False
        self._viewer_toolbar_auto_visible = False
        self._viewer_toolbar_auto_hide_job: str | None = None
        self._viewer_toolbar_fade_job: str | None = None
        self._viewer_toolbar_fade_target: float | None = None
        self._viewer_toolbar_opacity = 1.0
        self._viewer_markup_active = False
        self._viewer_markup_tool = "rectangle"
        self._viewer_markup_timeline_snapshot: EditTimeline | None = None
        self._viewer_gallery_scope = "folder"
        self._services_shutdown = False

        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self.brightness_var = tk.IntVar(value=0)
        self.contrast_var = tk.IntVar(value=0)
        self.saturation_var = tk.IntVar(value=0)
        self.sharpness_var = tk.IntVar(value=0)
        self.temperature_var = tk.IntVar(value=0)
        self.tint_var = tk.IntVar(value=0)
        self.shadows_var = tk.IntVar(value=0)
        self.highlights_var = tk.IntVar(value=0)
        self.denoise_var = tk.IntVar(value=0)
        self.level_black_var = tk.IntVar(value=0)
        self.level_white_var = tk.IntVar(value=255)
        self.level_gamma_var = tk.DoubleVar(value=1.0)
        self.fine_rotation_var = tk.DoubleVar(value=0.0)
        self.fine_rotation_auto_crop_var = tk.BooleanVar(value=True)
        self.fine_rotation_background_var = tk.StringVar(value="#ffffff")
        self.curve_channel_var = tk.StringVar(value="rgb")
        self._curve_points_by_channel: dict[str, tuple[tuple[float, float], ...]] = {
            channel: ((0.0, 0.0), (1.0, 1.0)) for channel in ("rgb", "red", "green", "blue")
        }
        self.grayscale_var = tk.BooleanVar(value=False)
        self.auto_contrast_var = tk.BooleanVar(value=False)
        self.annotation_tool_var = tk.StringVar(value="crop")
        self.current_tool_label_var = tk.StringVar(value="当前：裁剪")
        self.annotation_color_var = tk.StringVar(value="#ff3b30")
        self.annotation_fill_var = tk.StringVar(value="")
        self.annotation_stroke_var = tk.IntVar(value=4)
        self.annotation_opacity_var = tk.IntVar(value=100)
        self.annotation_font_size_var = tk.IntVar(value=5)
        self.annotation_text_var = tk.StringVar()
        self.annotation_effect_size_var = tk.IntVar(value=12)
        self.selected_annotation_var = tk.StringVar(value="未选择标注")
        self.selected_annotation_id: str | None = None
        self._annotation_start: tuple[float, float] | None = None
        self._annotation_drag_object: AnnotationObject | None = None
        self._annotation_original_object: AnnotationObject | None = None
        self._annotation_drag_action = "new"
        self._annotation_freehand_points: list[tuple[float, float]] = []
        self.perspective_points: list[tuple[float, float]] = [
            (0.05, 0.05),
            (0.95, 0.05),
            (0.95, 0.95),
            (0.05, 0.95),
        ]
        self._perspective_active_index: int | None = None
        self.canvas_left_var = tk.IntVar(value=0)
        self.canvas_top_var = tk.IntVar(value=0)
        self.canvas_right_var = tk.IntVar(value=0)
        self.canvas_bottom_var = tk.IntVar(value=0)
        self.canvas_background_var = tk.StringVar(value="#ffffff")
        self.recent_files: list[str] = []
        self.recent_directories: list[str] = []
        self.recent_projects: list[str] = []
        self.recent_export_directories: list[str] = []
        self.generation_prompt_history: list[str] = []
        self.generation_candidate_revision_ids: list[str] = []
        self.generation_favorite_revision_ids: list[str] = []
        self.generate_favorites_only_var = tk.BooleanVar(value=False)
        self.generation_reference_revision_id = ""
        self.generation_reference_settings: list[tuple[str, str, float]] = []
        self.task_tray_settings: list[dict[str, object]] = []
        self.favorite_actions: list[str] = ["open", "save", "undo", "redo", "crop"]
        self.custom_shortcuts: dict[str, str] = {
            key: shortcut for key, _label, shortcut in ACTION_CATALOG
        }
        self._custom_bound_sequences: set[str] = set()
        self.viewer_shortcuts: dict[str, list[str]] = {
            action: list(sequences) for action, sequences in VIEWER_SHORTCUT_DEFAULTS.items()
        }
        self._viewer_bound_sequences: set[str] = set()
        self.viewer_zoom_step_percent = 10
        self.viewer_zoom_presets = list(DEFAULT_VIEWER_ZOOM_PRESETS)
        self.viewer_favorite_paths: list[str] = []
        self.restore_workspace = True
        self.restore_workspace_var = tk.BooleanVar(value=True)
        self.last_project = ""
        self.clean_signature: tuple[object, ...] | None = None

        self._load_config()
        self._set_viewer_topmost(self.viewer_topmost_var.get())
        # The approved viewer shell uses one fixed, high-contrast dark palette.
        self.theme_mode_var.set("dark")
        self.thumbnail_sidebar_visible_var.set(False)
        self._bind_document_variables()
        self.dpi_manager.refresh()
        scale = dpi_scale(self.dpi_manager.current_dpi)
        default_size = (round(1240 * scale), round(780 * scale))
        self.geometry(self.dpi_manager.fit_saved_geometry(self._saved_geometry, default_size))
        self._update_monitor_constraints()
        self.window_controller: BorderlessWindowController | None = None
        if should_use_borderless_chrome():
            self.window_controller = BorderlessWindowController(self)
        self._build_ui()
        self._viewer_info_trace = self.info_var.trace_add(
            "write", lambda *_args: self._update_viewer_footer_info()
        )
        self._update_viewer_footer_info()
        self.after_idle(self._restore_task_tray_history)
        self.after_idle(self._restore_ai_job_summary)
        self.after_idle(self._restore_generation_candidate_history)
        self.after_idle(self._restore_generation_reference)
        self.after_idle(self._update_generation_provider_capability_label)
        self.rounded_window_effect = RoundedWindowEffect(
            self,
            radius=8,
            allow_region_fallback=self.window_controller is not None,
            maximized_provider=(
                (lambda: self.window_controller.maximized)
                if self.window_controller is not None
                else None
            ),
            fullscreen_provider=(
                (lambda: self.window_controller.fullscreen)
                if self.window_controller is not None
                else None
            ),
            resizing_provider=(
                (lambda: self.window_controller.resizing)
                if self.window_controller is not None
                else None
            ),
            moving_provider=(
                (lambda: self.window_controller.moving)
                if self.window_controller is not None
                else None
            ),
        )
        self._setup_experience()
        self._bind_shortcuts()
        self._drop_target = WindowsFileDrop(self, self.open_paths)
        self._startup_jobs.append(self.after(0, self._drop_target.enable))
        self.bind("<Configure>", self._on_configure, add="+")
        if self.window_controller is not None:
            self.bind(
                "<<WindowMoveStarted>>",
                self._on_window_interaction_started,
                add="+",
            )
            self.bind(
                "<<WindowResizeStarted>>",
                self._on_window_interaction_started,
                add="+",
            )
            self.bind(
                "<<WindowMoveFinished>>",
                self._on_window_move_finished,
                add="+",
            )
            self.bind(
                "<<WindowResizeFinished>>",
                self._on_window_resize_finished,
                add="+",
            )
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._autosave_job = self.after(2000, self._autosave_session)
        self._source_monitor_job = self.after(2000, self._monitor_source_file)
        self._startup_jobs.append(self.after(250, self._offer_session_recovery))
        self._startup_jobs.append(self.after(650, self._offer_batch_recovery))
        self._startup_jobs.append(self.after(800, self._offer_conversion_recovery))
        self._startup_jobs.append(self.after(900, self._offer_onboarding))
        self._startup_jobs.append(self.after(1200, self._offer_crash_report))
        self._theme_poll_job = self.after(2000, self._poll_system_theme)

    def _setup_experience(self) -> None:
        tokens = self.theme_manager.apply(self.theme_mode_var.get())
        self._apply_thumbnail_palette(tokens)
        self._last_theme_name = tokens.name
        for variable in (
            self.status_var,
            self.info_var,
            self.crop_var,
            self.size_var,
            self.current_tool_label_var,
            self.selected_annotation_var,
        ):
            self.localizer.register_variable(variable)
        self.localizer.bind_mapped_windows(self)
        self.localizer.set_locale("zh-CN", self)
        self.thumbnail_sidebar.set_title("资源库")
        self._set_window_title(self.localizer.translate_text(APP_NAME))
        self._refresh_accessible_names()
        toolbar_target = self.canvas_select_button
        edit_target = self.inspector_tabs
        self.focus_navigator = FocusNavigator(
            self,
            (
                toolbar_target,
                self.thumbnail_sidebar.canvas,
                self.conversion_workspace.tree,
                edit_target,
                self.canvas,
                self.panel_canvas,
                self.task_tray.trigger_button,
                self.dock_open_button,
                self.status_label,
            ),
        )
        self._name_interactive_widgets(self)
        self.bind_all("<Map>", self._on_accessible_widget_map, add="+")

    def _set_window_title(self, title: str) -> None:
        self.title(title)
        self.window_title_var.set(title)

    def report_callback_exception(
        self,
        exception_type: type[BaseException],
        exception: BaseException,
        traceback_value: object,
    ) -> None:
        self.crash_store.record(
            exception_type,
            exception,
            traceback_value if hasattr(traceback_value, "tb_frame") else None,
            "tk-main",
        )
        messagebox.showerror(
            APP_NAME,
            "程序遇到错误，已在本机保存脱敏诊断记录。可从“信息”菜单导出问题报告。",
            parent=self,
        )

    def _refresh_accessible_names(self) -> None:
        set_accessible_name(self.canvas, self.localizer.gettext("accessibility_canvas"))
        set_accessible_name(self.canvas_tools, self.localizer.gettext("accessibility_toolbar"))
        set_accessible_name(self.panel_shell, self.localizer.gettext("accessibility_edit_tools"))
        set_accessible_name(self.panel_canvas, self.localizer.gettext("accessibility_panel"))
        set_accessible_name(self.status_label, self.localizer.gettext("accessibility_status"))
        set_accessible_name(
            self.thumbnail_sidebar.canvas,
            self.localizer.gettext("accessibility_thumbnails"),
        )
        set_accessible_name(self.conversion_workspace.tree, "转换任务队列")
        set_accessible_name(self.task_tray.trigger_button, "打开统一任务托盘")
        set_accessible_name(self.task_tray.tree, "统一任务列表")

    def _apply_thumbnail_palette(self, tokens: ThemeTokens) -> None:
        self.thumbnail_sidebar.apply_palette(
            surface=tokens.surface,
            surface_alt=tokens.surface_alt,
            foreground=tokens.foreground,
            muted=tokens.muted,
            accent=tokens.accent,
            border=tokens.border,
        )
        if hasattr(self, "thumbnail_filmstrip"):
            self.thumbnail_filmstrip.apply_palette(
                surface=tokens.canvas,
                surface_alt=tokens.canvas,
                accent=tokens.accent,
                border=tokens.canvas,
            )
        if hasattr(self, "viewer_gallery_scope_bar"):
            self.viewer_gallery_scope_bar.apply_palette(
                background=tokens.canvas,
                container=tokens.surface,
                border=tokens.border,
                shadow=tokens.canvas,
            )
        if self.window_controller is not None:
            self.window_controller.set_resize_handle_backgrounds(
                titlebar=tokens.surface,
                body=tokens.canvas,
            )
        if hasattr(self, "conversion_workspace"):
            self.conversion_workspace.apply_tokens(tokens)
        if hasattr(self, "task_tray"):
            self.task_tray.apply_tokens(tokens)
        if self._feature_palette_window is not None:
            try:
                if self._feature_palette_window.winfo_exists():
                    self._feature_palette_window.apply_tokens(tokens)
            except tk.TclError:
                self._feature_palette_window = None
        if hasattr(self, "command_dock"):
            self.command_dock.apply_palette(
                background=tokens.canvas,
                container=tokens.surface_alt,
                border=tokens.surface_high,
            )
        if hasattr(self, "dock_zoom_percent_entry"):
            self.dock_zoom_percent_entry.configure(
                background=tokens.canvas,
                foreground=tokens.foreground,
                insertbackground=tokens.foreground,
                highlightbackground=tokens.canvas,
                highlightcolor=tokens.foreground,
                selectbackground=tokens.surface_high,
                selectforeground=tokens.foreground,
            )
        if hasattr(self, "canvas_tools"):
            self.canvas_tools.apply_palette(
                background=tokens.canvas,
                container=tokens.surface,
                border=tokens.border,
                shadow=tokens.surface_high,
            )
        if hasattr(self, "animation_controls"):
            self.animation_controls.apply_palette(
                background=tokens.canvas,
                container=tokens.surface,
                border=tokens.border,
                shadow=tokens.surface_high,
            )
        if hasattr(self, "crop_action_bar"):
            self.crop_action_bar.apply_palette(
                background=tokens.canvas,
                container=tokens.surface,
                border=tokens.border,
                shadow=tokens.surface_high,
            )
        if hasattr(self, "zoom_preset_bar"):
            self.zoom_preset_bar.apply_palette(
                background=tokens.canvas,
                container=tokens.surface,
                border=tokens.border,
                shadow=tokens.surface_high,
            )
        if hasattr(self, "viewer_markup_bar"):
            self.viewer_markup_bar.apply_palette(
                background=tokens.canvas,
                container=tokens.surface,
                border=tokens.border,
                shadow=tokens.surface_high,
            )
        if hasattr(self, "animation_frame_slider"):
            self.animation_frame_slider.apply_palette(
                surface=tokens.surface,
                track=tokens.surface_high,
                accent=tokens.accent,
                outline=tokens.border,
            )

    def _on_accessible_widget_map(self, event: tk.Event) -> None:
        widget = event.widget
        # A ttk.Combobox drop-down is created directly by Tcl.  Its <Map>
        # event carries a string widget path rather than a Tkinter object and
        # there is no Python-side child tree on which to set accessible names.
        if not isinstance(widget, tk.Misc):
            return
        try:
            widget.after_idle(lambda: self._name_interactive_widgets(widget))
        except tk.TclError:
            pass

    def _name_interactive_widgets(self, widget: tk.Misc) -> None:
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            try:
                child_class = child.winfo_class()
                if child_class in {
                    "TButton",
                    "TCheckbutton",
                    "TRadiobutton",
                    "TEntry",
                    "TCombobox",
                    "TSpinbox",
                    "TScale",
                    "Listbox",
                    "Canvas",
                }:
                    name = str(getattr(child, "accessible_name", ""))
                    if "text" in child.keys():
                        name = str(child.cget("text")) or name
                    if not name:
                        name = child_class
                    set_accessible_name(child, name)
            except tk.TclError:
                pass
            self._name_interactive_widgets(child)

    def _poll_system_theme(self) -> None:
        self._theme_poll_job = None
        if self.theme_mode_var.get() == "system":
            resolved = resolve_theme("system")
            if resolved.name != self._last_theme_name:
                tokens = self.theme_manager.apply("system")
                self._last_theme_name = tokens.name
                self._apply_thumbnail_palette(tokens)
                self.localizer.apply_widget_tree(self)
        self._theme_poll_job = self.after(2000, self._poll_system_theme)

    def _fit_dialog(self, window: tk.Toplevel, width: int, height: int) -> None:
        work = monitor_work_area(self)
        scale = dpi_scale(self.dpi_manager.current_dpi)
        requested_width = round(width * scale)
        requested_height = round(height * scale)
        bounded_width = min(requested_width, max(420, work.width - 32))
        bounded_height = min(requested_height, max(320, work.height - 32))
        x = work.left + max(16, (work.width - bounded_width) // 2)
        y = work.top + max(16, (work.height - bounded_height) // 2)
        window.geometry(f"{bounded_width}x{bounded_height}+{x}+{y}")
        window.maxsize(work.width, work.height)

    def _update_monitor_constraints(self) -> None:
        work = monitor_work_area(self)
        scale = dpi_scale(self.dpi_manager.current_dpi)
        minimum_width = min(round(1040 * scale), max(320, work.width - 24))
        minimum_height = min(round(680 * scale), max(240, work.height - 24))
        self.minsize(minimum_width, minimum_height)

    def _show_empty_state(self) -> None:
        if self.original is not None or not hasattr(self, "canvas"):
            return
        width = max(500, self.canvas.winfo_width())
        height = max(320, self.canvas.winfo_height())
        tokens = self.theme_manager.tokens
        self.canvas.delete("all")
        self.canvas.create_text(
            width // 2,
            height // 2 - 34,
            text=self.localizer.gettext("empty_title"),
            fill=tokens.foreground,
            font=("Microsoft YaHei UI", 18, "bold"),
            tags=("empty",),
        )
        self.canvas.create_text(
            width // 2,
            height // 2 + 2,
            text=self.localizer.gettext("empty_body"),
            fill=tokens.muted,
            font=("Microsoft YaHei UI", 10),
            tags=("empty",),
        )
        button = self.canvas.create_text(
            width // 2,
            height // 2 + 52,
            text=f"  {self.localizer.gettext('empty_open')}  ",
            fill=tokens.on_accent,
            font=("Microsoft YaHei UI", 10, "bold"),
            tags=("empty_open",),
        )
        bounds = self.canvas.bbox(button)
        if bounds:
            rectangle = self.canvas.create_rectangle(
                bounds[0] - 10,
                bounds[1] - 7,
                bounds[2] + 10,
                bounds[3] + 7,
                fill=tokens.accent,
                outline=tokens.accent,
                tags=("empty_open",),
            )
            self.canvas.tag_lower(rectangle, button)
        self.canvas.tag_bind("empty_open", "<Button-1>", lambda _event: self.open_file())
        self.canvas.tag_bind("empty_open", "<Return>", lambda _event: self.open_file())

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")

        self.columnconfigure(0, weight=0, minsize=220)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0, minsize=296)
        self.rowconfigure(2, weight=1)

        self._build_menu()
        self.config(menu="")

        self.window_titlebar: MaterialTitleBar | None = None
        if self.window_controller is not None:
            self.window_titlebar = MaterialTitleBar(
                self,
                titlevariable=self.window_title_var,
                controller=self.window_controller,
                close_command=self.close,
            )
            self.window_titlebar.grid(row=0, column=0, columnspan=3, sticky="ew")

        self.toolbar = ttk.Frame(self, style="AppBar.TFrame", padding=(18, 11))
        self.toolbar.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.toolbar.columnconfigure(1, weight=1)

        brand = ttk.Frame(self.toolbar, style="AppBar.TFrame")
        brand.grid(row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Label(brand, text="◈", style="BrandMark.TLabel").grid(
            row=0, column=0, rowspan=2, padx=(0, 9)
        )
        ttk.Label(brand, text=APP_NAME, style="Title.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(
            brand,
            text="个性化影像工作台",
            style="Eyebrow.TLabel",
        ).grid(row=1, column=1, sticky="w")

        context = ttk.Frame(self.toolbar, style="AppBar.TFrame")
        context.grid(row=0, column=1, sticky="w")
        ttk.Label(
            context,
            textvariable=self.info_var,
            style="Muted.Toolbar.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            context,
            text="本地编辑 · 自动保存",
            style="Eyebrow.TLabel",
        ).grid(row=1, column=0, sticky="w")

        self.previous_button = ttk.Button(
            self.toolbar,
            text="‹",
            width=3,
            style="Icon.TButton",
            command=lambda: self.navigate_folder(-1),
        )
        self.previous_button.grid(row=0, column=2, padx=(8, 2))
        ttk.Button(
            self.toolbar,
            text="›",
            width=3,
            style="Icon.TButton",
            command=lambda: self.navigate_folder(1),
        ).grid(row=0, column=3, padx=(0, 8))
        ttk.Button(
            self.toolbar,
            text="幻灯片",
            width=5,
            style="Quiet.TButton",
            command=self.toggle_slideshow,
        ).grid(row=0, column=4, padx=(0, 2))
        ttk.Button(
            self.toolbar,
            text="属性",
            width=4,
            style="Quiet.TButton",
            command=self.show_properties,
        ).grid(row=0, column=5, padx=(0, 8))
        ttk.Checkbutton(
            self.toolbar,
            text="编辑器",
            style="Toolbar.TCheckbutton",
            variable=self.edit_panel_visible_var,
            command=self.toggle_edit_panel,
        ).grid(row=0, column=6, padx=(0, 8))
        ttk.Checkbutton(
            self.toolbar,
            text="资源库",
            style="Toolbar.TCheckbutton",
            variable=self.thumbnail_sidebar_visible_var,
            command=self.toggle_thumbnail_sidebar,
        ).grid(row=0, column=7, padx=(0, 10))
        ttk.Label(self.toolbar, text="缩放", style="Toolbar.TLabel").grid(
            row=0, column=8, padx=(0, 4)
        )
        self.zoom_box = ttk.Combobox(
            self.toolbar, textvariable=self.zoom_mode, values=ZOOM_LEVELS, state="readonly", width=7
        )
        self.zoom_box.grid(row=0, column=9)
        self.zoom_box.bind("<<ComboboxSelected>>", lambda _event: self._zoom_changed())
        self.menu_button = ttk.Button(
            self.toolbar,
            text="⋯",
            width=3,
            style="Icon.TButton",
            command=self._show_app_menu,
        )
        self.menu_button.grid(row=0, column=10, padx=(8, 0))
        self.toolbar.grid_remove()

        self.mode_bar = ttk.Frame(self, style="Surface.TFrame", padding=(14, 5))
        self.mode_bar.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.mode_bar.columnconfigure(0, weight=1)
        self.mode_bar.columnconfigure(2, weight=1)
        mode_group = ttk.Frame(self.mode_bar, style="Surface.TFrame")
        mode_group.grid(row=0, column=1)
        self.mode_buttons: dict[ModeId, MaterialButton] = {}
        for column, mode in enumerate(ModeId):
            button = MaterialButton(
                mode_group,
                text=MODE_LABELS[mode],
                width=82,
                height=36,
                command=partial(self._activate_mode, mode),
            )
            button.grid(row=0, column=column, padx=2)
            set_accessible_name(button, f"切换到{MODE_LABELS[mode]}模式")
            self.mode_buttons[mode] = button

        self.viewer_mode_actions = ttk.Frame(self.mode_bar, style="Surface.TFrame")
        self.viewer_mode_actions.grid(row=0, column=0, sticky="w")
        self.viewer_open_folder_button = MaterialButton(
            self.viewer_mode_actions,
            text="文件夹",
            width=60,
            height=30,
            command=self.open_directory,
        )
        self.viewer_open_folder_button.grid(row=0, column=0, padx=(0, 5))
        set_accessible_name(self.viewer_open_folder_button, "打开图片文件夹")
        viewer_previous = MaterialButton(
            self.viewer_mode_actions,
            text="‹",
            width=34,
            height=30,
            variant="icon",
            command=lambda: self.navigate_folder(-1),
        )
        viewer_previous.grid(row=0, column=1, padx=(0, 1))
        set_accessible_name(viewer_previous, "上一张图片")
        viewer_next = MaterialButton(
            self.viewer_mode_actions,
            text="›",
            width=34,
            height=30,
            variant="icon",
            command=lambda: self.navigate_folder(1),
        )
        viewer_next.grid(row=0, column=2, padx=1)
        set_accessible_name(viewer_next, "下一张图片")
        viewer_fit = MaterialButton(
            self.viewer_mode_actions,
            text="适合窗口",
            width=76,
            height=30,
            command=self.reset_view,
        )
        viewer_fit.grid(row=0, column=3, padx=(5, 1))
        set_accessible_name(viewer_fit, "适应窗口")
        viewer_actual = MaterialButton(
            self.viewer_mode_actions,
            text="1:1",
            width=44,
            height=30,
            command=lambda: self.set_zoom("100%"),
        )
        viewer_actual.grid(row=0, column=4, padx=1)
        set_accessible_name(viewer_actual, "实际尺寸")
        self.viewer_file_compare_button = MaterialButton(
            self.viewer_mode_actions,
            text="双图",
            width=54,
            height=30,
            command=self._toggle_file_comparison,
        )
        self.viewer_file_compare_button.grid(row=0, column=5, padx=(5, 1))
        set_accessible_name(self.viewer_file_compare_button, "选择另一张图片并排比较")
        self.viewer_to_convert_button = MaterialButton(
            self.viewer_mode_actions,
            text="去转图",
            width=60,
            height=30,
            variant="filled",
            command=partial(self._activate_mode, ModeId.CONVERT),
        )
        self.viewer_to_convert_button.grid(row=0, column=6, padx=(7, 1))
        set_accessible_name(self.viewer_to_convert_button, "将当前图片送到转图模式")
        self.viewer_to_edit_button = MaterialButton(
            self.viewer_mode_actions,
            text="去改图",
            width=60,
            height=30,
            command=partial(self._activate_mode, ModeId.EDIT),
        )
        self.viewer_to_edit_button.grid(row=0, column=7, padx=1)
        set_accessible_name(self.viewer_to_edit_button, "将当前图片送到改图模式")
        self._viewer_secondary_action_widgets = (
            viewer_previous,
            viewer_next,
            viewer_fit,
            viewer_actual,
            self.viewer_file_compare_button,
            self.viewer_to_convert_button,
            self.viewer_to_edit_button,
        )
        for widget in self._viewer_secondary_action_widgets:
            widget.grid_remove()
        mode_status_area = ttk.Frame(self.mode_bar, style="Surface.TFrame")
        mode_status_area.grid(row=0, column=2, sticky="e", padx=(12, 0))
        ttk.Label(
            mode_status_area,
            textvariable=self.mode_hint_var,
            style="Muted.Toolbar.TLabel",
        ).grid(row=0, column=0, sticky="e", padx=(0, 8))
        self.task_tray = TaskTray(
            self,
            mode_status_area,
            on_activate=self._activate_unified_task,
            on_cancel=self._cancel_unified_task,
            on_retry=self._retry_unified_task,
            on_changed=self._task_tray_changed,
        )
        self.task_tray.trigger_button.grid(row=0, column=1, sticky="e")
        self.favorite_toolbar = ttk.Frame(self.mode_bar, style="Surface.TFrame")
        self.favorite_toolbar.grid(
            row=1,
            column=0,
            columnspan=3,
            pady=(4, 0),
        )
        self._refresh_favorite_toolbar()
        # Favorites remain available from workflow settings and the command
        # palette, but no longer consume a permanent row in the primary shell.
        self.favorite_toolbar.grid_remove()
        set_accessible_name(self.mode_bar, "四模式导航")

        self.thumbnail_sidebar = ThumbnailSidebar(
            self,
            self._activate_thumbnail,
            loader=ThumbnailLoader(
                max_workers=max(1, min(4, self.decode_workers_var.get())), cache_size=96
            ),
            title="资源库",
            style="Surface.TFrame",
        )
        self.thumbnail_sidebar.grid(row=2, column=0, sticky="nsew")

        self.conversion_workspace = ConversionWorkspace(
            self,
            self._activate_conversion_input,
            on_queue_changed=self._conversion_queue_changed,
            on_request_files=self._choose_conversion_files,
            on_request_folder=self._choose_conversion_folder,
            on_retry_tasks=self._retry_conversion_tasks,
            on_open_result=self._open_conversion_result,
            on_reveal_result=self._reveal_file,
            tokens=self.theme_manager.tokens,
        )
        self.conversion_workspace.grid(row=2, column=0, sticky="nsew")
        self.conversion_workspace.grid_remove()

        canvas_frame = ttk.Frame(self, style="CanvasShell.TFrame", padding=(0, 0, 0, 0))
        self.canvas_frame = canvas_frame
        canvas_frame.grid(row=2, column=1, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            canvas_frame,
            bg="#1f232b",
            highlightthickness=1,
            highlightcolor="#67507a",
            cursor="crosshair",
            takefocus=True,
        )
        self.canvas.material_role = "editor-canvas"
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<ButtonPress-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<Motion>", self._canvas_motion)
        self.canvas.bind("<Leave>", self._canvas_leave)
        self.canvas.bind("<MouseWheel>", self._zoom_with_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._zoom_with_wheel)
        self.canvas.bind("<ButtonPress-2>", self._start_pan)
        self.canvas.bind("<B2-Motion>", self._drag_pan)
        self.canvas.bind("<ButtonRelease-2>", self._finish_pan)
        self.canvas.bind("<ButtonPress-3>", self._start_pan)
        self.canvas.bind("<B3-Motion>", self._drag_pan)
        self.canvas.bind("<ButtonRelease-3>", self._finish_pan)
        self.canvas.bind("<Double-Button-1>", lambda _event: self.reset_view())
        self.canvas.bind("<Alt-Left>", lambda _event: self._nudge_selected(-1, 0))
        self.canvas.bind("<Alt-Right>", lambda _event: self._nudge_selected(1, 0))
        self.canvas.bind("<Alt-Up>", lambda _event: self._nudge_selected(0, -1))
        self.canvas.bind("<Alt-Down>", lambda _event: self._nudge_selected(0, 1))

        self.canvas_tools = MaterialFloatingBar(canvas_frame, max_width=178)
        self.canvas_tools.place(x=14, y=14, anchor="nw")
        floating_tools = self.canvas_tools.content
        self.canvas_select_button = MaterialButton(
            floating_tools,
            text="↖",
            width=38,
            height=34,
            variant="icon",
            command=lambda: self._set_annotation_tool("select"),
        )
        self.canvas_select_button.grid(row=0, column=0, padx=1)
        set_accessible_name(self.canvas_select_button, "选择工具")
        self.canvas_pan_button = MaterialButton(
            floating_tools,
            text="✥",
            width=38,
            height=34,
            variant="icon",
            command=self._show_pan_hint,
        )
        self.canvas_pan_button.grid(row=0, column=1, padx=1)
        set_accessible_name(self.canvas_pan_button, "平移画布")
        self.canvas_fit_button = MaterialButton(
            floating_tools,
            text="⌕",
            width=38,
            height=34,
            variant="icon",
            command=self.reset_view,
        )
        self.canvas_fit_button.grid(row=0, column=2, padx=1)
        set_accessible_name(self.canvas_fit_button, "适合画布")

        self.crop_action_bar = MaterialFloatingBar(canvas_frame, max_width=520)
        crop_action_content = self.crop_action_bar.content
        ttk.Label(
            crop_action_content,
            textvariable=self.crop_action_hint_var,
            style="Muted.Toolbar.TLabel",
        ).grid(row=0, column=0, sticky="w", padx=(5, 10))
        self.crop_action_apply_button = MaterialButton(
            crop_action_content,
            text="应用裁剪  Enter",
            width=132,
            height=34,
            variant="filled",
            command=self.apply_crop,
        )
        self.crop_action_apply_button.grid(row=0, column=1, padx=2)
        self.crop_action_apply_button.state(("disabled",))
        set_accessible_name(self.crop_action_apply_button, "应用当前裁剪区域")
        self.crop_action_cancel_button = MaterialButton(
            crop_action_content,
            text="取消  Esc",
            width=84,
            height=34,
            variant="tonal",
            command=self._cancel_crop_interaction,
        )
        self.crop_action_cancel_button.grid(row=0, column=2, padx=(2, 5))
        set_accessible_name(self.crop_action_cancel_button, "取消本次裁剪")
        self.crop_action_bar.place_forget()

        self.animation_controls = MaterialFloatingBar(canvas_frame, max_width=500)
        animation_bar = self.animation_controls.content
        animation_bar.columnconfigure(3, weight=1)
        self.animation_previous_button = MaterialButton(
            animation_bar,
            text="‹",
            width=34,
            height=34,
            variant="icon",
            command=lambda: self._step_animation(-1),
        )
        self.animation_previous_button.grid(row=0, column=0, padx=1)
        set_accessible_name(self.animation_previous_button, "上一帧")
        self.animation_play_button = MaterialButton(
            animation_bar,
            text="暂停",
            width=54,
            height=34,
            command=self._toggle_animation_playback,
        )
        self.animation_play_button.grid(row=0, column=1, padx=1)
        set_accessible_name(self.animation_play_button, "暂停动画")
        self.animation_next_button = MaterialButton(
            animation_bar,
            text="›",
            width=34,
            height=34,
            variant="icon",
            command=lambda: self._step_animation(1),
        )
        self.animation_next_button.grid(row=0, column=2, padx=1)
        set_accessible_name(self.animation_next_button, "下一帧")
        self.animation_frame_slider = MaterialSlider(
            animation_bar,
            variable=self.animation_frame_var,
            from_=1,
            to=1,
            command=self._seek_animation_from_slider,
        )
        self.animation_frame_slider.configure(width=132)
        self.animation_frame_slider.grid(row=0, column=3, sticky="ew", padx=(7, 3))
        set_accessible_name(self.animation_frame_slider, "动画帧进度")
        ttk.Label(
            animation_bar,
            textvariable=self.animation_position_var,
            style="Muted.Toolbar.TLabel",
        ).grid(row=0, column=4, padx=(3, 5))
        ttk.Label(
            animation_bar,
            text="速度",
            style="Muted.Toolbar.TLabel",
        ).grid(row=0, column=5, padx=(1, 3))
        self.animation_speed_box = ttk.Combobox(
            animation_bar,
            textvariable=self.animation_speed_var,
            values=tuple(ANIMATION_SPEED_FACTORS),
            state="readonly",
            justify="center",
            width=5,
        )
        self.animation_speed_box.grid(row=0, column=6, padx=(0, 2))
        self.animation_speed_box.bind(
            "<<ComboboxSelected>>",
            self._animation_speed_changed,
        )
        set_accessible_name(self.animation_speed_box, "动画播放速度")
        self.animation_controls.place_forget()
        self.canvas_metadata = ttk.Label(
            canvas_frame,
            text="P3 色域 · 8 位    适合画布",
            style="CanvasMeta.TLabel",
        )
        self.canvas_metadata.place(relx=1.0, rely=1.0, x=-18, y=-88, anchor="se")

        self.generate_workspace = ttk.Frame(
            canvas_frame,
            style="CanvasShell.TFrame",
            padding=(10, 10),
        )
        self.generate_workspace.grid(row=0, column=0, sticky="nsew")
        self.generate_workspace.columnconfigure(0, weight=1)
        self.generate_workspace.rowconfigure(0, weight=1)
        generate_card = ttk.Frame(
            self.generate_workspace,
            style="Surface.TFrame",
            padding=(18, 16),
        )
        generate_card.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        generate_card.columnconfigure(0, weight=0, minsize=300)
        generate_card.columnconfigure(1, weight=1)
        generate_card.rowconfigure(2, weight=1)
        ttk.Label(generate_card, text="生图模式", style="Title.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            generate_card,
            text=("先用完全离线的模拟服务验证任务、取消和候选流转；真实模型后续按能力协议接入。"),
            style="Muted.Toolbar.TLabel",
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 18))
        generation_setup = self.generation_setup_panel = ttk.Frame(
            generate_card,
            style="Surface.TFrame",
        )
        generation_setup.grid(row=2, column=0, sticky="nsew", padx=(0, 24))
        generation_setup.columnconfigure(0, weight=1)
        candidate_panel = self.generation_candidate_panel = ttk.Frame(
            generate_card,
            style="Surface.TFrame",
        )
        candidate_panel.grid(row=2, column=1, sticky="nsew")
        candidate_panel.columnconfigure(0, weight=1)
        candidate_panel.rowconfigure(2, weight=1)
        ttk.Label(generation_setup, text="提示词").grid(row=0, column=0, sticky="w")
        self.generate_prompt_box = ttk.Combobox(
            generation_setup,
            textvariable=self.generate_prompt_var,
            values=tuple(self.generation_prompt_history),
            state="normal",
            width=36,
        )
        self.generate_prompt_box.grid(row=1, column=0, sticky="ew", pady=(5, 12))
        self.generate_prompt_box.bind("<Return>", lambda _event: self._start_mock_generation())
        ttk.Label(generation_setup, text="负面提示词").grid(row=2, column=0, sticky="w")
        self.generate_negative_prompt_entry = ttk.Entry(
            generation_setup,
            textvariable=self.generate_negative_prompt_var,
        )
        self.generate_negative_prompt_entry.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(5, 10),
        )
        reference_box = ttk.LabelFrame(
            generation_setup,
            text="参考图（可选）",
            style="Panel.TLabelframe",
            padding=(8, 6),
        )
        reference_box.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        reference_box.columnconfigure(1, weight=1)
        ttk.Label(
            reference_box,
            textvariable=self.generate_reference_status_var,
            style="Muted.Toolbar.TLabel",
            wraplength=290,
            justify="left",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 5))
        self.generate_reference_listbox = tk.Listbox(
            reference_box,
            height=3,
            exportselection=False,
            activestyle="none",
            selectmode=tk.SINGLE,
        )
        self.generate_reference_listbox.grid(
            row=1,
            column=0,
            columnspan=4,
            sticky="ew",
            pady=(0, 6),
        )
        self.generate_reference_listbox.bind(
            "<<ListboxSelect>>",
            self._generation_reference_selected,
        )
        self.generate_reference_choose_button = ttk.Button(
            reference_box,
            text="添加图片…",
            width=9,
            command=self._choose_generation_reference,
        )
        self.generate_reference_choose_button.grid(row=2, column=0, padx=(0, 5))
        self.generate_reference_current_button = ttk.Button(
            reference_box,
            text="添加当前图",
            width=9,
            command=self._use_current_generation_reference,
        )
        self.generate_reference_current_button.grid(row=2, column=1, sticky="w")
        self.generate_reference_remove_button = ttk.Button(
            reference_box,
            text="移除",
            width=6,
            command=self._remove_selected_generation_reference,
        )
        self.generate_reference_remove_button.grid(row=2, column=2, sticky="e")
        self.generate_reference_clear_button = ttk.Button(
            reference_box,
            text="清空",
            width=6,
            command=self._clear_generation_reference,
        )
        self.generate_reference_clear_button.grid(row=2, column=3, sticky="e")
        ttk.Label(reference_box, text="角色").grid(row=3, column=0, sticky="w", pady=(7, 0))
        self.generate_reference_role_box = ttk.Combobox(
            reference_box,
            textvariable=self.generate_reference_role_var,
            values=tuple(GENERATION_REFERENCE_ROLES),
            state="readonly",
            width=7,
        )
        self.generate_reference_role_box.grid(
            row=3,
            column=1,
            sticky="w",
            pady=(7, 0),
        )
        ttk.Label(reference_box, text="强度").grid(
            row=3,
            column=2,
            sticky="e",
            padx=(8, 4),
            pady=(7, 0),
        )
        self.generate_reference_strength_spinbox = ttk.Spinbox(
            reference_box,
            from_=0,
            to=100,
            increment=5,
            width=5,
            textvariable=self.generate_reference_strength_var,
            justify="center",
        )
        self.generate_reference_strength_spinbox.grid(
            row=3,
            column=3,
            sticky="e",
            pady=(7, 0),
        )
        ttk.Label(
            reference_box,
            textvariable=self.generate_provider_capability_var,
            style="Muted.Toolbar.TLabel",
            wraplength=290,
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(7, 0))
        self.generate_reference_role_box.bind(
            "<<ComboboxSelected>>",
            self._update_selected_generation_reference_settings,
        )
        self.generate_reference_strength_spinbox.bind(
            "<FocusOut>",
            self._update_selected_generation_reference_settings,
        )
        self.generate_reference_strength_spinbox.bind(
            "<Return>",
            self._update_selected_generation_reference_settings,
        )
        options = ttk.Frame(generation_setup, style="Surface.TFrame")
        options.grid(row=5, column=0, sticky="ew")
        ttk.Label(options, text="服务").grid(row=0, column=0, sticky="w")
        self.generate_provider_box = ttk.Combobox(
            options,
            textvariable=self.generate_provider_var,
            values=tuple(
                profile.display_name for profile in self.generation_provider_catalog.profiles
            ),
            state="readonly",
            width=12,
        )
        self.generate_provider_box.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(6, 0))
        self.generate_provider_box.bind(
            "<<ComboboxSelected>>",
            self._generation_provider_selected,
        )
        ttk.Label(options, text="尺寸").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.generate_size_box = ttk.Combobox(
            options,
            textvariable=self.generate_size_var,
            values=("768 × 768", "1024 × 1024", "1344 × 768", "768 × 1344"),
            state="readonly",
            width=12,
        )
        self.generate_size_box.grid(row=1, column=1, padx=(6, 18), pady=(8, 0))
        ttk.Label(options, text="数量").grid(row=1, column=2, sticky="w", pady=(8, 0))
        self.generate_count_spinbox = ttk.Spinbox(
            options,
            from_=1,
            to=4,
            textvariable=self.generate_count_var,
            width=5,
        )
        self.generate_count_spinbox.grid(row=1, column=3, padx=(6, 0), pady=(8, 0))
        ttk.Label(options, text="风格").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.generate_style_box = ttk.Combobox(
            options,
            textvariable=self.generate_style_preset_var,
            values=tuple(GENERATION_STYLE_PRESETS),
            state="readonly",
            width=15,
        )
        self.generate_style_box.grid(row=2, column=1, padx=(6, 18), pady=(8, 0))
        self.generate_seed_checkbutton = ttk.Checkbutton(
            options,
            text="固定种子",
            variable=self.generate_seed_enabled_var,
        )
        self.generate_seed_checkbutton.grid(row=2, column=2, sticky="w", pady=(8, 0))
        self.generate_seed_entry = ttk.Entry(
            options,
            textvariable=self.generate_seed_var,
            width=9,
        )
        self.generate_seed_entry.grid(row=2, column=3, padx=(6, 0), pady=(8, 0))
        generate_actions = ttk.Frame(generation_setup, style="Surface.TFrame")
        generate_actions.grid(row=6, column=0, sticky="w", pady=(14, 10))
        self.mock_generate_button = MaterialButton(
            generate_actions,
            text="运行离线测试",
            width=132,
            variant="filled",
            command=self._start_mock_generation,
        )
        self.mock_generate_button.grid(row=0, column=0, padx=(0, 8))
        self.mock_cancel_button = MaterialButton(
            generate_actions,
            text="取消任务",
            width=92,
            command=self._cancel_mock_generation,
        )
        self.mock_cancel_button.grid(row=0, column=1)
        self.mock_retry_button = MaterialButton(
            generate_actions,
            text="重试失败任务",
            width=112,
            command=self._retry_failed_generation,
        )
        self.mock_retry_button.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            candidate_panel,
            textvariable=self.generate_status_var,
            style="InspectorTitle.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 3))
        ttk.Label(
            candidate_panel,
            textvariable=self.generate_candidates_var,
            style="Muted.Toolbar.TLabel",
            wraplength=560,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))
        candidate_shell = ttk.Frame(candidate_panel, style="Surface.TFrame")
        candidate_shell.grid(row=2, column=0, sticky="nsew")
        candidate_shell.columnconfigure(0, weight=1)
        candidate_shell.rowconfigure(0, weight=1)
        candidate_canvas = tk.Canvas(
            candidate_shell,
            width=264,
            height=360,
            highlightthickness=0,
        )
        candidate_canvas.material_role = "panel"
        candidate_canvas.grid(row=0, column=0, sticky="nsew")
        candidate_scroll = ttk.Scrollbar(
            candidate_shell,
            orient="vertical",
            command=candidate_canvas.yview,
        )
        candidate_scroll.grid(row=0, column=1, sticky="ns")
        candidate_canvas.configure(yscrollcommand=candidate_scroll.set)
        self.generation_candidate_grid = GenerationCandidateGrid(
            candidate_canvas,
            on_select=self._select_generated_candidate,
            columns=2,
            thumbnail_size=(112, 74),
        )
        candidate_window = candidate_canvas.create_window(
            0,
            0,
            window=self.generation_candidate_grid,
            anchor="nw",
        )
        self.generation_candidate_grid.bind(
            "<Configure>",
            lambda _event: candidate_canvas.configure(scrollregion=candidate_canvas.bbox("all")),
        )
        candidate_canvas.bind(
            "<Configure>",
            lambda event: candidate_canvas.itemconfigure(candidate_window, width=event.width),
        )
        candidate_actions = ttk.Frame(candidate_panel, style="Surface.TFrame")
        candidate_actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        candidate_actions.columnconfigure((0, 1, 2), weight=1, uniform="candidate-action")
        self.generated_open_button = MaterialButton(
            candidate_actions,
            text="打开查看",
            width=82,
            height=36,
            command=lambda: self._send_generated_candidate_to_mode(ModeId.VIEWER),
        )
        self.generated_open_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.generated_edit_button = MaterialButton(
            candidate_actions,
            text="发送到改图",
            width=82,
            height=36,
            command=lambda: self._send_generated_candidate_to_mode(ModeId.EDIT),
        )
        self.generated_edit_button.grid(row=0, column=1, sticky="ew", padx=4)
        self.generated_convert_button = MaterialButton(
            candidate_actions,
            text="发送到转图",
            width=82,
            height=36,
            variant="tonal",
            command=lambda: self._send_generated_candidate_to_mode(ModeId.CONVERT),
        )
        self.generated_convert_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        self.generated_variation_button = MaterialButton(
            candidate_actions,
            text="基于所选生成变体",
            height=36,
            variant="filled",
            command=self._generate_selected_candidate_variation,
        )
        self.generated_variation_button.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(8, 0),
        )
        self.generated_favorite_button = MaterialButton(
            candidate_actions,
            text="收藏候选",
            height=34,
            variant="tonal",
            command=self._toggle_generated_candidate_favorite,
        )
        self.generated_favorite_button.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(8, 0),
        )
        self.generated_remove_button = MaterialButton(
            candidate_actions,
            text="删除候选",
            height=34,
            command=self._remove_generated_candidate,
        )
        self.generated_remove_button.grid(
            row=2,
            column=1,
            sticky="ew",
            padx=4,
            pady=(8, 0),
        )
        self.generated_favorites_only_checkbutton = ttk.Checkbutton(
            candidate_actions,
            text="仅看收藏",
            variable=self.generate_favorites_only_var,
            command=self._generation_favorites_filter_changed,
        )
        self.generated_favorites_only_checkbutton.grid(
            row=2,
            column=2,
            sticky="w",
            padx=(4, 0),
            pady=(8, 0),
        )
        for button in (
            self.generated_open_button,
            self.generated_edit_button,
            self.generated_convert_button,
            self.generated_variation_button,
            self.generated_favorite_button,
            self.generated_remove_button,
        ):
            button.state(("disabled",))
        self.mock_cancel_button.state(("disabled",))
        self.mock_retry_button.state(("disabled",))
        set_accessible_name(self.generate_prompt_box, "生图提示词与历史")
        set_accessible_name(self.generate_negative_prompt_entry, "生图负面提示词")
        set_accessible_name(self.generate_reference_listbox, "已选择的生成参考图")
        set_accessible_name(self.generate_reference_choose_button, "添加本地生成参考图")
        set_accessible_name(self.generate_reference_current_button, "添加当前图片作为生成参考图")
        set_accessible_name(self.generate_reference_remove_button, "移除所选生成参考图")
        set_accessible_name(self.generate_reference_clear_button, "清空生成参考图")
        set_accessible_name(self.generate_reference_role_box, "参考图角色")
        set_accessible_name(self.generate_reference_strength_spinbox, "参考图强度")
        set_accessible_name(self.generate_style_box, "生成风格预设")
        set_accessible_name(self.generate_seed_checkbutton, "启用固定生成种子")
        set_accessible_name(self.generate_seed_entry, "生成种子")
        set_accessible_name(self.generate_provider_box, "生图服务选择")
        set_accessible_name(self.mock_retry_button, "重试失败的生图任务")
        set_accessible_name(self.generated_open_button, "打开所选生成候选")
        set_accessible_name(self.generated_edit_button, "将所选生成候选发送到改图")
        set_accessible_name(self.generated_convert_button, "将所选生成候选发送到转图")
        set_accessible_name(self.generated_variation_button, "基于所选生成候选创建变体")
        set_accessible_name(self.generated_favorite_button, "收藏或取消收藏所选生成候选")
        set_accessible_name(self.generated_remove_button, "从生图工作区删除所选候选")
        set_accessible_name(self.generated_favorites_only_checkbutton, "仅显示收藏的生成候选")
        self.generate_workspace.grid_remove()

        self.panel_shell = ttk.Frame(
            self,
            style="Surface.TFrame",
            padding=(12, 6, 12, 8),
            width=296,
        )
        self.panel_shell.grid(row=2, column=2, sticky="nsew")
        self.panel_shell.grid_propagate(False)
        self.panel_shell.rowconfigure(1, weight=1)
        self.panel_shell.columnconfigure(0, weight=1)

        inspector_header = ttk.Frame(self.panel_shell, style="InspectorHeader.TFrame")
        self.inspector_header = inspector_header
        inspector_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        inspector_header.columnconfigure(0, weight=1)
        self.inspector_tabs = MaterialTabStrip(
            inspector_header,
            tabs=(
                ("convert", "转图"),
                ("adjust", "调整"),
                ("smart", "智能"),
                ("annotation", "标注"),
                ("history", "历史"),
            ),
            command=self._show_inspector_tab,
            selected="adjust",
        )
        self.inspector_tabs.grid(row=0, column=0, sticky="ew")

        panel_canvas = tk.Canvas(self.panel_shell, width=270, highlightthickness=0)
        self.panel_canvas = panel_canvas
        panel_canvas.material_role = "panel"
        panel_canvas.grid(row=1, column=0, sticky="nsew")
        panel_scroll = ttk.Scrollbar(
            self.panel_shell, orient="vertical", command=panel_canvas.yview
        )
        panel_scroll.grid(row=1, column=1, sticky="ns")
        panel_canvas.configure(yscrollcommand=panel_scroll.set)
        panel = ttk.Frame(panel_canvas, style="Surface.TFrame", padding=(4, 0, 8, 8))
        self.panel_content = panel
        panel_window = panel_canvas.create_window(0, 0, window=panel, anchor="nw")
        panel.bind(
            "<Configure>",
            lambda event: panel_canvas.configure(scrollregion=panel_canvas.bbox("all")),
        )
        panel_canvas.bind(
            "<Configure>", lambda event: panel_canvas.itemconfigure(panel_window, width=event.width)
        )
        panel_canvas.bind(
            "<Enter>",
            lambda _event: panel_canvas.bind_all(
                "<MouseWheel>",
                lambda event: panel_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"),
            ),
        )
        panel_canvas.bind("<Leave>", lambda _event: panel_canvas.unbind_all("<MouseWheel>"))
        panel.columnconfigure(0, weight=1)

        conversion_job_box = ttk.LabelFrame(
            panel,
            text="转换任务",
            style="Panel.TLabelframe",
        )
        self.conversion_job_box = conversion_job_box
        conversion_job_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        conversion_job_box.columnconfigure(0, weight=1)
        ttk.Label(
            conversion_job_box,
            textvariable=self.conversion_recipe_status_var,
            style="Muted.Toolbar.TLabel",
            wraplength=248,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(4, 7))
        conversion_recipe_actions = ttk.Frame(conversion_job_box)
        conversion_recipe_actions.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        conversion_recipe_actions.columnconfigure((0, 1, 2), weight=1, uniform="recipe")
        ttk.Button(
            conversion_recipe_actions,
            text="导入配方",
            command=self._import_conversion_recipe,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(
            conversion_recipe_actions,
            text="导出配方",
            command=self._export_conversion_recipe,
        ).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(
            conversion_recipe_actions,
            text="当前设置",
            command=self._use_current_conversion_settings,
        ).grid(row=0, column=2, sticky="ew", padx=(3, 0))
        ttk.Label(conversion_job_box, text="输出文件夹").grid(row=2, column=0, sticky="w")
        conversion_output_row = ttk.Frame(conversion_job_box)
        conversion_output_row.grid(row=3, column=0, sticky="ew", pady=(4, 9))
        conversion_output_row.columnconfigure(0, weight=1)
        ttk.Entry(
            conversion_output_row,
            textvariable=self.conversion_output_dir_var,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(
            conversion_output_row,
            text="选择…",
            width=7,
            command=self._choose_conversion_output_directory,
        ).grid(row=0, column=1)
        conversion_run_actions = ttk.Frame(conversion_job_box)
        conversion_run_actions.grid(row=4, column=0, sticky="ew")
        conversion_run_actions.columnconfigure((0, 1), weight=1, uniform="run")
        self.conversion_start_button = ttk.Button(
            conversion_run_actions,
            text="开始转换",
            style="Accent.TButton",
            command=self._start_conversion_queue,
        )
        self.conversion_start_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.conversion_cancel_button = ttk.Button(
            conversion_run_actions,
            text="取消任务",
            command=self._cancel_conversion_queue,
        )
        self.conversion_cancel_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.conversion_cancel_button.state(("disabled",))
        ttk.Label(
            conversion_job_box,
            textvariable=self.conversion_job_status_var,
            style="Muted.Toolbar.TLabel",
            wraplength=248,
            justify="left",
        ).grid(row=5, column=0, sticky="ew", pady=(8, 2))

        export_box = ttk.LabelFrame(panel, text="导出", style="Panel.TLabelframe")
        self.export_box = export_box
        export_box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        export_box.columnconfigure(1, weight=1)
        ttk.Label(export_box, text="预设").grid(row=0, column=0, sticky="w", pady=5)
        export_preset = ttk.Combobox(
            export_box,
            textvariable=self.export_preset_var,
            values=EXPORT_PRESETS,
            state="readonly",
        )
        export_preset.grid(row=0, column=1, sticky="ew", pady=5)
        export_preset.bind("<<ComboboxSelected>>", lambda _event: self._apply_export_preset())
        ttk.Label(export_box, text="格式").grid(row=1, column=0, sticky="w", pady=5)
        fmt = ttk.Combobox(
            export_box,
            textvariable=self.format_var,
            values=("JPEG", "PNG", "WEBP", "AVIF", "BMP", "TIFF", "GIF"),
            state="readonly",
        )
        fmt.grid(row=1, column=1, sticky="ew", pady=5)
        fmt.bind("<<ComboboxSelected>>", lambda _event: self._format_changed())
        self._slider(export_box, "质量", self.quality_var, 1, 100, 2, self._custom_export_changed)
        ttk.Label(export_box, text="目标 KB").grid(row=3, column=0, sticky="w", pady=5)
        target_entry = ttk.Entry(export_box, textvariable=self.target_size_var)
        self.target_size_entry = target_entry
        target_entry.grid(row=3, column=1, sticky="ew", pady=5)
        target_entry.bind("<KeyRelease>", lambda _event: self._custom_export_changed())
        self.webp_lossless_check = ttk.Checkbutton(
            export_box,
            text="WebP 无损",
            variable=self.webp_lossless_var,
            command=self._advanced_format_changed,
        )
        self.webp_lossless_check.grid(row=4, column=1, sticky="w", pady=5)
        ttk.Label(export_box, text="PNG 调色板").grid(row=5, column=0, sticky="w", pady=5)
        self.png_palette_box = ttk.Combobox(
            export_box,
            textvariable=self.png_palette_var,
            values=tuple(PNG_PALETTE_LABELS),
            state="readonly",
        )
        self.png_palette_box.grid(row=5, column=1, sticky="ew", pady=5)
        self.png_palette_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._advanced_format_changed(),
        )
        ttk.Label(export_box, text="TIFF 压缩").grid(row=6, column=0, sticky="w", pady=5)
        self.tiff_compression_box = ttk.Combobox(
            export_box,
            textvariable=self.tiff_compression_var,
            values=tuple(TIFF_COMPRESSION_LABELS),
            state="readonly",
        )
        self.tiff_compression_box.grid(row=6, column=1, sticky="ew", pady=5)
        self.tiff_compression_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._advanced_format_changed(),
        )
        ttk.Label(export_box, text="文件前缀").grid(row=7, column=0, sticky="w", pady=5)
        prefix_entry = ttk.Entry(export_box, textvariable=self.filename_prefix_var)
        prefix_entry.grid(row=7, column=1, sticky="ew", pady=5)
        prefix_entry.bind("<KeyRelease>", lambda _event: self._save_config())
        ttk.Label(export_box, text="文件后缀").grid(row=8, column=0, sticky="w", pady=5)
        suffix_entry = ttk.Entry(export_box, textvariable=self.filename_suffix_var)
        suffix_entry.grid(row=8, column=1, sticky="ew", pady=5)
        suffix_entry.bind("<KeyRelease>", lambda _event: self._save_config())
        ttk.Label(export_box, text="透明底色").grid(
            row=9,
            column=0,
            sticky="w",
            pady=5,
        )
        transparency_row = ttk.Frame(export_box)
        transparency_row.grid(row=9, column=1, sticky="ew", pady=5)
        transparency_row.columnconfigure(0, weight=1)
        transparency_box = ttk.Combobox(
            transparency_row,
            textvariable=self.conversion_background_var,
            values=tuple(TRANSPARENCY_BACKGROUND_LABELS),
            state="readonly",
            width=12,
        )
        transparency_box.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        transparency_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_conversion_draft_from_controls(),
        )
        ttk.Button(
            transparency_row,
            text="自定义",
            width=6,
            command=self._choose_conversion_transparency_background,
        ).grid(row=0, column=1)
        ttk.Checkbutton(
            export_box,
            text="保留 EXIF / ICC / DPI / XMP",
            variable=self.preserve_metadata_var,
            command=self._custom_export_changed,
        ).grid(row=10, column=1, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            export_box,
            text="覆盖前创建 .bak 备份",
            variable=self.backup_on_overwrite_var,
            command=self._save_config,
        ).grid(row=11, column=1, sticky="w", pady=(4, 0))
        self._update_format_advanced_controls()
        ttk.Checkbutton(
            export_box,
            text="保存后定位文件",
            variable=self.reveal_after_save_var,
            command=self._save_config,
        ).grid(row=9, column=1, sticky="w", pady=(4, 0))
        ttk.Label(export_box, textvariable=self.size_var).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        resize_box = ttk.LabelFrame(panel, text="尺寸", style="Panel.TLabelframe")
        self.resize_box = resize_box
        resize_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        resize_box.columnconfigure(1, weight=1)
        ttk.Label(resize_box, text="宽度").grid(row=0, column=0, sticky="w", pady=5)
        width_entry = ttk.Entry(resize_box, textvariable=self.width_var)
        width_entry.grid(row=0, column=1, sticky="ew", pady=5)
        width_entry.bind("<KeyRelease>", self._width_changed)
        ttk.Label(resize_box, text="高度").grid(row=1, column=0, sticky="w", pady=5)
        height_entry = ttk.Entry(resize_box, textvariable=self.height_var)
        height_entry.grid(row=1, column=1, sticky="ew", pady=5)
        height_entry.bind("<KeyRelease>", self._height_changed)
        ttk.Label(resize_box, text="最长边").grid(row=2, column=0, sticky="w", pady=5)
        max_side_entry = ttk.Entry(resize_box, textvariable=self.max_side_var)
        max_side_entry.grid(row=2, column=1, sticky="ew", pady=5)
        max_side_entry.bind("<KeyRelease>", lambda _event: self._max_side_changed())
        ttk.Checkbutton(resize_box, text="保持比例", variable=self.keep_ratio_var).grid(
            row=3, column=1, sticky="w", pady=(4, 0)
        )
        preset_row = ttk.Frame(resize_box)
        preset_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        for index, (label, value) in enumerate(
            (("原图", "original"), ("50%", "50"), ("25%", "25"), ("最长边 1920", "1920"))
        ):
            ttk.Button(preset_row, text=label, command=partial(self.apply_size_preset, value)).grid(
                row=0, column=index, padx=(0, 6)
            )
        ttk.Label(resize_box, text="裁剪比例").grid(row=5, column=0, sticky="w", pady=(10, 0))
        crop_ratio_row = ttk.Frame(resize_box)
        crop_ratio_row.grid(row=5, column=1, sticky="ew", pady=(10, 0))
        crop_ratio_row.columnconfigure(0, weight=1)
        crop_ratio = ttk.Combobox(
            crop_ratio_row,
            textvariable=self.crop_ratio_var,
            values=("自由", "1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"),
            state="normal",
            width=8,
        )
        crop_ratio.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        crop_ratio.bind("<<ComboboxSelected>>", lambda _event: self.apply_crop_ratio_to_current())
        crop_ratio.bind("<Return>", lambda _event: self.apply_crop_ratio_to_current())
        ttk.Button(crop_ratio_row, text="居中裁剪", command=self.center_crop).grid(row=0, column=1)
        ttk.Label(resize_box, textvariable=self.crop_var, wraplength=300).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        adjust_box = ttk.Frame(
            panel,
            style="Surface.TFrame",
            padding=(10, 8, 10, 8),
        )
        adjust_box.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        adjust_box.columnconfigure(1, weight=1)
        ttk.Label(adjust_box, text="光线", style="InspectorTitle.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self.auto_adjust_button = MaterialButton(
            adjust_box,
            text="自动",
            width=42,
            height=24,
            variant="text",
            command=self._toggle_auto_adjust,
        )
        self.auto_adjust_button.set_selected(self.auto_contrast_var.get())
        self.auto_adjust_button.grid(row=0, column=1, sticky="e", pady=(0, 4))
        self._slider(adjust_box, "曝光", self.brightness_var, -100, 100, 1, self._live_adjust)
        self._slider(adjust_box, "对比度", self.contrast_var, -100, 100, 2, self._live_adjust)
        self._slider(
            adjust_box,
            "高光",
            self.highlights_var,
            -100,
            100,
            3,
            lambda: self._advanced_color_changed("highlights", self.highlights_var.get()),
        )
        self._slider(
            adjust_box,
            "阴影",
            self.shadows_var,
            -100,
            100,
            4,
            lambda: self._advanced_color_changed("shadows", self.shadows_var.get()),
        )

        geometry_box = ttk.LabelFrame(panel, text="精细旋转", style="Panel.TLabelframe")
        geometry_box.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        geometry_box.columnconfigure(1, weight=1)
        ttk.Label(geometry_box, text="角度").grid(row=0, column=0, sticky="w", pady=5)
        fine_scale = ttk.Scale(
            geometry_box,
            from_=-45,
            to=45,
            orient="horizontal",
            variable=self.fine_rotation_var,
            command=lambda _value: self._fine_rotation_changed(),
        )
        fine_scale.grid(row=0, column=1, sticky="ew", pady=5)
        fine_scale.bind("<ButtonPress-1>", self._begin_slider_command)
        fine_scale.bind("<ButtonRelease-1>", self._end_slider_command)
        ttk.Label(geometry_box, textvariable=self.fine_rotation_var, width=6).grid(
            row=0, column=2, sticky="e"
        )
        ttk.Checkbutton(
            geometry_box,
            text="自动裁去旋转空边",
            variable=self.fine_rotation_auto_crop_var,
            command=self._fine_rotation_changed,
        ).grid(row=1, column=1, sticky="w")
        ttk.Button(geometry_box, text="自动拉直", command=self.auto_straighten).grid(
            row=1, column=2, padx=(6, 0)
        )

        tone_box = ttk.Frame(
            panel,
            style="Surface.TFrame",
            padding=(10, 8, 10, 8),
        )
        tone_box.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        tone_box.columnconfigure(1, weight=1)
        ttk.Label(tone_box, text="颜色", style="InspectorTitle.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        MaterialButton(
            tone_box,
            text="重置",
            width=42,
            height=24,
            variant="text",
            command=self._reset_color_controls,
        ).grid(row=0, column=1, sticky="e", pady=(0, 4))
        self._slider(
            tone_box,
            "色温",
            self.temperature_var,
            -100,
            100,
            1,
            lambda: self._advanced_color_changed("temperature", self.temperature_var.get()),
        )
        self._slider(
            tone_box,
            "自然饱和度",
            self.saturation_var,
            -100,
            100,
            2,
            self._live_adjust,
        )
        self._slider(
            tone_box,
            "色调",
            self.tint_var,
            -100,
            100,
            3,
            lambda: self._advanced_color_changed("tint", self.tint_var.get()),
        )

        detail_section = ttk.Frame(panel, style="Surface.TFrame")
        detail_section.columnconfigure(0, weight=1)
        self.detail_expanded_var = tk.BooleanVar(value=False)
        self.detail_toggle_button = ttk.Button(
            detail_section,
            text="细节  ⌄",
            style="Quiet.TButton",
            command=self._toggle_detail_section,
        )
        self.detail_toggle_button.grid(row=0, column=0, sticky="ew")
        self.detail_body = ttk.Frame(detail_section, style="Surface.TFrame", padding=(4, 8, 4, 4))
        self.detail_body.grid(row=1, column=0, sticky="ew")
        self.detail_body.columnconfigure(1, weight=1)
        self._slider(
            self.detail_body,
            "锐化",
            self.sharpness_var,
            0,
            100,
            0,
            self._live_adjust,
        )
        self._slider(
            self.detail_body,
            "降噪",
            self.denoise_var,
            0,
            100,
            1,
            lambda: self._advanced_color_changed("denoise", self.denoise_var.get()),
        )
        self._slider(
            self.detail_body,
            "黑场",
            self.level_black_var,
            0,
            254,
            2,
            self._levels_changed,
        )
        self._slider(
            self.detail_body,
            "白场",
            self.level_white_var,
            1,
            255,
            3,
            self._levels_changed,
        )
        ttk.Label(self.detail_body, text="Gamma").grid(row=4, column=0, sticky="w", pady=5)
        gamma_scale = MaterialSlider(
            self.detail_body,
            from_=0.1,
            to=3.0,
            variable=self.level_gamma_var,
            command=self._levels_changed,
        )
        gamma_scale.grid(row=4, column=1, sticky="ew", pady=5)
        gamma_scale.bind("<<MaterialSliderStart>>", self._begin_slider_command)
        gamma_scale.bind("<<MaterialSliderEnd>>", self._end_slider_command)
        ttk.Label(self.detail_body, textvariable=self.level_gamma_var, width=6).grid(
            row=4, column=2
        )
        self.detail_body.grid_remove()

        curve_box = ttk.LabelFrame(panel, text="曲线", style="Panel.TLabelframe")
        curve_box.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        curve_box.columnconfigure(0, weight=1)
        curve_header = ttk.Frame(curve_box)
        curve_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        curve_header.columnconfigure(0, weight=1)
        curve_channel = ttk.Combobox(
            curve_header,
            textvariable=self.curve_channel_var,
            values=("rgb", "red", "green", "blue"),
            state="readonly",
            width=8,
        )
        curve_channel.grid(row=0, column=0, sticky="w")
        curve_channel.bind("<<ComboboxSelected>>", lambda _event: self._curve_channel_changed())
        ttk.Button(curve_header, text="重置通道", command=lambda: self.curve_editor.reset()).grid(
            row=0, column=1, sticky="e"
        )
        self.curve_editor = CurveEditor(curve_box, command=self._curve_changed)
        self.curve_editor.grid(row=1, column=0, sticky="ew")
        ttk.Label(curve_box, text="左键添加/拖动，右键删除控制点").grid(
            row=2, column=0, sticky="w", pady=(5, 0)
        )

        analysis_box = ttk.LabelFrame(panel, text="直方图与历史", style="Panel.TLabelframe")
        analysis_box.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        analysis_box.columnconfigure(0, weight=1)
        self.histogram_canvas = tk.Canvas(
            analysis_box, height=105, background="#171b22", highlightthickness=0
        )
        self.histogram_canvas.material_role = "histogram"
        self.histogram_canvas.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.history_tree = ttk.Treeview(
            analysis_box,
            columns=("step",),
            show="tree",
            height=8,
            selectmode="browse",
        )
        self.history_tree.grid(row=1, column=0, sticky="ew")
        self.history_tree.bind("<<TreeviewSelect>>", self._history_selected)

        watermark_box = ttk.LabelFrame(panel, text="文字水印", style="Panel.TLabelframe")
        watermark_box.grid(row=7, column=0, sticky="ew", pady=(0, 10))
        watermark_box.columnconfigure(1, weight=1)
        ttk.Label(watermark_box, text="内容").grid(row=0, column=0, sticky="w", pady=5)
        watermark_entry = ttk.Entry(watermark_box, textvariable=self.watermark_text_var)
        watermark_entry.grid(row=0, column=1, sticky="ew", pady=5)
        watermark_entry.bind("<KeyRelease>", lambda _event: self._watermark_changed())
        ttk.Label(watermark_box, text="位置").grid(row=1, column=0, sticky="w", pady=5)
        watermark_position = ttk.Combobox(
            watermark_box,
            textvariable=self.watermark_position_var,
            values=("右下", "左下", "右上", "左上", "居中"),
            state="readonly",
            width=8,
        )
        watermark_position.grid(row=1, column=1, sticky="ew", pady=5)
        watermark_position.bind("<<ComboboxSelected>>", lambda _event: self._watermark_changed())
        self._slider(
            watermark_box, "透明度", self.watermark_opacity_var, 0, 100, 2, self._watermark_changed
        )
        self._slider(
            watermark_box, "字号", self.watermark_size_var, 2, 16, 3, self._watermark_changed
        )

        help_box = ttk.LabelFrame(panel, text="快捷键", style="Panel.TLabelframe")
        help_box.grid(row=8, column=0, sticky="ew")
        ttk.Label(
            help_box,
            text=(
                "Ctrl+O 打开  Ctrl+S 保存\nCtrl+R 右转  Ctrl+Z 撤销编辑\n"
                "拖动预览区可直接设置裁剪范围"
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        annotation_box = ttk.LabelFrame(panel, text="标注对象", style="Panel.TLabelframe")
        annotation_box.grid(row=9, column=0, sticky="ew", pady=(10, 10))
        annotation_box.columnconfigure(1, weight=1)
        ttk.Label(annotation_box, textvariable=self.selected_annotation_var).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Label(annotation_box, text="文字").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(annotation_box, textvariable=self.annotation_text_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Label(annotation_box, text="描边色").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(annotation_box, textvariable=self.annotation_color_var, width=10).grid(
            row=2, column=1, sticky="ew", pady=4
        )
        ttk.Button(
            annotation_box,
            text="选择",
            command=lambda: self._choose_annotation_color("stroke"),
        ).grid(row=2, column=2, padx=(5, 0))
        ttk.Label(annotation_box, text="填充色").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(annotation_box, textvariable=self.annotation_fill_var, width=10).grid(
            row=3, column=1, sticky="ew", pady=4
        )
        ttk.Button(
            annotation_box,
            text="选择",
            command=lambda: self._choose_annotation_color("fill"),
        ).grid(row=3, column=2, padx=(5, 0))
        self._slider(
            annotation_box,
            "描边",
            self.annotation_stroke_var,
            1,
            30,
            4,
            lambda: None,
        )
        self._slider(
            annotation_box,
            "透明度",
            self.annotation_opacity_var,
            1,
            100,
            5,
            lambda: None,
        )
        self._slider(
            annotation_box,
            "字号",
            self.annotation_font_size_var,
            1,
            20,
            6,
            lambda: None,
        )
        self._slider(
            annotation_box,
            "效果强度",
            self.annotation_effect_size_var,
            2,
            60,
            7,
            lambda: None,
        )
        annotation_actions = ttk.Frame(annotation_box)
        annotation_actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(annotation_actions, text="应用样式", command=self.apply_annotation_style).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(
            annotation_actions, text="删除对象", command=self.delete_selected_annotation
        ).grid(row=0, column=1)

        geometry_workflow_box = ttk.LabelFrame(panel, text="透视与画布", style="Panel.TLabelframe")
        geometry_workflow_box.grid(row=10, column=0, sticky="ew", pady=(0, 10))
        geometry_workflow_box.columnconfigure(1, weight=1)
        ttk.Button(
            geometry_workflow_box,
            text="编辑四角",
            command=lambda: self._set_annotation_tool("perspective"),
        ).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Button(geometry_workflow_box, text="应用透视", command=self.apply_perspective).grid(
            row=0, column=1, sticky="ew", pady=4
        )
        for row, (label, variable) in enumerate(
            (
                ("左边距", self.canvas_left_var),
                ("上边距", self.canvas_top_var),
                ("右边距", self.canvas_right_var),
                ("下边距", self.canvas_bottom_var),
            ),
            start=1,
        ):
            ttk.Label(geometry_workflow_box, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Spinbox(
                geometry_workflow_box, from_=0, to=10000, textvariable=variable, width=9
            ).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Label(geometry_workflow_box, text="背景色").grid(row=5, column=0, sticky="w", pady=3)
        canvas_color_row = ttk.Frame(geometry_workflow_box)
        canvas_color_row.grid(row=5, column=1, sticky="ew", pady=3)
        canvas_color_row.columnconfigure(0, weight=1)
        ttk.Entry(canvas_color_row, textvariable=self.canvas_background_var).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(
            canvas_color_row,
            text="选择",
            command=self._choose_canvas_background,
        ).grid(row=0, column=1, padx=(5, 0))
        ttk.Button(
            geometry_workflow_box, text="应用画布扩展", command=self.apply_canvas_expansion
        ).grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        workflow_box = ttk.LabelFrame(panel, text="高频工作流", style="Panel.TLabelframe")
        workflow_box.grid(row=11, column=0, sticky="ew")
        ttk.Button(workflow_box, text="配置收藏与快捷键…", command=self.configure_workflow).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Checkbutton(
            workflow_box,
            text="启动时恢复最近编辑项目",
            variable=self.restore_workspace_var,
            command=self._workspace_setting_changed,
        ).grid(row=1, column=0, sticky="w", pady=(7, 0))

        look_box = ttk.Frame(
            panel,
            style="Surface.TFrame",
            padding=(8, 4, 8, 2),
        )
        look_box.columnconfigure(0, weight=1)
        self.look_strip = MaterialLookStrip(look_box, self._apply_material_look)
        self.look_strip.grid(row=0, column=0, sticky="ew")

        annotation_tools_box = ttk.LabelFrame(
            panel,
            text="标注工具",
            style="Panel.TLabelframe",
        )
        annotation_tools_box.columnconfigure((0, 1, 2), weight=1)
        self.annotation_tool_buttons: dict[str, ttk.Button] = {}
        for index, tool in enumerate(ANNOTATION_TOOL_LABELS):
            button = ttk.Button(
                annotation_tools_box,
                text=ANNOTATION_TOOL_LABELS[tool],
                style=(
                    "ToolShelfActive.TButton"
                    if tool == self.annotation_tool_var.get()
                    else "ToolShelf.TButton"
                ),
                command=partial(self._set_annotation_tool, tool),
            )
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=2, pady=2)
            self.annotation_tool_buttons[tool] = button
        ttk.Button(
            annotation_tools_box,
            text="应用裁剪",
            style="Accent.TButton",
            command=self.apply_crop,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", padx=2, pady=(8, 2))
        ttk.Button(
            annotation_tools_box,
            text="重置",
            style="Quiet.TButton",
            command=self.reset_crop,
        ).grid(row=4, column=2, sticky="ew", padx=2, pady=(8, 2))

        smart_edit_box = ttk.LabelFrame(
            panel,
            text="智能改图",
            style="Panel.TLabelframe",
        )
        smart_edit_box.columnconfigure(0, weight=1)
        ttk.Label(
            smart_edit_box,
            text="本地高清与智能去背景都会生成新修订，不会覆盖原图。",
            style="Muted.Toolbar.TLabel",
            wraplength=244,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(4, 9))
        smart_scale_actions = ttk.Frame(smart_edit_box)
        smart_scale_actions.grid(row=1, column=0, sticky="ew")
        smart_scale_actions.columnconfigure((0, 1), weight=1, uniform="smart-scale")
        self.smart_upscale_2x_button = MaterialButton(
            smart_scale_actions,
            text="2× 高清",
            width=112,
            height=38,
            variant="filled",
            command=lambda: self._start_smart_upscale(2),
        )
        self.smart_upscale_2x_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.smart_upscale_4x_button = MaterialButton(
            smart_scale_actions,
            text="4× 高清",
            width=112,
            height=38,
            command=lambda: self._start_smart_upscale(4),
        )
        self.smart_upscale_4x_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        smart_background_box = ttk.Frame(smart_edit_box)
        smart_background_box.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        smart_background_box.columnconfigure((1, 3), weight=1)
        ttk.Label(
            smart_background_box,
            text="容差",
            style="Muted.Toolbar.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.smart_background_tolerance_spinbox = ttk.Spinbox(
            smart_background_box,
            from_=0,
            to=254,
            increment=1,
            width=4,
            textvariable=self.smart_background_tolerance_var,
            justify="center",
        )
        self.smart_background_tolerance_spinbox.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(4, 10),
        )
        ttk.Label(
            smart_background_box,
            text="羽化",
            style="Muted.Toolbar.TLabel",
        ).grid(row=0, column=2, sticky="w")
        self.smart_background_feather_spinbox = ttk.Spinbox(
            smart_background_box,
            from_=1,
            to=255,
            increment=1,
            width=4,
            textvariable=self.smart_background_feather_var,
            justify="center",
        )
        self.smart_background_feather_spinbox.grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(4, 0),
        )
        self.smart_remove_background_button = MaterialButton(
            smart_edit_box,
            text="智能去背景",
            height=38,
            variant="tonal",
            command=self._start_smart_remove_background,
        )
        self.smart_remove_background_button.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(7, 0),
        )
        set_accessible_name(self.smart_background_tolerance_spinbox, "去背景容差")
        set_accessible_name(self.smart_background_feather_spinbox, "去背景边缘羽化")
        set_accessible_name(self.smart_remove_background_button, "开始智能去背景")

        smart_alpha_box = ttk.LabelFrame(
            smart_edit_box,
            text="去背景边缘细化",
            style="Panel.TLabelframe",
        )
        smart_alpha_box.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        smart_alpha_box.columnconfigure((0, 1), weight=1, uniform="smart-alpha")
        ttk.Label(
            smart_alpha_box,
            textvariable=self.smart_alpha_refinement_var,
            style="Muted.Toolbar.TLabel",
            wraplength=220,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(2, 5))
        ttk.Label(smart_alpha_box, text="画笔大小").grid(
            row=1,
            column=0,
            sticky="w",
        )
        self.smart_alpha_brush_size_spinbox = ttk.Spinbox(
            smart_alpha_box,
            from_=2,
            to=256,
            increment=2,
            width=6,
            textvariable=self.smart_alpha_brush_size_var,
            justify="center",
        )
        self.smart_alpha_brush_size_spinbox.grid(row=1, column=1, sticky="e")
        self.smart_alpha_preserve_button = MaterialButton(
            smart_alpha_box,
            text="保留主体画笔",
            height=34,
            command=lambda: self._activate_smart_alpha_brush("preserve"),
        )
        self.smart_alpha_preserve_button.grid(
            row=2,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(6, 0),
        )
        self.smart_alpha_remove_button = MaterialButton(
            smart_alpha_box,
            text="删除背景画笔",
            height=34,
            command=lambda: self._activate_smart_alpha_brush("remove"),
        )
        self.smart_alpha_remove_button.grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(4, 0),
            pady=(6, 0),
        )
        self.smart_alpha_clear_button = MaterialButton(
            smart_alpha_box,
            text="清除笔划",
            height=32,
            command=self._clear_smart_alpha_strokes,
        )
        self.smart_alpha_clear_button.grid(
            row=3,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(6, 0),
        )
        self.smart_alpha_apply_button = MaterialButton(
            smart_alpha_box,
            text="应用边缘细化",
            height=32,
            variant="tonal",
            command=self._apply_smart_alpha_refinement,
        )
        self.smart_alpha_apply_button.grid(
            row=3,
            column=1,
            sticky="ew",
            padx=(4, 0),
            pady=(6, 0),
        )
        self._smart_alpha_controls = (
            self.smart_alpha_brush_size_spinbox,
            self.smart_alpha_preserve_button,
            self.smart_alpha_remove_button,
            self.smart_alpha_clear_button,
            self.smart_alpha_apply_button,
        )
        set_accessible_name(self.smart_alpha_brush_size_spinbox, "边缘细化画笔大小")
        set_accessible_name(self.smart_alpha_preserve_button, "使用保留主体画笔")
        set_accessible_name(self.smart_alpha_remove_button, "使用删除背景画笔")
        set_accessible_name(self.smart_alpha_clear_button, "清除全部边缘细化笔划")
        set_accessible_name(self.smart_alpha_apply_button, "应用去背景边缘细化")

        smart_background_replace_box = ttk.LabelFrame(
            smart_edit_box,
            text="替换背景",
            style="Panel.TLabelframe",
        )
        smart_background_replace_box.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        smart_background_replace_box.columnconfigure(1, weight=1)
        ttk.Label(smart_background_replace_box, text="类型").grid(
            row=0,
            column=0,
            sticky="w",
            pady=3,
        )
        self.smart_background_replace_mode_box = ttk.Combobox(
            smart_background_replace_box,
            textvariable=self.smart_background_replace_mode_var,
            values=("纯色背景", "自定义图片"),
            state="readonly",
            width=12,
        )
        self.smart_background_replace_mode_box.grid(
            row=0,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        self.smart_background_replace_mode_box.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._smart_background_mode_changed(),
        )
        ttk.Label(smart_background_replace_box, text="底色").grid(
            row=1,
            column=0,
            sticky="w",
            pady=3,
        )
        self.smart_background_replace_color_entry = ttk.Entry(
            smart_background_replace_box,
            textvariable=self.smart_background_replace_color_var,
            width=10,
        )
        self.smart_background_replace_color_entry.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=3,
        )
        self.smart_background_replace_color_button = ttk.Button(
            smart_background_replace_box,
            text="选择颜色",
            command=self._choose_smart_background_color,
        )
        self.smart_background_replace_color_button.grid(
            row=1,
            column=2,
            padx=(6, 0),
            pady=3,
        )
        ttk.Label(
            smart_background_replace_box,
            textvariable=self.smart_background_replace_image_var,
            style="Muted.Toolbar.TLabel",
            wraplength=150,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 3))
        self.smart_background_replace_image_button = ttk.Button(
            smart_background_replace_box,
            text="选择背景图",
            command=self._choose_smart_background_image,
        )
        self.smart_background_replace_image_button.grid(
            row=2,
            column=2,
            padx=(6, 0),
            pady=(4, 3),
        )
        self.smart_background_replace_button = MaterialButton(
            smart_background_replace_box,
            text="应用背景",
            height=36,
            variant="tonal",
            command=self._apply_smart_background_replacement,
        )
        self.smart_background_replace_button.grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(6, 0),
        )
        self._smart_background_replace_controls = (
            self.smart_background_replace_mode_box,
            self.smart_background_replace_color_entry,
            self.smart_background_replace_color_button,
            self.smart_background_replace_image_button,
        )
        set_accessible_name(self.smart_background_replace_mode_box, "背景替换类型")
        set_accessible_name(self.smart_background_replace_color_entry, "背景替换底色")
        set_accessible_name(self.smart_background_replace_color_button, "选择背景替换底色")
        set_accessible_name(self.smart_background_replace_image_button, "选择自定义背景图片")
        set_accessible_name(self.smart_background_replace_button, "应用背景替换")
        self.smart_background_replace_button.state(("disabled",))

        smart_repair_box = ttk.LabelFrame(
            smart_edit_box,
            text="物体擦除与局部修复",
            style="Panel.TLabelframe",
        )
        smart_repair_box.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        smart_repair_box.columnconfigure(1, weight=1)
        ttk.Label(
            smart_repair_box,
            textvariable=self.smart_repair_region_var,
            style="Muted.Toolbar.TLabel",
            wraplength=220,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(2, 5))
        ttk.Label(smart_repair_box, text="边缘羽化").grid(
            row=1,
            column=0,
            sticky="w",
            pady=3,
        )
        self.smart_repair_feather_spinbox = ttk.Spinbox(
            smart_repair_box,
            from_=0,
            to=64,
            increment=1,
            textvariable=self.smart_repair_feather_var,
            justify="center",
            width=7,
        )
        self.smart_repair_feather_spinbox.grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        smart_repair_actions = ttk.Frame(smart_repair_box)
        smart_repair_actions.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        smart_repair_actions.columnconfigure((0, 1), weight=1, uniform="smart-repair")
        self.smart_repair_detect_button = MaterialButton(
            smart_repair_actions,
            text="智能识别主体区域",
            height=36,
            variant="tonal",
            command=self._detect_smart_repair_region,
        )
        self.smart_repair_detect_button.grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(0, 6),
        )
        self.smart_repair_next_button = MaterialButton(
            smart_repair_actions,
            text="下一个主体",
            height=36,
            command=self._select_next_smart_repair_suggestion,
        )
        self.smart_repair_next_button.grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(4, 0),
            pady=(0, 6),
        )
        self.smart_repair_select_button = MaterialButton(
            smart_repair_actions,
            text="框选擦除区域",
            height=36,
            command=self._select_smart_repair_region,
        )
        self.smart_repair_select_button.grid(row=1, column=0, sticky="ew", padx=(0, 4))
        self.smart_repair_apply_button = MaterialButton(
            smart_repair_actions,
            text="擦除并修复",
            height=36,
            variant="tonal",
            command=self._start_smart_region_repair,
        )
        self.smart_repair_apply_button.grid(row=1, column=1, sticky="ew", padx=(4, 0))
        ttk.Label(
            smart_repair_box,
            textvariable=self.smart_mask_refinement_var,
            style="Muted.Toolbar.TLabel",
            wraplength=220,
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(9, 3))
        ttk.Label(smart_repair_box, text="蒙版画笔").grid(
            row=4,
            column=0,
            sticky="w",
            pady=3,
        )
        self.smart_mask_brush_size_spinbox = ttk.Spinbox(
            smart_repair_box,
            from_=2,
            to=256,
            increment=1,
            textvariable=self.smart_mask_brush_size_var,
            justify="center",
            width=7,
        )
        self.smart_mask_brush_size_spinbox.grid(
            row=4,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        smart_mask_actions = ttk.Frame(smart_repair_box)
        smart_mask_actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        smart_mask_actions.columnconfigure((0, 1), weight=1, uniform="smart-mask")
        self.smart_mask_add_button = MaterialButton(
            smart_mask_actions,
            text="加入蒙版",
            height=34,
            variant="tonal",
            command=lambda: self._activate_smart_mask_brush("add"),
        )
        self.smart_mask_add_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.smart_mask_remove_button = MaterialButton(
            smart_mask_actions,
            text="移出蒙版",
            height=34,
            command=lambda: self._activate_smart_mask_brush("remove"),
        )
        self.smart_mask_remove_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.smart_mask_undo_button = MaterialButton(
            smart_mask_actions,
            text="撤销笔划",
            height=34,
            command=self._undo_smart_mask_stroke,
        )
        self.smart_mask_undo_button.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(6, 0),
        )
        self.smart_mask_reset_button = MaterialButton(
            smart_mask_actions,
            text="重置蒙版",
            height=34,
            command=self._reset_smart_mask_refinement,
        )
        self.smart_mask_reset_button.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(4, 0),
            pady=(6, 0),
        )
        self._smart_mask_controls = (
            self.smart_mask_brush_size_spinbox,
            self.smart_mask_add_button,
            self.smart_mask_remove_button,
            self.smart_mask_undo_button,
            self.smart_mask_reset_button,
        )
        set_accessible_name(self.smart_repair_feather_spinbox, "物体擦除边缘羽化")
        set_accessible_name(self.smart_repair_detect_button, "离线智能识别需要擦除的主体区域")
        set_accessible_name(self.smart_repair_next_button, "选择下一个智能主体候选")
        set_accessible_name(self.smart_repair_select_button, "在画布框选需要擦除的物体")
        set_accessible_name(self.smart_repair_apply_button, "擦除框选物体并局部修复")
        set_accessible_name(self.smart_mask_brush_size_spinbox, "主体蒙版精修画笔大小")
        set_accessible_name(self.smart_mask_add_button, "使用加入主体蒙版画笔")
        set_accessible_name(self.smart_mask_remove_button, "使用移出主体蒙版画笔")
        set_accessible_name(self.smart_mask_undo_button, "撤销上一条主体蒙版笔划")
        set_accessible_name(self.smart_mask_reset_button, "重置为自动识别主体蒙版")
        self.smart_repair_apply_button.state(("disabled",))
        for control in self._smart_mask_controls:
            control.state(("disabled",))

        smart_text_box = ttk.LabelFrame(
            smart_edit_box,
            text="替换图片文字",
            style="Panel.TLabelframe",
        )
        smart_text_box.grid(row=7, column=0, sticky="ew", pady=(12, 0))
        smart_text_box.columnconfigure(1, weight=1)
        ttk.Label(smart_text_box, text="新文字").grid(
            row=0,
            column=0,
            sticky="w",
            pady=3,
        )
        self.smart_text_entry = ttk.Entry(
            smart_text_box,
            textvariable=self.smart_text_value_var,
        )
        self.smart_text_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
        ttk.Label(smart_text_box, text="字体").grid(row=1, column=0, sticky="w", pady=3)
        self.smart_text_font_box = ttk.Combobox(
            smart_text_box,
            textvariable=self.smart_text_font_var,
            values=SMART_TEXT_FONTS,
            state="readonly",
            width=11,
        )
        self.smart_text_font_box.grid(row=1, column=1, sticky="ew", pady=3)
        self.smart_text_font_size_spinbox = ttk.Spinbox(
            smart_text_box,
            from_=8,
            to=512,
            increment=1,
            width=5,
            textvariable=self.smart_text_font_size_var,
            justify="center",
        )
        self.smart_text_font_size_spinbox.grid(
            row=1,
            column=2,
            sticky="e",
            padx=(6, 0),
            pady=3,
        )
        ttk.Label(smart_text_box, text="颜色").grid(row=2, column=0, sticky="w", pady=3)
        self.smart_text_color_entry = ttk.Entry(
            smart_text_box,
            textvariable=self.smart_text_color_var,
            width=10,
        )
        self.smart_text_color_entry.grid(row=2, column=1, sticky="ew", pady=3)
        self.smart_text_color_button = ttk.Button(
            smart_text_box,
            text="选择",
            command=self._choose_smart_text_color,
        )
        self.smart_text_color_button.grid(row=2, column=2, padx=(6, 0), pady=3)
        ttk.Label(smart_text_box, text="排版").grid(row=3, column=0, sticky="w", pady=3)
        self.smart_text_align_box = ttk.Combobox(
            smart_text_box,
            textvariable=self.smart_text_align_var,
            values=tuple(SMART_TEXT_ALIGNMENTS),
            state="readonly",
            width=7,
        )
        self.smart_text_align_box.grid(row=3, column=1, sticky="ew", pady=3)
        self.smart_text_rotation_spinbox = ttk.Spinbox(
            smart_text_box,
            from_=-180,
            to=180,
            increment=1,
            width=5,
            textvariable=self.smart_text_rotation_var,
            justify="center",
        )
        self.smart_text_rotation_spinbox.grid(
            row=3,
            column=2,
            sticky="e",
            padx=(6, 0),
            pady=3,
        )
        ttk.Label(smart_text_box, text="弧度").grid(row=4, column=0, sticky="w", pady=3)
        self.smart_text_curve_spinbox = ttk.Spinbox(
            smart_text_box,
            from_=-100,
            to=100,
            increment=5,
            textvariable=self.smart_text_curve_var,
            justify="center",
        )
        self.smart_text_curve_spinbox.grid(
            row=4,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=3,
        )
        ttk.Label(smart_text_box, text="字效").grid(row=5, column=0, sticky="w", pady=3)
        self.smart_text_effect_box = ttk.Combobox(
            smart_text_box,
            textvariable=self.smart_text_effect_var,
            values=tuple(SMART_TEXT_EFFECTS),
            state="readonly",
            width=9,
        )
        self.smart_text_effect_box.grid(row=5, column=1, sticky="ew", pady=3)
        self.smart_text_effect_size_spinbox = ttk.Spinbox(
            smart_text_box,
            from_=0,
            to=64,
            increment=1,
            width=5,
            textvariable=self.smart_text_effect_size_var,
            justify="center",
        )
        self.smart_text_effect_size_spinbox.grid(
            row=5,
            column=2,
            sticky="e",
            padx=(6, 0),
            pady=3,
        )
        ttk.Label(smart_text_box, text="辅助色").grid(row=6, column=0, sticky="w", pady=3)
        self.smart_text_effect_color_entry = ttk.Entry(
            smart_text_box,
            textvariable=self.smart_text_effect_color_var,
            width=10,
        )
        self.smart_text_effect_color_entry.grid(row=6, column=1, sticky="ew", pady=3)
        self.smart_text_effect_color_button = ttk.Button(
            smart_text_box,
            text="选择",
            command=self._choose_smart_text_effect_color,
        )
        self.smart_text_effect_color_button.grid(row=6, column=2, padx=(6, 0), pady=3)
        ttk.Label(
            smart_text_box,
            textvariable=self.smart_text_region_var,
            style="Muted.Toolbar.TLabel",
            wraplength=220,
            justify="left",
        ).grid(row=7, column=0, columnspan=3, sticky="ew", pady=(5, 3))
        smart_text_actions = ttk.Frame(smart_text_box)
        smart_text_actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        smart_text_actions.columnconfigure((0, 1), weight=1, uniform="smart-text")
        self.smart_text_select_button = MaterialButton(
            smart_text_actions,
            text="框选原文字",
            height=36,
            command=self._select_smart_text_region,
        )
        self.smart_text_select_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.smart_text_replace_button = MaterialButton(
            smart_text_actions,
            text="替换文字",
            height=36,
            variant="tonal",
            command=self._start_smart_text_replacement,
        )
        self.smart_text_replace_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.smart_text_detect_button = MaterialButton(
            smart_text_actions,
            text="自动检测文字",
            height=34,
            command=self._detect_smart_text_regions,
        )
        self.smart_text_detect_button.grid(
            row=1,
            column=0,
            sticky="ew",
            padx=(0, 4),
            pady=(6, 0),
        )
        self.smart_text_next_region_button = MaterialButton(
            smart_text_actions,
            text="下一个区域",
            height=34,
            command=self._select_next_smart_text_suggestion,
        )
        self.smart_text_next_region_button.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(4, 0),
            pady=(6, 0),
        )
        self.smart_text_recognize_button = MaterialButton(
            smart_text_actions,
            text="识别框内文字",
            height=34,
            variant="tonal",
            command=self._recognize_smart_text_region,
        )
        self.smart_text_recognize_button.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 0),
        )
        self.smart_text_font_match_button = MaterialButton(
            smart_text_actions,
            text="推荐相似字体",
            height=34,
            command=self._recommend_smart_text_font,
        )
        self.smart_text_font_match_button.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 0),
        )
        ttk.Label(
            smart_text_box,
            textvariable=self.smart_text_ocr_var,
            style="Muted.Toolbar.TLabel",
            wraplength=220,
            justify="left",
        ).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        ttk.Label(
            smart_text_box,
            textvariable=self.smart_text_font_match_var,
            style="Muted.Toolbar.TLabel",
            wraplength=220,
            justify="left",
        ).grid(row=10, column=0, columnspan=3, sticky="ew", pady=(5, 0))
        smart_text_history_actions = ttk.Frame(smart_text_box)
        smart_text_history_actions.grid(
            row=11,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(6, 0),
        )
        smart_text_history_actions.columnconfigure((0, 1), weight=1, uniform="smart-text-history")
        self.smart_text_undo_button = MaterialButton(
            smart_text_history_actions,
            text="撤销文字参数",
            height=32,
            command=self._undo_smart_text_draft,
        )
        self.smart_text_undo_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.smart_text_redo_button = MaterialButton(
            smart_text_history_actions,
            text="重做文字参数",
            height=32,
            command=self._redo_smart_text_draft,
        )
        self.smart_text_redo_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self._smart_text_input_widgets = (
            self.smart_text_entry,
            self.smart_text_font_box,
            self.smart_text_font_size_spinbox,
            self.smart_text_color_entry,
            self.smart_text_color_button,
            self.smart_text_align_box,
            self.smart_text_rotation_spinbox,
            self.smart_text_curve_spinbox,
            self.smart_text_effect_box,
            self.smart_text_effect_color_entry,
            self.smart_text_effect_color_button,
            self.smart_text_effect_size_spinbox,
        )
        set_accessible_name(self.smart_text_entry, "替换后的文字")
        set_accessible_name(self.smart_text_font_box, "替换文字字体")
        set_accessible_name(self.smart_text_font_size_spinbox, "替换文字字号")
        set_accessible_name(self.smart_text_color_entry, "替换文字颜色")
        set_accessible_name(self.smart_text_align_box, "替换文字对齐方式")
        set_accessible_name(self.smart_text_rotation_spinbox, "替换文字旋转角度")
        set_accessible_name(self.smart_text_curve_spinbox, "替换文字弧度百分比")
        set_accessible_name(self.smart_text_effect_box, "替换文字字效")
        set_accessible_name(self.smart_text_effect_color_entry, "替换文字字效辅助色")
        set_accessible_name(self.smart_text_effect_color_button, "选择替换文字字效辅助色")
        set_accessible_name(self.smart_text_effect_size_spinbox, "替换文字字效强度")
        set_accessible_name(self.smart_text_select_button, "在画布框选原文字区域")
        set_accessible_name(self.smart_text_replace_button, "修复背景并替换文字")
        set_accessible_name(self.smart_text_detect_button, "自动检测可能的文字区域")
        set_accessible_name(self.smart_text_next_region_button, "选择下一个文字区域建议")
        set_accessible_name(self.smart_text_recognize_button, "离线识别框选区域中的原文字")
        set_accessible_name(self.smart_text_font_match_button, "离线推荐框选原文字的相似字体")
        set_accessible_name(self.smart_text_undo_button, "撤销文字替换参数")
        set_accessible_name(self.smart_text_redo_button, "重做文字替换参数")
        self.smart_text_undo_button.state(("disabled",))
        self.smart_text_redo_button.state(("disabled",))
        self.smart_text_next_region_button.state(("disabled",))
        smart_result_actions = ttk.Frame(smart_edit_box)
        smart_result_actions.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        smart_result_actions.columnconfigure((0, 1), weight=1, uniform="smart-result")
        self.smart_edit_compare_button = MaterialButton(
            smart_result_actions,
            text="对比结果",
            width=112,
            height=36,
            command=self._compare_smart_result,
        )
        self.smart_edit_compare_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.smart_edit_send_button = MaterialButton(
            smart_result_actions,
            text="发送到转图",
            width=112,
            height=36,
            command=self._send_smart_result_to_conversion,
        )
        self.smart_edit_send_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        smart_cancel_actions = ttk.Frame(smart_edit_box)
        smart_cancel_actions.grid(row=9, column=0, sticky="ew", pady=(6, 0))
        smart_cancel_actions.columnconfigure((0, 1), weight=1, uniform="smart-cancel")
        self.smart_edit_cancel_button = MaterialButton(
            smart_cancel_actions,
            text="取消任务",
            width=112,
            height=34,
            command=self._cancel_smart_edit,
        )
        self.smart_edit_cancel_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.smart_edit_discard_button = MaterialButton(
            smart_cancel_actions,
            text="放弃候选",
            width=112,
            height=34,
            command=self._discard_smart_result,
        )
        self.smart_edit_discard_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Label(
            smart_edit_box,
            textvariable=self.smart_edit_status_var,
            style="InspectorTitle.TLabel",
            wraplength=244,
            justify="left",
        ).grid(row=10, column=0, sticky="ew", pady=(10, 3))
        ttk.Label(
            smart_edit_box,
            textvariable=self.smart_edit_result_var,
            style="Muted.Toolbar.TLabel",
            wraplength=244,
            justify="left",
        ).grid(row=11, column=0, sticky="ew")
        self.smart_edit_cancel_button.state(("disabled",))
        self.smart_edit_compare_button.state(("disabled",))
        self.smart_edit_send_button.state(("disabled",))
        self.smart_edit_discard_button.state(("disabled",))

        self.inspector_groups: dict[str, tuple[tk.Misc, ...]] = {
            "convert": (conversion_job_box, resize_box, export_box),
            "adjust": (
                look_box,
                adjust_box,
                tone_box,
                detail_section,
                curve_box,
            ),
            "smart": (smart_edit_box,),
            "annotation": (
                annotation_tools_box,
                annotation_box,
                geometry_box,
                watermark_box,
                geometry_workflow_box,
            ),
            "history": (analysis_box, workflow_box, help_box),
        }
        self.detail_advanced_widgets = (curve_box,)
        for widgets in self.inspector_groups.values():
            for row, widget in enumerate(widgets):
                widget.grid_configure(row=row, column=0, sticky="ew", pady=(0, 10))
        self.panel_sections = {key: widgets[0] for key, widgets in self.inspector_groups.items()}

        panel_footer = ttk.Frame(self.panel_shell, style="Surface.TFrame", padding=(4, 10, 4, 2))
        panel_footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        panel_footer.columnconfigure(0, weight=1)
        self.export_footer_button = MaterialButton(
            panel_footer,
            text="导出图像  Ctrl+E",
            width=252,
            height=44,
            variant="filled",
            command=self.quick_export,
        )
        self.export_footer_button.grid(row=0, column=0, sticky="ew")
        set_accessible_name(self.export_footer_button, "导出图像")

        self.command_dock = MaterialCommandDock(self)
        self._place_command_dock()
        dock = self.command_dock.content
        self.dock_open_button = MaterialButton(
            dock,
            text="打开",
            width=64,
            height=42,
            variant="ghost",
            text_size=11,
            command=self.open_file,
        )
        self.dock_open_button.grid(row=0, column=0, padx=(0, 2))
        self.dock_save_button = MaterialButton(
            dock,
            text="保存",
            width=48,
            command=self.save_as,
        )
        self.dock_save_button.grid(row=0, column=1, padx=2)
        self.dock_export_button = MaterialButton(
            dock,
            text="导出",
            width=48,
            command=self.quick_export,
        )
        self.dock_export_button.grid(row=0, column=2, padx=(2, 8))
        self.dock_primary_separator = ttk.Separator(dock, orient="vertical")
        self.dock_primary_separator.grid(row=0, column=3, sticky="ns", padx=4)
        self.dock_undo_button = MaterialButton(
            dock,
            text="↶",
            width=38,
            height=38,
            variant="icon",
            command=self.undo,
        )
        self.dock_undo_button.grid(row=0, column=4, padx=1)
        set_accessible_name(self.dock_undo_button, "撤销")
        self.dock_redo_button = MaterialButton(
            dock,
            text="↷",
            width=38,
            height=38,
            variant="icon",
            command=self.redo,
        )
        self.dock_redo_button.grid(row=0, column=5, padx=1)
        set_accessible_name(self.dock_redo_button, "重做")
        self.dock_crop_button = MaterialButton(
            dock,
            text="⌗",
            width=38,
            height=38,
            variant="icon",
            command=self._enter_convert_crop,
        )
        self.dock_crop_button.set_selected(True)
        self.dock_crop_button.grid(row=0, column=6, padx=1)
        set_accessible_name(self.dock_crop_button, "裁剪")
        self.dock_rotate_button = MaterialButton(
            dock,
            text="↻",
            width=42,
            height=42,
            variant="icon",
            icon_size=16,
            command=self._rotate_from_dock,
        )
        self.dock_rotate_button.grid(row=0, column=9, padx=1)
        set_accessible_name(self.dock_rotate_button, "向右旋转")
        self.dock_favorite_button = MaterialButton(
            dock,
            text="☆",
            width=42,
            height=42,
            variant="icon",
            icon_size=18,
            command=self.toggle_viewer_favorite,
        )
        self.dock_favorite_button.grid(row=0, column=10, padx=1)
        set_accessible_name(self.dock_favorite_button, "收藏当前图片（F）")
        self.dock_markup_button = MaterialButton(
            dock,
            text="✎",
            width=42,
            height=42,
            variant="icon",
            icon_size=17,
            command=self.toggle_viewer_markup,
        )
        self.dock_markup_button.grid(row=0, column=11, padx=1)
        set_accessible_name(self.dock_markup_button, "轻标注：矩形框与指向箭头")
        self.dock_compare_button = MaterialButton(
            dock,
            text="◐",
            width=38,
            height=38,
            variant="icon",
            command=self._toggle_dock_compare,
        )
        self.dock_compare_button.grid(row=0, column=8, padx=(1, 4))
        set_accessible_name(self.dock_compare_button, "对比原图")
        self._dock_compare_trace = self.compare_original.trace_add(
            "write",
            lambda *_args: self.dock_compare_button.set_selected(self.compare_original.get()),
        )
        self.dock_split_button = MaterialButton(
            dock,
            text="分屏",
            width=52,
            height=38,
            command=self._toggle_dock_split_compare,
        )
        self.dock_split_button.grid(row=0, column=9, padx=(1, 4))
        set_accessible_name(self.dock_split_button, "分屏对比")
        self._dock_split_trace = self.split_compare_var.trace_add(
            "write",
            lambda *_args: self.dock_split_button.set_selected(self.split_compare_var.get()),
        )
        self.dock_edit_separator = ttk.Separator(dock, orient="vertical")
        self.dock_edit_separator.grid(row=0, column=12, sticky="ns", padx=4)
        self.dock_fullscreen_button = MaterialButton(
            dock,
            text="⛶",
            width=42,
            height=42,
            variant="icon",
            icon_size=16,
            icon_name="overlapping_rectangles",
            command=self.toggle_fullscreen,
        )
        self.dock_fullscreen_button.grid(row=0, column=13, padx=1)
        set_accessible_name(self.dock_fullscreen_button, "全屏")
        self.dock_menu_button = MaterialButton(
            dock,
            text="更多",
            width=62,
            variant="filled",
            command=self._show_feature_palette,
        )
        self.dock_menu_button.grid(row=0, column=14, padx=(4, 1))
        set_accessible_name(self.dock_menu_button, "打开看图更多功能")

        self.dock_previous_button = MaterialButton(
            dock,
            text="‹",
            width=38,
            height=38,
            variant="icon",
            command=lambda: self.navigate_folder(-1),
        )
        self.dock_previous_button.grid(row=0, column=1, padx=1)
        set_accessible_name(self.dock_previous_button, "上一张图片")
        self.dock_next_button = MaterialButton(
            dock,
            text="›",
            width=38,
            height=38,
            variant="icon",
            command=lambda: self.navigate_folder(1),
        )
        self.dock_next_button.grid(row=0, column=2, padx=1)
        set_accessible_name(self.dock_next_button, "下一张图片")
        self.dock_zoom_out_button = MaterialButton(
            dock,
            text="−",
            width=42,
            height=42,
            variant="icon",
            icon_size=16,
            command=lambda: self.step_zoom(-1),
        )
        self.dock_zoom_out_button.grid(row=0, column=4, padx=(7, 1))
        set_accessible_name(self.dock_zoom_out_button, "缩小图片")
        self.dock_zoom_percent_button = MaterialButton(
            dock,
            text="100%",
            width=64,
            height=42,
            text_size=11,
            command=self._begin_inline_zoom_edit,
        )
        self.dock_zoom_percent_button.grid(row=0, column=5, padx=1)
        set_accessible_name(self.dock_zoom_percent_button, "当前缩放比例，点击输入任意比例")
        self.viewer_zoom_input_var = tk.StringVar(value="100")
        self.dock_zoom_percent_entry = tk.Entry(
            dock,
            textvariable=self.viewer_zoom_input_var,
            width=6,
            justify="center",
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            font=("Microsoft YaHei UI", 10),
        )
        self.dock_zoom_percent_entry.bind("<Return>", self._commit_inline_zoom_edit)
        self.dock_zoom_percent_entry.bind("<KP_Enter>", self._commit_inline_zoom_edit)
        self.dock_zoom_percent_entry.bind("<Escape>", self._cancel_inline_zoom_edit)
        self.dock_zoom_percent_entry.bind("<FocusOut>", self._commit_inline_zoom_edit)
        set_accessible_name(self.dock_zoom_percent_entry, "输入缩放比例，范围 10 到 800")
        self._viewer_zoom_editing = False
        self.dock_fit_button = MaterialButton(
            dock,
            text="适窗",
            width=52,
            height=42,
            variant="icon",
            icon_size=13,
            command=self.reset_view,
        )
        self.dock_fit_button.grid(row=0, column=7, padx=1)
        set_accessible_name(self.dock_fit_button, "快速适合当前窗口")
        self.dock_actual_size_button = MaterialButton(
            dock,
            text="100%",
            width=56,
            height=42,
            variant="icon",
            icon_size=13,
            command=lambda: self._set_zoom_percent(100),
        )
        self.dock_actual_size_button.grid(row=0, column=8, padx=1)
        set_accessible_name(self.dock_actual_size_button, "立即恢复到 100% 缩放")
        self.dock_zoom_in_button = MaterialButton(
            dock,
            text="＋",
            width=42,
            height=42,
            variant="icon",
            icon_size=16,
            command=lambda: self.step_zoom(1),
        )
        self.dock_zoom_in_button.grid(row=0, column=6, padx=1)
        set_accessible_name(self.dock_zoom_in_button, "放大图片")
        for viewer_toolbar_button in (
            self.dock_open_button,
            self.dock_zoom_out_button,
            self.dock_zoom_percent_button,
            self.dock_zoom_in_button,
            self.dock_fit_button,
            self.dock_actual_size_button,
            self.dock_rotate_button,
            self.dock_favorite_button,
            self.dock_markup_button,
            self.dock_fullscreen_button,
        ):
            viewer_toolbar_button.material_role = "viewer-toolbar"

        self._dock_widgets = (
            self.dock_open_button,
            self.dock_save_button,
            self.dock_export_button,
            self.dock_primary_separator,
            self.dock_undo_button,
            self.dock_redo_button,
            self.dock_crop_button,
            self.dock_rotate_button,
            self.dock_compare_button,
            self.dock_split_button,
            self.dock_edit_separator,
            self.dock_fullscreen_button,
            self.dock_menu_button,
            self.dock_previous_button,
            self.dock_next_button,
            self.dock_zoom_out_button,
            self.dock_zoom_percent_button,
            self.dock_zoom_in_button,
            self.dock_fit_button,
            self.dock_actual_size_button,
            self.dock_favorite_button,
            self.dock_markup_button,
        )
        self._dock_widgets_by_mode = {
            ModeId.VIEWER: (
                self.dock_open_button,
                self.dock_primary_separator,
                self.dock_zoom_out_button,
                self.dock_zoom_percent_button,
                self.dock_zoom_in_button,
                self.dock_fit_button,
                self.dock_actual_size_button,
                self.dock_rotate_button,
                self.dock_favorite_button,
                self.dock_markup_button,
                self.dock_edit_separator,
                self.dock_fullscreen_button,
            ),
            ModeId.CONVERT: (
                self.dock_open_button,
                self.dock_export_button,
                self.dock_primary_separator,
                self.dock_crop_button,
                self.dock_rotate_button,
                self.dock_menu_button,
            ),
            ModeId.EDIT: (
                self.dock_open_button,
                self.dock_save_button,
                self.dock_export_button,
                self.dock_primary_separator,
                self.dock_undo_button,
                self.dock_redo_button,
                self.dock_compare_button,
                self.dock_fullscreen_button,
                self.dock_menu_button,
            ),
            ModeId.GENERATE: (),
        }

        self.zoom_preset_bar = MaterialFloatingBar(self, max_width=540)
        self.zoom_preset_buttons: dict[str, MaterialButton] = {}
        self._rebuild_zoom_preset_buttons()
        self.zoom_preset_bar.place_forget()

        self.viewer_markup_bar = MaterialFloatingBar(canvas_frame, max_width=500)
        markup_content = self.viewer_markup_bar.content
        self.viewer_markup_tool_buttons: dict[str, MaterialButton] = {}
        for column, (tool, label, accessible_name) in enumerate(
            (
                ("rectangle", "▭", "绘制矩形框"),
                ("arrow", "↗", "绘制指向箭头"),
            )
        ):
            button = MaterialButton(
                markup_content,
                text=label,
                width=42,
                height=34,
                variant="icon",
                icon_size=17,
                command=partial(self._set_viewer_markup_tool, tool),
            )
            button.grid(row=0, column=column, padx=1)
            button.set_selected(tool == self._viewer_markup_tool)
            set_accessible_name(button, accessible_name)
            self.viewer_markup_tool_buttons[tool] = button
        self.viewer_markup_color_button = MaterialButton(
            markup_content,
            text="颜色",
            width=54,
            height=34,
            command=lambda: self._choose_annotation_color("stroke"),
        )
        self.viewer_markup_color_button.grid(row=0, column=2, padx=(5, 1))
        set_accessible_name(self.viewer_markup_color_button, "选择标注颜色")
        self.viewer_markup_undo_button = MaterialButton(
            markup_content,
            text="↶",
            width=38,
            height=34,
            variant="icon",
            icon_size=16,
            command=self._undo_viewer_markup,
        )
        self.viewer_markup_undo_button.grid(row=0, column=3, padx=1)
        set_accessible_name(self.viewer_markup_undo_button, "撤销上一处标注")
        self.viewer_markup_copy_button = MaterialButton(
            markup_content,
            text="仅复制",
            width=64,
            height=34,
            command=self._finish_viewer_markup_copy,
        )
        self.viewer_markup_copy_button.grid(row=0, column=4, padx=(5, 1))
        set_accessible_name(self.viewer_markup_copy_button, "复制标注结果且不修改原图")
        self.viewer_markup_replace_button = MaterialButton(
            markup_content,
            text="替换原图",
            width=78,
            height=34,
            variant="tonal",
            command=self._finish_viewer_markup_replace,
        )
        self.viewer_markup_replace_button.grid(row=0, column=5, padx=1)
        set_accessible_name(self.viewer_markup_replace_button, "确认后用标注结果替换原图")
        self.viewer_markup_cancel_button = MaterialButton(
            markup_content,
            text="取消修改",
            width=78,
            height=34,
            command=self._cancel_viewer_markup,
        )
        self.viewer_markup_cancel_button.grid(row=0, column=6, padx=1)
        set_accessible_name(self.viewer_markup_cancel_button, "取消本次全部轻标注")
        self.viewer_markup_bar.place_forget()

        self.status_label = ttk.Label(
            canvas_frame,
            textvariable=self.status_var,
            style="CanvasMeta.TLabel",
            anchor="w",
            padding=(0, 0),
        )
        self.viewer_file_details = ttk.Label(
            canvas_frame,
            textvariable=self.viewer_footer_var,
            style="ViewerStatus.TLabel",
            anchor="w",
            padding=(0, 0),
        )
        self.viewer_gallery_button = MaterialButton(
            canvas_frame,
            text="▦ 图库",
            width=92,
            height=34,
            variant="tonal",
            command=self.toggle_thumbnail_sidebar_from_shortcut,
        )
        set_accessible_name(self.viewer_gallery_button, "预览当前文件夹的全部图片")
        self.viewer_settings_button = MaterialButton(
            canvas_frame,
            text="⚙",
            width=38,
            height=38,
            variant="icon",
            icon_size=17,
            command=self.open_viewer_settings,
        )
        set_accessible_name(self.viewer_settings_button, "看图设置")
        self.viewer_topmost_button = MaterialButton(
            canvas_frame,
            text="",
            width=38,
            height=38,
            variant="icon",
            icon_size=18,
            icon_name="pin",
            command=self.toggle_viewer_topmost,
        )
        self.viewer_topmost_button.material_role = "emphasized-toggle"
        self.viewer_topmost_button.inactive_foreground = "#737b86"
        self.viewer_topmost_button.selected_fill = None
        self.viewer_topmost_button.selected_foreground = "#ffffff"
        self.viewer_topmost_button.selected_outline = None
        set_accessible_name(self.viewer_topmost_button, "窗口置顶（P）")
        self.viewer_topmost_button.set_selected(self.viewer_topmost_var.get())
        self.thumbnail_filmstrip = ThumbnailFilmstrip(
            canvas_frame,
            self._activate_thumbnail,
        )
        self.thumbnail_filmstrip.place_forget()
        self.viewer_gallery_scope_bar = MaterialFloatingBar(canvas_frame, max_width=250)
        gallery_scope_content = self.viewer_gallery_scope_bar.content
        self.viewer_gallery_folder_button = MaterialButton(
            gallery_scope_content,
            text="当前文件夹",
            width=104,
            height=32,
            variant="ghost",
            text_size=9,
            command=lambda: self._set_viewer_gallery_scope("folder"),
        )
        self.viewer_gallery_folder_button.grid(row=0, column=0, padx=(0, 2))
        self.viewer_gallery_favorites_button = MaterialButton(
            gallery_scope_content,
            text="我的收藏",
            width=92,
            height=32,
            variant="ghost",
            text_size=9,
            command=lambda: self._set_viewer_gallery_scope("favorites"),
        )
        self.viewer_gallery_favorites_button.grid(row=0, column=1, padx=(2, 0))
        for gallery_scope_button in (
            self.viewer_gallery_folder_button,
            self.viewer_gallery_favorites_button,
        ):
            gallery_scope_button.material_role = "viewer-toolbar"
        set_accessible_name(self.viewer_gallery_folder_button, "显示当前文件夹图片")
        set_accessible_name(self.viewer_gallery_favorites_button, "显示我的收藏图片")
        self.viewer_gallery_scope_bar.place_forget()
        self._sync_viewer_gallery_scope_buttons()
        self._viewer_navigation_visible = False
        self._viewer_navigation_geometry = (104, 152, 42)
        self._viewer_navigation_hover: int | None = None
        self._viewer_navigation_pressed: int | None = None
        self._viewer_navigation_tooltip_job: str | None = None
        self._viewer_navigation_fade_job: str | None = None
        self._viewer_navigation_hide_job: str | None = None
        self._viewer_navigation_opacity = 1.0
        self._viewer_navigation_photos: dict[int, ImageTk.PhotoImage] = {}
        self._viewer_navigation_photo_cache: dict[
            tuple[int, int, str, int], ImageTk.PhotoImage
        ] = {}
        self._update_viewer_gallery_button()
        self._place_canvas_status()
        if not self.edit_panel_visible_var.get():
            self.panel_shell.grid_remove()
        if not self.thumbnail_sidebar_visible_var.get():
            self.thumbnail_sidebar.grid_remove()
        self._show_inspector_tab("adjust", sync_mode=False)
        self._validate_feature_callbacks()
        self._mode_unsubscribe = self.mode_router.subscribe(self._on_mode_transition)
        self._apply_mode_layout(self.mode_router.active_mode, announce=False)
        self.after_idle(self._show_empty_state)

    def _activate_mode(self, mode: ModeId) -> None:
        self.mode_router.switch(mode)

    def _restore_task_tray_history(self) -> None:
        restored: list[UnifiedTaskItem] = []
        invalid = 0
        for payload in self.task_tray_settings[:50]:
            try:
                item = UnifiedTaskItem.from_json(payload)
            except (TypeError, ValueError):
                invalid += 1
                continue
            if not item.status.terminal:
                item = replace(
                    item,
                    status=UnifiedTaskStatus.UNKNOWN,
                    detail=f"上次退出时任务尚未结束 · {item.detail}",
                    cancellable=False,
                    retryable=False,
                )
            restored.append(item)
        self.task_tray.set_items(restored, notify=False)
        if invalid:
            self._schedule_config_save()

    def _task_tray_changed(self, items: tuple[UnifiedTaskItem, ...]) -> None:
        self.task_tray_settings = [item.to_json() for item in items]
        self._schedule_config_save()

    @staticmethod
    def _unified_status_from_job(status: JobStatus) -> UnifiedTaskStatus:
        return {
            JobStatus.SUBMITTING: UnifiedTaskStatus.PENDING,
            JobStatus.QUEUED: UnifiedTaskStatus.PENDING,
            JobStatus.RUNNING: UnifiedTaskStatus.RUNNING,
            JobStatus.SUCCEEDED: UnifiedTaskStatus.SUCCEEDED,
            JobStatus.FAILED: UnifiedTaskStatus.FAILED,
            JobStatus.CANCELLED: UnifiedTaskStatus.CANCELLED,
            JobStatus.UNKNOWN: UnifiedTaskStatus.UNKNOWN,
        }[status]

    def _sync_ai_task_to_tray(
        self,
        snapshot: ManagedJobSnapshot,
        *,
        title: str | None = None,
        detail: str | None = None,
    ) -> None:
        kind = (
            UnifiedTaskKind.GENERATION
            if snapshot.request_kind is JobRequestKind.GENERATION
            else UnifiedTaskKind.SMART_EDIT
        )
        mode = (
            ModeId.GENERATE if snapshot.request_kind is JobRequestKind.GENERATION else ModeId.EDIT
        )
        previous = self.task_tray.get(snapshot.job_id)
        if title is None:
            title = (
                previous.title
                if previous is not None
                else "图片生成"
                if kind is UnifiedTaskKind.GENERATION
                else "智能改图"
            )
        if detail is None:
            if snapshot.error is not None:
                detail = snapshot.error.message
            elif snapshot.status is JobStatus.SUCCEEDED:
                detail = f"已完成 · {len(snapshot.outputs)} 个输出"
            else:
                detail = {
                    JobStatus.SUBMITTING: "正在安全提交",
                    JobStatus.QUEUED: "等待 Provider 执行",
                    JobStatus.RUNNING: "Provider 正在处理",
                    JobStatus.CANCELLED: "任务已取消，没有自动重试",
                    JobStatus.UNKNOWN: "状态未知，没有自动重试",
                    JobStatus.FAILED: "任务失败",
                    JobStatus.SUCCEEDED: "任务完成",
                }[snapshot.status]
        unified_status = self._unified_status_from_job(snapshot.status)
        retryable = bool(
            kind is UnifiedTaskKind.GENERATION
            and unified_status in {UnifiedTaskStatus.FAILED, UnifiedTaskStatus.UNKNOWN}
            and snapshot.job_id == self._mock_generation_job_id
            and self._last_generation_request is not None
        )
        self.task_tray.upsert(
            UnifiedTaskItem(
                task_id=snapshot.job_id,
                kind=kind,
                title=title,
                status=unified_status,
                mode=mode,
                detail=detail,
                cancellable=not unified_status.terminal,
                retryable=retryable,
                created_at=snapshot.created_at,
                updated_at=snapshot.updated_at,
            )
        )

    def _sync_conversion_task_to_tray(
        self,
        session: ConversionSession,
        *,
        active: bool,
        detail: str | None = None,
        status_override: UnifiedTaskStatus | None = None,
    ) -> None:
        task_id = self._conversion_tray_task_id
        if task_id is None:
            task_id = f"conversion:{self._conversion_generation}"
            self._conversion_tray_task_id = task_id
        total = len(session.tasks)
        succeeded = sum(task.status is ConversionTaskStatus.SUCCEEDED for task in session.tasks)
        failed = sum(task.status is ConversionTaskStatus.FAILED for task in session.tasks)
        cancelled = sum(task.status is ConversionTaskStatus.CANCELLED for task in session.tasks)
        finished = succeeded + failed + cancelled
        unfinished = total - finished
        if status_override is not None:
            status = status_override
        elif active:
            status = UnifiedTaskStatus.RUNNING
        elif unfinished:
            status = UnifiedTaskStatus.UNKNOWN
        elif failed:
            status = UnifiedTaskStatus.FAILED
        elif cancelled:
            status = UnifiedTaskStatus.CANCELLED
        else:
            status = UnifiedTaskStatus.SUCCEEDED
        if detail is None:
            detail = (
                f"共 {total} 项 · 成功 {succeeded} · 失败 {failed} · "
                f"取消 {cancelled} · 待处理 {unfinished}"
            )
        self.task_tray.upsert(
            UnifiedTaskItem(
                task_id=task_id,
                kind=UnifiedTaskKind.CONVERSION,
                title=f"批量转换 {total} 项",
                status=status,
                mode=ModeId.CONVERT,
                detail=detail,
                progress_current=finished,
                progress_total=max(1, total),
                cancellable=active,
                retryable=(not active and not unfinished and bool(failed or cancelled)),
            )
        )

    def _activate_unified_task(self, item: UnifiedTaskItem) -> None:
        self.mode_router.switch(item.mode)
        if item.kind is UnifiedTaskKind.SMART_EDIT:
            self._show_inspector_tab("smart")
        elif item.kind is UnifiedTaskKind.CONVERSION:
            self.conversion_workspace.set_task_filter("全部")
        self.status_var.set(f"已前往任务：{item.title}")

    def _cancel_unified_task(self, item: UnifiedTaskItem) -> None:
        if item.kind is UnifiedTaskKind.CONVERSION:
            if item.task_id == self._conversion_tray_task_id:
                self._cancel_conversion_queue()
            return
        if item.kind is UnifiedTaskKind.GENERATION and item.task_id == self._mock_generation_job_id:
            self._cancel_mock_generation()
            return
        if item.kind is UnifiedTaskKind.SMART_EDIT and item.task_id == self._smart_edit_job_id:
            self._cancel_smart_edit()
            return
        try:
            snapshot = self.ai_job_manager.cancel(item.task_id)
        except Exception:
            self.status_var.set("任务记录已失效，无法取消")
            return
        self._sync_ai_task_to_tray(snapshot)

    def _retry_unified_task(self, item: UnifiedTaskItem) -> None:
        if item.kind is UnifiedTaskKind.GENERATION and item.task_id == self._mock_generation_job_id:
            self._retry_failed_generation()
            return
        if item.kind is UnifiedTaskKind.CONVERSION and self._conversion_session is not None:
            retryable = tuple(
                task
                for task in self._conversion_session.tasks
                if task.status in {ConversionTaskStatus.FAILED, ConversionTaskStatus.CANCELLED}
            )
            self._retry_conversion_tasks(retryable)
            return
        self.status_var.set("该任务缺少可安全复用的原始请求，不能自动重试")

    def _show_task_tray(self) -> None:
        self.task_tray.show()

    def _restore_ai_job_summary(self) -> None:
        try:
            jobs = self.ai_job_manager.list_jobs()
        except Exception:
            return
        edit_jobs = tuple(job for job in jobs if job.request_kind is JobRequestKind.EDIT)
        generation_jobs = tuple(
            job for job in jobs if job.request_kind is JobRequestKind.GENERATION
        )
        edit_unknown = sum(job.status is JobStatus.UNKNOWN for job in edit_jobs)
        edit_succeeded = sum(job.status is JobStatus.SUCCEEDED for job in edit_jobs)
        generation_unknown = sum(job.status is JobStatus.UNKNOWN for job in generation_jobs)
        generation_succeeded = sum(job.status is JobStatus.SUCCEEDED for job in generation_jobs)
        for job in jobs:
            self._sync_ai_task_to_tray(job)
        if self._ai_job_recovery_error is not None:
            self.smart_edit_status_var.set(
                "旧智能任务记录已损坏并隔离；基础看图、转图和本地编辑不受影响"
            )
            self.generate_status_var.set("旧生成任务记录已损坏并隔离，未自动重试")
            return
        if edit_unknown:
            self.smart_edit_status_var.set(
                f"恢复到 {edit_unknown} 个状态未知的改图任务；为避免重复执行，未自动重试"
            )
        elif edit_succeeded:
            self.smart_edit_status_var.set(f"已恢复 {edit_succeeded} 条完成的改图任务记录")
        if edit_jobs:
            self.smart_edit_result_var.set(
                f"本地任务记录：{len(edit_jobs)} 条 · 完成 {edit_succeeded} · 未知 {edit_unknown}"
            )
        if generation_unknown:
            self.generate_status_var.set(
                f"恢复到 {generation_unknown} 个状态未知的生成任务；未自动提交或重试"
            )
        elif generation_succeeded:
            self.generate_status_var.set(f"已恢复 {generation_succeeded} 条完成的生成任务记录")

    def _feature_callbacks(self) -> dict[str, Callable[[], object]]:
        show_menu = self._show_app_menu
        return {
            "activate_pan": self._show_pan_hint,
            "actual_size": lambda: self.set_zoom("100%"),
            "animation_next": lambda: self._step_animation(1),
            "animation_previous": lambda: self._step_animation(-1),
            "animation_toggle": self._toggle_animation_playback,
            "apply_canvas_expansion": self.apply_canvas_expansion,
            "apply_crop": self.apply_crop,
            "apply_perspective": self.apply_perspective,
            "auto_straighten": self.auto_straighten,
            "batch_process": self.batch_process,
            "batch_process_folder": self.batch_process_folder,
            "cancel_mock_generation": self._cancel_mock_generation,
            "close": self.close,
            "compare_original": self._toggle_dock_compare,
            "configure_slideshow": self.configure_slideshow,
            "configure_workflow": self.configure_workflow,
            "conversion_add_files": self.conversion_workspace.request_files,
            "conversion_add_folder": self.conversion_workspace.request_folder,
            "conversion_queue_cancel": self._cancel_conversion_queue,
            "conversion_queue_run": self._start_conversion_queue,
            "conversion_recipe_export": self._export_conversion_recipe,
            "conversion_recipe_import": self._import_conversion_recipe,
            "copy_current": self.copy_current,
            "copy_result": self.copy_result,
            "delete_current_to_recycle_bin": self.delete_current_to_recycle_bin,
            "enter_crop_tool": self._enter_convert_crop,
            "export_current_animation": self.export_current_animation,
            "export_diagnostic_report": self.export_diagnostic_report,
            "export_settings": lambda: self._show_inspector_tab("convert"),
            "fine_rotation_changed": self._fine_rotation_changed,
            "flip_horizontal": self.flip_horizontal,
            "flip_vertical": self.flip_vertical,
            "move_current": self.move_current,
            "navigate_first": lambda: self.navigate_folder_to("first"),
            "navigate_last": lambda: self.navigate_folder_to("last"),
            "navigate_next": lambda: self.navigate_folder(1),
            "navigate_previous": lambda: self.navigate_folder(-1),
            "open_directory": self.open_directory,
            "open_file": self.open_file,
            "open_project_dialog": self.open_project_dialog,
            "open_recent_directory": show_menu,
            "open_recent_export_directory": show_menu,
            "open_recent_file": show_menu,
            "open_recent_project": show_menu,
            "open_recycle_bin": open_recycle_bin,
            "open_settings": self.open_settings,
            "paste_from_clipboard": self.paste_from_clipboard,
            "quick_export": self.quick_export,
            "redo": self.redo,
            "rename_current": self.rename_current,
            "reset_crop": self.reset_crop,
            "reset_edits": self.reset_edits,
            "reset_view": self.reset_view,
            "resize_settings": lambda: self._show_inspector_tab("convert"),
            "reveal_current": self.reveal_current,
            "rotate_left": lambda: self.rotate(-90),
            "rotate_right": self._rotate_from_dock,
            "save_as": self.save_as,
            "save_project_dialog": self.save_project_dialog,
            "select_annotation_tool": partial(self._enter_edit_annotation_tool, "select"),
            "select_arrow_tool": partial(self._enter_edit_annotation_tool, "arrow"),
            "select_blur_tool": partial(self._enter_edit_annotation_tool, "blur"),
            "select_ellipse_tool": partial(self._enter_edit_annotation_tool, "ellipse"),
            "select_freehand_tool": partial(self._enter_edit_annotation_tool, "freehand"),
            "select_line_tool": partial(self._enter_edit_annotation_tool, "line"),
            "select_mosaic_tool": partial(self._enter_edit_annotation_tool, "mosaic"),
            "select_number_tool": partial(self._enter_edit_annotation_tool, "number"),
            "select_perspective_tool": partial(
                self._enter_edit_annotation_tool,
                "perspective",
            ),
            "select_rectangle_tool": partial(self._enter_edit_annotation_tool, "rectangle"),
            "select_redact_tool": partial(self._enter_edit_annotation_tool, "redact"),
            "select_text_tool": partial(self._enter_edit_annotation_tool, "text"),
            "show_adjustments": lambda: self._show_inspector_tab("adjust"),
            "show_annotations": lambda: self._show_inspector_tab("annotation"),
            "show_generate_workspace": partial(self._activate_mode, ModeId.GENERATE),
            "choose_generation_reference": self._choose_generation_reference,
            "show_history": lambda: self._show_inspector_tab("history"),
            "show_smart_edit": lambda: self._show_inspector_tab("smart"),
            "show_onboarding": self.show_onboarding,
            "show_properties": self.show_properties,
            "show_shortcut_help": self.show_shortcut_help,
            "show_feature_palette": self._show_feature_palette,
            "show_task_tray": self._show_task_tray,
            "start_mock_generation": self._start_mock_generation,
            "generate_open_candidate": lambda: self._send_generated_candidate_to_mode(
                ModeId.VIEWER
            ),
            "generate_send_to_edit": lambda: self._send_generated_candidate_to_mode(ModeId.EDIT),
            "generate_send_to_convert": lambda: self._send_generated_candidate_to_mode(
                ModeId.CONVERT
            ),
            "retry_failed_generation": self._retry_failed_generation,
            "generate_candidate_variation": self._generate_selected_candidate_variation,
            "generate_toggle_candidate_favorite": self._toggle_generated_candidate_favorite,
            "generate_remove_candidate": self._remove_generated_candidate,
            "generate_toggle_favorites_filter": self._toggle_generation_favorites_filter,
            "smart_compare_result": self._compare_smart_result,
            "smart_discard_result": self._discard_smart_result,
            "smart_alpha_preserve": partial(
                self._activate_smart_alpha_brush,
                "preserve",
            ),
            "smart_alpha_remove": partial(
                self._activate_smart_alpha_brush,
                "remove",
            ),
            "smart_alpha_clear": self._clear_smart_alpha_strokes,
            "smart_alpha_apply": self._apply_smart_alpha_refinement,
            "smart_choose_background_color": self._choose_smart_background_color,
            "smart_choose_background_image": self._choose_smart_background_image,
            "smart_apply_background": self._apply_smart_background_replacement,
            "smart_detect_repair_region": self._detect_smart_repair_region,
            "smart_next_repair_region": self._select_next_smart_repair_suggestion,
            "smart_mask_add": partial(self._activate_smart_mask_brush, "add"),
            "smart_mask_remove": partial(self._activate_smart_mask_brush, "remove"),
            "smart_mask_undo": self._undo_smart_mask_stroke,
            "smart_mask_reset": self._reset_smart_mask_refinement,
            "smart_select_repair_region": self._select_smart_repair_region,
            "smart_apply_region_repair": self._start_smart_region_repair,
            "smart_detect_text_regions": self._detect_smart_text_regions,
            "smart_next_text_region": self._select_next_smart_text_suggestion,
            "smart_recognize_text": self._recognize_smart_text_region,
            "smart_recommend_font": self._recommend_smart_text_font,
            "smart_remove_background": self._start_smart_remove_background,
            "smart_send_to_convert": self._send_smart_result_to_conversion,
            "smart_replace_text": self._start_smart_text_replacement,
            "smart_upscale_2x": lambda: self._start_smart_upscale(2),
            "smart_upscale_4x": lambda: self._start_smart_upscale(4),
            "toggle_edit_panel": self.toggle_edit_panel,
            "toggle_fullscreen": self.toggle_fullscreen,
            "toggle_immersive": self.toggle_immersive,
            "toggle_file_comparison": self._toggle_file_comparison,
            "toggle_slideshow": self.toggle_slideshow,
            "toggle_split_compare": self._toggle_dock_split_compare,
            "toggle_thumbnail_sidebar": self.toggle_thumbnail_sidebar_from_shortcut,
            "undo": self.undo,
            "update_watermark": self._watermark_changed,
            "zoom_150": lambda: self.set_zoom("150%"),
            "zoom_200": lambda: self.set_zoom("200%"),
            "zoom_50": lambda: self.set_zoom("50%"),
        }

    def _validate_feature_callbacks(self) -> None:
        commands = self._feature_callbacks()
        missing = sorted(
            feature.command
            for feature in self.feature_registry
            if feature.command is not None and feature.command not in commands
        )
        if missing:
            raise RuntimeError("功能注册表存在未绑定命令：" + "、".join(missing))

    def resolve_feature_callback(self, feature_id: str) -> Callable[[], object]:
        feature = self.feature_registry[feature_id]
        if feature.command is None:
            raise KeyError(f"功能没有命令：{feature_id}")
        return self._feature_callbacks()[feature.command]

    def invoke_feature(self, feature_id: str) -> object:
        feature = self.feature_registry[feature_id]
        if feature.mode is not None and feature.mode is not self.mode_router.active_mode:
            self.mode_router.switch(feature.mode)
        return self.resolve_feature_callback(feature_id)()

    def _enter_convert_crop(self) -> None:
        self.mode_router.switch(ModeId.CONVERT)
        self._set_annotation_tool("crop")
        self.status_var.set("裁剪：在图片上拖动框选，然后点击“应用裁剪”或按 Enter")
        self._update_crop_action_bar()

    def _crop_confirmation_available(self) -> bool:
        return (
            self.original is not None
            and self.annotation_tool_var.get() == "crop"
            and self.mode_router.active_mode in {ModeId.CONVERT, ModeId.EDIT}
            and not self._smart_repair_selecting_region
            and not self._smart_text_selecting_region
            and not self._smart_text_adjusting_region
            and not self._side_by_side_comparison_active()
            and not self._is_immersive
        )

    def _update_crop_action_bar(self) -> None:
        if not hasattr(self, "crop_action_bar"):
            return
        if not self._crop_confirmation_available():
            self.crop_action_bar.place_forget()
            return
        if self.crop_box is None:
            self.crop_action_hint_var.set("拖动图片创建裁剪框")
            self.crop_action_apply_button.state(("disabled",))
        else:
            left, top, right, bottom = self.crop_box
            self.crop_action_hint_var.set(f"已选择 {right - left} × {bottom - top}")
            self.crop_action_apply_button.state(("!disabled",))
        self.crop_action_bar.place(
            in_=self.canvas_frame,
            relx=0.5,
            rely=1.0,
            y=-78,
            anchor="s",
        )
        self.crop_action_bar.tk.call("raise", self.crop_action_bar._w)

    def _cancel_crop_interaction(self) -> None:
        self.crop_box = None
        self.drag_start = None
        self.crop_drag_start_box = None
        self.crop_action = "new"
        if self.document is not None and self.mode_router.active_mode is not ModeId.CONVERT:
            self.document.update_state("crop_cancel", crop_box=None)
        if self.mode_router.active_mode is ModeId.CONVERT:
            self.crop_var.set(
                "配方中已有裁切"
                if self.conversion_draft_history.current.crop_box is not None
                else "未设置裁剪"
            )
        else:
            self.crop_var.set("未设置裁剪")
        self._set_annotation_tool("select")
        self._draw_crop()
        self.status_var.set("已取消本次裁剪；已应用的裁剪保持不变")
        self._refresh_preview()

    def _enter_edit_annotation_tool(self, tool: str) -> None:
        self._show_inspector_tab("annotation")
        self._set_annotation_tool(tool)

    def _rotate_from_dock(self) -> None:
        self.rotate(90)

    def _on_mode_transition(self, transition: ModeTransition) -> None:
        if transition.mode is not ModeId.EDIT and self._smart_alpha_brush_active:
            self._smart_alpha_brush_active = False
            self._smart_alpha_current_points.clear()
            self.canvas.delete("smart-alpha-brush")
            self.canvas.configure(cursor="arrow")
        if transition.mode is not ModeId.EDIT and self._smart_mask_brush_active:
            self._smart_mask_brush_active = False
            self._smart_mask_current_points.clear()
            self.canvas.delete("smart-mask-brush")
            self.canvas.configure(cursor="arrow")
        if transition.changed_mode or transition.reason == "snapshot":
            self._apply_mode_layout(transition.mode)

    def _apply_mode_layout(self, mode: ModeId, *, announce: bool = True) -> None:
        resume_animation = False
        if mode is not ModeId.VIEWER and self._animation_playing:
            self._animation_resume_on_viewer = True
            self._animation_playing = False
            self._cancel_animation_timer()
        elif (
            mode is ModeId.VIEWER
            and self._animation_resume_on_viewer
            and self._animation_sequence is not None
            and not self.file_compare_var.get()
        ):
            self._animation_resume_on_viewer = False
            self._animation_playing = True
            self._animation_index = (
                self._animation_displayed_index + 1
            ) % self._animation_sequence.frame_count
            resume_animation = True
        for candidate, button in self.mode_buttons.items():
            button.set_selected(candidate is mode)
        self.mode_hint_var.set(MODE_HINTS[mode])
        # The current product surface is intentionally a single-task viewer.
        # Other workspaces remain implemented, but their switcher is not part
        # of the default viewing shell.
        self.mode_bar.grid_remove()
        self.canvas_frame.configure(
            padding=(0, 48, 0, 0) if mode is ModeId.VIEWER else (0, 0, 0, 66)
        )
        self.canvas.configure(highlightthickness=0 if mode is ModeId.VIEWER else 1)
        if mode is ModeId.CONVERT:
            self._apply_conversion_draft_to_controls()
        elif mode is ModeId.EDIT and self.document is not None:
            self._apply_document_state(self.document.state)
            self._sync_controls_from_timeline()
        self.generate_workspace.grid_remove()
        if mode is ModeId.VIEWER:
            self.viewer_mode_actions.grid()
        else:
            self.viewer_mode_actions.grid_remove()
        self.favorite_toolbar.grid_remove()
        self._update_command_dock_for_mode(mode)
        self._update_viewer_gallery_button()

        sidebar_titles = {
            ModeId.VIEWER: "资源库",
            ModeId.CONVERT: "转换队列",
            ModeId.EDIT: "资源与版本",
            ModeId.GENERATE: "参考资源",
        }
        self.thumbnail_sidebar.title_var.set(sidebar_titles[mode])

        show_side_area = (
            mode is not ModeId.VIEWER
            and (mode is ModeId.CONVERT or self.thumbnail_sidebar_visible_var.get())
        ) and not self._is_immersive
        self.columnconfigure(0, minsize=220 if show_side_area else 0)
        self.columnconfigure(
            2,
            minsize=296 if mode in {ModeId.CONVERT, ModeId.EDIT} else 0,
        )
        if mode is ModeId.CONVERT:
            self.thumbnail_sidebar.grid_remove()
            if show_side_area:
                self.conversion_workspace.grid()
            else:
                self.conversion_workspace.grid_remove()
            self._enqueue_current_conversion_handoff()
        else:
            self.conversion_workspace.grid_remove()
            if show_side_area:
                self.thumbnail_sidebar.grid()
                self.thumbnail_sidebar.select(self.navigation.current)
            else:
                self.thumbnail_sidebar.grid_remove()

        if mode is ModeId.VIEWER:
            self.panel_shell.grid_remove()
        elif mode is ModeId.CONVERT:
            self.panel_shell.grid()
            self._show_inspector_tab("convert", sync_mode=False)
        elif mode is ModeId.EDIT:
            self.panel_shell.grid()
            stored_tab = str(self.mode_router.state_for(ModeId.EDIT).get("inspector_tab", "adjust"))
            if stored_tab not in {"adjust", "smart", "annotation", "history"}:
                stored_tab = "adjust"
            self._show_inspector_tab(stored_tab, sync_mode=False)
        else:
            self.panel_shell.grid_remove()
            self.generate_workspace.grid()
            self.generate_workspace.tkraise()

        if mode in {ModeId.VIEWER, ModeId.GENERATE}:
            self.canvas_tools.place_forget()
            if mode is ModeId.GENERATE:
                self.status_label.place_forget()
                self.canvas_metadata.place_forget()
                self.viewer_file_details.place_forget()
                self.viewer_gallery_button.place_forget()
                self.viewer_gallery_scope_bar.place_forget()
                self.thumbnail_filmstrip.place_forget()
                self._hide_viewer_navigation()
            elif not self._is_immersive:
                self._place_canvas_status()
        elif not self._is_immersive:
            self.canvas_tools.place(x=14, y=14, anchor="nw")
            self._place_canvas_status()
        self._update_animation_controls()

        if announce:
            self.status_var.set(f"已切换到{MODE_LABELS[mode]}模式")
        if resume_animation:
            self._show_animation_frame()
        else:
            self._schedule_preview()
        self._update_crop_action_bar()

    def _update_command_dock_for_mode(self, mode: ModeId) -> None:
        """Expose only the commands needed for the active workflow."""

        if mode is not ModeId.VIEWER and hasattr(self, "_viewer_zoom_editing"):
            self._viewer_zoom_editing = False
            self.dock_zoom_percent_entry.grid_remove()
        visible = set(self._dock_widgets_by_mode[mode])
        for widget in self._dock_widgets:
            if widget in visible:
                widget.grid()
            else:
                widget.grid_remove()
        if (
            mode is ModeId.VIEWER
            and getattr(self, "_viewer_zoom_editing", False)
            and hasattr(self, "dock_zoom_percent_entry")
        ):
            self.dock_zoom_percent_button.grid_remove()
            self.dock_zoom_percent_entry.grid(row=0, column=5, padx=1, ipady=6)
        elif hasattr(self, "dock_zoom_percent_entry"):
            self.dock_zoom_percent_entry.grid_remove()
        viewer_toolbar_shown = (
            self.viewer_toolbar_visible_var.get()
            and not self._viewer_clean_mode
            and (self.viewer_toolbar_mode_var.get() == "fixed" or self._viewer_toolbar_auto_visible)
        )
        if (
            mode is ModeId.GENERATE
            or self._is_immersive
            or (mode is ModeId.VIEWER and not viewer_toolbar_shown)
        ):
            self._hide_zoom_preset_popup()
            self.command_dock.place_forget()
            return
        if mode is not ModeId.VIEWER:
            self._hide_zoom_preset_popup()
        if mode is ModeId.VIEWER:
            if self.viewer_toolbar_mode_var.get() == "fixed":
                self._viewer_toolbar_opacity = 1.0
                self._set_viewer_toolbar_visual_opacity(1.0)
            self.command_dock.set_profile("top")
            self.command_dock.update_idletasks()
            toolbar_width = min(
                560,
                max(420, self.command_dock.content.winfo_reqwidth() + 28),
            )
            titlebar_height = (
                self.window_titlebar.winfo_reqheight() if self.window_titlebar is not None else 0
            )
            self.command_dock.place(
                in_=self,
                relx=0.5,
                rely=0.0,
                x=0,
                y=(titlebar_height + 6 - round((1.0 - self._viewer_toolbar_opacity) * 18)),
                anchor="n",
                relwidth=0.0,
                relheight=0.0,
                width=toolbar_width,
                height=56,
            )
            self.command_dock._layout()
            self.command_dock.tk.call("raise", self.command_dock._w)
            return
        self.command_dock.set_profile("capsule")
        widths = {
            ModeId.CONVERT: 420,
            ModeId.EDIT: 590,
        }
        self.command_dock.place(
            in_=self,
            relx=0.5,
            rely=1.0,
            y=-12,
            anchor="s",
            width=widths[mode],
            height=64,
        )
        self.command_dock._layout()

    def _toggle_zoom_preset_popup(self) -> None:
        if self._zoom_preset_visible:
            self._hide_zoom_preset_popup()
            return
        if self.mode_router.active_mode is not ModeId.VIEWER or self._is_immersive:
            return
        current = self.zoom_mode.get()
        for value, button in self.zoom_preset_buttons.items():
            button.set_selected(value == current)
        top = self.command_dock.winfo_y() + self.command_dock.winfo_height() + 8
        self.zoom_preset_bar.place(
            relx=0.5,
            y=top,
            anchor="n",
            width=526,
            height=52,
        )
        self.zoom_preset_bar._layout()
        self.zoom_preset_bar.tk.call("raise", self.zoom_preset_bar._w)
        self._zoom_preset_visible = True

    def _hide_zoom_preset_popup(self) -> None:
        if not hasattr(self, "zoom_preset_bar"):
            return
        self.zoom_preset_bar.place_forget()
        self._zoom_preset_visible = False

    def _select_zoom_preset(self, value: str) -> None:
        self._hide_zoom_preset_popup()
        if value == "适应":
            self.reset_view()
        else:
            self.set_zoom(value)

    def _choose_smart_text_color(self) -> None:
        selected = colorchooser.askcolor(
            self.smart_text_color_var.get(),
            title="选择替换文字颜色",
            parent=self,
        )[1]
        if selected:
            self.smart_text_color_var.set(selected.lower())

    def _choose_smart_text_effect_color(self) -> None:
        selected = colorchooser.askcolor(
            self.smart_text_effect_color_var.get(),
            title="选择文字字效辅助色",
            parent=self,
        )[1]
        if selected:
            self.smart_text_effect_color_var.set(selected.lower())

    def _detect_smart_text_regions(self) -> None:
        if self.document is None or self._current_asset_revision is None:
            self.smart_edit_status_var.set("请先打开一张图片并等待资源登记完成")
            return
        future = self._smart_text_detection_future
        if future is not None and not future.done():
            self.smart_edit_status_var.set("正在分析文字区域，请稍候")
            return
        try:
            source_path = self.asset_repository.resolve_content(
                self._current_asset_revision.revision_id
            )
        except Exception:
            self.smart_edit_status_var.set("当前图片无法用于离线文字检测")
            return
        self._smart_text_detection_generation += 1
        generation = self._smart_text_detection_generation
        self._smart_text_suggestions = ()
        self._smart_text_suggestion_index = -1
        self._smart_text_perspective_quad = None
        self.smart_text_curve_var.set(0)
        self.smart_text_effect_var.set("普通")
        self.smart_text_region_var.set("区域：正在离线分析可能的文字行…")
        self.smart_edit_status_var.set("正在后台检测文字区域；不会上传图片")
        self._smart_text_detection_future = self._analysis_executor.submit(
            detect_text_regions,
            source_path,
        )
        self._update_smart_edit_buttons()
        self.after(30, lambda: self._poll_smart_text_detection(generation))

    def _poll_smart_text_detection(self, generation: int) -> None:
        if generation != self._smart_text_detection_generation:
            return
        future = self._smart_text_detection_future
        if future is None:
            return
        if not future.done():
            self.after(40, lambda: self._poll_smart_text_detection(generation))
            return
        self._smart_text_detection_future = None
        try:
            suggestions = future.result()
        except Exception:
            self._smart_text_suggestions = ()
            self._smart_text_suggestion_index = -1
            self.smart_text_region_var.set("区域：自动检测失败，请手工框选")
            self.smart_edit_status_var.set("离线检测失败；手工框选和原有编辑功能不受影响")
            self._update_smart_edit_buttons()
            return
        self._smart_text_suggestions = suggestions
        self._smart_text_suggestion_index = -1
        if not suggestions:
            self.smart_text_region_var.set("区域：未发现明显文字，请手工框选")
            self.smart_edit_status_var.set("未发现明显文字区域；可继续手工框选")
            self._update_smart_edit_buttons()
            return
        self._select_next_smart_text_suggestion()

    def _select_next_smart_text_suggestion(self) -> None:
        suggestions = self._smart_text_suggestions
        if not suggestions or self.original is None:
            self.smart_edit_status_var.set("当前没有可切换的文字区域建议")
            return
        self._smart_text_suggestion_index = (self._smart_text_suggestion_index + 1) % len(
            suggestions
        )
        suggestion = suggestions[self._smart_text_suggestion_index]
        left, top, right, bottom = suggestion.region
        width, height = self.original.size
        box = (
            max(0, min(width - 1, math.floor(left * width))),
            max(0, min(height - 1, math.floor(top * height))),
            max(1, min(width, math.ceil(right * width))),
            max(1, min(height, math.ceil(bottom * height))),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            self.smart_edit_status_var.set("文字区域建议无效，请手工框选")
            return
        self._smart_text_region_box = box
        self._smart_text_perspective_quad = suggestion.perspective_quad
        self.crop_box = box
        self._smart_text_selecting_region = False
        self.smart_text_rotation_var.set(suggestion.rotation)
        self.smart_text_curve_var.set(round(suggestion.curve * 100, 6))
        effect_label = SMART_TEXT_EFFECT_LABELS[suggestion.visual_effect]
        previous_effect = SMART_TEXT_EFFECTS.get(self.smart_text_effect_var.get(), "plain")
        self.smart_text_effect_var.set(effect_label)
        if suggestion.estimated_fill_color is not None:
            self.smart_text_color_var.set(suggestion.estimated_fill_color)
        if suggestion.estimated_effect_color is not None:
            self.smart_text_effect_color_var.set(suggestion.estimated_effect_color)
        elif previous_effect != suggestion.visual_effect:
            self.smart_text_effect_color_var.set(
                SMART_TEXT_EFFECT_DEFAULT_COLORS[suggestion.visual_effect]
            )
        if suggestion.estimated_effect_size is not None:
            self.smart_text_effect_size_var.set(suggestion.estimated_effect_size)
        elif suggestion.visual_effect == "gradient":
            self.smart_text_effect_size_var.set(0)
        if suggestion.visual_effect in {"outline", "shadow", "glow"}:
            try:
                effect_size = int(self.smart_text_effect_size_var.get())
            except (TypeError, ValueError, tk.TclError):
                effect_size = 0
            if effect_size < 1:
                self.smart_text_effect_size_var.set(4)
        self._update_smart_text_region_label()
        self._draw_crop()
        confidence = round(suggestion.confidence * 100)
        if suggestion.perspective_quad is not None:
            perspective_planes = tuple(
                item for item in suggestions if item.perspective_quad is not None
            )
            if len(perspective_planes) > 1:
                plane_index = perspective_planes.index(suggestion) + 1
                angle_label = (
                    f"透视文字平面 {plane_index}/{len(perspective_planes)}"
                    f" · 方向 {suggestion.rotation:+.0f}°"
                )
            else:
                angle_label = f"透视文字 · 方向 {suggestion.rotation:+.0f}°"
        elif abs(suggestion.curve) >= 0.05:
            curve_direction = "上拱" if suggestion.curve > 0 else "下弯"
            angle_label = (
                f"弧形文字 {curve_direction} {suggestion.curve * 100:+.0f}%"
                f" · 方向 {suggestion.rotation:+.0f}°"
            )
        elif abs(suggestion.rotation) < 0.05:
            angle_label = "水平文字"
        elif abs(suggestion.rotation) >= 74.5:
            angle_label = f"竖排文字方向 {suggestion.rotation:+.0f}°"
        else:
            angle_label = f"检测角度 {suggestion.rotation:+.0f}°"
        visual_effect_label = {
            "plain": "",
            "outline": "描边文字",
            "shadow": "投影文字",
            "gradient": "渐变填充文字",
            "glow": "发光文字",
        }[suggestion.visual_effect]
        visual_effect_suffix = (
            f" · {visual_effect_label}（已估计主色、辅助色和强度，可校正）"
            if visual_effect_label
            else ""
        )
        color_estimate_suffix = (
            f" · 估计文字色 {suggestion.estimated_fill_color}"
            if not visual_effect_label and suggestion.estimated_fill_color is not None
            else ""
        )
        self.smart_edit_status_var.set(
            f"文字区域建议 {self._smart_text_suggestion_index + 1}/{len(suggestions)}"
            f" · {angle_label}{visual_effect_suffix}{color_estimate_suffix}"
            f" · 可信度 {confidence}% · 可拖动边框校正"
        )
        self._update_smart_edit_buttons()

    def _recognize_smart_text_region(self) -> None:
        revision = self._current_asset_revision
        if self.document is None or revision is None:
            self.smart_edit_status_var.set("请先打开一张图片并等待资源登记完成")
            return
        if not self.text_ocr_backend.available:
            self.smart_text_ocr_var.set("框内文字识别：本机不可用")
            self.smart_edit_status_var.set(
                f"{self.text_ocr_backend.unavailable_reason}；可继续手工输入新文字"
            )
            return
        future = self._smart_text_ocr_future
        if future is not None and not future.done():
            self.smart_edit_status_var.set("正在识别框内文字，请稍候")
            return
        region = self._smart_text_region_box or self.crop_box
        if region is None:
            self.smart_edit_status_var.set("请先自动检测或手工框选原文字区域")
            return
        width = revision.width or (self.original.width if self.original is not None else 0)
        height = revision.height or (self.original.height if self.original is not None else 0)
        if width < 1 or height < 1:
            self.smart_edit_status_var.set("当前图片尺寸无效，无法识别文字")
            return
        left, top, right, bottom = region
        left = max(0, min(width - 1, left))
        top = max(0, min(height - 1, top))
        right = max(left + 1, min(width, right))
        bottom = max(top + 1, min(height, bottom))
        normalized = (left / width, top / height, right / width, bottom / height)
        try:
            rotation = float(self.smart_text_rotation_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.smart_edit_status_var.set("旋转角度必须是数字，无法识别文字")
            return
        if not -180 <= rotation <= 180:
            self.smart_edit_status_var.set("旋转角度必须在 -180 到 180 度之间")
            return
        try:
            curve_percent = float(self.smart_text_curve_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.smart_edit_status_var.set("弧度必须是数字，无法识别文字")
            return
        if not math.isfinite(curve_percent) or not -100 <= curve_percent <= 100:
            self.smart_edit_status_var.set("弧度必须在 -100% 到 100% 之间")
            return
        if self._smart_text_perspective_quad is not None and abs(curve_percent) >= 0.05:
            self.smart_edit_status_var.set("透视四角与弧形文字校正不能同时使用")
            return
        try:
            source_path = self.asset_repository.resolve_content(revision.revision_id)
        except Exception:
            self.smart_edit_status_var.set("当前图片无法用于离线文字识别")
            return
        self._smart_text_ocr_generation += 1
        generation = self._smart_text_ocr_generation
        curve = curve_percent / 100
        self.smart_text_ocr_var.set("框内文字识别：正在本地处理…")
        self.smart_edit_status_var.set(
            "正在本地校直弧形文字并识别；图片不会上传"
            if abs(curve) >= 0.05
            else "正在后台识别框内文字；图片不会上传"
        )
        if self._smart_text_perspective_quad is not None:
            self._smart_text_ocr_future = self._analysis_executor.submit(
                self.text_ocr_backend.recognize,
                source_path,
                region=normalized,
                rotation=rotation,
                quadrilateral=self._smart_text_perspective_quad,
            )
        elif abs(curve) >= 0.05:
            self._smart_text_ocr_future = self._analysis_executor.submit(
                self.text_ocr_backend.recognize,
                source_path,
                region=normalized,
                rotation=rotation,
                curve=curve,
            )
        else:
            self._smart_text_ocr_future = self._analysis_executor.submit(
                self.text_ocr_backend.recognize,
                source_path,
                region=normalized,
                rotation=rotation,
            )
        self._update_smart_edit_buttons()
        self.after(30, lambda: self._poll_smart_text_ocr(generation))

    def _poll_smart_text_ocr(self, generation: int) -> None:
        if generation != self._smart_text_ocr_generation:
            return
        future = self._smart_text_ocr_future
        if future is None:
            return
        if not future.done():
            self.after(40, lambda: self._poll_smart_text_ocr(generation))
            return
        self._smart_text_ocr_future = None
        try:
            result = future.result()
        except TextOcrUnavailableError as error:
            self.smart_text_ocr_var.set("框内文字识别：本机不可用")
            self.smart_edit_status_var.set(f"{error}；可继续手工输入新文字")
            self._update_smart_edit_buttons()
            return
        except Exception:
            self.smart_text_ocr_var.set("框内文字识别：识别失败")
            self.smart_edit_status_var.set("离线文字识别失败；手工输入和原有编辑功能不受影响")
            self._update_smart_edit_buttons()
            return
        if result is None:
            self.smart_text_ocr_var.set("框内文字识别：未发现清晰文字")
            self.smart_edit_status_var.set("框内未识别到文字；可调整区域或继续手工输入")
            self._update_smart_edit_buttons()
            return
        recognized = result.text.strip()
        truncated = len(recognized) > 500
        if truncated:
            recognized = recognized[:500]
        self.smart_text_value_var.set(recognized)
        suffix = " · 已截取前 500 字" if truncated else ""
        confidence_label = (
            "系统未提供可信度"
            if result.confidence is None
            else f"可信度 {round(result.confidence * 100)}%"
        )
        enhancement_label = (
            " · 已增强低对比文字" if result.preprocessing == "adaptive-contrast" else ""
        )
        geometry_label = (
            " · 已校直弧形文字"
            if result.geometry_correction == "curve"
            else " · 已校正透视文字"
            if result.geometry_correction == "perspective"
            else ""
        )
        self.smart_text_ocr_var.set(
            f"识别结果：{result.language_label} · {confidence_label}"
            f"{geometry_label}{enhancement_label}{suffix}"
        )
        self.smart_edit_status_var.set(
            "弧形文字已校直并填入新文字输入框；请校对后执行替换"
            if result.geometry_correction == "curve"
            else "识别内容已填入新文字输入框；请校对后执行替换"
        )
        self._update_smart_edit_buttons()

    def _recommend_smart_text_font(self) -> None:
        source_revision = self._smart_edit_ready_source()
        if source_revision is None:
            return
        future = self._smart_font_match_future
        if future is not None and not future.done():
            self.smart_edit_status_var.set("字体推荐正在本地分析，请稍候")
            return
        region = self._smart_text_region_box or self.crop_box
        if region is None:
            self.smart_edit_status_var.set("请先框选需要比较字体的原文字区域")
            return
        text = self.smart_text_value_var.get().strip()
        if not text or len(text) > 200:
            self.smart_edit_status_var.set("字体推荐文字必须包含 1 到 200 个字符")
            return
        width = source_revision.width or (self.original.width if self.original is not None else 0)
        height = source_revision.height or (
            self.original.height if self.original is not None else 0
        )
        if width < 1 or height < 1:
            self.smart_edit_status_var.set("当前图片尺寸无法用于字体推荐")
            return
        left, top, right, bottom = region
        left = max(0, min(width - 1, left))
        top = max(0, min(height - 1, top))
        right = max(left + 1, min(width, right))
        bottom = max(top + 1, min(height, bottom))
        if right - left < 8 or bottom - top < 8:
            self.smart_edit_status_var.set("字体推荐区域过小，请重新框选")
            return
        try:
            rotation = float(self.smart_text_rotation_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.smart_edit_status_var.set("字体推荐旋转角度必须是数字")
            return
        try:
            source_path = self.asset_repository.resolve_content(source_revision.revision_id)
        except Exception:
            self.smart_edit_status_var.set("当前图片无法用于离线字体推荐")
            return
        normalized_region = (
            left / width,
            top / height,
            right / width,
            bottom / height,
        )
        self._smart_text_region_box = (left, top, right, bottom)
        self._update_smart_text_region_label()
        self._smart_font_match_generation += 1
        generation = self._smart_font_match_generation
        self.smart_text_font_match_var.set("字体推荐：正在本地比较候选字体…")
        self.smart_edit_status_var.set("正在后台比较文字轮廓；图片不会上传")
        self._smart_font_match_future = self._analysis_executor.submit(
            match_fonts,
            source_path,
            region=normalized_region,
            text=text,
            rotation=rotation,
            maximum_results=3,
        )
        self._update_smart_edit_buttons()
        self.after(30, lambda: self._poll_smart_font_match(generation))

    def _poll_smart_font_match(self, generation: int) -> None:
        if generation != self._smart_font_match_generation:
            return
        future = self._smart_font_match_future
        if future is None:
            return
        if not future.done():
            self.after(40, lambda: self._poll_smart_font_match(generation))
            return
        self._smart_font_match_future = None
        try:
            suggestions = future.result()
        except FontMatchError as error:
            self.smart_text_font_match_var.set("字体推荐：未得到可靠结果")
            self.smart_edit_status_var.set(f"{error}；可继续手工选择字体")
            self._update_smart_edit_buttons()
            return
        except Exception:
            self.smart_text_font_match_var.set("字体推荐：本地比较失败")
            self.smart_edit_status_var.set("字体推荐失败；手工排版和其他编辑功能不受影响")
            self._update_smart_edit_buttons()
            return
        best = suggestions[0]
        self.smart_text_font_var.set(best.font_family)
        summary = " · ".join(
            f"{item.font_family} {round(item.score * 100)}%" for item in suggestions
        )
        self.smart_text_font_match_var.set(f"字体推荐：{summary}")
        self.smart_edit_status_var.set(f"已选用最相似候选“{best.font_family}”；请预览并按需调整")
        self._update_smart_edit_buttons()

    def _reset_smart_edit_for_new_document(self) -> None:
        self._smart_background_compose_generation += 1
        if self._smart_background_compose_after is not None:
            try:
                self.after_cancel(self._smart_background_compose_after)
            except tk.TclError:
                pass
            self._smart_background_compose_after = None
        background_future = self._smart_background_compose_future
        if background_future is not None and not background_future.done():
            background_future.cancel()
        self._smart_background_compose_future = None
        self._smart_background_compose_source_revision_id = None
        self._smart_background_alpha_revision = None
        self._smart_text_detection_generation += 1
        detection_future = self._smart_text_detection_future
        if detection_future is not None and not detection_future.done():
            detection_future.cancel()
        self._smart_text_detection_future = None
        self._smart_text_suggestions = ()
        self._smart_text_suggestion_index = -1
        self._smart_text_ocr_generation += 1
        ocr_future = self._smart_text_ocr_future
        if ocr_future is not None and not ocr_future.done():
            ocr_future.cancel()
        self._smart_text_ocr_future = None
        self._smart_selection_generation += 1
        selection_future = self._smart_selection_future
        if selection_future is not None and not selection_future.done():
            selection_future.cancel()
        self._smart_selection_future = None
        self._smart_selection_suggestions = ()
        self._smart_selection_index = -1
        self._clear_smart_selection_mask()
        self._smart_font_match_generation += 1
        font_match_future = self._smart_font_match_future
        if font_match_future is not None and not font_match_future.done():
            font_match_future.cancel()
        self._smart_font_match_future = None
        self._smart_alpha_brush_active = False
        self._smart_alpha_current_points.clear()
        self._smart_alpha_strokes.clear()
        self.smart_alpha_refinement_var.set("边缘画笔：等待去背景结果")
        self.canvas.delete("smart-alpha-brush")
        if self._smart_edit_after is not None:
            try:
                self.after_cancel(self._smart_edit_after)
            except tk.TclError:
                pass
            self._smart_edit_after = None
        if self._smart_edit_job_id is not None:
            try:
                snapshot = self.ai_job_manager.get_job(self._smart_edit_job_id)
                if not snapshot.status.terminal:
                    self.ai_job_manager.cancel(self._smart_edit_job_id)
            except Exception:
                pass
        self._smart_edit_job_id = None
        self._smart_edit_import_future = None
        self._smart_edit_presented_job_id = None
        self._smart_edit_source_revision_id = None
        self._smart_edit_result_revision = None
        self._smart_edit_operation = None
        self._smart_repair_region_box = None
        self._smart_repair_selecting_region = False
        self._smart_text_region_box = None
        self._smart_text_selecting_region = False
        self._smart_text_adjusting_region = False
        self._smart_text_perspective_quad = None
        self.smart_text_effect_var.set("普通")
        self.smart_text_effect_color_var.set("#ffffff")
        self.smart_text_effect_size_var.set(4)
        self.smart_text_history.reset()
        self.smart_edit_status_var.set("请选择本地高清、智能去背景或文字替换")
        self.smart_edit_result_var.set("结果将作为当前图片的新修订保存")
        self.smart_text_ocr_var.set(
            f"框内文字识别：{self.text_ocr_backend.display_name}可用"
            if self.text_ocr_backend.available
            else "框内文字识别：未安装，可继续手工输入"
        )
        self.smart_text_font_match_var.set("字体推荐：框选文字并填写或识别原文字后可使用")
        self._update_smart_text_region_label()
        self._update_smart_repair_region_label()
        self._update_smart_edit_buttons()

    def _detect_smart_repair_region(self) -> None:
        source_revision = self._smart_edit_ready_source()
        if source_revision is None:
            return
        future = self._smart_selection_future
        if future is not None and not future.done():
            self.smart_edit_status_var.set("智能选区正在本地分析，请稍候")
            return
        try:
            source_path = self.asset_repository.resolve_content(source_revision.revision_id)
        except Exception:
            self.smart_edit_status_var.set("当前图片无法用于离线智能选区")
            return
        self._smart_selection_generation += 1
        generation = self._smart_selection_generation
        self._smart_repair_selecting_region = False
        self._smart_selection_suggestions = ()
        self._smart_selection_index = -1
        self._clear_smart_selection_mask()
        self.smart_repair_region_var.set("区域：正在离线识别主要主体…")
        self.smart_edit_status_var.set("正在后台分析显著区域；图片不会上传")
        self._smart_selection_future = self._analysis_executor.submit(
            detect_smart_selection,
            source_path,
        )
        self._update_smart_edit_buttons()
        self.after(30, lambda: self._poll_smart_repair_region(generation))

    def _poll_smart_repair_region(self, generation: int) -> None:
        if generation != self._smart_selection_generation:
            return
        future = self._smart_selection_future
        if future is None:
            return
        if not future.done():
            self.after(40, lambda: self._poll_smart_repair_region(generation))
            return
        self._smart_selection_future = None
        try:
            suggestions = future.result()
        except SmartSelectionError as error:
            self._smart_repair_region_box = None
            self._smart_selection_suggestions = ()
            self._smart_selection_index = -1
            self._clear_smart_selection_mask()
            self._update_smart_repair_region_label()
            self.smart_edit_status_var.set(f"{error}；请继续手工框选")
            self._update_smart_edit_buttons()
            return
        except Exception:
            self._smart_repair_region_box = None
            self._smart_selection_suggestions = ()
            self._smart_selection_index = -1
            self._clear_smart_selection_mask()
            self._update_smart_repair_region_label()
            self.smart_edit_status_var.set("智能选区失败；手工框选和其他编辑功能不受影响")
            self._update_smart_edit_buttons()
            return
        if not suggestions:
            self._smart_repair_region_box = None
            self._smart_selection_suggestions = ()
            self._smart_selection_index = -1
            self._clear_smart_selection_mask()
            self._update_smart_repair_region_label()
            self.smart_edit_status_var.set("未发现可靠主体，请手工框选需要擦除的区域")
            self._update_smart_edit_buttons()
            return
        self._smart_selection_suggestions = suggestions
        self._smart_selection_index = -1
        self._select_next_smart_repair_suggestion()

    def _select_next_smart_repair_suggestion(self) -> None:
        suggestions = self._smart_selection_suggestions
        if not suggestions:
            self.smart_edit_status_var.set("尚无智能主体候选，请先执行识别")
            self._update_smart_edit_buttons()
            return
        source_revision = self._smart_edit_ready_source()
        if source_revision is None:
            return
        width = source_revision.width or (self.original.width if self.original is not None else 0)
        height = source_revision.height or (
            self.original.height if self.original is not None else 0
        )
        if width < 1 or height < 1:
            self.smart_edit_status_var.set("当前图片尺寸无法用于智能选区")
            self._update_smart_edit_buttons()
            return
        self._smart_selection_index = (self._smart_selection_index + 1) % len(suggestions)
        suggestion = suggestions[self._smart_selection_index]
        self._reset_smart_mask_refinement(announce=False, redraw=False)
        left = max(0, min(width - 1, round(suggestion.region[0] * width)))
        top = max(0, min(height - 1, round(suggestion.region[1] * height)))
        right = max(left + 1, min(width, round(suggestion.region[2] * width)))
        bottom = max(top + 1, min(height, round(suggestion.region[3] * height)))
        box = (left, top, right, bottom)
        if self.file_compare_var.get():
            self._disable_file_comparison(announce=False)
        self._smart_text_selecting_region = False
        self._smart_text_perspective_quad = None
        self._smart_repair_selecting_region = True
        self._smart_repair_region_box = box
        self._smart_selection_mask_box = box
        self.crop_box = box
        if self.document is not None:
            self.document.update_state("smart_repair_auto_region", crop_box=box)
        self._set_annotation_tool("crop")
        self._draw_smart_selection_mask()
        self._draw_crop()
        confidence = round(suggestion.confidence * 100)
        source_label = (
            "颜色分离"
            if suggestion.selection_kind == "color_split"
            else (
                "形状分离"
                if suggestion.selection_kind == "shape_split"
                else ("透明轮廓" if suggestion.source == "alpha" else "背景对比")
            )
        )
        self.smart_repair_region_var.set(
            f"区域：{right - left} × {bottom - top} · "
            f"候选 {self._smart_selection_index + 1}/{len(suggestions)} · "
            f"{source_label} {confidence}%"
        )
        self.smart_edit_status_var.set(
            "像素蒙版已显示；可切换候选，也可拖动边框校正（校正后改用矩形修复）"
        )
        self._update_smart_edit_buttons()

    def _select_smart_repair_region(self) -> None:
        if self.document is None or self.document.source_path is None:
            self.smart_edit_status_var.set("请先打开一张图片")
            return
        if self.file_compare_var.get():
            self._disable_file_comparison(announce=False)
        self._smart_selection_suggestions = ()
        self._smart_selection_index = -1
        self._clear_smart_selection_mask()
        self._smart_text_selecting_region = False
        self._smart_text_perspective_quad = None
        self._smart_repair_selecting_region = True
        self._smart_repair_region_box = None
        self.crop_box = None
        self.document.update_state("smart_repair_region_start", crop_box=None)
        self._set_annotation_tool("crop")
        self.smart_repair_region_var.set("区域：请在画布拖动框选需要擦除的物体")
        self.smart_edit_status_var.set("在画布框选物体；松开后可继续调整边框")
        self._draw_crop()

    def _update_smart_repair_region_label(self) -> None:
        region = self._smart_repair_region_box
        if region is None:
            self.smart_repair_region_var.set("区域：请在画布框选需要擦除的物体")
            return
        left, top, right, bottom = region
        self.smart_repair_region_var.set(
            f"区域：{right - left} × {bottom - top} · 位置 {left},{top}"
        )

    def _clear_smart_selection_mask(self) -> None:
        self._smart_mask_brush_active = False
        self._smart_mask_current_points.clear()
        self._smart_mask_strokes.clear()
        self._smart_selection_refined_mask_rle = None
        self._smart_selection_mask_box = None
        self._smart_selection_mask_preview = None
        if hasattr(self, "smart_mask_refinement_var"):
            self.smart_mask_refinement_var.set("蒙版精修：请先智能识别主体")
        if hasattr(self, "canvas"):
            self.canvas.delete("smart-selection-mask")
            self.canvas.delete("smart-mask-brush")

    def _effective_smart_selection_mask(self) -> tuple[str, tuple[int, int]] | None:
        if not 0 <= self._smart_selection_index < len(self._smart_selection_suggestions):
            return None
        suggestion = self._smart_selection_suggestions[self._smart_selection_index]
        if suggestion.mask_size is None or suggestion.mask_rle is None:
            return None
        return (
            self._smart_selection_refined_mask_rle or suggestion.mask_rle,
            suggestion.mask_size,
        )

    def _reset_smart_mask_refinement(
        self,
        *,
        announce: bool = True,
        redraw: bool = True,
    ) -> None:
        self._smart_mask_brush_active = False
        self._smart_mask_current_points.clear()
        self._smart_mask_strokes.clear()
        self._smart_selection_refined_mask_rle = None
        if hasattr(self, "canvas"):
            self.canvas.delete("smart-mask-brush")
        self._sync_smart_mask_region_box()
        if redraw and hasattr(self, "canvas"):
            self._draw_smart_selection_mask()
            self._draw_crop()
        if hasattr(self, "smart_mask_refinement_var"):
            self.smart_mask_refinement_var.set(
                "蒙版精修：自动蒙版已恢复"
                if self._effective_smart_selection_mask() is not None
                else "蒙版精修：请先智能识别主体"
            )
        if announce:
            self.smart_edit_status_var.set("已重置为当前主体的自动像素蒙版")
            self._update_smart_edit_buttons()

    def _sync_smart_mask_region_box(self) -> None:
        effective = self._effective_smart_selection_mask()
        if effective is None:
            return
        source_revision = self._smart_edit_ready_source()
        if source_revision is None:
            return
        width = source_revision.width or (self.original.width if self.original is not None else 0)
        height = source_revision.height or (
            self.original.height if self.original is not None else 0
        )
        if width < 1 or height < 1:
            return
        if self._smart_selection_refined_mask_rle is None:
            suggestion = self._smart_selection_suggestions[self._smart_selection_index]
            box = (
                max(0, min(width - 1, round(suggestion.region[0] * width))),
                max(0, min(height - 1, round(suggestion.region[1] * height))),
                max(1, min(width, round(suggestion.region[2] * width))),
                max(1, min(height, round(suggestion.region[3] * height))),
            )
        else:
            mask_rle, mask_size = effective
            mask = decode_selection_mask(mask_rle, mask_size, target_size=(width, height))
            try:
                bounds = mask.getbbox()
            finally:
                mask.close()
            if bounds is None:
                return
            box = bounds
        left, top, right, bottom = box
        right = max(left + 1, right)
        bottom = max(top + 1, bottom)
        normalized_box = (left, top, right, bottom)
        self._smart_selection_mask_box = normalized_box
        self._smart_repair_region_box = normalized_box
        self.crop_box = normalized_box
        if self.document is not None:
            self.document.update_state("smart_repair_mask_region", crop_box=normalized_box)

    def _activate_smart_mask_brush(self, mode: str) -> None:
        if mode not in {"add", "remove"}:
            raise ValueError("主体蒙版画笔模式无效")
        if self._effective_smart_selection_mask() is None:
            self.smart_edit_status_var.set("请先智能识别一个带像素蒙版的主体")
            return
        if self.file_compare_var.get():
            self._disable_file_comparison(announce=False)
        self._smart_alpha_brush_active = False
        self._smart_mask_brush_mode = mode
        self._smart_mask_brush_active = True
        self._smart_repair_selecting_region = False
        self._set_annotation_tool("select")
        label = "加入蒙版" if mode == "add" else "移出蒙版"
        self.smart_mask_refinement_var.set(f"当前：{label}画笔 · 在画布拖动")
        self.smart_edit_status_var.set(f"{label}画笔已启用；每次松开鼠标形成一条可撤销笔划")
        self.canvas.configure(cursor="crosshair")

    def _rebuild_smart_mask_refinement(self) -> None:
        if not 0 <= self._smart_selection_index < len(self._smart_selection_suggestions):
            raise SmartSelectionError("当前没有可修整的主体蒙版")
        suggestion = self._smart_selection_suggestions[self._smart_selection_index]
        if suggestion.mask_rle is None or suggestion.mask_size is None:
            raise SmartSelectionError("当前主体候选没有像素蒙版")
        self._smart_selection_refined_mask_rle = (
            refine_selection_mask(
                suggestion.mask_rle,
                suggestion.mask_size,
                tuple(self._smart_mask_strokes),
            )
            if self._smart_mask_strokes
            else None
        )
        self._sync_smart_mask_region_box()
        self._draw_smart_selection_mask()
        self._draw_crop()

    def _undo_smart_mask_stroke(self) -> None:
        if not self._smart_mask_strokes:
            self.smart_edit_status_var.set("当前没有可撤销的主体蒙版笔划")
            return
        self._smart_mask_strokes.pop()
        self._smart_mask_current_points.clear()
        self.canvas.delete("smart-mask-brush")
        self._rebuild_smart_mask_refinement()
        self._update_smart_mask_refinement_label()
        self.smart_edit_status_var.set("已撤销上一条主体蒙版笔划")
        self._update_smart_edit_buttons()

    def _update_smart_mask_refinement_label(self) -> None:
        add_count = sum(stroke.mode == "add" for stroke in self._smart_mask_strokes)
        remove_count = len(self._smart_mask_strokes) - add_count
        self.smart_mask_refinement_var.set(
            f"蒙版笔划：加入 {add_count} · 移出 {remove_count}"
            if self._smart_mask_strokes
            else "蒙版精修：自动蒙版未修改"
        )

    def _start_smart_mask_stroke(self, event: tk.Event) -> None:
        if self.base_preview_image is None:
            return
        self._smart_mask_current_points = [self._canvas_to_normalized(event.x, event.y)]
        self._draw_smart_mask_strokes()

    def _drag_smart_mask_stroke(self, event: tk.Event) -> None:
        if not self._smart_mask_current_points:
            return
        point = self._canvas_to_normalized(event.x, event.y)
        previous = self._smart_mask_current_points[-1]
        if (point[0] - previous[0]) ** 2 + (point[1] - previous[1]) ** 2 < 0.000004:
            return
        total_points = sum(len(stroke.points) for stroke in self._smart_mask_strokes)
        if total_points + len(self._smart_mask_current_points) >= 10_000:
            self.smart_edit_status_var.set("主体蒙版采样点已达到 10,000 个上限")
            return
        self._smart_mask_current_points.append(point)
        self._draw_smart_mask_strokes()

    def _finish_smart_mask_stroke(self, _event: tk.Event) -> None:
        if not self._smart_mask_current_points or self.base_preview_image is None:
            return
        if len(self._smart_mask_strokes) >= 100:
            self.smart_edit_status_var.set("主体蒙版笔划已达到 100 条上限")
            self._smart_mask_current_points.clear()
            return
        try:
            brush_size = int(self.smart_mask_brush_size_var.get())
        except (TypeError, ValueError, tk.TclError):
            brush_size = 18
        brush_size = max(2, min(256, brush_size))
        radius = min(
            0.25,
            max(0.001, brush_size / min(self.base_preview_image.size) / 2),
        )
        stroke = SelectionMaskStroke(
            mode=self._smart_mask_brush_mode,
            radius=radius,
            points=tuple(self._smart_mask_current_points),
        )
        self._smart_mask_current_points.clear()
        self._smart_mask_strokes.append(stroke)
        try:
            self._rebuild_smart_mask_refinement()
        except SmartSelectionError as error:
            self._smart_mask_strokes.pop()
            self._rebuild_smart_mask_refinement()
            self.smart_edit_status_var.set(f"{error}；已放弃本条笔划")
            return
        self.canvas.delete("smart-mask-brush")
        self._update_smart_mask_refinement_label()
        self.smart_edit_status_var.set("主体蒙版已实时更新；可继续精修、撤销或直接执行擦除")
        self._update_smart_edit_buttons()

    def _draw_smart_mask_strokes(self) -> None:
        self.canvas.delete("smart-mask-brush")
        image = self.base_preview_image
        if image is None or not self._smart_mask_current_points:
            return
        try:
            brush_size = int(self.smart_mask_brush_size_var.get())
        except (TypeError, ValueError, tk.TclError):
            brush_size = 18
        brush_size = max(2, min(256, brush_size))
        canvas_points = [
            self._normalized_to_canvas(x, y) for x, y in self._smart_mask_current_points
        ]
        color = "#2e7d32" if self._smart_mask_brush_mode == "add" else "#c62828"
        width = max(3, round(brush_size * self.preview_scale))
        flattened = [coordinate for point in canvas_points for coordinate in point]
        if len(canvas_points) > 1:
            self.canvas.create_line(
                *flattened,
                fill=color,
                width=width,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                stipple="gray50",
                tags="smart-mask-brush",
            )
        else:
            x, y = canvas_points[0]
            radius_canvas = width / 2
            self.canvas.create_oval(
                x - radius_canvas,
                y - radius_canvas,
                x + radius_canvas,
                y + radius_canvas,
                fill=color,
                outline="",
                stipple="gray50",
                tags="smart-mask-brush",
            )

    def _draw_smart_selection_mask(self) -> None:
        self.canvas.delete("smart-selection-mask")
        self._smart_selection_mask_preview = None
        if (
            self.base_preview_image is None
            or self.preview is None
            or not 0 <= self._smart_selection_index < len(self._smart_selection_suggestions)
        ):
            return
        effective = self._effective_smart_selection_mask()
        if effective is None:
            return
        mask_rle, mask_size = effective
        try:
            mask = decode_selection_mask(
                mask_rle,
                mask_size,
                target_size=self.base_preview_image.size,
            )
        except SmartSelectionError:
            return
        try:
            display_size = (self.preview.width(), self.preview.height())
            if mask.size != display_size:
                displayed_mask = mask.resize(display_size, Image.Resampling.NEAREST)
            else:
                displayed_mask = mask.copy()
            try:
                accent = ImageColor.getrgb(self.theme_manager.tokens.accent)
                overlay = Image.new("RGBA", display_size, (*accent, 0))
                alpha = displayed_mask.point(lambda value: 92 if value else 0)
                try:
                    overlay.putalpha(alpha)
                    preview = ImageTk.PhotoImage(overlay, master=self.canvas)
                finally:
                    alpha.close()
                    overlay.close()
            finally:
                displayed_mask.close()
        finally:
            mask.close()
        self._smart_selection_mask_preview = preview
        self.canvas.create_image(
            self.preview_offset[0],
            self.preview_offset[1],
            image=preview,
            anchor="nw",
            tags="smart-selection-mask",
        )

    def _start_smart_region_repair(self) -> None:
        source_revision = self._smart_edit_ready_source()
        if source_revision is None:
            return
        region = self._smart_repair_region_box or self.crop_box
        if region is None:
            self.smart_edit_status_var.set("请先在画布框选需要擦除的物体")
            return
        width = source_revision.width or (self.original.width if self.original is not None else 0)
        height = source_revision.height or (
            self.original.height if self.original is not None else 0
        )
        left, top, right, bottom = region
        left = max(0, min(width - 1, left))
        top = max(0, min(height - 1, top))
        right = max(left + 1, min(width, right))
        bottom = max(top + 1, min(height, bottom))
        if right - left < 4 or bottom - top < 4:
            self.smart_edit_status_var.set("擦除区域过小，请重新框选")
            return
        try:
            feather = int(self.smart_repair_feather_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.smart_edit_status_var.set("修复羽化必须是整数")
            return
        if not 0 <= feather <= 64:
            self.smart_edit_status_var.set("修复羽化必须在 0 到 64 像素之间")
            return
        self._smart_repair_region_box = (left, top, right, bottom)
        self._smart_repair_selecting_region = False
        self._set_annotation_tool("select")
        self._update_smart_repair_region_label()
        suggestion = (
            self._smart_selection_suggestions[self._smart_selection_index]
            if 0 <= self._smart_selection_index < len(self._smart_selection_suggestions)
            else None
        )
        use_mask = (
            suggestion is not None
            and suggestion.mask_size is not None
            and suggestion.mask_rle is not None
            and self._effective_smart_selection_mask() is not None
            and self._smart_selection_mask_box == (left, top, right, bottom)
        )
        operation = "erase_mask" if use_mask else "erase_region"
        parameters: tuple[tuple[str, Scalar], ...] = (
            ("operation", operation),
            ("region_left", left / width),
            ("region_top", top / height),
            ("region_right", right / width),
            ("region_bottom", bottom / height),
            ("feather", feather),
        )
        if use_mask:
            assert suggestion is not None
            assert suggestion.mask_size is not None
            assert suggestion.mask_rle is not None
            effective = self._effective_smart_selection_mask()
            assert effective is not None
            effective_mask_rle, effective_mask_size = effective
            parameters = (
                *parameters,
                ("mask_width", effective_mask_size[0]),
                ("mask_height", effective_mask_size[1]),
                ("mask_rle", effective_mask_rle),
                ("mask_refined", bool(self._smart_mask_strokes)),
                ("mask_stroke_count", len(self._smart_mask_strokes)),
                (
                    "mask_point_count",
                    sum(len(stroke.points) for stroke in self._smart_mask_strokes),
                ),
            )
        self._smart_mask_brush_active = False
        submitted = self._submit_smart_edit(
            source_revision,
            operation=operation,
            prompt=("按主体像素蒙版擦除并局部修复" if use_mask else "擦除框选物体并局部修复"),
            parameters=parameters,
            result_summary=(
                f"{'像素蒙版' if use_mask else '矩形区域'}："
                f"{right - left} × {bottom - top} · 羽化 {feather}px"
            ),
        )
        if submitted:
            self._update_smart_edit_buttons()

    def _select_smart_text_region(self) -> None:
        if self.document is None or self.document.source_path is None:
            self.smart_edit_status_var.set("请先打开一张图片")
            return
        if self.file_compare_var.get():
            self._disable_file_comparison(announce=False)
        self._smart_text_suggestions = ()
        self._smart_text_suggestion_index = -1
        self._smart_text_perspective_quad = None
        self.smart_text_curve_var.set(0)
        self.smart_text_effect_var.set("普通")
        self._smart_repair_selecting_region = False
        self._smart_text_selecting_region = True
        self._smart_text_region_box = None
        self.crop_box = None
        self.document.update_state("smart_text_region_start", crop_box=None)
        self._set_annotation_tool("crop")
        self.smart_text_region_var.set("区域：请在画布拖动框选原文字")
        self.smart_edit_status_var.set("在画布框选原文字区域；松开后可校正边框")
        self._draw_crop()

    def _update_smart_text_region_label(self) -> None:
        region = self._smart_text_region_box
        if region is None:
            self.smart_text_region_var.set("区域：请在画布框选原文字")
            return
        left, top, right, bottom = region
        perspective_label = (
            " · 透视四角已校正" if self._smart_text_perspective_quad is not None else ""
        )
        try:
            curve_percent = float(self.smart_text_curve_var.get())
        except (TypeError, ValueError, tk.TclError):
            curve_percent = 0.0
        curve_label = (
            f" · 弧度 {curve_percent:+.0f}%"
            if self._smart_text_perspective_quad is None and abs(curve_percent) >= 0.05
            else ""
        )
        effect_label = self.smart_text_effect_var.get()
        effect_suffix = f" · {effect_label}字效" if effect_label != "普通" else ""
        self.smart_text_region_var.set(
            f"区域：{right - left} × {bottom - top} · 位置 {left},{top}"
            f"{perspective_label}{curve_label}{effect_suffix}"
        )

    def _start_smart_text_replacement(self) -> None:
        source_revision = self._smart_edit_ready_source()
        if source_revision is None:
            return
        region = self._smart_text_region_box or self.crop_box
        if region is None:
            self.smart_edit_status_var.set("请先在画布框选需要替换的原文字")
            return
        width = source_revision.width or (self.original.width if self.original is not None else 0)
        height = source_revision.height or (
            self.original.height if self.original is not None else 0
        )
        left, top, right, bottom = region
        left = max(0, min(width - 1, left))
        top = max(0, min(height - 1, top))
        right = max(left + 1, min(width, right))
        bottom = max(top + 1, min(height, bottom))
        if right - left < 4 or bottom - top < 4:
            self.smart_edit_status_var.set("文字区域过小，请重新框选")
            return
        text = self.smart_text_value_var.get().strip()
        if not text or len(text) > 500:
            self.smart_edit_status_var.set("新文字必须包含 1 到 500 个字符")
            return
        font_family = self.smart_text_font_var.get()
        if font_family not in SMART_TEXT_FONTS:
            self.smart_edit_status_var.set("请选择受支持的中文字体")
            return
        try:
            font_size = int(self.smart_text_font_size_var.get())
            rotation = float(self.smart_text_rotation_var.get())
            curve_percent = float(self.smart_text_curve_var.get())
            effect_size = int(self.smart_text_effect_size_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.smart_edit_status_var.set("字号、旋转角度、弧度和字效强度必须是数字")
            return
        if not 8 <= font_size <= 512:
            self.smart_edit_status_var.set("字号必须在 8 到 512 像素之间")
            return
        if not -180 <= rotation <= 180:
            self.smart_edit_status_var.set("旋转角度必须在 -180 到 180 度之间")
            return
        if not -100 <= curve_percent <= 100:
            self.smart_edit_status_var.set("文字弧度必须在 -100% 到 100% 之间")
            return
        effect_label = self.smart_text_effect_var.get()
        visual_effect = SMART_TEXT_EFFECTS.get(effect_label)
        if visual_effect is None:
            self.smart_edit_status_var.set("请选择受支持的文字字效")
            return
        if not 0 <= effect_size <= 64:
            self.smart_edit_status_var.set("字效强度必须在 0 到 64 像素之间")
            return
        if visual_effect in {"outline", "shadow", "glow"} and effect_size < 1:
            self.smart_edit_status_var.set("描边、投影或发光强度必须至少为 1 像素")
            return
        color = self.smart_text_color_var.get().strip().lower()
        try:
            ImageColor.getrgb(color)
        except ValueError:
            self.smart_edit_status_var.set("文字颜色无效，请使用 #RRGGBB 格式")
            return
        effect_color = self.smart_text_effect_color_var.get().strip().lower()
        try:
            ImageColor.getrgb(effect_color)
        except ValueError:
            self.smart_edit_status_var.set("字效辅助色无效，请使用 #RRGGBB 格式")
            return
        align_label = self.smart_text_align_var.get()
        align = SMART_TEXT_ALIGNMENTS.get(align_label)
        if align is None:
            self.smart_edit_status_var.set("请选择文字对齐方式")
            return
        self._smart_text_region_box = (left, top, right, bottom)
        self._update_smart_text_region_label()
        try:
            draft = TextReplacementDraft(
                region=(left / width, top / height, right / width, bottom / height),
                text=text,
                font_family=font_family,
                font_size=font_size,
                color=color,
                align=align,
                rotation=rotation,
                curve=curve_percent / 100,
                perspective_quad=self._smart_text_perspective_quad,
                visual_effect=visual_effect,
                effect_color=effect_color,
                effect_size=effect_size,
            )
        except ValueError as error:
            self.smart_edit_status_var.set(str(error))
            return
        submitted = self._submit_smart_edit(
            source_revision,
            operation="replace_text",
            prompt="替换图片文字",
            parameters=draft.to_parameters(),
            result_summary=(
                f"文字：{text[:18]} · {font_family} {font_size}px · {align_label}"
                f" · {effect_label}字效 · 弧度 {curve_percent:+.0f}%"
            ),
        )
        if submitted:
            self.smart_text_history.push(draft)
            self._update_smart_edit_buttons()

    def _load_smart_text_draft(self, draft: TextReplacementDraft) -> None:
        width = self.original.width if self.original is not None else 1
        height = self.original.height if self.original is not None else 1
        left, top, right, bottom = draft.region
        self._smart_text_region_box = (
            round(left * width),
            round(top * height),
            round(right * width),
            round(bottom * height),
        )
        self.crop_box = self._smart_text_region_box
        self._smart_text_perspective_quad = draft.perspective_quad
        self.smart_text_value_var.set(draft.text)
        self.smart_text_font_var.set(draft.font_family)
        self.smart_text_font_size_var.set(draft.font_size)
        self.smart_text_color_var.set(draft.color)
        align_label = next(
            label for label, value in SMART_TEXT_ALIGNMENTS.items() if value == draft.align
        )
        self.smart_text_align_var.set(align_label)
        self.smart_text_rotation_var.set(draft.rotation)
        self.smart_text_curve_var.set(round(draft.curve * 100, 6))
        self.smart_text_effect_var.set(SMART_TEXT_EFFECT_LABELS[draft.visual_effect])
        self.smart_text_effect_color_var.set(draft.effect_color)
        self.smart_text_effect_size_var.set(draft.effect_size)
        self._update_smart_text_region_label()
        self._draw_crop()

    def _undo_smart_text_draft(self) -> None:
        draft = self.smart_text_history.undo()
        if draft is None:
            self.smart_edit_status_var.set("没有可撤销的文字参数")
            return
        self._load_smart_text_draft(draft)
        self.smart_edit_status_var.set("已撤销文字参数；可检查后重新生成候选")
        self._update_smart_edit_buttons()

    def _redo_smart_text_draft(self) -> None:
        draft = self.smart_text_history.redo()
        if draft is None:
            self.smart_edit_status_var.set("没有可重做的文字参数")
            return
        self._load_smart_text_draft(draft)
        self.smart_edit_status_var.set("已重做文字参数；可检查后重新生成候选")
        self._update_smart_edit_buttons()

    def _start_smart_upscale(self, scale: int) -> None:
        if scale not in {2, 4}:
            raise ValueError("本地高清倍数必须是 2 或 4")
        source_revision = self._smart_edit_ready_source()
        if source_revision is None:
            return
        width = source_revision.width or (self.original.width if self.original is not None else 0)
        height = source_revision.height or (
            self.original.height if self.original is not None else 0
        )
        target_width, target_height = width * scale, height * scale
        if (
            target_width < 1
            or target_height < 1
            or target_width > 16_384
            or target_height > 16_384
            or target_width * target_height > 128_000_000
        ):
            self.smart_edit_status_var.set("目标尺寸超过本地高清安全上限")
            return
        self._submit_smart_edit(
            source_revision,
            operation="upscale",
            prompt=f"本地高清 {scale}×",
            parameters=(("operation", "upscale"), ("scale", scale)),
            result_summary=f"目标尺寸：{target_width} × {target_height}",
        )

    def _start_smart_remove_background(self) -> None:
        source_revision = self._smart_edit_ready_source()
        if source_revision is None:
            return
        try:
            tolerance = int(self.smart_background_tolerance_var.get())
            feather = int(self.smart_background_feather_var.get())
        except (TypeError, ValueError, tk.TclError):
            self.smart_edit_status_var.set("背景容差和羽化必须是整数")
            return
        if not 0 <= tolerance <= 254:
            self.smart_edit_status_var.set("背景容差必须在 0 到 254 之间")
            return
        if not 1 <= feather <= 255:
            self.smart_edit_status_var.set("边缘羽化必须在 1 到 255 之间")
            return
        submitted = self._submit_smart_edit(
            source_revision,
            operation="remove_background",
            prompt="离线智能去背景",
            parameters=(
                ("operation", "remove_background"),
                ("tolerance", tolerance),
                ("feather", feather),
            ),
            result_summary=f"输出：透明 PNG · 容差 {tolerance} · 羽化 {feather}",
        )
        if submitted:
            self._smart_alpha_brush_active = False
            self._smart_alpha_current_points.clear()
            self._smart_alpha_strokes.clear()
            self.canvas.delete("smart-alpha-brush")
            self.canvas.configure(cursor="arrow")
            self.smart_alpha_refinement_var.set("边缘画笔：等待去背景结果")

    def _smart_background_mode_changed(self) -> None:
        mode = self.smart_background_replace_mode_var.get()
        if mode == "自定义图片" and self.smart_background_replace_image_path is None:
            self.smart_edit_status_var.set("自定义图片会居中铺满裁切；请先选择背景图")
        elif mode == "自定义图片":
            self.smart_edit_status_var.set("自定义背景图会居中铺满裁切，并以底色填充透明区域")
        else:
            self.smart_edit_status_var.set("纯色背景会与透明结果合成并创建新的不透明 PNG 修订")
        self._update_smart_edit_buttons()

    def _choose_smart_background_color(self) -> None:
        selected = colorchooser.askcolor(
            self.smart_background_replace_color_var.get(),
            parent=self,
            title="选择替换背景底色",
        )[1]
        if selected:
            self.smart_background_replace_color_var.set(selected.lower())
            self.smart_edit_status_var.set(f"背景底色已选择：{selected.lower()}")

    def _choose_smart_background_image(self) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in sorted(SUPPORTED_INPUTS))
        selected = filedialog.askopenfilename(
            parent=self,
            title="选择自定义背景图片",
            filetypes=[("图片文件", patterns), ("所有文件", "*.*")],
        )
        if not selected:
            return
        path = Path(selected)
        self.smart_background_replace_image_path = path
        self.smart_background_replace_image_var.set(f"背景图：{path.name}")
        self.smart_background_replace_mode_var.set("自定义图片")
        self.smart_edit_status_var.set("背景图已选择；应用时会安全解码并居中铺满裁切")
        self._update_smart_edit_buttons()

    def _apply_smart_background_replacement(self) -> None:
        alpha_revision = self._smart_background_alpha_revision
        if alpha_revision is None:
            self.smart_edit_status_var.set("请先完成一次智能去背景或边缘细化")
            return
        try:
            mode = self.smart_background_replace_mode_var.get()
            if mode == "纯色背景":
                spec = BackgroundSpec.solid_color(self.smart_background_replace_color_var.get())
            elif mode == "自定义图片":
                if self.smart_background_replace_image_path is None:
                    self.smart_edit_status_var.set("请先选择一张自定义背景图片")
                    return
                spec = BackgroundSpec.custom_image(
                    self.smart_background_replace_image_path,
                    fallback_color=self.smart_background_replace_color_var.get(),
                )
            else:
                self.smart_edit_status_var.set("背景替换类型无效")
                return
        except ValueError as error:
            self.smart_edit_status_var.set(str(error))
            return
        future = self._smart_background_compose_future
        if future is not None and not future.done():
            self.smart_edit_status_var.set("已有背景替换正在运行")
            return
        self._smart_background_compose_generation += 1
        generation = self._smart_background_compose_generation
        source_revision_id = alpha_revision.revision_id
        self._smart_background_compose_source_revision_id = source_revision_id
        self.smart_edit_status_var.set("正在后台合成背景并准备安全入库")
        self.smart_edit_result_var.set(
            "纯色背景" if spec.kind is BackgroundKind.COLOR else "自定义图片 · 居中铺满裁切"
        )
        self._smart_background_compose_future = self._asset_executor.submit(
            self._compose_and_import_smart_background,
            source_revision_id,
            spec,
        )
        self._update_smart_edit_buttons()
        self._smart_background_compose_after = self.after(
            40,
            lambda: self._finish_smart_background_replacement(
                generation,
                source_revision_id,
            ),
        )

    def _compose_and_import_smart_background(
        self,
        source_revision_id: str,
        spec: BackgroundSpec,
    ) -> tuple[AssetRevision, BackgroundCompositionResult]:
        source_revision = self.asset_repository.get_revision(source_revision_id)
        source_path = self.asset_repository.resolve_content(source_revision_id)
        asset = self.asset_repository.get_asset(source_revision.asset_id)
        staging_parent = self.settings_store.path.parent
        staging_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".light-image-editor-background-",
            dir=staging_parent,
        ) as directory:
            staged = Path(directory) / "background-result.png"
            result = compose_background(source_path, staged, spec)
            if not result.source_had_transparency:
                raise ValueError("当前去背景结果没有透明区域，无法显示替换背景")
            parameters: dict[str, Scalar] = {
                "operation": "replace_background",
                "background_kind": spec.kind.value,
                "background_color": spec.color,
                "background_sha256": result.background_sha256,
            }
            if spec.image_path is not None:
                parameters["background_file_name"] = spec.image_path.name
            provenance = Provenance(
                origin="content_edit",
                actor="local_background_compositor",
                source_revision_ids=(source_revision.revision_id,),
                parameters=parameters,
            )
            suffix = "纯色背景" if spec.kind is BackgroundKind.COLOR else "自定义背景"
            revision = self.asset_repository.import_managed(
                staged,
                provenance=provenance,
                asset_id=source_revision.asset_id,
                parent_revision_id=source_revision.revision_id,
                asset_name=f"{Path(asset.name).stem}-{suffix}.png",
                expected_media_type="image/png",
            )
            return revision, result

    def _finish_smart_background_replacement(
        self,
        generation: int,
        source_revision_id: str,
    ) -> None:
        self._smart_background_compose_after = None
        future = self._smart_background_compose_future
        if future is None:
            return
        if not future.done():
            self._smart_background_compose_after = self.after(
                50,
                lambda: self._finish_smart_background_replacement(
                    generation,
                    source_revision_id,
                ),
            )
            return
        self._smart_background_compose_future = None
        try:
            revision, result = future.result()
        except Exception as error:
            if generation == self._smart_background_compose_generation:
                self.smart_edit_status_var.set(f"背景替换失败：{error}")
                self.smart_edit_result_var.set("透明修订保持不变，可调整设置后重试")
                self._update_smart_edit_buttons()
            return
        if (
            generation != self._smart_background_compose_generation
            or self._smart_background_compose_source_revision_id != source_revision_id
            or self._smart_background_alpha_revision is None
            or self._smart_background_alpha_revision.revision_id != source_revision_id
        ):
            return
        current_revision = self._current_asset_revision
        if current_revision is None or current_revision.asset_id != revision.asset_id:
            return
        self._smart_edit_result_revision = revision
        self._smart_edit_operation = "replace_background"
        kind_label = "纯色" if result.background_sha256 is None else "自定义图片"
        self.smart_edit_status_var.set(f"{kind_label}背景替换完成 · 已保存为新的托管修订")
        self.smart_edit_result_var.set(
            f"结果：{revision.width} × {revision.height} · 不透明 PNG · 修订 {revision.sequence}"
        )
        self.mode_router.update_state(
            {
                "smart_operation": "replace_background",
                "smart_result_revision_id": revision.revision_id,
                "smart_result_parent_revision_id": revision.parent_revision_id,
                "smart_result_width": revision.width,
                "smart_result_height": revision.height,
                "smart_result_operation": "replace_background",
                "smart_background_kind": revision.provenance.parameters.get("background_kind"),
                "smart_background_sha256": result.background_sha256,
            },
            mode=ModeId.EDIT,
        )
        self._update_smart_edit_buttons()

    def _activate_smart_alpha_brush(self, mode: str) -> None:
        if mode not in {"preserve", "remove"}:
            raise ValueError("边缘细化画笔模式无效")
        revision = self._smart_background_alpha_revision
        if revision is None:
            self.smart_edit_status_var.set("请先完成一次智能去背景")
            return
        if self.file_compare_var.get():
            self._disable_file_comparison(announce=False)
        self._smart_mask_brush_active = False
        self._smart_mask_current_points.clear()
        self.canvas.delete("smart-mask-brush")
        self._smart_alpha_brush_mode = mode
        self._smart_alpha_brush_active = True
        label = "保留主体" if mode == "preserve" else "删除背景"
        self.smart_alpha_refinement_var.set(f"当前：{label}画笔 · 在画布拖动标记区域")
        self.smart_edit_status_var.set(
            f"{label}画笔已启用；标记基于原图坐标，完成后点击应用边缘细化"
        )
        self.canvas.configure(cursor="crosshair")

    def _clear_smart_alpha_strokes(self) -> None:
        self._smart_alpha_current_points.clear()
        self._smart_alpha_strokes.clear()
        self.canvas.delete("smart-alpha-brush")
        self.smart_alpha_refinement_var.set("边缘画笔：笔划已清除")
        self._update_smart_edit_buttons()

    def _apply_smart_alpha_refinement(self) -> None:
        revision = self._smart_background_alpha_revision
        if revision is None:
            self.smart_edit_status_var.set("请先完成一次智能去背景")
            return
        if not self._smart_alpha_strokes:
            self.smart_edit_status_var.set("请先使用保留或删除画笔标记边缘")
            return
        try:
            strokes_payload = serialize_alpha_strokes(tuple(self._smart_alpha_strokes))
        except ValueError as error:
            self.smart_edit_status_var.set(str(error))
            return
        stroke_count = len(self._smart_alpha_strokes)
        point_count = sum(len(stroke.points) for stroke in self._smart_alpha_strokes)
        self._smart_alpha_brush_active = False
        submitted = self._submit_smart_edit(
            revision,
            operation="refine_alpha",
            prompt="细化去背景边缘",
            parameters=(
                ("operation", "refine_alpha"),
                ("strokes", strokes_payload),
                ("stroke_count", stroke_count),
                ("point_count", point_count),
            ),
            result_summary=f"边缘细化：{stroke_count} 条笔划 · {point_count} 个采样点",
        )
        if not submitted:
            self._smart_alpha_brush_active = True

    def _start_smart_alpha_stroke(self, event: tk.Event) -> None:
        if self.original is None:
            return
        self._smart_alpha_current_points = [self._canvas_to_normalized(event.x, event.y)]
        self._draw_smart_alpha_strokes()

    def _drag_smart_alpha_stroke(self, event: tk.Event) -> None:
        if not self._smart_alpha_current_points:
            return
        point = self._canvas_to_normalized(event.x, event.y)
        previous = self._smart_alpha_current_points[-1]
        if (point[0] - previous[0]) ** 2 + (point[1] - previous[1]) ** 2 < 0.000004:
            return
        total_points = sum(len(stroke.points) for stroke in self._smart_alpha_strokes)
        if total_points + len(self._smart_alpha_current_points) >= 10_000:
            self.smart_edit_status_var.set("边缘细化采样点已达到 10,000 个上限")
            return
        self._smart_alpha_current_points.append(point)
        self._draw_smart_alpha_strokes()

    def _finish_smart_alpha_stroke(self, _event: tk.Event) -> None:
        if not self._smart_alpha_current_points or self.original is None:
            return
        if len(self._smart_alpha_strokes) >= 100:
            self.smart_edit_status_var.set("边缘细化笔划已达到 100 条上限")
            self._smart_alpha_current_points.clear()
            return
        try:
            brush_size = int(self.smart_alpha_brush_size_var.get())
        except (TypeError, ValueError, tk.TclError):
            brush_size = 18
        brush_size = max(2, min(256, brush_size))
        radius = min(0.25, max(0.001, brush_size / min(self.original.size) / 2))
        self._smart_alpha_strokes.append(
            AlphaBrushStroke(
                mode=self._smart_alpha_brush_mode,
                radius=radius,
                points=tuple(self._smart_alpha_current_points),
            )
        )
        self._smart_alpha_current_points.clear()
        self._draw_smart_alpha_strokes()
        preserve_count = sum(stroke.mode == "preserve" for stroke in self._smart_alpha_strokes)
        remove_count = len(self._smart_alpha_strokes) - preserve_count
        self.smart_alpha_refinement_var.set(f"笔划：保留 {preserve_count} · 删除 {remove_count}")
        self._update_smart_edit_buttons()

    def _draw_smart_alpha_strokes(self) -> None:
        self.canvas.delete("smart-alpha-brush")
        image = self.base_preview_image
        if image is None:
            return
        strokes: list[tuple[str, float, tuple[tuple[float, float], ...]]] = [
            (stroke.mode, stroke.radius, stroke.points) for stroke in self._smart_alpha_strokes
        ]
        if self._smart_alpha_current_points:
            try:
                brush_size = int(self.smart_alpha_brush_size_var.get())
            except (TypeError, ValueError, tk.TclError):
                brush_size = 18
            brush_size = max(2, min(256, brush_size))
            radius = min(0.25, max(0.001, brush_size / min(image.size) / 2))
            strokes.append(
                (
                    self._smart_alpha_brush_mode,
                    radius,
                    tuple(self._smart_alpha_current_points),
                )
            )
        for mode, radius, points in strokes:
            canvas_points = [self._normalized_to_canvas(x, y) for x, y in points]
            color = "#2e7d32" if mode == "preserve" else "#c62828"
            width = max(3, round(radius * min(image.size) * self.preview_scale * 2))
            flattened = [coordinate for point in canvas_points for coordinate in point]
            if len(canvas_points) > 1:
                self.canvas.create_line(
                    *flattened,
                    fill=color,
                    width=width,
                    capstyle=tk.ROUND,
                    joinstyle=tk.ROUND,
                    stipple="gray50",
                    tags="smart-alpha-brush",
                )
            else:
                x, y = canvas_points[0]
                radius_canvas = width / 2
                self.canvas.create_oval(
                    x - radius_canvas,
                    y - radius_canvas,
                    x + radius_canvas,
                    y + radius_canvas,
                    fill=color,
                    outline=color,
                    stipple="gray50",
                    tags="smart-alpha-brush",
                )

    def _smart_edit_ready_source(self) -> AssetRevision | None:
        if self.document is None or self.document.source_path is None:
            self.smart_edit_status_var.set("请先打开一张图片")
            return None
        source_revision = self._current_asset_revision
        if source_revision is None:
            self.smart_edit_status_var.set("图片正在登记到资源库，请稍后再试")
            return None
        import_future = self._smart_edit_import_future
        if import_future is not None and not import_future.done():
            self.smart_edit_status_var.set("上一项智能结果正在安全入库，请稍候")
            return None
        background_future = self._smart_background_compose_future
        if background_future is not None and not background_future.done():
            self.smart_edit_status_var.set("背景替换正在后台合成，请稍候")
            return None
        if self._smart_edit_job_id is not None:
            previous = self.ai_job_manager.get_job(self._smart_edit_job_id)
            if not previous.status.terminal:
                self.smart_edit_status_var.set("已有智能改图任务正在运行")
                return None
        return source_revision

    def _submit_smart_edit(
        self,
        source_revision: AssetRevision,
        *,
        operation: str,
        prompt: str,
        parameters: tuple[tuple[str, Scalar], ...],
        result_summary: str,
    ) -> bool:
        if self._smart_edit_after is not None:
            self.after_cancel(self._smart_edit_after)
            self._smart_edit_after = None
        request = ContentEditRequest(
            source_revision=source_revision,
            prompt=prompt,
            model=MOCK_MODEL_ID,
            parameters=parameters,
        )
        label = self._smart_edit_label(operation)
        try:
            requirements = AIExecutionRequirements.for_request(
                self.mock_provider.manifest,
                request,
                remote_image_upload=False,
                estimated_cost_minor=0,
            )
            self.ai_authorization_gate.authorize_execution(requirements)
            snapshot = self.ai_job_manager.submit_edit(request)
        except Exception:
            self.smart_edit_status_var.set(f"无法创建{label}任务，原图片未受影响")
            self.smart_edit_result_var.set("任务未提交，也不会自动重试")
            return False
        self._smart_edit_job_id = snapshot.job_id
        self._smart_edit_operation = operation
        self._smart_edit_source_revision_id = source_revision.revision_id
        self._smart_edit_presented_job_id = None
        self._smart_edit_import_future = None
        self._smart_edit_result_revision = None
        if operation != "refine_alpha":
            self._smart_background_alpha_revision = None
        self.smart_edit_status_var.set(f"{label}正在后台提交，可随时取消")
        self.smart_edit_result_var.set(result_summary)
        self._sync_ai_task_to_tray(
            snapshot,
            title=label,
            detail=f"正在后台提交 · {result_summary}",
        )
        self.mode_router.update_state(
            {
                "smart_job_id": snapshot.job_id,
                "smart_operation": operation,
                "smart_parameters": dict(parameters),
                "smart_source_revision_id": source_revision.revision_id,
            },
            mode=ModeId.EDIT,
        )
        self._update_smart_edit_buttons()
        self._smart_edit_after = self.after(40, self._finish_smart_edit)
        return True

    @staticmethod
    def _smart_edit_label(operation: str | None) -> str:
        labels = {
            "remove_background": "智能去背景",
            "refine_alpha": "边缘细化",
            "replace_background": "背景替换",
            "erase_region": "物体擦除",
            "erase_mask": "主体蒙版擦除",
            "replace_text": "文字替换",
        }
        return labels.get(operation, "本地高清")

    @staticmethod
    def _smart_edit_running_action(operation: str | None) -> str:
        actions = {
            "remove_background": "正在分离主体",
            "refine_alpha": "正在应用保留与删除笔划",
            "erase_region": "正在擦除物体并重建局部背景",
            "erase_mask": "正在按像素蒙版擦除主体",
            "replace_text": "正在修复背景并排版",
        }
        return actions.get(operation, "正在放大")

    def _finish_smart_edit(self) -> None:
        self._smart_edit_after = None
        job_id = self._smart_edit_job_id
        if job_id is None:
            return
        operation = self._smart_edit_operation
        label = self._smart_edit_label(operation)
        import_future = self._smart_edit_import_future
        if import_future is not None:
            if not import_future.done():
                self._smart_edit_after = self.after(60, self._finish_smart_edit)
                return
            self._smart_edit_import_future = None
            try:
                imported = import_future.result()
            except Exception:
                self.smart_edit_status_var.set(f"{label}结果安全校验或入库失败")
                self.smart_edit_result_var.set("未采用服务结果；原图片和编辑历史不受影响")
                self._update_smart_edit_buttons()
                return
            if self._smart_edit_job_id != job_id:
                return
            snapshot = self.ai_job_manager.get_job(job_id)
            self._sync_ai_task_to_tray(snapshot, title=label)
            self._present_smart_edit_result(snapshot, imported)
            return

        snapshot = self.ai_job_manager.get_job(job_id)
        self._sync_ai_task_to_tray(snapshot, title=label)
        if snapshot.status in {JobStatus.SUBMITTING, JobStatus.QUEUED, JobStatus.RUNNING}:
            if snapshot.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                self.ai_job_manager.refresh(job_id)
            status_labels = {
                JobStatus.SUBMITTING: "正在安全提交",
                JobStatus.QUEUED: "已排队",
                JobStatus.RUNNING: self._smart_edit_running_action(operation),
            }
            self.smart_edit_status_var.set(f"{label} · {status_labels[snapshot.status]}")
            self._smart_edit_after = self.after(80, self._finish_smart_edit)
            return
        if snapshot.status is JobStatus.CANCELLED:
            self.smart_edit_status_var.set(f"{label}任务已取消")
            self.smart_edit_result_var.set("未创建新修订")
            self._update_smart_edit_buttons()
            return
        if snapshot.status in {JobStatus.FAILED, JobStatus.UNKNOWN}:
            message = snapshot.error.message if snapshot.error is not None else "未知错误"
            self.smart_edit_status_var.set(
                f"{label}任务状态未知"
                if snapshot.status is JobStatus.UNKNOWN
                else f"{label}任务失败"
            )
            self.smart_edit_result_var.set(message)
            self._update_smart_edit_buttons()
            return
        if snapshot.status is not JobStatus.SUCCEEDED:
            self.smart_edit_status_var.set(f"任务状态：{snapshot.status.value}")
            self._update_smart_edit_buttons()
            return
        if self._smart_edit_presented_job_id == job_id:
            return
        source_revision_id = self._smart_edit_source_revision_id
        if source_revision_id is None:
            self.smart_edit_status_var.set(f"{label}任务缺少源修订，已停止入库")
            self._update_smart_edit_buttons()
            return
        self.smart_edit_status_var.set(f"{label}结果已生成，正在安全解码并写入修订树")
        self._smart_edit_import_future = self._asset_executor.submit(
            self._import_smart_edit_candidate,
            snapshot,
            source_revision_id,
        )
        self._smart_edit_after = self.after(30, self._finish_smart_edit)

    def _import_smart_edit_candidate(
        self,
        snapshot: ManagedJobSnapshot,
        source_revision_id: str,
    ) -> AssetRevision:
        if snapshot.request_kind is not JobRequestKind.EDIT:
            raise ValueError("智能改图结果的任务类型无效")
        if snapshot.status is not JobStatus.SUCCEEDED or len(snapshot.outputs) != 1:
            raise ValueError("智能改图任务必须返回一个成功候选")
        source_revision = self.asset_repository.get_revision(source_revision_id)
        provider_revision = snapshot.outputs[0].current_revision
        provenance = provider_revision.provenance
        if provenance.origin != "content_edit":
            raise ValueError("智能改图结果的来源类型无效")
        if provenance.request_id != snapshot.request_id:
            raise ValueError("智能改图结果的请求身份不匹配")
        if provenance.provider_id != snapshot.provider_id:
            raise ValueError("智能改图结果的服务身份不匹配")
        if provenance.source_revision_ids != (source_revision.revision_id,):
            raise ValueError("智能改图结果的源修订血缘不匹配")
        source = _local_file_uri_path(provider_revision.content_uri)
        asset = self.asset_repository.get_asset(source_revision.asset_id)
        parameters = provenance.parameters
        operation = parameters.get("operation")
        if operation not in {
            "upscale",
            "remove_background",
            "refine_alpha",
            "erase_region",
            "erase_mask",
            "replace_text",
        }:
            raise ValueError("智能改图结果的操作类型无效")
        if operation == "remove_background":
            name = f"{Path(asset.name).stem}-去背景.png"
        elif operation == "refine_alpha":
            name = f"{Path(asset.name).stem}-边缘细化.png"
        elif operation == "replace_text":
            name = f"{Path(asset.name).stem}-改文字.png"
        elif operation in {"erase_region", "erase_mask"}:
            name = f"{Path(asset.name).stem}-物体擦除.png"
        else:
            scale = parameters.get("scale")
            if scale not in {2, 4}:
                raise ValueError("本地高清结果的倍率无效")
            name = f"{Path(asset.name).stem}-高清{scale}x.png"
        return self.asset_repository.import_managed(
            source,
            provenance=provenance,
            asset_id=source_revision.asset_id,
            parent_revision_id=source_revision.revision_id,
            asset_name=name,
            expected_media_type="image/png",
        )

    def _present_smart_edit_result(
        self,
        snapshot: ManagedJobSnapshot,
        revision: AssetRevision,
    ) -> None:
        self._smart_edit_result_revision = revision
        self._smart_edit_presented_job_id = snapshot.job_id
        label = self._smart_edit_label(self._smart_edit_operation)
        self.smart_edit_status_var.set(f"{label}完成 · 已保存为新的托管修订")
        alpha_summary = (
            " · 透明 PNG"
            if self._smart_edit_operation in {"remove_background", "refine_alpha"}
            else ""
        )
        self.smart_edit_result_var.set(
            f"结果：{revision.width} × {revision.height}{alpha_summary} · 修订 {revision.sequence}"
        )
        self._sync_ai_task_to_tray(
            snapshot,
            title=label,
            detail=f"已安全入库为修订 {revision.sequence} · {revision.width}×{revision.height}",
        )
        self.mode_router.update_state(
            {
                "smart_job_id": snapshot.job_id,
                "smart_result_revision_id": revision.revision_id,
                "smart_result_parent_revision_id": revision.parent_revision_id,
                "smart_result_width": revision.width,
                "smart_result_height": revision.height,
                "smart_result_operation": self._smart_edit_operation,
            },
            mode=ModeId.EDIT,
        )
        if self._smart_edit_operation == "remove_background":
            self._smart_background_alpha_revision = revision
            self._smart_alpha_brush_active = False
            self._smart_alpha_current_points.clear()
            self._smart_alpha_strokes.clear()
            self.canvas.delete("smart-alpha-brush")
            self.canvas.configure(cursor="arrow")
            self.smart_alpha_refinement_var.set("透明结果已就绪，可用保留/删除画笔继续细化")
        elif self._smart_edit_operation == "refine_alpha":
            self._smart_background_alpha_revision = revision
            self._smart_alpha_brush_active = False
            self._smart_alpha_current_points.clear()
            self._smart_alpha_strokes.clear()
            self.canvas.delete("smart-alpha-brush")
            self.canvas.configure(cursor="arrow")
            self.smart_alpha_refinement_var.set("边缘细化已应用；可继续添加笔划")
        self._update_smart_edit_buttons()

    def _cancel_smart_edit(self) -> None:
        selection_future = self._smart_selection_future
        if selection_future is not None and not selection_future.done():
            self._smart_selection_generation += 1
            cancelled = selection_future.cancel()
            self._smart_selection_future = None
            self._smart_selection_suggestions = ()
            self._smart_selection_index = -1
            self._update_smart_repair_region_label()
            self.smart_edit_status_var.set(
                "智能选区已取消"
                if cancelled
                else "已停止等待智能选区；后台只读取本地图片且不会改动修订"
            )
            self._update_smart_edit_buttons()
            return
        font_match_future = self._smart_font_match_future
        if font_match_future is not None and not font_match_future.done():
            self._smart_font_match_generation += 1
            cancelled = font_match_future.cancel()
            self._smart_font_match_future = None
            self.smart_text_font_match_var.set("字体推荐：已取消")
            self.smart_edit_status_var.set(
                "字体推荐已取消"
                if cancelled
                else "已停止等待字体推荐；后台只读取本地图片且不会改动修订"
            )
            self._update_smart_edit_buttons()
            return
        background_future = self._smart_background_compose_future
        if background_future is not None and not background_future.done():
            self._smart_background_compose_generation += 1
            cancelled = background_future.cancel()
            if self._smart_background_compose_after is not None:
                self.after_cancel(self._smart_background_compose_after)
                self._smart_background_compose_after = None
            self._smart_background_compose_future = None
            self.smart_edit_status_var.set(
                "背景替换已取消"
                if cancelled
                else "已停止等待背景替换；若已进入原子入库，修订仍会保留"
            )
            self.smart_edit_result_var.set("透明修订保持不变")
            self._update_smart_edit_buttons()
            return
        if self._smart_edit_job_id is None:
            if self._smart_selection_suggestions:
                self._smart_selection_suggestions = ()
                self._smart_selection_index = -1
                self._smart_repair_region_box = None
                self._smart_repair_selecting_region = False
                self._clear_smart_selection_mask()
                self._update_smart_repair_region_label()
                self.smart_edit_status_var.set("智能主体选区已清除")
                self._update_smart_edit_buttons()
                return
            self.smart_edit_status_var.set("当前没有可取消的智能任务")
            return
        import_future = self._smart_edit_import_future
        if import_future is not None and not import_future.done():
            self.smart_edit_status_var.set("结果正在原子入库，完成后可选择发送或放弃")
            if self._smart_edit_after is None:
                self._smart_edit_after = self.after(60, self._finish_smart_edit)
            return
        if self._smart_edit_after is not None:
            self.after_cancel(self._smart_edit_after)
            self._smart_edit_after = None
        snapshot = self.ai_job_manager.cancel(self._smart_edit_job_id)
        label = self._smart_edit_label(self._smart_edit_operation)
        self.smart_edit_status_var.set(
            f"{label}任务已取消"
            if snapshot.status is JobStatus.CANCELLED
            else f"任务已结束：{snapshot.status.value}"
        )
        self.smart_edit_result_var.set("未创建新修订")
        self._sync_ai_task_to_tray(snapshot, title=label)
        self._update_smart_edit_buttons()

    def _update_smart_edit_buttons(self) -> None:
        provider_active = False
        if self._smart_edit_job_id is not None:
            try:
                provider_active = not self.ai_job_manager.get_job(
                    self._smart_edit_job_id
                ).status.terminal
            except Exception:
                provider_active = False
        import_active = bool(
            self._smart_edit_import_future is not None and not self._smart_edit_import_future.done()
        )
        detection_active = bool(
            self._smart_text_detection_future is not None
            and not self._smart_text_detection_future.done()
        )
        ocr_active = bool(
            self._smart_text_ocr_future is not None and not self._smart_text_ocr_future.done()
        )
        font_match_active = bool(
            self._smart_font_match_future is not None and not self._smart_font_match_future.done()
        )
        selection_active = bool(
            self._smart_selection_future is not None and not self._smart_selection_future.done()
        )
        background_active = bool(
            self._smart_background_compose_future is not None
            and not self._smart_background_compose_future.done()
        )
        active = (
            provider_active
            or import_active
            or detection_active
            or ocr_active
            or font_match_active
            or selection_active
            or background_active
        )
        state = ("disabled",) if active else ("!disabled",)
        self.smart_upscale_2x_button.state(state)
        self.smart_upscale_4x_button.state(state)
        self.smart_remove_background_button.state(state)
        self.smart_repair_detect_button.state(state)
        self.smart_repair_select_button.state(state)
        self.smart_repair_next_button.state(
            ("!disabled",)
            if len(self._smart_selection_suggestions) > 1 and not active
            else ("disabled",)
        )
        self.smart_repair_feather_spinbox.state(state)
        self.smart_repair_apply_button.state(
            ("!disabled",)
            if self._smart_repair_region_box is not None and not active
            else ("disabled",)
        )
        mask_ready = self._effective_smart_selection_mask() is not None and not active
        mask_input_state = ("!disabled",) if mask_ready else ("disabled",)
        self.smart_mask_brush_size_spinbox.state(mask_input_state)
        self.smart_mask_add_button.state(mask_input_state)
        self.smart_mask_remove_button.state(mask_input_state)
        mask_history_state = (
            ("!disabled",) if mask_ready and bool(self._smart_mask_strokes) else ("disabled",)
        )
        self.smart_mask_undo_button.state(mask_history_state)
        self.smart_mask_reset_button.state(mask_history_state)
        self.smart_text_select_button.state(state)
        self.smart_text_replace_button.state(state)
        self.smart_text_detect_button.state(state)
        self.smart_text_recognize_button.state(state)
        self.smart_text_font_match_button.state(state)
        self.smart_text_next_region_button.state(
            ("!disabled",)
            if len(self._smart_text_suggestions) > 1 and not active
            else ("disabled",)
        )
        spinbox_state = ("disabled",) if active else ("!disabled",)
        self.smart_background_tolerance_spinbox.state(spinbox_state)
        self.smart_background_feather_spinbox.state(spinbox_state)
        for widget in self._smart_text_input_widgets:
            widget.state(spinbox_state)
        self.smart_edit_cancel_button.state(
            ("!disabled",)
            if provider_active or background_active or font_match_active or selection_active
            else ("disabled",)
        )
        result_state = (
            ("!disabled",)
            if self._smart_edit_result_revision is not None and not active
            else ("disabled",)
        )
        self.smart_edit_compare_button.state(result_state)
        self.smart_edit_send_button.state(result_state)
        self.smart_edit_discard_button.state(result_state)
        alpha_result_ready = bool(self._smart_background_alpha_revision is not None and not active)
        alpha_input_state = ("!disabled",) if alpha_result_ready else ("disabled",)
        self.smart_alpha_brush_size_spinbox.state(alpha_input_state)
        self.smart_alpha_preserve_button.state(alpha_input_state)
        self.smart_alpha_remove_button.state(alpha_input_state)
        alpha_action_state = (
            ("!disabled",)
            if alpha_result_ready and bool(self._smart_alpha_strokes)
            else ("disabled",)
        )
        self.smart_alpha_clear_button.state(alpha_action_state)
        self.smart_alpha_apply_button.state(alpha_action_state)
        background_input_state = ("!disabled",) if alpha_result_ready else ("disabled",)
        for widget in self._smart_background_replace_controls:
            widget.state(background_input_state)
        background_spec_ready = bool(
            self.smart_background_replace_mode_var.get() == "纯色背景"
            or self.smart_background_replace_image_path is not None
        )
        self.smart_background_replace_button.state(
            ("!disabled",) if alpha_result_ready and background_spec_ready else ("disabled",)
        )
        self.smart_text_undo_button.state(
            ("!disabled",) if self.smart_text_history.can_undo and not active else ("disabled",)
        )
        self.smart_text_redo_button.state(
            ("!disabled",) if self.smart_text_history.can_redo and not active else ("disabled",)
        )

    def _compare_smart_result(self) -> None:
        revision = self._smart_edit_result_revision
        if revision is None:
            self.smart_edit_status_var.set("尚无可对比的智能改图结果")
            return
        try:
            path = self.asset_repository.resolve_content(revision.revision_id)
        except Exception as error:
            self.smart_edit_status_var.set(f"无法读取智能改图结果：{error}")
            return
        if self._begin_file_comparison(path):
            label = self._smart_edit_label(self._smart_edit_operation)
            self.smart_edit_status_var.set(f"正在载入对比：左侧原图，右侧{label}结果")

    def _discard_smart_result(self) -> None:
        revision = self._smart_edit_result_revision
        if revision is None:
            self.smart_edit_status_var.set("当前没有可放弃的智能候选")
            return
        try:
            path = self.asset_repository.resolve_content(revision.revision_id)
        except Exception:
            path = None
        if (
            path is not None
            and self._comparison_path is not None
            and self._same_path(path, self._comparison_path)
        ):
            self._disable_file_comparison(announce=False)
        self._smart_edit_result_revision = None
        self._smart_background_alpha_revision = None
        self._smart_background_compose_generation += 1
        self._smart_background_compose_source_revision_id = None
        self._smart_alpha_brush_active = False
        self._smart_alpha_current_points.clear()
        self._smart_alpha_strokes.clear()
        self.canvas.delete("smart-alpha-brush")
        self.canvas.configure(cursor="arrow")
        self.smart_alpha_refinement_var.set("边缘画笔：等待去背景结果")
        self.smart_edit_status_var.set("已放弃当前候选")
        self.smart_edit_result_var.set("托管修订仍保留在本地修订树中，可供恢复和审计")
        self.mode_router.update_state(
            {
                "smart_result_revision_id": None,
                "smart_result_discarded": True,
            },
            mode=ModeId.EDIT,
        )
        self._update_smart_edit_buttons()

    def _send_smart_result_to_conversion(self) -> None:
        revision = self._smart_edit_result_revision
        if revision is None:
            self.smart_edit_status_var.set("尚无可发送的智能改图结果")
            return
        try:
            path = self.asset_repository.resolve_content(revision.revision_id)
            asset = self.asset_repository.get_asset(revision.asset_id)
        except Exception as error:
            self.smart_edit_status_var.set(f"无法读取智能改图结果：{error}")
            return
        asset_handoff = AssetHandoff(
            id=revision.asset_id,
            uri=path.as_uri(),
            media_type=revision.media_type,
            display_name=asset.name,
            metadata={"source": "smart_edit", "managed": True},
        )
        revision_handoff = RevisionHandoff(
            id=revision.revision_id,
            asset_id=revision.asset_id,
            number=revision.sequence,
            metadata={
                "parent_revision_id": revision.parent_revision_id,
                "operation": self._smart_edit_operation,
            },
        )
        if self.file_compare_var.get():
            self._disable_file_comparison(announce=False)
        self._conversion_handoff_override = (path, asset_handoff, revision_handoff)
        self.mode_router.switch(ModeId.CONVERT)
        self.mode_router.set_handoff(asset_handoff, revision_handoff, mode=ModeId.CONVERT)
        label = self._smart_edit_label(self._smart_edit_operation)
        self.conversion_job_status_var.set(f"{label}修订已加入转换队列，可继续选择格式和尺寸")

    def _choose_generation_reference(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择生成参考图",
            filetypes=[
                (
                    "图片文件",
                    "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif",
                ),
                ("所有文件", "*.*"),
            ],
        )
        if not selected:
            return
        self._begin_generation_reference_import(Path(selected))

    def _use_current_generation_reference(self) -> None:
        revision = self._current_asset_revision
        if revision is None or self.document is None or self.document.source_path is None:
            self.generate_reference_status_var.set("当前图片尚未完成安全登记")
            return
        self._set_generation_reference(revision)

    def _begin_generation_reference_import(self, path: Path) -> None:
        future = self._generation_reference_future
        if future is not None and not future.done():
            self.generate_reference_status_var.set("上一张参考图正在安全登记，请稍候")
            return
        self.generate_reference_status_var.set(f"正在安全登记参考图：{path.name}")
        self._generation_reference_future = self._asset_executor.submit(
            self.asset_repository.ensure_external,
            path,
        )
        self._update_generation_buttons()
        self.after(20, self._poll_generation_reference_import)

    def _poll_generation_reference_import(self) -> None:
        future = self._generation_reference_future
        if future is None:
            return
        if not future.done():
            self.after(30, self._poll_generation_reference_import)
            return
        self._generation_reference_future = None
        try:
            revision = future.result()
        except Exception:
            self.generate_reference_status_var.set("参考图解码或登记失败，未加入请求")
            self._update_generation_buttons()
            return
        self._set_generation_reference(revision)

    def _selected_generation_reference_index(self) -> int | None:
        selection = self.generate_reference_listbox.curselection()
        if selection:
            index = int(selection[0])
            if 0 <= index < len(self._generation_references):
                return index
        current = self._generation_reference_revision
        if current is not None:
            return next(
                (
                    index
                    for index, reference in enumerate(self._generation_references)
                    if reference.revision.revision_id == current.revision_id
                ),
                None,
            )
        return None

    def _refresh_generation_reference_list(self, selected_index: int | None = None) -> None:
        # An asynchronous import finishes while the list is temporarily disabled.
        # Tk ignores selection changes on a disabled listbox, so unlock it before
        # rebuilding and let _update_generation_buttons restore the correct state.
        self.generate_reference_listbox.configure(state=tk.NORMAL)
        self.generate_reference_listbox.delete(0, tk.END)
        for index, reference in enumerate(self._generation_references, start=1):
            try:
                name = self.asset_repository.get_asset(reference.revision.asset_id).name
            except Exception:
                name = reference.revision.revision_id
            role_label = GENERATION_REFERENCE_ROLE_LABELS[reference.role]
            self.generate_reference_listbox.insert(
                tk.END,
                f"{index}. [{role_label} {round(reference.strength * 100)}%] {name}",
            )
        if not self._generation_references:
            self._generation_reference_revision = None
            self.generation_reference_revision_id = ""
            self.generate_reference_status_var.set("未选择参考图 · 最多可添加 4 张")
            self.mode_router.update_state(
                {
                    "reference_revision_id": None,
                    "reference_revision_ids": (),
                    "reference_count": 0,
                },
                mode=ModeId.GENERATE,
            )
            self._update_generation_buttons()
            return
        if selected_index is None:
            selected_index = self._selected_generation_reference_index()
        selected_index = max(
            0,
            min(
                len(self._generation_references) - 1,
                0 if selected_index is None else selected_index,
            ),
        )
        self.generate_reference_listbox.selection_clear(0, tk.END)
        self.generate_reference_listbox.selection_set(selected_index)
        self.generate_reference_listbox.activate(selected_index)
        self.generate_reference_listbox.see(selected_index)
        self._generation_reference_selected()
        primary = self._generation_references[0]
        self.generation_reference_revision_id = primary.revision.revision_id
        self.mode_router.update_state(
            {
                "reference_revision_id": primary.revision.revision_id,
                "reference_revision_ids": tuple(
                    reference.revision.revision_id for reference in self._generation_references
                ),
                "reference_count": len(self._generation_references),
            },
            mode=ModeId.GENERATE,
        )
        self._update_generation_buttons()

    def _generation_reference_selected(
        self,
        _event: tk.Event[tk.Misc] | None = None,
    ) -> None:
        index = self._selected_generation_reference_index()
        if index is None:
            return
        reference = self._generation_references[index]
        self._generation_reference_revision = reference.revision
        self.generate_reference_role_var.set(GENERATION_REFERENCE_ROLE_LABELS[reference.role])
        self.generate_reference_strength_var.set(round(reference.strength * 100))
        try:
            asset = self.asset_repository.get_asset(reference.revision.asset_id)
            name = asset.name
        except Exception:
            name = reference.revision.revision_id
        dimensions = (
            f" · {reference.revision.width}×{reference.revision.height}"
            if reference.revision.width is not None and reference.revision.height is not None
            else ""
        )
        self.generate_reference_status_var.set(
            f"已选 {len(self._generation_references)}/4 · 当前：{name}{dimensions}"
        )

    def _set_generation_reference(
        self,
        revision: AssetRevision,
        *,
        role: str | None = None,
        strength: float | None = None,
    ) -> None:
        try:
            self.asset_repository.get_asset(revision.asset_id)
            self.asset_repository.resolve_content(revision.revision_id, verify=False)
        except Exception:
            self.generate_reference_status_var.set("参考图修订不可用")
            return
        existing_index = next(
            (
                index
                for index, reference in enumerate(self._generation_references)
                if reference.revision.revision_id == revision.revision_id
            ),
            None,
        )
        if existing_index is not None:
            self._refresh_generation_reference_list(existing_index)
            self.generate_reference_status_var.set(
                f"这张图片已经在参考图列表中 · {existing_index + 1}/"
                f"{len(self._generation_references)}"
            )
            return
        if len(self._generation_references) >= 4:
            self.generate_reference_status_var.set("最多只能添加 4 张参考图，请先移除一张")
            return
        reference_role = role or GENERATION_REFERENCE_ROLES.get(
            self.generate_reference_role_var.get(),
            "content",
        )
        reference_strength = strength
        if reference_strength is None:
            try:
                reference_strength = int(self.generate_reference_strength_var.get()) / 100
            except (tk.TclError, TypeError, ValueError):
                reference_strength = 0.5
        self._generation_references.append(
            GenerationReference(
                revision=revision,
                role=reference_role,
                strength=reference_strength,
            )
        )
        self._refresh_generation_reference_list(len(self._generation_references) - 1)
        self._schedule_config_save()

    def _update_selected_generation_reference_settings(
        self,
        _event: tk.Event[tk.Misc] | None = None,
    ) -> bool:
        index = self._selected_generation_reference_index()
        if index is None:
            return True
        role = GENERATION_REFERENCE_ROLES.get(self.generate_reference_role_var.get())
        try:
            strength = int(self.generate_reference_strength_var.get())
        except (tk.TclError, TypeError, ValueError):
            self.generate_reference_status_var.set("参考图强度必须是 0 到 100 的整数")
            return False
        if role is None or not 0 <= strength <= 100:
            self.generate_reference_status_var.set("参考图角色或强度无效")
            return False
        current = self._generation_references[index]
        updated = GenerationReference(
            revision=current.revision,
            role=role,
            strength=strength / 100,
        )
        if updated != current:
            self._generation_references[index] = updated
            self._refresh_generation_reference_list(index)
            self._schedule_config_save()
        return True

    def _remove_selected_generation_reference(self) -> None:
        index = self._selected_generation_reference_index()
        if index is None:
            self.generate_reference_status_var.set("请先选择要移除的参考图")
            return
        self._generation_references.pop(index)
        self._refresh_generation_reference_list(
            None
            if not self._generation_references
            else min(index, len(self._generation_references) - 1)
        )
        self._schedule_config_save()

    def _clear_generation_reference(self) -> None:
        future = self._generation_reference_future
        if future is not None and not future.done():
            future.cancel()
        self._generation_reference_future = None
        self._generation_reference_revision = None
        self._generation_references.clear()
        self.generation_reference_revision_id = ""
        self._refresh_generation_reference_list()
        self._schedule_config_save()

    def _restore_generation_reference(self) -> None:
        settings = tuple(self.generation_reference_settings)
        if not settings and self.generation_reference_revision_id:
            settings = (
                (
                    self.generation_reference_revision_id,
                    GENERATION_REFERENCE_ROLES.get(
                        self.generate_reference_role_var.get(),
                        "content",
                    ),
                    self.generate_reference_strength_var.get() / 100,
                ),
            )
        if not settings:
            self._refresh_generation_reference_list()
            return
        restored: list[GenerationReference] = []
        for revision_id, role, strength in settings[:4]:
            try:
                revision = self.asset_repository.get_revision(revision_id)
                self.asset_repository.resolve_content(revision_id, verify=False)
                restored.append(
                    GenerationReference(
                        revision=revision,
                        role=role,
                        strength=strength,
                    )
                )
            except Exception:
                continue
        self._generation_references = restored
        self._refresh_generation_reference_list(0 if restored else None)
        if len(restored) != len(settings):
            self.generate_reference_status_var.set(
                f"已恢复 {len(restored)} 张参考图；失效项目已移除"
            )
            self._schedule_config_save()

    def _update_generation_provider_capability_label(self) -> None:
        try:
            profile = self.generation_provider_catalog.by_display_name(
                self.generate_provider_var.get()
            )
        except KeyError:
            self.generate_provider_capability_var.set("服务配置无效")
            self._update_generation_buttons()
            return
        manifest = profile.manifest
        model_id = profile.model_id
        capabilities = ["文生图"]
        if manifest.supports(ProviderCapability.NEGATIVE_PROMPT, model_id):
            capabilities.append("负面提示词")
        if manifest.supports(ProviderCapability.REFERENCE_IMAGE, model_id):
            capabilities.append("参考图")
        if manifest.supports(ProviderCapability.SEED, model_id):
            capabilities.append("固定种子")
        if manifest.supports(ProviderCapability.STYLE_PRESET, model_id):
            capabilities.append("风格预设")
        if manifest.supports(ProviderCapability.VARIATION, model_id):
            capabilities.append("候选变体")
        location = "本地" if manifest.offline else "联网"
        self.generate_provider_capability_var.set(
            f"{profile.status_detail} · {' / '.join(capabilities)} · {location}"
        )
        self._update_generation_buttons()

    def _generation_provider_selected(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._update_generation_provider_capability_label()
        self._schedule_config_save()

    def _authorize_generation_request(
        self,
        profile: GenerationProviderProfile,
        request: GenerationRequest,
        requirements: AIExecutionRequirements,
    ) -> bool:
        if requirements.is_directly_allowed:
            self.ai_authorization_gate.authorize_execution(requirements)
            return True

        upload_authorized = False
        paid_authorized = False
        if requirements.remote_image_upload:
            uploaded_revisions = (
                (request.variation_revision,)
                if request.variation_revision is not None
                else tuple(reference.revision for reference in request.all_references)
            )
            reference_names: list[str] = []
            for uploaded_revision in uploaded_revisions:
                try:
                    reference_names.append(
                        self.asset_repository.get_asset(uploaded_revision.asset_id).name
                    )
                except Exception:
                    reference_names.append(uploaded_revision.revision_id)
            reference_summary = "、".join(reference_names[:3]) or "所选参考图"
            if len(reference_names) > 3:
                reference_summary += f" 等 {len(reference_names)} 张"
            upload_authorized = messagebox.askyesno(
                "确认上传参考图",
                f"服务：{profile.display_name}\n"
                f"图片：{len(uploaded_revisions)} 张（{reference_summary}）\n\n"
                "本次任务需要将这些参考图上传到服务商。仅授权本次请求，是否继续？",
            )
            if not upload_authorized:
                self.generate_status_var.set("已取消：未授权上传参考图")
                self.generate_candidates_var.set("任务未提交，图片没有离开本机")
                return False
        if requirements.estimated_cost_minor > 0:
            amount = requirements.estimated_cost_minor / 100
            paid_authorized = messagebox.askyesno(
                "确认生成费用",
                f"服务：{profile.display_name}\n"
                f"本次预计最高费用：{requirements.currency_code} {amount:.2f}\n\n"
                "费用授权仅对本次不可变请求有效，是否继续？",
            )
            if not paid_authorized:
                self.generate_status_var.set("已取消：未授权本次生成费用")
                self.generate_candidates_var.set("任务未提交，也不会产生本次费用")
                return False
        authorization = self.ai_authorization_gate.issue_authorization(
            requirements,
            authorize_remote_image_upload=upload_authorized,
            authorize_paid_execution=paid_authorized,
        )
        self.ai_authorization_gate.authorize_execution(requirements, authorization)
        return True

    def _submit_generation_request(
        self,
        profile: GenerationProviderProfile,
        request: GenerationRequest,
    ) -> ManagedJobSnapshot | None:
        if not profile.selectable:
            self.generate_status_var.set(f"当前服务不可用：{profile.status_detail}")
            return None
        if profile.provider_id != self.ai_job_manager.provider_id:
            self.generate_status_var.set("当前服务尚未配置执行器，任务未提交")
            return None
        requirements = AIExecutionRequirements.for_request(
            profile.manifest,
            request,
            remote_image_upload=(
                (bool(request.all_references) or request.variation_revision is not None)
                and not profile.manifest.offline
            ),
            estimated_cost_minor=profile.estimated_cost_minor(request.count),
            currency_code=profile.currency_code,
        )
        try:
            if not self._authorize_generation_request(profile, request, requirements):
                return None
            snapshot = self.ai_job_manager.submit_generation(request)
        except Exception:
            self.generate_status_var.set("无法创建生成任务，基础看图与转图功能不受影响")
            self.generate_candidates_var.set("任务未提交，也不会自动重试或重复计费")
            return None
        self._last_generation_request = request
        self._last_generation_profile = profile
        return snapshot

    def _begin_generation_tracking(
        self,
        snapshot: ManagedJobSnapshot,
        request: GenerationRequest,
        profile: GenerationProviderProfile,
        *,
        retry: bool = False,
    ) -> None:
        self._mock_generation_job_id = snapshot.job_id
        self._mock_generation_presented_job_id = None
        self._mock_generation_import_future = None
        action = "重试任务" if retry else "任务"
        self.generate_status_var.set(f"{profile.display_name} · {action}正在后台提交")
        self.generate_candidates_var.set("正在等待服务生成候选，可随时取消…")
        self._sync_ai_task_to_tray(
            snapshot,
            title="候选变体" if request.variation_revision is not None else "图片生成",
            detail=f"{profile.display_name} · {action}正在后台提交",
        )
        self._remember_generation_prompt(request.prompt)
        self.mode_router.update_state(
            {
                "prompt": request.prompt,
                "size": f"{request.width}x{request.height}",
                "count": request.count,
                "seed": request.seed,
                "style_preset": request.style_preset,
                "negative_prompt": request.negative_prompt,
                "reference_revision_id": (
                    None
                    if request.reference_revision is None
                    else request.reference_revision.revision_id
                ),
                "reference_revision_ids": tuple(
                    reference.revision.revision_id for reference in request.all_references
                ),
                "reference_count": len(request.all_references),
                "reference_roles": tuple(reference.role for reference in request.all_references),
                "reference_strengths": tuple(
                    reference.strength for reference in request.all_references
                ),
                "reference_role": request.reference_role,
                "reference_strength": request.reference_strength,
                "variation_of_revision_id": (
                    None
                    if request.variation_revision is None
                    else request.variation_revision.revision_id
                ),
                "variation_strength": request.variation_strength,
                "provider_id": profile.provider_id,
                "model_id": profile.model_id,
                "job_id": snapshot.job_id,
            },
            mode=ModeId.GENERATE,
        )
        self._schedule_config_save()
        self._update_generation_buttons()
        self._mock_generation_after = self.after(40, self._finish_mock_generation)

    def _start_mock_generation(self) -> None:
        prompt = self.generate_prompt_var.get().strip()
        if not prompt:
            self.generate_status_var.set("请先输入提示词")
            return
        import_future = self._mock_generation_import_future
        if import_future is not None and not import_future.done():
            self.generate_status_var.set("上一批候选正在安全入库，请稍候")
            return
        reference_future = self._generation_reference_future
        if reference_future is not None and not reference_future.done():
            self.generate_status_var.set("参考图正在安全登记，请稍候")
            return
        negative_prompt = self.generate_negative_prompt_var.get().strip()
        if len(negative_prompt) > 10_000:
            self.generate_status_var.set("负面提示词不能超过 10,000 个字符")
            return
        if not self._update_selected_generation_reference_settings():
            self.generate_status_var.set("请先修正当前参考图的角色或强度")
            return
        references = tuple(self._generation_references)
        primary_reference = references[0] if references else None
        reference_revision = None if primary_reference is None else primary_reference.revision
        try:
            width_text, height_text = self.generate_size_var.get().split("×", 1)
            width, height = int(width_text.strip()), int(height_text.strip())
            count = max(1, min(4, int(self.generate_count_var.get())))
        except (tk.TclError, TypeError, ValueError):
            self.generate_status_var.set("尺寸或数量设置无效")
            return
        style_label = self.generate_style_preset_var.get()
        style_preset = GENERATION_STYLE_PRESETS.get(style_label)
        if style_preset is None:
            self.generate_status_var.set("风格预设设置无效")
            return
        seed: int | None = None
        if self.generate_seed_enabled_var.get():
            try:
                seed = int(self.generate_seed_var.get().strip())
            except ValueError:
                self.generate_status_var.set("固定种子必须是 0 到 4,294,967,295 的整数")
                return
            if not 0 <= seed <= 4_294_967_295:
                self.generate_status_var.set("固定种子必须是 0 到 4,294,967,295 的整数")
                return
        try:
            profile = self.generation_provider_catalog.by_display_name(
                self.generate_provider_var.get()
            )
        except KeyError:
            self.generate_status_var.set("请选择有效的生图服务")
            return
        manifest = profile.manifest
        model_id = profile.model_id
        if negative_prompt and not manifest.supports(
            ProviderCapability.NEGATIVE_PROMPT,
            model_id,
        ):
            self.generate_status_var.set("当前服务不支持负面提示词")
            return
        if references and not manifest.supports(
            ProviderCapability.REFERENCE_IMAGE,
            model_id,
        ):
            self.generate_status_var.set("当前服务不支持参考图生成")
            return
        if seed is not None and not manifest.supports(
            ProviderCapability.SEED,
            model_id,
        ):
            self.generate_status_var.set("当前服务不支持固定种子")
            return
        if style_preset and not manifest.supports(
            ProviderCapability.STYLE_PRESET,
            model_id,
        ):
            self.generate_status_var.set("当前服务不支持风格预设")
            return

        if self._mock_generation_after is not None:
            self.after_cancel(self._mock_generation_after)
            self._mock_generation_after = None
        request = GenerationRequest(
            prompt=prompt,
            model=model_id,
            width=width,
            height=height,
            count=count,
            seed=seed,
            style_preset=style_preset,
            negative_prompt=negative_prompt,
            reference_revision=reference_revision,
            reference_role=("content" if primary_reference is None else primary_reference.role),
            reference_strength=(0.5 if primary_reference is None else primary_reference.strength),
            additional_references=references[1:],
        )
        snapshot = self._submit_generation_request(profile, request)
        if snapshot is None:
            return
        self._begin_generation_tracking(snapshot, request, profile)

    def _retry_failed_generation(self) -> None:
        request = self._last_generation_request
        profile = self._last_generation_profile
        if request is None or profile is None:
            self.generate_status_var.set("没有可重试的失败任务")
            return
        job_id = self._mock_generation_job_id
        if job_id is None:
            self.generate_status_var.set("没有可重试的失败任务")
            return
        try:
            current = self.ai_job_manager.get_job(job_id)
        except Exception:
            self.generate_status_var.set("失败任务记录不可用")
            return
        if current.status not in {JobStatus.FAILED, JobStatus.UNKNOWN}:
            self.generate_status_var.set("只有失败或状态未知的任务可以显式重试")
            return
        retry_request = replace(
            request,
            request_id=f"req_{uuid.uuid4().hex}",
            idempotency_key=None,
        )
        snapshot = self._submit_generation_request(profile, retry_request)
        if snapshot is None:
            return
        self._begin_generation_tracking(snapshot, retry_request, profile, retry=True)

    def _finish_mock_generation(self) -> None:
        self._mock_generation_after = None
        job_id = self._mock_generation_job_id
        if job_id is None:
            return
        provider_label = (
            self._last_generation_profile.display_name
            if self._last_generation_profile is not None
            else "生成服务"
        )
        import_future = self._mock_generation_import_future
        if import_future is not None:
            if not import_future.done():
                self._mock_generation_after = self.after(60, self._finish_mock_generation)
                return
            self._mock_generation_import_future = None
            try:
                imported = import_future.result()
            except Exception:
                self.generate_status_var.set("候选安全校验或入库失败")
                self.generate_candidates_var.set("未采用服务结果；原图片和基础功能不受影响")
                self._update_generation_buttons()
                return
            if self._mock_generation_job_id != job_id or not imported:
                self._update_generation_buttons()
                return
            snapshot = self.ai_job_manager.get_job(job_id)
            self._sync_ai_task_to_tray(snapshot)
            self._present_generated_candidates(snapshot, imported)
            return

        snapshot = self.ai_job_manager.get_job(job_id)
        self._sync_ai_task_to_tray(snapshot)
        if snapshot.status in {JobStatus.SUBMITTING, JobStatus.QUEUED, JobStatus.RUNNING}:
            if snapshot.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                self.ai_job_manager.refresh(job_id)
            status_labels = {
                JobStatus.SUBMITTING: "正在安全提交",
                JobStatus.QUEUED: "已排队",
                JobStatus.RUNNING: "正在生成",
            }
            self.generate_status_var.set(f"{provider_label} · {status_labels[snapshot.status]}")
            self._mock_generation_after = self.after(80, self._finish_mock_generation)
            return
        if snapshot.status is JobStatus.CANCELLED:
            self.generate_status_var.set(f"{provider_label} · 任务已取消")
            self.generate_candidates_var.set("未生成候选结果")
            self._update_generation_buttons()
            return
        if snapshot.status in {JobStatus.FAILED, JobStatus.UNKNOWN}:
            message = snapshot.error.message if snapshot.error is not None else "未知错误"
            self.generate_status_var.set(
                "任务状态未知" if snapshot.status is JobStatus.UNKNOWN else "生成任务失败"
            )
            self.generate_candidates_var.set(message)
            self._update_generation_buttons()
            return
        if snapshot.status is not JobStatus.SUCCEEDED:
            self.generate_status_var.set(f"任务状态：{snapshot.status.value}")
            self._update_generation_buttons()
            return
        if self._mock_generation_presented_job_id == job_id:
            return
        self.generate_status_var.set("候选已生成，正在安全解码并导入资源库")
        self._mock_generation_import_future = self._asset_executor.submit(
            self._import_generated_candidates,
            snapshot,
        )
        self._mock_generation_after = self.after(30, self._finish_mock_generation)

    def _import_generated_candidates(
        self,
        snapshot: ManagedJobSnapshot,
    ) -> tuple[AssetRevision, ...]:
        imported: list[AssetRevision] = []
        for asset in snapshot.outputs:
            provider_revision = asset.current_revision
            source = _local_file_uri_path(provider_revision.content_uri)
            imported.append(
                self.asset_repository.import_managed(
                    source,
                    provenance=provider_revision.provenance,
                    asset_id=asset.asset_id,
                    asset_name=asset.name,
                    expected_media_type=provider_revision.media_type,
                )
            )
        return tuple(imported)

    def _remember_generation_prompt(self, prompt: str) -> None:
        normalized = prompt.strip()
        if not normalized:
            return
        self.generation_prompt_history = [
            item for item in self.generation_prompt_history if item != normalized
        ]
        self.generation_prompt_history.insert(0, normalized)
        del self.generation_prompt_history[20:]
        self.generate_prompt_box.configure(values=tuple(self.generation_prompt_history))
        self._schedule_config_save()

    def _generation_candidate_item(
        self,
        revision_id: str,
    ) -> GenerationCandidateItem | None:
        try:
            revision = self.asset_repository.get_revision(revision_id)
            asset = self.asset_repository.get_asset(revision.asset_id)
            path = self.asset_repository.resolve_content(revision_id, verify=False)
        except Exception:
            return None
        prompt_value = revision.provenance.parameters.get("prompt", "")
        prompt = prompt_value if isinstance(prompt_value, str) else ""
        dimensions = (
            f" · {revision.width}×{revision.height}"
            if revision.width is not None and revision.height is not None
            else ""
        )
        return GenerationCandidateItem(
            revision_id=revision.revision_id,
            asset_id=revision.asset_id,
            path=path,
            label=f"{asset.name}{dimensions}",
            prompt=prompt,
            favorite=revision_id in self.generation_favorite_revision_ids,
        )

    def _restore_generation_candidate_history(
        self,
        *,
        selected_revision_id: str | None = None,
    ) -> None:
        requested_recent = tuple(self.generation_candidate_revision_ids)
        requested_favorites = tuple(self.generation_favorite_revision_ids)
        requested = tuple(dict.fromkeys(requested_favorites + requested_recent))
        all_items = tuple(
            item
            for revision_id in requested
            if (item := self._generation_candidate_item(revision_id)) is not None
        )
        available = {item.revision_id for item in all_items}
        self.generation_candidate_revision_ids = [
            revision_id for revision_id in requested_recent if revision_id in available
        ]
        self.generation_favorite_revision_ids = [
            revision_id for revision_id in requested_favorites if revision_id in available
        ]
        favorite_ids = set(self.generation_favorite_revision_ids)
        all_items = tuple(
            replace(item, favorite=item.revision_id in favorite_ids) for item in all_items
        )
        items = (
            tuple(item for item in all_items if item.favorite)
            if self.generate_favorites_only_var.get()
            else all_items
        )
        self._generated_candidates_by_revision = {item.revision_id: item for item in items}
        preferred = selected_revision_id or self._selected_generated_revision_id
        self.generation_candidate_grid.set_candidates(
            items,
            selected_revision_id=preferred,
        )
        if items:
            self.mode_router.update_state(
                {
                    "candidate_revision_ids": tuple(item.revision_id for item in items),
                    "candidate_count": len(items),
                    "favorite_candidate_revision_ids": tuple(self.generation_favorite_revision_ids),
                },
                mode=ModeId.GENERATE,
            )
            selected = self.generation_candidate_grid.selected_revision_id
            assert selected is not None
            self._select_generated_candidate(selected)
            favorite_count = len(self.generation_favorite_revision_ids)
            filter_label = "收藏" if self.generate_favorites_only_var.get() else "历史"
            self.generate_candidates_var.set(
                f"已显示 {len(items)} 个{filter_label}候选 · 共收藏 {favorite_count} 个。"
            )
        else:
            self._selected_generated_revision_id = None
            self.mode_router.set_handoff(None, None, mode=ModeId.GENERATE)
            self.mode_router.update_state(
                {
                    "selected_candidate_revision_id": None,
                    "candidate_revision_ids": (),
                    "candidate_count": 0,
                    "favorite_candidate_revision_ids": tuple(self.generation_favorite_revision_ids),
                },
                mode=ModeId.GENERATE,
            )
            self.generate_candidates_var.set(
                "尚无收藏候选；可关闭“仅看收藏”查看历史。"
                if self.generate_favorites_only_var.get()
                else "尚无候选结果。"
            )
            self._update_generation_buttons()
        if (
            tuple(self.generation_candidate_revision_ids) != requested_recent
            or tuple(self.generation_favorite_revision_ids) != requested_favorites
        ):
            self._schedule_config_save()

    def _remember_generated_candidates(
        self,
        imported: tuple[AssetRevision, ...],
    ) -> None:
        newest = [revision.revision_id for revision in imported]
        merged = newest + [
            revision_id
            for revision_id in self.generation_candidate_revision_ids
            if revision_id not in newest
        ]
        self.generation_candidate_revision_ids = merged[:12]
        self.generate_favorites_only_var.set(False)
        self._restore_generation_candidate_history()
        self._schedule_config_save()

    def _toggle_generated_candidate_favorite(self) -> None:
        revision_id = self._selected_generated_revision_id
        if revision_id is None or revision_id not in self._generated_candidates_by_revision:
            self.generate_status_var.set("请先选择一个生成候选")
            return
        if revision_id in self.generation_favorite_revision_ids:
            self.generation_favorite_revision_ids = [
                item for item in self.generation_favorite_revision_ids if item != revision_id
            ]
            action = "已取消收藏"
        else:
            self.generation_favorite_revision_ids.insert(0, revision_id)
            del self.generation_favorite_revision_ids[128:]
            action = "已收藏"
        self._restore_generation_candidate_history(selected_revision_id=revision_id)
        self.generate_status_var.set(f"{action}；收藏状态已保存在本机")
        self._schedule_config_save()

    def _remove_generated_candidate(self) -> None:
        revision_id = self._selected_generated_revision_id
        item = (
            None if revision_id is None else self._generated_candidates_by_revision.get(revision_id)
        )
        if revision_id is None or item is None:
            self.generate_status_var.set("请先选择一个生成候选")
            return
        self.generation_candidate_revision_ids = [
            value for value in self.generation_candidate_revision_ids if value != revision_id
        ]
        self.generation_favorite_revision_ids = [
            value for value in self.generation_favorite_revision_ids if value != revision_id
        ]
        self._selected_generated_revision_id = None
        self._restore_generation_candidate_history()
        self.generate_status_var.set(f"已从候选工作区删除：{item.label}")
        self.generate_candidates_var.set("素材修订仍安全保留，用于任务来源与跨模式追溯。")
        self._schedule_config_save()

    def _generation_favorites_filter_changed(self) -> None:
        self._restore_generation_candidate_history()
        state = "已仅显示收藏候选" if self.generate_favorites_only_var.get() else "已显示全部候选"
        self.generate_status_var.set(state)
        self._schedule_config_save()

    def _toggle_generation_favorites_filter(self) -> None:
        self.generate_favorites_only_var.set(not self.generate_favorites_only_var.get())
        self._generation_favorites_filter_changed()

    def _select_generated_candidate(self, revision_id: str) -> None:
        item = self._generated_candidates_by_revision.get(revision_id)
        if item is None:
            return
        try:
            revision = self.asset_repository.get_revision(revision_id)
            asset = self.asset_repository.get_asset(revision.asset_id)
        except Exception:
            return
        self._selected_generated_revision_id = revision_id
        self.generation_candidate_grid.select(revision_id, notify=False)
        self.mode_router.set_handoff(
            AssetHandoff(
                id=revision.asset_id,
                uri=item.path.as_uri(),
                media_type=revision.media_type,
                display_name=asset.name,
                metadata={"source": "generation", "managed": True},
            ),
            RevisionHandoff(
                id=revision.revision_id,
                asset_id=revision.asset_id,
                number=revision.sequence,
                metadata={
                    "provider": revision.provenance.provider_id,
                    "request_id": revision.provenance.request_id,
                    "cost_minor": 0,
                    "currency": "CNY",
                },
            ),
            mode=ModeId.GENERATE,
        )
        self.mode_router.update_state(
            {"selected_candidate_revision_id": revision_id},
            mode=ModeId.GENERATE,
        )
        self.generate_candidates_var.set(f"已选择：{item.label}")
        self._update_generation_buttons()

    def _generate_selected_candidate_variation(self) -> None:
        revision_id = self._selected_generated_revision_id
        if revision_id is None:
            self.generate_status_var.set("请先选择一个生成候选")
            return
        try:
            source = self.asset_repository.get_revision(revision_id)
            profile = self.generation_provider_catalog.by_display_name(
                self.generate_provider_var.get()
            )
        except (KeyError, ValueError):
            self.generate_status_var.set("所选候选或生成服务不可用")
            return
        if not profile.manifest.supports(
            ProviderCapability.VARIATION,
            profile.model_id,
        ):
            self.generate_status_var.set("当前服务不支持候选变体")
            return
        parameters = source.provenance.parameters
        prompt_value = parameters.get("prompt", "")
        prompt = prompt_value if isinstance(prompt_value, str) else ""
        if not prompt.strip():
            prompt = "基于所选候选生成视觉变体"
        negative_value = parameters.get("negative_prompt", "")
        negative_prompt = negative_value if isinstance(negative_value, str) else ""
        style_value = parameters.get("style_preset", "")
        style_preset = style_value if isinstance(style_value, str) else ""
        seed_value = parameters.get("seed")
        seed = (
            (seed_value + 1) % 4_294_967_296
            if isinstance(seed_value, int) and not isinstance(seed_value, bool)
            else None
        )
        try:
            count = max(1, min(4, int(self.generate_count_var.get())))
        except (tk.TclError, TypeError, ValueError):
            count = 1
        request = GenerationRequest(
            prompt=prompt,
            model=profile.model_id,
            width=source.width or 1024,
            height=source.height or 1024,
            count=count,
            seed=seed,
            style_preset=style_preset,
            negative_prompt=negative_prompt,
            variation_revision=source,
            variation_strength=0.35,
        )
        snapshot = self._submit_generation_request(profile, request)
        if snapshot is None:
            return
        self._begin_generation_tracking(snapshot, request, profile)
        self.generate_status_var.set("正在基于所选候选生成变体")
        self.generate_candidates_var.set(f"来源修订：{source.revision_id} · 变化强度 35%")

    def _send_generated_candidate_to_mode(self, mode: ModeId) -> None:
        revision_id = self._selected_generated_revision_id
        item = (
            None if revision_id is None else self._generated_candidates_by_revision.get(revision_id)
        )
        if revision_id is None or item is None:
            self.generate_status_var.set("请先选择一个生成候选")
            return
        try:
            revision = self.asset_repository.get_revision(revision_id)
            asset = self.asset_repository.get_asset(revision.asset_id)
        except Exception as error:
            self.generate_status_var.set(f"无法读取生成候选：{error}")
            return
        asset_handoff = AssetHandoff(
            id=revision.asset_id,
            uri=item.path.as_uri(),
            media_type=revision.media_type,
            display_name=asset.name,
            metadata={"source": "generation", "managed": True},
        )
        revision_handoff = RevisionHandoff(
            id=revision.revision_id,
            asset_id=revision.asset_id,
            number=revision.sequence,
            metadata={
                "provider": revision.provenance.provider_id,
                "request_id": revision.provenance.request_id,
            },
        )
        if mode is ModeId.CONVERT:
            self._conversion_handoff_override = (
                item.path,
                asset_handoff,
                revision_handoff,
            )
            self.mode_router.switch(ModeId.CONVERT)
            self.mode_router.set_handoff(
                asset_handoff,
                revision_handoff,
                mode=ModeId.CONVERT,
            )
            self.conversion_job_status_var.set("生成候选已加入转换队列，可继续选择格式和尺寸")
            return
        if not self._confirm_discard_changes():
            return
        self.mode_router.switch(mode)
        self._asset_identity_open_hint = (item.path, revision)
        self.open_paths([item.path], confirm=False)
        self.mode_router.set_handoff(asset_handoff, revision_handoff, mode=mode)
        self.status_var.set(
            "正在打开生成候选并进入改图模式" if mode is ModeId.EDIT else "正在打开生成候选"
        )

    def _update_generation_buttons(self) -> None:
        provider_active = False
        if self._mock_generation_job_id is not None:
            try:
                provider_active = not self.ai_job_manager.get_job(
                    self._mock_generation_job_id
                ).status.terminal
            except Exception:
                provider_active = False
        import_active = bool(
            self._mock_generation_import_future is not None
            and not self._mock_generation_import_future.done()
        )
        reference_import_active = bool(
            self._generation_reference_future is not None
            and not self._generation_reference_future.done()
        )
        active = provider_active or import_active or reference_import_active
        try:
            selected_profile = self.generation_provider_catalog.by_display_name(
                self.generate_provider_var.get()
            )
            provider_ready = (
                selected_profile.selectable
                and selected_profile.provider_id == self.ai_job_manager.provider_id
            )
        except KeyError:
            provider_ready = False
        self.mock_generate_button.state(
            ("!disabled",) if not active and provider_ready else ("disabled",)
        )
        self.mock_cancel_button.state(("!disabled",) if provider_active else ("disabled",))
        retryable = False
        if self._mock_generation_job_id is not None and not active:
            try:
                retryable = self.ai_job_manager.get_job(self._mock_generation_job_id).status in {
                    JobStatus.FAILED,
                    JobStatus.UNKNOWN,
                }
            except Exception:
                retryable = False
        self.mock_retry_button.state(("!disabled",) if retryable else ("disabled",))
        candidate_state = (
            ("!disabled",)
            if self._selected_generated_revision_id is not None and not active
            else ("disabled",)
        )
        self.generated_open_button.state(candidate_state)
        self.generated_edit_button.state(candidate_state)
        self.generated_convert_button.state(candidate_state)
        self.generated_variation_button.state(candidate_state)
        self.generated_favorite_button.state(candidate_state)
        self.generated_remove_button.state(candidate_state)
        selected_is_favorite = (
            self._selected_generated_revision_id in self.generation_favorite_revision_ids
            if self._selected_generated_revision_id is not None
            else False
        )
        self.generated_favorite_button.configure(
            text="取消收藏" if selected_is_favorite else "收藏候选"
        )
        self.generated_favorites_only_checkbutton.state(("disabled",) if active else ("!disabled",))

        reference_control_state = ("disabled",) if active else ("!disabled",)
        can_add_reference = not active and len(self._generation_references) < 4
        self.generate_reference_choose_button.state(
            ("!disabled",) if can_add_reference else ("disabled",)
        )
        current_reference_state = (
            ("!disabled",)
            if can_add_reference and self._current_asset_revision is not None
            else ("disabled",)
        )
        self.generate_reference_current_button.state(current_reference_state)
        has_reference = bool(self._generation_references)
        self.generate_reference_remove_button.state(
            ("!disabled",) if not active and has_reference else ("disabled",)
        )
        clear_reference_state = ("!disabled",) if not active and has_reference else ("disabled",)
        self.generate_reference_clear_button.state(clear_reference_state)
        self.generate_reference_listbox.configure(state=tk.DISABLED if active else tk.NORMAL)
        self.generate_negative_prompt_entry.state(reference_control_state)
        reference_setting_state = ("!disabled",) if not active and has_reference else ("disabled",)
        self.generate_reference_strength_spinbox.state(reference_setting_state)
        self.generate_reference_role_box.state(
            ("!disabled", "readonly") if not active and has_reference else ("disabled",)
        )
        self.generate_size_box.state(("disabled",) if active else ("!disabled", "readonly"))
        self.generate_count_spinbox.state(reference_control_state)
        self.generate_style_box.state(("disabled",) if active else ("!disabled", "readonly"))
        self.generate_seed_checkbutton.state(reference_control_state)
        self.generate_seed_entry.state(reference_control_state)
        self.generate_provider_box.state(("disabled",) if active else ("!disabled", "readonly"))

    def _present_generated_candidates(
        self,
        snapshot: ManagedJobSnapshot,
        imported: tuple[AssetRevision, ...],
    ) -> None:
        if not imported:
            return
        names = "、".join(asset.name for asset in snapshot.outputs)
        provider_label = (
            self._last_generation_profile.display_name
            if self._last_generation_profile is not None
            else "生成服务"
        )
        self.generate_status_var.set(f"{provider_label} · {len(imported)} 个候选已安全入库")
        self.generate_candidates_var.set(f"本批候选：{names}\n结果已安全解码并保存为本地资源修订。")
        self._remember_generated_candidates(imported)
        revision = imported[0]
        self._select_generated_candidate(revision.revision_id)
        self.mode_router.update_state(
            {
                "job_id": snapshot.job_id,
                "candidate_revision_ids": tuple(item.revision_id for item in imported),
                "candidate_count": len(imported),
            },
            mode=ModeId.GENERATE,
        )
        self._mock_generation_presented_job_id = snapshot.job_id
        self._sync_ai_task_to_tray(
            snapshot,
            detail=f"{len(imported)} 个候选已安全入库",
        )
        self._update_generation_buttons()

    def _cancel_mock_generation(self) -> None:
        if self._mock_generation_job_id is None:
            self.generate_status_var.set("当前没有可取消的任务")
            return
        if self._mock_generation_after is not None:
            self.after_cancel(self._mock_generation_after)
            self._mock_generation_after = None
        snapshot = self.ai_job_manager.cancel(self._mock_generation_job_id)
        self.generate_status_var.set(
            "生成任务已取消"
            if snapshot.status is JobStatus.CANCELLED
            else f"任务已结束：{snapshot.status.value}"
        )
        self.generate_candidates_var.set("未生成候选结果")
        self._sync_ai_task_to_tray(snapshot)
        self._update_generation_buttons()

    def _sync_mode_handoff(self) -> None:
        revision = self._current_asset_revision
        path = self._resolved_current_asset_path()
        if self.document is None or revision is None or path is None:
            self.mode_router.set_handoff(None)
            return
        self.mode_router.set_handoff(
            AssetHandoff(
                id=revision.asset_id,
                uri=path.as_uri(),
                media_type=revision.media_type,
                display_name=path.name,
                metadata={
                    "width": revision.width,
                    "height": revision.height,
                    "storage_kind": revision.storage_kind.value,
                },
            ),
            RevisionHandoff(
                id=revision.revision_id,
                asset_id=revision.asset_id,
                number=revision.sequence,
                dirty=self.document.dirty,
                project_uri=(
                    self.document.project_path.as_uri()
                    if self.document.project_path is not None
                    else None
                ),
            ),
        )

    def _resolved_current_asset_path(self) -> Path | None:
        document = self.document
        revision = self._current_asset_revision
        if document is None or document.source_path is None or revision is None:
            return None
        fingerprint = document.source_fingerprint
        if fingerprint is None or not fingerprint.matches(document.source_path):
            return None
        try:
            resolved = self.asset_repository.resolve_content(
                revision.revision_id,
                verify=False,
            )
        except Exception:
            return None
        if not self._same_path(resolved, document.source_path):
            return None
        return resolved

    def _invalidate_asset_registration(self, *, clear_relocation_hint: bool = True) -> None:
        self._asset_registration_generation += 1
        future = self._asset_registration_future
        if future is not None:
            future.cancel()
        self._asset_registration_future = None
        self._asset_registration_path = None
        self._current_asset_revision = None
        self._last_asset_registration_error = None
        if clear_relocation_hint:
            self._asset_relocation_hint = None
            self._asset_identity_open_hint = None
        self.mode_router.set_handoff(None)

    def _schedule_asset_registration(
        self,
        path: Path,
        *,
        parent_revision: AssetRevision | None = None,
    ) -> None:
        if self._services_shutdown or self.document is None:
            return
        try:
            source = path.resolve(strict=False)
        except OSError:
            source = path.absolute()

        identity_hint = self._asset_identity_open_hint
        self._asset_identity_open_hint = None
        if identity_hint is not None and self._same_path(identity_hint[0], source):
            try:
                managed_path = self.asset_repository.resolve_content(
                    identity_hint[1].revision_id,
                    verify=False,
                )
            except Exception:
                managed_path = None
            if managed_path is not None and self._same_path(managed_path, source):
                self._asset_registration_generation += 1
                future = self._asset_registration_future
                if future is not None:
                    future.cancel()
                self._asset_registration_future = None
                self._asset_registration_path = None
                self._current_asset_revision = identity_hint[1]
                self._last_asset_registration_error = None
                self._sync_mode_handoff()
                if self.mode_router.active_mode is ModeId.CONVERT:
                    self._enqueue_current_conversion_handoff()
                return

        hint = self._asset_relocation_hint
        if parent_revision is None and hint is not None and self._same_path(hint[0], source):
            parent_revision = hint[1]
        self._asset_relocation_hint = None

        self._asset_registration_generation += 1
        generation = self._asset_registration_generation
        expected_document = self.document
        self._current_asset_revision = None
        self._last_asset_registration_error = None
        self.mode_router.set_handoff(None)

        provenance = None
        if parent_revision is not None:
            provenance = Provenance(
                origin="relocate",
                actor="user",
                source_revision_ids=(parent_revision.revision_id,),
                parameters={"path": str(source)},
            )
        future = self._asset_executor.submit(
            self.asset_repository.ensure_external,
            source,
            provenance=provenance,
            asset_id=(None if parent_revision is None else parent_revision.asset_id),
            parent_revision_id=(None if parent_revision is None else parent_revision.revision_id),
            asset_name=source.name,
        )
        self._asset_registration_future = future
        self._asset_registration_path = source
        self.after(
            10,
            self._poll_asset_registration,
            generation,
            source,
            expected_document,
            future,
        )

    def _poll_asset_registration(
        self,
        generation: int,
        path: Path,
        expected_document: Document,
        future: Future[AssetRevision],
    ) -> None:
        if self._services_shutdown or generation != self._asset_registration_generation:
            return
        if not future.done():
            self.after(
                10,
                self._poll_asset_registration,
                generation,
                path,
                expected_document,
                future,
            )
            return
        self._asset_registration_future = None
        self._asset_registration_path = None
        try:
            revision = future.result()
        except Exception as error:
            # Asset indexing is supplementary to opening the image.  Keep the
            # preview usable and avoid surfacing a worker failure as a Tk popup.
            self._last_asset_registration_error = error
            self._current_asset_revision = None
            self.mode_router.set_handoff(None)
            if self.mode_router.active_mode is ModeId.CONVERT:
                self._enqueue_current_conversion_handoff()
            return

        document = self.document
        if (
            document is not expected_document
            or document.source_path is None
            or not self._same_path(document.source_path, path)
        ):
            return
        self._current_asset_revision = revision
        if self._resolved_current_asset_path() is None:
            self._current_asset_revision = None
        self._sync_mode_handoff()
        if self.mode_router.active_mode is ModeId.CONVERT:
            self._enqueue_current_conversion_handoff()

    def _choose_conversion_files(self) -> tuple[str, ...]:
        return filedialog.askopenfilenames(
            title="添加要转换的图片",
            filetypes=[
                (
                    "图片文件",
                    "*.jpg *.jpeg *.png *.apng *.webp *.avif *.heic *.heif "
                    "*.bmp *.tif *.tiff *.gif",
                ),
                ("所有文件", "*.*"),
            ],
        )

    def _choose_conversion_folder(self) -> str | None:
        folder = filedialog.askdirectory(title="添加图片文件夹")
        return folder or None

    def _choose_conversion_output_directory(self) -> str | None:
        initial = self.conversion_output_dir_var.get().strip()
        if not initial and self.recent_export_directories:
            initial = self.recent_export_directories[0]
        folder = filedialog.askdirectory(
            title="选择转换输出文件夹",
            initialdir=initial or None,
        )
        if not folder:
            return None
        self.conversion_output_dir_var.set(folder)
        return folder

    def _choose_conversion_transparency_background(self) -> None:
        current = self._conversion_transparency_background() or "#ffffff"
        _rgb, selected = colorchooser.askcolor(
            color=current,
            title="选择透明区域填充颜色",
        )
        if not selected:
            return
        self.conversion_background_var.set(selected.lower())

    def _conversion_transparency_background(self) -> str | None:
        value = self.conversion_background_var.get().strip()
        if value in TRANSPARENCY_BACKGROUND_LABELS:
            return TRANSPARENCY_BACKGROUND_LABELS[value]
        return value or None

    def _activate_conversion_input(self, path: Path) -> bool:
        if self.image_path is not None:
            try:
                if self.image_path.resolve() == path.resolve():
                    return True
            except OSError:
                pass
        self.open_path(path)
        return self.image_path == path

    def _enqueue_current_conversion_handoff(self) -> None:
        if self._restoring_conversion_session:
            return
        override = self._conversion_handoff_override
        if override is not None:
            self._conversion_handoff_override = None
            path, asset, revision = override
            try:
                self.conversion_workspace.add_handoff(path, asset, revision)
            except (OSError, ValueError) as error:
                self.conversion_job_status_var.set(f"无法加入智能改图结果：{error}")
            return
        if self.document is None or self.document.source_path is None:
            return
        path = self.document.source_path
        if not path.exists():
            return
        context = self.mode_router.context_for(ModeId.CONVERT)
        try:
            revision = self._current_asset_revision
            resolved = self._resolved_current_asset_path()
            identity_matches = bool(
                revision is not None
                and resolved is not None
                and context.asset is not None
                and context.revision is not None
                and context.asset.id == revision.asset_id
                and context.revision.id == revision.revision_id
                and self._same_path(resolved, path)
            )
            if identity_matches:
                assert context.asset is not None
                self.conversion_workspace.add_handoff(
                    path,
                    context.asset,
                    context.revision,
                )
            else:
                self.conversion_workspace.add_files((path,))
                if context.asset is not None:
                    self.conversion_job_status_var.set(
                        "已忽略与当前文件不一致的资源身份，按真实文件加入队列"
                    )
        except (OSError, ValueError) as error:
            self.conversion_job_status_var.set(f"无法加入当前图片：{error}")

    def _conversion_queue_changed(self, items: tuple[object, ...]) -> None:
        self._invalidate_conversion_session()
        enabled = len(self.conversion_workspace.queue.enabled_items)
        self.mode_router.update_state(
            {"queue_count": len(items), "enabled_count": enabled},
            mode=ModeId.CONVERT,
        )
        if self._conversion_thread is None:
            self.conversion_job_status_var.set(
                "尚未添加转换任务" if not items else f"队列已准备 · {enabled} / {len(items)} 项启用"
            )
        self._update_conversion_buttons()

    def _conversion_control_state(self) -> ConversionControlState:
        current = ConversionControlState.from_draft(self.conversion_draft_history.current)
        png_palette_label = self.png_palette_var.get()
        if png_palette_label not in PNG_PALETTE_LABELS:
            raise ValueError("PNG 调色板选项无效")
        tiff_compression_label = self.tiff_compression_var.get()
        if tiff_compression_label not in TIFF_COMPRESSION_LABELS:
            raise ValueError("TIFF 压缩选项无效")
        return ConversionControlState(
            width_text=self.width_var.get(),
            height_text=self.height_var.get(),
            max_side_text=self.max_side_var.get(),
            allow_upscale=current.allow_upscale,
            watermark_text=self.watermark_text_var.get(),
            watermark_position=self.watermark_position_var.get(),
            watermark_opacity=int(self.watermark_opacity_var.get()),
            watermark_size_percent=int(self.watermark_size_var.get()),
            output_format=self.format_var.get(),
            quality=int(self.quality_var.get()),
            target_size_text=self.target_size_var.get(),
            webp_lossless=self.webp_lossless_var.get(),
            png_palette_colors=PNG_PALETTE_LABELS[png_palette_label],
            tiff_compression=TIFF_COMPRESSION_LABELS[tiff_compression_label],
            preserve_metadata=(
                self.preserve_metadata_var.get() and not self.batch_strip_metadata_var.get()
            ),
            rename_template=self.batch_rename_template_var.get(),
            conflict_policy=self.batch_conflict_var.get(),
            preserve_animation=self.batch_preserve_animation_var.get(),
            backup_on_overwrite=self.backup_on_overwrite_var.get(),
            transparency_background=self._conversion_transparency_background(),
            transparency_background_explicit=True,
        )

    def _apply_conversion_draft_to_controls(self) -> None:
        draft = self.conversion_draft_history.current
        state = ConversionControlState.from_draft(draft)
        self._syncing_document = True
        try:
            self.rotation = draft.rotation
            self.flip_h = draft.flip_horizontal
            self.flip_v = draft.flip_vertical
            self.width_var.set(state.width_text)
            self.height_var.set(state.height_text)
            self.max_side_var.set(state.max_side_text)
            self.watermark_text_var.set(state.watermark_text)
            self.watermark_position_var.set(state.watermark_position)
            self.watermark_opacity_var.set(state.watermark_opacity)
            self.watermark_size_var.set(state.watermark_size_percent)
            self.format_var.set(state.output_format)
            self.quality_var.set(state.quality)
            self.target_size_var.set(state.target_size_text)
            self.webp_lossless_var.set(state.webp_lossless)
            palette_label = next(
                (
                    label
                    for label, value in PNG_PALETTE_LABELS.items()
                    if value == state.png_palette_colors
                ),
                "保留原色",
            )
            self.png_palette_var.set(palette_label)
            compression_label = next(
                (
                    label
                    for label, value in TIFF_COMPRESSION_LABELS.items()
                    if value == state.tiff_compression
                ),
                "不压缩",
            )
            self.tiff_compression_var.set(compression_label)
            self.preserve_metadata_var.set(state.preserve_metadata)
            self.batch_strip_metadata_var.set(not state.preserve_metadata)
            self.batch_rename_template_var.set(state.rename_template)
            self.batch_conflict_var.set(state.conflict_policy)
            self.batch_preserve_animation_var.set(state.preserve_animation)
            self.backup_on_overwrite_var.set(state.backup_on_overwrite)
            background = state.transparency_background
            background_label = next(
                (
                    label
                    for label, value in TRANSPARENCY_BACKGROUND_LABELS.items()
                    if value == background
                ),
                background or "不自动处理",
            )
            self.conversion_background_var.set(background_label)
            self.export_preset_var.set("自定义")
            self.crop_box = None
        finally:
            self._syncing_document = False
        self._update_format_advanced_controls()
        self.crop_var.set("配方中已有裁切" if draft.crop_box is not None else "未设置裁剪")

    def _update_conversion_draft_from_controls(self) -> None:
        if self._syncing_document or self.mode_router.active_mode is not ModeId.CONVERT:
            return
        try:
            updated = self._conversion_control_state().apply_to(
                self.conversion_draft_history.current
            )
        except (tk.TclError, TypeError, ValueError):
            self.size_var.set("预计输出大小：等待有效的转换设置")
            return
        if self.conversion_draft_history.push(updated):
            self._conversion_recipe_override = None
            self.conversion_recipe_status_var.set(f"当前草稿：{updated.name}（已修改）")
            self._invalidate_conversion_session()
            self._schedule_preview()
        self._update_estimate()

    def _push_conversion_draft(
        self,
        draft: ConversionDraft,
        *,
        status: str,
        apply_controls: bool = False,
    ) -> bool:
        if not self.conversion_draft_history.push(draft):
            return False
        self._conversion_recipe_override = None
        if apply_controls:
            self._apply_conversion_draft_to_controls()
        self._invalidate_conversion_session()
        self.conversion_recipe_status_var.set(
            f"当前草稿：{self.conversion_draft_history.current.name}"
        )
        self.status_var.set(status)
        self._refresh_preview()
        self._update_estimate()
        return True

    def _invalidate_conversion_session(self) -> None:
        if self._restoring_conversion_session:
            return
        thread = self._conversion_thread
        if thread is not None and thread.is_alive():
            return
        self._conversion_session = None
        self._conversion_run_base_session = None
        try:
            self.conversion_checkpoint_writer.clear()
        except RuntimeError:
            pass

    @staticmethod
    def _positive_conversion_value(value: str, label: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        try:
            number = int(text)
        except ValueError as error:
            raise ValueError(f"{label}必须是正整数") from error
        if number <= 0:
            raise ValueError(f"{label}必须大于 0")
        return number

    def _build_current_conversion_recipe(self) -> TransformRecipe:
        updated = self._conversion_control_state().apply_to(self.conversion_draft_history.current)
        self.conversion_draft_history.push(updated)
        return updated.to_recipe()

    def _active_conversion_recipe(self) -> TransformRecipe:
        return self._build_current_conversion_recipe()

    def _use_current_conversion_settings(self) -> None:
        self._conversion_recipe_override = None
        current = self.conversion_draft_history.current.with_name("当前转换设置")
        self.conversion_draft_history.push(current)
        self.conversion_recipe_status_var.set("使用当前面板设置")
        self.conversion_job_status_var.set("已切换到当前裁切、尺寸与格式设置")
        self._invalidate_conversion_session()

    def _import_conversion_recipe(self) -> None:
        path = filedialog.askopenfilename(
            title="导入转换配方",
            filetypes=[("转换配方", "*.lierecipe"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            recipe = self.recipe_store.load(path)
        except (OSError, RecipeFileError, ValueError) as error:
            self.conversion_job_status_var.set(f"配方导入失败：{error}")
            return
        self.conversion_draft_history.reset(ConversionDraft.from_recipe(recipe))
        self._conversion_recipe_override = recipe
        self._apply_conversion_draft_to_controls()
        self._invalidate_conversion_session()
        self.conversion_recipe_status_var.set(
            f"已导入：{recipe.name} · {len(recipe.steps)} 个步骤 · "
            f"{recipe.export_profile.output_format}"
        )
        self.conversion_job_status_var.set("配方已载入，尚未执行")
        self.mode_router.update_state(
            {"recipe_path": str(Path(path)), "recipe_name": recipe.name},
            mode=ModeId.CONVERT,
        )
        self._refresh_preview()
        self._update_estimate()

    def _export_conversion_recipe(self) -> None:
        try:
            recipe = self._active_conversion_recipe()
        except ValueError as error:
            self.conversion_job_status_var.set(f"当前设置无效：{error}")
            return
        path = filedialog.asksaveasfilename(
            title="导出转换配方",
            defaultextension=".lierecipe",
            initialfile=f"{recipe.name}.lierecipe",
            filetypes=[("转换配方", "*.lierecipe")],
        )
        if not path:
            return
        try:
            saved = self.recipe_store.save(recipe, path)
        except (OSError, RecipeFileError, ValueError) as error:
            self.conversion_job_status_var.set(f"配方导出失败：{error}")
            return
        self.conversion_job_status_var.set(f"配方已导出：{saved.name}")

    def _start_conversion_queue(self) -> None:
        if self._conversion_thread is not None and self._conversion_thread.is_alive():
            self.conversion_job_status_var.set("转换任务正在运行")
            return
        if not self.conversion_workspace.queue.enabled_items:
            self.conversion_job_status_var.set("请先添加并启用至少一张图片")
            return
        output_text = self.conversion_output_dir_var.get().strip()
        if not output_text:
            output_text = self._choose_conversion_output_directory() or ""
        if not output_text:
            self.conversion_job_status_var.set("请选择输出文件夹")
            return
        output_directory = Path(output_text)
        try:
            output_directory.mkdir(parents=True, exist_ok=True)
            recipe = self._active_conversion_recipe()
            workers = max(1, min(16, int(self.batch_concurrency_var.get())))
            session = self._conversion_session
            if session is not None and self._conversion_session_matches_current(
                session,
                recipe,
                output_directory,
            ):
                resumable = tuple(
                    task
                    for task in session.tasks
                    if task.status
                    in {
                        ConversionTaskStatus.PENDING,
                        ConversionTaskStatus.FAILED,
                        ConversionTaskStatus.CANCELLED,
                    }
                )
                if not resumable:
                    self.conversion_job_status_var.set(
                        "当前转换会话已全部成功；修改队列或配方后可创建新任务"
                    )
                    return
                runner = session.resume_runner(max_workers=workers)
                base_session = session
                operation_label = f"继续转换 {len(resumable)} 项"
            else:
                queue_snapshot = ConversionQueue.from_ordered(self.conversion_workspace.queue.items)
                runner = ConversionRunner(
                    queue_snapshot,
                    recipe,
                    output_directory,
                    max_workers=workers,
                )
                base_session = ConversionSession.capture(queue_snapshot, runner)
                operation_label = f"正在转换 {len(runner.tasks)} 项"
        except (OSError, tk.TclError, TypeError, ValueError) as error:
            self.conversion_job_status_var.set(f"无法开始转换：{error}")
            return

        self._begin_conversion_operation(
            runner,
            base_session,
            status=f"{operation_label} · 并发 {workers}",
        )

    def _conversion_session_matches_current(
        self,
        session: ConversionSession,
        recipe: TransformRecipe,
        output_directory: Path,
    ) -> bool:
        def queue_signature(items: Sequence[object]) -> tuple[tuple[str, bool], ...]:
            signature: list[tuple[str, bool]] = []
            for item in items:
                path = Path(getattr(item, "path")).resolve(strict=False)
                signature.append((os.path.normcase(str(path)), bool(getattr(item, "enabled"))))
            return tuple(signature)

        return (
            queue_signature(session.queue_items)
            == queue_signature(self.conversion_workspace.queue.items)
            and session.recipe.to_json() == recipe.to_json()
            and session.output_directory == output_directory.resolve(strict=False)
        )

    def _begin_conversion_operation(
        self,
        runner: ConversionRunner,
        base_session: ConversionSession,
        *,
        status: str,
    ) -> None:
        while True:
            try:
                self._conversion_ui_queue.get_nowait()
            except queue.Empty:
                break
        self._conversion_generation += 1
        generation = self._conversion_generation
        self._active_conversion_generation = generation
        self._conversion_tray_task_id = f"conversion:{generation}"
        self._conversion_runner = runner
        self._conversion_run_base_session = base_session
        self._conversion_run_queue = base_session.create_queue()
        self._conversion_session = base_session
        self.conversion_workspace.set_task_records(base_session.tasks)
        self.conversion_workspace.set_running(True)
        self.conversion_job_status_var.set(status)
        self._sync_conversion_task_to_tray(base_session, active=True, detail=status)
        self._checkpoint_conversion_session(base_session, force=True)
        self._conversion_thread = threading.Thread(
            target=self._run_conversion_worker,
            args=(runner, generation),
            name="conversion-coordinator",
            daemon=True,
        )
        self._conversion_thread.start()
        self._update_conversion_buttons()
        self._schedule_conversion_poll()

    def _run_conversion_worker(
        self,
        runner: ConversionRunner,
        generation: int,
    ) -> None:
        try:
            result = runner.run()
        except BaseException as error:
            self._conversion_ui_queue.put((generation, "error", error))
        else:
            self._conversion_ui_queue.put((generation, "result", result))

    def _schedule_conversion_poll(self) -> None:
        if self._conversion_poll_job is not None:
            try:
                self.after_cancel(self._conversion_poll_job)
            except tk.TclError:
                pass
        self._conversion_poll_job = self.after(80, self._poll_conversion_worker)

    def _poll_conversion_worker(self) -> None:
        self._conversion_poll_job = None
        runner = self._conversion_runner
        snapshot: ConversionSession | None = None
        if runner is not None and self._conversion_run_base_session is not None:
            try:
                snapshot = self._conversion_run_base_session.merge_tasks(list(runner.tasks))
            except ConversionSessionError as error:
                self.conversion_job_status_var.set(f"转换状态合并失败：{error}")
            else:
                self._conversion_session = snapshot
                self.conversion_workspace.set_task_records(snapshot.tasks)
                self._checkpoint_conversion_session(snapshot)
                self._sync_conversion_task_to_tray(snapshot, active=True)
        terminal: tuple[str, object] | None = None
        while True:
            try:
                generation, kind, payload = self._conversion_ui_queue.get_nowait()
            except queue.Empty:
                break
            if generation == self._active_conversion_generation:
                terminal = (kind, payload)
        thread = self._conversion_thread
        if terminal is None and thread is not None and thread.is_alive():
            self._schedule_conversion_poll()
            return
        if terminal is None:
            terminal = ("error", RuntimeError("转换线程提前结束，未返回结果"))
        kind, payload = terminal
        self._conversion_thread = None
        base_session = self._conversion_run_base_session
        if (
            kind == "result"
            and isinstance(payload, ConversionRunResult)
            and base_session is not None
        ):
            try:
                final_session = base_session.merge_tasks(list(payload.tasks))
            except ConversionSessionError as error:
                self.conversion_job_status_var.set(f"转换结果合并失败：{error}")
            else:
                self._finish_conversion_run(payload, final_session)
        else:
            self.conversion_job_status_var.set(f"转换任务失败：{payload}")
            failed_session = snapshot or base_session
            if failed_session is not None:
                self._conversion_session = failed_session
                self._checkpoint_conversion_session(failed_session, force=True)
                self._sync_conversion_task_to_tray(
                    failed_session,
                    active=False,
                    detail=f"转换任务失败：{payload}",
                    status_override=UnifiedTaskStatus.FAILED,
                )
        self._active_conversion_generation = None
        self._conversion_run_base_session = None
        self._conversion_run_queue = None
        self.conversion_workspace.set_running(False)
        self._update_conversion_buttons()

    def _checkpoint_conversion_session(
        self,
        session: ConversionSession,
        *,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self._last_conversion_checkpoint < 0.5:
            return
        self._last_conversion_checkpoint = now
        try:
            self.conversion_checkpoint_writer.submit(session)
        except RuntimeError:
            return
        failure = self.conversion_checkpoint_writer.last_failure
        if (
            failure is not None
            and self.conversion_checkpoint_writer.persisted_generation < failure.generation
        ):
            self.conversion_job_status_var.set(f"转换检查点写入失败：{failure.error}")

    def _finish_conversion_run(
        self,
        result: ConversionRunResult,
        session: ConversionSession,
    ) -> None:
        del result
        self._conversion_session = session
        self.conversion_workspace.set_task_records(session.tasks)
        succeeded = sum(task.status is ConversionTaskStatus.SUCCEEDED for task in session.tasks)
        failed = sum(task.status is ConversionTaskStatus.FAILED for task in session.tasks)
        cancelled = sum(task.status is ConversionTaskStatus.CANCELLED for task in session.tasks)
        self.conversion_job_status_var.set(
            f"转换完成 · 成功 {succeeded} · 失败 {failed} · 取消 {cancelled}"
        )
        self._sync_conversion_task_to_tray(session, active=False)
        successful_outputs = tuple(
            task.output
            for task in session.tasks
            if task.status is ConversionTaskStatus.SUCCEEDED and task.output is not None
        )
        if successful_outputs:
            self._remember_export_directory(successful_outputs[0].parent)
        if failed or cancelled:
            self._checkpoint_conversion_session(session, force=True)
        else:
            try:
                self.conversion_checkpoint_writer.clear()
            except RuntimeError:
                pass
        self.mode_router.update_state(
            {
                "last_succeeded": succeeded,
                "last_failed": failed,
                "last_cancelled": cancelled,
            },
            mode=ModeId.CONVERT,
        )

    def _cancel_conversion_queue(self) -> None:
        runner = self._conversion_runner
        thread = self._conversion_thread
        if runner is None or thread is None or not thread.is_alive():
            self.conversion_job_status_var.set("当前没有正在运行的转换任务")
            self._update_conversion_buttons()
            return
        runner.cancel()
        base_session = self._conversion_run_base_session
        if base_session is not None:
            try:
                snapshot = base_session.merge_tasks(list(runner.tasks))
            except ConversionSessionError:
                self.conversion_workspace.set_task_records(runner.tasks)
            else:
                self._conversion_session = snapshot
                self.conversion_workspace.set_task_records(snapshot.tasks)
                self._checkpoint_conversion_session(snapshot, force=True)
        self.conversion_job_status_var.set("正在取消；已停止派发新任务…")
        current_session = self._conversion_session
        if current_session is not None:
            self._sync_conversion_task_to_tray(
                current_session,
                active=True,
                detail="正在取消；已停止派发新任务",
            )

    def _retry_conversion_tasks(self, records: tuple[object, ...]) -> None:
        thread = self._conversion_thread
        if thread is not None and thread.is_alive():
            self.conversion_job_status_var.set("转换任务运行中，暂不能重试")
            return
        session = self._conversion_session
        selected = tuple(
            record
            for record in records
            if isinstance(record, ConversionTaskRecord)
            and record.status in {ConversionTaskStatus.FAILED, ConversionTaskStatus.CANCELLED}
        )
        if session is None or not selected:
            self.conversion_job_status_var.set("当前任务已失效，无法安全重试")
            return
        if any(
            task.status in {ConversionTaskStatus.PENDING, ConversionTaskStatus.RUNNING}
            for task in session.tasks
        ):
            self.conversion_job_status_var.set("会话仍有未完成任务，请先继续整个会话")
            return
        if not self._quarantine_recovered_outputs(selected):
            return
        try:
            workers = max(1, min(16, int(self.batch_concurrency_var.get())))
            runner = ConversionRunner.retry_tasks(
                selected,
                session.recipe,
                session.output_directory,
                max_workers=workers,
            )
        except (OSError, tk.TclError, TypeError, ValueError) as error:
            self.conversion_job_status_var.set(f"无法重试任务：{error}")
            return
        self._begin_conversion_operation(
            runner,
            session,
            status=f"正在重试 {len(selected)} 项 · 并发 {workers}",
        )

    def _quarantine_recovered_outputs(
        self,
        records: Sequence[ConversionTaskRecord],
    ) -> bool:
        for record in records:
            error = record.error or ""
            target = record.planned_output
            if not error.startswith("恢复验证失败") or not target.exists():
                continue
            backup = self._unique_export_path(
                target.parent,
                f"{target.name}.corrupt",
                ".bak",
            )
            try:
                target.replace(backup)
            except OSError as move_error:
                self.conversion_job_status_var.set(f"无法隔离损坏输出 {target.name}：{move_error}")
                return False
            self.conversion_job_status_var.set(f"已将损坏输出保留为 {backup.name}，准备重试")
        return True

    def _open_conversion_result(self, path: Path) -> None:
        if not path.is_file():
            self.conversion_job_status_var.set(f"结果文件不存在：{path}")
            return
        self.mode_router.switch(ModeId.VIEWER)
        self.open_path(path)

    def _offer_conversion_recovery(self) -> None:
        if (
            not self.conversion_session_path.is_file()
            or self._conversion_recovery_thread is not None
        ):
            return
        while True:
            try:
                self._conversion_recovery_queue.get_nowait()
            except queue.Empty:
                break
        self._conversion_recovery_thread = threading.Thread(
            target=self._load_conversion_recovery_worker,
            name="conversion-recovery-loader",
            daemon=True,
        )
        self._conversion_recovery_thread.start()
        self._conversion_recovery_poll_job = self.after(
            40,
            self._poll_conversion_recovery,
        )

    def _load_conversion_recovery_worker(self) -> None:
        try:
            session = self.conversion_session_store.load(self.conversion_session_path)
        except (OSError, ConversionSessionError) as error:
            self._conversion_recovery_queue.put(("error", error))
        else:
            self._conversion_recovery_queue.put(("session", session))

    def _poll_conversion_recovery(self) -> None:
        self._conversion_recovery_poll_job = None
        try:
            kind, payload = self._conversion_recovery_queue.get_nowait()
        except queue.Empty:
            thread = self._conversion_recovery_thread
            if thread is not None and thread.is_alive():
                self._conversion_recovery_poll_job = self.after(
                    40,
                    self._poll_conversion_recovery,
                )
                return
            kind, payload = (
                "error",
                RuntimeError("转换恢复线程提前结束，未返回结果"),
            )
        self._conversion_recovery_thread = None
        if kind != "session" or not isinstance(payload, ConversionSession):
            self.conversion_job_status_var.set(f"转换会话恢复失败：{payload}")
            return
        session = payload
        succeeded = sum(task.status is ConversionTaskStatus.SUCCEEDED for task in session.tasks)
        unfinished = len(session.tasks) - succeeded
        restore = messagebox.askyesno(
            APP_NAME,
            "检测到上次的转图任务。\n\n"
            f"队列 {len(session.queue_items)} 项，已成功 {succeeded} 项，"
            f"可继续 {unfinished} 项。\n\n是否恢复队列、配方和任务状态？",
        )
        if not restore:
            try:
                self.conversion_checkpoint_writer.clear()
            except RuntimeError:
                pass
            self.conversion_job_status_var.set("已放弃上次转图会话")
            return
        self._restore_conversion_session(session)

    def _restore_conversion_session(self, session: ConversionSession) -> None:
        self._restoring_conversion_session = True
        try:
            self.conversion_workspace.set_queue(session.create_queue(), notify=False)
            self.conversion_draft_history.reset(ConversionDraft.from_recipe(session.recipe))
            self._conversion_recipe_override = session.recipe
            self._conversion_session = session
            self._conversion_tray_task_id = "conversion:recovered"
            self._conversion_run_base_session = None
            self._conversion_run_queue = None
            self._apply_conversion_draft_to_controls()
            self.conversion_output_dir_var.set(str(session.output_directory))
            self.batch_concurrency_var.set(session.max_workers)
            self.conversion_workspace.set_task_records(session.tasks)
            self._sync_conversion_task_to_tray(
                session,
                active=False,
                detail="已从上次退出时的转换检查点恢复",
            )
            self._conversion_queue_changed(session.queue_items)
            self.conversion_recipe_status_var.set(
                f"已恢复：{session.recipe.name} · {len(session.recipe.steps)} 个步骤"
            )
            self.mode_router.switch(ModeId.CONVERT)
        finally:
            self._restoring_conversion_session = False
        self.conversion_job_status_var.set("转图会话已恢复；点击“开始转换”只继续未完成或失败任务")
        self._refresh_preview()
        self._update_estimate()

    def _update_conversion_buttons(self) -> None:
        if not hasattr(self, "conversion_start_button"):
            return
        running = self._conversion_thread is not None and self._conversion_thread.is_alive()
        can_start = bool(self.conversion_workspace.queue.enabled_items) and not running
        self.conversion_start_button.state(("!disabled",) if can_start else ("disabled",))
        self.conversion_cancel_button.state(("!disabled",) if running else ("disabled",))

    def _show_inspector_tab(self, section: str, *, sync_mode: bool = True) -> None:
        groups = getattr(self, "inspector_groups", {})
        if section not in groups:
            return
        if sync_mode:
            target_mode = ModeId.CONVERT if section == "convert" else ModeId.EDIT
            if target_mode is ModeId.EDIT:
                self.mode_router.update_state({"inspector_tab": section}, mode=ModeId.EDIT)
            if self.mode_router.active_mode is not target_mode:
                self.mode_router.switch(target_mode)
        self.active_inspector_tab = section
        for name, widgets in groups.items():
            for widget in widgets:
                if name == section:
                    widget.grid()
                else:
                    widget.grid_remove()
        if section == "adjust" and not self.detail_expanded_var.get():
            for widget in self.detail_advanced_widgets:
                widget.grid_remove()
        self.inspector_tabs.select(section)
        self.panel_canvas.yview_moveto(0.0)

    def _apply_material_look(self, look: str) -> None:
        presets = {
            "original": (0, 0, 0, 0, False),
            "vivid": (6, 14, 22, 2, False),
            "warm": (4, 6, 12, 18, False),
            "mono": (0, 12, 0, 0, True),
        }
        brightness, contrast, saturation, temperature, grayscale = presets.get(
            look, presets["original"]
        )
        self.brightness_var.set(brightness)
        self.contrast_var.set(contrast)
        self.saturation_var.set(saturation)
        self.temperature_var.set(temperature)
        self.grayscale_var.set(grayscale)
        self._live_adjust()

    def _toggle_auto_adjust(self) -> None:
        self.auto_contrast_var.set(not self.auto_contrast_var.get())
        self.auto_adjust_button.set_selected(self.auto_contrast_var.get())
        self._live_adjust()

    def _reset_color_controls(self) -> None:
        self.temperature_var.set(0)
        self.saturation_var.set(0)
        self.tint_var.set(0)
        if self.document is None:
            self._live_adjust()
            return
        self._advanced_color_changed("temperature", 0)
        self._set_document_edit_field("saturation", 0)
        self._advanced_color_changed("tint", 0)
        self.status_var.set("颜色参数已重置")

    def _toggle_detail_section(self) -> None:
        expanded = not self.detail_expanded_var.get()
        self.detail_expanded_var.set(expanded)
        self.detail_toggle_button.configure(text="细节  ⌃" if expanded else "细节  ⌄")
        if expanded:
            self.detail_body.grid()
            for widget in self.detail_advanced_widgets:
                widget.grid()
        else:
            self.detail_body.grid_remove()
            for widget in self.detail_advanced_widgets:
                widget.grid_remove()
        self.panel_canvas.configure(scrollregion=self.panel_canvas.bbox("all"))

    def _place_command_dock(self) -> None:
        self.command_dock.place(
            in_=self,
            relx=0.5,
            rely=1.0,
            x=0,
            y=-12,
            anchor="s",
            relwidth=0.0,
            relheight=0.0,
            width=690,
            height=64,
        )

    def _rebuild_zoom_preset_buttons(self) -> None:
        if not hasattr(self, "zoom_preset_bar"):
            return
        content = self.zoom_preset_bar.content
        for child in content.winfo_children():
            child.destroy()
        self.zoom_preset_buttons.clear()
        for column, value in enumerate(self.viewer_zoom_presets):
            label = "适合窗口" if value == "适应" else value
            width = 76 if value == "适应" else max(50, len(label) * 10 + 18)
            button = MaterialButton(
                content,
                text=label,
                width=width,
                height=34,
                variant="tonal",
                command=partial(self._select_zoom_preset, value),
            )
            button.grid(row=0, column=column, padx=1)
            set_accessible_name(button, f"缩放到{label}")
            self.zoom_preset_buttons[value] = button
        self.zoom_preset_bar._layout()

    def _place_canvas_status(self) -> None:
        if self.mode_router.active_mode is ModeId.VIEWER:
            self.status_label.place_forget()
            self.canvas_metadata.place_forget()
            if self._viewer_clean_mode:
                self.viewer_file_details.place_forget()
                self.viewer_gallery_button.place_forget()
                self.viewer_settings_button.place_forget()
                self.viewer_topmost_button.place_forget()
                self.viewer_markup_bar.place_forget()
                self.viewer_gallery_scope_bar.place_forget()
                self.thumbnail_filmstrip.place_forget()
            else:
                self.viewer_file_details.place(relx=0.0, rely=1.0, x=16, y=-18, anchor="sw")
                self.viewer_gallery_button.place(
                    relx=1.0,
                    rely=1.0,
                    x=-16,
                    y=-12,
                    anchor="se",
                )
                self.viewer_settings_button.place(
                    relx=1.0,
                    rely=0.0,
                    x=-14,
                    y=14,
                    anchor="ne",
                )
                self.viewer_settings_button.tk.call("raise", self.viewer_settings_button._w)
                self.viewer_topmost_button.place(
                    relx=1.0,
                    rely=0.0,
                    x=-58,
                    y=14,
                    anchor="ne",
                )
                self.viewer_topmost_button.tk.call("raise", self.viewer_topmost_button._w)
                if self._viewer_markup_active:
                    self.viewer_markup_bar.place(
                        relx=0.5,
                        rely=0.0,
                        x=0,
                        y=68,
                        anchor="n",
                    )
                    self.viewer_markup_bar._layout()
                    self.viewer_markup_bar.tk.call("raise", self.viewer_markup_bar._w)
                else:
                    self.viewer_markup_bar.place_forget()
            if (
                not self._viewer_clean_mode
                and self.thumbnail_sidebar_visible_var.get()
                and not self._is_immersive
            ):
                self.thumbnail_filmstrip.place(
                    relx=0.5,
                    rely=1.0,
                    y=-12,
                    anchor="s",
                    relwidth=0.56,
                    height=78,
                )
                self.thumbnail_filmstrip.tk.call("raise", self.thumbnail_filmstrip._w)
                self.viewer_gallery_scope_bar.place(
                    relx=0.5,
                    rely=1.0,
                    y=-91,
                    anchor="s",
                )
                self.viewer_gallery_scope_bar._layout()
                self.viewer_gallery_scope_bar.tk.call("raise", self.viewer_gallery_scope_bar._w)
            else:
                self.viewer_gallery_scope_bar.place_forget()
                self.thumbnail_filmstrip.place_forget()
        else:
            self.viewer_file_details.place_forget()
            self.viewer_gallery_button.place_forget()
            self.viewer_settings_button.place_forget()
            self.viewer_topmost_button.place_forget()
            self.viewer_markup_bar.place_forget()
            self.viewer_gallery_scope_bar.place_forget()
            self.thumbnail_filmstrip.place_forget()
            self._hide_viewer_navigation()
            self.status_label.place(relx=0.0, rely=1.0, x=16, y=-16, anchor="sw")
            self.canvas_metadata.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
        self._update_viewer_navigation_buttons()

    def _update_viewer_gallery_button(self) -> None:
        if not hasattr(self, "viewer_gallery_button"):
            return
        gallery_paths = self._viewer_gallery_paths()
        image_count = len(gallery_paths)
        if tuple(self.thumbnail_filmstrip.paths) != gallery_paths:
            self.thumbnail_filmstrip.set_paths(
                gallery_paths,
                self.navigation.current,
            )
        else:
            self.thumbnail_filmstrip.select(self.navigation.current, reveal=False)
        self._sync_viewer_gallery_scope_buttons()
        self.viewer_gallery_button.set_text("▦ 此文件夹")
        self.viewer_gallery_button.set_selected(
            self.thumbnail_sidebar_visible_var.get()
            and self.mode_router.active_mode is ModeId.VIEWER
        )
        scope_label = "当前文件夹" if self._viewer_gallery_scope == "folder" else "我的收藏"
        set_accessible_name(
            self.viewer_gallery_button,
            f"预览{scope_label}图片，共 {image_count} 张"
            if image_count
            else f"预览{scope_label}图片",
        )
        self._update_viewer_navigation_buttons()

    def _viewer_gallery_paths(self) -> tuple[Path, ...]:
        if self._viewer_gallery_scope == "folder":
            return tuple(self.navigation.files)
        favorites: list[Path] = []
        for saved_path in self.viewer_favorite_paths:
            path = Path(saved_path)
            if path.suffix.lower() not in SUPPORTED_INPUTS:
                continue
            try:
                available = path.is_file()
            except OSError:
                available = False
            if not available:
                continue
            if any(self._viewer_paths_match(path, existing) for existing in favorites):
                continue
            favorites.append(path)
        return tuple(favorites)

    def _sync_viewer_gallery_scope_buttons(self) -> None:
        if not hasattr(self, "viewer_gallery_folder_button"):
            return
        self.viewer_gallery_folder_button.set_selected(self._viewer_gallery_scope == "folder")
        self.viewer_gallery_favorites_button.set_selected(self._viewer_gallery_scope == "favorites")

    def _set_viewer_gallery_scope(self, scope: str) -> str:
        if scope not in {"folder", "favorites"}:
            raise ValueError("未知的图片预览范围")
        self._viewer_gallery_scope = scope
        self._update_viewer_gallery_button()
        if self.thumbnail_sidebar_visible_var.get():
            self._place_canvas_status()
        if scope == "folder":
            self.status_var.set(f"当前文件夹 · {len(self.navigation.files)} 张")
        else:
            count = len(self.thumbnail_filmstrip.paths)
            self.status_var.set(f"我的收藏 · {count} 张" if count else "我的收藏中暂无图片")
        return "break"

    def _update_viewer_navigation_buttons(self) -> None:
        if not hasattr(self, "_viewer_navigation_visible"):
            return
        canvas_width = max(1, self.canvas_frame.winfo_width())
        canvas_height = max(1, self.canvas_frame.winfo_height())
        navigation_geometry = self._viewer_navigation_dimensions(
            canvas_width,
            canvas_height,
        )
        self._viewer_navigation_geometry = navigation_geometry
        should_show = (
            self.mode_router.active_mode is ModeId.VIEWER
            and not self._is_immersive
            and not self._viewer_clean_mode
            and not self._viewer_markup_active
            and len(self.navigation.files) > 1
        )
        if should_show:
            newly_visible = not self._viewer_navigation_visible
            self._viewer_navigation_visible = True
            if newly_visible:
                self._viewer_navigation_opacity = 1.0
            self._draw_viewer_navigation()
            self._schedule_viewer_navigation_fade()
        else:
            self._hide_viewer_navigation()

    def _draw_viewer_navigation(self) -> None:
        self.canvas.delete("viewer-navigation")
        self.canvas.delete("viewer-navigation-tooltip")
        if not self._viewer_navigation_visible:
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        button_width, _button_height, icon_size = self._viewer_navigation_geometry
        opacity_step = max(0, min(10, round(self._viewer_navigation_opacity * 10)))
        if opacity_step == 0:
            self._viewer_navigation_photos.clear()
            return
        arrow_y = height / 2
        positions = {-1: 8 + button_width / 2, 1: width - 8 - button_width / 2}
        for direction, arrow_x in positions.items():
            color = (
                self.theme_manager.tokens.foreground
                if self._viewer_navigation_hover == direction
                else self.theme_manager.tokens.muted
            )
            cache_key = (direction, icon_size, color, opacity_step)
            arrow_photo = self._viewer_navigation_photo_cache.get(cache_key)
            if arrow_photo is None:
                arrow_height = max(72, round(icon_size * 0.78))
                arrow_width = max(38, round(arrow_height * 0.43))
                stroke_width = max(4, round(arrow_height * 0.06))
                padding = stroke_width * 2
                image_width = arrow_width + padding * 2
                image_height = arrow_height + padding * 2
                red, green, blue = ImageColor.getrgb(color)
                arrow_fill = (red, green, blue, round(255 * opacity_step / 10))

                def paint_arrow(
                    draw: object,
                    scale: int,
                    *,
                    side: int = direction,
                    arrow_color: tuple[int, int, int, int] = arrow_fill,
                ) -> None:
                    scaled_stroke = stroke_width * scale
                    radius = scaled_stroke / 2
                    top = padding * scale
                    middle = image_height * scale / 2
                    bottom = (image_height - padding) * scale
                    left = padding * scale
                    right = (image_width - padding) * scale
                    points = (
                        ((right, top), (left, middle), (right, bottom))
                        if side < 0
                        else ((left, top), (right, middle), (left, bottom))
                    )
                    draw.line(
                        points,
                        fill=arrow_color,
                        width=scaled_stroke,
                        joint="curve",
                    )
                    for point_x, point_y in points:
                        draw.ellipse(
                            (
                                point_x - radius,
                                point_y - radius,
                                point_x + radius,
                                point_y + radius,
                            ),
                            fill=arrow_color,
                        )

                arrow_photo = render_photo(
                    image_width,
                    image_height,
                    paint_arrow,
                    master=self.canvas,
                )
                self._viewer_navigation_photo_cache[cache_key] = arrow_photo
            self._viewer_navigation_photos[direction] = arrow_photo
            self.canvas.create_image(
                arrow_x,
                arrow_y,
                image=arrow_photo,
                tags=("viewer-navigation", f"viewer-navigation-{direction}"),
            )
        self.canvas.tag_raise("viewer-navigation")

    def _hide_viewer_navigation(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self._viewer_navigation_visible = False
        self._viewer_navigation_hover = None
        self._viewer_navigation_pressed = None
        for job_name in ("_viewer_navigation_fade_job", "_viewer_navigation_hide_job"):
            job = getattr(self, job_name, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, job_name, None)
        self._cancel_viewer_navigation_tooltip()
        self.canvas.delete("viewer-navigation")
        self.canvas.delete("viewer-navigation-tooltip")
        self._viewer_navigation_photos.clear()

    def _viewer_navigation_direction_at(self, x: int, y: int) -> int | None:
        if not self._viewer_navigation_visible:
            return None
        button_width, button_height, _icon_size = self._viewer_navigation_geometry
        canvas_width = max(1, self.canvas.winfo_width())
        canvas_height = max(1, self.canvas.winfo_height())
        if abs(y - canvas_height / 2) > button_height / 2:
            return None
        if x <= button_width + 8:
            return -1
        if x >= canvas_width - button_width - 8:
            return 1
        return None

    def _set_viewer_navigation_hover(self, direction: int | None) -> None:
        if self._viewer_navigation_hover == direction:
            return
        self._viewer_navigation_hover = direction
        self._cancel_viewer_navigation_tooltip()
        if direction is not None:
            if self._viewer_navigation_hide_job is not None:
                try:
                    self.after_cancel(self._viewer_navigation_hide_job)
                except tk.TclError:
                    pass
                self._viewer_navigation_hide_job = None
            self._animate_viewer_navigation_opacity(1.0)
            self._viewer_navigation_tooltip_job = self.after(
                450,
                lambda selected=direction: self._show_viewer_navigation_tooltip(selected),
            )
        else:
            self._draw_viewer_navigation()
            self._schedule_viewer_navigation_fade()

    def _schedule_viewer_navigation_fade(self) -> None:
        if not self._viewer_navigation_visible or self._viewer_navigation_hover is not None:
            return
        if self._viewer_navigation_hide_job is not None:
            try:
                self.after_cancel(self._viewer_navigation_hide_job)
            except tk.TclError:
                pass
        self._viewer_navigation_hide_job = self.after(
            1100,
            lambda: self._animate_viewer_navigation_opacity(0.0),
        )

    def _animate_viewer_navigation_opacity(self, target: float) -> None:
        self._viewer_navigation_hide_job = None
        if self._viewer_navigation_fade_job is not None:
            try:
                self.after_cancel(self._viewer_navigation_fade_job)
            except tk.TclError:
                pass
            self._viewer_navigation_fade_job = None
        bounded_target = max(0.0, min(1.0, float(target)))

        def step() -> None:
            self._viewer_navigation_fade_job = None
            delta = bounded_target - self._viewer_navigation_opacity
            if abs(delta) <= 0.01:
                self._viewer_navigation_opacity = bounded_target
                self._draw_viewer_navigation()
                return
            self._viewer_navigation_opacity += max(-0.10, min(0.10, delta))
            self._draw_viewer_navigation()
            self._viewer_navigation_fade_job = self.after(16, step)

        step()

    def _cancel_viewer_navigation_tooltip(self) -> None:
        if self._viewer_navigation_tooltip_job is not None:
            try:
                self.after_cancel(self._viewer_navigation_tooltip_job)
            except tk.TclError:
                pass
            self._viewer_navigation_tooltip_job = None
        if hasattr(self, "canvas"):
            self.canvas.delete("viewer-navigation-tooltip")

    def _show_viewer_navigation_tooltip(self, direction: int) -> None:
        self._viewer_navigation_tooltip_job = None
        if self._viewer_navigation_hover != direction or not self._viewer_navigation_visible:
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        button_width, _button_height, _icon_size = self._viewer_navigation_geometry
        arrow_x = 8 + button_width / 2 if direction < 0 else width - 8 - button_width / 2
        tooltip_x = arrow_x + 58 if direction < 0 else arrow_x - 58
        tooltip_y = height / 2 + 54
        label = "上一张图片" if direction < 0 else "下一张图片"
        text_id = self.canvas.create_text(
            tooltip_x,
            tooltip_y,
            text=label,
            fill=self.theme_manager.tokens.foreground,
            font=("Microsoft YaHei UI", 9),
            tags=("viewer-navigation-tooltip",),
        )
        bounds = self.canvas.bbox(text_id)
        if bounds is not None:
            left, top, right, bottom = bounds
            background_id = self.canvas.create_rectangle(
                left - 9,
                top - 6,
                right + 9,
                bottom + 6,
                fill=self.theme_manager.tokens.surface_high,
                outline="",
                tags=("viewer-navigation-tooltip",),
            )
            self.canvas.tag_lower(background_id, text_id)
        self.canvas.tag_raise("viewer-navigation-tooltip")

    @staticmethod
    def _viewer_navigation_dimensions(width: int, height: int) -> tuple[int, int, int]:
        """Scale edge-navigation targets and arrows with the viewer viewport."""

        short_edge = max(1, min(int(width), int(height)))
        button_width = max(156, min(232, round(short_edge * 0.16)))
        button_height = max(240, min(380, round(short_edge * 0.30)))
        icon_size = max(76, min(128, round(short_edge * 0.11)))
        icon_size = round(icon_size / 4) * 4
        return button_width, button_height, icon_size

    @staticmethod
    def _viewer_fit_render_size(canvas_width: int, canvas_height: int) -> tuple[int, int]:
        """Use nearly all of the viewer while preserving compact UI breathing room."""

        safe_width = max(100, int(canvas_width))
        safe_height = max(100, int(canvas_height))
        horizontal_margin = max(24, min(48, round(safe_width * 0.025)))
        vertical_margin = max(64, min(96, round(safe_height * 0.083)))
        return (
            max(100, safe_width - horizontal_margin),
            max(100, safe_height - vertical_margin),
        )

    def _show_app_menu(self) -> None:
        anchor = (
            self.dock_menu_button if self.dock_menu_button.winfo_ismapped() else self.menu_button
        )
        x = anchor.winfo_rootx() + anchor.winfo_width()
        y = anchor.winfo_rooty() + anchor.winfo_height()
        try:
            self.menu_bar.tk_popup(x, y)
        finally:
            self.menu_bar.grab_release()

    def _show_feature_palette(self) -> None:
        window = self._feature_palette_window
        if window is not None:
            try:
                if window.winfo_exists():
                    window.deiconify()
                    window.lift()
                    window.focus_search()
                    return
            except tk.TclError:
                pass
        self._feature_palette_window = FeatureCommandPalette(
            self,
            self.feature_registry,
            active_mode=self.mode_router.active_mode,
            on_invoke=self.invoke_feature,
            on_closed=self._feature_palette_closed,
            initial_scope=FeaturePaletteScope.CURRENT,
        )
        self._fit_dialog(self._feature_palette_window, 860, 650)
        tokens = self.theme_manager.apply(self.theme_mode_var.get())
        self._feature_palette_window.apply_tokens(tokens)
        self._name_interactive_widgets(self._feature_palette_window)

    def _feature_palette_closed(self) -> None:
        self._feature_palette_window = None

    def _show_pan_hint(self) -> None:
        self.canvas.focus_set()
        self.status_var.set("按住鼠标中键或右键拖动画布")

    def _toggle_dock_compare(self) -> None:
        self.compare_original.set(not self.compare_original.get())
        self.dock_compare_button.set_selected(self.compare_original.get())
        self._toggle_compare_original()

    def _toggle_dock_split_compare(self) -> None:
        self.split_compare_var.set(not self.split_compare_var.get())
        self.dock_split_button.set_selected(self.split_compare_var.get())
        self._toggle_split_compare()

    def _build_menu(self) -> None:
        self.menu_bar = tk.Menu(self)
        file_menu = tk.Menu(self.menu_bar, tearoff=False)
        file_menu.add_command(label="打开...", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(
            label="打开文件夹...",
            accelerator="Ctrl+Shift+O",
            command=self.open_directory,
        )
        file_menu.add_command(label="打开编辑项目...", command=self.open_project_dialog)
        file_menu.add_command(
            label="从剪贴板粘贴", accelerator="Ctrl+V", command=self.paste_from_clipboard
        )
        self.recent_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="最近打开", menu=self.recent_menu)
        self.recent_directory_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="最近目录", menu=self.recent_directory_menu)
        self.recent_project_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="最近编辑项目", menu=self.recent_project_menu)
        self.recent_export_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="最近导出位置", menu=self.recent_export_menu)
        file_menu.add_separator()
        file_menu.add_command(label="重命名...", accelerator="F2", command=self.rename_current)
        file_menu.add_command(label="复制到...", command=self.copy_current)
        file_menu.add_command(label="移动到...", command=self.move_current)
        file_menu.add_command(
            label="在资源管理器中显示", accelerator="Ctrl+Shift+E", command=self.reveal_current
        )
        file_menu.add_command(
            label="移入回收站", accelerator="Delete", command=self.delete_current_to_recycle_bin
        )
        file_menu.add_command(label="打开回收站", command=open_recycle_bin)
        file_menu.add_separator()
        file_menu.add_command(label="保存为...", accelerator="Ctrl+S", command=self.save_as)
        file_menu.add_command(
            label="保存编辑项目...", accelerator="Ctrl+Shift+S", command=self.save_project_dialog
        )
        file_menu.add_command(label="快速导出", accelerator="Ctrl+E", command=self.quick_export)
        file_menu.add_command(
            label="复制编辑结果", accelerator="Ctrl+Shift+C", command=self.copy_result
        )
        file_menu.add_command(label="批量处理文件...", command=self.batch_process)
        file_menu.add_command(label="批量处理文件夹...", command=self.batch_process_folder)
        file_menu.add_command(label="导出当前动画...", command=self.export_current_animation)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        self.menu_bar.add_cascade(label="文件", menu=file_menu, underline=0)

        edit_menu = tk.Menu(self.menu_bar, tearoff=False)
        edit_menu.add_command(label="撤销", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_command(label="重做", accelerator="Ctrl+Y", command=self.redo)
        edit_menu.add_separator()
        edit_menu.add_command(label="重置快速编辑", command=self.reset_edits)
        edit_menu.add_command(label="重置裁剪", accelerator="Esc", command=self.reset_crop)
        edit_menu.add_command(label="应用裁剪", accelerator="Enter", command=self.apply_crop)
        edit_menu.add_separator()
        edit_menu.add_command(label="左转 90°", command=lambda: self.rotate(-90))
        edit_menu.add_command(
            label="右转 90°", accelerator="Ctrl+R", command=lambda: self.rotate(90)
        )
        edit_menu.add_command(label="水平翻转", command=self.flip_horizontal)
        edit_menu.add_command(label="垂直翻转", command=self.flip_vertical)
        edit_menu.add_separator()
        edit_menu.add_command(label="收藏与快捷键…", command=self.configure_workflow)
        edit_menu.add_separator()
        edit_menu.add_command(label="设置...", accelerator="Ctrl+,", command=self.open_settings)
        self.menu_bar.add_cascade(label="编辑", menu=edit_menu, underline=0)

        view_menu = tk.Menu(self.menu_bar, tearoff=False)
        view_menu.add_command(
            label="第一张", accelerator="Home", command=lambda: self.navigate_folder_to("first")
        )
        view_menu.add_command(
            label="上一张", accelerator="Left", command=lambda: self.navigate_folder(-1)
        )
        view_menu.add_command(
            label="下一张", accelerator="Right", command=lambda: self.navigate_folder(1)
        )
        view_menu.add_command(
            label="最后一张", accelerator="End", command=lambda: self.navigate_folder_to("last")
        )
        view_menu.add_separator()
        view_menu.add_command(label="适合窗口", accelerator="Ctrl+0", command=self.reset_view)
        view_menu.add_command(label="50%", command=lambda: self.set_zoom("50%"))
        view_menu.add_command(
            label="100%", accelerator="Ctrl+1", command=lambda: self.set_zoom("100%")
        )
        view_menu.add_command(label="150%", command=lambda: self.set_zoom("150%"))
        view_menu.add_command(label="200%", command=lambda: self.set_zoom("200%"))
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="显示编辑工具",
            variable=self.edit_panel_visible_var,
            command=self.toggle_edit_panel,
        )
        view_menu.add_checkbutton(
            label="显示缩略图侧栏",
            accelerator="Ctrl+B",
            variable=self.thumbnail_sidebar_visible_var,
            command=self.toggle_thumbnail_sidebar,
        )
        view_menu.add_command(label="全屏", accelerator="F11", command=self.toggle_fullscreen)
        view_menu.add_command(
            label="沉浸式看图", accelerator="Ctrl+F11", command=self.toggle_immersive
        )
        view_menu.add_command(
            label="开始/停止幻灯片", accelerator="F5", command=self.toggle_slideshow
        )
        view_menu.add_command(label="幻灯片设置...", command=self.configure_slideshow)
        self.menu_bar.add_cascade(label="视图", menu=view_menu, underline=0)

        info_menu = tk.Menu(self.menu_bar, tearoff=False)
        info_menu.add_command(
            label="全部功能...", accelerator="Ctrl+K", command=self._show_feature_palette
        )
        info_menu.add_separator()
        info_menu.add_command(
            label="图片属性", accelerator="Alt+Enter", command=self.show_properties
        )
        info_menu.add_command(label="导出问题报告...", command=self.export_diagnostic_report)
        info_menu.add_command(label="快捷键帮助", accelerator="F1", command=self.show_shortcut_help)
        info_menu.add_command(label="首次使用引导", command=self.show_onboarding)
        self.menu_bar.add_cascade(label="信息", menu=info_menu, underline=0)

        self.config(menu=self.menu_bar)
        self._refresh_recent_menu()
        self._refresh_recent_directory_menu()
        self._refresh_recent_project_menu()
        self._refresh_recent_export_menu()
        self.localizer.apply_menu(self.menu_bar)

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.delete(0, "end")
        existing = [path for path in self.recent_files if Path(path).exists()]
        self.recent_files = existing[:8]
        if not self.recent_files:
            self.recent_menu.add_command(label="暂无", state="disabled")
            return
        for path in self.recent_files:
            label = Path(path).name
            self.recent_menu.add_command(label=label, command=partial(self.open_path, path))

    def _refresh_recent_directory_menu(self) -> None:
        self.recent_directory_menu.delete(0, "end")
        existing = [path for path in self.recent_directories if Path(path).is_dir()]
        self.recent_directories = existing[:8]
        if not self.recent_directories:
            self.recent_directory_menu.add_command(label="暂无", state="disabled")
            return
        for path in self.recent_directories:
            self.recent_directory_menu.add_command(
                label=path,
                command=partial(self.open_paths, [path]),
            )

    def _refresh_recent_project_menu(self) -> None:
        self.recent_project_menu.delete(0, "end")
        self.recent_projects = [path for path in self.recent_projects if Path(path).exists()][:8]
        if not self.recent_projects:
            self.recent_project_menu.add_command(label="暂无", state="disabled")
            return
        for path in self.recent_projects:
            self.recent_project_menu.add_command(
                label=Path(path).name,
                command=partial(self.open_project_path, path),
            )

    def _refresh_recent_export_menu(self) -> None:
        self.recent_export_menu.delete(0, "end")
        self.recent_export_directories = [
            path for path in self.recent_export_directories if Path(path).is_dir()
        ][:8]
        if not self.recent_export_directories:
            self.recent_export_menu.add_command(label="暂无", state="disabled")
            return
        for path in self.recent_export_directories:
            self.recent_export_menu.add_command(
                label=path, command=partial(open_folder, Path(path))
            )

    def _slider(
        self, parent: tk.Misc, label: str, var: tk.IntVar, start: int, end: int, row: int, command
    ) -> None:
        ttk.Label(parent, text=label, style="LibraryMeta.TLabel").grid(
            row=row, column=0, sticky="w", pady=1
        )
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky="ew", pady=1)
        frame.columnconfigure(0, weight=1)
        scale = MaterialSlider(
            frame,
            from_=start,
            to=end,
            variable=var,
            command=command,
        )
        scale.grid(row=0, column=0, sticky="ew")
        scale.bind("<<MaterialSliderStart>>", self._begin_slider_command)
        scale.bind("<<MaterialSliderEnd>>", self._end_slider_command)
        ttk.Label(frame, textvariable=var, width=4, anchor="e").grid(row=0, column=1, sticky="e")

    def _begin_slider_command(self, _event: tk.Event) -> None:
        self._active_slider_merge_key = uuid.uuid4().hex

    def _end_slider_command(self, _event: tk.Event) -> None:
        self._active_slider_merge_key = None

    def _action_callback(self, action: str):
        feature_ids = {
            "open": "viewer.open",
            "save": "convert.save_as",
            "save_project": "global.save_project",
            "undo": "edit.undo",
            "redo": "edit.redo",
            "rotate_right": "convert.rotate",
            "select": "edit.annotation.select",
            "crop": "convert.crop",
            "text": "edit.annotation.text",
            "arrow": "edit.annotation.arrow",
            "rectangle": "edit.annotation.rectangle",
            "mosaic": "edit.annotation.mosaic",
            "blur": "edit.annotation.blur",
        }
        return partial(self.invoke_feature, feature_ids[action])

    def _refresh_favorite_toolbar(self) -> None:
        if not hasattr(self, "favorite_toolbar"):
            return
        for child in self.favorite_toolbar.winfo_children():
            child.destroy()
        ttk.Label(
            self.favorite_toolbar,
            text="收藏工具",
            style="Eyebrow.TLabel",
        ).grid(row=0, column=0, padx=(0, 7))
        labels = {key: label for key, label, _shortcut in ACTION_CATALOG}
        for column, action in enumerate(self.favorite_actions, start=1):
            if action not in labels:
                continue
            button = MaterialButton(
                self.favorite_toolbar,
                text=labels[action],
                command=self._action_callback(action),
                width=max(52, len(labels[action]) * 14 + 20),
                height=28,
            )
            button.grid(row=0, column=column, padx=1)
            set_accessible_name(button, f"收藏工具：{labels[action]}")
        configure_button = MaterialButton(
            self.favorite_toolbar,
            text="配置…",
            command=self.configure_workflow,
            width=58,
            height=28,
            variant="text",
        )
        configure_button.grid(row=0, column=len(self.favorite_actions) + 1, padx=(7, 0))
        set_accessible_name(configure_button, "配置收藏工具与快捷键")

    def _apply_custom_shortcuts(self) -> None:
        for sequence in self._custom_bound_sequences | self._viewer_bound_sequences:
            self.unbind(sequence)
        self._custom_bound_sequences.clear()
        self._viewer_bound_sequences.clear()
        valid_actions = {key for key, _label, _shortcut in ACTION_CATALOG}
        for action, sequence in self.custom_shortcuts.items():
            if action not in valid_actions or not sequence:
                continue
            try:
                self.bind(
                    sequence,
                    lambda event, current=action: self._invoke_custom_shortcut(current, event),
                )
            except tk.TclError:
                continue
            self._custom_bound_sequences.add(sequence)
        for action, sequences in self.viewer_shortcuts.items():
            if action not in VIEWER_SHORTCUT_DEFAULTS:
                continue
            for sequence in sequences:
                if not sequence:
                    continue
                try:
                    self.bind(
                        sequence,
                        lambda event, current=action, bound=sequence: self._invoke_viewer_shortcut(
                            current, bound, event
                        ),
                    )
                except tk.TclError:
                    continue
                self._viewer_bound_sequences.add(sequence)

    def _invoke_viewer_shortcut(
        self,
        action: str,
        sequence: str,
        event: tk.Event,
    ) -> str | None:
        if self._text_input_has_focus():
            return None
        if self.mode_router.active_mode is ModeId.VIEWER:
            if action == "previous":
                self.navigate_folder(-1)
            elif action == "next":
                self.navigate_folder(1)
            elif action == "zoom_in":
                self.step_zoom(1)
            elif action == "zoom_out":
                self.step_zoom(-1)
            elif action == "toggle_toolbar":
                self.toggle_viewer_toolbar()
            elif action == "toggle_clean":
                self.toggle_viewer_clean_mode()
            elif action == "toggle_topmost":
                self.toggle_viewer_topmost()
            elif action == "toggle_favorite":
                self.toggle_viewer_favorite()
            return "break"

        custom_action = next(
            (
                current
                for current, custom_sequence in self.custom_shortcuts.items()
                if custom_sequence == sequence
            ),
            None,
        )
        if custom_action is not None:
            return self._invoke_custom_shortcut(custom_action, event)
        if sequence in {"<Left>", "<KP_Left>"}:
            return self._navigation_key(event, -1)
        if sequence in {"<Right>", "<KP_Right>"}:
            return self._navigation_key(event, 1)
        return None

    def _invoke_custom_shortcut(self, action: str, event: tk.Event) -> str | None:
        # Undo/redo are contextual document commands.  Routing them through the
        # feature registry would select ``edit.undo``/``edit.redo`` and switch a
        # viewer-only window into the full edit workspace before doing the work.
        # Keep the active shell unchanged, matching the toolbar's direct undo.
        if action == "undo":
            if self.mode_router.active_mode is ModeId.VIEWER and self._viewer_markup_active:
                self._undo_viewer_markup()
            else:
                self.undo()
            return "break"
        if action == "redo":
            self.redo()
            return "break"
        if action in {"select", "crop", "text", "arrow", "rectangle", "mosaic", "blur"}:
            widget_class = event.widget.winfo_class() if event.widget is not None else ""
            if widget_class in {"Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox"}:
                return None
        self._action_callback(action)()
        return "break"

    def configure_workflow(self) -> None:
        window = tk.Toplevel(self)
        window.title("收藏与快捷键")
        window.geometry("560x560")
        window.transient(self)
        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="选择收藏操作（可多选）").pack(anchor="w")
        favorite_list = tk.Listbox(body, selectmode="multiple", exportselection=False, height=10)
        favorite_list.pack(fill="x", pady=(5, 12))
        labels: dict[str, str] = {}
        for index, (key, label, _shortcut) in enumerate(ACTION_CATALOG):
            labels[key] = label
            favorite_list.insert("end", f"{label}  [{key}]")
            if key in self.favorite_actions:
                favorite_list.selection_set(index)

        ttk.Label(body, text="快捷键（Tk 格式，例如 <Control-Alt-t>）").pack(anchor="w")
        shortcut_canvas = tk.Canvas(body, height=250, highlightthickness=0)
        shortcut_scroll = ttk.Scrollbar(body, orient="vertical", command=shortcut_canvas.yview)
        shortcut_frame = ttk.Frame(shortcut_canvas)
        shortcut_frame.bind(
            "<Configure>",
            lambda _event: shortcut_canvas.configure(scrollregion=shortcut_canvas.bbox("all")),
        )
        shortcut_canvas.create_window(0, 0, window=shortcut_frame, anchor="nw")
        shortcut_canvas.configure(yscrollcommand=shortcut_scroll.set)
        shortcut_canvas.pack(side="left", fill="both", expand=True, pady=(5, 10))
        shortcut_scroll.pack(side="right", fill="y", pady=(5, 10))
        shortcut_vars: dict[str, tk.StringVar] = {}
        for row, (key, label, default) in enumerate(ACTION_CATALOG):
            ttk.Label(shortcut_frame, text=label, width=16).grid(
                row=row, column=0, sticky="w", pady=2
            )
            variable = tk.StringVar(value=self.custom_shortcuts.get(key, default))
            shortcut_vars[key] = variable
            ttk.Entry(shortcut_frame, textvariable=variable, width=28).grid(
                row=row, column=1, sticky="ew", pady=2
            )

        def save_workflow() -> None:
            selected = [ACTION_CATALOG[index][0] for index in favorite_list.curselection()]
            shortcuts = {key: variable.get().strip() for key, variable in shortcut_vars.items()}
            used = [sequence for sequence in shortcuts.values() if sequence]
            if len(used) != len(set(used)):
                messagebox.showerror(APP_NAME, "快捷键不能重复。", parent=window)
                return
            for sequence in used:
                try:
                    window.bind(sequence, lambda _event: None)
                    window.unbind(sequence)
                except tk.TclError:
                    messagebox.showerror(APP_NAME, f"快捷键格式无效：{sequence}", parent=window)
                    return
            self.favorite_actions = selected
            self.custom_shortcuts = shortcuts
            self._refresh_favorite_toolbar()
            self._apply_custom_shortcuts()
            self._save_config()
            window.destroy()

        buttons = ttk.Frame(window, padding=(14, 0, 14, 14))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="保存", command=save_workflow).pack(side="right")
        ttk.Button(buttons, text="取消", command=window.destroy).pack(side="right", padx=(0, 8))

    @staticmethod
    def _viewer_shortcut_display(sequences: Sequence[str]) -> str:
        labels = {
            "<Left>": "←",
            "<Right>": "→",
            "<Up>": "↑",
            "<Down>": "↓",
            "<KP_Left>": "小键盘←",
            "<KP_Right>": "小键盘→",
            "<KP_Up>": "小键盘↑",
            "<KP_Down>": "小键盘↓",
        }
        values: list[str] = []
        for sequence in sequences:
            if sequence in labels:
                values.append(labels[sequence])
            elif sequence.startswith("<Key-") and sequence.endswith(">"):
                values.append(sequence[5:-1].upper())
            elif sequence.startswith("<") and sequence.endswith(">"):
                values.append(sequence[1:-1])
            else:
                values.append(sequence)
        return "，".join(values)

    @staticmethod
    def _normalize_viewer_shortcut_token(token: str) -> str:
        value = token.strip()
        aliases = {
            "←": "<Left>",
            "左": "<Left>",
            "LEFT": "<Left>",
            "→": "<Right>",
            "右": "<Right>",
            "RIGHT": "<Right>",
            "↑": "<Up>",
            "上": "<Up>",
            "UP": "<Up>",
            "↓": "<Down>",
            "下": "<Down>",
            "DOWN": "<Down>",
            "小键盘←": "<KP_Left>",
            "小键盘→": "<KP_Right>",
            "小键盘↑": "<KP_Up>",
            "小键盘↓": "<KP_Down>",
        }
        if not value:
            raise ValueError("按键不能为空")
        aliased = aliases.get(value.upper(), aliases.get(value))
        if aliased is not None:
            return aliased
        if value.startswith("<") and value.endswith(">"):
            return value
        if len(value) == 1 and value.isprintable():
            return f"<Key-{value.lower()}>"
        if value.upper().startswith("F") and value[1:].isdigit():
            return f"<{value.upper()}>"
        raise ValueError(f"无法识别按键：{value}")

    @classmethod
    def _parse_viewer_shortcut_text(cls, text: str) -> list[str]:
        normalized = text.replace("、", ",").replace("，", ",").replace("；", ",")
        values: list[str] = []
        for token in normalized.split(","):
            if not token.strip():
                continue
            sequence = cls._normalize_viewer_shortcut_token(token)
            if sequence not in values:
                values.append(sequence)
        return values

    @staticmethod
    def _parse_viewer_zoom_presets(text: str) -> list[str]:
        normalized = text.replace("、", ",").replace("，", ",").replace("；", ",")
        values: list[str] = []
        for token in normalized.split(","):
            value = token.strip()
            if not value:
                continue
            if value in {"适应", "适合", "适合窗口"}:
                preset = "适应"
            else:
                raw_percent = value.removesuffix("%").strip()
                try:
                    percent = int(raw_percent)
                except ValueError as error:
                    raise ValueError(f"无效缩放刻度：{value}") from error
                if not MIN_ZOOM_PERCENT <= percent <= MAX_ZOOM_PERCENT:
                    raise ValueError(
                        f"缩放刻度需在 {MIN_ZOOM_PERCENT}% 至 {MAX_ZOOM_PERCENT}% 之间"
                    )
                preset = f"{percent}%"
            if preset not in values:
                values.append(preset)
        if "适应" not in values:
            values.insert(0, "适应")
        else:
            values.remove("适应")
            values.insert(0, "适应")
        if len(values) > 8:
            raise ValueError("缩放刻度最多设置 8 个")
        return values

    def _apply_viewer_preferences(
        self,
        *,
        shortcuts: dict[str, list[str]],
        zoom_step_percent: int,
        zoom_presets: list[str],
        toolbar_visible: bool,
        toolbar_mode: str | None = None,
        topmost_enabled: bool | None = None,
        save: bool = True,
    ) -> None:
        self.viewer_shortcuts = {
            action: list(shortcuts.get(action, [])) for action in VIEWER_SHORTCUT_DEFAULTS
        }
        self.viewer_zoom_step_percent = max(1, min(100, int(zoom_step_percent)))
        self.viewer_zoom_presets = list(zoom_presets)
        self.viewer_toolbar_visible_var.set(bool(toolbar_visible))
        if toolbar_mode in VIEWER_TOOLBAR_MODE_LABELS:
            self.viewer_toolbar_mode_var.set(toolbar_mode)
        if topmost_enabled is not None:
            self._set_viewer_topmost(bool(topmost_enabled))
        self._viewer_toolbar_auto_visible = False
        self._apply_custom_shortcuts()
        self._rebuild_zoom_preset_buttons()
        self._update_command_dock_for_mode(self.mode_router.active_mode)
        self._place_canvas_status()
        if save:
            self._save_config()

    def toggle_viewer_toolbar(self) -> str:
        self.viewer_toolbar_visible_var.set(not self.viewer_toolbar_visible_var.get())
        if self.viewer_toolbar_visible_var.get():
            self._viewer_toolbar_auto_visible = self.viewer_toolbar_mode_var.get() == "auto"
        self._update_command_dock_for_mode(self.mode_router.active_mode)
        self.status_var.set(
            "已显示顶部工具栏" if self.viewer_toolbar_visible_var.get() else "已隐藏顶部工具栏"
        )
        self._save_config()
        return "break"

    def toggle_viewer_clean_mode(self) -> str:
        if self.mode_router.active_mode is not ModeId.VIEWER:
            return "break"
        if not self._viewer_clean_mode and self._viewer_markup_active:
            self.status_var.set("请先在轻标注栏选择：仅复制、替换原图或取消修改")
            return "break"
        self._viewer_clean_mode = not self._viewer_clean_mode
        self._hide_zoom_preset_popup()
        self._update_command_dock_for_mode(self.mode_router.active_mode)
        self._place_canvas_status()
        self.status_var.set(
            "已进入纯净阅图，按 Tab 或 Esc 恢复界面"
            if self._viewer_clean_mode
            else "已退出纯净阅图"
        )
        return "break"

    def _set_viewer_markup_tool(self, tool: str) -> None:
        if tool not in {"rectangle", "arrow"}:
            return
        self._viewer_markup_tool = tool
        self._set_annotation_tool(tool)
        for name, button in self.viewer_markup_tool_buttons.items():
            button.set_selected(name == tool)
        self.status_var.set(f"轻标注：拖动绘制{ANNOTATION_TOOL_LABELS[tool]}")

    def _set_viewer_markup_active(self, enabled: bool) -> bool:
        active = bool(enabled)
        if active and self.document is None:
            self.status_var.set("请先打开一张图片，再使用轻标注")
            return False
        if self._viewer_markup_active == active:
            return True
        if active and self.document is not None:
            self._viewer_markup_timeline_snapshot = copy.deepcopy(self.document.timeline)
        self._viewer_markup_active = active
        self.dock_markup_button.set_selected(active)
        set_accessible_name(
            self.dock_markup_button,
            "退出轻标注" if active else "轻标注：矩形框与指向箭头",
        )
        self._clear_annotation_drag(redraw=False)
        self.selected_annotation_id = None
        if active:
            self._set_viewer_markup_tool(self._viewer_markup_tool)
        else:
            self._set_annotation_tool("select")
        self._place_canvas_status()
        return True

    def _close_viewer_markup_ui(self, message: str) -> None:
        self._viewer_markup_active = False
        self._viewer_markup_timeline_snapshot = None
        self.dock_markup_button.set_selected(False)
        set_accessible_name(
            self.dock_markup_button,
            "轻标注：矩形框与指向箭头",
        )
        self._clear_annotation_drag(redraw=False)
        self.selected_annotation_id = None
        self._set_annotation_tool("select")
        self._place_canvas_status()
        self.status_var.set(message)

    def _restore_viewer_markup_snapshot(self) -> None:
        if self.document is None or self._viewer_markup_timeline_snapshot is None:
            return
        self.document.timeline = copy.deepcopy(self._viewer_markup_timeline_snapshot)
        self._sync_controls_from_timeline()
        self._refresh_history_panel()
        self._refresh_preview()

    def _undo_viewer_markup(self) -> None:
        if self.document is None or self._viewer_markup_timeline_snapshot is None:
            return
        baseline = self._viewer_markup_timeline_snapshot.cursor
        if self.document.timeline.cursor <= baseline:
            self.status_var.set("本次轻标注没有可撤销的内容")
            return
        self.document.undo_command()
        self._refresh_history_panel()
        self._refresh_preview()
        self.status_var.set("已撤销上一处轻标注")

    def _cancel_viewer_markup(self) -> None:
        self._restore_viewer_markup_snapshot()
        self._close_viewer_markup_ui("已取消本次全部修改")

    def _finish_viewer_markup_copy(self) -> None:
        if self.original is None or self.document is None:
            return
        result: Image.Image | None = None
        try:
            result = render_image(self.original, self.document.timeline)
            copy_image_to_clipboard(result)
            result_size = result.size
        except Exception as error:
            messagebox.showerror(APP_NAME, f"复制失败：{error}")
            return
        finally:
            if result is not None:
                result.close()
        self._restore_viewer_markup_snapshot()
        self._close_viewer_markup_ui(
            f"已复制标注结果：{result_size[0]} × {result_size[1]}，原图未修改"
        )

    def _finish_viewer_markup_replace(self) -> None:
        if self.document is None or self.original is None or self.image_path is None:
            self.status_var.set("当前图片没有可替换的本地原文件")
            return
        source_format = (self.document.metadata.original_format or "").upper()
        source_format = {"JPG": "JPEG", "TIF": "TIFF"}.get(source_format, source_format)
        if source_format not in {"JPEG", "PNG", "WEBP", "AVIF", "BMP", "TIFF", "GIF"}:
            messagebox.showinfo(
                APP_NAME,
                "当前原图格式不支持安全写回，请使用“仅复制”。",
            )
            return
        options = self._read_options()
        if options is None:
            return
        options = replace(options, output_format=source_format, target_size_kb=None)
        saved = self._save_document_image(self.image_path, options)
        if saved is None:
            return
        self._mark_clean(saved)
        self.session_store.clear()
        self._close_viewer_markup_ui("已替换原图，并保留安全备份设置")
        self.open_path(saved, confirm=False)

    def toggle_viewer_markup(self) -> str:
        if self.mode_router.active_mode is not ModeId.VIEWER:
            return "break"
        if self._viewer_markup_active:
            self._place_canvas_status()
            self.status_var.set("请在轻标注栏选择：仅复制、替换原图或取消修改")
        else:
            self._set_viewer_markup_active(True)
        return "break"

    def _set_viewer_topmost(self, enabled: bool) -> None:
        self.viewer_topmost_var.set(bool(enabled))
        try:
            self.attributes("-topmost", bool(enabled))
        except tk.TclError:
            pass
        if hasattr(self, "viewer_topmost_button"):
            self.viewer_topmost_button.set_selected(bool(enabled))
            set_accessible_name(
                self.viewer_topmost_button,
                "窗口已置顶，点击取消（P）" if enabled else "窗口置顶（P）",
            )

    def toggle_viewer_topmost(self) -> str:
        enabled = not self.viewer_topmost_var.get()
        self._set_viewer_topmost(enabled)
        self.status_var.set("窗口已置顶" if enabled else "已取消窗口置顶")
        self._save_config()
        return "break"

    @staticmethod
    def _viewer_favorite_key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    @classmethod
    def _viewer_paths_match(cls, left: Path, right: Path) -> bool:
        if cls._viewer_favorite_key(left) == cls._viewer_favorite_key(right):
            return True
        try:
            return os.path.samefile(left, right)
        except (OSError, ValueError):
            return False

    def _viewer_image_is_favorite(self, path: Path | None = None) -> bool:
        selected = self.image_path if path is None else path
        if selected is None:
            return False
        return any(
            self._viewer_paths_match(Path(saved_path), selected)
            for saved_path in self.viewer_favorite_paths
        )

    def _update_viewer_favorite_button(self) -> None:
        if not hasattr(self, "dock_favorite_button"):
            return
        available = self.image_path is not None
        selected = available and self._viewer_image_is_favorite()
        self.dock_favorite_button.state(("!disabled",) if available else ("disabled",))
        self.dock_favorite_button.set_selected(bool(selected))
        self.dock_favorite_button.set_text("★" if selected else "☆")
        set_accessible_name(
            self.dock_favorite_button,
            "取消收藏当前图片（F）" if selected else "收藏当前图片（F）",
        )

    def toggle_viewer_favorite(self) -> str:
        if self.mode_router.active_mode is not ModeId.VIEWER:
            return "break"
        path = self.image_path
        if path is None:
            self.status_var.set("当前图片没有可收藏的文件路径")
            return "break"
        matching = [
            saved_path
            for saved_path in self.viewer_favorite_paths
            if self._viewer_paths_match(Path(saved_path), path)
        ]
        if matching:
            self.viewer_favorite_paths = [
                saved_path
                for saved_path in self.viewer_favorite_paths
                if not self._viewer_paths_match(Path(saved_path), path)
            ]
            message = f"已取消收藏 · {path.name}"
        else:
            self.viewer_favorite_paths.insert(0, os.path.abspath(os.fspath(path)))
            del self.viewer_favorite_paths[2000:]
            message = f"已收藏 · {path.name}"
        self._update_viewer_favorite_button()
        self._update_viewer_gallery_button()
        self.status_var.set(message)
        self._save_config()
        return "break"

    def _update_viewer_toolbar_auto_visibility(self, pointer_y: int) -> None:
        if (
            self.mode_router.active_mode is not ModeId.VIEWER
            or self.viewer_toolbar_mode_var.get() != "auto"
            or not self.viewer_toolbar_visible_var.get()
            or self._viewer_clean_mode
        ):
            return
        if pointer_y <= 96:
            if self._viewer_toolbar_auto_hide_job is not None:
                try:
                    self.after_cancel(self._viewer_toolbar_auto_hide_job)
                except tk.TclError:
                    pass
                self._viewer_toolbar_auto_hide_job = None
            self._animate_auto_viewer_toolbar(True)
            return
        if self._viewer_toolbar_auto_visible and self._viewer_toolbar_auto_hide_job is None:
            self._viewer_toolbar_auto_hide_job = self.after(
                700,
                self._hide_auto_viewer_toolbar,
            )

    def _hide_auto_viewer_toolbar(self) -> None:
        self._viewer_toolbar_auto_hide_job = None
        if self.viewer_toolbar_mode_var.get() != "auto" or self._viewer_clean_mode:
            return
        self._animate_auto_viewer_toolbar(False)

    def _set_viewer_toolbar_visual_opacity(self, opacity: float) -> None:
        bounded = max(0.0, min(1.0, float(opacity)))
        # On Windows the complete dock subtree is faded by DWM.  This avoids
        # regenerating ten PIL/ImageTk button surfaces on every animation frame
        # and lets the compositor sustain high-refresh displays smoothly.
        if self.command_dock.set_visual_opacity(bounded):
            return
        for widget in self._dock_widgets_by_mode.get(ModeId.VIEWER, ()):
            if isinstance(widget, MaterialButton):
                widget.set_visual_opacity(bounded)

        tokens = self.theme_manager.tokens
        canvas_rgb = ImageColor.getrgb(tokens.canvas)
        border_rgb = ImageColor.getrgb(tokens.border)
        separator_rgb = tuple(
            round(canvas_rgb[index] + (border_rgb[index] - canvas_rgb[index]) * bounded)
            for index in range(3)
        )
        separator_color = "#{:02x}{:02x}{:02x}".format(*separator_rgb)
        ttk.Style(self).configure(
            "ViewerToolbarFade.TSeparator",
            background=separator_color,
        )
        for separator in (self.dock_primary_separator, self.dock_edit_separator):
            separator.configure(style="ViewerToolbarFade.TSeparator")

    def _animate_auto_viewer_toolbar(self, show: bool) -> None:
        if self.viewer_toolbar_mode_var.get() != "auto":
            return
        target = 1.0 if show else 0.0
        if self._viewer_toolbar_fade_job is not None and self._viewer_toolbar_fade_target == target:
            return
        if self._viewer_toolbar_fade_job is not None:
            try:
                self.after_cancel(self._viewer_toolbar_fade_job)
            except tk.TclError:
                pass
            self._viewer_toolbar_fade_job = None
        if show and not self._viewer_toolbar_auto_visible:
            self._viewer_toolbar_auto_visible = True
            self._viewer_toolbar_opacity = 0.0
            self._set_viewer_toolbar_visual_opacity(0.0)
            self._update_command_dock_for_mode(self.mode_router.active_mode)

        self._viewer_toolbar_fade_target = target
        start_opacity = self._viewer_toolbar_opacity
        started_at = time.perf_counter()
        duration_seconds = max(0.08, 0.24 * abs(target - start_opacity))

        def position_only() -> None:
            if not self.command_dock.winfo_ismapped():
                return
            titlebar_height = (
                self.window_titlebar.winfo_reqheight() if self.window_titlebar is not None else 0
            )
            self.command_dock.place_configure(
                y=(titlebar_height + 6 - round((1.0 - self._viewer_toolbar_opacity) * 18))
            )

        def step() -> None:
            self._viewer_toolbar_fade_job = None
            progress = min(
                1.0,
                max(0.0, (time.perf_counter() - started_at) / duration_seconds),
            )
            eased = progress * progress * (3.0 - 2.0 * progress)
            self._viewer_toolbar_opacity = start_opacity + (target - start_opacity) * eased
            self._set_viewer_toolbar_visual_opacity(self._viewer_toolbar_opacity)
            position_only()
            if progress >= 1.0:
                self._viewer_toolbar_fade_target = None
                self._viewer_toolbar_opacity = target
                self._set_viewer_toolbar_visual_opacity(target)
                if not show:
                    self._viewer_toolbar_auto_visible = False
                    self._update_command_dock_for_mode(self.mode_router.active_mode)
                return
            # DWM now composites the complete dock, so an 8 ms cadence can feed
            # 120 Hz displays without rebuilding every child control.
            self._viewer_toolbar_fade_job = self.after(8, step)

        step()

    def open_viewer_settings(self) -> None:
        existing = self._viewer_settings_window
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
        window = tk.Toplevel(self)
        self._viewer_settings_window = window
        window.title("看图设置")
        window.transient(self)
        window.resizable(False, False)
        self._fit_dialog(window, 620, 700)

        body = ttk.Frame(window, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="按键绑定", style="SectionTitle.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text="每个功能可填写多个按键，用逗号分隔，例如：A，←",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 10))

        shortcut_frame = ttk.Frame(body)
        shortcut_frame.pack(fill="x")
        shortcut_frame.columnconfigure(1, weight=1)
        shortcut_vars: dict[str, tk.StringVar] = {}
        first_entry: ttk.Entry | None = None
        for row, action in enumerate(VIEWER_SHORTCUT_DEFAULTS):
            ttk.Label(shortcut_frame, text=VIEWER_SHORTCUT_LABELS[action]).grid(
                row=row,
                column=0,
                sticky="w",
                padx=(0, 18),
                pady=5,
            )
            variable = tk.StringVar(
                value=self._viewer_shortcut_display(self.viewer_shortcuts[action])
            )
            shortcut_vars[action] = variable
            entry = ttk.Entry(shortcut_frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            if first_entry is None:
                first_entry = entry

        display_frame = ttk.LabelFrame(body, text="工具栏与缩放", padding=12)
        display_frame.pack(fill="x", pady=(18, 0))
        display_frame.columnconfigure(1, weight=1)
        toolbar_mode = tk.StringVar(value=self.viewer_toolbar_mode_var.get())
        topmost_enabled = tk.BooleanVar(value=self.viewer_topmost_var.get())
        ttk.Label(
            display_frame,
            text="工具栏显示方式",
        ).grid(row=0, column=0, sticky="w", padx=(0, 18), pady=5)
        toolbar_mode_choices = ttk.Frame(display_frame)
        toolbar_mode_choices.grid(row=0, column=1, sticky="w", pady=5)
        for mode, label in VIEWER_TOOLBAR_MODE_LABELS.items():
            ttk.Radiobutton(
                toolbar_mode_choices,
                text=label,
                variable=toolbar_mode,
                value=mode,
            ).pack(side="left", padx=(0, 18))
        ttk.Label(display_frame, text="窗口显示").grid(
            row=1, column=0, sticky="w", padx=(0, 18), pady=5
        )
        ttk.Checkbutton(
            display_frame,
            text="始终置于其他窗口上方",
            variable=topmost_enabled,
        ).grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(display_frame, text="每次缩放幅度（%）").grid(
            row=2, column=0, sticky="w", padx=(0, 18), pady=5
        )
        zoom_step = tk.IntVar(value=self.viewer_zoom_step_percent)
        ttk.Entry(
            display_frame,
            textvariable=zoom_step,
            width=8,
        ).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Label(display_frame, text="缩放刻度").grid(
            row=3, column=0, sticky="w", padx=(0, 18), pady=5
        )
        zoom_presets = tk.StringVar(value="，".join(self.viewer_zoom_presets))
        ttk.Entry(display_frame, textvariable=zoom_presets).grid(
            row=3, column=1, sticky="ew", pady=5
        )
        ttk.Label(
            display_frame,
            text="支持 10% 至 800%，最多 8 个；“适应”会自动保留。",
            style="Muted.TLabel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(3, 0))

        def restore_defaults() -> None:
            for action, sequences in VIEWER_SHORTCUT_DEFAULTS.items():
                shortcut_vars[action].set(self._viewer_shortcut_display(sequences))
            zoom_step.set(10)
            zoom_presets.set("，".join(DEFAULT_VIEWER_ZOOM_PRESETS))
            toolbar_mode.set("fixed")
            topmost_enabled.set(False)

        def close_window() -> None:
            self._viewer_settings_window = None
            window.destroy()

        def save_settings() -> None:
            try:
                shortcuts = {
                    action: self._parse_viewer_shortcut_text(variable.get())
                    for action, variable in shortcut_vars.items()
                }
                all_sequences = [
                    sequence for sequences in shortcuts.values() for sequence in sequences
                ]
                if len(all_sequences) != len(set(all_sequences)):
                    raise ValueError("同一个按键不能同时绑定多个看图功能")
                for sequence in all_sequences:
                    probe = window.bind(sequence, lambda _event: None)
                    window.unbind(sequence, probe)
                step = max(1, min(100, int(zoom_step.get())))
                presets = self._parse_viewer_zoom_presets(zoom_presets.get())
                selected_toolbar_mode = toolbar_mode.get()
                if selected_toolbar_mode not in VIEWER_TOOLBAR_MODE_LABELS:
                    raise ValueError("请选择有效的工具栏显示方式")
            except (ValueError, tk.TclError) as error:
                messagebox.showerror(APP_NAME, str(error), parent=window)
                return
            self._apply_viewer_preferences(
                shortcuts=shortcuts,
                zoom_step_percent=step,
                zoom_presets=presets,
                toolbar_visible=True,
                toolbar_mode=selected_toolbar_mode,
                topmost_enabled=topmost_enabled.get(),
            )
            self.status_var.set("看图设置已保存")
            close_window()

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(18, 0))
        ttk.Button(buttons, text="恢复默认", command=restore_defaults).pack(side="left")
        ttk.Button(
            buttons,
            text="进入纯净阅图",
            command=lambda: (close_window(), self.toggle_viewer_clean_mode()),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="保存", command=save_settings, style="Accent.TButton").pack(
            side="right"
        )
        ttk.Button(buttons, text="取消", command=close_window).pack(side="right", padx=(0, 8))
        window.protocol("WM_DELETE_WINDOW", close_window)
        window.bind("<Escape>", lambda _event: close_window())
        window.bind("<Control-Return>", lambda _event: save_settings())
        self._name_interactive_widgets(window)
        if first_entry is not None:
            first_entry.focus_set()

    def open_settings(self) -> None:
        window = tk.Toplevel(self)
        window.title(self.localizer.gettext("settings_title"))
        window.transient(self)
        self._fit_dialog(window, 720, 560)
        window.minsize(560, 420)

        theme_labels = {
            "system": self.localizer.gettext("theme_system"),
            "light": self.localizer.gettext("theme_light"),
            "dark": self.localizer.gettext("theme_dark"),
        }
        theme_by_label = {label: key for key, label in theme_labels.items()}
        selected_theme = tk.StringVar(value=theme_labels[self.theme_mode_var.get()])
        selected_workers = tk.IntVar(value=self.decode_workers_var.get())
        selected_cache = tk.IntVar(value=self.preview_cache_var.get())

        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=14, pady=(14, 8))

        general = ttk.Frame(notebook, padding=18)
        performance = ttk.Frame(notebook, padding=18)
        saving = ttk.Frame(notebook, padding=18)
        privacy = ttk.Frame(notebook, padding=18)
        updates = ttk.Frame(notebook, padding=18)
        associations = ttk.Frame(notebook, padding=18)
        notebook.add(general, text=self.localizer.gettext("settings_general"))
        notebook.add(performance, text=self.localizer.gettext("settings_performance"))
        notebook.add(saving, text=self.localizer.gettext("settings_save"))
        notebook.add(privacy, text=self.localizer.gettext("settings_privacy"))
        notebook.add(updates, text=self.localizer.gettext("settings_updates"))
        notebook.add(associations, text=self.localizer.gettext("settings_associations"))

        general.columnconfigure(1, weight=1)
        ttk.Label(general, text=self.localizer.gettext("theme")).grid(
            row=0, column=0, sticky="w", padx=(0, 18), pady=8
        )
        theme_box = ttk.Combobox(
            general,
            state="readonly",
            textvariable=selected_theme,
            values=tuple(theme_labels.values()),
        )
        theme_box.grid(row=0, column=1, sticky="ew", pady=8)
        performance.columnconfigure(1, weight=1)
        ttk.Label(performance, text=self.localizer.gettext("decode_workers")).grid(
            row=0, column=0, sticky="w", padx=(0, 18), pady=8
        )
        ttk.Spinbox(performance, from_=1, to=8, textvariable=selected_workers, width=10).grid(
            row=0, column=1, sticky="w", pady=8
        )
        ttk.Label(performance, text=self.localizer.gettext("preview_cache")).grid(
            row=1, column=0, sticky="w", padx=(0, 18), pady=8
        )
        ttk.Spinbox(performance, from_=1, to=20, textvariable=selected_cache, width=10).grid(
            row=1, column=1, sticky="w", pady=8
        )
        ttk.Label(performance, text=self.localizer.gettext("batch_concurrency")).grid(
            row=2, column=0, sticky="w", padx=(0, 18), pady=8
        )
        ttk.Spinbox(
            performance, from_=1, to=8, textvariable=self.batch_concurrency_var, width=10
        ).grid(row=2, column=1, sticky="w", pady=8)
        ttk.Label(performance, text=self.localizer.gettext("batch_memory")).grid(
            row=3, column=0, sticky="w", padx=(0, 18), pady=8
        )
        ttk.Spinbox(
            performance,
            from_=128,
            to=8192,
            increment=128,
            textvariable=self.batch_memory_var,
            width=10,
        ).grid(row=3, column=1, sticky="w", pady=8)

        ttk.Checkbutton(
            saving,
            text=self.localizer.gettext("preserve_metadata"),
            variable=self.preserve_metadata_var,
        ).pack(anchor="w", pady=8)
        ttk.Checkbutton(
            saving,
            text=self.localizer.gettext("backup_overwrite"),
            variable=self.backup_on_overwrite_var,
        ).pack(anchor="w", pady=8)
        ttk.Checkbutton(
            saving,
            text=self.localizer.gettext("reveal_after_save"),
            variable=self.reveal_after_save_var,
        ).pack(anchor="w", pady=8)

        ttk.Label(
            privacy,
            text=privacy_summary(),
            wraplength=600,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            privacy,
            text=self.localizer.gettext("offline_ready"),
            style="Accent.TLabel",
        ).pack(anchor="w", pady=(18, 0))
        ttk.Checkbutton(
            privacy,
            text=self.localizer.gettext("crash_consent"),
            variable=self.crash_reporting_consent_var,
        ).pack(anchor="w", pady=(18, 8))
        ttk.Button(
            privacy,
            text=self.localizer.gettext("export_diagnostic_report"),
            command=self.export_diagnostic_report,
        ).pack(anchor="w")
        ttk.Label(
            updates,
            text=self.localizer.gettext("update_text"),
            wraplength=600,
            justify="left",
        ).pack(anchor="w")
        ttk.Radiobutton(
            updates,
            text=self.localizer.gettext("stable_channel"),
            variable=self.update_channel_var,
            value="stable",
        ).pack(anchor="w", pady=(18, 4))
        ttk.Radiobutton(
            updates,
            text=self.localizer.gettext("beta_channel"),
            variable=self.update_channel_var,
            value="beta",
        ).pack(anchor="w", pady=4)
        ttk.Button(
            updates,
            text=self.localizer.gettext("check_updates"),
            command=self.check_for_updates,
        ).pack(anchor="w", pady=(18, 6))
        rollback = UpdateCache().rollback_installer()
        ttk.Button(
            updates,
            text=self.localizer.gettext("rollback_update"),
            command=self.rollback_update,
            state="normal" if rollback else "disabled",
        ).pack(anchor="w")
        ttk.Label(
            associations,
            text=self.localizer.gettext("association_text"),
            wraplength=600,
            justify="left",
        ).pack(anchor="w")
        ttk.Button(
            associations,
            text=self.localizer.gettext("open_default_apps"),
            command=self._open_default_apps_settings,
        ).pack(anchor="w", pady=(18, 0))

        def bounded_int(variable: tk.IntVar, fallback: int, low: int, high: int) -> int:
            try:
                value = variable.get()
            except tk.TclError:
                value = fallback
            return max(low, min(high, value))

        def apply_settings() -> None:
            theme_mode = theme_by_label.get(selected_theme.get(), "system")
            self.theme_mode_var.set(theme_mode)
            self.decode_workers_var.set(
                bounded_int(selected_workers, self.decode_workers_var.get(), 1, 8)
            )
            self.preview_cache_var.set(
                bounded_int(selected_cache, self.preview_cache_var.get(), 1, 20)
            )
            self.thumbnail_sidebar.set_title("资源库")
            self._set_window_title(self.localizer.translate_text(APP_NAME))
            tokens = self.theme_manager.apply(theme_mode)
            self._last_theme_name = tokens.name
            self._apply_thumbnail_palette(tokens)
            self._refresh_accessible_names()
            if self.original is None:
                self._show_empty_state()
            self._save_config()
            self.status_var.set(self.localizer.gettext("settings_saved"))
            window.destroy()

        buttons = ttk.Frame(window, padding=(14, 0, 14, 14))
        buttons.pack(fill="x")
        apply_button = ttk.Button(
            buttons,
            text=self.localizer.gettext("apply"),
            command=apply_settings,
            style="Accent.TButton",
        )
        apply_button.pack(side="right")
        ttk.Button(
            buttons,
            text=self.localizer.gettext("close"),
            command=window.destroy,
        ).pack(side="right", padx=(0, 8))
        window.bind("<Escape>", lambda _event: window.destroy())
        window.bind("<Control-Return>", lambda _event: apply_settings())
        self.theme_manager.apply(self.theme_mode_var.get())
        self._name_interactive_widgets(window)
        theme_box.focus_set()

    def _open_default_apps_settings(self) -> None:
        if os.name != "nt":
            return
        try:
            startfile = getattr(os, "startfile")
            startfile("ms-settings:defaultapps?registeredAppUser=LightImageEditor")
        except OSError as error:
            messagebox.showerror(APP_NAME, str(error), parent=self)

    def export_diagnostic_report(self) -> Path | None:
        notes = simpledialog.askstring(
            APP_NAME,
            "请简要描述问题和复现步骤（报告会自动脱敏）：",
            parent=self,
        )
        if notes is None:
            return None
        target = filedialog.asksaveasfilename(
            title="导出脱敏问题报告",
            defaultextension=".zip",
            initialfile=f"LightImageEditor-diagnostics-{__version__}.zip",
            filetypes=[("ZIP", "*.zip")],
            parent=self,
        )
        if not target:
            return None
        try:
            saved = self.diagnostic_reporter.export(
                target,
                self.settings_store.load_migrated(),
                notes,
            )
        except OSError as error:
            messagebox.showerror(APP_NAME, f"问题报告导出失败：{error}", parent=self)
            return None
        self.status_var.set(f"脱敏问题报告已导出：{saved}")
        messagebox.showinfo(APP_NAME, f"问题报告已保存：\n{saved}", parent=self)
        return saved

    def _offer_crash_report(self) -> None:
        pending = self.crash_store.pending()
        if not pending:
            return
        should_export = messagebox.askyesno(
            APP_NAME,
            f"检测到 {len(pending)} 份尚未处理的崩溃记录。是否现在导出脱敏问题报告？",
            parent=self,
        )
        if should_export and self.export_diagnostic_report() is None:
            return
        for path in pending:
            try:
                self.crash_store.acknowledge(path)
            except (OSError, ValueError, TypeError):
                continue

    def _update_service(self) -> UpdateService:
        keys = load_trusted_keys(trusted_keys_path())
        if not keys:
            raise UpdateError("没有配置可信发布清单公钥")
        return UpdateService(keys)

    def check_for_updates(self) -> None:
        channel = self.update_channel_var.get()
        self._save_config()
        self.status_var.set("正在主动检查更新...")

        def worker() -> None:
            try:
                service = self._update_service()
                manifest = service.check(channel, __version__)
            except Exception as error:
                message = str(error)
                self.after(0, lambda: self._show_update_error(message))
                return
            self.after(0, lambda: self._offer_update(service, manifest))

        threading.Thread(target=worker, name="manual-update-check", daemon=True).start()

    def _show_update_error(self, message: str) -> None:
        self.status_var.set("更新检查安全失败")
        messagebox.showwarning(
            APP_NAME,
            f"未安装任何内容。\n\n{message}",
            parent=self,
        )

    def _offer_update(self, service: UpdateService, manifest: UpdateManifest | None) -> None:
        if manifest is None:
            self.status_var.set("当前通道已是最新版本")
            messagebox.showinfo(APP_NAME, "当前通道已是最新版本。", parent=self)
            return
        version = manifest.version
        if not messagebox.askyesno(
            APP_NAME,
            f"发现版本 {version}。是否下载并验证安装包？",
            parent=self,
        ):
            return
        self.status_var.set(f"正在下载并验证版本 {version}...")

        def worker() -> None:
            try:
                installer = service.download(manifest)
            except Exception as error:
                message = str(error)
                self.after(0, lambda: self._show_update_error(message))
                return
            self.after(0, lambda: self._launch_verified_update(installer, version))

        threading.Thread(target=worker, name="manual-update-download", daemon=True).start()

    def _launch_verified_update(self, installer: Path, version: str) -> None:
        self.status_var.set(f"版本 {version} 已通过全部安全校验")
        if not messagebox.askyesno(
            APP_NAME,
            "安装包的清单签名、哈希和 Authenticode 均已验证。现在安装吗？",
            parent=self,
        ):
            return
        try:
            launch_installer(installer)
        except UpdateError as error:
            self._show_update_error(str(error))

    def rollback_update(self) -> None:
        installer = UpdateCache().rollback_installer()
        if installer is None:
            messagebox.showinfo(APP_NAME, "没有可用的已验证回滚安装包。", parent=self)
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"将使用已验证安装包回滚：\n{installer.name}\n\n是否继续？",
            parent=self,
        ):
            return
        try:
            launch_installer(installer)
        except UpdateError as error:
            self._show_update_error(str(error))

    def show_shortcut_help(self) -> None:
        window = tk.Toplevel(self)
        window.title(self.localizer.gettext("help_title"))
        window.transient(self)
        self._fit_dialog(window, 660, 600)
        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)
        tree = ttk.Treeview(body, columns=("action", "shortcut"), show="headings")
        tree.heading("action", text=self.localizer.translate_text("操作"))
        tree.heading("shortcut", text=self.localizer.translate_text("快捷键"))
        tree.column("action", width=300, anchor="w")
        tree.column("shortcut", width=220, anchor="w")
        fixed_actions = (
            ("设置", "Ctrl+,"),
            ("快捷键帮助", "F1"),
            ("全部功能", "Ctrl+K"),
            ("切换焦点区域", "F6 / Shift+F6"),
            ("上一张 / 下一张", "← / →"),
            ("第一页 / 最后一页", "Home / End"),
            ("全屏", "F11"),
            ("幻灯片", "F5"),
            ("图片属性", "Alt+Enter"),
        )
        seen: set[tuple[str, str]] = set()
        for key, label, default in ACTION_CATALOG:
            row = (
                self.localizer.translate_text(label),
                self.custom_shortcuts.get(key, default),
            )
            if row not in seen:
                tree.insert("", "end", values=row)
                seen.add(row)
        for label, shortcut in fixed_actions:
            row = (self.localizer.translate_text(label), shortcut)
            if row not in seen:
                tree.insert("", "end", values=row)
        scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Button(
            window,
            text=self.localizer.gettext("close"),
            command=window.destroy,
        ).pack(anchor="e", padx=14, pady=(0, 14))
        window.bind("<Escape>", lambda _event: window.destroy())
        self.theme_manager.apply(self.theme_mode_var.get())
        self._name_interactive_widgets(window)
        tree.focus_set()

    def _offer_onboarding(self) -> None:
        if not self.onboarding_completed:
            self.show_onboarding()

    def show_onboarding(self) -> None:
        if self._onboarding_window is not None:
            try:
                if self._onboarding_window.winfo_exists():
                    self._onboarding_window.lift()
                    self._onboarding_window.focus_force()
                    return
            except tk.TclError:
                pass
        window = tk.Toplevel(self)
        self._onboarding_window = window
        window.title(self.localizer.gettext("onboarding_title"))
        window.transient(self)
        self._fit_dialog(window, 620, 500)
        body = ttk.Frame(window, padding=24)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=self.localizer.gettext("onboarding_title"),
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(
            body,
            text=self.localizer.gettext("onboarding_intro"),
            wraplength=540,
            justify="left",
        ).pack(anchor="w", pady=(0, 18))
        for number, key in enumerate(
            ("onboarding_open", "onboarding_view", "onboarding_edit", "onboarding_privacy"),
            start=1,
        ):
            ttk.Label(
                body,
                text=f"{number}. {self.localizer.gettext(key)}",
                wraplength=540,
                justify="left",
            ).pack(anchor="w", pady=7)

        def finish() -> None:
            self.onboarding_completed = True
            self._save_config()
            self._onboarding_window = None
            window.destroy()
            self.canvas.focus_set()

        button = ttk.Button(
            body,
            text=self.localizer.gettext("onboarding_done"),
            command=finish,
            style="Accent.TButton",
        )
        button.pack(anchor="e", pady=(24, 0))
        window.protocol("WM_DELETE_WINDOW", finish)
        window.bind("<Escape>", lambda _event: finish())
        window.bind("<Return>", lambda _event: finish())
        self.theme_manager.apply(self.theme_mode_var.get())
        self._name_interactive_widgets(window)
        button.focus_set()

    def _workspace_setting_changed(self) -> None:
        self.restore_workspace = self.restore_workspace_var.get()
        self._save_config()

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda _event: self.open_file())
        self.bind("<Control-Shift-o>", lambda _event: self.open_directory())
        self.bind(
            "<Control-v>",
            lambda _event: self._guarded_text_key_action(self.paste_from_clipboard),
        )
        self.bind("<Control-s>", lambda _event: self.save_as())
        self.bind("<Control-Shift-S>", lambda _event: self.save_project_dialog())
        self.bind("<Control-e>", lambda _event: self.quick_export())
        self.bind("<Control-Shift-C>", lambda _event: self.copy_result())
        self.bind("<Control-r>", lambda _event: self.rotate(90))
        self.bind("<Control-z>", lambda _event: self.undo())
        self.bind("<Control-y>", lambda _event: self.redo())
        self.bind("<Control-0>", lambda _event: self.reset_view())
        self.bind("<Control-1>", lambda _event: self.set_zoom("100%"))
        self.bind("<Control-b>", lambda _event: self.toggle_thumbnail_sidebar_from_shortcut())
        self.bind("<Control-plus>", lambda _event: self.step_zoom(1))
        self.bind("<Control-equal>", lambda _event: self.step_zoom(1))
        self.bind("<Control-minus>", lambda _event: self.step_zoom(-1))
        self.bind("<Left>", lambda event: self._navigation_key(event, -1))
        self.bind("<Right>", lambda event: self._navigation_key(event, 1))
        self.bind("<Prior>", lambda event: self._navigation_key(event, -1))
        self.bind("<Next>", lambda event: self._navigation_key(event, 1))
        self.bind("<Home>", lambda event: self._edge_navigation_key(event, "first"))
        self.bind("<End>", lambda event: self._edge_navigation_key(event, "last"))
        self.bind("<Escape>", lambda _event: self._handle_escape())
        self.bind(
            "<Return>",
            lambda _event: self._guarded_text_key_action(self.apply_current_tool),
        )
        self.bind("<space>", lambda _event: self._guarded_compare_key(True))
        self.bind("<KeyRelease-space>", lambda _event: self._guarded_compare_key(False))
        self.bind("<F11>", lambda _event: self.toggle_fullscreen())
        self.bind("<Control-F11>", lambda _event: self.toggle_immersive())
        self.bind("<F5>", lambda _event: self.toggle_slideshow())
        self.bind("<F2>", lambda _event: self.rename_current())
        self.bind("<Delete>", self._delete_key)
        self.bind("<Control-Shift-E>", lambda _event: self.reveal_current())
        self.bind("<Alt-Return>", lambda _event: self.show_properties())
        self.bind("<Control-comma>", lambda _event: self.open_settings())
        self.bind("<Control-k>", lambda _event: self._show_feature_palette())
        self.bind("<F1>", lambda _event: self.show_shortcut_help())
        self.bind("<F6>", lambda _event: self.focus_navigator.move())
        self.bind("<Shift-F6>", lambda _event: self.focus_navigator.move(reverse=True))
        self._apply_custom_shortcuts()

    def _text_input_has_focus(self) -> bool:
        focused = self.focus_get()
        return isinstance(focused, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox, ttk.Spinbox))

    def _guarded_text_key_action(self, action: Callable[[], object]) -> str | None:
        if self._text_input_has_focus():
            return None
        action()
        return "break"

    def _guarded_compare_key(self, enabled: bool) -> str | None:
        if self._text_input_has_focus():
            return None
        self._set_compare(enabled)
        return "break"

    def _navigation_key(self, _event: tk.Event, direction: int) -> str | None:
        if self._text_input_has_focus():
            return None
        self.navigate_folder(direction)
        return "break"

    def _edge_navigation_key(self, _event: tk.Event, edge: str) -> str | None:
        if self._text_input_has_focus():
            return None
        self.navigate_folder_to(edge)
        return "break"

    def _delete_key(self, _event: tk.Event) -> str | None:
        if self._text_input_has_focus():
            return None
        self.delete_selected_or_file()
        return "break"

    def _bind_document_variables(self) -> None:
        text_fields = (
            (self.format_var, "output_format"),
            (self.target_size_var, "target_size_text"),
            (self.filename_prefix_var, "filename_prefix"),
            (self.filename_suffix_var, "filename_suffix"),
            (self.width_var, "width_text"),
            (self.height_var, "height_text"),
            (self.max_side_var, "max_side_text"),
            (self.crop_ratio_var, "crop_ratio"),
            (self.watermark_text_var, "watermark_text"),
            (self.watermark_position_var, "watermark_position"),
            (self.export_preset_var, "export_preset"),
        )
        integer_fields = (
            (self.quality_var, "quality"),
            (self.watermark_opacity_var, "watermark_opacity"),
            (self.watermark_size_var, "watermark_size_percent"),
        )
        boolean_fields = (
            (self.preserve_metadata_var, "preserve_metadata"),
            (self.backup_on_overwrite_var, "backup_on_overwrite"),
        )
        for variable, field in text_fields:
            variable.trace_add(
                "write",
                lambda *_args, current=variable, name=field: self._set_document_field(
                    name, current.get()
                ),
            )
        for variable, field in integer_fields:
            variable.trace_add(
                "write",
                lambda *_args, current=variable, name=field: self._set_document_field(
                    name, int(current.get())
                ),
            )
        for variable, field in boolean_fields:
            variable.trace_add(
                "write",
                lambda *_args, current=variable, name=field: self._set_document_field(
                    name, bool(current.get())
                ),
            )
        for variable in (
            self.batch_rename_template_var,
            self.batch_conflict_var,
            self.batch_strip_metadata_var,
            self.batch_preserve_animation_var,
            self.conversion_background_var,
        ):
            variable.trace_add(
                "write",
                lambda *_args: self._update_conversion_draft_from_controls(),
            )
        self.conversion_output_dir_var.trace_add(
            "write",
            lambda *_args: self._conversion_output_directory_changed(),
        )
        edit_fields = (
            (self.brightness_var, "brightness"),
            (self.contrast_var, "contrast"),
            (self.saturation_var, "saturation"),
            (self.sharpness_var, "sharpness"),
            (self.grayscale_var, "grayscale"),
            (self.auto_contrast_var, "auto_contrast"),
        )
        for variable, field in edit_fields:
            variable.trace_add(
                "write",
                lambda *_args, current=variable, name=field: self._set_document_edit_field(
                    name, current.get()
                ),
            )

    def _set_document_field(self, field: str, value: object) -> None:
        if self._syncing_document:
            return
        if self.mode_router.active_mode is ModeId.CONVERT:
            self._update_conversion_draft_from_controls()
            return
        if self.document is None:
            return
        self.document.update_state(f"set_{field}", **{field: value})
        if field in {"width_text", "height_text", "max_side_text"}:
            self._commit_resize_from_state()
        elif field in {
            "watermark_text",
            "watermark_position",
            "watermark_opacity",
            "watermark_size_percent",
        }:
            state = self.document.state
            self._commit_command(
                EditCommand.create(
                    "watermark",
                    {
                        "text": state.watermark_text,
                        "position": state.watermark_position,
                        "opacity": state.watermark_opacity,
                        "size_percent": state.watermark_size_percent,
                    },
                    "调整文字水印",
                    merge_key="ui:watermark",
                ),
                merge=True,
                refresh=True,
            )

    def _conversion_output_directory_changed(self) -> None:
        if (
            self._syncing_document
            or self._restoring_conversion_session
            or self.mode_router.active_mode is not ModeId.CONVERT
        ):
            return
        self._invalidate_conversion_session()

    def _commit_resize_from_state(self) -> None:
        if self.document is None:
            return
        state = self.document.state

        def positive(text: str) -> int | None:
            try:
                value = int(text.strip())
            except ValueError:
                return None
            return value if value > 0 else None

        width = positive(state.width_text)
        height = positive(state.height_text)
        max_side = positive(state.max_side_text)
        if max_side is not None:
            params = {
                "exact_width": None,
                "exact_height": None,
                "max_width": max_side,
                "max_height": max_side,
            }
        elif width is not None and height is not None:
            params = {
                "exact_width": width,
                "exact_height": height,
                "max_width": None,
                "max_height": None,
            }
        else:
            params = {
                "exact_width": None,
                "exact_height": None,
                "max_width": width,
                "max_height": height,
            }
        self._commit_command(
            EditCommand.create("resize", params, "调整输出尺寸", merge_key="ui:resize"),
            merge=True,
            refresh=True,
        )

    def _set_document_edit_field(self, field: str, value: object) -> None:
        if self._syncing_document or self.document is None:
            return
        edit_state = replace(self.document.state.edit_state, **{field: value})
        self.document.update_state(f"edit_{field}", edit_state=edit_state)
        renderer_name = "sharpen" if field == "sharpness" else field
        labels = {
            "brightness": "亮度",
            "contrast": "对比度",
            "saturation": "饱和度",
            "sharpen": "锐化",
            "grayscale": "黑白",
            "auto_contrast": "自动优化",
        }
        merge_key = (
            f"{self._active_slider_merge_key}:{renderer_name}"
            if self._active_slider_merge_key
            else None
        )
        self._commit_command(
            EditCommand.create(
                "adjust",
                {"name": renderer_name, "value": value},
                f"调整{labels.get(renderer_name, renderer_name)}",
                merge_key=merge_key,
            ),
            merge=merge_key is not None,
            refresh=False,
        )

    def _commit_command(
        self, command: EditCommand, *, merge: bool = False, refresh: bool = True
    ) -> bool:
        if self._syncing_document or self.document is None:
            return False
        if self.document.project_read_only:
            self.status_var.set("项目包含当前版本不支持的操作，已按只读方式打开")
            return False
        try:
            apply_command_to_plan(reduce_timeline(self.document.timeline), command)
            self.document.commit_command(command, merge=merge)
        except ValueError as error:
            messagebox.showerror(APP_NAME, str(error))
            return False
        self._refresh_history_panel()
        if refresh:
            self._refresh_preview()
        return True

    def _advanced_color_changed(self, name: str, value: int) -> None:
        if self._syncing_document or self.document is None:
            return
        merge_key = (
            f"{self._active_slider_merge_key}:{name}" if self._active_slider_merge_key else None
        )
        self._commit_command(
            EditCommand.create(
                "adjust",
                {"name": name, "value": value},
                f"调整{name}",
                merge_key=merge_key,
            ),
            merge=merge_key is not None,
        )

    def _levels_changed(self) -> None:
        if self._syncing_document or self.document is None:
            return
        black = self.level_black_var.get()
        white = self.level_white_var.get()
        if black >= white:
            return
        merge_key = (
            f"{self._active_slider_merge_key}:levels" if self._active_slider_merge_key else None
        )
        self._commit_command(
            EditCommand.create(
                "levels",
                {"black": black, "white": white, "gamma": self.level_gamma_var.get()},
                "调整色阶",
                merge_key=merge_key,
            ),
            merge=merge_key is not None,
        )

    def _fine_rotation_changed(self) -> None:
        if self._syncing_document or self.document is None:
            return
        merge_key = (
            f"{self._active_slider_merge_key}:fine_rotate"
            if self._active_slider_merge_key
            else None
        )
        self._commit_command(
            EditCommand.create(
                "fine_rotate",
                {
                    "angle": round(self.fine_rotation_var.get(), 2),
                    "auto_crop": self.fine_rotation_auto_crop_var.get(),
                    "background": self.fine_rotation_background_var.get(),
                },
                "精细旋转",
                merge_key=merge_key,
            ),
            merge=merge_key is not None,
        )

    def auto_straighten(self) -> None:
        if self.document is None:
            return
        self.status_var.set("正在分析画面中的水平线和垂直线…")
        self.update_idletasks()
        source = render_image(self.document.image, self.document.timeline)
        try:
            result = detect_straighten_angle(source)
        finally:
            source.close()
        if not result.reliable:
            self.status_var.set("没有检测到可信的拉直角度")
            return
        current_angle = reduce_timeline(self.document.timeline).geometry.fine_angle
        self.fine_rotation_var.set(max(-45.0, min(45.0, current_angle + result.angle)))
        self._fine_rotation_changed()
        self.status_var.set(f"已自动拉直 {result.angle:+.2f}°（置信度 {result.confidence:.2f}）")

    def _curve_channel_changed(self) -> None:
        channel = self.curve_channel_var.get()
        self.curve_editor.set_points(self._curve_points_by_channel[channel])

    def _curve_changed(self, points: tuple[tuple[float, float], ...]) -> None:
        channel = self.curve_channel_var.get()
        self._curve_points_by_channel[channel] = points
        self._commit_command(
            EditCommand.create(
                "curve",
                {"channel": channel, "points": [[x, y] for x, y in points]},
                f"调整 {channel.upper()} 曲线",
            )
        )

    def _update_document_geometry(self, kind: str) -> None:
        if self._syncing_document or self.document is None:
            return
        edit_state = replace(
            self.document.state.edit_state,
            rotation=self.rotation,
            flip_horizontal=self.flip_h,
            flip_vertical=self.flip_v,
        )
        self.document.update_state(kind, edit_state=edit_state, crop_box=self.crop_box)

    def _apply_document_state(self, state: DocumentState) -> None:
        self._syncing_document = True
        try:
            self.rotation = state.edit_state.rotation
            self.flip_h = state.edit_state.flip_horizontal
            self.flip_v = state.edit_state.flip_vertical
            self.brightness_var.set(state.edit_state.brightness)
            self.contrast_var.set(state.edit_state.contrast)
            self.saturation_var.set(state.edit_state.saturation)
            self.sharpness_var.set(state.edit_state.sharpness)
            self.grayscale_var.set(state.edit_state.grayscale)
            self.auto_contrast_var.set(state.edit_state.auto_contrast)
            self.crop_box = state.crop_box
            self.crop_ratio_var.set(state.crop_ratio)
            self.width_var.set(state.width_text)
            self.height_var.set(state.height_text)
            self.max_side_var.set(state.max_side_text)
            self.format_var.set(state.output_format)
            self.quality_var.set(state.quality)
            self.target_size_var.set(state.target_size_text)
            self.filename_prefix_var.set(state.filename_prefix)
            self.filename_suffix_var.set(state.filename_suffix)
            self.watermark_text_var.set(state.watermark_text)
            self.watermark_position_var.set(state.watermark_position)
            self.watermark_opacity_var.set(state.watermark_opacity)
            self.watermark_size_var.set(state.watermark_size_percent)
            self.preserve_metadata_var.set(state.preserve_metadata)
            self.backup_on_overwrite_var.set(state.backup_on_overwrite)
            self.export_preset_var.set(state.export_preset)
        finally:
            self._syncing_document = False

    def _sync_controls_from_timeline(self) -> None:
        if self.document is None:
            return
        was_dirty = self.document.dirty
        plan = reduce_timeline(self.document.timeline)
        self._syncing_document = True
        try:
            self.rotation = plan.geometry.rotation
            self.flip_h = plan.geometry.flip_horizontal
            self.flip_v = plan.geometry.flip_vertical
            self.fine_rotation_var.set(plan.geometry.fine_angle)
            self.fine_rotation_auto_crop_var.set(plan.geometry.auto_crop)
            self.fine_rotation_background_var.set(plan.geometry.background)
            if plan.geometry.perspective is not None:
                self.perspective_points = [
                    (
                        plan.geometry.perspective[index],
                        plan.geometry.perspective[index + 1],
                    )
                    for index in range(0, 8, 2)
                ]
            else:
                self.perspective_points = [
                    (0.05, 0.05),
                    (0.95, 0.05),
                    (0.95, 0.95),
                    (0.05, 0.95),
                ]
            self.canvas_left_var.set(plan.geometry.canvas_margins[0])
            self.canvas_top_var.set(plan.geometry.canvas_margins[1])
            self.canvas_right_var.set(plan.geometry.canvas_margins[2])
            self.canvas_bottom_var.set(plan.geometry.canvas_margins[3])
            self.canvas_background_var.set(plan.geometry.canvas_background)
            self.brightness_var.set(plan.color.brightness)
            self.contrast_var.set(plan.color.contrast)
            self.saturation_var.set(plan.color.saturation)
            self.sharpness_var.set(plan.color.sharpen)
            self.grayscale_var.set(plan.color.grayscale)
            self.auto_contrast_var.set(plan.color.auto_contrast)
            self.temperature_var.set(plan.color.temperature)
            self.tint_var.set(plan.color.tint)
            self.shadows_var.set(plan.color.shadows)
            self.highlights_var.set(plan.color.highlights)
            self.denoise_var.set(plan.color.denoise)
            self.level_black_var.set(plan.color.level_black)
            self.level_white_var.set(plan.color.level_white)
            self.level_gamma_var.set(plan.color.level_gamma)
            self._curve_points_by_channel = dict(plan.color.curves)
            if hasattr(self, "curve_editor"):
                self.curve_editor.set_points(
                    self._curve_points_by_channel[self.curve_channel_var.get()]
                )
            self.watermark_text_var.set(plan.annotation.watermark_text)
            self.watermark_position_var.set(plan.annotation.watermark_position)
            self.watermark_opacity_var.set(plan.annotation.watermark_opacity)
            self.watermark_size_var.set(plan.annotation.watermark_size_percent)
        finally:
            self._syncing_document = False
        width_text = ""
        height_text = ""
        max_side_text = ""
        if plan.size.exact_width is not None and plan.size.exact_height is not None:
            width_text = str(plan.size.exact_width)
            height_text = str(plan.size.exact_height)
        elif plan.size.max_width is not None and plan.size.max_width == plan.size.max_height:
            max_side_text = str(plan.size.max_width)
        else:
            width_text = str(plan.size.max_width or "")
            height_text = str(plan.size.max_height or "")
        synchronized = replace(
            self.document.state,
            edit_state=EditState(
                rotation=plan.geometry.rotation,
                flip_horizontal=plan.geometry.flip_horizontal,
                flip_vertical=plan.geometry.flip_vertical,
                brightness=plan.color.brightness,
                contrast=plan.color.contrast,
                saturation=plan.color.saturation,
                sharpness=plan.color.sharpen,
                grayscale=plan.color.grayscale,
                auto_contrast=plan.color.auto_contrast,
            ),
            crop_box=None,
            width_text=width_text,
            height_text=height_text,
            max_side_text=max_side_text,
            watermark_text=plan.annotation.watermark_text,
            watermark_position=plan.annotation.watermark_position,
            watermark_opacity=plan.annotation.watermark_opacity,
            watermark_size_percent=plan.annotation.watermark_size_percent,
        )
        self._syncing_document = True
        try:
            self.width_var.set(width_text)
            self.height_var.set(height_text)
            self.max_side_var.set(max_side_text)
        finally:
            self._syncing_document = False
        self.document.set_state("sync_timeline", synchronized)
        self.crop_box = None
        if self.selected_annotation_id and not any(
            annotation.id == self.selected_annotation_id for annotation in plan.annotations
        ):
            self.selected_annotation_id = None
            self.selected_annotation_var.set("未选择标注")
        elif self.selected_annotation_id:
            selected = next(
                annotation
                for annotation in plan.annotations
                if annotation.id == self.selected_annotation_id
            )
            self._load_annotation_style(selected)
        if not was_dirty:
            self.document.mark_clean()

    def _refresh_history_panel(self) -> None:
        if not hasattr(self, "history_tree"):
            return
        tree = self.history_tree
        selected = self.document.timeline.cursor if self.document is not None else 0
        tree.delete(*tree.get_children())
        tree.insert("", "end", iid="history:0", text="0  原图")
        if self.document is not None:
            for index, command in enumerate(self.document.timeline.commands, start=1):
                marker = "" if index <= self.document.timeline.cursor else "（已撤销）"
                tree.insert(
                    "",
                    "end",
                    iid=f"history:{index}",
                    text=f"{index}  {command.label}{marker}",
                )
        iid = f"history:{selected}"
        if tree.exists(iid):
            tree.selection_set(iid)
            tree.see(iid)
        self._sync_mode_handoff()

    def _history_selected(self, _event: tk.Event) -> None:
        if self.document is None:
            return
        selection = self.history_tree.selection()
        if not selection:
            return
        try:
            cursor = int(selection[0].split(":", 1)[1])
        except (IndexError, ValueError):
            return
        if cursor == self.document.timeline.cursor:
            return
        self.document.jump_history(cursor)
        self._sync_controls_from_timeline()
        self._refresh_history_panel()
        self._refresh_preview()
        self.status_var.set(f"已跳转到历史步骤 {cursor}")

    def _draw_histogram(self, image: Image.Image) -> None:
        if not hasattr(self, "histogram_canvas"):
            return
        canvas = self.histogram_canvas
        if not canvas.winfo_ismapped():
            return
        values = histogram(image, max_pixels=262_144)
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        for channel, color in (("red", "#ef4444"), ("green", "#22c55e"), ("blue", "#3b82f6")):
            bins = values[channel]
            peak = max(1, max(bins))
            points: list[float] = []
            for index, count in enumerate(bins):
                points.extend((index * (width - 1) / 255, height - count * (height - 2) / peak))
            canvas.create_line(*points, fill=color, width=1)

    def _new_document_state(self, image: Image.Image) -> DocumentState:
        return DocumentState(
            width_text="",
            height_text="",
            output_format=self.format_var.get(),
            quality=self.quality_var.get(),
            target_size_text=self.target_size_var.get(),
            filename_prefix=self.filename_prefix_var.get(),
            filename_suffix=self.filename_suffix_var.get(),
            watermark_text=self.watermark_text_var.get(),
            watermark_position=self.watermark_position_var.get(),
            watermark_opacity=self.watermark_opacity_var.get(),
            watermark_size_percent=self.watermark_size_var.get(),
            preserve_metadata=self.preserve_metadata_var.get(),
            backup_on_overwrite=self.backup_on_overwrite_var.get(),
            export_preset=self.export_preset_var.get(),
        )

    def _load_config(self) -> None:
        data = self.settings_store.load_migrated()
        theme_mode = str(data.get("theme_mode", "system"))
        self.theme_mode_var.set(
            theme_mode if theme_mode in {"system", "light", "dark"} else "system"
        )
        self.decode_workers_var.set(_bounded_setting_int(data.get("decode_workers", 2), 2, 1, 8))
        self.preview_cache_var.set(_bounded_setting_int(data.get("preview_cache", 3), 3, 1, 20))
        self.onboarding_completed = bool(data.get("onboarding_completed", False))
        channel = str(data.get("update_channel", "stable"))
        self.update_channel_var.set(channel if channel in {"stable", "beta"} else "stable")
        self.crash_reporting_consent_var.set(bool(data.get("crash_reporting_consent", False)))
        self.format_var.set(data.get("format", "JPEG"))
        self.quality_var.set(_bounded_setting_int(data.get("quality", 85), 85, 1, 100))
        self.target_size_var.set(str(data.get("target_size_kb", "") or ""))
        self.filename_prefix_var.set(str(data.get("filename_prefix", "") or ""))
        self.filename_suffix_var.set(str(data.get("filename_suffix", "") or ""))
        self.watermark_text_var.set(str(data.get("watermark_text", "") or ""))
        self.watermark_position_var.set(str(data.get("watermark_position", "右下") or "右下"))
        self.watermark_opacity_var.set(
            _bounded_setting_int(data.get("watermark_opacity", 35), 35, 0, 100)
        )
        self.watermark_size_var.set(
            _bounded_setting_int(data.get("watermark_size_percent", 5), 5, 1, 30)
        )
        self.reveal_after_save_var.set(bool(data.get("reveal_after_save", False)))
        self.recent_files = _string_list_setting(data.get("recent_files", []))
        self.recent_directories = _string_list_setting(data.get("recent_directories", []))
        self.recent_projects = _string_list_setting(data.get("recent_projects", []))
        self.recent_export_directories = _string_list_setting(
            data.get("recent_export_directories", [])
        )
        self.generation_prompt_history = list(
            dict.fromkeys(
                prompt.strip()
                for prompt in _string_list_setting(data.get("generation_prompt_history", []))
                if prompt.strip()
            )
        )[:20]
        self.generation_candidate_revision_ids = list(
            dict.fromkeys(
                revision_id.strip()
                for revision_id in _string_list_setting(
                    data.get("generation_candidate_revision_ids", [])
                )
                if revision_id.strip()
            )
        )[:12]
        self.generation_favorite_revision_ids = list(
            dict.fromkeys(
                revision_id.strip()
                for revision_id in _string_list_setting(
                    data.get("generation_favorite_revision_ids", [])
                )
                if revision_id.strip()
            )
        )[:128]
        raw_favorites_only = data.get("generation_favorites_only", False)
        self.generate_favorites_only_var.set(
            raw_favorites_only if isinstance(raw_favorites_only, bool) else False
        )
        raw_task_tray_items = data.get("task_tray_items", [])
        self.task_tray_settings = (
            [dict(item) for item in raw_task_tray_items if isinstance(item, dict)][:50]
            if isinstance(raw_task_tray_items, list)
            else []
        )
        self.generation_reference_revision_id = str(
            data.get("generation_reference_revision_id", "") or ""
        ).strip()
        self.generate_negative_prompt_var.set(
            str(data.get("generation_negative_prompt", "") or "")[:10_000]
        )
        provider_display_name = str(
            data.get(
                "generation_provider_display_name",
                self.generation_provider_catalog.default().display_name,
            )
        )
        try:
            self.generation_provider_catalog.by_display_name(provider_display_name)
        except KeyError:
            provider_display_name = self.generation_provider_catalog.default().display_name
        self.generate_provider_var.set(provider_display_name)
        reference_role = str(data.get("generation_reference_role", "内容"))
        self.generate_reference_role_var.set(
            reference_role if reference_role in GENERATION_REFERENCE_ROLES else "内容"
        )
        self.generate_reference_strength_var.set(
            _bounded_setting_int(
                data.get("generation_reference_strength", 50),
                50,
                0,
                100,
            )
        )
        raw_generation_references = data.get("generation_references", [])
        generation_reference_settings: list[tuple[str, str, float]] = []
        seen_reference_revision_ids: set[str] = set()
        if isinstance(raw_generation_references, list):
            for item in raw_generation_references:
                if not isinstance(item, dict):
                    continue
                revision_id = str(item.get("revision_id", "") or "").strip()
                role = str(item.get("role", "content") or "content")
                raw_strength = item.get("strength", 0.5)
                if (
                    not revision_id
                    or revision_id in seen_reference_revision_ids
                    or role not in GENERATION_REFERENCE_ROLE_LABELS
                    or isinstance(raw_strength, bool)
                    or not isinstance(raw_strength, (int, float))
                ):
                    continue
                strength = float(raw_strength)
                if strength != strength or not 0 <= strength <= 1:
                    continue
                generation_reference_settings.append((revision_id, role, strength))
                seen_reference_revision_ids.add(revision_id)
                if len(generation_reference_settings) == 4:
                    break
        if not generation_reference_settings and self.generation_reference_revision_id:
            generation_reference_settings.append(
                (
                    self.generation_reference_revision_id,
                    GENERATION_REFERENCE_ROLES.get(
                        self.generate_reference_role_var.get(),
                        "content",
                    ),
                    self.generate_reference_strength_var.get() / 100,
                )
            )
        self.generation_reference_settings = generation_reference_settings
        style_preset = str(data.get("generation_style_preset", "无"))
        self.generate_style_preset_var.set(
            style_preset if style_preset in GENERATION_STYLE_PRESETS else "无"
        )
        self.generate_seed_enabled_var.set(bool(data.get("generation_seed_enabled", False)))
        self.generate_seed_var.set(
            str(
                _bounded_setting_int(
                    data.get("generation_seed", 0),
                    0,
                    0,
                    4_294_967_295,
                )
            )
        )
        known_actions = {key for key, _label, _shortcut in ACTION_CATALOG}
        favorites = data.get("favorite_actions", self.favorite_actions)
        if isinstance(favorites, list):
            self.favorite_actions = [str(action) for action in favorites if action in known_actions]
        shortcuts = data.get("custom_shortcuts", {})
        if isinstance(shortcuts, dict):
            self.custom_shortcuts.update(
                {
                    str(action): str(sequence)
                    for action, sequence in shortcuts.items()
                    if action in known_actions and isinstance(sequence, str)
                }
            )
        viewer_shortcuts = data.get("viewer_shortcuts", {})
        if isinstance(viewer_shortcuts, dict):
            for action in VIEWER_SHORTCUT_DEFAULTS:
                sequences = viewer_shortcuts.get(action)
                if isinstance(sequences, list):
                    self.viewer_shortcuts[action] = [
                        str(sequence)
                        for sequence in sequences
                        if isinstance(sequence, str) and sequence
                    ][:6]
        self.viewer_zoom_step_percent = _bounded_setting_int(
            data.get("viewer_zoom_step_percent", 10),
            10,
            1,
            100,
        )
        saved_zoom_presets = data.get("viewer_zoom_presets", [])
        if isinstance(saved_zoom_presets, list) and saved_zoom_presets:
            try:
                self.viewer_zoom_presets = self._parse_viewer_zoom_presets(
                    ",".join(str(value) for value in saved_zoom_presets)
                )
            except ValueError:
                self.viewer_zoom_presets = list(DEFAULT_VIEWER_ZOOM_PRESETS)
        self.viewer_toolbar_visible_var.set(bool(data.get("viewer_toolbar_visible", True)))
        toolbar_mode = str(data.get("viewer_toolbar_mode", "fixed"))
        self.viewer_toolbar_mode_var.set(
            toolbar_mode if toolbar_mode in VIEWER_TOOLBAR_MODE_LABELS else "fixed"
        )
        self.viewer_topmost_var.set(bool(data.get("viewer_topmost", False)))
        saved_viewer_favorites = data.get("viewer_favorite_paths", [])
        if isinstance(saved_viewer_favorites, list):
            self.viewer_favorite_paths = list(
                dict.fromkeys(
                    os.path.abspath(str(path))
                    for path in saved_viewer_favorites
                    if isinstance(path, str) and path.strip()
                )
            )[:2000]
        self.restore_workspace = bool(data.get("restore_workspace", True))
        self.restore_workspace_var.set(self.restore_workspace)
        self.last_project = str(data.get("last_project", "") or "")
        saved_tool = str(data.get("annotation_tool", "crop"))
        if saved_tool in ANNOTATION_TOOL_LABELS:
            self.annotation_tool_var.set(saved_tool)
            self.current_tool_label_var.set(f"当前：{ANNOTATION_TOOL_LABELS[saved_tool]}")
        self.edit_panel_visible_var.set(bool(data.get("edit_panel_visible", True)))
        self.thumbnail_sidebar_visible_var.set(bool(data.get("viewer_sidebar_visible_v2", False)))
        self.slideshow_interval_var.set(
            _bounded_setting_float(data.get("slideshow_interval", 3.0), 3.0, 0.5, 3600.0)
        )
        self.slideshow_loop_var.set(bool(data.get("slideshow_loop", True)))
        self.preserve_metadata_var.set(bool(data.get("preserve_metadata", True)))
        self.backup_on_overwrite_var.set(bool(data.get("backup_on_overwrite", True)))
        self.batch_concurrency_var.set(
            _bounded_setting_int(data.get("batch_concurrency", 2), 2, 1, 16)
        )
        self.batch_memory_var.set(
            _bounded_setting_int(data.get("batch_memory_mb", 512), 512, 32, 262_144)
        )
        self.batch_rename_template_var.set(
            str(data.get("batch_rename_template", "{stem}_{index:04d}"))
        )
        self.batch_conflict_var.set(str(data.get("batch_conflict_policy", "unique")))
        self.batch_strip_metadata_var.set(bool(data.get("batch_strip_metadata", False)))
        self.batch_preserve_animation_var.set(bool(data.get("batch_preserve_animation", True)))
        preset = str(data.get("export_preset", "自定义"))
        self.export_preset_var.set(preset if preset in EXPORT_PRESETS else "自定义")
        geometry = data.get("window_geometry", "")
        self._saved_geometry = str(geometry) if isinstance(geometry, str) else ""

    def _schedule_config_save(self, delay_ms: int = 400) -> None:
        """Coalesce high-frequency settings changes onto one idle disk write."""

        if self._services_shutdown:
            return
        self._cancel_scheduled_config_save()
        self._config_save_job = self.after(delay_ms, self._flush_scheduled_config_save)

    def _flush_scheduled_config_save(self) -> None:
        self._config_save_job = None
        self._save_config()

    def _cancel_scheduled_config_save(self) -> None:
        job = self._config_save_job
        self._config_save_job = None
        if job is None:
            return
        try:
            self.after_cancel(job)
        except tk.TclError:
            pass

    def _save_config(self) -> None:
        self._cancel_scheduled_config_save()
        data = self.settings_store.load_migrated()
        data.pop("locale", None)
        data.update(
            {
                "schema_version": SETTINGS_SCHEMA_VERSION,
                "theme_mode": self.theme_mode_var.get(),
                "decode_workers": self.decode_workers_var.get(),
                "preview_cache": self.preview_cache_var.get(),
                "onboarding_completed": self.onboarding_completed,
                "update_channel": self.update_channel_var.get(),
                "crash_reporting_consent": self.crash_reporting_consent_var.get(),
                "format": self.format_var.get(),
                "quality": self.quality_var.get(),
                "target_size_kb": self.target_size_var.get().strip(),
                "filename_prefix": self.filename_prefix_var.get().strip(),
                "filename_suffix": self.filename_suffix_var.get().strip(),
                "watermark_text": self.watermark_text_var.get().strip(),
                "watermark_position": self.watermark_position_var.get(),
                "watermark_opacity": self.watermark_opacity_var.get(),
                "watermark_size_percent": self.watermark_size_var.get(),
                "reveal_after_save": self.reveal_after_save_var.get(),
                "recent_files": self.recent_files[:8],
                "recent_directories": self.recent_directories[:8],
                "recent_projects": self.recent_projects[:8],
                "recent_export_directories": self.recent_export_directories[:8],
                "generation_prompt_history": self.generation_prompt_history[:20],
                "generation_candidate_revision_ids": self.generation_candidate_revision_ids[:12],
                "generation_favorite_revision_ids": self.generation_favorite_revision_ids[:128],
                "generation_favorites_only": self.generate_favorites_only_var.get(),
                "task_tray_items": (
                    [item.to_json() for item in self.task_tray.items]
                    if hasattr(self, "task_tray")
                    else list(self.task_tray_settings)
                ),
                "generation_reference_revision_id": self.generation_reference_revision_id,
                "generation_references": [
                    {
                        "revision_id": reference.revision.revision_id,
                        "role": reference.role,
                        "strength": reference.strength,
                    }
                    for reference in self._generation_references
                ],
                "generation_negative_prompt": self.generate_negative_prompt_var.get()[:10_000],
                "generation_provider_display_name": self.generate_provider_var.get(),
                "generation_reference_role": self.generate_reference_role_var.get(),
                "generation_reference_strength": self.generate_reference_strength_var.get(),
                "generation_style_preset": self.generate_style_preset_var.get(),
                "generation_seed_enabled": self.generate_seed_enabled_var.get(),
                "generation_seed": self.generate_seed_var.get().strip(),
                "favorite_actions": self.favorite_actions,
                "custom_shortcuts": self.custom_shortcuts,
                "viewer_shortcuts": self.viewer_shortcuts,
                "viewer_zoom_step_percent": self.viewer_zoom_step_percent,
                "viewer_zoom_presets": self.viewer_zoom_presets,
                "viewer_toolbar_visible": self.viewer_toolbar_visible_var.get(),
                "viewer_toolbar_mode": self.viewer_toolbar_mode_var.get(),
                "viewer_topmost": self.viewer_topmost_var.get(),
                "viewer_favorite_paths": self.viewer_favorite_paths[:2000],
                "restore_workspace": self.restore_workspace_var.get(),
                "last_project": self.last_project,
                "annotation_tool": self.annotation_tool_var.get(),
                "edit_panel_visible": self.edit_panel_visible_var.get(),
                "thumbnail_sidebar_visible": self.thumbnail_sidebar_visible_var.get(),
                "viewer_sidebar_visible_v2": self.thumbnail_sidebar_visible_var.get(),
                "slideshow_interval": self.slideshow_interval_var.get(),
                "slideshow_loop": self.slideshow_loop_var.get(),
                "preserve_metadata": self.preserve_metadata_var.get(),
                "backup_on_overwrite": self.backup_on_overwrite_var.get(),
                "batch_concurrency": self.batch_concurrency_var.get(),
                "batch_memory_mb": self.batch_memory_var.get(),
                "batch_rename_template": self.batch_rename_template_var.get(),
                "batch_conflict_policy": self.batch_conflict_var.get(),
                "batch_strip_metadata": self.batch_strip_metadata_var.get(),
                "batch_preserve_animation": self.batch_preserve_animation_var.get(),
                "export_preset": self.export_preset_var.get(),
                "window_geometry": self._saved_geometry or self.geometry(),
            }
        )
        self.settings_store.save(data)

    def _on_configure(self, event: tk.Event) -> None:
        # Child widgets also carry the toplevel bind tag.  Re-rendering the image
        # for every inspector/thumbnail configure event creates a preview queue
        # behind the user's next navigation click.
        canvas = getattr(self, "canvas", None)
        if event.widget is not self and event.widget is not canvas:
            return
        if (
            event.widget is self
            and self.window_controller is not None
            and self.window_controller.moving
        ):
            return
        actively_resizing = bool(
            self.window_controller is not None and self.window_controller.resizing
        )
        if event.widget is self and actively_resizing:
            return
        custom_maximized = bool(self.window_controller and self.window_controller.maximized)
        if (
            event.widget is self
            and not self._is_fullscreen
            and not self._is_immersive
            and not custom_maximized
        ):
            self._saved_geometry = self.geometry()
            self._schedule_window_resize_finish()
        if event.widget is not canvas:
            return
        current_size = (max(1, int(event.width)), max(1, int(event.height)))
        if current_size == self._last_canvas_configure_size:
            return
        self._last_canvas_configure_size = current_size
        if self.mode_router.active_mode is ModeId.VIEWER:
            if not self._viewer_resize_preview_pending:
                self._preview_generation += 1
            self._viewer_resize_preview_pending = True
            if self._preview_job is not None:
                try:
                    self.after_cancel(self._preview_job)
                except tk.TclError:
                    pass
                self._preview_job = None
            self.canvas.delete("viewer-navigation")
            self.canvas.delete("viewer-navigation-tooltip")
            if self.preview is not None:
                self._move_cached_viewer_preview(redraw_navigation=False)
            elif self.original is None:
                self.after_idle(self._show_empty_state)
            if not actively_resizing:
                self._schedule_window_resize_finish()
            return
        self._update_viewer_navigation_buttons()
        if self.original is None:
            self.after_idle(self._show_empty_state)
        else:
            self._schedule_preview()

    def _on_window_move_finished(self, _event: tk.Event | None = None) -> None:
        if self._is_fullscreen or self._is_immersive:
            return
        self._saved_geometry = self.geometry()
        self.rounded_window_effect.invalidate()
        # Refresh monitor/DPI constraints once after the move, not for every
        # intermediate pointer position.
        self._schedule_window_resize_finish()

    def _on_window_interaction_started(self, _event: tk.Event | None = None) -> None:
        """Freeze toolbar compositing during native move/resize gestures."""

        if self._viewer_toolbar_fade_job is not None:
            try:
                self.after_cancel(self._viewer_toolbar_fade_job)
            except tk.TclError:
                pass
            self._viewer_toolbar_fade_job = None
        self._viewer_toolbar_fade_target = None
        if self._viewer_toolbar_auto_hide_job is not None:
            try:
                self.after_cancel(self._viewer_toolbar_auto_hide_job)
            except tk.TclError:
                pass
            self._viewer_toolbar_auto_hide_job = None
        if (
            self.mode_router.active_mode is ModeId.VIEWER
            and self.viewer_toolbar_mode_var.get() == "auto"
        ):
            opacity = 1.0 if self._viewer_toolbar_auto_visible else 0.0
            self._viewer_toolbar_opacity = opacity
            self._set_viewer_toolbar_visual_opacity(opacity)
            if not self._viewer_toolbar_auto_visible:
                self._update_command_dock_for_mode(self.mode_router.active_mode)

    def _on_window_resize_finished(self, _event: tk.Event | None = None) -> None:
        if self._is_fullscreen or self._is_immersive:
            return
        self._saved_geometry = self.geometry()
        self.rounded_window_effect.invalidate()
        if self.window_controller is not None:
            self.window_controller._schedule_raise_resize_handles()
        self._schedule_window_resize_finish()

    def _schedule_window_resize_finish(self) -> None:
        if self._window_resize_idle_job is not None:
            try:
                self.after_cancel(self._window_resize_idle_job)
            except tk.TclError:
                pass
        self._window_resize_idle_job = self.after(140, self._finish_window_resize)

    def _finish_window_resize(self) -> None:
        self._window_resize_idle_job = None
        if self._services_shutdown:
            return
        if self.dpi_manager.refresh():
            self._update_monitor_constraints()
            self.dpi_manager.constrain_to_current_work_area()
            tokens = self.theme_manager.apply(self.theme_mode_var.get())
            self._apply_thumbnail_palette(tokens)
        if self._viewer_resize_preview_pending:
            self._viewer_resize_preview_pending = False
            if self.original is None:
                self._show_empty_state()
                self._update_viewer_navigation_buttons()
            else:
                self._refresh_preview()

    def open_file(self) -> None:
        if not self._confirm_discard_changes():
            return
        paths = filedialog.askopenfilenames(
            title="选择一张或多张图片",
            filetypes=[
                (
                    "图片文件",
                    "*.jpg *.jpeg *.png *.apng *.webp *.avif *.heic *.heif "
                    "*.bmp *.tif *.tiff *.gif",
                ),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return
        self.open_paths(paths, confirm=False)

    def open_directory(self) -> None:
        if not self._confirm_discard_changes():
            return
        selected = filedialog.askdirectory(
            title="选择图片文件夹",
            initialdir=(
                str(self.image_path.parent)
                if self.image_path is not None
                else self.recent_directories[0]
                if self.recent_directories
                else None
            ),
        )
        if selected:
            self.open_directory_path(selected, confirm=False)

    def open_directory_path(
        self,
        path: str | Path,
        *,
        confirm: bool = True,
    ) -> bool:
        directory = Path(path)
        if confirm and not self._confirm_discard_changes():
            return False
        files = iter_image_files([directory]) if directory.is_dir() else []
        if not files:
            self.status_var.set("所选文件夹中没有可打开的图片")
            messagebox.showwarning(APP_NAME, "所选文件夹中没有可打开的受支持图片。")
            return False
        self._disable_file_comparison(
            announce=False,
            refresh=False,
            resume_animation=False,
        )
        self.stop_slideshow()
        self.navigation = NavigationSession(
            [file.resolve() for file in files],
            source_label=str(directory.resolve()),
        )
        self.thumbnail_sidebar.set_paths(self.navigation.files, self.navigation.current)
        self._failed_navigation_paths.clear()
        assert self.navigation.current is not None
        self._begin_image_load(self.navigation.current, notify_error=True)
        return True

    def open_project_dialog(self) -> None:
        path = filedialog.askopenfilename(
            title="打开编辑项目",
            filetypes=[("轻量图片编辑器项目", "*.lieproj"), ("所有文件", "*.*")],
        )
        if path:
            self.open_project_path(Path(path))

    def open_project_path(self, path: str | Path, *, confirm: bool = True) -> None:
        if confirm and not self._confirm_discard_changes():
            return
        self._disable_file_comparison(
            announce=False,
            refresh=False,
            resume_animation=False,
        )
        project_path = Path(path)
        try:
            loaded = load_project(project_path)
        except ProjectSourceMissing:
            source = filedialog.askopenfilename(
                title="重新定位项目引用的原图",
                initialdir=str(project_path.parent),
                filetypes=[
                    (
                        "图片文件",
                        "*.jpg *.jpeg *.png *.apng *.webp *.avif *.heic *.heif "
                        "*.bmp *.tif *.tiff *.gif",
                    ),
                    ("所有文件", "*.*"),
                ],
            )
            if not source:
                return
            try:
                loaded = load_project(project_path, source_override=source)
            except ProjectSourceModified:
                loaded = load_project(
                    project_path, source_override=source, allow_modified_source=True
                )
            except ProjectError as error:
                messagebox.showerror(APP_NAME, str(error))
                return
        except ProjectSourceModified:
            if not messagebox.askyesno(
                APP_NAME,
                "项目引用的原图已发生变化。继续打开可能导致渲染结果变化，是否只读确认后打开？",
            ):
                return
            try:
                loaded = load_project(project_path, allow_modified_source=True)
            except ProjectError as retry_error:
                messagebox.showerror(APP_NAME, str(retry_error))
                return
        except ProjectError as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        self.stop_slideshow()
        self.document = loaded.document
        self._reset_smart_edit_for_new_document()
        self.original = loaded.document.image
        self.image_path = loaded.document.source_path
        self.navigation = NavigationSession.from_activation([self.image_path])
        self.thumbnail_sidebar.set_paths(self.navigation.files, self.navigation.current)
        self._load_current_image(project_path.name, recovered=True)
        self._remember_project(project_path)
        if loaded.unsupported_commands:
            self.status_var.set(
                "项目含不支持的命令，已只读打开：" + ", ".join(loaded.unsupported_commands)
            )
        elif loaded.migrated_from:
            self.status_var.set("旧项目已迁移到当前格式；保存时会写入新格式")
        else:
            self.status_var.set(f"已打开编辑项目：{project_path.name}")

    def save_project_dialog(self) -> None:
        if self.document is None:
            messagebox.showinfo(APP_NAME, "请先打开或粘贴一张图片。")
            return
        if self.document.project_read_only:
            messagebox.showwarning(APP_NAME, "该项目包含当前版本不支持的命令，不能覆盖保存。")
            return
        if self.document.source_path is None:
            base_path = filedialog.asksaveasfilename(
                title="先保存项目原图（无损 PNG）",
                defaultextension=".png",
                initialfile="project-source.png",
                filetypes=[("PNG", "*.png")],
            )
            if not base_path:
                return
            base_target = Path(base_path)
            if base_target.exists() and not messagebox.askyesno(
                APP_NAME, f"原图文件已存在：\n{base_target}\n\n是否覆盖？"
            ):
                return
            try:
                self.document.image.save(base_target, "PNG")
                self.document.mark_clean(base_target)
                self.document.timeline.mark_dirty()
                self.image_path = base_target
            except OSError as error:
                messagebox.showerror(APP_NAME, f"保存项目原图失败：{error}")
                return
        initial = (
            self.document.project_path.name
            if self.document.project_path is not None
            else f"{self.document.source_path.stem}.lieproj"
        )
        target = filedialog.asksaveasfilename(
            title="保存编辑项目",
            defaultextension=".lieproj",
            initialfile=initial,
            filetypes=[("轻量图片编辑器项目", "*.lieproj")],
        )
        if not target:
            return
        try:
            saved = save_project(self.document, target)
        except ProjectError as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        self.session_store.clear()
        self._remember_project(saved)
        self.status_var.set(f"编辑项目已保存：{saved}")

    def open_path(self, path: str | Path, confirm: bool = True) -> None:
        self.open_paths([path], confirm=confirm)

    def open_paths(self, paths: Sequence[str | Path], confirm: bool = True) -> None:
        normalized = [Path(path) for path in paths]
        if len(normalized) == 1 and normalized[0].suffix.casefold() == ".lieproj":
            self.open_project_path(normalized[0], confirm=confirm)
            return
        if confirm and not self._confirm_discard_changes():
            return
        self._disable_file_comparison(
            announce=False,
            refresh=False,
            resume_animation=False,
        )
        session = NavigationSession.from_activation(paths)
        if session.current is None:
            self.status_var.set("没有找到可打开的受支持图片")
            messagebox.showwarning(
                APP_NAME, "所选路径中没有可打开的 JPG、PNG、WEBP、BMP、TIFF 或 GIF 图片。"
            )
            return
        self.stop_slideshow()
        self.navigation = session
        self.thumbnail_sidebar.set_paths(session.files, session.current)
        self._failed_navigation_paths.clear()
        self._begin_image_load(session.current, notify_error=True)

    def handle_activation(self, paths: Sequence[str | Path]) -> None:
        self.deiconify()
        self.lift()
        try:
            self.attributes("-topmost", True)
            self.after(
                80,
                lambda: self.attributes("-topmost", self.viewer_topmost_var.get()),
            )
            self.focus_force()
        except tk.TclError:
            pass
        if paths:
            self.open_paths(paths)

    def _begin_image_load(
        self,
        path: Path,
        *,
        notify_error: bool = False,
        skip_direction: int = 0,
    ) -> None:
        self._stop_animation_playback()
        self._cancel_pending_preview_updates()
        self.thumbnail_sidebar.select(path)
        self._load_generation += 1
        generation = self._load_generation
        self.status_var.set(f"正在后台解码：{path.name}")
        future = self.image_loader.load(path, take_cached=True)
        self.after(
            10,
            lambda: self._poll_image_load(
                generation,
                path,
                future,
                notify_error=notify_error,
                skip_direction=skip_direction,
            ),
        )

    def _poll_image_load(
        self,
        generation: int,
        path: Path,
        future: Future[Image.Image],
        *,
        notify_error: bool,
        skip_direction: int,
    ) -> None:
        if generation != self._load_generation:
            return
        if not future.done():
            self.after(
                10,
                lambda: self._poll_image_load(
                    generation,
                    path,
                    future,
                    notify_error=notify_error,
                    skip_direction=skip_direction,
                ),
            )
            return
        try:
            image = future.result()
        except ImageLoadFailure as failure:
            self._handle_load_failure(failure, notify_error, skip_direction)
            return
        except Exception as error:
            failure = ImageLoadFailure(path, "decode", f"无法打开图片：{error}")
            self._handle_load_failure(failure, notify_error, skip_direction)
            return

        self.document = Document.from_decoded(image, path, self._new_document_state(image))
        self._reset_smart_edit_for_new_document()
        self._last_failed_path = None
        self._last_source_status = "unchanged"
        self.original = self.document.image
        self.image_path = self.document.source_path
        exited_duplicate_comparison = bool(
            self.file_compare_var.get()
            and self._comparison_path is not None
            and self._same_path(path, self._comparison_path)
        )
        if exited_duplicate_comparison:
            self._disable_file_comparison(
                announce=False,
                refresh=False,
                resume_animation=False,
            )
        self._load_current_image(path.name)
        self._remember_file(path)
        suffix = " · 已退出重复的双图对比" if exited_duplicate_comparison else ""
        self.status_var.set(f"图片已载入 · {path.name}{suffix}")
        self.image_loader.prefetch(self.navigation.neighbors())
        if self.document.metadata.is_animated:
            self._begin_animation_playback(path)

    def _handle_load_failure(
        self,
        failure: ImageLoadFailure,
        notify_error: bool,
        skip_direction: int,
    ) -> None:
        key = str(failure.path).casefold()
        self._failed_navigation_paths.add(key)
        self._last_failed_path = failure.path
        self._clear_document_after_load_failure(failure.path)
        self.status_var.set(f"{failure.path.name}：{failure.user_message}")
        self.canvas.delete("all")
        tokens = self.theme_manager.tokens
        center_x = max(240, self.canvas.winfo_width() // 2)
        center_y = max(150, self.canvas.winfo_height() // 2)
        self.canvas.create_text(
            center_x,
            center_y - 44,
            text=self.localizer.translate_text(
                f"无法显示 {failure.path.name}\n{failure.user_message}\n可继续切换其他图片。"
            ),
            fill=tokens.foreground,
            justify="center",
            font=("Microsoft YaHei UI", 12),
        )
        actions = (
            (
                self.localizer.gettext("error_retry"),
                lambda: self._begin_image_load(failure.path, notify_error=True),
            ),
            (self.localizer.gettext("error_choose"), self.open_file),
            (
                self.localizer.gettext("error_reveal"),
                lambda: self._open_folder(failure.path.parent),
            ),
        )
        for index, (label, callback) in enumerate(actions):
            x = center_x + (index - 1) * 150
            text_item = self.canvas.create_text(
                x,
                center_y + 42,
                text=f"  {label}  ",
                fill="#ffffff",
                font=("Microsoft YaHei UI", 10, "bold"),
                tags=(f"load_action_{index}",),
            )
            bounds = self.canvas.bbox(text_item)
            if bounds:
                rectangle = self.canvas.create_rectangle(
                    bounds[0] - 8,
                    bounds[1] - 6,
                    bounds[2] + 8,
                    bounds[3] + 6,
                    fill=tokens.accent,
                    outline=tokens.accent,
                    tags=(f"load_action_{index}",),
                )
                self.canvas.tag_lower(rectangle, text_item)
            self.canvas.tag_bind(
                f"load_action_{index}",
                "<Button-1>",
                lambda _event, action=callback: action(),
            )
        if notify_error:
            messagebox.showwarning(APP_NAME, f"{failure.path.name}\n\n{failure.user_message}")
        if skip_direction and len(self._failed_navigation_paths) < len(self.navigation.files):
            target = self.navigation.target(skip_direction, wrap=True)
            if target is not None:
                self._begin_image_load(target, skip_direction=skip_direction)

    def _clear_document_after_load_failure(self, failed_path: Path) -> None:
        """Prevent file actions from targeting the previously displayed document."""

        self._clear_annotation_drag(redraw=False)
        self._release_base_preview()
        self.document = None
        self.original = None
        self.image_path = None
        self.preview = None
        self.crop_box = None
        self.selected_annotation_id = None
        self.clean_signature = None
        self.info_var.set(f"未载入 · {failed_path.name}")
        self._set_window_title(failed_path.name)
        self._invalidate_asset_registration()

    def _image_info_text(self) -> str:
        if self.original is None:
            return "未载入图片"
        name = self.image_path.name if self.image_path is not None else "剪贴板图片"
        format_label = (
            self.image_path.suffix.removeprefix(".").upper()
            if self.image_path is not None and self.image_path.suffix
            else (self.original.format or "图片").upper()
        )
        file_size = ""
        if self.image_path is not None:
            try:
                file_size = self._format_bytes(self.image_path.stat().st_size)
            except OSError:
                file_size = ""
        position = self.navigation.position
        details = [name, format_label, f"{self.original.width} × {self.original.height}"]
        if file_size:
            details.append(file_size)
        if position and position[1] > 1:
            details.append(f"第 {position[0]} / {position[1]} 张")
        if self.document is not None and self.document.metadata.is_animated:
            details.append(f"动画 · {self.document.metadata.frame_count} 帧")
        return "  ·  ".join(details)

    def _viewer_footer_info(self) -> str:
        if self.original is None:
            return "未载入图片"
        format_label = (
            self.image_path.suffix.removeprefix(".").upper()
            if self.image_path is not None and self.image_path.suffix
            else (self.original.format or "图片").upper()
        )
        metrics = [f"{self.original.width} × {self.original.height}"]
        if self.image_path is not None:
            try:
                metrics.append(self._format_bytes(self.image_path.stat().st_size))
            except OSError:
                pass
        return "  ·  ".join((format_label, *metrics))

    def _update_viewer_footer_info(self) -> None:
        self.viewer_footer_var.set(self._viewer_footer_info())

    def _begin_animation_playback(self, path: Path) -> None:
        self._animation_generation += 1
        generation = self._animation_generation
        future = self.image_loader.load_animation(path)
        self._animation_pending_future = future
        self.after(20, self._poll_animation_load, generation, future)

    def _poll_animation_load(
        self,
        generation: int,
        future: Future[AnimationSequence],
    ) -> None:
        if generation != self._animation_generation:
            return
        if not future.done():
            self.after(20, self._poll_animation_load, generation, future)
            return
        try:
            self._animation_sequence = future.result()
        except Exception as error:
            self._animation_pending_future = None
            self.status_var.set(f"动画首帧可查看，但多帧播放不可用：{error}")
            self._update_animation_controls()
            return
        self._animation_pending_future = None
        self._animation_index = 0
        self._animation_displayed_index = 0
        self._animation_playing = (
            self.mode_router.active_mode is ModeId.VIEWER and not self.file_compare_var.get()
        )
        self._animation_resume_on_viewer = self.mode_router.active_mode is not ModeId.VIEWER
        self._animation_resume_after_file_compare = self.file_compare_var.get()
        self._update_animation_controls()
        if self._animation_playing:
            self._show_animation_frame()

    def _update_animation_controls(self) -> None:
        sequence = self._animation_sequence
        should_show = (
            sequence is not None
            and self.mode_router.active_mode is ModeId.VIEWER
            and not self.file_compare_var.get()
            and not self._is_immersive
        )
        if should_show:
            self.animation_controls.place(relx=0.5, y=14, anchor="n")
        else:
            self.animation_controls.place_forget()
        if sequence is None:
            self.animation_position_var.set("动画未载入")
            self.animation_frame_slider.minimum = 1.0
            self.animation_frame_slider.maximum = 1.0
            self.animation_frame_var.set(1)
            return
        self.animation_frame_slider.minimum = 1.0
        self.animation_frame_slider.maximum = float(max(1, sequence.frame_count))
        self.animation_frame_var.set(self._animation_displayed_index + 1)
        self.animation_position_var.set(
            f"第 {self._animation_displayed_index + 1} / {sequence.frame_count} 帧"
        )
        if self._animation_playing:
            self.animation_play_button.set_text("暂停")
            set_accessible_name(self.animation_play_button, "暂停动画")
        else:
            self.animation_play_button.set_text("播放")
            set_accessible_name(self.animation_play_button, "播放动画")

    def _cancel_animation_timer(self) -> None:
        if self._animation_job is None:
            return
        try:
            self.after_cancel(self._animation_job)
        except tk.TclError:
            pass
        self._animation_job = None

    def _animation_speed_factor(self) -> float:
        return ANIMATION_SPEED_FACTORS.get(self.animation_speed_var.get(), 1.0)

    def _animation_delay_ms(self, duration_ms: int) -> int:
        """Scale one source-frame duration without mutating animation metadata."""

        return max(10, round(max(1, duration_ms) / self._animation_speed_factor()))

    def _animation_speed_changed(self, _event: tk.Event | None = None) -> None:
        label = self.animation_speed_var.get()
        if label not in ANIMATION_SPEED_FACTORS:
            label = "1×"
            self.animation_speed_var.set(label)
        sequence = self._animation_sequence
        if (
            sequence is not None
            and self._animation_playing
            and self.mode_router.active_mode is ModeId.VIEWER
            and not self.file_compare_var.get()
        ):
            self._cancel_animation_timer()
            duration = sequence.durations[self._animation_displayed_index]
            self._animation_job = self.after(
                self._animation_delay_ms(duration),
                self._show_animation_frame,
            )
        self.status_var.set(f"动画播放速度：{label}")

    def _seek_animation_from_slider(self) -> None:
        sequence = self._animation_sequence
        if sequence is None:
            self.animation_frame_var.set(1)
            return
        if self.file_compare_var.get() or self.mode_router.active_mode is not ModeId.VIEWER:
            self.animation_frame_var.set(self._animation_displayed_index + 1)
            self.status_var.set("请先退出双图对比并返回看图模式，再定位动画帧")
            return
        try:
            requested = int(self.animation_frame_var.get())
        except (tk.TclError, TypeError, ValueError):
            requested = self._animation_displayed_index + 1
        target = max(0, min(sequence.frame_count - 1, requested - 1))
        self._animation_playing = False
        self._animation_resume_on_viewer = False
        self._animation_resume_after_file_compare = False
        self._cancel_animation_timer()
        self._animation_index = target
        self._show_animation_frame()
        self.status_var.set(f"已定位到第 {target + 1} 帧，动画已暂停")

    def _toggle_animation_playback(self) -> None:
        sequence = self._animation_sequence
        if sequence is None:
            return
        if self.file_compare_var.get():
            self.status_var.set("请先退出双图对比，再控制动画播放")
            return
        if self._animation_playing:
            self._animation_playing = False
            self._animation_resume_on_viewer = False
            self._animation_resume_after_file_compare = False
            self._cancel_animation_timer()
            self._animation_index = self._animation_displayed_index
            self._update_animation_controls()
            return
        self._animation_playing = True
        self._animation_resume_on_viewer = False
        self._animation_resume_after_file_compare = False
        self._animation_index = (self._animation_displayed_index + 1) % sequence.frame_count
        self._update_animation_controls()
        self._show_animation_frame()

    def _step_animation(self, direction: int) -> None:
        sequence = self._animation_sequence
        if sequence is None:
            return
        if self.file_compare_var.get():
            self.status_var.set("请先退出双图对比，再逐帧查看动画")
            return
        self._animation_playing = False
        self._animation_resume_on_viewer = False
        self._animation_resume_after_file_compare = False
        self._cancel_animation_timer()
        self._animation_index = (
            self._animation_displayed_index + (1 if direction >= 0 else -1)
        ) % sequence.frame_count
        self._show_animation_frame()

    def _show_animation_frame(self) -> None:
        self._animation_job = None
        sequence = self._animation_sequence
        if self.file_compare_var.get() or self.mode_router.active_mode is not ModeId.VIEWER:
            self._animation_playing = False
            self._update_animation_controls()
            return
        if sequence is None or self.document is None or self.document.timeline.cursor:
            self._animation_playing = False
            self._update_animation_controls()
            return
        index = self._animation_index % sequence.frame_count
        frame = sequence.frames[index]
        canvas_w = max(100, self.canvas.winfo_width())
        canvas_h = max(100, self.canvas.winfo_height())
        zoom = self.zoom_mode.get()
        scale = (
            min(canvas_w / frame.width, canvas_h / frame.height, 1.0)
            if zoom == "适应"
            else int(zoom.replace("%", "")) / 100
        )
        size = (max(1, round(frame.width * scale)), max(1, round(frame.height * scale)))
        display = (
            frame.copy() if size == frame.size else frame.resize(size, Image.Resampling.BILINEAR)
        )
        managed = color_manage_for_display(display, self.document.metadata.icc_profile)
        if managed is not display:
            display.close()
        display = managed
        self._display_preview(
            PreviewFrame(working=frame, image=display, scale=display.width / frame.width),
            canvas_w,
            canvas_h,
        )
        self._animation_displayed_index = index
        self._update_animation_controls()
        if self._animation_playing:
            duration = sequence.durations[index]
            self._animation_index = (index + 1) % sequence.frame_count
            self._animation_job = self.after(
                self._animation_delay_ms(duration),
                self._show_animation_frame,
            )

    def _stop_animation_playback(self) -> None:
        self._animation_generation += 1
        self._cancel_animation_timer()
        pending = self._animation_pending_future
        self._animation_pending_future = None
        if pending is not None:
            self._discard_animation_when_ready(pending)
        if self._animation_sequence is not None:
            for frame in self._animation_sequence.frames:
                frame.close()
        self._animation_sequence = None
        self._animation_index = 0
        self._animation_displayed_index = 0
        self._animation_playing = False
        self._animation_resume_on_viewer = False
        self._animation_resume_after_file_compare = False
        self._update_animation_controls()

    @staticmethod
    def _discard_animation_when_ready(future: Future[AnimationSequence]) -> None:
        def discard(completed: Future[AnimationSequence]) -> None:
            if completed.cancelled() or completed.exception() is not None:
                return
            for frame in completed.result().frames:
                frame.close()

        if future.done():
            discard(future)
        else:
            future.add_done_callback(discard)

    def export_current_animation(self) -> None:
        if (
            self.image_path is None
            or self.document is None
            or not self.document.metadata.is_animated
        ):
            messagebox.showinfo(APP_NAME, "当前文件不是可导出的动画图片。")
            return
        target = filedialog.asksaveasfilename(
            title="导出动画",
            defaultextension=".webp",
            initialfile=f"{self.image_path.stem}_animated.webp",
            filetypes=[
                ("动画 WebP", "*.webp"),
                ("动画 GIF", "*.gif"),
                ("APNG", "*.png *.apng"),
            ],
        )
        if not target:
            return
        suffix = Path(target).suffix.casefold()
        output_format = (
            "GIF" if suffix == ".gif" else ("PNG" if suffix in {".png", ".apng"} else "WEBP")
        )
        try:
            sequence = self._animation_sequence or decode_animation(self.image_path)
            timeline = self.document.timeline
            saved = export_animation(
                sequence,
                target,
                output_format,
                quality=self.quality_var.get(),
                transform=lambda frame: render_image(frame, timeline),
            )
        except Exception as error:
            messagebox.showerror(APP_NAME, f"动画导出失败：{error}")
            return
        self._remember_export_directory(saved.parent)
        self.status_var.set(f"动画已导出：{saved}")
        messagebox.showinfo(APP_NAME, f"动画导出完成：\n{saved}")

    def navigate_folder(self, direction: int, *, wrap: bool = True) -> bool:
        if len(self.navigation.files) <= 1:
            self.status_var.set("当前浏览序列没有其他图片")
            return False
        if not self._confirm_discard_changes():
            self.stop_slideshow()
            return False
        target = self.navigation.target(direction, wrap=wrap)
        if target is None:
            self.stop_slideshow()
            self.status_var.set("幻灯片已播放到末尾")
            return False
        self._begin_image_load(target, skip_direction=direction)
        return True

    def navigate_folder_to(self, position: str) -> None:
        if not self.navigation.files:
            return
        if not self._confirm_discard_changes():
            return
        target = self.navigation.select_edge(position)
        if target is not None:
            self._begin_image_load(target, notify_error=True)

    def _activate_thumbnail(self, path: Path) -> bool:
        current = self.navigation.current
        if current is not None:
            try:
                if current.resolve() == path.resolve():
                    return True
            except OSError:
                if current.absolute() == path.absolute():
                    return True
        if not self._confirm_discard_changes():
            return False
        if self._viewer_gallery_scope == "favorites":
            try:
                available = path.is_file() and path.suffix.lower() in SUPPORTED_INPUTS
            except OSError:
                available = False
            if not available:
                stale_key = self._viewer_favorite_key(path)
                self.viewer_favorite_paths = [
                    saved_path
                    for saved_path in self.viewer_favorite_paths
                    if self._viewer_favorite_key(Path(saved_path)) != stale_key
                ]
                self._update_viewer_gallery_button()
                self._save_config()
                self.status_var.set("收藏图片已不存在，已从列表移除")
                return False
            self.open_path(path, confirm=False)
            return True
        if not self.navigation.select(path):
            self.status_var.set("图片已不在当前浏览序列中")
            return False
        self.stop_slideshow()
        self._begin_image_load(path, notify_error=True)
        return True

    def paste_from_clipboard(self) -> None:
        if not self._confirm_discard_changes():
            return
        self._disable_file_comparison(
            announce=False,
            refresh=False,
            resume_animation=False,
        )
        try:
            clipboard = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"读取剪贴板失败：{exc}")
            return

        if isinstance(clipboard, Image.Image):
            copied = clipboard.copy()
            self.document = Document.from_decoded(copied, None, self._new_document_state(copied))
            self._reset_smart_edit_for_new_document()
            self.document.base_revision = 1
            self.document.mark_recovered_dirty()
            self.original = self.document.image
            self.image_path = None
            self.navigation = NavigationSession([])
            self.thumbnail_sidebar.set_paths([], None)
            self._load_current_image("剪贴板图片", recovered=True)
            self.status_var.set("已从剪贴板粘贴图片")
            return

        if isinstance(clipboard, list):
            for item in clipboard:
                path = Path(item)
                if path.exists() and path.suffix.lower() in SUPPORTED_INPUTS:
                    self.open_paths([path], confirm=False)
                    return

        messagebox.showinfo(APP_NAME, "剪贴板里没有可粘贴的图片。")

    def _load_current_image(self, display_name: str, *, recovered: bool = False) -> None:
        if self.original is None or self.document is None:
            return
        # Startup layout events may still have a deferred resize marker.  The
        # freshly loaded document owns the current preview from this point on.
        self._viewer_resize_preview_pending = False
        self._clear_annotation_drag(redraw=False)
        self.selected_annotation_id = None
        self._set_window_title(display_name)
        self._apply_document_state(self.document.state)
        self._sync_controls_from_timeline()
        if self.mode_router.active_mode is ModeId.CONVERT:
            self._apply_conversion_draft_to_controls()
        self.info_var.set(
            self._image_info_text()
            if self.image_path is not None
            else f"{display_name}  {self.original.width} x {self.original.height}"
        )
        self._update_viewer_gallery_button()
        self._update_viewer_favorite_button()
        self.crop_var.set("未设置裁剪")
        self.pan_offset = (0, 0)
        self.zoom_mode.set("适应")
        # Synchronizing the many inspector variables can schedule a redundant
        # preview through widget callbacks.  The direct render below is the only
        # request needed for a newly loaded document.
        if self._preview_job is not None:
            try:
                self.after_cancel(self._preview_job)
            except tk.TclError:
                pass
            self._preview_job = None
        self._refresh_preview()
        self._refresh_history_panel()
        if self.document.source_path is None:
            self._invalidate_asset_registration()
        else:
            self._schedule_asset_registration(self.document.source_path)
        if not recovered:
            self._mark_clean()

    def _remember_file(self, path: Path) -> None:
        value = str(path)
        self.recent_files = [item for item in self.recent_files if item != value]
        self.recent_files.insert(0, value)
        self.recent_files = self.recent_files[:8]
        directory = str(path.parent)
        self.recent_directories = [item for item in self.recent_directories if item != directory]
        self.recent_directories.insert(0, directory)
        self.recent_directories = self.recent_directories[:8]
        self._refresh_recent_menu()
        self._refresh_recent_directory_menu()
        # Rapid next/previous navigation used to synchronously read and rewrite
        # the complete settings file for every image.  Keep menus current while
        # moving the disk write off the latency-critical navigation path.
        self._schedule_config_save()

    def _remember_project(self, path: Path) -> None:
        value = str(path.resolve())
        self.recent_projects = [item for item in self.recent_projects if item != value]
        self.recent_projects.insert(0, value)
        self.recent_projects = self.recent_projects[:8]
        self.last_project = value
        self._refresh_recent_project_menu()
        self._save_config()

    def _remember_export_directory(self, path: Path) -> None:
        value = str(path.resolve())
        self.recent_export_directories = [
            item for item in self.recent_export_directories if item != value
        ]
        self.recent_export_directories.insert(0, value)
        self.recent_export_directories = self.recent_export_directories[:8]
        self._refresh_recent_export_menu()
        self._save_config()

    def toggle_edit_panel(self) -> None:
        if self.edit_panel_visible_var.get():
            if self.mode_router.active_mode is not ModeId.EDIT:
                self.mode_router.switch(ModeId.EDIT)
            else:
                self.panel_shell.grid()
            self.status_var.set("编辑器已展开")
        else:
            if self.mode_router.active_mode in {ModeId.CONVERT, ModeId.EDIT}:
                self.mode_router.switch(ModeId.VIEWER)
            else:
                self.panel_shell.grid_remove()
            self.status_var.set("编辑器已收起，画布空间已扩大")
        self._save_config()
        self._schedule_preview()

    def toggle_thumbnail_sidebar_from_shortcut(self) -> str:
        self.thumbnail_sidebar_visible_var.set(not self.thumbnail_sidebar_visible_var.get())
        self.toggle_thumbnail_sidebar()
        return "break"

    def toggle_thumbnail_sidebar(self) -> None:
        if self.mode_router.active_mode is ModeId.VIEWER:
            self.thumbnail_sidebar.grid_remove()
            self.conversion_workspace.grid_remove()
            self.columnconfigure(0, minsize=0)
            if self.thumbnail_sidebar_visible_var.get() and not self._is_immersive:
                self.thumbnail_filmstrip.select(self.navigation.current)
                scope_label = "当前文件夹" if self._viewer_gallery_scope == "folder" else "我的收藏"
                self.status_var.set(f"已显示{scope_label}图片")
            else:
                self.viewer_gallery_scope_bar.place_forget()
                self.thumbnail_filmstrip.place_forget()
                self.status_var.set("已收起图片预览")
            self._place_canvas_status()
            self._update_viewer_gallery_button()
            self._save_config()
            self._schedule_preview()
            return
        if self.thumbnail_sidebar_visible_var.get() and not self._is_immersive:
            if self.mode_router.active_mode is ModeId.CONVERT:
                self.thumbnail_sidebar.grid_remove()
                self.conversion_workspace.grid()
                self.status_var.set("已显示转换任务队列")
            else:
                self.conversion_workspace.grid_remove()
                self.thumbnail_sidebar.grid()
                self.thumbnail_sidebar.select(self.navigation.current)
                self.status_var.set("已显示当前目录缩略图")
            self.columnconfigure(0, minsize=220)
        else:
            self.thumbnail_sidebar.grid_remove()
            self.conversion_workspace.grid_remove()
            self.columnconfigure(0, minsize=0)
            self.status_var.set("已隐藏左侧栏")
        self._update_viewer_gallery_button()
        self._save_config()
        self._schedule_preview()

    def toggle_fullscreen(self) -> None:
        self._hide_zoom_preset_popup()
        if self._is_immersive:
            self.toggle_immersive()
            return
        target = not self._is_fullscreen
        if not self._set_fullscreen_state(target):
            return
        self._is_fullscreen = target
        self._update_command_dock_for_mode(self.mode_router.active_mode)
        self._place_canvas_status()
        self.status_var.set(
            "已进入全屏，按 F11 或 Esc 退出" if self._is_fullscreen else "已退出全屏"
        )

    def toggle_immersive(self) -> None:
        target = not self._is_immersive
        if not self._set_fullscreen_state(target):
            return
        self._is_immersive = target
        if target:
            self._is_fullscreen = True
            self.config(menu="")
            self.toolbar.grid_remove()
            self.mode_bar.grid_remove()
            self.panel_shell.grid_remove()
            self.thumbnail_sidebar.grid_remove()
            self.conversion_workspace.grid_remove()
            self.command_dock.place_forget()
            self.status_label.place_forget()
            self.canvas_metadata.place_forget()
            self.viewer_file_details.place_forget()
            self.viewer_gallery_button.place_forget()
            self.viewer_gallery_scope_bar.place_forget()
            self.thumbnail_filmstrip.place_forget()
            self._hide_viewer_navigation()
        else:
            self._is_fullscreen = False
            self.config(menu="")
            self.toolbar.grid_remove()
            self.mode_bar.grid_remove()
            self._apply_mode_layout(self.mode_router.active_mode, announce=False)
        self._update_animation_controls()
        self._schedule_preview()

    def _set_fullscreen_state(self, enabled: bool) -> bool:
        """Enter fullscreen without letting work-area min/max constraints block Tk."""

        if enabled:
            self.minsize(1, 1)
            self.maxsize(
                max(16_384, self.winfo_screenwidth() * 4),
                max(16_384, self.winfo_screenheight() * 4),
            )
        if self.window_controller is not None:
            if not self.window_controller.set_fullscreen(enabled):
                self.dpi_manager.refresh()
                self._update_monitor_constraints()
                self.status_var.set("无法切换全屏：窗口控制器未响应")
                return False
            if self.window_titlebar is not None:
                if enabled:
                    self.window_titlebar.grid_remove()
                else:
                    self.window_titlebar.grid()
            self.rounded_window_effect.schedule()
            if not enabled:
                self.dpi_manager.refresh()
                self._update_monitor_constraints()
            return True
        try:
            self.attributes("-fullscreen", enabled)
        except tk.TclError as error:
            self.dpi_manager.refresh()
            self._update_monitor_constraints()
            self.status_var.set(f"无法切换全屏：{error}")
            return False
        if not enabled:
            self.dpi_manager.refresh()
            self._update_monitor_constraints()
        return True

    def _handle_escape(self) -> None:
        if self._slideshow_job is not None:
            self.stop_slideshow()
        elif self._viewer_clean_mode:
            self.toggle_viewer_clean_mode()
        elif self._is_immersive:
            self.toggle_immersive()
        elif self._is_fullscreen:
            self.toggle_fullscreen()
        elif self.annotation_tool_var.get() == "perspective":
            self._set_annotation_tool("select")
        elif self.selected_annotation_id is not None:
            self.selected_annotation_id = None
            self.selected_annotation_var.set("未选择标注")
            self._draw_annotation_overlay()
        elif self.annotation_tool_var.get() == "crop" and self._crop_confirmation_available():
            self._cancel_crop_interaction()
        else:
            self.reset_crop()

    def apply_current_tool(self) -> None:
        if self.original is None:
            self.open_file()
        elif self.annotation_tool_var.get() == "crop":
            self.apply_crop()
        elif self.annotation_tool_var.get() == "perspective":
            self.apply_perspective()

    def configure_slideshow(self) -> None:
        interval = simpledialog.askfloat(
            APP_NAME,
            "每张图片显示多少秒？",
            initialvalue=self.slideshow_interval_var.get(),
            minvalue=0.5,
            maxvalue=3600.0,
            parent=self,
        )
        if interval is None:
            return
        self.slideshow_interval_var.set(interval)
        loop = messagebox.askyesno(APP_NAME, "播放到最后一张后循环播放吗？", parent=self)
        self.slideshow_loop_var.set(loop)
        self._save_config()

    def toggle_slideshow(self) -> None:
        if self._slideshow_job is not None:
            self.stop_slideshow()
            return
        if len(self.navigation.files) <= 1:
            messagebox.showinfo(APP_NAME, "当前浏览序列至少需要两张图片才能播放幻灯片。")
            return
        self.status_var.set("幻灯片播放中，按 F5 或 Esc 停止")
        self._schedule_slideshow()

    def _schedule_slideshow(self) -> None:
        interval_ms = max(500, round(self.slideshow_interval_var.get() * 1000))
        self._slideshow_job = self.after(interval_ms, self._advance_slideshow)

    def _advance_slideshow(self) -> None:
        self._slideshow_job = None
        advanced = self.navigate_folder(1, wrap=self.slideshow_loop_var.get())
        if advanced:
            self._schedule_slideshow()

    def stop_slideshow(self) -> None:
        if self._slideshow_job is not None:
            self.after_cancel(self._slideshow_job)
            self._slideshow_job = None
            self.status_var.set("幻灯片已停止")

    def _current_file(self) -> Path | None:
        if self.image_path is None:
            messagebox.showinfo(APP_NAME, "当前内容不是磁盘上的图片文件。")
            return None
        return self.image_path

    def reveal_current(self) -> None:
        path = self._current_file()
        if path is not None:
            try:
                reveal_file(path)
            except OSError as error:
                messagebox.showerror(APP_NAME, f"无法打开资源管理器：{error}")

    def rename_current(self) -> None:
        path = self._current_file()
        if path is None:
            return
        name = simpledialog.askstring(
            APP_NAME,
            "输入新文件名（保留或修改扩展名）：",
            initialvalue=path.name,
            parent=self,
        )
        if name is None or name == path.name:
            return
        try:
            target = rename_file(path, name)
        except (OSError, ValueError) as error:
            messagebox.showerror(APP_NAME, f"重命名失败：{error}")
            return
        parent_revision = self._current_asset_revision
        self.image_loader.invalidate(path)
        self.thumbnail_sidebar.invalidate(path)
        self.thumbnail_filmstrip.invalidate(path)
        self.navigation.files[self.navigation.index] = target
        self.thumbnail_sidebar.set_paths(self.navigation.files, target)
        self.image_path = target
        if self.document is not None:
            self.document.rebind_source(target)
        self._schedule_asset_registration(target, parent_revision=parent_revision)
        self.info_var.set(self._image_info_text())
        self._remember_file(target)
        self.status_var.set(f"已重命名为：{target.name}")

    def copy_current(self) -> None:
        path = self._current_file()
        if path is None:
            return
        folder = filedialog.askdirectory(title="复制到文件夹", initialdir=str(path.parent))
        if not folder:
            return
        try:
            target = copy_file(path, folder)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"复制失败：{error}")
            return
        self.status_var.set(f"已复制到：{target}")

    def move_current(self) -> None:
        path = self._current_file()
        if path is None or not self._confirm_discard_changes():
            return
        folder = filedialog.askdirectory(title="移动到文件夹", initialdir=str(path.parent))
        if not folder:
            return
        try:
            target = move_file(path, folder)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"移动失败：{error}")
            return
        parent_revision = self._current_asset_revision
        self.image_loader.invalidate(path)
        self.thumbnail_sidebar.invalidate(path)
        self.thumbnail_filmstrip.invalidate(path)
        self.status_var.set(f"已移动到：{target}")
        if parent_revision is not None:
            self._asset_relocation_hint = (target.resolve(strict=False), parent_revision)
        self.open_paths([target], confirm=False)

    def delete_current_to_recycle_bin(self) -> None:
        path = self._current_file()
        if path is None or not self._confirm_discard_changes():
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"将 {path.name} 移入回收站？\n\n文件不会被永久删除，可从 Windows 回收站恢复。",
            parent=self,
        ):
            return
        try:
            send_to_recycle_bin(path)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"无法移入回收站：{error}")
            return
        self.image_loader.invalidate(path)
        self.thumbnail_sidebar.invalidate(path)
        self.thumbnail_filmstrip.invalidate(path)
        target = self.navigation.remove(path)
        self.thumbnail_sidebar.set_paths(self.navigation.files, target)
        self.original = None
        self.document = None
        self.image_path = None
        self.session_store.clear()
        self.preview = None
        self.canvas.delete("all")
        self._invalidate_asset_registration()
        if target is not None:
            self._begin_image_load(target, skip_direction=1)
        else:
            self.info_var.set("未载入图片")
            self.status_var.set("文件已移入回收站；可通过“文件 → 打开回收站”恢复")
        if messagebox.askyesno(APP_NAME, "文件已移入回收站。现在打开回收站查看或恢复吗？"):
            open_recycle_bin()

    def show_properties(self) -> None:
        path = self._current_file()
        if path is None or self.original is None:
            return
        try:
            properties = image_properties(
                path,
                self.original,
                self.document.metadata if self.document is not None else None,
            )
        except OSError as error:
            messagebox.showerror(APP_NAME, f"无法读取图片属性：{error}")
            return
        window = tk.Toplevel(self)
        window.title(f"属性 - {path.name}")
        window.geometry("620x520")
        window.minsize(520, 380)
        window.transient(self)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window(0, 0, window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for row, (label, value) in enumerate(properties):
            ttk.Label(body, text=label, width=18).grid(row=row, column=0, sticky="nw", pady=4)
            ttk.Label(body, text=value, wraplength=410).grid(row=row, column=1, sticky="nw", pady=4)
        body.columnconfigure(1, weight=1)

    def save_as(self) -> None:
        if self.original is None:
            messagebox.showinfo(APP_NAME, "请先打开一张图片。")
            return
        options = self._read_options()
        if options is None:
            return
        affixes = self._read_filename_affixes()
        if affixes is None:
            return
        prefix, suffix = affixes
        default_stem = self.image_path.stem if self.image_path is not None else "clipboard"
        default_name = self._output_stem(
            default_stem, prefix=prefix, suffix=suffix
        ) + extension_for_format(options.output_format)
        target = filedialog.asksaveasfilename(
            title="保存图片",
            defaultextension=extension_for_format(options.output_format),
            initialfile=default_name,
            initialdir=(
                self.recent_export_directories[0]
                if self.recent_export_directories
                else str(self.image_path.parent if self.image_path else Path.home() / "Pictures")
            ),
            filetypes=[
                (options.output_format, "*" + extension_for_format(options.output_format)),
                ("所有文件", "*.*"),
            ],
        )
        if not target:
            return
        try:
            saved = (
                self._save_conversion_image(Path(target), options)
                if self.mode_router.active_mode is ModeId.CONVERT
                else self._save_document_image(Path(target), options)
            )
            if saved is None:
                return
            self._save_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"保存失败：{exc}")
            return
        self.status_var.set(f"已保存：{saved}")
        self._remember_export_directory(saved.parent)
        if self.mode_router.active_mode is not ModeId.CONVERT:
            self._mark_clean(saved)
            self.session_store.clear()
        if self.reveal_after_save_var.get():
            self._reveal_file(saved)

    def quick_export(self) -> None:
        if self.original is None:
            messagebox.showinfo(APP_NAME, "请先打开一张图片。")
            return
        options = self._read_options()
        if options is None:
            return
        affixes = self._read_filename_affixes()
        if affixes is None:
            return
        prefix, suffix = affixes
        if self.recent_export_directories:
            folder = Path(self.recent_export_directories[0])
            stem = self.image_path.stem if self.image_path is not None else "clipboard"
        elif self.image_path is not None:
            folder = self.image_path.parent
            stem = self.image_path.stem
        else:
            folder = Path.home() / "Pictures" / "LightImageEditor"
            stem = "clipboard"
        target = self._unique_export_path(
            folder,
            self._output_stem(stem, fallback_suffix="_edited", prefix=prefix, suffix=suffix),
            extension_for_format(options.output_format),
        )
        try:
            saved = (
                self._save_conversion_image(target, options)
                if self.mode_router.active_mode is ModeId.CONVERT
                else self._save_document_image(target, options)
            )
            if saved is None:
                return
            self._save_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"快速导出失败：{exc}")
            return
        self.status_var.set(f"已快速导出：{saved}")
        self._remember_export_directory(saved.parent)
        if self.reveal_after_save_var.get():
            self._reveal_file(saved)

    def _save_document_image(self, target: Path, options: ExportOptions) -> Path | None:
        if self.original is None or self.document is None:
            return None
        allow_animation_flatten = False
        allow_transparency_flatten = False
        conflict_policy = ConflictPolicy.ERROR
        while True:
            try:
                return save_image(
                    self.original,
                    target,
                    options,
                    metadata=self.document.metadata,
                    source_path=self.document.source_path,
                    source_fingerprint=self.document.source_fingerprint,
                    conflict_policy=conflict_policy,
                    backup_on_overwrite=self.document.state.backup_on_overwrite,
                    source_passthrough=self.document.base_revision == 0,
                    timeline=self.document.timeline,
                    allow_animation_flatten=allow_animation_flatten,
                    allow_transparency_flatten=allow_transparency_flatten,
                )
            except AnimationFlatteningRequired:
                if not messagebox.askyesno(
                    APP_NAME,
                    "这是动画图片。当前编辑器只会导出当前显示帧，动画帧将丢失。\n\n"
                    "是否确认另存为单帧图片？原动画不会被直接修改。",
                ):
                    return None
                allow_animation_flatten = True
            except TransparencyFlatteningRequired:
                if not messagebox.askyesno(
                    APP_NAME,
                    "目标格式不支持透明通道，透明区域将使用白色背景。是否继续？",
                ):
                    return None
                allow_transparency_flatten = True
            except FileExistsError:
                if not messagebox.askyesno(
                    APP_NAME,
                    f"目标文件已存在：\n{target}\n\n是否覆盖？"
                    + (
                        "覆盖前会创建 .bak 备份。"
                        if self.document.state.backup_on_overwrite
                        else ""
                    ),
                ):
                    return None
                conflict_policy = ConflictPolicy.OVERWRITE
            except SourceChangedError as error:
                messagebox.showerror(
                    APP_NAME,
                    f"{error}\n\n请另存为新文件，或重新打开源文件确认外部更改。",
                )
                return None

    def _save_conversion_image(
        self,
        target: Path,
        options: ExportOptions,
    ) -> Path | None:
        if self.original is None:
            return None
        recipe = self._active_conversion_recipe()
        profile = recipe.export_profile
        result = execute_transform_recipe(self.original, recipe)
        allow_animation_flatten = False
        allow_transparency_flatten = profile.transparency_background is not None
        conflict_policy = ConflictPolicy.ERROR
        try:
            while True:
                try:
                    return save_image(
                        result,
                        target,
                        options,
                        metadata=(self.document.metadata if self.document is not None else None),
                        source_path=(
                            self.document.source_path if self.document is not None else None
                        ),
                        source_fingerprint=(
                            self.document.source_fingerprint if self.document is not None else None
                        ),
                        conflict_policy=conflict_policy,
                        backup_on_overwrite=profile.backup_on_overwrite,
                        source_passthrough=False,
                        allow_animation_flatten=allow_animation_flatten,
                        allow_transparency_flatten=allow_transparency_flatten,
                    )
                except AnimationFlatteningRequired:
                    if not messagebox.askyesno(
                        APP_NAME,
                        "这是动画图片。当前转换配方会输出单帧图片，是否继续？",
                    ):
                        return None
                    allow_animation_flatten = True
                except TransparencyFlatteningRequired:
                    if not messagebox.askyesno(
                        APP_NAME,
                        "目标格式不支持透明通道，透明区域将使用白色背景。是否继续？",
                    ):
                        return None
                    allow_transparency_flatten = True
                except FileExistsError:
                    if not messagebox.askyesno(
                        APP_NAME,
                        f"目标文件已存在：\n{target}\n\n是否覆盖？"
                        + ("覆盖前会创建 .bak 备份。" if profile.backup_on_overwrite else ""),
                    ):
                        return None
                    conflict_policy = ConflictPolicy.OVERWRITE
                except SourceChangedError as error:
                    messagebox.showerror(
                        APP_NAME,
                        f"{error}\n\n请另存为新文件，或重新打开源文件确认外部更改。",
                    )
                    return None
        finally:
            result.close()

    def copy_result(self) -> None:
        if self.original is None:
            messagebox.showinfo(APP_NAME, "请先打开一张图片。")
            return
        options = self._read_options()
        if options is None:
            return
        result: Image.Image | None = None
        try:
            if self.mode_router.active_mode is ModeId.CONVERT:
                result = execute_transform_recipe(
                    self.original,
                    self._active_conversion_recipe(),
                )
            else:
                result = (
                    render_image(self.original, self.document.timeline)
                    if self.document
                    else prepare_image(self.original, options)
                )
            copy_image_to_clipboard(result)
            result_size = result.size
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"复制失败：{exc}")
            return
        finally:
            if result is not None:
                result.close()
        self.status_var.set(f"已复制编辑结果：{result_size[0]} x {result_size[1]}")

    @staticmethod
    def _unique_export_path(folder: Path, stem: str, extension: str) -> Path:
        return unique_export_path(folder, stem, extension)

    def _read_filename_affixes(self) -> tuple[str, str] | None:
        state = self.document.state if self.document is not None else None
        try:
            prefix = validate_filename_affix(
                "文件前缀",
                (
                    self.filename_prefix_var.get()
                    if self.mode_router.active_mode is ModeId.CONVERT
                    else state.filename_prefix
                    if state is not None
                    else ""
                ),
            )
            suffix = validate_filename_affix(
                "文件后缀",
                (
                    self.filename_suffix_var.get()
                    if self.mode_router.active_mode is ModeId.CONVERT
                    else state.filename_suffix
                    if state is not None
                    else ""
                ),
            )
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return None
        self.filename_prefix_var.set(prefix)
        self.filename_suffix_var.set(suffix)
        return prefix, suffix

    def _output_stem(
        self,
        stem: str,
        fallback_suffix: str = "",
        prefix: str | None = None,
        suffix: str | None = None,
    ) -> str:
        state = self.document.state if self.document is not None else DocumentState()
        clean_prefix = validate_filename_affix(
            "文件前缀", state.filename_prefix if prefix is None else prefix
        )
        clean_suffix = validate_filename_affix(
            "文件后缀", state.filename_suffix if suffix is None else suffix
        )
        return f"{clean_prefix}{stem}{clean_suffix or fallback_suffix}"

    def batch_process(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择要批量处理的图片",
            filetypes=[
                (
                    "图片文件",
                    "*.jpg *.jpeg *.png *.apng *.webp *.avif *.heic *.heif "
                    "*.bmp *.tif *.tiff *.gif",
                ),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return
        self._start_batch(paths)

    def batch_process_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择要批量处理的文件夹")
        if not folder:
            return
        self._start_batch([folder], output_title="选择输出文件夹", source_root=Path(folder))

    def _start_batch(
        self,
        paths: list[str] | tuple[str, ...],
        output_title: str = "选择输出文件夹",
        source_root: Path | None = None,
    ) -> None:
        output_dir = filedialog.askdirectory(title=output_title)
        if not output_dir:
            return
        options = self._read_options(allow_without_image=True)
        if options is None:
            return
        files = iter_image_files(paths)
        if not files:
            messagebox.showinfo(APP_NAME, "没有找到可处理的图片。")
            return
        self._open_batch_setup(files, Path(output_dir), options, source_root)

    def _open_batch_setup(
        self,
        files: list[Path],
        output_dir: Path,
        options: ExportOptions,
        source_root: Path | None,
    ) -> None:
        window = tk.Toplevel(self)
        window.title(f"批处理设置 · {len(files)} 个文件")
        window.geometry("620x560")
        window.transient(self)
        window.grab_set()
        body = ttk.Frame(window, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        output_format = tk.StringVar(value=options.output_format)
        quality = tk.IntVar(value=options.quality)
        max_width = tk.StringVar(value=str(options.max_width or ""))
        max_height = tk.StringVar(value=str(options.max_height or ""))
        watermark = tk.StringVar(value=options.watermark_text)
        fields = (
            (
                "输出格式",
                ttk.Combobox(
                    body,
                    textvariable=output_format,
                    values=("JPEG", "PNG", "WEBP", "AVIF", "BMP", "TIFF"),
                    state="readonly",
                ),
            ),
            ("质量", ttk.Spinbox(body, from_=1, to=100, textvariable=quality)),
            ("最大宽度", ttk.Entry(body, textvariable=max_width)),
            ("最大高度", ttk.Entry(body, textvariable=max_height)),
            ("文字水印", ttk.Entry(body, textvariable=watermark)),
            ("重命名模板", ttk.Entry(body, textvariable=self.batch_rename_template_var)),
            (
                "并发任务",
                ttk.Spinbox(body, from_=1, to=16, textvariable=self.batch_concurrency_var),
            ),
            (
                "内存上限 MB",
                ttk.Spinbox(
                    body, from_=32, to=8192, increment=32, textvariable=self.batch_memory_var
                ),
            ),
            (
                "同名策略",
                ttk.Combobox(
                    body,
                    textvariable=self.batch_conflict_var,
                    values=("unique", "error", "overwrite"),
                    state="readonly",
                ),
            ),
        )
        for row, (label, control) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=5)
            control.grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(
            body,
            text="清理 EXIF / ICC / DPI / XMP 元数据",
            variable=self.batch_strip_metadata_var,
        ).grid(row=9, column=1, sticky="w", pady=5)
        ttk.Checkbutton(
            body,
            text="GIF / APNG / WebP 保持动画",
            variable=self.batch_preserve_animation_var,
        ).grid(row=10, column=1, sticky="w", pady=5)
        capabilities = codec_capabilities()
        codec_text = "AVIF 可用" if capabilities.avif else "AVIF 不可用"
        codec_text += "；HEIF 插件可用" if capabilities.heif else "；HEIF 插件未安装（可选）"
        ttk.Label(body, text=codec_text).grid(row=11, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(
            body,
            text="模板变量：{name} {stem} {ext} {index:04d} {date} {datetime}",
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=5)

        def build_recipe() -> BatchRecipe:
            recipe = BatchRecipe(
                output_format=output_format.get(),
                quality=quality.get(),
                max_width=int(max_width.get()) if max_width.get().strip() else None,
                max_height=int(max_height.get()) if max_height.get().strip() else None,
                watermark_text=watermark.get().strip(),
                watermark_position=options.watermark_position,
                watermark_opacity=options.watermark_opacity,
                watermark_size_percent=options.watermark_size_percent,
                strip_metadata=self.batch_strip_metadata_var.get(),
                rename_template=self.batch_rename_template_var.get(),
                conflict_policy=self.batch_conflict_var.get(),
                preserve_animation=self.batch_preserve_animation_var.get(),
            )
            recipe.validate()
            return recipe

        def import_preset() -> None:
            path = filedialog.askopenfilename(
                parent=window,
                title="导入批处理预设",
                filetypes=[("批处理预设", "*.liebatchpreset"), ("JSON", "*.json")],
            )
            if not path:
                return
            try:
                preset = BatchPreset.load(path)
            except Exception as error:
                messagebox.showerror(APP_NAME, f"预设导入失败：{error}", parent=window)
                return
            recipe = preset.recipe
            output_format.set(recipe.output_format)
            quality.set(recipe.quality)
            max_width.set(str(recipe.max_width or ""))
            max_height.set(str(recipe.max_height or ""))
            watermark.set(recipe.watermark_text)
            self.batch_rename_template_var.set(recipe.rename_template)
            self.batch_conflict_var.set(recipe.conflict_policy)
            self.batch_strip_metadata_var.set(recipe.strip_metadata)
            self.batch_preserve_animation_var.set(recipe.preserve_animation)

        def export_preset() -> None:
            path = filedialog.asksaveasfilename(
                parent=window,
                title="导出批处理预设",
                defaultextension=".liebatchpreset",
                filetypes=[("批处理预设", "*.liebatchpreset")],
            )
            if not path:
                return
            try:
                BatchPreset(Path(path).stem, build_recipe()).save(path)
            except Exception as error:
                messagebox.showerror(APP_NAME, f"预设导出失败：{error}", parent=window)

        def start() -> None:
            try:
                recipe = build_recipe()
            except (TypeError, ValueError, tk.TclError) as error:
                messagebox.showerror(APP_NAME, f"批处理设置无效：{error}", parent=window)
                return
            window.destroy()
            self._launch_batch(files, output_dir, recipe, source_root)

        actions = ttk.Frame(body)
        actions.grid(row=13, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(actions, text="导入预设", command=import_preset).pack(side="left", padx=4)
        ttk.Button(actions, text="导出预设", command=export_preset).pack(side="left", padx=4)
        ttk.Button(actions, text="取消", command=window.destroy).pack(side="left", padx=4)
        ttk.Button(actions, text="开始处理", style="Accent.TButton", command=start).pack(
            side="left", padx=4
        )

    def _launch_batch(
        self,
        files: list[Path],
        output_dir: Path,
        recipe: BatchRecipe,
        source_root: Path | None,
    ) -> None:
        manifest = BatchManifest.create(
            files,
            output_dir,
            recipe,
            concurrency=self.batch_concurrency_var.get(),
            memory_limit_mb=self.batch_memory_var.get(),
            source_root=source_root,
        )
        if manifest.same_directory_detected:
            self.status_var.set("已检测到输入输出同目录；同路径覆盖会被安全阻止")
        self.batch_manifest = manifest
        self._save_config()
        self._run_batch_manifest()

    def _run_batch_manifest(self) -> None:
        manifest = self.batch_manifest
        if manifest is None:
            return
        self.batch_runner = BatchRunner(
            manifest,
            self.batch_manifest_path,
            progress=lambda state: self._batch_ui_queue.put((False, state)),
        )
        self._open_batch_progress(len(manifest.tasks))
        self.batch_window.geometry("720x210")
        self.status_var.set(f"正在批量处理 {len(manifest.tasks)} 个文件...")
        threading.Thread(target=self._execute_batch_manifest, daemon=True).start()
        self.after(40, self._poll_batch_ui_queue)

    def _execute_batch_manifest(self) -> None:
        runner = self.batch_runner
        if runner is None:
            return
        manifest = runner.run()
        self._batch_ui_queue.put((True, manifest))

    def _poll_batch_ui_queue(self) -> None:
        completed = False
        while True:
            try:
                is_complete, manifest = self._batch_ui_queue.get_nowait()
            except queue.Empty:
                break
            if is_complete:
                self._finish_advanced_batch(manifest)
                completed = True
            else:
                self._update_advanced_batch_progress(manifest)
        if not completed and self.batch_runner is not None:
            self.after(40, self._poll_batch_ui_queue)

    def _update_advanced_batch_progress(self, manifest: BatchManifest) -> None:
        counts = manifest.counts()
        finished = (
            counts[TaskStatus.SUCCEEDED.value]
            + counts[TaskStatus.FAILED.value]
            + counts[TaskStatus.CANCELLED.value]
        )
        self._update_batch_progress(
            finished,
            len(manifest.tasks),
            f"成功 {counts[TaskStatus.SUCCEEDED.value]} · "
            f"失败 {counts[TaskStatus.FAILED.value]} · "
            f"取消 {counts[TaskStatus.CANCELLED.value]}",
        )

    def _finish_advanced_batch(self, manifest: BatchManifest) -> None:
        self.batch_manifest = manifest
        self.batch_runner = None
        counts = manifest.counts()
        ok = counts[TaskStatus.SUCCEEDED.value]
        failed = counts[TaskStatus.FAILED.value]
        cancelled = counts[TaskStatus.CANCELLED.value]
        input_bytes = sum(task.input_bytes for task in manifest.tasks)
        output_bytes = sum(task.output_bytes for task in manifest.tasks)
        savings = self._batch_savings_text(input_bytes, output_bytes)
        self._finish_batch_progress(
            ok, failed + cancelled, len(manifest.tasks), Path(manifest.output_dir), savings
        )
        self.bell()
        peak = manifest.peak_reserved_bytes / 1024 / 1024
        self.status_var.set(
            f"批处理完成：成功 {ok}，失败 {failed}，取消 {cancelled}，峰值预算 {peak:.1f} MB"
        )
        if hasattr(self, "batch_action_frame"):
            for child in self.batch_action_frame.winfo_children():
                child.destroy()
            actions = (
                ("重试失败项", self._retry_batch_failures),
                ("再次运行", self._rerun_batch),
                ("导出日志", self._export_batch_log_dialog),
                ("打开输出", lambda: self._open_folder(Path(manifest.output_dir))),
                ("关闭", self.batch_window.destroy),
            )
            for column, (label, command) in enumerate(actions):
                ttk.Button(self.batch_action_frame, text=label, command=command).grid(
                    row=0, column=column, padx=4
                )
        summary = f"成功 {ok} 个，失败 {failed} 个，取消 {cancelled} 个。"
        if failed or cancelled:
            messagebox.showwarning(APP_NAME, summary + "\n可在进度窗口重试或导出错误日志。")
        else:
            messagebox.showinfo(APP_NAME, summary)

    def _retry_batch_failures(self) -> None:
        if self.batch_manifest is None or not self.batch_manifest.retry_failed():
            messagebox.showinfo(APP_NAME, "没有可重试的任务。")
            return
        self._run_batch_manifest()

    def _rerun_batch(self) -> None:
        if self.batch_manifest is None:
            return
        self.batch_manifest.restart_all()
        self._run_batch_manifest()

    def _export_batch_log_dialog(self) -> None:
        if self.batch_manifest is None:
            return
        target = filedialog.asksaveasfilename(
            title="导出批处理日志",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("JSON", "*.json")],
        )
        if not target:
            return
        try:
            export_batch_log(self.batch_manifest, target)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"日志导出失败：{error}")
            return
        self.status_var.set(f"批处理日志已导出：{target}")

    def _offer_batch_recovery(self) -> None:
        if not self.batch_manifest_path.exists() or self.batch_runner is not None:
            return
        try:
            manifest = BatchManifest.load(self.batch_manifest_path)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return
        if manifest.complete:
            return
        if messagebox.askyesno(
            APP_NAME,
            "检测到上次未完成的批处理任务，是否从待处理项继续？",
        ):
            self.batch_manifest = manifest
            self._run_batch_manifest()

    def _reveal_file(self, path: Path) -> None:
        reveal_file(path)

    def _open_folder(self, path: Path) -> None:
        open_folder(path)

    def _run_batch(
        self,
        files: list[Path],
        output_dir: Path,
        options: ExportOptions,
        source_root: Path | None = None,
        affixes: tuple[str, str] = ("", ""),
    ) -> None:
        ok = 0
        failures: list[str] = []
        input_bytes = 0
        output_bytes = 0
        source_root = source_root.resolve() if source_root else None
        for index, file_path in enumerate(files, start=1):
            if self.batch_cancel_requested:
                failures.append("用户取消了剩余任务")
                break
            self.after(
                0,
                partial(
                    self._update_batch_progress,
                    index - 1,
                    len(files),
                    f"正在处理：{file_path.name}",
                ),
            )
            try:
                image = open_image(file_path)
                target_dir = output_dir
                if source_root is not None:
                    try:
                        relative_parent = file_path.resolve().parent.relative_to(source_root)
                        target_dir = output_dir / relative_parent
                    except ValueError:
                        target_dir = output_dir
                target = self._unique_export_path(
                    target_dir,
                    self._output_stem(file_path.stem, prefix=affixes[0], suffix=affixes[1]),
                    extension_for_format(options.output_format),
                )
                save_image(image, target, options)
                input_bytes += file_path.stat().st_size
                output_bytes += target.stat().st_size
                ok += 1
            except Exception as exc:
                failures.append(f"{file_path.name}: {exc}")

        def finish() -> None:
            savings = self._batch_savings_text(input_bytes, output_bytes)
            self._finish_batch_progress(ok, len(failures), len(files), output_dir, savings)
            if failures:
                messagebox.showwarning(
                    APP_NAME,
                    f"完成 {ok} 张，失败 {len(failures)} 张。\n\n" + "\n".join(failures[:8]),
                )
            self.status_var.set(
                f"批量处理完成：成功 {ok} 张，失败 {len(failures)} 张，"
                f"{savings}，输出到 {output_dir}"
            )

        self.after(0, finish)

    def _open_batch_progress(self, total: int) -> None:
        if hasattr(self, "batch_window") and self.batch_window.winfo_exists():
            self.batch_window.destroy()
        self.batch_cancel_requested = False
        self.batch_window = tk.Toplevel(self)
        self.batch_window.title("批量处理")
        self.batch_window.geometry("460x170")
        self.batch_window.resizable(False, False)
        self.batch_window.transient(self)
        self.batch_window.protocol("WM_DELETE_WINDOW", self._cancel_batch)
        self.batch_progress_text = tk.StringVar(value=f"准备处理 {total} 张图片")
        self.batch_progress_count = tk.StringVar(value=f"0 / {total}")
        ttk.Label(self.batch_window, textvariable=self.batch_progress_text).grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 8)
        )
        self.batch_progress = ttk.Progressbar(self.batch_window, mode="determinate", maximum=total)
        self.batch_progress.grid(row=1, column=0, sticky="ew", padx=18)
        ttk.Label(self.batch_window, textvariable=self.batch_progress_count).grid(
            row=2, column=0, sticky="w", padx=18, pady=(8, 0)
        )
        self.batch_action_frame = ttk.Frame(self.batch_window)
        self.batch_action_frame.grid(row=3, column=0, sticky="e", padx=18, pady=(10, 0))
        ttk.Button(self.batch_action_frame, text="取消剩余任务", command=self._cancel_batch).grid(
            row=0, column=0
        )

    def _update_batch_progress(self, done: int, total: int, text: str) -> None:
        if not hasattr(self, "batch_window") or not self.batch_window.winfo_exists():
            return
        self.batch_progress["maximum"] = total
        self.batch_progress["value"] = done
        self.batch_progress_text.set(text)
        self.batch_progress_count.set(f"{done} / {total}")

    def _finish_batch_progress(
        self, ok: int, failed: int, total: int, output_dir: Path, savings: str = ""
    ) -> None:
        if not hasattr(self, "batch_window") or not self.batch_window.winfo_exists():
            return
        self.batch_progress["value"] = total
        summary = f"完成：成功 {ok} 张，失败 {failed} 张"
        if savings:
            summary += f"，{savings}"
        self.batch_progress_text.set(summary)
        self.batch_progress_count.set(f"{total} / {total}")
        self.batch_window.protocol("WM_DELETE_WINDOW", self.batch_window.destroy)
        if hasattr(self, "batch_action_frame"):
            for child in self.batch_action_frame.winfo_children():
                child.destroy()
            ttk.Button(
                self.batch_action_frame,
                text="打开输出文件夹",
                command=lambda: self._open_folder(output_dir),
            ).grid(row=0, column=0, padx=(0, 8))
            ttk.Button(
                self.batch_action_frame, text="关闭", command=self.batch_window.destroy
            ).grid(row=0, column=1)

    def _cancel_batch(self) -> None:
        self.batch_cancel_requested = True
        if self.batch_runner is not None:
            self.batch_runner.cancel()
        if self._conversion_runner is not None:
            self._conversion_runner.cancel()
        if self._conversion_poll_job is not None:
            try:
                self.after_cancel(self._conversion_poll_job)
            except tk.TclError:
                pass
            self._conversion_poll_job = None
        if hasattr(self, "batch_progress_text"):
            self.batch_progress_text.set("正在取消剩余任务...")

    def _batch_savings_text(self, input_bytes: int, output_bytes: int) -> str:
        if input_bytes <= 0:
            return "无大小统计"
        saved = input_bytes - output_bytes
        percent = saved / input_bytes * 100
        if saved >= 0:
            return f"节省 {self._format_bytes(saved)} ({percent:.1f}%)"
        return f"增大 {self._format_bytes(abs(saved))} ({abs(percent):.1f}%)"

    def _state_signature(self) -> tuple[object, ...] | None:
        if self.document is None:
            return None
        return (
            self.document.state,
            self.document.base_revision,
            self.document.timeline.active_signature(),
            self.document.project_path,
        )

    def _mark_clean(self, saved_path: Path | None = None) -> None:
        if self.document is not None:
            same_source = bool(
                saved_path is not None
                and self.document.source_path is not None
                and saved_path.resolve() == self.document.source_path.resolve()
            )
            self.document.mark_clean(saved_path if same_source else None)
        self.clean_signature = self._state_signature()

    def has_unsaved_changes(self) -> bool:
        return self.document is not None and self.document.dirty

    def _confirm_discard_changes(self) -> bool:
        if not self.has_unsaved_changes():
            return True
        discard = messagebox.askyesno(APP_NAME, "当前图片有未保存的修改。要放弃这些修改吗？")
        if discard:
            self.session_store.clear()
        return discard

    def close(self) -> None:
        if self._confirm_discard_changes():
            self.session_store.clear()
            self._shutdown_services()
            self.destroy()

    def request_exit_for_upgrade(self) -> bool:
        if self.has_unsaved_changes():
            decision = messagebox.askyesnocancel(
                APP_NAME,
                "安装程序准备升级软件，但当前图片有未保存的修改。\n\n"
                "选择“是”先保存，选择“否”放弃修改并安全退出，选择“取消”终止升级。",
                parent=self,
            )
            if decision is None:
                self.status_var.set("用户已取消升级，程序继续运行")
                return False
            if decision:
                self.save_as()
                if self.has_unsaved_changes():
                    self.status_var.set("修改尚未保存，升级已取消")
                    return False
            else:
                self.session_store.clear()
        self.after(50, self._finish_upgrade_exit)
        return True

    def _finish_upgrade_exit(self) -> None:
        self._shutdown_services()
        self.destroy()

    def _cancel_pending_preview_updates(self) -> None:
        self._cancel_annotation_drag_preview()
        if self._window_resize_idle_job is not None:
            try:
                self.after_cancel(self._window_resize_idle_job)
            except tk.TclError:
                pass
            self._window_resize_idle_job = None
        if self._preview_job is not None:
            try:
                self.after_cancel(self._preview_job)
            except tk.TclError:
                pass
            self._preview_job = None
        for job in tuple(self._preview_poll_jobs):
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._preview_poll_jobs.clear()
        self._preview_generation += 1

    def _shutdown_services(self) -> None:
        if self._services_shutdown:
            return
        self._services_shutdown = True
        if self._mode_unsubscribe is not None:
            self._mode_unsubscribe()
            self._mode_unsubscribe = None
        if self._mock_generation_after is not None:
            try:
                self.after_cancel(self._mock_generation_after)
            except tk.TclError:
                pass
            self._mock_generation_after = None
        if self._mock_generation_job_id is not None:
            self.ai_job_manager.cancel(self._mock_generation_job_id)
        if self._smart_edit_after is not None:
            try:
                self.after_cancel(self._smart_edit_after)
            except tk.TclError:
                pass
            self._smart_edit_after = None
        if self._smart_background_compose_after is not None:
            try:
                self.after_cancel(self._smart_background_compose_after)
            except tk.TclError:
                pass
            self._smart_background_compose_after = None
        self._smart_background_compose_generation += 1
        background_future = self._smart_background_compose_future
        if background_future is not None and not background_future.done():
            background_future.cancel()
        self._smart_background_compose_future = None
        self._smart_font_match_generation += 1
        font_match_future = self._smart_font_match_future
        if font_match_future is not None and not font_match_future.done():
            font_match_future.cancel()
        self._smart_font_match_future = None
        self._smart_selection_generation += 1
        selection_future = self._smart_selection_future
        if selection_future is not None and not selection_future.done():
            selection_future.cancel()
        self._smart_selection_future = None
        if self._smart_edit_job_id is not None:
            self.ai_job_manager.cancel(self._smart_edit_job_id)
        self.stop_slideshow()
        self._stop_animation_playback()
        self._cancel_pending_preview_updates()
        if self.batch_runner is not None:
            self.batch_runner.cancel()
        conversion_runner = self._conversion_runner
        if conversion_runner is not None:
            conversion_runner.cancel()
            base_session = self._conversion_run_base_session
            if base_session is not None:
                try:
                    snapshot = base_session.merge_tasks(list(conversion_runner.tasks))
                except ConversionSessionError:
                    pass
                else:
                    self._conversion_session = snapshot
                    self._checkpoint_conversion_session(snapshot, force=True)
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        if self._source_monitor_job is not None:
            self.after_cancel(self._source_monitor_job)
            self._source_monitor_job = None
        if self._theme_poll_job is not None:
            self.after_cancel(self._theme_poll_job)
            self._theme_poll_job = None
        for job in self._startup_jobs:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self._startup_jobs.clear()
        self._cancel_scheduled_config_save()
        self._save_config()
        self._drop_target.disable()
        self.ai_job_manager.shutdown(wait=True)
        self._invalidate_asset_registration()
        self._asset_executor.shutdown(wait=True, cancel_futures=True)
        self._analysis_executor.shutdown(wait=True, cancel_futures=True)
        self.thumbnail_filmstrip.shutdown()
        self.thumbnail_sidebar.shutdown()
        self.image_loader.shutdown()
        self.preview_renderer.shutdown()
        self.conversion_checkpoint_writer.close(timeout=1.5)
        self._close_comparison_images()
        self._release_base_preview()
        self._cancel_all_tk_jobs()

    def _cancel_all_tk_jobs(self) -> None:
        """Cancel widget-owned idle jobs before the Tcl interpreter is destroyed."""

        try:
            jobs = self.tk.splitlist(self.tk.call("after", "info"))
        except tk.TclError:
            return
        for job in jobs:
            try:
                # Use Tcl directly: callbacks may belong to child widgets.  Calling
                # root.after_cancel() would delete the command from the wrong
                # widget bookkeeping list and make widget destruction fail.
                self.tk.call("after", "cancel", job)
            except tk.TclError:
                pass

    def _autosave_session(self) -> None:
        self._autosave_job = None
        try:
            signature = self._state_signature()
            if self.document is not None and self.document.dirty:
                if signature != self._last_autosave_signature:
                    self.session_store.save(self.document)
                    self._last_autosave_signature = signature
            elif self.document is None or not self.document.dirty:
                self.session_store.clear()
                self._last_autosave_signature = None
        except OSError as error:
            self.status_var.set(f"自动保存会话失败：{error}")
        finally:
            self._autosave_job = self.after(2000, self._autosave_session)

    def _offer_session_recovery(self) -> None:
        recovery = self.session_store.load()
        if recovery is None:
            self._restore_workspace()
            return
        message = "检测到上次异常退出时未完成的编辑。是否恢复？"
        if recovery.source_changed:
            message += "\n\n源文件在异常退出后发生过变化，恢复后请另存为新文件。"
        if not messagebox.askyesno(APP_NAME, message):
            self.session_store.clear()
            return
        self.document = recovery.document
        self.original = recovery.document.image
        self.image_path = recovery.document.source_path
        self.navigation = (
            NavigationSession.from_activation([self.image_path])
            if self.image_path is not None
            else NavigationSession([])
        )
        self.thumbnail_sidebar.set_paths(self.navigation.files, self.navigation.current)
        display_name = self.image_path.name if self.image_path is not None else "恢复的剪贴板图片"
        self._load_current_image(display_name, recovered=True)
        self.status_var.set("已恢复上次未完成编辑；可继续编辑、另存或安全放弃")

    def _restore_workspace(self) -> None:
        if self.document is not None or not self.restore_workspace_var.get():
            return
        project = Path(self.last_project) if self.last_project else None
        if project is not None and project.exists():
            self.open_project_path(project, confirm=False)

    def _monitor_source_file(self) -> None:
        self._source_monitor_job = None
        if self.document is not None:
            status = self.document.source_status()
            if status.kind != self._last_source_status:
                if status.kind == "modified":
                    self.status_var.set("源文件已被其他程序修改；覆盖保存已禁用，请重新打开或另存")
                elif status.kind == "renamed":
                    self.status_var.set(f"源文件已被外部重命名为：{status.path}")
                elif status.kind == "missing":
                    self.status_var.set("源文件已被移动或删除；当前编辑仍可另存为新文件")
                self._last_source_status = status.kind
        self._source_monitor_job = self.after(2000, self._monitor_source_file)

    def undo(self) -> None:
        if self.mode_router.active_mode is ModeId.CONVERT:
            draft = self.conversion_draft_history.undo()
            if draft is None:
                self.status_var.set("转图草稿没有可撤销的操作")
                return
            self._apply_conversion_draft_to_controls()
            self._invalidate_conversion_session()
            self._refresh_preview()
            self._update_estimate()
            self.status_var.set("已撤销一步转图设置")
            return
        if self.document is None or self.document.undo_command() is None:
            self.status_var.set("没有可撤销的操作")
            return
        self._sync_controls_from_timeline()
        self._refresh_history_panel()
        self._refresh_preview()
        self.status_var.set("已撤销一步")

    def redo(self) -> None:
        if self.mode_router.active_mode is ModeId.CONVERT:
            draft = self.conversion_draft_history.redo()
            if draft is None:
                self.status_var.set("转图草稿没有可重做的操作")
                return
            self._apply_conversion_draft_to_controls()
            self._invalidate_conversion_session()
            self._refresh_preview()
            self._update_estimate()
            self.status_var.set("已重做一步转图设置")
            return
        if self.document is None or self.document.redo_command() is None:
            self.status_var.set("没有可重做的操作")
            return
        self._sync_controls_from_timeline()
        self._refresh_history_panel()
        self._refresh_preview()
        self.status_var.set("已重做一步")

    def edit_state(self) -> EditState:
        return self.document.state.edit_state if self.document is not None else EditState()

    def reset_edits(self, refresh: bool = True) -> None:
        if self.document is None:
            return
        self._syncing_document = True
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self.brightness_var.set(0)
        self.contrast_var.set(0)
        self.saturation_var.set(0)
        self.sharpness_var.set(0)
        self.grayscale_var.set(False)
        self.auto_contrast_var.set(False)
        self._syncing_document = False
        for name, value in (
            ("brightness", 0),
            ("contrast", 0),
            ("saturation", 0),
            ("sharpen", 0),
            ("grayscale", False),
            ("auto_contrast", False),
        ):
            self._commit_command(
                EditCommand.create("adjust", {"name": name, "value": value}, "重置快速编辑"),
                refresh=False,
            )
        if refresh:
            self.status_var.set("已重置快速编辑")
            self._refresh_preview()

    def reset_crop(self) -> None:
        if self.mode_router.active_mode is ModeId.CONVERT:
            self.crop_box = None
            changed = self._push_conversion_draft(
                self.conversion_draft_history.current.with_crop(None),
                status="已重置转图裁切",
            )
            self.crop_var.set("未设置裁剪")
            if not changed:
                self._refresh_preview()
            self._update_crop_action_bar()
            return
        self.crop_box = None
        self._commit_command(EditCommand.create("crop", {"box": None}, "重置裁剪"))
        self.crop_var.set("未设置裁剪")
        self._update_crop_action_bar()

    def apply_crop(self) -> None:
        if self.original is None:
            return
        if self.crop_box is None:
            self.status_var.set("没有可应用的裁剪区域")
            self._update_crop_action_bar()
            return
        reference = self.base_preview_image
        if reference is None:
            return
        left, top, right, bottom = self.crop_box
        normalized = (
            left / reference.width,
            top / reference.height,
            right / reference.width,
            bottom / reference.height,
        )
        conversion_mode = self.mode_router.active_mode is ModeId.CONVERT
        if not conversion_mode and self.document is None:
            return
        existing = (
            self.conversion_draft_history.current.crop_box
            if conversion_mode
            else reduce_timeline(self.document.timeline).crop_box
        )
        if existing is not None:
            existing_width = existing[2] - existing[0]
            existing_height = existing[3] - existing[1]
            normalized = (
                existing[0] + normalized[0] * existing_width,
                existing[1] + normalized[1] * existing_height,
                existing[0] + normalized[2] * existing_width,
                existing[1] + normalized[3] * existing_height,
            )
        if conversion_mode:
            self.crop_box = None
            self._push_conversion_draft(
                self.conversion_draft_history.current.with_crop(normalized),
                status="已应用转图裁切，可单独撤销",
            )
            self.crop_var.set("配方中已有裁切")
            self._update_crop_action_bar()
            return
        self._commit_command(
            EditCommand.create("crop", {"box": list(normalized)}, "应用裁剪"),
            refresh=False,
        )
        self.crop_box = None
        self.document.update_state("apply_crop", crop_box=None)
        self.crop_var.set("未设置裁剪")
        self.status_var.set("已非破坏应用裁剪，可从历史面板恢复")
        self._refresh_preview()
        self._update_crop_action_bar()

    def center_crop(self) -> None:
        image = self.base_preview_image or self._current_working_image()
        if image is None:
            return
        ratio = self._crop_ratio_value()
        if ratio is None:
            ratio = image.width / image.height
        width = image.width
        height = round(width / ratio)
        if height > image.height:
            height = image.height
            width = round(height * ratio)
        left = max(0, (image.width - width) // 2)
        top = max(0, (image.height - height) // 2)
        self.crop_box = (left, top, left + width, top + height)
        if self.document is not None and self.mode_router.active_mode is not ModeId.CONVERT:
            self.document.update_state("center_crop", crop_box=self.crop_box)
        self._update_crop_label()
        self._refresh_preview()
        self.status_var.set("已设置居中裁剪")

    def apply_crop_ratio_to_current(self) -> None:
        if self.crop_box is None:
            return
        image = self.base_preview_image or self._current_working_image()
        ratio = self._crop_ratio_value()
        if image is None or ratio is None:
            self._update_crop_label()
            return
        left, top, right, bottom = self.crop_box
        width = right - left
        height = max(4, round(width / ratio))
        if top + height > image.height:
            height = image.height - top
            width = round(height * ratio)
        if left + width > image.width:
            width = image.width - left
            height = round(width / ratio)
        self.crop_box = (left, top, min(image.width, left + width), min(image.height, top + height))
        if self.document is not None and self.mode_router.active_mode is not ModeId.CONVERT:
            self.document.update_state("crop_ratio", crop_box=self.crop_box)
        self._update_crop_label()
        self._refresh_preview()

    def rotate(self, degrees: int) -> None:
        if self.original is None:
            return
        if self.mode_router.active_mode is ModeId.CONVERT:
            self.crop_box = None
            current = self.conversion_draft_history.current
            updated = current.with_rotation(current.rotation + degrees)
            self._push_conversion_draft(
                updated,
                status=f"转图旋转已设为 {updated.rotation}°",
            )
            return
        if self.document is None:
            return
        self.crop_box = None
        self._commit_command(
            EditCommand.create("rotate", {"degrees": degrees}, f"旋转 {degrees:+d}°"),
            refresh=False,
        )
        self._sync_controls_from_timeline()
        self.status_var.set(f"已旋转到 {self.rotation}°")
        self._refresh_preview()

    def flip_horizontal(self) -> None:
        if self.original is None:
            return
        if self.mode_router.active_mode is ModeId.CONVERT:
            self.crop_box = None
            current = self.conversion_draft_history.current
            self._push_conversion_draft(
                current.with_flip("horizontal", not current.flip_horizontal),
                status="已切换转图水平翻转",
            )
            return
        self.crop_box = None
        self._commit_command(EditCommand.create("flip_horizontal", {}, "水平翻转"), refresh=False)
        self._sync_controls_from_timeline()
        self.status_var.set("已切换水平翻转")
        self._refresh_preview()

    def flip_vertical(self) -> None:
        if self.original is None:
            return
        if self.mode_router.active_mode is ModeId.CONVERT:
            self.crop_box = None
            current = self.conversion_draft_history.current
            self._push_conversion_draft(
                current.with_flip("vertical", not current.flip_vertical),
                status="已切换转图垂直翻转",
            )
            return
        self.crop_box = None
        self._commit_command(EditCommand.create("flip_vertical", {}, "垂直翻转"), refresh=False)
        self._sync_controls_from_timeline()
        self.status_var.set("已切换垂直翻转")
        self._refresh_preview()

    def apply_size_preset(self, value: str) -> None:
        if self.original is None:
            return
        try:
            reference_size = self._resize_reference_size()
            width, height, max_side = resolve_size_preset(reference_size, value)
        except ValueError as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        conversion_mode = self.mode_router.active_mode is ModeId.CONVERT
        previous_sync = self._syncing_document
        if conversion_mode:
            self._syncing_document = True
        self._ratio_lock = True
        try:
            self.width_var.set("" if width is None else str(width))
            self.height_var.set("" if height is None else str(height))
            self.max_side_var.set("" if max_side is None else str(max_side))
        finally:
            self._ratio_lock = False
            self._syncing_document = previous_sync
        if conversion_mode:
            self._update_conversion_draft_from_controls()
        self.status_var.set("已按当前内容的缩放前尺寸应用常用比例")
        self._update_estimate()

    def _resize_reference_size(self) -> tuple[int, int]:
        """Return the edited content size with only the resize stage disabled."""

        if self.original is None:
            raise ValueError("请先打开一张图片。")
        if self.mode_router.active_mode is ModeId.CONVERT:
            recipe = self.conversion_draft_history.current.to_recipe()
            without_resize = replace(
                recipe,
                steps=tuple(step for step in recipe.steps if step.kind != "resize"),
            )
            reference = execute_transform_recipe(self.original, without_resize)
            try:
                return reference.size
            finally:
                reference.close()
        if self.document is None:
            return self.original.size
        plan = replace(reduce_timeline(self.document.timeline), size=SizeSpec())
        reference = render_image(self.original, plan)
        try:
            return reference.size
        finally:
            reference.close()

    def _read_options(
        self, allow_without_image: bool = False, quiet: bool = False
    ) -> ExportOptions | None:
        conversion_mode = self.mode_router.active_mode is ModeId.CONVERT
        has_source = self.original is not None if conversion_mode else self.document is not None
        if not has_source and not allow_without_image:
            if not quiet:
                messagebox.showinfo(APP_NAME, "请先打开一张图片。")
            return None
        if conversion_mode:
            try:
                profile = self._active_conversion_recipe().export_profile
                return ExportOptions(
                    output_format=profile.output_format,
                    quality=profile.quality,
                    target_size_kb=profile.target_size_kb,
                    webp_lossless=profile.webp_lossless,
                    png_palette_colors=profile.png_palette_colors,
                    tiff_compression=profile.tiff_compression,
                    preserve_metadata=profile.preserve_metadata,
                )
            except (tk.TclError, TypeError, ValueError) as error:
                if not quiet:
                    messagebox.showerror(APP_NAME, str(error))
                return None
        state = (
            self.document.state
            if self.document is not None
            else DocumentState(
                edit_state=self.edit_state(),
                crop_box=self.crop_box,
                crop_ratio=self.crop_ratio_var.get(),
                width_text=self.width_var.get(),
                height_text=self.height_var.get(),
                max_side_text=self.max_side_var.get(),
                output_format=self.format_var.get(),
                quality=self.quality_var.get(),
                target_size_text=self.target_size_var.get(),
                filename_prefix=self.filename_prefix_var.get(),
                filename_suffix=self.filename_suffix_var.get(),
                watermark_text=self.watermark_text_var.get(),
                watermark_position=self.watermark_position_var.get(),
                watermark_opacity=self.watermark_opacity_var.get(),
                watermark_size_percent=self.watermark_size_var.get(),
                preserve_metadata=self.preserve_metadata_var.get(),
                backup_on_overwrite=self.backup_on_overwrite_var.get(),
                export_preset=self.export_preset_var.get(),
            )
        )
        try:
            return state.export_options()
        except ValueError as error:
            if not quiet:
                messagebox.showerror(APP_NAME, str(error))
            return None

    def _current_working_image(self) -> Image.Image | None:
        if self.original is None:
            return None
        if self.mode_router.active_mode is ModeId.CONVERT:
            return execute_transform_recipe(
                self.original,
                self.conversion_draft_history.current.to_recipe(),
            )
        if self.document is not None:
            return render_image(self.original, self.document.timeline)
        return apply_edits(self.original, self.edit_state())

    def _width_changed(self, _event: tk.Event) -> None:
        if self.width_var.get().strip():
            self.max_side_var.set("")
        if self._ratio_lock or not self.keep_ratio_var.get() or self.original is None:
            self._update_estimate()
            return
        try:
            width = int(self.width_var.get())
            if width > 0:
                source_width, source_height = self._resize_reference_size()
                self._ratio_lock = True
                try:
                    self.height_var.set(str(round(width * source_height / source_width)))
                finally:
                    self._ratio_lock = False
        except ValueError:
            pass
        self._update_estimate()

    def _height_changed(self, _event: tk.Event) -> None:
        if self.height_var.get().strip():
            self.max_side_var.set("")
        if self._ratio_lock or not self.keep_ratio_var.get() or self.original is None:
            self._update_estimate()
            return
        try:
            height = int(self.height_var.get())
            if height > 0:
                source_width, source_height = self._resize_reference_size()
                self._ratio_lock = True
                try:
                    self.width_var.set(str(round(height * source_width / source_height)))
                finally:
                    self._ratio_lock = False
        except ValueError:
            pass
        self._update_estimate()

    def _max_side_changed(self) -> None:
        if self.max_side_var.get().strip():
            self._ratio_lock = True
            self.width_var.set("")
            self.height_var.set("")
            self._ratio_lock = False
        self._update_estimate()

    def _live_adjust(self) -> None:
        self.status_var.set("预览已更新")
        self._schedule_preview()

    def _watermark_changed(self) -> None:
        self.status_var.set("水印设置已更新")
        self._save_config()
        self._update_estimate()

    def _apply_export_preset(self) -> None:
        if self.mode_router.active_mode is ModeId.CONVERT:
            source_format = (
                self.document.metadata.original_format if self.document is not None else None
            )
            state = DocumentState(
                width_text=self.width_var.get(),
                height_text=self.height_var.get(),
                max_side_text=self.max_side_var.get(),
                output_format=self.format_var.get(),
                quality=self.quality_var.get(),
                target_size_text=self.target_size_var.get(),
                preserve_metadata=self.preserve_metadata_var.get(),
                backup_on_overwrite=self.backup_on_overwrite_var.get(),
                export_preset=self.export_preset_var.get(),
            )
            try:
                state = apply_export_preset(
                    state,
                    self.export_preset_var.get(),
                    source_format,
                )
            except ValueError as error:
                messagebox.showerror(APP_NAME, str(error))
                return
            self._syncing_document = True
            try:
                self.width_var.set(state.width_text)
                self.height_var.set(state.height_text)
                self.max_side_var.set(state.max_side_text)
                self.format_var.set(state.output_format)
                self.quality_var.set(state.quality)
                self.target_size_var.set(state.target_size_text)
                self.preserve_metadata_var.set(state.preserve_metadata)
                self.batch_strip_metadata_var.set(not state.preserve_metadata)
                self.export_preset_var.set(state.export_preset)
                self.webp_lossless_var.set(False)
                self.png_palette_var.set("保留原色")
                self.tiff_compression_var.set("不压缩")
            finally:
                self._syncing_document = False
            self._update_format_advanced_controls()
            self._update_conversion_draft_from_controls()
            self._save_config()
            self.status_var.set(f"已应用转图导出预设：{state.export_preset}")
            return
        if self.document is None:
            return
        try:
            state = apply_export_preset(
                self.document.state,
                self.export_preset_var.get(),
                self.document.metadata.original_format,
            )
        except ValueError as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        self.document.set_state("export_preset", state)
        self._apply_document_state(state)
        self._save_config()
        self._update_estimate()
        self.status_var.set(f"已应用导出预设：{state.export_preset}")

    def _format_changed(self) -> None:
        output_format = self.format_var.get().strip().upper()
        if output_format != "WEBP":
            self.webp_lossless_var.set(False)
        if output_format != "PNG":
            self.png_palette_var.set("保留原色")
        if output_format != "TIFF":
            self.tiff_compression_var.set("不压缩")
        self._update_format_advanced_controls()
        self._custom_export_changed()

    def _advanced_format_changed(self) -> None:
        if self.format_var.get().strip().upper() == "WEBP" and self.webp_lossless_var.get():
            self.target_size_var.set("")
        self._update_format_advanced_controls()
        self._custom_export_changed()

    def _update_format_advanced_controls(self) -> None:
        output_format = self.format_var.get().strip().upper()
        if output_format == "WEBP":
            self.webp_lossless_check.state(("!disabled",))
        else:
            self.webp_lossless_check.state(("disabled",))
        self.png_palette_box.configure(state="readonly" if output_format == "PNG" else "disabled")
        self.tiff_compression_box.configure(
            state="readonly" if output_format == "TIFF" else "disabled"
        )
        if output_format == "WEBP" and self.webp_lossless_var.get():
            self.target_size_entry.state(("disabled",))
        else:
            self.target_size_entry.state(("!disabled",))

    def _custom_export_changed(self) -> None:
        if self.mode_router.active_mode is ModeId.CONVERT:
            if not self._syncing_document:
                self._syncing_document = True
                try:
                    self.export_preset_var.set("自定义")
                finally:
                    self._syncing_document = False
                self._update_conversion_draft_from_controls()
            self._save_config()
            self._update_estimate()
            return
        if not self._syncing_document and self.document is not None:
            self.document.update_state("custom_export", export_preset="自定义")
            self._syncing_document = True
            try:
                self.export_preset_var.set("自定义")
            finally:
                self._syncing_document = False
        self._save_config()
        self._update_estimate()

    def _set_compare(self, enabled: bool) -> None:
        if self.original is None:
            return
        if enabled:
            self._disable_file_comparison(
                announce=False,
                refresh=False,
                resume_animation=False,
            )
        self.compare_original.set(enabled)
        self._refresh_preview()

    def _toggle_compare_original(self) -> None:
        if self.compare_original.get():
            self._disable_file_comparison(
                announce=False,
                refresh=False,
                resume_animation=False,
            )
            self.split_compare_var.set(False)
        self._refresh_preview()

    def _toggle_split_compare(self) -> None:
        if self.split_compare_var.get():
            self._disable_file_comparison(
                announce=False,
                refresh=False,
                resume_animation=False,
            )
            self.compare_original.set(False)
            self.status_var.set("分屏对比：左侧编辑前，右侧编辑后")
        self._refresh_preview()

    def _toggle_file_comparison(self) -> None:
        if self.file_compare_var.get():
            self._disable_file_comparison()
            return
        self.choose_file_comparison()

    def _side_by_side_comparison_active(self) -> bool:
        return self.split_compare_var.get() or self.file_compare_var.get()

    def choose_file_comparison(self) -> None:
        if self.original is None:
            self.status_var.set("请先打开一张图片，再选择并排比较对象")
            return
        path = filedialog.askopenfilename(
            title="选择要并排比较的图片",
            initialdir=str(self.image_path.parent) if self.image_path is not None else None,
            filetypes=[
                (
                    "图片文件",
                    "*.jpg *.jpeg *.png *.apng *.webp *.avif *.heic *.heif "
                    "*.bmp *.tif *.tiff *.gif",
                ),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._begin_file_comparison(Path(path))

    @staticmethod
    def _same_path(first: Path, second: Path) -> bool:
        try:
            return first.resolve() == second.resolve()
        except OSError:
            return first.absolute() == second.absolute()

    def _begin_file_comparison(self, path: Path) -> bool:
        if self.original is None:
            self.status_var.set("请先打开一张图片，再选择并排比较对象")
            return False
        if self.image_path is not None and self._same_path(self.image_path, path):
            self.status_var.set("请选择另一张不同的图片进行并排比较")
            return False
        if path.suffix.casefold() not in SUPPORTED_INPUTS:
            self.status_var.set("所选文件不是受支持的图片格式")
            return False
        self._comparison_load_generation += 1
        generation = self._comparison_load_generation
        self.status_var.set(f"正在后台载入对比图片：{path.name}")
        future = self.image_loader.load(path)
        self.after(10, self._poll_file_comparison_load, generation, path, future)
        return True

    def _poll_file_comparison_load(
        self,
        generation: int,
        path: Path,
        future: Future[Image.Image],
    ) -> None:
        if generation != self._comparison_load_generation:
            return
        if not future.done():
            self.after(10, self._poll_file_comparison_load, generation, path, future)
            return
        try:
            comparison = future.result().copy()
        except ImageLoadFailure as failure:
            self.status_var.set(f"无法载入对比图片：{failure.user_message}")
            messagebox.showwarning(APP_NAME, f"{path.name}\n\n{failure.user_message}")
            return
        except Exception as error:
            self.status_var.set(f"无法载入对比图片：{error}")
            messagebox.showwarning(APP_NAME, f"{path.name}\n\n无法载入对比图片：{error}")
            return
        if generation != self._comparison_load_generation:
            comparison.close()
            return
        old = self._comparison_image
        self._comparison_image = comparison
        self._comparison_path = path
        if old is not None:
            self._retire_comparison_image(old)
        self.compare_original.set(False)
        self.split_compare_var.set(False)
        self._animation_resume_after_file_compare = self._animation_playing
        if self._animation_playing:
            self._animation_playing = False
            self._cancel_animation_timer()
        self.file_compare_var.set(True)
        self.viewer_file_compare_button.set_selected(True)
        self._update_animation_controls()
        self.mode_router.update_state(
            mode=ModeId.VIEWER,
            file_compare_enabled=True,
            file_compare_path=str(path),
        )
        primary_name = self.image_path.name if self.image_path is not None else "当前图片"
        self.status_var.set(f"双图对比：左侧 {primary_name}，右侧 {path.name}")
        self._refresh_preview()

    def _disable_file_comparison(
        self,
        *,
        announce: bool = True,
        refresh: bool = True,
        resume_animation: bool = True,
    ) -> None:
        self._comparison_load_generation += 1
        image = self._comparison_image
        self._comparison_image = None
        self._comparison_path = None
        self.file_compare_var.set(False)
        if hasattr(self, "viewer_file_compare_button"):
            self.viewer_file_compare_button.set_selected(False)
        self.mode_router.update_state(
            mode=ModeId.VIEWER,
            file_compare_enabled=False,
            file_compare_path=None,
        )
        if image is not None:
            self._retire_comparison_image(image)
        should_resume_animation = bool(
            resume_animation
            and self._animation_resume_after_file_compare
            and self._animation_sequence is not None
            and self.mode_router.active_mode is ModeId.VIEWER
        )
        self._animation_resume_after_file_compare = False
        if announce:
            self.status_var.set("已退出双图对比")
        if should_resume_animation:
            assert self._animation_sequence is not None
            self._animation_playing = True
            self._animation_index = (
                self._animation_displayed_index + 1
            ) % self._animation_sequence.frame_count
            self._update_animation_controls()
            self._show_animation_frame()
        elif refresh and self.original is not None:
            self._refresh_preview()

    def _retain_comparison_image(self, image: Image.Image) -> None:
        key = id(image)
        with self._comparison_ref_lock:
            self._comparison_render_refs[key] = self._comparison_render_refs.get(key, 0) + 1

    def _release_comparison_image(self, image: Image.Image) -> None:
        key = id(image)
        close_image: Image.Image | None = None
        with self._comparison_ref_lock:
            remaining = self._comparison_render_refs.get(key, 0) - 1
            if remaining > 0:
                self._comparison_render_refs[key] = remaining
            else:
                self._comparison_render_refs.pop(key, None)
                close_image = self._retired_comparison_images.pop(key, None)
        if close_image is not None:
            close_image.close()

    def _retire_comparison_image(self, image: Image.Image) -> None:
        key = id(image)
        with self._comparison_ref_lock:
            if self._comparison_render_refs.get(key, 0) > 0:
                self._retired_comparison_images[key] = image
                return
        image.close()

    def _close_comparison_images(self) -> None:
        images: dict[int, Image.Image] = {}
        if self._comparison_image is not None:
            images[id(self._comparison_image)] = self._comparison_image
        with self._comparison_ref_lock:
            images.update(self._retired_comparison_images)
            self._comparison_render_refs.clear()
            self._retired_comparison_images.clear()
        self._comparison_image = None
        self._comparison_path = None
        for image in images.values():
            image.close()

    @staticmethod
    def _anchored_pan_offset(
        *,
        anchor: tuple[float, float],
        old_offset: tuple[float, float],
        old_scale: float,
        new_scale: float,
        content_size: tuple[float, float],
        canvas_size: tuple[int, int],
    ) -> tuple[int, int]:
        """Return center-relative pan that keeps the anchor's source pixel fixed."""

        safe_old_scale = max(1e-9, old_scale)
        source_x = (anchor[0] - old_offset[0]) / safe_old_scale
        source_y = (anchor[1] - old_offset[1]) / safe_old_scale
        new_width = max(1, int(content_size[0] * new_scale))
        new_height = max(1, int(content_size[1] * new_scale))
        centered_x = (canvas_size[0] - new_width) / 2
        centered_y = (canvas_size[1] - new_height) / 2
        desired_x = anchor[0] - source_x * new_scale
        desired_y = anchor[1] - source_y * new_scale
        return (round(desired_x - centered_x), round(desired_y - centered_y))

    def _current_zoom_percent(self) -> int:
        if self.zoom_mode.get() == "适应":
            return max(MIN_ZOOM_PERCENT, round(self.preview_scale * 100))
        try:
            return int(self.zoom_mode.get().removesuffix("%"))
        except ValueError:
            return 100

    def _zoom_content_size(self) -> tuple[float, float] | None:
        if (
            self.base_preview_image is not None
            and not self.file_compare_var.get()
            and not self.split_compare_var.get()
        ):
            return (float(self.base_preview_image.width), float(self.base_preview_image.height))
        if self.preview is not None and self.preview_scale > 0:
            return (
                self.preview.width() / self.preview_scale,
                self.preview.height() / self.preview_scale,
            )
        return None

    def _refresh_after_zoom(self) -> None:
        if (
            self._animation_sequence is not None
            and self.mode_router.active_mode is ModeId.VIEWER
            and not self.file_compare_var.get()
        ):
            self._cancel_animation_timer()
            self._animation_index = self._animation_displayed_index
            self._show_animation_frame()
        else:
            self._refresh_preview()

    def _set_zoom_percent(
        self,
        percent: float,
        *,
        anchor: tuple[float, float] | None = None,
    ) -> None:
        bounded = max(MIN_ZOOM_PERCENT, min(MAX_ZOOM_PERCENT, round(percent)))
        canvas_size = (
            max(100, self.canvas.winfo_width()),
            max(100, self.canvas.winfo_height()),
        )
        focus = anchor or (canvas_size[0] / 2, canvas_size[1] / 2)
        content_size = self._zoom_content_size()
        if content_size is not None and self.preview is not None:
            focus = (
                max(
                    self.preview_offset[0],
                    min(self.preview_offset[0] + self.preview.width(), focus[0]),
                ),
                max(
                    self.preview_offset[1],
                    min(self.preview_offset[1] + self.preview.height(), focus[1]),
                ),
            )
            self.pan_offset = self._anchored_pan_offset(
                anchor=focus,
                old_offset=self.preview_offset,
                old_scale=self.preview_scale,
                new_scale=bounded / 100,
                content_size=content_size,
                canvas_size=canvas_size,
            )
        self.zoom_mode.set(f"{bounded}%")
        self.status_var.set(f"缩放：{bounded}%")
        self._refresh_after_zoom()

    def _zoom_with_wheel(self, event: tk.Event) -> str:
        direction = 1 if event.delta > 0 else -1
        steps = max(1, abs(event.delta) // 120)
        factor = (1 + self.viewer_zoom_step_percent / 100) ** steps
        current = self._current_zoom_percent()
        target = current * factor if direction > 0 else current / factor
        self._set_zoom_percent(target, anchor=(event.x, event.y))
        return "break"

    def step_zoom(self, direction: int) -> None:
        current = self._current_zoom_percent()
        factor = 1 + self.viewer_zoom_step_percent / 100
        target = current * factor if direction >= 0 else current / factor
        self._set_zoom_percent(target)

    def _begin_inline_zoom_edit(self) -> None:
        if self.mode_router.active_mode is not ModeId.VIEWER:
            return
        self._hide_zoom_preset_popup()
        self._viewer_zoom_editing = True
        self.viewer_zoom_input_var.set(str(self._current_zoom_percent()))
        self.dock_zoom_percent_button.grid_remove()
        self.dock_zoom_percent_entry.grid(row=0, column=5, padx=1, ipady=6)
        self.dock_zoom_percent_entry.focus_set()
        self.dock_zoom_percent_entry.selection_range(0, "end")

    def _commit_inline_zoom_edit(self, _event: tk.Event | None = None) -> str:
        if not self._viewer_zoom_editing:
            return "break"
        self._viewer_zoom_editing = False
        value = self.viewer_zoom_input_var.get().strip().removesuffix("%").strip()
        try:
            percent = float(value)
        except ValueError:
            self.status_var.set("请输入 10 到 800 之间的缩放比例")
        else:
            if MIN_ZOOM_PERCENT <= percent <= MAX_ZOOM_PERCENT:
                self._set_zoom_percent(percent)
            else:
                self.status_var.set("缩放比例范围为 10% 到 800%")
        self.dock_zoom_percent_entry.grid_remove()
        self.dock_zoom_percent_button.grid(row=0, column=5, padx=1)
        self.canvas.focus_set()
        return "break"

    def _cancel_inline_zoom_edit(self, _event: tk.Event | None = None) -> str:
        if not self._viewer_zoom_editing:
            return "break"
        self._viewer_zoom_editing = False
        self.dock_zoom_percent_entry.grid_remove()
        self.dock_zoom_percent_button.grid(row=0, column=5, padx=1)
        self.canvas.focus_set()
        return "break"

    def _zoom_changed(self) -> None:
        value = self.zoom_mode.get()
        if value == "适应":
            self.reset_view()
            return
        try:
            percent = int(value.removesuffix("%"))
        except ValueError:
            percent = 100
        self._set_zoom_percent(percent)

    def set_zoom(self, value: str) -> None:
        if value == "适应":
            self.reset_view()
            return
        try:
            percent = int(value.removesuffix("%"))
        except ValueError as error:
            raise ValueError(f"无效缩放值：{value}") from error
        self._set_zoom_percent(percent)

    def reset_view(self) -> None:
        self.zoom_mode.set("适应")
        self.pan_offset = (0, 0)
        self.status_var.set("图片已适合窗口")
        self._refresh_after_zoom()

    def _start_pan(self, event: tk.Event) -> None:
        if self.preview is None:
            return
        if self.mode_router.active_mode is ModeId.VIEWER and not any(self._viewer_pan_axes()):
            self.pan_start = None
            self.canvas.configure(cursor="arrow")
            return
        self.pan_start = (event.x, event.y)
        self.pan_start_offset = self.pan_offset
        self.canvas.configure(cursor="fleur")

    def _drag_pan(self, event: tk.Event) -> None:
        if self.pan_start is None:
            return
        dx = event.x - self.pan_start[0]
        dy = event.y - self.pan_start[1]
        if self.mode_router.active_mode is ModeId.VIEWER:
            allow_x, allow_y = self._viewer_pan_axes()
            dx = dx if allow_x else 0
            dy = dy if allow_y else 0
        self.pan_offset = (self.pan_start_offset[0] + dx, self.pan_start_offset[1] + dy)
        if self.mode_router.active_mode is ModeId.VIEWER:
            self._move_cached_viewer_preview()
        else:
            self._refresh_preview()

    def _move_cached_viewer_preview(self, *, redraw_navigation: bool = True) -> None:
        """Move the existing viewer frame immediately without a render round-trip."""

        if self.preview is None:
            return
        canvas_w = max(100, self.canvas.winfo_width())
        canvas_h = max(100, self.canvas.winfo_height())
        image_w = self.preview.width()
        image_h = self.preview.height()
        centered_x = (canvas_w - image_w) // 2
        centered_y = (canvas_h - image_h) // 2
        min_x, max_x = min(0, canvas_w - image_w), max(0, canvas_w - image_w)
        min_y, max_y = min(0, canvas_h - image_h), max(0, canvas_h - image_h)
        offset_x = max(min_x, min(max_x, centered_x + self.pan_offset[0]))
        offset_y = max(min_y, min(max_y, centered_y + self.pan_offset[1]))
        delta_x = offset_x - self.preview_offset[0]
        delta_y = offset_y - self.preview_offset[1]
        self.preview_offset = (offset_x, offset_y)
        self.pan_offset = (offset_x - centered_x, offset_y - centered_y)
        if delta_x or delta_y:
            self.canvas.move("all", delta_x, delta_y)
        if redraw_navigation:
            self._draw_viewer_navigation()

    def _viewer_pan_axes(self) -> tuple[bool, bool]:
        """Return the axes where the rendered image exceeds the visible canvas."""

        if self.preview is None:
            return (False, False)
        canvas_w = max(1, self.canvas.winfo_width())
        canvas_h = max(1, self.canvas.winfo_height())
        return (self.preview.width() > canvas_w, self.preview.height() > canvas_h)

    def _finish_pan(self, _event: tk.Event) -> None:
        self.pan_start = None
        if self.mode_router.active_mode is ModeId.VIEWER:
            self.canvas.configure(cursor="fleur" if any(self._viewer_pan_axes()) else "arrow")
        else:
            self._update_crop_cursor(_event)

    def _schedule_preview(self) -> None:
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(60, self._refresh_preview)

    def _refresh_preview(self) -> None:
        self._preview_job = None
        if self.original is None:
            return
        canvas_w = max(100, self.canvas.winfo_width())
        canvas_h = max(100, self.canvas.winfo_height())
        zoom_percent = (
            None if self.zoom_mode.get() == "适应" else int(self.zoom_mode.get().replace("%", ""))
        )
        self._preview_generation += 1
        generation = self._preview_generation
        conversion_recipe = (
            self.conversion_draft_history.current.to_recipe()
            if self.mode_router.active_mode is ModeId.CONVERT
            else None
        )
        timeline = None if conversion_recipe is not None else self._timeline_for_preview()
        if zoom_percent is None:
            render_size = (
                self._viewer_fit_render_size(canvas_w, canvas_h)
                if self.mode_router.active_mode is ModeId.VIEWER
                else (max(100, round(canvas_w * 0.68)), max(100, round(canvas_h * 0.72)))
            )
        else:
            render_size = (canvas_w, canvas_h)
        comparison_image = (
            self._comparison_image
            if self.file_compare_var.get()
            and self.mode_router.active_mode in {ModeId.VIEWER, ModeId.EDIT}
            else None
        )
        comparison_labels: tuple[str, str] | None = None
        if comparison_image is not None:
            primary_name = self.image_path.name if self.image_path is not None else "当前图片"
            secondary_name = (
                self._comparison_path.name if self._comparison_path is not None else "对比图片"
            )
            comparison_labels = (primary_name, secondary_name)
            self._retain_comparison_image(comparison_image)
        display_proxy = None
        if (
            comparison_image is None
            and self.mode_router.active_mode is ModeId.VIEWER
            and self.image_path is not None
            and zoom_percent is None
            and not self.compare_original.get()
            and not self.split_compare_var.get()
            and timeline is not None
            and timeline.cursor == 0
        ):
            display_proxy = self.image_loader.cached_preview(self.image_path)
        try:
            future = self.preview_renderer.render(
                self.original,
                self.edit_state(),
                compare_original=self.compare_original.get(),
                canvas_size=render_size,
                zoom_percent=zoom_percent,
                timeline=timeline,
                split_compare=self.split_compare_var.get(),
                comparison_image=comparison_image,
                comparison_labels=comparison_labels,
                display_proxy=display_proxy,
                conversion_recipe=conversion_recipe,
                allow_fit_upscale=self.mode_router.active_mode is ModeId.VIEWER,
            )
        except Exception:
            if comparison_image is not None:
                self._release_comparison_image(comparison_image)
            if display_proxy is not None:
                display_proxy.close()
            raise
        if comparison_image is not None:
            future.add_done_callback(
                lambda _future, image=comparison_image: self._release_comparison_image(image)
            )
        if display_proxy is not None:
            future.add_done_callback(lambda _future, image=display_proxy: image.close())
        self._schedule_preview_poll(generation, future, canvas_w, canvas_h)

    def _schedule_preview_poll(
        self,
        generation: int,
        future: Future[PreviewFrame],
        canvas_w: int,
        canvas_h: int,
    ) -> None:
        holder: list[str] = []

        def poll() -> None:
            if holder:
                self._preview_poll_jobs.discard(holder[0])
            self._poll_preview(generation, future, canvas_w, canvas_h)

        job = self.after(10, poll)
        holder.append(job)
        self._preview_poll_jobs.add(job)

    def _poll_preview(
        self,
        generation: int,
        future: Future[PreviewFrame],
        canvas_w: int,
        canvas_h: int,
    ) -> None:
        if generation != self._preview_generation:
            self._discard_preview_when_ready(future)
            return
        if not future.done():
            self._schedule_preview_poll(generation, future, canvas_w, canvas_h)
            return
        try:
            frame = future.result()
        except Exception as error:
            self.status_var.set(f"无法生成预览：{error}")
            return
        if generation != self._preview_generation:
            frame.close()
            return
        self._display_preview(frame, canvas_w, canvas_h)
        self.preview_ready_generation += 1

    @staticmethod
    def _discard_preview_when_ready(future: Future[PreviewFrame]) -> None:
        def discard(completed: Future[PreviewFrame]) -> None:
            if completed.cancelled() or completed.exception() is not None:
                return
            completed.result().close()

        if future.done():
            discard(future)
        else:
            future.add_done_callback(discard)

    def _release_base_preview(self) -> None:
        if self._base_preview_owned and self.base_preview_image is not None:
            self.base_preview_image.close()
        self.base_preview_image = None
        self._base_preview_owned = False

    def _display_preview(self, frame: PreviewFrame, canvas_w: int, canvas_h: int) -> None:
        self._release_base_preview()
        self.base_preview_image = frame.working
        self._base_preview_owned = frame.owns_working
        image = frame.image
        previous_preview = self.preview
        next_preview = ImageTk.PhotoImage(image, master=self.canvas)
        self.preview_scale = frame.scale
        centered_x = (canvas_w - image.width) // 2
        centered_y = (canvas_h - image.height) // 2
        min_x, max_x = min(0, canvas_w - image.width), max(0, canvas_w - image.width)
        min_y, max_y = min(0, canvas_h - image.height), max(0, canvas_h - image.height)
        offset_x = max(min_x, min(max_x, centered_x + self.pan_offset[0]))
        offset_y = max(min_y, min(max_y, centered_y + self.pan_offset[1]))
        self.preview_offset = (offset_x, offset_y)
        self.pan_offset = (offset_x - centered_x, offset_y - centered_y)
        displayed_percent = (
            round(frame.scale * 100)
            if self.zoom_mode.get() == "适应"
            else self._current_zoom_percent()
        )
        zoom_label = f"{displayed_percent}%"
        self.dock_zoom_percent_button.set_text(zoom_label)
        set_accessible_name(
            self.dock_zoom_percent_button,
            f"当前缩放 {zoom_label}，点击输入任意比例",
        )
        self.canvas_metadata.configure(text=f"P3 色域 · 8 位    {zoom_label}")

        tokens = self.theme_manager.tokens
        self.canvas.delete("all")
        shadow_color = (
            "#000000"
            if self.mode_router.active_mode is ModeId.VIEWER
            else ("#c8c1cc" if tokens.name == "light" else "#09080c")
        )
        if self.mode_router.active_mode is not ModeId.VIEWER:
            grid_color = "#dbd7dd" if tokens.name == "light" else "#211f26"
            for x in range(0, canvas_w, 28):
                self.canvas.create_line(
                    x,
                    0,
                    x,
                    canvas_h,
                    fill=grid_color,
                    tags="canvas-grid",
                )
            for y in range(0, canvas_h, 28):
                self.canvas.create_line(
                    0,
                    y,
                    canvas_w,
                    y,
                    fill=grid_color,
                    tags="canvas-grid",
                )
        shadow_x = self.preview_offset[0] + 12
        shadow_y = self.preview_offset[1] + 16
        self.canvas.create_rectangle(
            shadow_x,
            shadow_y,
            shadow_x + image.width,
            shadow_y + image.height,
            fill=shadow_color,
            outline="",
            tags="image-shadow",
        )
        self.canvas.create_image(
            self.preview_offset[0], self.preview_offset[1], image=next_preview, anchor="nw"
        )
        # Publish the replacement only after its Canvas item exists. Retaining
        # the old Tk image for one display frame prevents Tcl from blanking the
        # existing item while the new one is being attached.
        self.preview = next_preview
        if previous_preview is not None:
            self.after(34, lambda retained=previous_preview: None)
        if frame.split_layout is not None:
            layout = frame.split_layout
            left_label, right_label = frame.split_labels or ("编辑前", "编辑后")
            divider_x = self.preview_offset[0] + layout.divider_x
            self.canvas.create_line(
                divider_x,
                self.preview_offset[1],
                divider_x,
                self.preview_offset[1] + image.height,
                fill=tokens.accent,
                width=2,
            )
            self.canvas.create_text(
                self.preview_offset[0] + layout.before_panel[0] + 8,
                self.preview_offset[1] + 8,
                text=left_label,
                fill=tokens.accent,
                anchor="nw",
            )
            self.canvas.create_text(
                self.preview_offset[0] + layout.after_panel[2] - 8,
                self.preview_offset[1] + 8,
                text=right_label,
                fill=tokens.accent_hover,
                anchor="ne",
            )
        self._draw_checker_bounds(image.width, image.height)
        if frame.split_layout is None:
            self._draw_smart_selection_mask()
            self._draw_crop()
            self._draw_smart_mask_strokes()
            self._draw_annotation_overlay()
        self._draw_histogram(frame.working)
        if self.edit_panel_visible_var.get() and self.mode_router.active_mode in {
            ModeId.CONVERT,
            ModeId.EDIT,
        }:
            self.after_idle(self._update_estimate)
        self._update_viewer_navigation_buttons()
        frame.image.close()

    def _draw_checker_bounds(self, width: int, height: int) -> None:
        ox, oy = self.preview_offset
        self.canvas.create_rectangle(
            ox - 1,
            oy - 1,
            ox + width + 1,
            oy + height + 1,
            outline=self.theme_manager.tokens.border,
        )

    def _canvas_to_image(self, x: int, y: int) -> tuple[int, int]:
        ox, oy = self.preview_offset
        ix = round((x - ox) / self.preview_scale)
        iy = round((y - oy) / self.preview_scale)
        image = self.base_preview_image
        if image is None:
            return (0, 0)
        return (max(0, min(ix, image.width)), max(0, min(iy, image.height)))

    def _image_to_canvas(self, x: int, y: int) -> tuple[int, int]:
        ox, oy = self.preview_offset
        return (round(x * self.preview_scale + ox), round(y * self.preview_scale + oy))

    def _set_annotation_tool(self, tool: str) -> None:
        if tool not in ANNOTATION_TOOL_LABELS:
            return
        if self._annotation_start is not None:
            self._clear_annotation_drag(redraw=False)
            self._refresh_preview()
        self.annotation_tool_var.set(tool)
        for name, button in getattr(self, "annotation_tool_buttons", {}).items():
            button.configure(
                style=("ToolShelfActive.TButton" if name == tool else "ToolShelf.TButton")
            )
        if hasattr(self, "dock_crop_button"):
            self.dock_crop_button.set_selected(tool == "crop")
        if hasattr(self, "canvas_select_button"):
            self.canvas_select_button.set_selected(tool == "select")
        self.current_tool_label_var.set(f"当前：{ANNOTATION_TOOL_LABELS[tool]}")
        if tool != "select":
            self.selected_annotation_id = None
            self.selected_annotation_var.set("未选择标注")
        if tool == "perspective" and self.document is not None:
            existing = reduce_timeline(self.document.timeline).geometry.perspective
            if existing is not None:
                self.perspective_points = [
                    (existing[index], existing[index + 1]) for index in range(0, 8, 2)
                ]
        self.canvas.configure(cursor="arrow" if tool == "select" else "crosshair")
        self.status_var.set(f"当前工具：{ANNOTATION_TOOL_LABELS[tool]}")
        self._draw_annotation_overlay()
        self._update_crop_action_bar()
        self._save_config()

    def _canvas_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        if self._side_by_side_comparison_active():
            self.status_var.set("请先退出并排对比，再进行裁剪或标注")
            return
        if self.mode_router.active_mode is ModeId.VIEWER:
            self._update_viewer_toolbar_auto_visibility(event.y)
            if self._viewer_markup_active:
                self._start_annotation(event, self._viewer_markup_tool)
                return
            navigation_direction = self._viewer_navigation_direction_at(event.x, event.y)
            if navigation_direction is not None:
                self._viewer_navigation_pressed = navigation_direction
                return
            self._start_pan(event)
            return
        if self._smart_mask_brush_active:
            self._start_smart_mask_stroke(event)
            return
        if self._smart_alpha_brush_active:
            self._start_smart_alpha_stroke(event)
            return
        tool = self.annotation_tool_var.get()
        if tool == "crop":
            self._start_crop(event)
        elif tool == "select":
            self._start_annotation_selection(event)
        elif tool == "perspective":
            self._start_perspective(event)
        else:
            self._start_annotation(event, tool)

    def _canvas_drag(self, event: tk.Event) -> None:
        if self._side_by_side_comparison_active():
            return
        if self.mode_router.active_mode is ModeId.VIEWER:
            if self._viewer_markup_active:
                self._drag_annotation(event, self._viewer_markup_tool)
                return
            if self._viewer_navigation_pressed is not None:
                return
            self._drag_pan(event)
            return
        if self._smart_mask_brush_active:
            self._drag_smart_mask_stroke(event)
            return
        if self._smart_alpha_brush_active:
            self._drag_smart_alpha_stroke(event)
            return
        tool = self.annotation_tool_var.get()
        if tool == "crop":
            self._drag_crop(event)
        elif tool == "select":
            self._drag_annotation_selection(event)
        elif tool == "perspective":
            self._drag_perspective(event)
        else:
            self._drag_annotation(event, tool)

    def _canvas_release(self, event: tk.Event) -> None:
        if self._side_by_side_comparison_active():
            return
        if self.mode_router.active_mode is ModeId.VIEWER:
            if self._viewer_markup_active:
                self._finish_annotation(event, self._viewer_markup_tool)
                return
            navigation_direction = self._viewer_navigation_direction_at(event.x, event.y)
            pressed_direction = self._viewer_navigation_pressed
            self._viewer_navigation_pressed = None
            if pressed_direction is not None:
                if navigation_direction == pressed_direction:
                    self.navigate_folder(pressed_direction)
                return
            self._finish_pan(event)
            return
        if self._smart_mask_brush_active:
            self._finish_smart_mask_stroke(event)
            return
        if self._smart_alpha_brush_active:
            self._finish_smart_alpha_stroke(event)
            return
        tool = self.annotation_tool_var.get()
        if tool == "crop":
            self._finish_crop(event)
        elif tool == "select":
            self._finish_annotation_selection(event)
        elif tool == "perspective":
            self._perspective_active_index = None
        else:
            self._finish_annotation(event, tool)

    def _canvas_motion(self, event: tk.Event) -> None:
        if self._side_by_side_comparison_active():
            self.canvas.configure(cursor="arrow")
            return
        if self.mode_router.active_mode is ModeId.VIEWER:
            self._update_viewer_toolbar_auto_visibility(event.y)
            if self._viewer_markup_active:
                self._set_viewer_navigation_hover(None)
                self.canvas.configure(cursor="crosshair")
                return
            navigation_direction = self._viewer_navigation_direction_at(event.x, event.y)
            self._set_viewer_navigation_hover(navigation_direction)
            self.canvas.configure(
                cursor=(
                    "hand2"
                    if navigation_direction is not None
                    else "fleur"
                    if any(self._viewer_pan_axes())
                    else "arrow"
                )
            )
            return
        if self._smart_mask_brush_active:
            self.canvas.configure(cursor="crosshair")
            return
        if self._smart_alpha_brush_active:
            self.canvas.configure(cursor="crosshair")
            return
        tool = self.annotation_tool_var.get()
        if tool == "crop":
            self._update_crop_cursor(event)
        elif tool == "select":
            handle = self._annotation_handle_at(event.x, event.y)
            if handle is not None:
                self.canvas.configure(cursor="sizing")
            elif self._annotation_at_canvas(event.x, event.y) is not None:
                self.canvas.configure(cursor="fleur")
            else:
                self.canvas.configure(cursor="arrow")
        else:
            self.canvas.configure(cursor="crosshair")

    def _canvas_leave(self, _event: tk.Event) -> None:
        if self.mode_router.active_mode is not ModeId.VIEWER:
            return
        self._set_viewer_navigation_hover(None)

    def _canvas_to_normalized(self, x: int, y: int) -> tuple[float, float]:
        image = self.base_preview_image
        if image is None:
            return (0.0, 0.0)
        ix, iy = self._canvas_to_image(x, y)
        return (ix / max(1, image.width), iy / max(1, image.height))

    def _normalized_to_canvas(self, x: float, y: float) -> tuple[int, int]:
        image = self.base_preview_image
        if image is None:
            return self.preview_offset
        return self._image_to_canvas(round(x * image.width), round(y * image.height))

    def _annotation_objects(self) -> tuple[AnnotationObject, ...]:
        if self.document is None:
            return ()
        return reduce_timeline(self.document.timeline).annotations

    def _selected_annotation(self) -> AnnotationObject | None:
        return next(
            (
                annotation
                for annotation in self._annotation_objects()
                if annotation.id == self.selected_annotation_id
            ),
            None,
        )

    def _hit_radius(self) -> int:
        try:
            dpi = float(self.winfo_fpixels("1i"))
        except tk.TclError:
            dpi = 96.0
        return hit_radius_px(dpi)

    def _annotation_at_canvas(self, x: int, y: int) -> AnnotationObject | None:
        image = self.base_preview_image
        if image is None:
            return None
        tolerance = self._hit_radius() / max(1, min(image.size) * self.preview_scale)
        return object_at(self._annotation_objects(), self._canvas_to_normalized(x, y), tolerance)

    def _annotation_handle_at(self, x: int, y: int) -> str | None:
        selected = self._selected_annotation()
        if selected is None:
            return None
        left, top, right, bottom = selected.bounds
        handles = {
            "nw": self._normalized_to_canvas(left, top),
            "ne": self._normalized_to_canvas(right, top),
            "sw": self._normalized_to_canvas(left, bottom),
            "se": self._normalized_to_canvas(right, bottom),
        }
        radius = self._hit_radius()
        return next(
            (
                name
                for name, (hx, hy) in handles.items()
                if abs(hx - x) <= radius and abs(hy - y) <= radius
            ),
            None,
        )

    def _start_annotation_selection(self, event: tk.Event) -> None:
        self._cancel_annotation_drag_preview()
        point = self._canvas_to_normalized(event.x, event.y)
        handle = self._annotation_handle_at(event.x, event.y)
        selected = self._selected_annotation()
        if handle is not None and selected is not None:
            self._annotation_drag_action = f"resize:{handle}"
            target = selected
        else:
            target = self._annotation_at_canvas(event.x, event.y)
            self._annotation_drag_action = "move"
        self._annotation_start = point
        self._annotation_original_object = target
        self._annotation_drag_object = target
        self.selected_annotation_id = target.id if target else None
        if target is not None:
            self._load_annotation_style(target)
        else:
            self.selected_annotation_var.set("未选择标注")
        self._draw_annotation_overlay()

    def _drag_annotation_selection(self, event: tk.Event) -> None:
        original = self._annotation_original_object
        start = self._annotation_start
        if original is None or start is None:
            return
        current = self._canvas_to_normalized(event.x, event.y)
        if self._annotation_drag_action == "move":
            draft = original.translated(current[0] - start[0], current[1] - start[1])
        else:
            left, top, right, bottom = original.bounds
            handle = self._annotation_drag_action.split(":", 1)[1]
            if "n" in handle:
                top = current[1]
            if "s" in handle:
                bottom = current[1]
            if "w" in handle:
                left = current[0]
            if "e" in handle:
                right = current[0]
            if abs(right - left) < 0.002 or abs(bottom - top) < 0.002:
                return
            draft = original.resized((left, top, right, bottom))
        self._annotation_drag_object = draft
        self._draw_annotation_overlay()
        self._schedule_annotation_drag_preview()

    def _finish_annotation_selection(self, _event: tk.Event) -> None:
        original = self._annotation_original_object
        draft = self._annotation_drag_object
        command: EditCommand | None = None
        if original is not None and draft is not None and draft != original:
            patch = draft.to_json()
            patch.pop("id", None)
            patch.pop("kind", None)
            command = EditCommand.create(
                "annotation_update",
                {"id": original.id, "patch": patch},
                f"调整{ANNOTATION_TOOL_LABELS[original.kind]}",
            )
        self._clear_annotation_drag(redraw=False)
        if command is not None:
            self._commit_command(command)
        else:
            # A transient frame can contain an intermediate position even when
            # the pointer returns to its starting point before release.
            self._refresh_preview()
        self._draw_annotation_overlay()

    def _start_annotation(self, event: tk.Event, tool: str) -> None:
        if self.document is None:
            return
        self._cancel_annotation_drag_preview()
        point = self._canvas_to_normalized(event.x, event.y)
        self._annotation_start = point
        self._annotation_freehand_points = [point]
        try:
            self._annotation_drag_object = self._annotation_from_drag(tool, point, point)
        except ValueError as error:
            self._annotation_start = None
            self.status_var.set(str(error))
            return
        self._draw_annotation_overlay()

    def _drag_annotation(self, event: tk.Event, tool: str) -> None:
        if self._annotation_start is None:
            return
        point = self._canvas_to_normalized(event.x, event.y)
        if tool == "freehand":
            previous = self._annotation_freehand_points[-1]
            if abs(point[0] - previous[0]) + abs(point[1] - previous[1]) >= 0.001:
                self._annotation_freehand_points.append(point)
        try:
            self._annotation_drag_object = self._annotation_from_drag(
                tool, self._annotation_start, point
            )
        except ValueError as error:
            self.status_var.set(str(error))
            return
        self._draw_annotation_overlay()
        self._schedule_annotation_drag_preview()

    def _finish_annotation(self, event: tk.Event, tool: str) -> None:
        if self.document is None or self._annotation_start is None:
            return
        point = self._canvas_to_normalized(event.x, event.y)
        if abs(point[0] - self._annotation_start[0]) < 0.003:
            point = (min(1.0, point[0] + 0.08), point[1])
        if abs(point[1] - self._annotation_start[1]) < 0.003:
            point = (point[0], min(1.0, point[1] + 0.08))
        if tool == "freehand" and len(self._annotation_freehand_points) < 2:
            self._annotation_freehand_points.append(point)
        try:
            annotation = self._annotation_from_drag(tool, self._annotation_start, point)
        except ValueError as error:
            messagebox.showerror(APP_NAME, str(error))
            self._clear_annotation_drag(redraw=False)
            self._refresh_preview()
            return
        if tool == "text":
            text = simpledialog.askstring(
                APP_NAME,
                "输入标注文字：",
                initialvalue=self.annotation_text_var.get(),
                parent=self,
            )
            if not text:
                self._clear_annotation_drag(redraw=False)
                self._refresh_preview()
                return
            self.annotation_text_var.set(text)
            annotation = annotation.with_patch({"text": text})
        self._commit_command(
            EditCommand.create(
                "annotation_add",
                {"object": annotation.to_json()},
                f"添加{ANNOTATION_TOOL_LABELS[tool]}",
            ),
            refresh=False,
        )
        if self.mode_router.active_mode is ModeId.VIEWER and self._viewer_markup_active:
            self.selected_annotation_id = None
            # Keep the final Canvas guide visible while the committed bitmap is
            # rendered in the background. ``_display_preview`` replaces the
            # guide and bitmap together, avoiding a release-time blink.
            self._clear_annotation_drag(redraw=False)
            self._refresh_preview()
            self.status_var.set(f"已添加{ANNOTATION_TOOL_LABELS[tool]} · 可继续绘制、撤销或另存")
            return
        self.selected_annotation_id = annotation.id
        self._clear_annotation_drag(redraw=False)
        self._set_annotation_tool("select")
        self.selected_annotation_id = annotation.id
        self._load_annotation_style(annotation)
        self._draw_annotation_overlay()
        self._refresh_preview()

    def _annotation_from_drag(
        self,
        tool: str,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> AnnotationObject:
        fill = self.annotation_fill_var.get().strip() or None
        if tool == "redact" and fill is None:
            fill = "#000000"
        number = 1 + max(
            (
                annotation.number
                for annotation in self._annotation_objects()
                if annotation.kind == "number"
            ),
            default=0,
        )
        return AnnotationObject.create(
            tool,
            (*start, *end),
            points=(
                [[x, y] for x, y in self._annotation_freehand_points] if tool == "freehand" else []
            ),
            text=self.annotation_text_var.get(),
            color=self.annotation_color_var.get(),
            fill_color=fill,
            stroke_width=self.annotation_stroke_var.get(),
            opacity=self.annotation_opacity_var.get(),
            font_size=self.annotation_font_size_var.get(),
            number=number,
            mosaic_size=self.annotation_effect_size_var.get(),
            blur_radius=self.annotation_effect_size_var.get(),
        )

    def _clear_annotation_drag(self, *, redraw: bool = True) -> None:
        self._cancel_annotation_drag_preview()
        self._annotation_start = None
        self._annotation_original_object = None
        self._annotation_drag_object = None
        self._annotation_freehand_points = []
        if redraw:
            self._draw_annotation_overlay()

    def _schedule_annotation_drag_preview(self) -> None:
        """Throttle exact drag previews while the Canvas guide remains immediate."""

        if self.mode_router.active_mode is ModeId.VIEWER and self._viewer_markup_active:
            # Viewer markup uses the lightweight Canvas guide while dragging.
            # Re-rendering the full source image every 40 ms made large images
            # feel sticky; the exact high-quality composite is rendered once
            # on pointer release instead.
            return
        if self._annotation_preview_job is not None or self._services_shutdown:
            return
        self._annotation_preview_job = self.after(40, self._render_annotation_drag_preview)

    def _render_annotation_drag_preview(self) -> None:
        self._annotation_preview_job = None
        if (
            self.document is not None
            and self._annotation_start is not None
            and self._annotation_drag_object is not None
        ):
            self._refresh_preview()

    def _cancel_annotation_drag_preview(self) -> None:
        if self._annotation_preview_job is None:
            return
        try:
            self.after_cancel(self._annotation_preview_job)
        except tk.TclError:
            pass
        self._annotation_preview_job = None

    def _timeline_for_preview(self) -> EditTimeline | None:
        """Return a snapshot containing the current drag draft without committing it."""

        if self.document is None:
            return None
        timeline = self.document.timeline
        draft = self._annotation_drag_object
        if self._annotation_start is None or draft is None:
            return timeline
        original = self._annotation_original_object
        if original is not None and draft == original:
            return timeline
        return _timeline_with_annotation_draft(timeline, draft, original)

    def _load_annotation_style(self, annotation: AnnotationObject) -> None:
        self.selected_annotation_var.set(
            f"已选择：{ANNOTATION_TOOL_LABELS[annotation.kind]}  {annotation.id[:8]}"
        )
        self.annotation_text_var.set(annotation.text)
        self.annotation_color_var.set(annotation.color)
        self.annotation_fill_var.set(annotation.fill_color or "")
        self.annotation_stroke_var.set(annotation.stroke_width)
        self.annotation_opacity_var.set(annotation.opacity)
        self.annotation_font_size_var.set(annotation.font_size)
        self.annotation_effect_size_var.set(
            annotation.mosaic_size if annotation.kind == "mosaic" else annotation.blur_radius
        )

    def apply_annotation_style(self) -> None:
        annotation = self._selected_annotation()
        if annotation is None:
            self.status_var.set("请先使用“选择”工具选中标注对象")
            return
        patch = {
            "text": self.annotation_text_var.get(),
            "color": self.annotation_color_var.get(),
            "fill_color": self.annotation_fill_var.get().strip() or None,
            "stroke_width": self.annotation_stroke_var.get(),
            "opacity": self.annotation_opacity_var.get(),
            "font_size": self.annotation_font_size_var.get(),
            "mosaic_size": self.annotation_effect_size_var.get(),
            "blur_radius": self.annotation_effect_size_var.get(),
        }
        try:
            updated = annotation.with_patch(patch)
        except ValueError as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        self._commit_command(
            EditCommand.create(
                "annotation_update",
                {"id": annotation.id, "patch": patch},
                f"修改{ANNOTATION_TOOL_LABELS[annotation.kind]}样式",
            )
        )
        self._load_annotation_style(updated)

    def delete_selected_annotation(self) -> None:
        annotation = self._selected_annotation()
        if annotation is None:
            return
        self._commit_command(
            EditCommand.create(
                "annotation_delete",
                {"id": annotation.id},
                f"删除{ANNOTATION_TOOL_LABELS[annotation.kind]}",
            )
        )
        self.selected_annotation_id = None
        self.selected_annotation_var.set("未选择标注")

    def delete_selected_or_file(self) -> None:
        if self.selected_annotation_id is not None and self.annotation_tool_var.get() == "select":
            self.delete_selected_annotation()
        else:
            self.delete_current_to_recycle_bin()

    def _nudge_selected(self, dx_pixels: int, dy_pixels: int) -> str:
        annotation = self._selected_annotation()
        image = self.base_preview_image
        if annotation is None or image is None:
            return "break"
        moved = annotation.translated(dx_pixels / image.width, dy_pixels / image.height)
        patch = moved.to_json()
        patch.pop("id", None)
        patch.pop("kind", None)
        self._commit_command(
            EditCommand.create(
                "annotation_update",
                {"id": annotation.id, "patch": patch},
                f"微调{ANNOTATION_TOOL_LABELS[annotation.kind]}位置",
            )
        )
        return "break"

    def _choose_annotation_color(self, target: str) -> None:
        current = (
            self.annotation_color_var.get()
            if target == "stroke"
            else (self.annotation_fill_var.get() or "#ffffff")
        )
        _rgb, color = colorchooser.askcolor(current, parent=self, title="选择标注颜色")
        if color:
            if target == "stroke":
                self.annotation_color_var.set(color)
            else:
                self.annotation_fill_var.set(color)

    def _start_perspective(self, event: tk.Event) -> None:
        radius = self._hit_radius()
        canvas_points = [self._normalized_to_canvas(x, y) for x, y in self.perspective_points]
        self._perspective_active_index = min(
            range(4),
            key=lambda index: (
                abs(canvas_points[index][0] - event.x) + abs(canvas_points[index][1] - event.y)
            ),
        )
        px, py = canvas_points[self._perspective_active_index]
        if abs(px - event.x) > radius * 2 or abs(py - event.y) > radius * 2:
            self._perspective_active_index = None

    def _drag_perspective(self, event: tk.Event) -> None:
        if self._perspective_active_index is None:
            return
        self.perspective_points[self._perspective_active_index] = self._canvas_to_normalized(
            event.x, event.y
        )
        self._draw_annotation_overlay()

    def apply_perspective(self) -> None:
        if self.document is None:
            return
        quad = [value for point in self.perspective_points for value in point]
        if not self._commit_command(EditCommand.create("perspective", {"quad": quad}, "透视校正")):
            return
        self._set_annotation_tool("select")

    def _choose_canvas_background(self) -> None:
        _rgb, color = colorchooser.askcolor(
            self.canvas_background_var.get(), parent=self, title="选择画布背景色"
        )
        if color:
            self.canvas_background_var.set(color)

    def apply_canvas_expansion(self) -> None:
        if self.document is None:
            return
        try:
            command = EditCommand.create(
                "canvas_expand",
                {
                    "margins": [
                        self.canvas_left_var.get(),
                        self.canvas_top_var.get(),
                        self.canvas_right_var.get(),
                        self.canvas_bottom_var.get(),
                    ],
                    "background": self.canvas_background_var.get(),
                },
                "扩展画布",
            )
            self._commit_command(command)
        except (tk.TclError, ValueError) as error:
            messagebox.showerror(APP_NAME, str(error))

    def _draw_annotation_overlay(self) -> None:
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("annotation-ui")
        if self.annotation_tool_var.get() == "perspective" and self.base_preview_image is not None:
            points = [self._normalized_to_canvas(x, y) for x, y in self.perspective_points]
            self.canvas.create_polygon(
                *[coordinate for point in points for coordinate in point],
                outline=self.theme_manager.tokens.accent,
                fill="",
                width=2,
                tags="annotation-ui",
            )
            radius = self._hit_radius()
            for index, (x, y) in enumerate(points, start=1):
                self.canvas.create_oval(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill=self.theme_manager.tokens.accent,
                    outline=self.theme_manager.tokens.foreground,
                    tags="annotation-ui",
                )
                self.canvas.create_text(x, y, text=str(index), fill="#111827", tags="annotation-ui")
            return
        annotation = self._annotation_drag_object or self._selected_annotation()
        if annotation is None or self.base_preview_image is None:
            return
        left, top, right, bottom = annotation.bounds
        x1, y1 = self._normalized_to_canvas(left, top)
        x2, y2 = self._normalized_to_canvas(right, bottom)
        color = annotation.color
        stroke = max(
            3 if self._viewer_markup_active else 2,
            round(
                annotation.stroke_width
                * min(self.base_preview_image.size)
                * self.preview_scale
                / 1000
            ),
        )
        if annotation.kind in {"line", "arrow"}:
            start = self._normalized_to_canvas(annotation.x1, annotation.y1)
            end = self._normalized_to_canvas(annotation.x2, annotation.y2)
            arrow_size = max(8, stroke * 4)
            self.canvas.create_line(
                *start,
                *end,
                fill=color,
                width=stroke,
                arrow=tk.LAST if annotation.kind == "arrow" else tk.NONE,
                arrowshape=(arrow_size, arrow_size, max(3, arrow_size // 2)),
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                tags="annotation-ui",
            )
        elif annotation.kind == "ellipse":
            self.canvas.create_oval(
                x1, y1, x2, y2, outline=color, width=stroke, tags="annotation-ui"
            )
        elif annotation.kind == "freehand" and annotation.points:
            points = [self._normalized_to_canvas(x, y) for x, y in annotation.points]
            if len(points) >= 2:
                self.canvas.create_line(
                    *[coordinate for point in points for coordinate in point],
                    fill=color,
                    width=stroke,
                    tags="annotation-ui",
                )
            else:
                x, y = points[0]
                self.canvas.create_oval(
                    x - 1,
                    y - 1,
                    x + 1,
                    y + 1,
                    fill=color,
                    outline=color,
                    tags="annotation-ui",
                )
        else:
            options: dict[str, object] = {
                "outline": color,
                "width": stroke,
                "tags": "annotation-ui",
            }
            if not self._viewer_markup_active:
                options["dash"] = (4, 3)
            self.canvas.create_rectangle(x1, y1, x2, y2, **options)
        if self.selected_annotation_id == annotation.id:
            radius = self._hit_radius()
            for x, y in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
                self.canvas.create_rectangle(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill=self.theme_manager.tokens.surface,
                    outline=self.theme_manager.tokens.accent,
                    tags="annotation-ui",
                )

    def _start_crop(self, event: tk.Event) -> None:
        if self.base_preview_image is None:
            return
        self.drag_start = self._canvas_to_image(event.x, event.y)
        self.crop_drag_start_box = self.crop_box
        self._smart_text_adjusting_region = (
            self._smart_text_region_box is not None
            and self.crop_drag_start_box == self._smart_text_region_box
        )
        perspective_index = self._hit_test_smart_text_perspective(event.x, event.y)
        if self._smart_text_adjusting_region and perspective_index is not None:
            self.crop_action = f"perspective:{perspective_index}"
            return
        self.crop_action = self._hit_test_crop(event.x, event.y)

    def _drag_crop(self, event: tk.Event) -> None:
        if self.base_preview_image is None or self.drag_start is None:
            return
        end = self._canvas_to_image(event.x, event.y)
        if self.crop_action.startswith("perspective:"):
            index = int(self.crop_action.partition(":")[2])
            self._set_smart_text_perspective_corner(index, end)
        elif self.crop_action == "new" or self.crop_drag_start_box is None:
            left, right = sorted((self.drag_start[0], end[0]))
            top, bottom = sorted((self.drag_start[1], end[1]))
            self.crop_box = (left, top, right, bottom)
            self.crop_box = self._constrain_box_to_ratio(self.crop_box, anchor=self.drag_start)
        elif self.crop_action == "move":
            self.crop_box = self._move_crop_box(
                self.crop_drag_start_box, end[0] - self.drag_start[0], end[1] - self.drag_start[1]
            )
        else:
            self.crop_box = self._resize_crop_box(self.crop_drag_start_box, self.crop_action, end)
        if self._smart_text_adjusting_region and not self.crop_action.startswith("perspective:"):
            self._smart_text_region_box = self.crop_box
            self._smart_text_perspective_quad = None
            self._update_smart_text_region_label()
        if self.document is not None:
            self.document.update_state("crop_drag", crop_box=self.crop_box)
        self._draw_crop()
        self._update_crop_label()

    def _finish_crop(self, _event: tk.Event) -> None:
        if self.crop_box is None:
            return
        left, top, right, bottom = self.crop_box
        if right - left < 4 or bottom - top < 4:
            self.crop_box = None
            if self.document is not None:
                self.document.update_state("crop_cancel", crop_box=None)
            self.crop_var.set("未设置裁剪")
            if self._smart_text_selecting_region:
                self._smart_text_region_box = None
                self._smart_text_perspective_quad = None
                self._update_smart_text_region_label()
                self.smart_edit_status_var.set("文字区域过小，请重新框选")
            if self._smart_repair_selecting_region:
                self._smart_repair_region_box = None
                self._update_smart_repair_region_label()
                self.smart_edit_status_var.set("擦除区域过小，请重新框选")
        else:
            self._update_crop_label()
            if self._smart_repair_selecting_region:
                if self.crop_box != self._smart_selection_mask_box:
                    self._smart_selection_suggestions = ()
                    self._smart_selection_index = -1
                    self._clear_smart_selection_mask()
                self._smart_repair_region_box = self.crop_box
                self._smart_repair_selecting_region = False
                self._update_smart_repair_region_label()
                self._set_annotation_tool("select")
                self.smart_edit_status_var.set(
                    "主体像素蒙版已记录，可调整羽化后执行修复"
                    if self._smart_selection_mask_box == self.crop_box
                    else "矩形擦除区域已记录，可调整羽化后执行修复"
                )
            elif self._smart_text_selecting_region:
                self._smart_text_region_box = self.crop_box
                self._smart_text_perspective_quad = None
                self._smart_text_selecting_region = False
                self._update_smart_text_region_label()
                self._set_annotation_tool("select")
                self.smart_edit_status_var.set("文字区域已记录，可调整文字样式后执行替换")
            elif self._smart_text_adjusting_region:
                self._smart_text_region_box = self.crop_box
                self._update_smart_text_region_label()
                self.smart_edit_status_var.set(
                    "透视角点校正已同步，可继续识别或替换"
                    if self.crop_action.startswith("perspective:")
                    else "文字区域校正已同步，可继续识别或替换"
                )
        self.drag_start = None
        self.crop_drag_start_box = None
        self.crop_action = "new"
        self._smart_text_adjusting_region = False
        self._update_smart_edit_buttons()
        self._draw_crop()
        self._update_estimate()

    def _update_crop_label(self) -> None:
        if not self.crop_box:
            return
        left, top, right, bottom = self.crop_box
        ratio_text = (
            f"  比例 {self.crop_ratio_var.get()}" if self.crop_ratio_var.get() != "自由" else ""
        )
        self.crop_var.set(
            f"裁剪区域：{right - left} x {bottom - top}  位置 {left},{top}{ratio_text}"
        )

    def _draw_crop(self) -> None:
        self.canvas.delete("crop")
        self._update_crop_action_bar()
        if self.crop_box is None:
            return
        tokens = self.theme_manager.tokens
        perspective_active = (
            self._smart_text_perspective_quad is not None
            and self._smart_text_region_box == self.crop_box
        )
        left, top = self._image_to_canvas(self.crop_box[0], self.crop_box[1])
        right, bottom = self._image_to_canvas(self.crop_box[2], self.crop_box[3])
        self.canvas.create_rectangle(
            left,
            top,
            right,
            bottom,
            outline=tokens.border if perspective_active else tokens.accent,
            width=1 if perspective_active else 2,
            dash=(4, 4) if perspective_active else (),
            tags="crop",
        )
        if not perspective_active:
            self.canvas.create_rectangle(
                left + 1,
                top + 1,
                right - 1,
                bottom - 1,
                outline=tokens.on_accent,
                width=1,
                tags="crop",
            )
            for x in (left + (right - left) / 3, left + (right - left) * 2 / 3):
                self.canvas.create_line(
                    x, top, x, bottom, fill=tokens.on_accent, dash=(4, 4), tags="crop"
                )
            for y in (top + (bottom - top) / 3, top + (bottom - top) * 2 / 3):
                self.canvas.create_line(
                    left, y, right, y, fill=tokens.on_accent, dash=(4, 4), tags="crop"
                )
            for hx, hy in self._crop_handle_points(left, top, right, bottom).values():
                self.canvas.create_rectangle(
                    hx - 4,
                    hy - 4,
                    hx + 4,
                    hy + 4,
                    fill=tokens.surface,
                    outline=tokens.accent,
                    tags="crop",
                )
        if self._smart_text_perspective_quad is not None and self.original is not None:
            quad_points = [
                self._image_to_canvas(
                    self._smart_text_perspective_quad[index] * self.original.width,
                    self._smart_text_perspective_quad[index + 1] * self.original.height,
                )
                for index in range(0, 8, 2)
            ]
            self.canvas.create_polygon(
                *[coordinate for point in quad_points for coordinate in point],
                outline=tokens.accent,
                fill="",
                width=3,
                tags="crop",
            )
            for index, (x, y) in enumerate(quad_points, start=1):
                self.canvas.create_oval(
                    x - 7,
                    y - 7,
                    x + 7,
                    y + 7,
                    fill=tokens.accent,
                    outline=tokens.on_accent,
                    width=2,
                    tags="crop",
                )
                self.canvas.create_text(
                    x,
                    y,
                    text=str(index),
                    fill=tokens.on_accent,
                    tags="crop",
                )

    def _hit_test_smart_text_perspective(self, x: int, y: int) -> int | None:
        if self._smart_text_perspective_quad is None or self.original is None:
            return None
        tolerance = 13
        for index in range(4):
            canvas_x, canvas_y = self._image_to_canvas(
                self._smart_text_perspective_quad[index * 2] * self.original.width,
                self._smart_text_perspective_quad[index * 2 + 1] * self.original.height,
            )
            if abs(x - canvas_x) <= tolerance and abs(y - canvas_y) <= tolerance:
                return index
        return None

    def _set_smart_text_perspective_corner(
        self,
        index: int,
        point: tuple[int, int],
    ) -> bool:
        if self._smart_text_perspective_quad is None or self.original is None:
            return False
        if not 0 <= index < 4:
            raise ValueError("透视文字角点索引无效")
        values = list(self._smart_text_perspective_quad)
        values[index * 2] = max(0.0, min(1.0, point[0] / max(1, self.original.width)))
        values[index * 2 + 1] = max(
            0.0,
            min(1.0, point[1] / max(1, self.original.height)),
        )
        try:
            quad = validate_normalized_quad(values)
        except ValueError:
            self.smart_edit_status_var.set("角点不能交叉或重叠，请保持四边形轮廓")
            return False
        left, top, right, bottom = quad_bounds(quad)
        region_box = (
            max(0, math.floor(left * self.original.width)),
            max(0, math.floor(top * self.original.height)),
            min(self.original.width, math.ceil(right * self.original.width)),
            min(self.original.height, math.ceil(bottom * self.original.height)),
        )
        self._smart_text_perspective_quad = quad
        self._smart_text_region_box = region_box
        self.crop_box = region_box
        self._update_smart_text_region_label()
        self.smart_edit_status_var.set(f"正在校正透视角点 {index + 1}")
        return True

    def _hit_test_crop(self, x: int, y: int) -> str:
        if self.crop_box is None:
            return "new"
        left, top = self._image_to_canvas(self.crop_box[0], self.crop_box[1])
        right, bottom = self._image_to_canvas(self.crop_box[2], self.crop_box[3])
        tolerance = 10
        for name, (hx, hy) in self._crop_handle_points(left, top, right, bottom).items():
            if abs(x - hx) <= tolerance and abs(y - hy) <= tolerance:
                return name
        if left <= x <= right and top <= y <= bottom:
            return "move"
        return "new"

    def _update_crop_cursor(self, event: tk.Event) -> None:
        if self._hit_test_smart_text_perspective(event.x, event.y) is not None:
            self.canvas.configure(cursor="sizing")
            return
        action = self._hit_test_crop(event.x, event.y)
        cursors = {
            "move": "fleur",
            "n": "sb_v_double_arrow",
            "s": "sb_v_double_arrow",
            "e": "sb_h_double_arrow",
            "w": "sb_h_double_arrow",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }
        self.canvas.configure(cursor=cursors.get(action, "crosshair"))

    @staticmethod
    def _crop_handle_points(
        left: int, top: int, right: int, bottom: int
    ) -> dict[str, tuple[float, float]]:
        mid_x = left + (right - left) / 2
        mid_y = top + (bottom - top) / 2
        return {
            "nw": (left, top),
            "n": (mid_x, top),
            "ne": (right, top),
            "e": (right, mid_y),
            "se": (right, bottom),
            "s": (mid_x, bottom),
            "sw": (left, bottom),
            "w": (left, mid_y),
        }

    def _move_crop_box(
        self, box: tuple[int, int, int, int], dx: int, dy: int
    ) -> tuple[int, int, int, int]:
        image = self.base_preview_image
        if image is None:
            return box
        left, top, right, bottom = box
        width = right - left
        height = bottom - top
        new_left = max(0, min(left + dx, image.width - width))
        new_top = max(0, min(top + dy, image.height - height))
        return (new_left, new_top, new_left + width, new_top + height)

    def _resize_crop_box(
        self, box: tuple[int, int, int, int], action: str, point: tuple[int, int]
    ) -> tuple[int, int, int, int]:
        image = self.base_preview_image
        if image is None:
            return box
        left, top, right, bottom = box
        x, y = point
        if "w" in action:
            left = x
        if "e" in action:
            right = x
        if "n" in action:
            top = y
        if "s" in action:
            bottom = y
        left, right = sorted((max(0, min(left, image.width)), max(0, min(right, image.width))))
        top, bottom = sorted((max(0, min(top, image.height)), max(0, min(bottom, image.height))))
        if right - left < 4:
            right = min(image.width, left + 4)
        if bottom - top < 4:
            bottom = min(image.height, top + 4)
        return self._constrain_box_to_ratio(
            (left, top, right, bottom), action=action, source_box=box
        )

    def _crop_ratio_value(self) -> float | None:
        try:
            return parse_crop_ratio(self.crop_ratio_var.get())
        except ValueError as error:
            self.status_var.set(str(error))
            return None

    def _constrain_box_to_ratio(
        self,
        box: tuple[int, int, int, int],
        anchor: tuple[int, int] | None = None,
        action: str = "se",
        source_box: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int, int, int]:
        ratio = self._crop_ratio_value()
        image = self.base_preview_image
        if ratio is None or image is None:
            return box

        left, top, right, bottom = box
        width = max(4, right - left)
        height = max(4, bottom - top)
        if width / height > ratio:
            width = round(height * ratio)
        else:
            height = round(width / ratio)

        if anchor is not None:
            anchor_x, anchor_y = anchor
            if right < anchor_x:
                left = anchor_x - width
                right = anchor_x
            else:
                left = anchor_x
                right = anchor_x + width
            if bottom < anchor_y:
                top = anchor_y - height
                bottom = anchor_y
            else:
                top = anchor_y
                bottom = anchor_y + height
        elif source_box is not None:
            old_left, old_top, old_right, old_bottom = source_box
            if "w" in action:
                right = old_right
                left = right - width
            else:
                left = old_left
                right = left + width
            if "n" in action:
                bottom = old_bottom
                top = bottom - height
            else:
                top = old_top
                bottom = top + height

        if left < 0:
            right -= left
            left = 0
        if top < 0:
            bottom -= top
            top = 0
        if right > image.width:
            left -= right - image.width
            right = image.width
        if bottom > image.height:
            top -= bottom - image.height
            bottom = image.height
        left = max(0, left)
        top = max(0, top)
        right = min(image.width, max(left + 4, right))
        bottom = min(image.height, max(top + 4, bottom))
        return (left, top, right, bottom)

    def _update_estimate(self) -> None:
        if self.original is None:
            self.size_var.set("预计输出大小：-")
            return
        options = self._read_options(allow_without_image=True, quiet=True)
        if options is None:
            self.size_var.set("预计输出大小：等待有效尺寸")
            return
        prepared: Image.Image | None = None
        try:
            if self.mode_router.active_mode is ModeId.CONVERT:
                prepared = execute_transform_recipe(
                    self.original,
                    self._active_conversion_recipe(),
                )
                size = estimate_save_size(prepared, options)
            elif self.document is not None:
                prepared = render_image(self.original, self.document.timeline)
                size = estimate_save_size(self.original, options, timeline=self.document.timeline)
            else:
                prepared = prepare_image(self.original, options)
                size = estimate_save_size(self.original, options)
            prepared_size = prepared.size
        except Exception:
            self.size_var.set("预计输出大小：无法计算")
            return
        finally:
            if prepared is not None:
                prepared.close()
        savings = ""
        if self.image_path is not None:
            try:
                savings = "，" + self._batch_savings_text(self.image_path.stat().st_size, size)
            except OSError:
                savings = ""
        self.size_var.set(
            f"预计输出：{prepared_size[0]} x {prepared_size[1]}，"
            f"{self._format_bytes(size)}{savings}"
        )

    @staticmethod
    def _format_bytes(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.2f} MB"
