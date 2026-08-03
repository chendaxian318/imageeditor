from __future__ import annotations

import threading
import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import cast

from PIL import Image, ImageGrab, ImageTk

from image_ops import (
    EditState,
    ExportOptions,
    apply_edits,
    estimate_save_size,
    extension_for_format,
    iter_image_files,
    open_image,
    prepare_image,
    save_image,
)

from ..files import (
    copy_image_to_clipboard,
    folder_position,
    list_folder_images,
    open_folder,
    reveal_file,
    unique_export_path,
    validate_filename_affix,
)
from ..identity import PRODUCT_NAME_ZH
from ..settings import SettingsStore

APP_NAME = PRODUCT_NAME_ZH
ZOOM_LEVELS = ("适应", "50%", "100%", "150%", "200%")


class ImageEditorApp(tk.Tk):
    def __init__(self, settings_store: SettingsStore | None = None) -> None:
        super().__init__()
        self.settings_store = settings_store or SettingsStore()
        self.title(APP_NAME)
        self.geometry("1240x780")
        self.minsize(1040, 680)

        self.image_path: Path | None = None
        self.original: Image.Image | None = None
        self.base_preview_image: Image.Image | None = None
        self.preview: ImageTk.PhotoImage | None = None
        self.preview_scale = 1.0
        self.preview_offset = (0, 0)
        self.pan_offset = (0, 0)
        self.pan_start: tuple[int, int] | None = None
        self.pan_start_offset = (0, 0)
        self.zoom_mode = tk.StringVar(value="适应")
        self.compare_original = tk.BooleanVar(value=False)
        self.reveal_after_save_var = tk.BooleanVar(value=False)
        self.crop_box: tuple[int, int, int, int] | None = None
        self.drag_start: tuple[int, int] | None = None
        self.crop_drag_start_box: tuple[int, int, int, int] | None = None
        self.crop_action = "new"
        self.batch_cancel_requested = False
        self._ratio_lock = False
        self._preview_job: str | None = None

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

        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self.brightness_var = tk.IntVar(value=0)
        self.contrast_var = tk.IntVar(value=0)
        self.saturation_var = tk.IntVar(value=0)
        self.sharpness_var = tk.IntVar(value=0)
        self.grayscale_var = tk.BooleanVar(value=False)
        self.auto_contrast_var = tk.BooleanVar(value=False)
        self.recent_files: list[str] = []
        self.undo_stack: list[dict[str, object]] = []
        self.redo_stack: list[dict[str, object]] = []
        self._restoring_history = False
        self.clean_signature: tuple[object, ...] | None = None

        self._load_config()
        self._build_ui()
        self._bind_shortcuts()
        self.bind("<Configure>", lambda _event: self._schedule_preview())
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TButton", padding=(10, 7))
        style.configure("Accent.TButton", padding=(12, 8), font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Panel.TLabelframe", padding=12)

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0)
        self.rowconfigure(1, weight=1)

        self._build_menu()

        toolbar = ttk.Frame(self, padding=(12, 10))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        toolbar.columnconfigure(18, weight=1)

        ttk.Button(toolbar, text="打开", command=self.open_file).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(toolbar, text="上一张", command=lambda: self.navigate_folder(-1)).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(toolbar, text="下一张", command=lambda: self.navigate_folder(1)).grid(
            row=0, column=2, padx=(0, 8)
        )
        ttk.Button(toolbar, text="粘贴", command=self.paste_from_clipboard).grid(
            row=0, column=3, padx=(0, 8)
        )
        ttk.Button(toolbar, text="保存为", style="Accent.TButton", command=self.save_as).grid(
            row=0, column=4, padx=(0, 8)
        )
        ttk.Button(toolbar, text="快速导出", command=self.quick_export).grid(
            row=0, column=5, padx=(0, 8)
        )
        ttk.Button(toolbar, text="复制", command=self.copy_result).grid(
            row=0, column=6, padx=(0, 8)
        )
        ttk.Button(toolbar, text="批量", command=self.batch_process).grid(
            row=0, column=7, padx=(0, 14)
        )
        ttk.Button(toolbar, text="撤销", command=self.undo).grid(row=0, column=8, padx=(0, 8))
        ttk.Button(toolbar, text="裁剪复位", command=self.reset_crop).grid(
            row=0, column=9, padx=(0, 8)
        )
        ttk.Button(toolbar, text="应用裁剪", command=self.apply_crop).grid(
            row=0, column=10, padx=(0, 14)
        )
        ttk.Button(toolbar, text="左转", command=lambda: self.rotate(-90)).grid(
            row=0, column=11, padx=(0, 6)
        )
        ttk.Button(toolbar, text="右转", command=lambda: self.rotate(90)).grid(
            row=0, column=12, padx=(0, 6)
        )
        ttk.Button(toolbar, text="水平翻转", command=self.flip_horizontal).grid(
            row=0, column=13, padx=(0, 6)
        )
        ttk.Button(toolbar, text="垂直翻转", command=self.flip_vertical).grid(
            row=0, column=14, padx=(0, 12)
        )
        ttk.Checkbutton(
            toolbar, text="对比原图", variable=self.compare_original, command=self._refresh_preview
        ).grid(row=0, column=15, padx=(0, 12))
        ttk.Label(toolbar, text="缩放").grid(row=0, column=16, padx=(0, 4))
        zoom = ttk.Combobox(
            toolbar, textvariable=self.zoom_mode, values=ZOOM_LEVELS, state="readonly", width=7
        )
        zoom.grid(row=0, column=17, padx=(0, 8))
        zoom.bind("<<ComboboxSelected>>", lambda _event: self._zoom_changed())
        ttk.Label(toolbar, textvariable=self.info_var).grid(row=0, column=18, sticky="e")

        canvas_frame = ttk.Frame(self, padding=(12, 0, 8, 12))
        canvas_frame.grid(row=1, column=0, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            canvas_frame, bg="#1f232b", highlightthickness=0, cursor="crosshair"
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<ButtonPress-1>", self._start_crop)
        self.canvas.bind("<B1-Motion>", self._drag_crop)
        self.canvas.bind("<ButtonRelease-1>", self._finish_crop)
        self.canvas.bind("<Motion>", self._update_crop_cursor)
        self.canvas.bind("<MouseWheel>", self._zoom_with_wheel)
        self.canvas.bind("<ButtonPress-2>", self._start_pan)
        self.canvas.bind("<B2-Motion>", self._drag_pan)
        self.canvas.bind("<ButtonRelease-2>", self._finish_pan)
        self.canvas.bind("<ButtonPress-3>", self._start_pan)
        self.canvas.bind("<B3-Motion>", self._drag_pan)
        self.canvas.bind("<ButtonRelease-3>", self._finish_pan)
        self.canvas.bind("<Double-Button-1>", lambda _event: self.reset_view())

        panel_shell = ttk.Frame(self, padding=(0, 0, 8, 12), width=360)
        panel_shell.grid(row=1, column=1, sticky="ns")
        panel_shell.rowconfigure(0, weight=1)
        panel_shell.columnconfigure(0, weight=1)
        panel_canvas = tk.Canvas(panel_shell, width=340, highlightthickness=0)
        panel_canvas.grid(row=0, column=0, sticky="ns")
        panel_scroll = ttk.Scrollbar(panel_shell, orient="vertical", command=panel_canvas.yview)
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
        ttk.Label(export_box, text="格式").grid(row=0, column=0, sticky="w", pady=5)
        fmt = ttk.Combobox(
            export_box,
            textvariable=self.format_var,
            values=("JPEG", "PNG", "WEBP", "BMP", "TIFF"),
            state="readonly",
        )
        fmt.grid(row=0, column=1, sticky="ew", pady=5)
        fmt.bind("<<ComboboxSelected>>", lambda _event: self._update_estimate())
        self._slider(export_box, "质量", self.quality_var, 1, 100, 1, self._update_estimate)
        ttk.Label(export_box, text="目标 KB").grid(row=2, column=0, sticky="w", pady=5)
        target_entry = ttk.Entry(export_box, textvariable=self.target_size_var)
        target_entry.grid(row=2, column=1, sticky="ew", pady=5)
        target_entry.bind("<KeyRelease>", lambda _event: self._update_estimate())
        ttk.Label(export_box, text="文件前缀").grid(row=3, column=0, sticky="w", pady=5)
        prefix_entry = ttk.Entry(export_box, textvariable=self.filename_prefix_var)
        prefix_entry.grid(row=3, column=1, sticky="ew", pady=5)
        prefix_entry.bind("<KeyRelease>", lambda _event: self._save_config())
        ttk.Label(export_box, text="文件后缀").grid(row=4, column=0, sticky="w", pady=5)
        suffix_entry = ttk.Entry(export_box, textvariable=self.filename_suffix_var)
        suffix_entry.grid(row=4, column=1, sticky="ew", pady=5)
        suffix_entry.bind("<KeyRelease>", lambda _event: self._save_config())
        ttk.Checkbutton(
            export_box,
            text="保存后定位文件",
            variable=self.reveal_after_save_var,
            command=self._save_config,
        ).grid(row=5, column=1, sticky="w", pady=(8, 0))
        ttk.Label(export_box, textvariable=self.size_var).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 0)
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
            values=("自由", "1:1", "4:3", "3:4", "16:9", "9:16"),
            state="readonly",
            width=8,
        )
        crop_ratio.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        crop_ratio.bind("<<ComboboxSelected>>", lambda _event: self.apply_crop_ratio_to_current())
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
        self._slider(adjust_box, "锐化", self.sharpness_var, -100, 100, 3, self._live_adjust)
        adjust_actions = ttk.Frame(adjust_box)
        adjust_actions.grid(row=4, column=1, sticky="ew", pady=(8, 0))
        grayscale_check = ttk.Checkbutton(
            adjust_actions, text="黑白", variable=self.grayscale_var, command=self._live_adjust
        )
        grayscale_check.grid(row=0, column=0, sticky="w", padx=(0, 10))
        grayscale_check.bind("<ButtonPress-1>", lambda _event: self._push_history())
        auto_check = ttk.Checkbutton(
            adjust_actions,
            text="自动优化",
            variable=self.auto_contrast_var,
            command=self._live_adjust,
        )
        auto_check.grid(row=0, column=1, sticky="w")
        auto_check.bind("<ButtonPress-1>", lambda _event: self._push_history())

        watermark_box = ttk.LabelFrame(panel, text="文字水印", style="Panel.TLabelframe")
        watermark_box.grid(row=3, column=0, sticky="ew", pady=(0, 10))
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
        help_box.grid(row=4, column=0, sticky="ew")
        ttk.Label(
            help_box,
            text=(
                "Ctrl+O 打开  Ctrl+S 保存\nCtrl+R 右转  Ctrl+Z 撤销编辑\n"
                "拖动预览区可直接设置裁剪范围"
            ),
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        status = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(12, 6))
        status.grid(row=2, column=0, columnspan=2, sticky="ew")

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self)
        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="打开...", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(
            label="从剪贴板粘贴", accelerator="Ctrl+V", command=self.paste_from_clipboard
        )
        self.recent_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="最近打开", menu=self.recent_menu)
        file_menu.add_separator()
        file_menu.add_command(label="保存为...", accelerator="Ctrl+S", command=self.save_as)
        file_menu.add_command(label="快速导出", accelerator="Ctrl+E", command=self.quick_export)
        file_menu.add_command(
            label="复制编辑结果", accelerator="Ctrl+Shift+C", command=self.copy_result
        )
        file_menu.add_command(label="批量处理文件...", command=self.batch_process)
        file_menu.add_command(label="批量处理文件夹...", command=self.batch_process_folder)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        menu_bar.add_cascade(label="文件", menu=file_menu)

        edit_menu = tk.Menu(menu_bar, tearoff=False)
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
        menu_bar.add_cascade(label="编辑", menu=edit_menu)

        view_menu = tk.Menu(menu_bar, tearoff=False)
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
        menu_bar.add_cascade(label="视图", menu=view_menu)

        self.config(menu=menu_bar)
        self._refresh_recent_menu()

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
        scale.bind("<ButtonPress-1>", lambda _event: self._push_history())
        ttk.Label(frame, textvariable=var, width=4).grid(row=0, column=1, sticky="e")

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda _event: self.open_file())
        self.bind("<Control-v>", lambda _event: self.paste_from_clipboard())
        self.bind("<Control-s>", lambda _event: self.save_as())
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
        self.bind("<Escape>", lambda _event: self.reset_crop())
        self.bind("<Return>", lambda _event: self.apply_crop())
        self.bind("<space>", lambda _event: self._set_compare(True))
        self.bind("<KeyRelease-space>", lambda _event: self._set_compare(False))

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
        }
        self.settings_store.save(data)

    def open_file(self) -> None:
        if not self._confirm_discard_changes():
            return
        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self.open_path(path, confirm=False)

    def open_path(self, path: str | Path, confirm: bool = True) -> None:
        if confirm and not self._confirm_discard_changes():
            return
        try:
            self.original = open_image(path)
            self.image_path = Path(path)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法打开图片：{exc}")
            return

        self.reset_edits(refresh=False)
        self.crop_box = None
        self.width_var.set(str(self.original.width))
        self.height_var.set(str(self.original.height))
        self.max_side_var.set("")
        self.info_var.set(self._image_info_text())
        self.crop_var.set("未设置裁剪")
        self.status_var.set("图片已载入")
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.pan_offset = (0, 0)
        self.zoom_mode.set("适应")
        self._remember_file(self.image_path)
        self._refresh_preview()
        self._mark_clean()

    def _folder_images(self) -> list[Path]:
        return list_folder_images(self.image_path)

    def _folder_position(self) -> tuple[int, int] | None:
        return folder_position(self.image_path, self._folder_images())

    def _image_info_text(self) -> str:
        if self.original is None:
            return "未载入图片"
        name = self.image_path.name if self.image_path is not None else "剪贴板图片"
        position = self._folder_position()
        suffix = f"  {position[0]} / {position[1]}" if position and position[1] > 1 else ""
        return f"{name}{suffix}  {self.original.width} x {self.original.height}"

    def navigate_folder(self, direction: int) -> None:
        files = self._navigation_files()
        if not files:
            return
        if self.image_path is None:
            return
        try:
            current = self.image_path.resolve()
            resolved = [path.resolve() for path in files]
            index = resolved.index(current)
        except (OSError, ValueError):
            self.status_var.set("无法定位当前图片在文件夹中的位置")
            return
        target = files[(index + direction) % len(files)]
        label = "下一张" if direction > 0 else "上一张"
        self._open_navigation_target(target, label)

    def navigate_folder_to(self, position: str) -> None:
        files = self._navigation_files()
        if not files:
            return
        if position == "first":
            self._open_navigation_target(files[0], "第一张")
        elif position == "last":
            self._open_navigation_target(files[-1], "最后一张")

    def _navigation_files(self) -> list[Path]:
        if self.image_path is None:
            self.status_var.set("当前图片不在文件夹中")
            return []
        if not self._confirm_discard_changes():
            return []
        files = self._folder_images()
        if len(files) <= 1:
            self.status_var.set("当前文件夹没有其他图片")
            return []
        return files

    def _open_navigation_target(self, target: Path, label: str) -> None:
        self.open_path(target, confirm=False)
        self.status_var.set(f"已打开{label}：{target.name}")

    def paste_from_clipboard(self) -> None:
        if not self._confirm_discard_changes():
            return
        try:
            clipboard = ImageGrab.grabclipboard()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"读取剪贴板失败：{exc}")
            return

        if isinstance(clipboard, Image.Image):
            self.original = clipboard.copy()
            self.image_path = None
            self._load_current_image("剪贴板图片")
            self.status_var.set("已从剪贴板粘贴图片")
            return

        if isinstance(clipboard, list):
            for item in clipboard:
                path = Path(item)
                if path.exists() and path.suffix.lower() in {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".bmp",
                    ".tif",
                    ".tiff",
                }:
                    self.open_path(path, confirm=False)
                    return

        messagebox.showinfo(APP_NAME, "剪贴板里没有可粘贴的图片。")

    def _load_current_image(self, display_name: str) -> None:
        if self.original is None:
            return
        self.reset_edits(refresh=False)
        self.crop_box = None
        self.width_var.set(str(self.original.width))
        self.height_var.set(str(self.original.height))
        self.max_side_var.set("")
        self.info_var.set(f"{display_name}  {self.original.width} x {self.original.height}")
        self.crop_var.set("未设置裁剪")
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.pan_offset = (0, 0)
        self.zoom_mode.set("适应")
        self._refresh_preview()
        self._mark_clean()

    def _remember_file(self, path: Path) -> None:
        value = str(path)
        self.recent_files = [item for item in self.recent_files if item != value]
        self.recent_files.insert(0, value)
        self.recent_files = self.recent_files[:8]
        self._refresh_recent_menu()
        self._save_config()

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
            filetypes=[
                (options.output_format, "*" + extension_for_format(options.output_format)),
                ("所有文件", "*.*"),
            ],
        )
        if not target:
            return
        try:
            saved = save_image(self.original, target, options)
            self._save_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"保存失败：{exc}")
            return
        self.status_var.set(f"已保存：{saved}")
        self._mark_clean()
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
        if self.image_path is not None:
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
            saved = save_image(self.original, target, options)
            self._save_config()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"快速导出失败：{exc}")
            return
        self.status_var.set(f"已快速导出：{saved}")
        self._mark_clean()
        if self.reveal_after_save_var.get():
            self._reveal_file(saved)

    def copy_result(self) -> None:
        if self.original is None:
            messagebox.showinfo(APP_NAME, "请先打开一张图片。")
            return
        options = self._read_options()
        if options is None:
            return
        try:
            result = prepare_image(self.original, options)
            copy_image_to_clipboard(result)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"复制失败：{exc}")
            return
        self.status_var.set(f"已复制编辑结果：{result.width} x {result.height}")

    @staticmethod
    def _unique_export_path(folder: Path, stem: str, extension: str) -> Path:
        return unique_export_path(folder, stem, extension)

    def _read_filename_affixes(self) -> tuple[str, str] | None:
        try:
            prefix = validate_filename_affix("文件前缀", self.filename_prefix_var.get())
            suffix = validate_filename_affix("文件后缀", self.filename_suffix_var.get())
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
        clean_prefix = validate_filename_affix(
            "文件前缀", self.filename_prefix_var.get() if prefix is None else prefix
        )
        clean_suffix = validate_filename_affix(
            "文件后缀", self.filename_suffix_var.get() if suffix is None else suffix
        )
        return f"{clean_prefix}{stem}{clean_suffix or fallback_suffix}"

    def batch_process(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择要批量处理的图片",
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"),
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
        affixes = self._read_filename_affixes()
        if affixes is None:
            return
        files = iter_image_files(paths)
        if not files:
            messagebox.showinfo(APP_NAME, "没有找到可处理的图片。")
            return
        self._save_config()
        self.status_var.set(f"正在批量处理 {len(files)} 张图片...")
        self._open_batch_progress(len(files))
        threading.Thread(
            target=self._run_batch,
            args=(files, Path(output_dir), options, source_root, affixes),
            daemon=True,
        ).start()

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
        if self.original is None:
            return None
        return (
            self.original.size,
            self.rotation,
            self.flip_h,
            self.flip_v,
            self.brightness_var.get(),
            self.contrast_var.get(),
            self.saturation_var.get(),
            self.sharpness_var.get(),
            self.grayscale_var.get(),
            self.auto_contrast_var.get(),
            self.crop_box,
            self.crop_ratio_var.get(),
            self.width_var.get(),
            self.height_var.get(),
            self.max_side_var.get(),
            self.format_var.get(),
            self.quality_var.get(),
            self.target_size_var.get(),
            self.filename_prefix_var.get(),
            self.filename_suffix_var.get(),
            self.watermark_text_var.get(),
            self.watermark_position_var.get(),
            self.watermark_opacity_var.get(),
            self.watermark_size_var.get(),
        )

    def _mark_clean(self) -> None:
        self.clean_signature = self._state_signature()

    def has_unsaved_changes(self) -> bool:
        return self.original is not None and self.clean_signature != self._state_signature()

    def _confirm_discard_changes(self) -> bool:
        if not self.has_unsaved_changes():
            return True
        return messagebox.askyesno(APP_NAME, "当前图片有未保存的修改。要放弃这些修改吗？")

    def close(self) -> None:
        if self._confirm_discard_changes():
            self.destroy()

    def _snapshot(self, include_image: bool = False) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "rotation": self.rotation,
            "flip_h": self.flip_h,
            "flip_v": self.flip_v,
            "brightness": self.brightness_var.get(),
            "contrast": self.contrast_var.get(),
            "saturation": self.saturation_var.get(),
            "sharpness": self.sharpness_var.get(),
            "grayscale": self.grayscale_var.get(),
            "auto_contrast": self.auto_contrast_var.get(),
            "crop_box": self.crop_box,
            "crop_ratio": self.crop_ratio_var.get(),
            "width": self.width_var.get(),
            "height": self.height_var.get(),
            "max_side": self.max_side_var.get(),
            "format": self.format_var.get(),
            "quality": self.quality_var.get(),
            "target_size_kb": self.target_size_var.get(),
            "filename_prefix": self.filename_prefix_var.get(),
            "filename_suffix": self.filename_suffix_var.get(),
            "watermark_text": self.watermark_text_var.get(),
            "watermark_position": self.watermark_position_var.get(),
            "watermark_opacity": self.watermark_opacity_var.get(),
            "watermark_size_percent": self.watermark_size_var.get(),
        }
        if include_image and self.original is not None:
            snapshot["original_image"] = self.original.copy()
            snapshot["image_path"] = self.image_path
        return snapshot

    def _restore_snapshot(self, snapshot: dict[str, object]) -> None:
        self._restoring_history = True
        try:
            if "original_image" in snapshot:
                self.original = cast(Image.Image, snapshot["original_image"]).copy()
                self.image_path = cast(Path | None, snapshot.get("image_path"))
            self.rotation = int(cast(int, snapshot["rotation"]))
            self.flip_h = bool(snapshot["flip_h"])
            self.flip_v = bool(snapshot["flip_v"])
            self.brightness_var.set(int(cast(int, snapshot["brightness"])))
            self.contrast_var.set(int(cast(int, snapshot["contrast"])))
            self.saturation_var.set(int(cast(int, snapshot["saturation"])))
            self.sharpness_var.set(int(cast(int, snapshot["sharpness"])))
            self.grayscale_var.set(bool(snapshot["grayscale"]))
            self.auto_contrast_var.set(bool(snapshot["auto_contrast"]))
            self.crop_box = cast(tuple[int, int, int, int] | None, snapshot["crop_box"])
            self.crop_ratio_var.set(str(snapshot["crop_ratio"]))
            self.width_var.set(str(snapshot["width"]))
            self.height_var.set(str(snapshot["height"]))
            self.max_side_var.set(str(snapshot.get("max_side", "")))
            self.format_var.set(str(snapshot["format"]))
            self.quality_var.set(int(cast(int, snapshot["quality"])))
            self.target_size_var.set(str(snapshot.get("target_size_kb", "")))
            self.filename_prefix_var.set(str(snapshot.get("filename_prefix", "")))
            self.filename_suffix_var.set(str(snapshot.get("filename_suffix", "")))
            self.watermark_text_var.set(str(snapshot.get("watermark_text", "")))
            self.watermark_position_var.set(str(snapshot.get("watermark_position", "右下")))
            self.watermark_opacity_var.set(int(cast(int, snapshot.get("watermark_opacity", 35))))
            self.watermark_size_var.set(int(cast(int, snapshot.get("watermark_size_percent", 5))))
        finally:
            self._restoring_history = False
        if self.crop_box:
            self._update_crop_label()
        else:
            self.crop_var.set("未设置裁剪")
        self._refresh_preview()

    def _push_history(self, include_image: bool = False) -> None:
        if self._restoring_history or self.original is None:
            return
        snapshot = self._snapshot(include_image=include_image)
        if self.undo_stack and self.undo_stack[-1] == snapshot:
            return
        self.undo_stack.append(snapshot)
        self.undo_stack = self.undo_stack[-60:]
        self.redo_stack.clear()

    def undo(self) -> None:
        if not self.undo_stack:
            self.status_var.set("没有可撤销的操作")
            return
        self.redo_stack.append(self._snapshot(include_image=True))
        snapshot = self.undo_stack.pop()
        self._restore_snapshot(snapshot)
        self.status_var.set("已撤销一步")

    def redo(self) -> None:
        if not self.redo_stack:
            self.status_var.set("没有可重做的操作")
            return
        self.undo_stack.append(self._snapshot(include_image=True))
        snapshot = self.redo_stack.pop()
        self._restore_snapshot(snapshot)
        self.status_var.set("已重做一步")

    def edit_state(self) -> EditState:
        return EditState(
            rotation=self.rotation,
            flip_horizontal=self.flip_h,
            flip_vertical=self.flip_v,
            brightness=self.brightness_var.get(),
            contrast=self.contrast_var.get(),
            saturation=self.saturation_var.get(),
            sharpness=self.sharpness_var.get(),
            grayscale=self.grayscale_var.get(),
            auto_contrast=self.auto_contrast_var.get(),
        )

    def reset_edits(self, refresh: bool = True) -> None:
        if refresh:
            self._push_history()
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self.brightness_var.set(0)
        self.contrast_var.set(0)
        self.saturation_var.set(0)
        self.sharpness_var.set(0)
        self.grayscale_var.set(False)
        self.auto_contrast_var.set(False)
        if refresh:
            self.status_var.set("已撤销所有快速编辑")
            self._refresh_preview()

    def reset_crop(self) -> None:
        self._push_history()
        self.crop_box = None
        self.crop_var.set("未设置裁剪")
        self._refresh_preview()

    def apply_crop(self) -> None:
        if self.original is None:
            return
        if self.crop_box is None:
            self.status_var.set("没有可应用的裁剪区域")
            return
        self._push_history(include_image=True)
        options = ExportOptions(
            output_format=self.format_var.get(),
            quality=self.quality_var.get(),
            crop_box=self.crop_box,
            edit_state=self.edit_state(),
        )
        self.original = prepare_image(self.original, options)
        self.reset_edits(refresh=False)
        self.crop_box = None
        self.crop_var.set("未设置裁剪")
        self.width_var.set(str(self.original.width))
        self.height_var.set(str(self.original.height))
        self.info_var.set(self._image_info_text())
        self.status_var.set("已应用裁剪，可继续编辑")
        self._refresh_preview()

    def center_crop(self) -> None:
        image = self.base_preview_image or self._current_working_image()
        if image is None:
            return
        self._push_history()
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
        self._push_history()
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
        self._update_crop_label()
        self._refresh_preview()

    def rotate(self, degrees: int) -> None:
        if self.original is None:
            return
        self._push_history()
        self.rotation = (self.rotation + degrees) % 360
        self.crop_box = None
        self.status_var.set(f"已旋转到 {self.rotation}°")
        self._refresh_preview()

    def flip_horizontal(self) -> None:
        if self.original is None:
            return
        self._push_history()
        self.flip_h = not self.flip_h
        self.crop_box = None
        self.status_var.set("已切换水平翻转")
        self._refresh_preview()

    def flip_vertical(self) -> None:
        if self.original is None:
            return
        self._push_history()
        self.flip_v = not self.flip_v
        self.crop_box = None
        self.status_var.set("已切换垂直翻转")
        self._refresh_preview()

    def apply_size_preset(self, value: str) -> None:
        source = self._current_working_image()
        if source is None:
            return
        self._push_history()
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
        try:
            width = int(self.width_var.get()) if self.width_var.get().strip() else None
            height = int(self.height_var.get()) if self.height_var.get().strip() else None
            max_side = int(self.max_side_var.get()) if self.max_side_var.get().strip() else None
            target_size_kb = (
                int(self.target_size_var.get()) if self.target_size_var.get().strip() else None
            )
        except ValueError:
            if not quiet:
                messagebox.showerror(APP_NAME, "宽度、高度、最长边和目标 KB 必须是数字。")
            return None
        if (width is not None and width <= 0) or (height is not None and height <= 0):
            if not quiet:
                messagebox.showerror(APP_NAME, "宽度和高度必须大于 0。")
            return None
        if max_side is not None and max_side <= 0:
            if not quiet:
                messagebox.showerror(APP_NAME, "最长边必须大于 0。")
            return None
        if max_side is not None and (width is not None or height is not None):
            if not quiet:
                messagebox.showerror(APP_NAME, "最长边会自动保持比例，不能和宽度或高度同时填写。")
            return None
        if target_size_kb is not None and target_size_kb <= 0:
            if not quiet:
                messagebox.showerror(APP_NAME, "目标 KB 必须大于 0。")
            return None
        output_format = self.format_var.get()
        if target_size_kb is not None and output_format not in {"JPEG", "WEBP"}:
            if not quiet:
                messagebox.showerror(APP_NAME, "目标大小自动压缩仅适用于 JPEG 和 WEBP。")
            return None
        watermark_opacity = self.watermark_opacity_var.get()
        watermark_size = self.watermark_size_var.get()
        if not 0 <= watermark_opacity <= 100:
            if not quiet:
                messagebox.showerror(APP_NAME, "水印透明度必须在 0 到 100 之间。")
            return None
        if not 1 <= watermark_size <= 30:
            if not quiet:
                messagebox.showerror(APP_NAME, "水印字号必须在 1 到 30 之间。")
            return None
        if not allow_without_image and self.original is None:
            if not quiet:
                messagebox.showinfo(APP_NAME, "请先打开一张图片。")
            return None
        return ExportOptions(
            output_format=output_format,
            quality=self.quality_var.get(),
            target_size_kb=target_size_kb,
            max_width=max_side if max_side is not None else (width if height is None else None),
            max_height=max_side if max_side is not None else (height if width is None else None),
            exact_width=width if height is not None else None,
            exact_height=height if width is not None else None,
            crop_box=self.crop_box,
            edit_state=self.edit_state(),
            watermark_text=self.watermark_text_var.get().strip(),
            watermark_position=self.watermark_position_var.get(),
            watermark_opacity=watermark_opacity,
            watermark_size_percent=watermark_size,
        )

    def _current_working_image(self) -> Image.Image | None:
        if self.original is None:
            return None
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

    def _history_live_adjust(self) -> None:
        self._push_history()
        self._live_adjust()

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
        working = (
            self.original.copy()
            if self.compare_original.get() and self.original is not None
            else self._current_working_image()
        )
        if working is None:
            return
        self.base_preview_image = working
        canvas_w = max(100, self.canvas.winfo_width())
        canvas_h = max(100, self.canvas.winfo_height())
        if self.zoom_mode.get() == "适应":
            scale = min(canvas_w / working.width, canvas_h / working.height, 1.0)
        else:
            scale = int(self.zoom_mode.get().replace("%", "")) / 100
        preview_size = (max(1, int(working.width * scale)), max(1, int(working.height * scale)))
        image = working.resize(preview_size, Image.Resampling.LANCZOS)
        self.preview = ImageTk.PhotoImage(image)
        self.preview_scale = image.width / working.width
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
        self._draw_checker_bounds(image.width, image.height)
        self._draw_crop()
        self._update_estimate()

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

    def _start_crop(self, event: tk.Event) -> None:
        if self.base_preview_image is None:
            return
        self._push_history()
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
        self._draw_crop()
        self._update_crop_label()

    def _finish_crop(self, _event: tk.Event) -> None:
        if self.crop_box is None:
            return
        left, top, right, bottom = self.crop_box
        if right - left < 4 or bottom - top < 4:
            self.crop_box = None
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
        value = self.crop_ratio_var.get()
        if value == "自由":
            return None
        width, height = value.split(":")
        return int(width) / int(height)

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
