"""Unified Material task tray for conversion, smart-edit, and generation jobs."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from tkinter import ttk

from PIL import ImageDraw

from ..shell import ModeId
from ..theme import ThemeTokens
from .antialias import render_photo
from .material import MaterialButton


def _now() -> datetime:
    return datetime.now(UTC)


class UnifiedTaskKind(StrEnum):
    CONVERSION = "conversion"
    SMART_EDIT = "smart_edit"
    GENERATION = "generation"


class UnifiedTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.FAILED,
            self.CANCELLED,
            self.UNKNOWN,
        }


_KIND_LABELS = {
    UnifiedTaskKind.CONVERSION: "转图",
    UnifiedTaskKind.SMART_EDIT: "改图",
    UnifiedTaskKind.GENERATION: "生图",
}
_STATUS_LABELS = {
    UnifiedTaskStatus.PENDING: "等待",
    UnifiedTaskStatus.RUNNING: "处理中",
    UnifiedTaskStatus.SUCCEEDED: "完成",
    UnifiedTaskStatus.FAILED: "失败",
    UnifiedTaskStatus.CANCELLED: "取消",
    UnifiedTaskStatus.UNKNOWN: "未知",
}


@dataclass(frozen=True, slots=True)
class UnifiedTaskItem:
    task_id: str
    kind: UnifiedTaskKind
    title: str
    status: UnifiedTaskStatus
    mode: ModeId
    detail: str = ""
    progress_current: int | None = None
    progress_total: int | None = None
    cancellable: bool = False
    retryable: bool = False
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.title.strip():
            raise ValueError("统一任务编号和标题不能为空")
        object.__setattr__(self, "kind", UnifiedTaskKind(self.kind))
        object.__setattr__(self, "status", UnifiedTaskStatus(self.status))
        object.__setattr__(self, "mode", ModeId(self.mode))
        if (self.progress_current is None) != (self.progress_total is None):
            raise ValueError("任务进度当前值和总数必须同时提供")
        if self.progress_current is not None:
            if (
                isinstance(self.progress_current, bool)
                or isinstance(self.progress_total, bool)
                or not isinstance(self.progress_current, int)
                or not isinstance(self.progress_total, int)
                or self.progress_total < 1
                or not 0 <= self.progress_current <= self.progress_total
            ):
                raise ValueError("统一任务进度无效")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("统一任务时间必须包含时区")
        if self.updated_at < self.created_at:
            raise ValueError("统一任务更新时间不能早于创建时间")
        if self.status.terminal and self.cancellable:
            raise ValueError("已结束任务不能取消")
        if not self.status.terminal and self.retryable:
            raise ValueError("未结束任务不能重试")

    @property
    def progress_label(self) -> str:
        if self.progress_current is None or self.progress_total is None:
            return ""
        return f"{self.progress_current}/{self.progress_total}"

    def to_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "title": self.title,
            "status": self.status.value,
            "mode": self.mode.value,
            "detail": self.detail,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "cancellable": self.cancellable,
            "retryable": self.retryable,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> UnifiedTaskItem:
        expected = {
            "task_id",
            "kind",
            "title",
            "status",
            "mode",
            "detail",
            "progress_current",
            "progress_total",
            "cancellable",
            "retryable",
            "created_at",
            "updated_at",
        }
        if set(payload) != expected:
            raise ValueError("统一任务记录字段不完整或包含未知字段")
        string_fields = (
            "task_id",
            "kind",
            "title",
            "status",
            "mode",
            "detail",
            "created_at",
            "updated_at",
        )
        if any(not isinstance(payload[field], str) for field in string_fields):
            raise ValueError("统一任务文本和时间字段必须是字符串")
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        updated_at = datetime.fromisoformat(str(payload["updated_at"]))
        progress_current = payload["progress_current"]
        progress_total = payload["progress_total"]
        if progress_current is not None and (
            isinstance(progress_current, bool) or not isinstance(progress_current, int)
        ):
            raise ValueError("统一任务当前进度必须是整数或 null")
        if progress_total is not None and (
            isinstance(progress_total, bool) or not isinstance(progress_total, int)
        ):
            raise ValueError("统一任务总进度必须是整数或 null")
        if not isinstance(payload["cancellable"], bool) or not isinstance(
            payload["retryable"], bool
        ):
            raise ValueError("统一任务操作状态必须是布尔值")
        return cls(
            task_id=str(payload["task_id"]),
            kind=UnifiedTaskKind(str(payload["kind"])),
            title=str(payload["title"]),
            status=UnifiedTaskStatus(str(payload["status"])),
            mode=ModeId(str(payload["mode"])),
            detail=str(payload["detail"]),
            progress_current=progress_current,
            progress_total=progress_total,
            cancellable=payload["cancellable"],
            retryable=payload["retryable"],
            created_at=created_at,
            updated_at=updated_at,
        )


class TaskTrayModel:
    """Bounded in-memory projection of persisted and currently active tasks."""

    def __init__(self, items: Iterable[UnifiedTaskItem] = (), *, maximum_items: int = 50) -> None:
        if (
            isinstance(maximum_items, bool)
            or not isinstance(maximum_items, int)
            or maximum_items < 1
        ):
            raise ValueError("统一任务托盘容量必须是正整数")
        self.maximum_items = maximum_items
        self._items: dict[str, UnifiedTaskItem] = {}
        for item in items:
            self.upsert(item)

    @property
    def items(self) -> tuple[UnifiedTaskItem, ...]:
        return tuple(
            sorted(
                self._items.values(),
                key=lambda item: (
                    item.status.terminal,
                    -item.updated_at.timestamp(),
                    item.task_id,
                ),
            )
        )

    @property
    def active_count(self) -> int:
        return sum(not item.status.terminal for item in self._items.values())

    def get(self, task_id: str) -> UnifiedTaskItem | None:
        return self._items.get(task_id)

    def upsert(self, item: UnifiedTaskItem) -> UnifiedTaskItem:
        if not isinstance(item, UnifiedTaskItem):
            raise TypeError("统一任务托盘只能保存 UnifiedTaskItem")
        previous = self._items.get(item.task_id)
        if previous is not None and item.created_at != previous.created_at:
            item = replace(item, created_at=previous.created_at)
        self._items[item.task_id] = item
        self._trim()
        return item

    def replace_all(self, items: Iterable[UnifiedTaskItem]) -> None:
        replacement: dict[str, UnifiedTaskItem] = {}
        for item in items:
            if not isinstance(item, UnifiedTaskItem):
                raise TypeError("统一任务托盘只能保存 UnifiedTaskItem")
            replacement[item.task_id] = item
        self._items = replacement
        self._trim()

    def clear_finished(self) -> int:
        removed = tuple(task_id for task_id, item in self._items.items() if item.status.terminal)
        for task_id in removed:
            self._items.pop(task_id, None)
        return len(removed)

    def _trim(self) -> None:
        if len(self._items) <= self.maximum_items:
            return
        ordered_oldest = sorted(
            self._items.values(),
            key=lambda item: (
                not item.status.terminal,
                item.updated_at,
                item.task_id,
            ),
        )
        for item in ordered_oldest[: len(self._items) - self.maximum_items]:
            self._items.pop(item.task_id, None)


TaskAction = Callable[[UnifiedTaskItem], object]
TaskTrayChanged = Callable[[tuple[UnifiedTaskItem, ...]], object]


class TaskTray(tk.Canvas):
    """Rounded popup task tray with a compact trigger in the application bar."""

    def __init__(
        self,
        master: tk.Misc,
        trigger_parent: tk.Misc,
        *,
        on_activate: TaskAction | None = None,
        on_cancel: TaskAction | None = None,
        on_retry: TaskAction | None = None,
        on_changed: TaskTrayChanged | None = None,
        maximum_items: int = 50,
    ) -> None:
        super().__init__(
            master,
            width=420,
            height=330,
            highlightthickness=0,
            takefocus=True,
        )
        self.model = TaskTrayModel(maximum_items=maximum_items)
        self.on_activate = on_activate
        self.on_cancel = on_cancel
        self.on_retry = on_retry
        self.on_changed = on_changed
        self._visible = False
        self._background = "#ded9df"
        self._surface = "#fffbfe"
        self._border = "#cac4d0"
        self._shadow = "#c4bec8"
        self._foreground = "#1d1b20"
        self._muted = "#79747e"
        self._accent = "#6750a4"
        self._danger = "#b3261e"
        self._task_by_iid: dict[str, UnifiedTaskItem] = {}

        self.trigger_button = MaterialButton(
            trigger_parent,
            text="任务 0",
            command=self.toggle,
            width=82,
            height=32,
            variant="tonal",
        )

        self.content = ttk.Frame(self, style="FloatingBar.TFrame", padding=(15, 13))
        self._content_window = self.create_window(
            210,
            165,
            window=self.content,
            anchor="center",
            width=392,
            height=302,
        )
        self.content.columnconfigure(0, weight=1)
        self.content.columnconfigure(1, weight=0)
        self.content.rowconfigure(1, weight=1)

        header = ttk.Frame(self.content, style="FloatingBar.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 9))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="统一任务", style="Title.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        self.summary_var = tk.StringVar(value="当前没有任务")
        ttk.Label(
            header,
            textvariable=self.summary_var,
            style="Muted.Toolbar.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.close_button = MaterialButton(
            header,
            text="×",
            command=self.hide,
            width=32,
            height=32,
            variant="icon",
        )
        self.close_button.grid(row=0, column=1, rowspan=2, sticky="e")

        self.tree = ttk.Treeview(
            self.content,
            columns=("module", "status", "progress", "title"),
            displaycolumns=("module", "status", "progress", "title"),
            show="headings",
            selectmode="browse",
            height=7,
            takefocus=True,
        )
        self.tree.heading("module", text="模块")
        self.tree.heading("status", text="状态")
        self.tree.heading("progress", text="进度")
        self.tree.heading("title", text="任务")
        self.tree.column("module", width=48, minwidth=44, stretch=False, anchor="center")
        self.tree.column("status", width=62, minwidth=56, stretch=False, anchor="center")
        self.tree.column("progress", width=58, minwidth=48, stretch=False, anchor="center")
        self.tree.column("title", width=190, minwidth=110, stretch=True, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree_scrollbar = ttk.Scrollbar(
            self.content,
            orient="vertical",
            command=self.tree.yview,
        )
        self.tree_scrollbar.grid(row=1, column=1, sticky="ns", padx=(5, 0))
        self.tree.configure(yscrollcommand=self.tree_scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", self._activate_from_event)
        self.tree.bind("<Return>", self._activate_from_event)

        self.detail_var = tk.StringVar(value="选择任务后查看详情")
        ttk.Label(
            self.content,
            textvariable=self.detail_var,
            style="Muted.Toolbar.TLabel",
            wraplength=382,
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 7))

        actions = ttk.Frame(self.content, style="FloatingBar.TFrame")
        actions.grid(row=3, column=0, columnspan=2, sticky="ew")
        actions.columnconfigure((0, 1, 2, 3), weight=1, uniform="task-tray-action")
        self.activate_button = MaterialButton(
            actions,
            text="前往",
            command=self.activate_selected,
            width=72,
            height=32,
            variant="filled",
        )
        self.activate_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.cancel_button = MaterialButton(
            actions,
            text="取消",
            command=self.cancel_selected,
            width=72,
            height=32,
            variant="tonal",
        )
        self.cancel_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.retry_button = MaterialButton(
            actions,
            text="重试",
            command=self.retry_selected,
            width=72,
            height=32,
            variant="tonal",
        )
        self.retry_button.grid(row=0, column=2, sticky="ew", padx=3)
        self.clear_button = MaterialButton(
            actions,
            text="清理完成",
            command=self.clear_finished,
            width=88,
            height=32,
            variant="text",
        )
        self.clear_button.grid(row=0, column=3, sticky="ew", padx=(3, 0))

        self.bind("<Escape>", lambda _event: self.hide())
        self.bind("<Configure>", self._layout)
        self._root_configure_binding = master.bind(
            "<Configure>",
            self._root_configured,
            add="+",
        )
        self._refresh()

    @property
    def items(self) -> tuple[UnifiedTaskItem, ...]:
        return self.model.items

    def get(self, task_id: str) -> UnifiedTaskItem | None:
        return self.model.get(task_id)

    def set_items(
        self,
        items: Iterable[UnifiedTaskItem],
        *,
        notify: bool = True,
    ) -> None:
        self.model.replace_all(items)
        self._refresh()
        if notify:
            self._notify_changed()

    def upsert(self, item: UnifiedTaskItem, *, notify: bool = True) -> None:
        previous = self.model.get(item.task_id)
        stored = self.model.upsert(item)
        self._refresh(selected_task_id=item.task_id)
        if notify and stored != previous:
            self._notify_changed()

    def select_task(self, task_id: str) -> bool:
        for iid, item in self._task_by_iid.items():
            if item.task_id != task_id:
                continue
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
            self._selection_changed()
            return True
        return False

    def selected_item(self) -> UnifiedTaskItem | None:
        selection = self.tree.selection()
        return self._task_by_iid.get(selection[0]) if selection else None

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        self._visible = True
        self._reposition()
        self.tk.call("raise", self._w)
        self.focus_set()

    def hide(self) -> None:
        self._visible = False
        self.place_forget()
        self.trigger_button.focus_set()

    def activate_selected(self) -> bool:
        item = self.selected_item()
        if item is None or self.on_activate is None:
            return False
        self.on_activate(item)
        self.hide()
        return True

    def cancel_selected(self) -> bool:
        item = self.selected_item()
        if item is None or not item.cancellable or self.on_cancel is None:
            return False
        self.on_cancel(item)
        return True

    def retry_selected(self) -> bool:
        item = self.selected_item()
        if item is None or not item.retryable or self.on_retry is None:
            return False
        self.on_retry(item)
        return True

    def clear_finished(self) -> int:
        removed = self.model.clear_finished()
        self._refresh()
        if removed:
            self._notify_changed()
        return removed

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self._background = tokens.surface_container
        self._surface = tokens.surface
        self._border = tokens.border
        self._shadow = tokens.surface_high
        self._foreground = tokens.foreground
        self._muted = tokens.muted
        self._accent = tokens.accent
        self._danger = tokens.danger
        self.configure(background=tokens.surface_container)
        self.tree.tag_configure("running", foreground=tokens.focus)
        self.tree.tag_configure("succeeded", foreground=tokens.accent)
        self.tree.tag_configure("failed", foreground=tokens.danger)
        self.tree.tag_configure("cancelled", foreground=tokens.muted)
        self.tree.tag_configure("unknown", foreground=tokens.danger)
        self._draw_surface()

    def _notify_changed(self) -> None:
        if self.on_changed is not None:
            self.on_changed(self.model.items)

    def _refresh(self, selected_task_id: str | None = None) -> None:
        previous = self.selected_item()
        if selected_task_id is None and previous is not None:
            selected_task_id = previous.task_id
        self.tree.delete(*self.tree.get_children())
        self._task_by_iid.clear()
        for index, item in enumerate(self.model.items):
            iid = f"task-{index}"
            tag = item.status.value
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    _KIND_LABELS[item.kind],
                    _STATUS_LABELS[item.status],
                    item.progress_label or "—",
                    item.title,
                ),
                tags=(tag,),
            )
            self._task_by_iid[iid] = item
        active = self.model.active_count
        total = len(self.model.items)
        self.summary_var.set("当前没有任务" if total == 0 else f"共 {total} 项 · 进行中 {active}")
        self.trigger_button.set_text(f"任务 {active}" if active else f"任务 {total}")
        self.trigger_button.set_selected(active > 0)
        self.clear_button.state(
            ("!disabled",)
            if any(item.status.terminal for item in self.model.items)
            else ("disabled",)
        )
        if selected_task_id is not None:
            self.select_task(selected_task_id)
        elif self.model.items:
            self.tree.selection_set("task-0")
            self._selection_changed()
        else:
            self.detail_var.set("选择任务后查看详情")
            self._update_action_buttons(None)

    def _selection_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        item = self.selected_item()
        if item is None:
            self.detail_var.set("选择任务后查看详情")
        else:
            progress = f" · {item.progress_label}" if item.progress_label else ""
            detail = item.detail or "没有附加详情"
            self.detail_var.set(
                f"{_KIND_LABELS[item.kind]} · {_STATUS_LABELS[item.status]}{progress}\n{detail}"
            )
        self._update_action_buttons(item)

    def _update_action_buttons(self, item: UnifiedTaskItem | None) -> None:
        self.activate_button.state(
            ("!disabled",) if item is not None and self.on_activate else ("disabled",)
        )
        self.cancel_button.state(
            ("!disabled",)
            if item is not None and item.cancellable and self.on_cancel
            else ("disabled",)
        )
        self.retry_button.state(
            ("!disabled",)
            if item is not None and item.retryable and self.on_retry
            else ("disabled",)
        )

    def _activate_from_event(self, _event: tk.Event[tk.Misc]) -> str:
        self.activate_selected()
        return "break"

    def _layout(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        width = max(320, self.winfo_width())
        height = max(260, self.winfo_height())
        self.coords(self._content_window, width / 2, height / 2)
        self.itemconfigure(
            self._content_window,
            width=max(280, width - 28),
            height=max(220, height - 28),
        )
        self._draw_surface()

    def _draw_surface(self) -> None:
        width = max(320, self.winfo_width())
        height = max(260, self.winfo_height())

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            draw.rounded_rectangle(
                (5 * scale, 8 * scale, (width - 5) * scale, (height - 3) * scale),
                radius=28 * scale,
                fill=self._shadow,
            )
            draw.rounded_rectangle(
                (5 * scale, 4 * scale, (width - 5) * scale, (height - 7) * scale),
                radius=28 * scale,
                fill=self._surface,
                outline=self._border,
                width=max(1, scale),
            )

        self.delete("task-tray-surface")
        self._surface_photo = render_photo(width, height, paint, master=self)
        self.create_image(
            0,
            0,
            image=self._surface_photo,
            anchor="nw",
            tags=("task-tray-surface",),
        )
        self.tag_lower("task-tray-surface", self._content_window)

    def _root_configured(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is self.master and self._visible:
            self.after_idle(self._reposition)

    def _reposition(self) -> None:
        if not self._visible or not self.winfo_exists():
            return
        self.trigger_button.update_idletasks()
        master = self.master
        x = (
            self.trigger_button.winfo_rootx()
            + self.trigger_button.winfo_width()
            - master.winfo_rootx()
        )
        y = (
            self.trigger_button.winfo_rooty()
            + self.trigger_button.winfo_height()
            - master.winfo_rooty()
            + 7
        )
        x = max(430, min(master.winfo_width() - 8, x))
        self.place(x=x, y=y, anchor="ne", width=420, height=330)
        self.tk.call("raise", self._w)

    def destroy(self) -> None:
        binding = self._root_configure_binding
        self._root_configure_binding = ""
        if binding:
            try:
                self.master.unbind("<Configure>", binding)
            except tk.TclError:
                pass
        try:
            self.trigger_button.destroy()
        except tk.TclError:
            pass
        super().destroy()


__all__ = [
    "TaskTray",
    "TaskTrayModel",
    "UnifiedTaskItem",
    "UnifiedTaskKind",
    "UnifiedTaskStatus",
]
