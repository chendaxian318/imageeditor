"""Serializable command timeline for deterministic non-destructive editing."""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EditStage(StrEnum):
    GEOMETRY = "geometry"
    CROP = "crop"
    COLOR = "color"
    ANNOTATION = "annotation"
    SIZE = "size"
    EXPORT = "export"


STAGE_ORDER = (
    EditStage.GEOMETRY,
    EditStage.CROP,
    EditStage.COLOR,
    EditStage.ANNOTATION,
    EditStage.SIZE,
    EditStage.EXPORT,
)

COMMAND_STAGES: dict[str, EditStage] = {
    "rotate": EditStage.GEOMETRY,
    "flip_horizontal": EditStage.GEOMETRY,
    "flip_vertical": EditStage.GEOMETRY,
    "fine_rotate": EditStage.GEOMETRY,
    "perspective": EditStage.GEOMETRY,
    "canvas_expand": EditStage.GEOMETRY,
    "crop": EditStage.CROP,
    "adjust": EditStage.COLOR,
    "levels": EditStage.COLOR,
    "curve": EditStage.COLOR,
    "watermark": EditStage.ANNOTATION,
    "annotation_add": EditStage.ANNOTATION,
    "annotation_update": EditStage.ANNOTATION,
    "annotation_delete": EditStage.ANNOTATION,
    "resize": EditStage.SIZE,
    "export": EditStage.EXPORT,
}


@dataclass(frozen=True)
class EditCommand:
    id: str
    kind: str
    stage: EditStage
    params: dict[str, Any]
    label: str
    created_at_utc: str
    merge_key: str | None = None

    @classmethod
    def create(
        cls,
        kind: str,
        params: dict[str, Any],
        label: str,
        *,
        merge_key: str | None = None,
    ) -> EditCommand:
        if kind not in COMMAND_STAGES:
            raise ValueError(f"未知编辑命令：{kind}")
        _validate_json_value(params)
        return cls(
            id=str(uuid.uuid4()),
            kind=kind,
            stage=COMMAND_STAGES[kind],
            params=dict(params),
            label=label,
            created_at_utc=datetime.now(UTC).isoformat(),
            merge_key=merge_key,
        )

    def merged_with(self, newer: EditCommand) -> EditCommand:
        if not self.merge_key or self.merge_key != newer.merge_key or self.kind != newer.kind:
            raise ValueError("命令不能合并。")
        return EditCommand(
            id=self.id,
            kind=self.kind,
            stage=self.stage,
            params=dict(newer.params),
            label=newer.label,
            created_at_utc=self.created_at_utc,
            merge_key=self.merge_key,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "stage": self.stage.value,
            "params": self.params,
            "label": self.label,
            "created_at_utc": self.created_at_utc,
            "merge_key": self.merge_key,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EditCommand:
        kind = str(data["kind"])
        if kind not in COMMAND_STAGES:
            raise UnknownCommandError(kind)
        params = dict(data.get("params", {}))
        _validate_json_value(params)
        stage = EditStage(str(data.get("stage", COMMAND_STAGES[kind].value)))
        if stage != COMMAND_STAGES[kind]:
            raise ValueError(f"命令 {kind} 的阶段不正确。")
        command_id = str(data.get("id", uuid.uuid4()))
        uuid.UUID(command_id)
        return cls(
            id=command_id,
            kind=kind,
            stage=stage,
            params=params,
            label=str(data.get("label", kind)),
            created_at_utc=str(data.get("created_at_utc", datetime.now(UTC).isoformat())),
            merge_key=(str(data["merge_key"]) if data.get("merge_key") is not None else None),
        )


class UnknownCommandError(ValueError):
    def __init__(self, kind: str) -> None:
        super().__init__(f"项目包含当前版本不支持的命令：{kind}")
        self.kind = kind


@dataclass
class EditTimeline:
    commands: list[EditCommand] = field(default_factory=list)
    cursor: int = 0
    revision: int = 0
    _clean_signature: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        if not 0 <= self.cursor <= len(self.commands):
            raise ValueError("历史位置超出命令范围。")
        self._clean_signature = self.active_signature()

    @property
    def active_commands(self) -> tuple[EditCommand, ...]:
        return tuple(self.commands[: self.cursor])

    @property
    def can_undo(self) -> bool:
        return self.cursor > 0

    @property
    def can_redo(self) -> bool:
        return self.cursor < len(self.commands)

    @property
    def dirty(self) -> bool:
        return self.active_signature() != self._clean_signature

    def commit(self, command: EditCommand, *, merge: bool = False) -> None:
        if len(self.commands) >= 10_000 and self.cursor == len(self.commands):
            raise ValueError("编辑历史已达到 10,000 条安全上限。")
        if self.cursor < len(self.commands):
            del self.commands[self.cursor :]
        if merge and self.commands and self.cursor == len(self.commands):
            previous = self.commands[-1]
            if previous.merge_key and previous.merge_key == command.merge_key:
                self.commands[-1] = previous.merged_with(command)
                self.revision += 1
                return
        self.commands.append(command)
        self.cursor = len(self.commands)
        self.revision += 1

    def undo(self) -> EditCommand | None:
        if not self.can_undo:
            return None
        self.cursor -= 1
        self.revision += 1
        return self.commands[self.cursor]

    def redo(self) -> EditCommand | None:
        if not self.can_redo:
            return None
        command = self.commands[self.cursor]
        self.cursor += 1
        self.revision += 1
        return command

    def jump(self, cursor: int) -> None:
        if not 0 <= cursor <= len(self.commands):
            raise ValueError("历史位置超出命令范围。")
        if cursor != self.cursor:
            self.cursor = cursor
            self.revision += 1

    def mark_clean(self) -> None:
        self._clean_signature = self.active_signature()

    def mark_dirty(self) -> None:
        self._clean_signature = "recovered:" + self.active_signature()

    def active_signature(self) -> str:
        payload = [command.to_json() for command in self.active_commands]
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_json(self) -> dict[str, Any]:
        return {
            "commands": [command.to_json() for command in self.commands],
            "cursor": self.cursor,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EditTimeline:
        raw_commands = data.get("commands", [])
        if not isinstance(raw_commands, list):
            raise ValueError("项目命令列表无效。")
        if len(raw_commands) > 10_000:
            raise ValueError("项目命令超过 10,000 条安全上限。")
        commands = [EditCommand.from_json(dict(item)) for item in raw_commands]
        cursor = int(data.get("cursor", len(commands)))
        return cls(commands=commands, cursor=cursor)


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError("命令参数嵌套过深。")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("命令参数不能包含 NaN 或无穷值。")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("命令参数键必须是字符串。")
            _validate_json_value(item, depth=depth + 1)
        return
    raise ValueError(f"命令参数不能序列化：{type(value).__name__}")
