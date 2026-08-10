"""Searchable command palette backed by the machine-readable feature registry."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from tkinter import ttk

from ..accessibility import set_accessible_name
from ..shell import FeatureDefinition, FeatureRegistry, ModeId
from ..theme import ThemeTokens
from ..windows_effects import RoundedWindowEffect
from .material import MaterialButton, MaterialSearchField

MODE_DISPLAY_LABELS: dict[ModeId | None, str] = {
    None: "全局",
    ModeId.VIEWER: "看图",
    ModeId.CONVERT: "转图",
    ModeId.EDIT: "改图",
    ModeId.GENERATE: "生图",
}


class FeaturePaletteScope(StrEnum):
    ALL = "全部功能"
    CURRENT = "当前模式"
    GLOBAL = "全局功能"
    VIEWER = "看图模式"
    CONVERT = "转图模式"
    EDIT = "改图模式"
    GENERATE = "生图模式"


_SCOPE_MODE = {
    FeaturePaletteScope.VIEWER: ModeId.VIEWER,
    FeaturePaletteScope.CONVERT: ModeId.CONVERT,
    FeaturePaletteScope.EDIT: ModeId.EDIT,
    FeaturePaletteScope.GENERATE: ModeId.GENERATE,
}


def visible_entry_label(feature: FeatureDefinition) -> str:
    """Turn a registry entry path into concise Chinese UI copy."""

    entry = feature.visible_entry
    if entry.startswith("menu.file"):
        return "文件菜单"
    if entry.startswith("menu.edit"):
        return "编辑菜单"
    if entry.startswith("menu.view"):
        return "视图菜单"
    if entry.startswith("menu.info"):
        return "信息菜单"
    if entry.startswith("dock."):
        return "底部命令栏"
    if entry.startswith("shell.task"):
        return "顶部任务托盘"
    if entry.startswith("sidebar."):
        return "资源库"
    if entry.startswith("canvas."):
        return "画布工具"
    if entry.startswith("workspace.convert"):
        return "转图队列"
    if entry.startswith("panel.convert"):
        return "转图参数面板"
    if entry.startswith("panel.edit.smart"):
        return "改图智能面板"
    if entry.startswith("panel.edit.annotation"):
        return "改图标注面板"
    if entry.startswith("panel.edit.history"):
        return "改图历史面板"
    if entry.startswith("panel.edit.adjust"):
        return "改图调整面板"
    if entry.startswith("mode.generate"):
        return "生图工作区"
    if entry.startswith("mode.viewer") or entry.startswith("viewer."):
        return "看图工作区"
    mode_label = MODE_DISPLAY_LABELS[feature.mode]
    return f"{mode_label}工作区" if feature.mode is not None else "全局界面"


@dataclass(frozen=True, slots=True)
class FeaturePaletteItem:
    feature: FeatureDefinition
    entry_label: str
    registry_index: int

    @property
    def feature_id(self) -> str:
        return self.feature.id

    @property
    def mode_label(self) -> str:
        return MODE_DISPLAY_LABELS[self.feature.mode]

    @property
    def search_text(self) -> str:
        return " ".join(
            (
                self.feature.label,
                self.mode_label,
                self.entry_label,
                self.feature.description,
                self.feature.shortcut or "",
                self.feature.id,
                self.feature.visible_entry,
            )
        ).casefold()


def build_feature_palette_items(
    registry: FeatureRegistry,
) -> tuple[FeaturePaletteItem, ...]:
    return tuple(
        FeaturePaletteItem(feature, visible_entry_label(feature), index)
        for index, feature in enumerate(registry)
    )


def filter_feature_palette_items(
    items: Iterable[FeaturePaletteItem],
    *,
    query: str = "",
    scope: FeaturePaletteScope | str = FeaturePaletteScope.ALL,
    active_mode: ModeId = ModeId.VIEWER,
) -> tuple[FeaturePaletteItem, ...]:
    """Filter registry items with stable ranking and current-mode awareness."""

    try:
        parsed_scope = (
            scope if isinstance(scope, FeaturePaletteScope) else FeaturePaletteScope(scope)
        )
    except ValueError:
        parsed_scope = FeaturePaletteScope.ALL
    tokens = tuple(token.casefold() for token in query.split() if token.strip())

    def in_scope(item: FeaturePaletteItem) -> bool:
        mode = item.feature.mode
        if parsed_scope is FeaturePaletteScope.ALL:
            return True
        if parsed_scope is FeaturePaletteScope.CURRENT:
            return mode is None or mode is active_mode
        if parsed_scope is FeaturePaletteScope.GLOBAL:
            return mode is None
        return mode is _SCOPE_MODE[parsed_scope]

    matches = [
        item
        for item in items
        if in_scope(item) and all(token in item.search_text for token in tokens)
    ]
    if not tokens:
        return tuple(matches)

    query_text = " ".join(tokens)

    def rank(item: FeaturePaletteItem) -> tuple[int, int, int]:
        label = item.feature.label.casefold()
        shortcut = (item.feature.shortcut or "").casefold()
        if label == query_text:
            match_rank = 0
        elif label.startswith(query_text):
            match_rank = 1
        elif shortcut == query_text:
            match_rank = 2
        elif query_text in label:
            match_rank = 3
        else:
            match_rank = 4
        mode_rank = 0 if item.feature.mode is active_mode else 1 if item.feature.mode is None else 2
        return match_rank, mode_rank, item.registry_index

    return tuple(sorted(matches, key=rank))


class FeatureCommandPalette(tk.Toplevel):
    """Material-styled searchable launcher for every registered feature."""

    def __init__(
        self,
        parent: tk.Misc,
        registry: FeatureRegistry,
        *,
        active_mode: ModeId,
        on_invoke: Callable[[str], object],
        on_closed: Callable[[], None] | None = None,
        initial_scope: FeaturePaletteScope | str = FeaturePaletteScope.ALL,
    ) -> None:
        super().__init__(parent)
        self.title("全部功能")
        self.transient(parent)
        self.minsize(620, 420)
        self.active_mode = active_mode
        self.on_invoke = on_invoke
        self.on_closed = on_closed
        self.items = build_feature_palette_items(registry)
        self.filtered_items: tuple[FeaturePaletteItem, ...] = ()
        self._closed = False
        self.query_var = tk.StringVar(self, "")
        try:
            parsed_scope = (
                initial_scope
                if isinstance(initial_scope, FeaturePaletteScope)
                else FeaturePaletteScope(initial_scope)
            )
        except ValueError:
            parsed_scope = FeaturePaletteScope.ALL
        self.scope_var = tk.StringVar(self, parsed_scope.value)
        self.result_var = tk.StringVar(self)
        self.detail_title_var = tk.StringVar(self, "选择一项功能")
        self.detail_var = tk.StringVar(self, "输入关键词，或按模块筛选全部功能。")
        self._build()
        self.rounded_window_effect = RoundedWindowEffect(self, radius=24)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self.bind("<Control-k>", lambda _event: self.focus_search())
        self.query_var.trace_add("write", self._query_changed)
        self._refresh()
        self.after_idle(self.focus_search)

    def _build(self) -> None:
        body = ttk.Frame(self, style="Surface.TFrame", padding=20)
        body.pack(fill="both", expand=True)
        header = ttk.Frame(body, style="Surface.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="全部功能", style="LibraryTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=f"搜索 {len(self.items)} 项功能并直接执行；切换模块时保留当前图片与修订。",
            style="LibraryMeta.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        filters = ttk.Frame(body, style="Surface.TFrame")
        filters.pack(fill="x", pady=(0, 10))
        filters.columnconfigure(0, weight=1)
        self.search_field = MaterialSearchField(filters, textvariable=self.query_var)
        self.search_field.placeholder.configure(text="搜索功能、模块、入口或快捷键")
        self.search_field.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.scope_box = ttk.Combobox(
            filters,
            textvariable=self.scope_var,
            state="readonly",
            values=tuple(scope.value for scope in FeaturePaletteScope),
            width=12,
        )
        self.scope_box.grid(row=0, column=1, sticky="e")
        self.scope_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh())
        set_accessible_name(self.search_field.entry, "搜索全部功能")
        set_accessible_name(self.scope_box, "筛选功能所属模块")

        ttk.Label(body, textvariable=self.result_var, style="LibraryMeta.TLabel").pack(
            fill="x", pady=(0, 6)
        )
        results = ttk.Frame(body, style="Surface.TFrame")
        results.pack(fill="both", expand=True)
        results.rowconfigure(0, weight=1)
        results.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            results,
            columns=("label", "mode", "entry", "shortcut"),
            show="headings",
            selectmode="browse",
            style="FeaturePalette.Treeview",
        )
        self.tree.heading("label", text="功能")
        self.tree.heading("mode", text="模块")
        self.tree.heading("entry", text="界面入口")
        self.tree.heading("shortcut", text="快捷键")
        self.tree.column("label", width=260, minwidth=160, anchor="w")
        self.tree.column("mode", width=72, minwidth=58, anchor="center", stretch=False)
        self.tree.column("entry", width=150, minwidth=110, anchor="w")
        self.tree.column("shortcut", width=96, minwidth=78, anchor="center", stretch=False)
        scroll = ttk.Scrollbar(results, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", self._invoke_selected)
        self.tree.bind("<Return>", self._invoke_selected)
        self.tree.bind("<BackSpace>", self._focus_search_from_tree)
        set_accessible_name(self.tree, "全部功能搜索结果")

        detail = ttk.Frame(body, style="Surface.TFrame", padding=(0, 10, 0, 0))
        detail.pack(fill="x")
        ttk.Label(detail, textvariable=self.detail_title_var, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            detail,
            textvariable=self.detail_var,
            style="LibraryMeta.TLabel",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(2, 8))
        actions = ttk.Frame(detail, style="Surface.TFrame")
        actions.pack(fill="x")
        self.close_button = MaterialButton(actions, text="关闭", width=72, command=self.close)
        self.close_button.pack(side="right")
        self.invoke_button = MaterialButton(
            actions,
            text="执行所选",
            width=96,
            variant="filled",
            command=self._invoke_selected,
        )
        self.invoke_button.pack(side="right", padx=(0, 8))
        set_accessible_name(self.close_button, "关闭全部功能面板")
        set_accessible_name(self.invoke_button, "执行所选功能")

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.configure(background=tokens.surface)
        style = ttk.Style(self)
        style.configure(
            "FeaturePalette.Treeview",
            background=tokens.surface,
            fieldbackground=tokens.surface,
            foreground=tokens.foreground,
            rowheight=32,
        )
        style.map(
            "FeaturePalette.Treeview",
            background=[("selected", tokens.accent)],
            foreground=[("selected", tokens.on_accent)],
        )
        self.tree.tag_configure("disabled", foreground=tokens.muted)
        self.search_field.apply_palette(
            background=tokens.surface,
            surface=tokens.surface_alt,
            border=tokens.border,
            foreground=tokens.foreground,
            muted=tokens.muted,
        )

    def focus_search(self) -> str:
        self.search_field.entry.focus_set()
        self.search_field.entry.selection_range(0, "end")
        return "break"

    def _query_changed(self, *_args: object) -> None:
        self._refresh()

    def _refresh(self) -> None:
        previous = self.tree.selection()[0] if self.tree.selection() else None
        self.filtered_items = filter_feature_palette_items(
            self.items,
            query=self.query_var.get(),
            scope=self.scope_var.get(),
            active_mode=self.active_mode,
        )
        self.tree.delete(*self.tree.get_children())
        for item in self.filtered_items:
            feature = item.feature
            self.tree.insert(
                "",
                "end",
                iid=feature.id,
                values=(
                    feature.label,
                    item.mode_label,
                    item.entry_label,
                    feature.shortcut or "—",
                ),
                tags=() if feature.enabled else ("disabled",),
            )
        count = len(self.filtered_items)
        self.result_var.set(
            f"找到 {count} 项 · 双击或按 Enter 执行"
            if count
            else "没有匹配的功能，请换一个关键词或筛选范围"
        )
        selected = previous if previous and self.tree.exists(previous) else None
        if selected is None and self.filtered_items:
            selected = self.filtered_items[0].feature.id
        if selected is not None:
            self.tree.selection_set(selected)
            self.tree.focus(selected)
            self.tree.see(selected)
        else:
            self.detail_title_var.set("没有匹配结果")
            self.detail_var.set("清空搜索词或切换到“全部功能”后再试。")
            self.invoke_button.configure(state="disabled")
        self._selection_changed()

    def _selected_item(self) -> FeaturePaletteItem | None:
        selection = self.tree.selection()
        if not selection:
            return None
        feature_id = selection[0]
        return next(
            (item for item in self.filtered_items if item.feature.id == feature_id),
            None,
        )

    def _selection_changed(self, _event: tk.Event | None = None) -> None:
        item = self._selected_item()
        if item is None:
            self.invoke_button.configure(state="disabled")
            return
        feature = item.feature
        self.detail_title_var.set(f"{feature.label} · {item.mode_label}")
        status = "可直接执行" if feature.enabled else "暂不可用"
        shortcut = f" · 快捷键 {feature.shortcut}" if feature.shortcut else ""
        description = feature.description or "通过现有界面回调执行，不会复制或改写业务逻辑。"
        self.detail_var.set(f"{item.entry_label} · {status}{shortcut}\n{description}")
        self.invoke_button.configure(state="normal" if feature.enabled else "disabled")

    def _invoke_selected(self, _event: tk.Event | None = None) -> str:
        item = self._selected_item()
        if item is None or not item.feature.enabled:
            return "break"
        feature_id = item.feature.id
        self.close()
        self.on_invoke(feature_id)
        return "break"

    def _focus_search_from_tree(self, _event: tk.Event) -> str:
        self.focus_search()
        current = self.query_var.get()
        if current:
            self.query_var.set(current[:-1])
        return "break"

    def close(self) -> str:
        if self._closed:
            return "break"
        self._closed = True
        try:
            self.destroy()
        finally:
            if self.on_closed is not None:
                self.on_closed()
        return "break"
