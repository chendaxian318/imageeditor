"""Reusable conversion input queue for the conversion workspace."""

from __future__ import annotations

import os
import tkinter as tk
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from tkinter import ttk

from ..domain.conversion_jobs import (
    AssetReference,
    ConversionInput,
    ConversionQueue,
    RevisionReference,
)
from ..theme import ThemeTokens
from .material import MaterialButton

QueueChangedCallback = Callable[[tuple[ConversionInput, ...]], object]
ActivateCallback = Callable[[Path], object]
PathValue = str | os.PathLike[str]
FileRequestCallback = Callable[[], Iterable[PathValue] | PathValue | None]
FolderRequestCallback = Callable[[], PathValue | None]
RetryTasksCallback = Callable[[tuple[object, ...]], object]
ResultCallback = Callable[[Path], object]

_NO_SELECTION = object()
_STATUS_LABELS = {
    "pending": "等待",
    "running": "处理中",
    "succeeded": "成功",
    "failed": "失败",
    "cancelled": "取消",
    "canceled": "取消",
    "skipped": "跳过",
}
_TASK_FILTERS: dict[str, frozenset[str] | None] = {
    "全部": None,
    "处理中": frozenset({"running"}),
    "成功": frozenset({"succeeded"}),
    "失败": frozenset({"failed"}),
    "取消": frozenset({"cancelled", "canceled"}),
}


def _path_key(path: PathValue) -> str:
    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
    return os.path.normcase(str(candidate)).casefold()


def _status_value(record: object) -> str:
    value = getattr(record, "status", "pending")
    value = getattr(value, "value", value)
    return str(value).strip().casefold()


def _record_path(record: object) -> Path | None:
    conversion_input = getattr(record, "input", None)
    value = getattr(conversion_input, "path", None)
    if value is None:
        value = getattr(record, "source", None)
    if value is None:
        value = getattr(record, "source_path", None)
    try:
        return None if value is None else Path(value)
    except TypeError:
        return None


def _record_output(record: object) -> Path | None:
    value = getattr(record, "output", None)
    if value is None:
        value = getattr(record, "planned_output", None)
    try:
        return None if value is None else Path(value)
    except TypeError:
        return None


def _record_result(record: object) -> Path | None:
    """Return an actual successful output, never a merely planned destination."""

    if _status_value(record) != "succeeded":
        return None
    value = getattr(record, "output", None)
    try:
        return None if value is None else Path(value)
    except TypeError:
        return None


class ConversionWorkspace(ttk.Frame):
    """Text-only, scalable input queue that can be embedded in the main shell.

    The widget deliberately owns no worker threads and opens no dialogs. The host
    may supply callbacks for the two add buttons, while tests and integrations can
    call :meth:`add_files` and :meth:`add_folder` directly.
    """

    def __init__(
        self,
        master: tk.Misc,
        on_activate: ActivateCallback | None = None,
        *,
        queue: ConversionQueue | None = None,
        on_queue_changed: QueueChangedCallback | None = None,
        on_request_files: FileRequestCallback | None = None,
        on_request_folder: FolderRequestCallback | None = None,
        tokens: ThemeTokens | None = None,
        width: int = 286,
        on_retry_tasks: RetryTasksCallback | None = None,
        on_open_result: ResultCallback | None = None,
        on_reveal_result: ResultCallback | None = None,
    ) -> None:
        self._style_prefix = f"ConversionWorkspace{id(self):x}"
        self._frame_style = f"{self._style_prefix}.TFrame"
        self._label_style = f"{self._style_prefix}.TLabel"
        self._muted_label_style = f"{self._style_prefix}.Muted.TLabel"
        self._tree_style = f"{self._style_prefix}.Treeview"
        super().__init__(master, style=self._frame_style, padding=(12, 12, 8, 10), width=width)
        self.grid_propagate(False)

        self.queue = queue if queue is not None else ConversionQueue()
        self.on_activate = on_activate
        self.on_queue_changed = on_queue_changed
        self.on_request_files = on_request_files
        self.on_request_folder = on_request_folder
        self.on_retry_tasks = on_retry_tasks
        self.on_open_result = on_open_result
        self.on_reveal_result = on_reveal_result
        self._iid_paths: dict[str, Path] = {}
        self._path_iids: dict[str, str] = {}
        self._task_records: tuple[object, ...] = ()
        self._task_status_by_path: dict[str, str] = {}
        self._task_iids: list[str] = []
        self._task_records_by_iid: dict[str, object] = {}
        self._tokens: ThemeTokens | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style=self._frame_style)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="转换队列", style=self._label_style).grid(
            row=0, column=0, sticky="w"
        )
        self.queue_summary_var = tk.StringVar(value="尚未添加图片")
        ttk.Label(
            header,
            textvariable=self.queue_summary_var,
            style=self._muted_label_style,
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        add_actions = ttk.Frame(self, style=self._frame_style)
        add_actions.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        add_actions.columnconfigure((0, 1), weight=1, uniform="conversion-add")
        self.add_files_button = MaterialButton(
            add_actions,
            text="添加文件",
            command=self.request_files,
            width=88,
            height=34,
            variant="filled",
        )
        self.add_files_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.add_folder_button = MaterialButton(
            add_actions,
            text="添加文件夹",
            command=self.request_folder,
            width=88,
            height=34,
            variant="tonal",
        )
        self.add_folder_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        list_shell = ttk.Frame(self, style=self._frame_style)
        list_shell.grid(row=2, column=0, sticky="nsew")
        list_shell.columnconfigure(0, weight=1)
        list_shell.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            list_shell,
            columns=("enabled", "status", "name"),
            displaycolumns=("enabled", "status", "name"),
            show="headings",
            selectmode="browse",
            style=self._tree_style,
            takefocus=True,
        )
        self.tree.heading("enabled", text="使用")
        self.tree.heading("status", text="状态")
        self.tree.heading("name", text="文件")
        self.tree.column("enabled", width=46, minwidth=42, stretch=False, anchor="center")
        self.tree.column("status", width=58, minwidth=52, stretch=False, anchor="center")
        self.tree.column("name", width=145, minwidth=80, stretch=True, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(
            list_shell,
            orient="vertical",
            command=self.tree.yview,
        )
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", self._activate_from_event)
        self.tree.bind("<Return>", self._activate_from_event)
        self.tree.bind("<space>", self._toggle_from_event)
        self.tree.bind("<Delete>", self._remove_from_event)

        self.selection_var = tk.StringVar(value="未选择文件")
        ttk.Label(
            self,
            textvariable=self.selection_var,
            style=self._muted_label_style,
            wraplength=max(160, width - 28),
        ).grid(row=3, column=0, sticky="ew", pady=(7, 7))

        item_actions = ttk.Frame(self, style=self._frame_style)
        item_actions.grid(row=4, column=0, sticky="ew")
        item_actions.columnconfigure((0, 1, 2), weight=1, uniform="conversion-item")
        self.toggle_button = MaterialButton(
            item_actions,
            text="排除所选",
            command=self.toggle_selected,
            width=60,
            height=32,
            variant="tonal",
        )
        self.toggle_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.remove_button = MaterialButton(
            item_actions,
            text="移除所选",
            command=self.remove_selected,
            width=60,
            height=32,
            variant="tonal",
        )
        self.remove_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.clear_button = MaterialButton(
            item_actions,
            text="清空队列",
            command=self.clear,
            width=60,
            height=32,
            variant="tonal",
        )
        self.clear_button.grid(row=0, column=2, sticky="ew", padx=(3, 0))

        ttk.Separator(self).grid(row=5, column=0, sticky="ew", pady=(11, 9))
        task_header = ttk.Frame(self, style=self._frame_style)
        task_header.grid(row=6, column=0, sticky="ew")
        task_header.columnconfigure(0, weight=1)
        ttk.Label(task_header, text="任务结果", style=self._label_style).grid(
            row=0, column=0, sticky="w"
        )
        self.task_filter_var = tk.StringVar(value="全部")
        self.task_filter_box = ttk.Combobox(
            task_header,
            textvariable=self.task_filter_var,
            values=tuple(_TASK_FILTERS),
            state="readonly",
            width=8,
            takefocus=True,
        )
        self.task_filter_box.grid(row=0, column=1, sticky="e")
        self.task_filter_box.bind("<<ComboboxSelected>>", self._task_filter_changed)

        task_list_shell = ttk.Frame(self, style=self._frame_style)
        task_list_shell.grid(row=7, column=0, sticky="ew", pady=(7, 0))
        task_list_shell.columnconfigure(0, weight=1)
        self.task_tree = ttk.Treeview(
            task_list_shell,
            columns=("status", "name"),
            displaycolumns=("status", "name"),
            show="headings",
            selectmode="browse",
            style=self._tree_style,
            height=5,
            takefocus=True,
        )
        self.task_tree.heading("status", text="状态")
        self.task_tree.heading("name", text="任务")
        self.task_tree.column("status", width=62, minwidth=54, stretch=False, anchor="center")
        self.task_tree.column("name", width=170, minwidth=80, stretch=True, anchor="w")
        self.task_tree.grid(row=0, column=0, sticky="ew")
        self.task_scrollbar = ttk.Scrollbar(
            task_list_shell,
            orient="vertical",
            command=self.task_tree.yview,
        )
        self.task_scrollbar.grid(row=0, column=1, sticky="ns")
        self.task_tree.configure(yscrollcommand=self.task_scrollbar.set)
        self.task_tree.bind("<<TreeviewSelect>>", self._task_selection_changed)
        self.task_tree.bind("<Double-1>", self._open_task_from_event)
        self.task_tree.bind("<Return>", self._open_task_from_event)

        task_actions = ttk.Frame(self, style=self._frame_style)
        task_actions.grid(row=8, column=0, sticky="ew", pady=(7, 0))
        task_actions.columnconfigure((0, 1, 2), weight=1, uniform="conversion-task")
        self.retry_task_button = MaterialButton(
            task_actions,
            text="重试任务",
            command=self.retry_selected_task,
            width=60,
            height=32,
            variant="tonal",
        )
        self.retry_task_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.open_result_button = MaterialButton(
            task_actions,
            text="打开结果",
            command=self.open_selected_result,
            width=60,
            height=32,
            variant="tonal",
        )
        self.open_result_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.reveal_result_button = MaterialButton(
            task_actions,
            text="定位结果",
            command=self.reveal_selected_result,
            width=60,
            height=32,
            variant="tonal",
        )
        self.reveal_result_button.grid(row=0, column=2, sticky="ew", padx=(3, 0))

        self.task_summary_var = tk.StringVar(value="尚未执行任务")
        self.task_result_var = tk.StringVar(value="结果将在转换开始后显示")
        self.task_detail_var = tk.StringVar(value="")
        ttk.Label(
            self,
            textvariable=self.task_summary_var,
            style=self._muted_label_style,
        ).grid(row=9, column=0, sticky="w", pady=(7, 0))
        ttk.Label(
            self,
            textvariable=self.task_result_var,
            style=self._muted_label_style,
        ).grid(row=10, column=0, sticky="w", pady=(2, 0))
        ttk.Label(
            self,
            textvariable=self.task_detail_var,
            style=self._muted_label_style,
            wraplength=max(160, width - 28),
        ).grid(row=11, column=0, sticky="ew", pady=(2, 0))

        self._material_action_buttons = (
            self.add_files_button,
            self.add_folder_button,
            self.toggle_button,
            self.remove_button,
            self.clear_button,
            self.retry_task_button,
            self.open_result_button,
            self.reveal_result_button,
        )

        self._refresh_tree()
        self._refresh_task_tree()
        if tokens is not None:
            self.apply_tokens(tokens)

    @property
    def items(self) -> tuple[ConversionInput, ...]:
        return self.queue.items

    @property
    def selected_path(self) -> Path | None:
        selection = self.tree.selection()
        return self._iid_paths.get(selection[0]) if selection else None

    @property
    def selected_input(self) -> ConversionInput | None:
        path = self.selected_path
        return None if path is None else self.queue.get(path)

    @property
    def current_selection(self) -> Path | None:
        """Alias used by shell integrations that expose a current asset."""

        return self.selected_path

    def set_queue(self, queue: ConversionQueue, *, notify: bool = False) -> None:
        """Replace the queue from a validated recovery snapshot."""

        if not isinstance(queue, ConversionQueue):
            raise TypeError("queue 必须是 ConversionQueue")
        self.queue = queue
        self._task_records = ()
        self._task_status_by_path.clear()
        self._refresh_tree(selected=None)
        self._refresh_task_tree()
        if notify and self.on_queue_changed is not None:
            self.on_queue_changed(self.queue.items)

    @property
    def task_records(self) -> tuple[object, ...]:
        return self._task_records

    @property
    def selected_task(self) -> object | None:
        selection = self.task_tree.selection()
        return self._task_records_by_iid.get(selection[0]) if selection else None

    @property
    def filtered_task_records(self) -> tuple[object, ...]:
        statuses = _TASK_FILTERS[self.task_filter_var.get()]
        if statuses is None:
            return self._task_records
        return tuple(record for record in self._task_records if _status_value(record) in statuses)

    def request_files(self) -> tuple[ConversionInput, ...]:
        """Ask the host for paths; this widget never opens its own dialog."""

        if self.on_request_files is None:
            return ()
        paths = self.on_request_files()
        if paths is None:
            return ()
        return self.add_files(paths)

    def request_folder(self) -> tuple[ConversionInput, ...]:
        """Ask the host for a folder; this widget never opens its own dialog."""

        if self.on_request_folder is None:
            return ()
        folder = self.on_request_folder()
        if folder is None:
            return ()
        return self.add_folder(folder)

    def add_files(
        self,
        paths: Iterable[PathValue] | PathValue,
    ) -> tuple[ConversionInput, ...]:
        if isinstance(paths, (str, os.PathLike)):
            paths = (paths,)
        added = self.queue.add_files(paths)
        if added:
            self._queue_mutated(selected=added[0].path)
        return added

    def add_folder(
        self,
        folder: PathValue,
        *,
        recursive: bool = True,
    ) -> tuple[ConversionInput, ...]:
        added = self.queue.add_folder(folder, recursive=recursive)
        if added:
            self._queue_mutated(selected=added[0].path)
        return added

    def add_paths(
        self,
        paths: Iterable[PathValue] | PathValue,
        *,
        recursive: bool = True,
    ) -> tuple[ConversionInput, ...]:
        if isinstance(paths, (str, os.PathLike)):
            paths = (paths,)
        added = self.queue.add_paths(paths, recursive=recursive)
        if added:
            self._queue_mutated(selected=added[0].path)
        return added

    def add_handoff(
        self,
        path: PathValue,
        asset: AssetReference,
        revision: RevisionReference | None = None,
    ) -> ConversionInput | None:
        """Add a cross-mode asset or enrich an existing path with its identity."""

        added = self.queue.add_file(path, asset=asset, revision=revision)
        selected = added.path if added is not None else Path(path)
        if added is not None:
            self._queue_mutated(selected=selected)
        else:
            current = self.queue.get(path)
            if current is not None:
                enriched = self.queue.attach_identity(
                    path,
                    asset=asset,
                    revision=revision,
                )
                if enriched is not current:
                    if self.on_queue_changed is not None:
                        self.on_queue_changed(self.queue.items)
                    self.select(enriched.path)
                    return enriched
            self.select(selected)
        return added

    def select(self, path: PathValue | None, *, reveal: bool = True) -> bool:
        if path is None:
            self.tree.selection_remove(self.tree.selection())
            self._selection_changed()
            return False
        key = _path_key(path)
        iid = self._path_iids.get(key)
        if iid is None or not self.tree.exists(iid):
            return False
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        if reveal:
            self.tree.see(iid)
        self._selection_changed()
        return True

    def set_enabled(self, path: PathValue, *, enabled: bool) -> ConversionInput:
        item = self.queue.set_enabled(path, enabled=enabled)
        iid = self._path_iids.get(_path_key(item.path))
        if iid is None or not self.tree.exists(iid):
            self._queue_mutated(selected=item.path)
            return item
        self._update_input_row(iid, item)
        total = len(self.queue)
        self.queue_summary_var.set(f"共 {total} 项 · 已启用 {len(self.queue.enabled_items)} 项")
        self.select(item.path)
        if self.on_queue_changed is not None:
            self.on_queue_changed(self.queue.items)
        return item

    def toggle_selected(self) -> ConversionInput | None:
        item = self.selected_input
        if item is None:
            return None
        return self.set_enabled(item.path, enabled=not item.enabled)

    def remove(self, path: PathValue) -> ConversionInput | None:
        removed = self.queue.remove(path)
        if removed is not None:
            self._queue_mutated(selected=None)
        return removed

    def remove_selected(self) -> ConversionInput | None:
        path = self.selected_path
        return None if path is None else self.remove(path)

    def clear(self) -> tuple[ConversionInput, ...]:
        removed = self.queue.clear()
        if removed:
            self._queue_mutated(selected=None)
        return removed

    def activate_selected(self) -> bool:
        path = self.selected_path
        if path is None or self.on_activate is None:
            return False
        self.on_activate(path)
        return True

    def set_task_records(self, records: Sequence[object] | Iterable[object]) -> None:
        """Display runner records without coupling the widget to a runner thread."""

        self._task_records = tuple(records)
        self._task_status_by_path.clear()
        counts: Counter[str] = Counter()
        for record in self._task_records:
            status = _status_value(record)
            counts[status] += 1
            path = _record_path(record)
            if path is not None:
                self._task_status_by_path[_path_key(path)] = status

        if not self._task_records:
            self.task_summary_var.set("尚未执行任务")
            self.task_result_var.set("结果将在转换开始后显示")
            self.task_detail_var.set("")
            self._refresh_tree()
            self._refresh_task_tree()
            return

        total = len(self._task_records)
        pending = counts["pending"]
        running = counts["running"]
        succeeded = counts["succeeded"]
        failed = counts["failed"]
        cancelled = counts["cancelled"] + counts["canceled"]
        self.task_summary_var.set(f"共 {total} 项 · 等待 {pending} · 处理中 {running}")
        self.task_result_var.set(f"成功 {succeeded} · 失败 {failed} · 取消 {cancelled}")
        self.task_detail_var.set(self._latest_task_detail())
        self._refresh_tree()
        self._refresh_task_tree()

    def set_running(self, running: bool) -> None:
        """Freeze queue mutation controls while a runner owns a queue snapshot."""

        if not isinstance(running, bool):
            raise TypeError("running 必须是布尔值")
        state = ("disabled",) if running else ("!disabled",)
        self.tree.state(state)
        self.add_files_button.state(state)
        self.add_folder_button.state(state)
        if running:
            self.toggle_button.state(("disabled",))
            self.remove_button.state(("disabled",))
            self.clear_button.state(("disabled",))
        else:
            self._selection_changed()
            self.clear_button.state(("!disabled",) if len(self.queue) else ("disabled",))

    def set_task_filter(self, label: str) -> None:
        if label not in _TASK_FILTERS:
            choices = "、".join(_TASK_FILTERS)
            raise ValueError(f"未知任务筛选：{label}；可选值为 {choices}")
        self.task_filter_var.set(label)
        self._apply_task_filter()

    def select_task(self, record: object, *, reveal: bool = True) -> bool:
        visible = set(self.task_tree.get_children())
        for iid, candidate in self._task_records_by_iid.items():
            if candidate is record and iid in visible:
                self.task_tree.selection_set(iid)
                self.task_tree.focus(iid)
                if reveal:
                    self.task_tree.see(iid)
                self._task_selection_changed()
                return True
        return False

    def retry_selected_task(self) -> bool:
        record = self.selected_task
        if (
            record is None
            or _status_value(record) not in {"failed", "cancelled", "canceled"}
            or self.on_retry_tasks is None
        ):
            return False
        self.on_retry_tasks((record,))
        return True

    def open_selected_result(self) -> bool:
        record = self.selected_task
        result = None if record is None else _record_result(record)
        if result is None or self.on_open_result is None:
            return False
        self.on_open_result(result)
        return True

    def reveal_selected_result(self) -> bool:
        record = self.selected_task
        result = None if record is None else _record_result(record)
        if result is None or self.on_reveal_result is None:
            return False
        self.on_reveal_result(result)
        return True

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        """Apply one Material palette without changing application-wide styles."""

        self._tokens = tokens
        style = ttk.Style(self)
        style.configure(self._frame_style, background=tokens.surface)
        style.configure(
            self._label_style,
            background=tokens.surface,
            foreground=tokens.foreground,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.configure(
            self._muted_label_style,
            background=tokens.surface,
            foreground=tokens.muted,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            self._tree_style,
            background=tokens.surface,
            fieldbackground=tokens.surface,
            foreground=tokens.foreground,
            bordercolor=tokens.border,
            rowheight=28,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            f"{self._tree_style}.Heading",
            background=tokens.surface_alt,
            foreground=tokens.foreground,
            bordercolor=tokens.border,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            self._tree_style,
            background=[("selected", tokens.accent)],
            foreground=[("selected", tokens.on_accent)],
        )
        self.tree.tag_configure("excluded", foreground=tokens.muted)
        self.tree.tag_configure("running", foreground=tokens.focus)
        self.tree.tag_configure("succeeded", foreground=tokens.accent)
        self.tree.tag_configure("failed", foreground=tokens.danger)
        self.tree.tag_configure("cancelled", foreground=tokens.muted)
        self.task_tree.tag_configure("running", foreground=tokens.focus)
        self.task_tree.tag_configure("succeeded", foreground=tokens.accent)
        self.task_tree.tag_configure("failed", foreground=tokens.danger)
        self.task_tree.tag_configure("cancelled", foreground=tokens.muted)
        for button in self._material_action_buttons:
            button.apply_palette(
                surface=tokens.surface,
                container=tokens.surface_container,
                hover=tokens.surface_high,
                foreground=tokens.foreground,
                accent=tokens.accent,
                on_accent=tokens.on_accent,
                disabled_container=tokens.surface_alt,
                disabled_foreground=tokens.muted,
                focus=tokens.focus,
                border=tokens.border,
            )

    def apply_palette(self, tokens: ThemeTokens) -> None:
        """Compatibility spelling for hosts that call palette application."""

        self.apply_tokens(tokens)

    def _queue_mutated(self, *, selected: Path | None) -> None:
        self._refresh_tree(selected=selected)
        if self.on_queue_changed is not None:
            self.on_queue_changed(self.queue.items)

    def _refresh_tree(self, *, selected: object = _NO_SELECTION) -> None:
        selected_path = self.selected_path if selected is _NO_SELECTION else selected
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self._iid_paths.clear()
        self._path_iids.clear()
        for index, item in enumerate(self.queue.items):
            iid = f"input-{index}"
            self.tree.insert(
                "",
                "end",
                iid=iid,
            )
            self._iid_paths[iid] = item.path
            self._path_iids[_path_key(item.path)] = iid
            self._update_input_row(iid, item)

        total = len(self.queue)
        enabled = len(self.queue.enabled_items)
        self.queue_summary_var.set(
            "尚未添加图片" if total == 0 else f"共 {total} 项 · 已启用 {enabled} 项"
        )
        self.clear_button.state(("!disabled",) if total else ("disabled",))
        if isinstance(selected_path, (str, os.PathLike)):
            self.select(selected_path)
        else:
            self._selection_changed()

    def _update_input_row(self, iid: str, item: ConversionInput) -> None:
        status = self._task_status_by_path.get(_path_key(item.path), "")
        status_label = _STATUS_LABELS.get(status, status or "—")
        tags: list[str] = []
        if not item.enabled:
            tags.append("excluded")
        if status in {"running", "succeeded", "failed", "cancelled", "canceled"}:
            tags.append("cancelled" if status == "canceled" else status)
        self.tree.item(
            iid,
            values=("启用" if item.enabled else "排除", status_label, item.path.name),
            tags=tuple(tags),
        )

    def _refresh_task_tree(self) -> None:
        previous = self.selected_task
        previous_path = None if previous is None else _record_path(previous)
        for iid in self._task_iids:
            if self.task_tree.exists(iid):
                self.task_tree.delete(iid)
        self._task_iids.clear()
        self._task_records_by_iid.clear()

        for index, record in enumerate(self._task_records):
            iid = f"task-{index}"
            status = _status_value(record)
            label = _STATUS_LABELS.get(status, status or "等待")
            path = _record_path(record)
            name = path.name if path is not None else f"任务 {index + 1}"
            tag = "cancelled" if status == "canceled" else status
            tags = (tag,) if tag in {"running", "succeeded", "failed", "cancelled"} else ()
            self.task_tree.insert(
                "",
                "end",
                iid=iid,
                values=(label, name),
                tags=tags,
            )
            self._task_iids.append(iid)
            self._task_records_by_iid[iid] = record

        self._apply_task_filter()
        if previous_path is not None:
            key = _path_key(previous_path)
            for record in self.filtered_task_records:
                path = _record_path(record)
                if path is not None and _path_key(path) == key:
                    self.select_task(record)
                    break

    def _apply_task_filter(self) -> None:
        statuses = _TASK_FILTERS[self.task_filter_var.get()]
        selected = self.selected_task
        for iid in self.task_tree.get_children():
            self.task_tree.detach(iid)
        for iid in self._task_iids:
            record = self._task_records_by_iid[iid]
            if statuses is None or _status_value(record) in statuses:
                self.task_tree.move(iid, "", "end")
        if selected is not None and not self.select_task(selected, reveal=False):
            self.task_tree.selection_remove(self.task_tree.selection())
        self._task_selection_changed()

    def _task_filter_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._apply_task_filter()

    def _task_selection_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        record = self.selected_task
        status = "" if record is None else _status_value(record)
        result = None if record is None else _record_result(record)
        retry_available = (
            record is not None
            and status in {"failed", "cancelled", "canceled"}
            and self.on_retry_tasks is not None
        )
        open_available = result is not None and self.on_open_result is not None
        reveal_available = result is not None and self.on_reveal_result is not None
        self.retry_task_button.state(("!disabled",) if retry_available else ("disabled",))
        self.open_result_button.state(("!disabled",) if open_available else ("disabled",))
        self.reveal_result_button.state(("!disabled",) if reveal_available else ("disabled",))

    def _selection_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        item = self.selected_input
        has_selection = item is not None
        state = ("!disabled",) if has_selection else ("disabled",)
        self.toggle_button.state(state)
        self.remove_button.state(state)
        if item is None:
            self.selection_var.set("未选择文件")
            self.toggle_button.configure(text="排除所选")
            return
        self.selection_var.set(str(item.path))
        self.toggle_button.configure(text="排除所选" if item.enabled else "启用所选")

    def _latest_task_detail(self) -> str:
        failures = [record for record in self._task_records if _status_value(record) == "failed"]
        if failures:
            record = failures[-1]
            path = _record_path(record)
            error = str(getattr(record, "error", "") or "未知错误")
            name = path.name if path is not None else "未知文件"
            return f"最近失败：{name} · {error}"
        completed = [
            record for record in self._task_records if _status_value(record) == "succeeded"
        ]
        if completed:
            output = _record_output(completed[-1])
            return "最近输出：" + (output.name if output is not None else "已完成")
        return "等待任务完成"

    def _activate_from_event(self, _event: tk.Event[tk.Misc]) -> str:
        self.activate_selected()
        return "break"

    def _toggle_from_event(self, _event: tk.Event[tk.Misc]) -> str:
        self.toggle_selected()
        return "break"

    def _remove_from_event(self, _event: tk.Event[tk.Misc]) -> str:
        self.remove_selected()
        return "break"

    def _open_task_from_event(self, _event: tk.Event[tk.Misc]) -> str:
        self.open_selected_result()
        return "break"


__all__ = ["ConversionWorkspace"]
