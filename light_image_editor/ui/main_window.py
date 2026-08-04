from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
import uuid
from collections.abc import Sequence
from concurrent.futures import Future
from dataclasses import replace
from functools import partial
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageGrab, ImageTk

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

from ..annotations import AnnotationObject, hit_radius_px, object_at, parse_crop_ratio
from ..batch import (
    BatchManifest,
    BatchPreset,
    BatchRecipe,
    BatchRunner,
    TaskStatus,
    export_batch_log,
)
from ..commands import EditCommand
from ..document import Document, DocumentState
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
from ..identity import PRODUCT_NAME_ZH
from ..image_loader import ImageLoader, ImageLoadFailure
from ..metadata import image_properties
from ..navigation import NavigationSession
from ..presets import EXPORT_PRESETS, apply_export_preset
from ..preview import PreviewFrame, PreviewRenderer, color_manage_for_display
from ..projects import (
    ProjectError,
    ProjectSourceMissing,
    ProjectSourceModified,
    load_project,
    save_project,
)
from ..renderer import (
    apply_command_to_plan,
    detect_straighten_angle,
    histogram,
    reduce_timeline,
    render_image,
)
from ..session import SessionStore
from ..settings import SettingsStore
from ..windows_drop import WindowsFileDrop
from .curve_editor import CurveEditor

APP_NAME = PRODUCT_NAME_ZH
ZOOM_LEVELS = ("适应", "50%", "100%", "150%", "200%")
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


class ImageEditorApp(tk.Tk):
    def __init__(
        self,
        settings_store: SettingsStore | None = None,
        image_loader: ImageLoader | None = None,
        preview_renderer: PreviewRenderer | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        super().__init__()
        self.settings_store = settings_store or SettingsStore()
        self.image_loader = image_loader or ImageLoader()
        self.preview_renderer = preview_renderer or PreviewRenderer()
        self.session_store = session_store or SessionStore()
        self.navigation = NavigationSession([])
        self.title(APP_NAME)
        self.geometry("1240x780")
        self.minsize(1040, 680)

        self.image_path: Path | None = None
        self.original: Image.Image | None = None
        self.document: Document | None = None
        self.base_preview_image: Image.Image | None = None
        self.preview: ImageTk.PhotoImage | None = None
        self.preview_scale = 1.0
        self.preview_offset = (0, 0)
        self.pan_offset = (0, 0)
        self.pan_start: tuple[int, int] | None = None
        self.pan_start_offset = (0, 0)
        self.zoom_mode = tk.StringVar(value="适应")
        self.compare_original = tk.BooleanVar(value=False)
        self.split_compare_var = tk.BooleanVar(value=False)
        self.split_compare_percent_var = tk.IntVar(value=50)
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
        self._animation_sequence: AnimationSequence | None = None
        self._animation_index = 0
        self._animation_job: str | None = None
        self._animation_generation = 0
        self._ratio_lock = False
        self._preview_job: str | None = None
        self._preview_generation = 0
        self.preview_ready_generation = 0
        self._load_generation = 0
        self._failed_navigation_paths: set[str] = set()
        self._saved_geometry = ""
        self._is_fullscreen = False
        self._is_immersive = False
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
        self.status_var = tk.StringVar(value="打开一张图片开始编辑")
        self.info_var = tk.StringVar(value="未载入图片")
        self.crop_var = tk.StringVar(value="未设置裁剪")
        self.size_var = tk.StringVar(value="预计输出大小：-")
        self.edit_panel_visible_var = tk.BooleanVar(value=False)
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
        self.favorite_actions: list[str] = ["open", "save", "undo", "redo", "crop"]
        self.custom_shortcuts: dict[str, str] = {
            key: shortcut for key, _label, shortcut in ACTION_CATALOG
        }
        self._custom_bound_sequences: set[str] = set()
        self.restore_workspace = True
        self.restore_workspace_var = tk.BooleanVar(value=True)
        self.last_project = ""
        self.clean_signature: tuple[object, ...] | None = None

        self._load_config()
        self._bind_document_variables()
        if self._saved_geometry:
            try:
                self.geometry(self._saved_geometry)
            except tk.TclError:
                self.geometry("1240x780")
        self._build_ui()
        self._bind_shortcuts()
        self._drop_target = WindowsFileDrop(self, self.open_paths)
        self.after(0, self._drop_target.enable)
        self.bind("<Configure>", self._on_configure)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._autosave_job = self.after(2000, self._autosave_session)
        self._source_monitor_job = self.after(2000, self._monitor_source_file)
        self.after(250, self._offer_session_recovery)
        self.after(650, self._offer_batch_recovery)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TButton", padding=(10, 7))
        style.configure("Accent.TButton", padding=(12, 8), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Panel.TLabelframe", padding=12)

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0)
        self.rowconfigure(2, weight=1)

        self._build_menu()

        self.toolbar = ttk.Frame(self, padding=(12, 10))
        self.toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.toolbar.columnconfigure(10, weight=1)

        ttk.Button(self.toolbar, text="打开", command=self.open_file).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(self.toolbar, text="上一张", command=lambda: self.navigate_folder(-1)).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(self.toolbar, text="下一张", command=lambda: self.navigate_folder(1)).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(self.toolbar, text="实际尺寸", command=lambda: self.set_zoom("100%")).grid(
            row=0, column=3, padx=(0, 6)
        )
        ttk.Button(self.toolbar, text="全屏", command=self.toggle_fullscreen).grid(
            row=0, column=4, padx=(0, 6)
        )
        ttk.Button(self.toolbar, text="幻灯片", command=self.toggle_slideshow).grid(
            row=0, column=5, padx=(0, 6)
        )
        ttk.Button(self.toolbar, text="属性", command=self.show_properties).grid(
            row=0, column=6, padx=(0, 6)
        )
        ttk.Checkbutton(
            self.toolbar,
            text="编辑工具",
            variable=self.edit_panel_visible_var,
            command=self.toggle_edit_panel,
        ).grid(row=0, column=7, padx=(0, 12))
        ttk.Label(self.toolbar, text="缩放").grid(row=0, column=8, padx=(0, 4))
        zoom = ttk.Combobox(
            self.toolbar, textvariable=self.zoom_mode, values=ZOOM_LEVELS, state="readonly", width=7
        )
        zoom.grid(row=0, column=9, padx=(0, 8))
        zoom.bind("<<ComboboxSelected>>", lambda _event: self._zoom_changed())
        ttk.Label(self.toolbar, textvariable=self.info_var).grid(row=0, column=10, sticky="e")

        self.edit_toolbar = ttk.Frame(self, padding=(12, 0, 12, 8))
        self.edit_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(self.edit_toolbar, text="粘贴", command=self.paste_from_clipboard).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(
            self.edit_toolbar, text="保存为", style="Accent.TButton", command=self.save_as
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(self.edit_toolbar, text="快速导出", command=self.quick_export).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(self.edit_toolbar, text="复制结果", command=self.copy_result).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(self.edit_toolbar, text="批量", command=self.batch_process).grid(
            row=0, column=4, padx=(0, 14)
        )
        ttk.Button(self.edit_toolbar, text="撤销", command=self.undo).grid(
            row=0, column=5, padx=(0, 8)
        )
        ttk.Button(self.edit_toolbar, text="裁剪复位", command=self.reset_crop).grid(
            row=0, column=6, padx=(0, 8)
        )
        ttk.Button(self.edit_toolbar, text="应用裁剪", command=self.apply_crop).grid(
            row=0, column=7, padx=(0, 14)
        )
        ttk.Button(self.edit_toolbar, text="左转", command=lambda: self.rotate(-90)).grid(
            row=0, column=8, padx=(0, 6)
        )
        ttk.Button(self.edit_toolbar, text="右转", command=lambda: self.rotate(90)).grid(
            row=0, column=9, padx=(0, 6)
        )
        ttk.Button(self.edit_toolbar, text="水平翻转", command=self.flip_horizontal).grid(
            row=0, column=10, padx=(0, 6)
        )
        ttk.Button(self.edit_toolbar, text="垂直翻转", command=self.flip_vertical).grid(
            row=0, column=11, padx=(0, 12)
        )
        ttk.Checkbutton(
            self.edit_toolbar,
            text="对比原图",
            variable=self.compare_original,
            command=self._refresh_preview,
        ).grid(row=0, column=12, padx=(0, 12))
        ttk.Checkbutton(
            self.edit_toolbar,
            text="分屏对比",
            variable=self.split_compare_var,
            command=self._refresh_preview,
        ).grid(row=0, column=13, padx=(0, 6))
        ttk.Scale(
            self.edit_toolbar,
            from_=10,
            to=90,
            orient="horizontal",
            variable=self.split_compare_percent_var,
            command=lambda _value: self._refresh_preview(),
            length=90,
        ).grid(row=0, column=14, padx=(0, 6))

        annotation_toolbar = ttk.Frame(self.edit_toolbar)
        annotation_toolbar.grid(row=1, column=0, columnspan=15, sticky="ew", pady=(8, 0))
        for column, tool in enumerate(ANNOTATION_TOOL_LABELS):
            ttk.Button(
                annotation_toolbar,
                text=ANNOTATION_TOOL_LABELS[tool],
                command=partial(self._set_annotation_tool, tool),
            ).grid(row=0, column=column, padx=(0, 4))
        ttk.Label(annotation_toolbar, textvariable=self.current_tool_label_var).grid(
            row=0, column=len(ANNOTATION_TOOL_LABELS), padx=(8, 0)
        )

        self.favorite_toolbar = ttk.Frame(self.edit_toolbar)
        self.favorite_toolbar.grid(row=2, column=0, columnspan=15, sticky="ew", pady=(6, 0))
        self._refresh_favorite_toolbar()

        canvas_frame = ttk.Frame(self, padding=(12, 0, 8, 12))
        canvas_frame.grid(row=2, column=0, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            canvas_frame,
            bg="#1f232b",
            highlightthickness=1,
            highlightcolor="#5eead4",
            cursor="crosshair",
            takefocus=True,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<ButtonPress-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<Motion>", self._canvas_motion)
        self.canvas.bind("<MouseWheel>", self._zoom_with_wheel)
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

        self.panel_shell = ttk.Frame(self, padding=(0, 0, 8, 12), width=360)
        self.panel_shell.grid(row=2, column=1, sticky="ns")
        self.panel_shell.rowconfigure(0, weight=1)
        self.panel_shell.columnconfigure(0, weight=1)
        panel_canvas = tk.Canvas(self.panel_shell, width=340, highlightthickness=0)
        panel_canvas.grid(row=0, column=0, sticky="ns")
        panel_scroll = ttk.Scrollbar(
            self.panel_shell, orient="vertical", command=panel_canvas.yview
        )
        panel_scroll.grid(row=0, column=1, sticky="ns")
        panel_canvas.configure(yscrollcommand=panel_scroll.set)
        panel = ttk.Frame(panel_canvas, padding=(8, 0, 12, 12))
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

        export_box = ttk.LabelFrame(panel, text="导出", style="Panel.TLabelframe")
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
            values=("JPEG", "PNG", "WEBP", "AVIF", "BMP", "TIFF"),
            state="readonly",
        )
        fmt.grid(row=1, column=1, sticky="ew", pady=5)
        fmt.bind("<<ComboboxSelected>>", lambda _event: self._custom_export_changed())
        self._slider(export_box, "质量", self.quality_var, 1, 100, 2, self._custom_export_changed)
        ttk.Label(export_box, text="目标 KB").grid(row=3, column=0, sticky="w", pady=5)
        target_entry = ttk.Entry(export_box, textvariable=self.target_size_var)
        target_entry.grid(row=3, column=1, sticky="ew", pady=5)
        target_entry.bind("<KeyRelease>", lambda _event: self._custom_export_changed())
        ttk.Label(export_box, text="文件前缀").grid(row=4, column=0, sticky="w", pady=5)
        prefix_entry = ttk.Entry(export_box, textvariable=self.filename_prefix_var)
        prefix_entry.grid(row=4, column=1, sticky="ew", pady=5)
        prefix_entry.bind("<KeyRelease>", lambda _event: self._save_config())
        ttk.Label(export_box, text="文件后缀").grid(row=5, column=0, sticky="w", pady=5)
        suffix_entry = ttk.Entry(export_box, textvariable=self.filename_suffix_var)
        suffix_entry.grid(row=5, column=1, sticky="ew", pady=5)
        suffix_entry.bind("<KeyRelease>", lambda _event: self._save_config())
        ttk.Checkbutton(
            export_box,
            text="保留 EXIF / ICC / DPI / XMP",
            variable=self.preserve_metadata_var,
            command=self._custom_export_changed,
        ).grid(row=6, column=1, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            export_box,
            text="覆盖前创建 .bak 备份",
            variable=self.backup_on_overwrite_var,
            command=self._save_config,
        ).grid(row=7, column=1, sticky="w", pady=(4, 0))
        ttk.Checkbutton(
            export_box,
            text="保存后定位文件",
            variable=self.reveal_after_save_var,
            command=self._save_config,
        ).grid(row=8, column=1, sticky="w", pady=(4, 0))
        ttk.Label(export_box, textvariable=self.size_var).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )

        resize_box = ttk.LabelFrame(panel, text="尺寸", style="Panel.TLabelframe")
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

        adjust_box = ttk.LabelFrame(panel, text="快速调色", style="Panel.TLabelframe")
        adjust_box.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        adjust_box.columnconfigure(1, weight=1)
        self._slider(adjust_box, "亮度", self.brightness_var, -100, 100, 0, self._live_adjust)
        self._slider(adjust_box, "对比度", self.contrast_var, -100, 100, 1, self._live_adjust)
        self._slider(adjust_box, "饱和度", self.saturation_var, -100, 100, 2, self._live_adjust)
        self._slider(adjust_box, "锐化", self.sharpness_var, 0, 100, 3, self._live_adjust)
        adjust_actions = ttk.Frame(adjust_box)
        adjust_actions.grid(row=4, column=1, sticky="ew", pady=(8, 0))
        grayscale_check = ttk.Checkbutton(
            adjust_actions, text="黑白", variable=self.grayscale_var, command=self._live_adjust
        )
        grayscale_check.grid(row=0, column=0, sticky="w", padx=(0, 10))
        auto_check = ttk.Checkbutton(
            adjust_actions,
            text="自动优化",
            variable=self.auto_contrast_var,
            command=self._live_adjust,
        )
        auto_check.grid(row=0, column=1, sticky="w")

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

        tone_box = ttk.LabelFrame(panel, text="高级调色", style="Panel.TLabelframe")
        tone_box.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        tone_box.columnconfigure(1, weight=1)
        self._slider(
            tone_box,
            "色温",
            self.temperature_var,
            -100,
            100,
            0,
            lambda: self._advanced_color_changed("temperature", self.temperature_var.get()),
        )
        self._slider(
            tone_box,
            "色调",
            self.tint_var,
            -100,
            100,
            1,
            lambda: self._advanced_color_changed("tint", self.tint_var.get()),
        )
        self._slider(
            tone_box,
            "阴影",
            self.shadows_var,
            -100,
            100,
            2,
            lambda: self._advanced_color_changed("shadows", self.shadows_var.get()),
        )
        self._slider(
            tone_box,
            "高光",
            self.highlights_var,
            -100,
            100,
            3,
            lambda: self._advanced_color_changed("highlights", self.highlights_var.get()),
        )
        self._slider(
            tone_box,
            "降噪",
            self.denoise_var,
            0,
            100,
            4,
            lambda: self._advanced_color_changed("denoise", self.denoise_var.get()),
        )
        self._slider(tone_box, "黑场", self.level_black_var, 0, 254, 5, self._levels_changed)
        self._slider(tone_box, "白场", self.level_white_var, 1, 255, 6, self._levels_changed)
        ttk.Label(tone_box, text="Gamma").grid(row=7, column=0, sticky="w", pady=5)
        gamma_scale = ttk.Scale(
            tone_box,
            from_=0.1,
            to=3.0,
            orient="horizontal",
            variable=self.level_gamma_var,
            command=lambda _value: self._levels_changed(),
        )
        gamma_scale.grid(row=7, column=1, sticky="ew", pady=5)
        gamma_scale.bind("<ButtonPress-1>", self._begin_slider_command)
        gamma_scale.bind("<ButtonRelease-1>", self._end_slider_command)
        ttk.Label(tone_box, textvariable=self.level_gamma_var, width=6).grid(row=7, column=2)

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

        self.status_label = ttk.Label(
            self, textvariable=self.status_var, anchor="w", padding=(12, 6)
        )
        self.status_label.grid(row=3, column=0, columnspan=2, sticky="ew")
        if not self.edit_panel_visible_var.get():
            self.edit_toolbar.grid_remove()
            self.panel_shell.grid_remove()

    def _build_menu(self) -> None:
        self.menu_bar = tk.Menu(self)
        file_menu = tk.Menu(self.menu_bar, tearoff=False)
        file_menu.add_command(label="打开...", accelerator="Ctrl+O", command=self.open_file)
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
        self.menu_bar.add_cascade(label="文件", menu=file_menu)

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
        self.menu_bar.add_cascade(label="编辑", menu=edit_menu)

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
        view_menu.add_command(label="适应视图", accelerator="Ctrl+0", command=self.reset_view)
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
        view_menu.add_command(label="全屏", accelerator="F11", command=self.toggle_fullscreen)
        view_menu.add_command(
            label="沉浸式看图", accelerator="Ctrl+F11", command=self.toggle_immersive
        )
        view_menu.add_command(
            label="开始/停止幻灯片", accelerator="F5", command=self.toggle_slideshow
        )
        view_menu.add_command(label="幻灯片设置...", command=self.configure_slideshow)
        self.menu_bar.add_cascade(label="视图", menu=view_menu)

        info_menu = tk.Menu(self.menu_bar, tearoff=False)
        info_menu.add_command(
            label="图片属性", accelerator="Alt+Enter", command=self.show_properties
        )
        self.menu_bar.add_cascade(label="信息", menu=info_menu)

        self.config(menu=self.menu_bar)
        self._refresh_recent_menu()
        self._refresh_recent_directory_menu()
        self._refresh_recent_project_menu()
        self._refresh_recent_export_menu()

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
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky="ew", pady=5)
        frame.columnconfigure(0, weight=1)
        scale = ttk.Scale(
            frame,
            from_=start,
            to=end,
            orient="horizontal",
            variable=var,
            command=lambda _v: command(),
        )
        scale.grid(row=0, column=0, sticky="ew")
        scale.bind("<ButtonPress-1>", self._begin_slider_command)
        scale.bind("<ButtonRelease-1>", self._end_slider_command)
        ttk.Label(frame, textvariable=var, width=4).grid(row=0, column=1, sticky="e")

    def _begin_slider_command(self, _event: tk.Event) -> None:
        self._active_slider_merge_key = uuid.uuid4().hex

    def _end_slider_command(self, _event: tk.Event) -> None:
        self._active_slider_merge_key = None

    def _action_callback(self, action: str):
        actions = {
            "open": self.open_file,
            "save": self.save_as,
            "save_project": self.save_project_dialog,
            "undo": self.undo,
            "redo": self.redo,
            "rotate_right": lambda: self.rotate(90),
            "select": lambda: self._set_annotation_tool("select"),
            "crop": lambda: self._set_annotation_tool("crop"),
            "text": lambda: self._set_annotation_tool("text"),
            "arrow": lambda: self._set_annotation_tool("arrow"),
            "rectangle": lambda: self._set_annotation_tool("rectangle"),
            "mosaic": lambda: self._set_annotation_tool("mosaic"),
            "blur": lambda: self._set_annotation_tool("blur"),
        }
        return actions[action]

    def _refresh_favorite_toolbar(self) -> None:
        if not hasattr(self, "favorite_toolbar"):
            return
        for child in self.favorite_toolbar.winfo_children():
            child.destroy()
        ttk.Label(self.favorite_toolbar, text="收藏：").grid(row=0, column=0, padx=(0, 5))
        labels = {key: label for key, label, _shortcut in ACTION_CATALOG}
        for column, action in enumerate(self.favorite_actions, start=1):
            if action not in labels:
                continue
            ttk.Button(
                self.favorite_toolbar,
                text=labels[action],
                command=self._action_callback(action),
            ).grid(row=0, column=column, padx=(0, 4))
        ttk.Button(
            self.favorite_toolbar,
            text="配置…",
            command=self.configure_workflow,
        ).grid(row=0, column=len(self.favorite_actions) + 1, padx=(8, 0))

    def _apply_custom_shortcuts(self) -> None:
        for sequence in self._custom_bound_sequences:
            self.unbind(sequence)
        self._custom_bound_sequences.clear()
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

    def _invoke_custom_shortcut(self, action: str, event: tk.Event) -> str | None:
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

    def _workspace_setting_changed(self) -> None:
        self.restore_workspace = self.restore_workspace_var.get()
        self._save_config()

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda _event: self.open_file())
        self.bind("<Control-v>", lambda _event: self.paste_from_clipboard())
        self.bind("<Control-s>", lambda _event: self.save_as())
        self.bind("<Control-Shift-S>", lambda _event: self.save_project_dialog())
        self.bind("<Control-e>", lambda _event: self.quick_export())
        self.bind("<Control-Shift-C>", lambda _event: self.copy_result())
        self.bind("<Control-r>", lambda _event: self.rotate(90))
        self.bind("<Control-z>", lambda _event: self.undo())
        self.bind("<Control-y>", lambda _event: self.redo())
        self.bind("<Control-0>", lambda _event: self.reset_view())
        self.bind("<Control-1>", lambda _event: self.set_zoom("100%"))
        self.bind("<Control-plus>", lambda _event: self.step_zoom(1))
        self.bind("<Control-equal>", lambda _event: self.step_zoom(1))
        self.bind("<Control-minus>", lambda _event: self.step_zoom(-1))
        self.bind("<Left>", lambda _event: self.navigate_folder(-1))
        self.bind("<Right>", lambda _event: self.navigate_folder(1))
        self.bind("<Prior>", lambda _event: self.navigate_folder(-1))
        self.bind("<Next>", lambda _event: self.navigate_folder(1))
        self.bind("<Home>", lambda _event: self.navigate_folder_to("first"))
        self.bind("<End>", lambda _event: self.navigate_folder_to("last"))
        self.bind("<Escape>", lambda _event: self._handle_escape())
        self.bind("<Return>", lambda _event: self.apply_current_tool())
        self.bind("<space>", lambda _event: self._set_compare(True))
        self.bind("<KeyRelease-space>", lambda _event: self._set_compare(False))
        self.bind("<F11>", lambda _event: self.toggle_fullscreen())
        self.bind("<Control-F11>", lambda _event: self.toggle_immersive())
        self.bind("<F5>", lambda _event: self.toggle_slideshow())
        self.bind("<F2>", lambda _event: self.rename_current())
        self.bind("<Delete>", lambda _event: self.delete_selected_or_file())
        self.bind("<Control-Shift-E>", lambda _event: self.reveal_current())
        self.bind("<Alt-Return>", lambda _event: self.show_properties())
        self._apply_custom_shortcuts()

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
        if self._syncing_document or self.document is None:
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
        result = detect_straighten_angle(source)
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
        values = histogram(image)
        canvas = self.histogram_canvas
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
        data = self.settings_store.load()
        self.format_var.set(data.get("format", "JPEG"))
        self.quality_var.set(int(data.get("quality", 85)))
        self.target_size_var.set(str(data.get("target_size_kb", "") or ""))
        self.filename_prefix_var.set(str(data.get("filename_prefix", "") or ""))
        self.filename_suffix_var.set(str(data.get("filename_suffix", "") or ""))
        self.watermark_text_var.set(str(data.get("watermark_text", "") or ""))
        self.watermark_position_var.set(str(data.get("watermark_position", "右下") or "右下"))
        self.watermark_opacity_var.set(int(data.get("watermark_opacity", 35)))
        self.watermark_size_var.set(int(data.get("watermark_size_percent", 5)))
        self.reveal_after_save_var.set(bool(data.get("reveal_after_save", False)))
        self.recent_files = [
            str(path) for path in data.get("recent_files", []) if isinstance(path, str)
        ]
        self.recent_directories = [
            str(path) for path in data.get("recent_directories", []) if isinstance(path, str)
        ]
        self.recent_projects = [
            str(path) for path in data.get("recent_projects", []) if isinstance(path, str)
        ]
        self.recent_export_directories = [
            str(path) for path in data.get("recent_export_directories", []) if isinstance(path, str)
        ]
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
        self.restore_workspace = bool(data.get("restore_workspace", True))
        self.restore_workspace_var.set(self.restore_workspace)
        self.last_project = str(data.get("last_project", "") or "")
        saved_tool = str(data.get("annotation_tool", "crop"))
        if saved_tool in ANNOTATION_TOOL_LABELS:
            self.annotation_tool_var.set(saved_tool)
            self.current_tool_label_var.set(f"当前：{ANNOTATION_TOOL_LABELS[saved_tool]}")
        self.edit_panel_visible_var.set(bool(data.get("edit_panel_visible", False)))
        self.slideshow_interval_var.set(float(data.get("slideshow_interval", 3.0)))
        self.slideshow_loop_var.set(bool(data.get("slideshow_loop", True)))
        self.preserve_metadata_var.set(bool(data.get("preserve_metadata", True)))
        self.backup_on_overwrite_var.set(bool(data.get("backup_on_overwrite", True)))
        self.batch_concurrency_var.set(max(1, min(16, int(data.get("batch_concurrency", 2)))))
        self.batch_memory_var.set(max(32, int(data.get("batch_memory_mb", 512))))
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

    def _save_config(self) -> None:
        data = {
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
            "favorite_actions": self.favorite_actions,
            "custom_shortcuts": self.custom_shortcuts,
            "restore_workspace": self.restore_workspace_var.get(),
            "last_project": self.last_project,
            "annotation_tool": self.annotation_tool_var.get(),
            "edit_panel_visible": self.edit_panel_visible_var.get(),
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
        self.settings_store.save(data)

    def _on_configure(self, _event: tk.Event) -> None:
        if not self._is_fullscreen and not self._is_immersive:
            self._saved_geometry = self.geometry()
        self._schedule_preview()

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
        self.original = loaded.document.image
        self.image_path = loaded.document.source_path
        self.navigation = NavigationSession.from_activation([self.image_path])
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
        session = NavigationSession.from_activation(paths)
        if session.current is None:
            self.status_var.set("没有找到可打开的受支持图片")
            messagebox.showwarning(
                APP_NAME, "所选路径中没有可打开的 JPG、PNG、WEBP、BMP、TIFF 或 GIF 图片。"
            )
            return
        self.stop_slideshow()
        self.navigation = session
        self._failed_navigation_paths.clear()
        self._begin_image_load(session.current, notify_error=True)

    def handle_activation(self, paths: Sequence[str | Path]) -> None:
        self.deiconify()
        self.lift()
        try:
            self.attributes("-topmost", True)
            self.after(80, lambda: self.attributes("-topmost", False))
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
        self._load_generation += 1
        generation = self._load_generation
        self.status_var.set(f"正在后台解码：{path.name}")
        future = self.image_loader.load(path)
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
        self._last_source_status = "unchanged"
        self.original = self.document.image
        self.image_path = self.document.source_path
        self._load_current_image(path.name)
        self._remember_file(path)
        self.status_var.set(f"图片已载入 · {self.navigation.source_label or path.parent}")
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
        self.status_var.set(f"{failure.path.name}：{failure.user_message}")
        self.canvas.delete("all")
        self.canvas.create_text(
            max(200, self.canvas.winfo_width() // 2),
            max(120, self.canvas.winfo_height() // 2),
            text=f"无法显示 {failure.path.name}\n{failure.user_message}\n可继续切换其他图片。",
            fill="#f3f4f6",
            justify="center",
            font=("Microsoft YaHei UI", 12),
        )
        if notify_error:
            messagebox.showwarning(APP_NAME, f"{failure.path.name}\n\n{failure.user_message}")
        if skip_direction and len(self._failed_navigation_paths) < len(self.navigation.files):
            target = self.navigation.target(skip_direction, wrap=True)
            if target is not None:
                self._begin_image_load(target, skip_direction=skip_direction)

    def _image_info_text(self) -> str:
        if self.original is None:
            return "未载入图片"
        name = self.image_path.name if self.image_path is not None else "剪贴板图片"
        position = self.navigation.position
        suffix = f"  {position[0]} / {position[1]}" if position and position[1] > 1 else ""
        animation = ""
        if self.document is not None and self.document.metadata.is_animated:
            animation = f"  动画图片 · {self.document.metadata.frame_count} 帧"
        return f"{name}{suffix}  {self.original.width} x {self.original.height}{animation}"

    def _begin_animation_playback(self, path: Path) -> None:
        self._animation_generation += 1
        generation = self._animation_generation
        future = self.image_loader.load_animation(path)
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
            self.status_var.set(f"动画首帧可查看，但多帧播放不可用：{error}")
            return
        self._animation_index = 0
        self._show_animation_frame()

    def _show_animation_frame(self) -> None:
        sequence = self._animation_sequence
        if sequence is None or self.document is None or self.document.timeline.cursor:
            return
        frame = sequence.frames[self._animation_index]
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
        display = color_manage_for_display(display, self.document.metadata.icc_profile)
        self._display_preview(
            PreviewFrame(working=frame, image=display, scale=display.width / frame.width),
            canvas_w,
            canvas_h,
        )
        duration = sequence.durations[self._animation_index]
        self._animation_index = (self._animation_index + 1) % sequence.frame_count
        self._animation_job = self.after(duration, self._show_animation_frame)

    def _stop_animation_playback(self) -> None:
        self._animation_generation += 1
        if self._animation_job is not None:
            try:
                self.after_cancel(self._animation_job)
            except tk.TclError:
                pass
            self._animation_job = None
        if self._animation_sequence is not None:
            for frame in self._animation_sequence.frames:
                frame.close()
        self._animation_sequence = None
        self._animation_index = 0

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

    def paste_from_clipboard(self) -> None:
        if not self._confirm_discard_changes():
            return
        try:
            clipboard = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"读取剪贴板失败：{exc}")
            return

        if isinstance(clipboard, Image.Image):
            copied = clipboard.copy()
            self.document = Document.from_decoded(copied, None, self._new_document_state(copied))
            self.document.base_revision = 1
            self.document.mark_recovered_dirty()
            self.original = self.document.image
            self.image_path = None
            self.navigation = NavigationSession([])
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
        self.title(f"{display_name} — {APP_NAME}")
        self._apply_document_state(self.document.state)
        self._sync_controls_from_timeline()
        self.info_var.set(
            self._image_info_text()
            if self.image_path is not None
            else f"{display_name}  {self.original.width} x {self.original.height}"
        )
        self.crop_var.set("未设置裁剪")
        self.pan_offset = (0, 0)
        self.zoom_mode.set("适应")
        self._refresh_preview()
        self._refresh_history_panel()
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
        self._save_config()

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
            self.edit_toolbar.grid()
            self.panel_shell.grid()
            self.status_var.set("编辑工具已展开")
        else:
            self.edit_toolbar.grid_remove()
            self.panel_shell.grid_remove()
            self.status_var.set("已切换到简洁查看模式")
        self._save_config()
        self._schedule_preview()

    def toggle_fullscreen(self) -> None:
        if self._is_immersive:
            self.toggle_immersive()
            return
        self._is_fullscreen = not self._is_fullscreen
        self.attributes("-fullscreen", self._is_fullscreen)
        self.status_var.set(
            "已进入全屏，按 F11 或 Esc 退出" if self._is_fullscreen else "已退出全屏"
        )

    def toggle_immersive(self) -> None:
        self._is_immersive = not self._is_immersive
        if self._is_immersive:
            self._is_fullscreen = True
            self.attributes("-fullscreen", True)
            self.config(menu="")
            self.toolbar.grid_remove()
            self.edit_toolbar.grid_remove()
            self.panel_shell.grid_remove()
            self.status_label.grid_remove()
        else:
            self._is_fullscreen = False
            self.attributes("-fullscreen", False)
            self.config(menu=self.menu_bar)
            self.toolbar.grid()
            self.status_label.grid()
            if self.edit_panel_visible_var.get():
                self.edit_toolbar.grid()
                self.panel_shell.grid()
        self._schedule_preview()

    def _handle_escape(self) -> None:
        if self._slideshow_job is not None:
            self.stop_slideshow()
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
        else:
            self.reset_crop()

    def apply_current_tool(self) -> None:
        if self.annotation_tool_var.get() == "crop":
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
        self.image_loader.invalidate(path)
        self.navigation.files[self.navigation.index] = target
        self.image_path = target
        if self.document is not None:
            self.document.rebind_source(target)
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
        self.image_loader.invalidate(path)
        self.status_var.set(f"已移动到：{target}")
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
        target = self.navigation.remove(path)
        self.original = None
        self.document = None
        self.image_path = None
        self.session_store.clear()
        self.preview = None
        self.canvas.delete("all")
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
            saved = self._save_document_image(Path(target), options)
            if saved is None:
                return
            self._save_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"保存失败：{exc}")
            return
        self.status_var.set(f"已保存：{saved}")
        self._remember_export_directory(saved.parent)
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
            saved = self._save_document_image(target, options)
            if saved is None:
                return
            self._save_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"快速导出失败：{exc}")
            return
        self.status_var.set(f"已快速导出：{saved}")
        self._remember_export_directory(saved.parent)
        self._mark_clean()
        self.session_store.clear()
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

    def copy_result(self) -> None:
        if self.original is None:
            messagebox.showinfo(APP_NAME, "请先打开一张图片。")
            return
        options = self._read_options()
        if options is None:
            return
        try:
            result = (
                render_image(self.original, self.document.timeline)
                if self.document
                else prepare_image(self.original, options)
            )
            copy_image_to_clipboard(result)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"复制失败：{exc}")
            return
        self.status_var.set(f"已复制编辑结果：{result.width} x {result.height}")

    @staticmethod
    def _unique_export_path(folder: Path, stem: str, extension: str) -> Path:
        return unique_export_path(folder, stem, extension)

    def _read_filename_affixes(self) -> tuple[str, str] | None:
        state = self.document.state if self.document is not None else None
        try:
            prefix = validate_filename_affix(
                "文件前缀", state.filename_prefix if state is not None else ""
            )
            suffix = validate_filename_affix(
                "文件后缀", state.filename_suffix if state is not None else ""
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

    def _shutdown_services(self) -> None:
        self.stop_slideshow()
        self._stop_animation_playback()
        if self.batch_runner is not None:
            self.batch_runner.cancel()
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        if self._source_monitor_job is not None:
            self.after_cancel(self._source_monitor_job)
            self._source_monitor_job = None
        self._save_config()
        self._drop_target.disable()
        self.image_loader.shutdown()
        self.preview_renderer.shutdown()

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
        if self.document is None or self.document.undo_command() is None:
            self.status_var.set("没有可撤销的操作")
            return
        self._sync_controls_from_timeline()
        self._refresh_history_panel()
        self._refresh_preview()
        self.status_var.set("已撤销一步")

    def redo(self) -> None:
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
        self.crop_box = None
        self._commit_command(EditCommand.create("crop", {"box": None}, "重置裁剪"))
        self.crop_var.set("未设置裁剪")

    def apply_crop(self) -> None:
        if self.original is None or self.document is None:
            return
        if self.crop_box is None:
            self.status_var.set("没有可应用的裁剪区域")
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
        existing = reduce_timeline(self.document.timeline).crop_box
        if existing is not None:
            existing_width = existing[2] - existing[0]
            existing_height = existing[3] - existing[1]
            normalized = (
                existing[0] + normalized[0] * existing_width,
                existing[1] + normalized[1] * existing_height,
                existing[0] + normalized[2] * existing_width,
                existing[1] + normalized[3] * existing_height,
            )
        self._commit_command(
            EditCommand.create("crop", {"box": list(normalized)}, "应用裁剪"),
            refresh=False,
        )
        self.crop_box = None
        self.document.update_state("apply_crop", crop_box=None)
        self.crop_var.set("未设置裁剪")
        self.status_var.set("已非破坏应用裁剪，可从历史面板恢复")
        self._refresh_preview()

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
        if self.document is not None:
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
        if self.document is not None:
            self.document.update_state("crop_ratio", crop_box=self.crop_box)
        self._update_crop_label()
        self._refresh_preview()

    def rotate(self, degrees: int) -> None:
        if self.original is None or self.document is None:
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
        self.crop_box = None
        self._commit_command(EditCommand.create("flip_horizontal", {}, "水平翻转"), refresh=False)
        self._sync_controls_from_timeline()
        self.status_var.set("已切换水平翻转")
        self._refresh_preview()

    def flip_vertical(self) -> None:
        if self.original is None:
            return
        self.crop_box = None
        self._commit_command(EditCommand.create("flip_vertical", {}, "垂直翻转"), refresh=False)
        self._sync_controls_from_timeline()
        self.status_var.set("已切换垂直翻转")
        self._refresh_preview()

    def apply_size_preset(self, value: str) -> None:
        source = self._current_working_image()
        if source is None:
            return
        self._ratio_lock = True
        if value == "original":
            self.width_var.set(str(source.width))
            self.height_var.set(str(source.height))
            self.max_side_var.set("")
        elif value == "1920":
            self.width_var.set("")
            self.height_var.set("")
            self.max_side_var.set("1920")
        else:
            scale = int(value) / 100
            self.width_var.set(str(max(1, round(source.width * scale))))
            self.height_var.set(str(max(1, round(source.height * scale))))
            self.max_side_var.set("")
        self._ratio_lock = False
        self.status_var.set("已应用常用尺寸")
        self._update_estimate()

    def _read_options(
        self, allow_without_image: bool = False, quiet: bool = False
    ) -> ExportOptions | None:
        if self.document is None:
            if not allow_without_image and not quiet:
                messagebox.showinfo(APP_NAME, "请先打开一张图片。")
            return None
        try:
            return self.document.state.export_options()
        except ValueError as error:
            if not quiet:
                messagebox.showerror(APP_NAME, str(error))
            return None

    def _current_working_image(self) -> Image.Image | None:
        if self.original is None:
            return None
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
                source = self._current_working_image() or self.original
                self._ratio_lock = True
                self.height_var.set(str(round(width * source.height / source.width)))
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
                source = self._current_working_image() or self.original
                self._ratio_lock = True
                self.width_var.set(str(round(height * source.width / source.height)))
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

    def _custom_export_changed(self) -> None:
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
        self.compare_original.set(enabled)
        self._refresh_preview()

    def _zoom_with_wheel(self, event: tk.Event) -> None:
        self.step_zoom(1 if event.delta > 0 else -1)

    def step_zoom(self, direction: int) -> None:
        current = self.zoom_mode.get()
        index = ZOOM_LEVELS.index(current) if current in ZOOM_LEVELS else 0
        index = max(0, min(len(ZOOM_LEVELS) - 1, index + direction))
        self.zoom_mode.set(ZOOM_LEVELS[index])
        self._zoom_changed()

    def _zoom_changed(self) -> None:
        if self.zoom_mode.get() == "适应":
            self.pan_offset = (0, 0)
        self._refresh_preview()

    def set_zoom(self, value: str) -> None:
        self.zoom_mode.set(value)
        self._zoom_changed()

    def reset_view(self) -> None:
        self.zoom_mode.set("适应")
        self.pan_offset = (0, 0)
        self.status_var.set("已适应视图")
        self._refresh_preview()

    def _start_pan(self, event: tk.Event) -> None:
        if self.preview is None:
            return
        self.pan_start = (event.x, event.y)
        self.pan_start_offset = self.pan_offset
        self.canvas.configure(cursor="fleur")

    def _drag_pan(self, event: tk.Event) -> None:
        if self.pan_start is None:
            return
        dx = event.x - self.pan_start[0]
        dy = event.y - self.pan_start[1]
        self.pan_offset = (self.pan_start_offset[0] + dx, self.pan_start_offset[1] + dy)
        self._refresh_preview()

    def _finish_pan(self, _event: tk.Event) -> None:
        self.pan_start = None
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
        future = self.preview_renderer.render(
            self.original,
            self.edit_state(),
            compare_original=self.compare_original.get(),
            canvas_size=(canvas_w, canvas_h),
            zoom_percent=zoom_percent,
            timeline=self.document.timeline if self.document is not None else None,
        )
        self.after(10, self._poll_preview, generation, future, canvas_w, canvas_h)

    def _poll_preview(
        self,
        generation: int,
        future: Future[PreviewFrame],
        canvas_w: int,
        canvas_h: int,
    ) -> None:
        if generation != self._preview_generation:
            return
        if not future.done():
            self.after(10, self._poll_preview, generation, future, canvas_w, canvas_h)
            return
        try:
            frame = future.result()
        except Exception as error:
            self.status_var.set(f"无法生成预览：{error}")
            return
        if generation != self._preview_generation:
            return
        self._display_preview(frame, canvas_w, canvas_h)
        self.preview_ready_generation += 1

    def _display_preview(self, frame: PreviewFrame, canvas_w: int, canvas_h: int) -> None:
        self.base_preview_image = frame.working
        image = frame.image
        split_x: int | None = None
        if self.split_compare_var.get() and not self.compare_original.get() and self.original:
            before = self.original.resize(image.size, Image.Resampling.LANCZOS)
            before = color_manage_for_display(
                before,
                self.document.metadata.icc_profile if self.document else None,
            )
            composite = image.copy()
            split_x = round(image.width * self.split_compare_percent_var.get() / 100)
            composite.paste(before.crop((0, 0, split_x, image.height)), (0, 0))
            image = composite
        self.preview = ImageTk.PhotoImage(image)
        self.preview_scale = frame.scale
        centered_x = (canvas_w - image.width) // 2
        centered_y = (canvas_h - image.height) // 2
        if image.width <= canvas_w:
            offset_x = centered_x
        else:
            offset_x = max(canvas_w - image.width, min(0, centered_x + self.pan_offset[0]))
        if image.height <= canvas_h:
            offset_y = centered_y
        else:
            offset_y = max(canvas_h - image.height, min(0, centered_y + self.pan_offset[1]))
        self.preview_offset = (offset_x, offset_y)

        self.canvas.delete("all")
        self.canvas.create_image(
            self.preview_offset[0], self.preview_offset[1], image=self.preview, anchor="nw"
        )
        if split_x is not None:
            divider_x = self.preview_offset[0] + split_x
            self.canvas.create_line(
                divider_x,
                self.preview_offset[1],
                divider_x,
                self.preview_offset[1] + image.height,
                fill="#fbbf24",
                width=2,
            )
            self.canvas.create_text(
                self.preview_offset[0] + 8,
                self.preview_offset[1] + 8,
                text="编辑前",
                fill="#fbbf24",
                anchor="nw",
            )
            self.canvas.create_text(
                self.preview_offset[0] + image.width - 8,
                self.preview_offset[1] + 8,
                text="编辑后",
                fill="#5eead4",
                anchor="ne",
            )
        self._draw_checker_bounds(image.width, image.height)
        self._draw_crop()
        self._draw_annotation_overlay()
        self._draw_histogram(frame.working)
        if self.edit_panel_visible_var.get():
            self.after_idle(self._update_estimate)

    def _draw_checker_bounds(self, width: int, height: int) -> None:
        ox, oy = self.preview_offset
        self.canvas.create_rectangle(
            ox - 1, oy - 1, ox + width + 1, oy + height + 1, outline="#4b5563"
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
        self.annotation_tool_var.set(tool)
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
        self._save_config()

    def _canvas_press(self, event: tk.Event) -> None:
        self.canvas.focus_set()
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

    def _finish_annotation_selection(self, _event: tk.Event) -> None:
        original = self._annotation_original_object
        draft = self._annotation_drag_object
        if original is not None and draft is not None and draft != original:
            patch = draft.to_json()
            patch.pop("id", None)
            patch.pop("kind", None)
            self._commit_command(
                EditCommand.create(
                    "annotation_update",
                    {"id": original.id, "patch": patch},
                    f"调整{ANNOTATION_TOOL_LABELS[original.kind]}",
                )
            )
        self._annotation_start = None
        self._annotation_original_object = None
        self._annotation_drag_object = None

    def _start_annotation(self, event: tk.Event, tool: str) -> None:
        if self.document is None:
            return
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
            self._clear_annotation_drag()
            return
        if tool == "text":
            text = simpledialog.askstring(
                APP_NAME,
                "输入标注文字：",
                initialvalue=self.annotation_text_var.get(),
                parent=self,
            )
            if not text:
                self._clear_annotation_drag()
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
        self.selected_annotation_id = annotation.id
        self._set_annotation_tool("select")
        self.selected_annotation_id = annotation.id
        self._load_annotation_style(annotation)
        self._clear_annotation_drag()
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

    def _clear_annotation_drag(self) -> None:
        self._annotation_start = None
        self._annotation_drag_object = None
        self._annotation_freehand_points = []
        self._draw_annotation_overlay()

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
                outline="#fbbf24",
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
                    fill="#fbbf24",
                    outline="#78350f",
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
        if annotation.kind in {"line", "arrow"}:
            start = self._normalized_to_canvas(annotation.x1, annotation.y1)
            end = self._normalized_to_canvas(annotation.x2, annotation.y2)
            self.canvas.create_line(*start, *end, fill=color, width=2, tags="annotation-ui")
        elif annotation.kind == "ellipse":
            self.canvas.create_oval(x1, y1, x2, y2, outline=color, width=2, tags="annotation-ui")
        elif annotation.kind == "freehand" and annotation.points:
            points = [self._normalized_to_canvas(x, y) for x, y in annotation.points]
            self.canvas.create_line(
                *[coordinate for point in points for coordinate in point],
                fill=color,
                width=2,
                tags="annotation-ui",
            )
        else:
            self.canvas.create_rectangle(
                x1,
                y1,
                x2,
                y2,
                outline=color,
                width=2,
                dash=(4, 3),
                tags="annotation-ui",
            )
        if self.selected_annotation_id == annotation.id:
            radius = self._hit_radius()
            for x, y in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
                self.canvas.create_rectangle(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    fill="#ccfbf1",
                    outline="#0f766e",
                    tags="annotation-ui",
                )

    def _start_crop(self, event: tk.Event) -> None:
        if self.base_preview_image is None:
            return
        self.drag_start = self._canvas_to_image(event.x, event.y)
        self.crop_drag_start_box = self.crop_box
        self.crop_action = self._hit_test_crop(event.x, event.y)

    def _drag_crop(self, event: tk.Event) -> None:
        if self.base_preview_image is None or self.drag_start is None:
            return
        end = self._canvas_to_image(event.x, event.y)
        if self.crop_action == "new" or self.crop_drag_start_box is None:
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
        else:
            self._update_crop_label()
        self.drag_start = None
        self.crop_drag_start_box = None
        self.crop_action = "new"
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
        if self.crop_box is None:
            return
        left, top = self._image_to_canvas(self.crop_box[0], self.crop_box[1])
        right, bottom = self._image_to_canvas(self.crop_box[2], self.crop_box[3])
        self.canvas.create_rectangle(
            left, top, right, bottom, outline="#2dd4bf", width=2, tags="crop"
        )
        self.canvas.create_rectangle(
            left + 1, top + 1, right - 1, bottom - 1, outline="#0f766e", width=1, tags="crop"
        )
        for x in (left + (right - left) / 3, left + (right - left) * 2 / 3):
            self.canvas.create_line(x, top, x, bottom, fill="#5eead4", dash=(4, 4), tags="crop")
        for y in (top + (bottom - top) / 3, top + (bottom - top) * 2 / 3):
            self.canvas.create_line(left, y, right, y, fill="#5eead4", dash=(4, 4), tags="crop")
        for hx, hy in self._crop_handle_points(left, top, right, bottom).values():
            self.canvas.create_rectangle(
                hx - 4, hy - 4, hx + 4, hy + 4, fill="#ccfbf1", outline="#0f766e", tags="crop"
            )

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
        try:
            if self.document is not None:
                prepared = render_image(self.original, self.document.timeline)
                size = estimate_save_size(self.original, options, timeline=self.document.timeline)
            else:
                prepared = prepare_image(self.original, options)
                size = estimate_save_size(self.original, options)
        except Exception:
            self.size_var.set("预计输出大小：无法计算")
            return
        savings = ""
        if self.image_path is not None:
            try:
                savings = "，" + self._batch_savings_text(self.image_path.stat().st_size, size)
            except OSError:
                savings = ""
        self.size_var.set(
            f"预计输出：{prepared.width} x {prepared.height}，{self._format_bytes(size)}{savings}"
        )

    @staticmethod
    def _format_bytes(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.2f} MB"
