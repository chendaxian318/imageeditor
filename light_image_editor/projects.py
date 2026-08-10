"""Versioned external-source project files for non-destructive editing."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .commands import EditCommand, EditTimeline, UnknownCommandError
from .document import Document, DocumentState
from .image_loader import decode_image

PROJECT_FORMAT = "light-image-editor-project"
PROJECT_SCHEMA_VERSION = 2
MAX_PROJECT_BYTES = 10 * 1024 * 1024


class ProjectError(RuntimeError):
    pass


class ProjectSourceMissing(ProjectError):
    def __init__(self, candidates: tuple[Path, ...]) -> None:
        super().__init__("项目引用的原图不存在，需要重新定位。")
        self.candidates = candidates


class ProjectSourceModified(ProjectError):
    def __init__(self, path: Path) -> None:
        super().__init__("项目引用的原图与保存项目时不同。")
        self.path = path


@dataclass(frozen=True)
class LoadedProject:
    document: Document
    migrated_from: int | None = None
    unsupported_commands: tuple[str, ...] = ()


def save_project(document: Document, path: str | Path) -> Path:
    if document.project_read_only:
        raise ProjectError("项目包含当前版本不支持的命令，只能只读打开。")
    if document.source_path is None or not document.source_path.exists():
        raise ProjectError("保存项目之前必须先将原图保存到磁盘。")
    project_path = Path(path).resolve()
    project_path.parent.mkdir(parents=True, exist_ok=True)
    source_path = document.source_path.resolve()
    try:
        relative_path = os.path.relpath(source_path, project_path.parent)
    except ValueError:
        relative_path = None
    payload: dict[str, Any] = dict(document.project_extra_fields)
    payload.update(
        {
            "format": PROJECT_FORMAT,
            "schema_version": PROJECT_SCHEMA_VERSION,
            "app_version": _application_version(),
            "source": {
                "relative_path": relative_path,
                "absolute_path": str(source_path),
                "size": source_path.stat().st_size,
                "mtime_ns": source_path.stat().st_mtime_ns,
                "sha256": file_sha256(source_path),
            },
            "timeline": document.timeline.to_json(),
            "export_state": document.state.to_json(),
        }
    )
    _atomic_write_json(project_path, payload)
    document.project_path = project_path
    document.mark_clean()
    return project_path


def load_project(
    path: str | Path,
    *,
    source_override: str | Path | None = None,
    allow_modified_source: bool = False,
) -> LoadedProject:
    project_path = Path(path).resolve()
    try:
        size = project_path.stat().st_size
    except OSError as error:
        raise ProjectError(f"无法读取项目：{error}") from error
    if size > MAX_PROJECT_BYTES:
        raise ProjectError("项目文件超过 10 MB 安全上限。")
    try:
        payload = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectError(f"项目 JSON 无效：{error}") from error
    if not isinstance(payload, dict) or payload.get("format") != PROJECT_FORMAT:
        raise ProjectError("这不是 CSImage 项目文件。")
    try:
        schema_version = int(payload.get("schema_version", 1))
    except (TypeError, ValueError) as error:
        raise ProjectError("项目版本号无效。") from error
    if schema_version < 1:
        raise ProjectError("项目版本过旧且无法迁移。")

    source_data = payload.get("source", {})
    if schema_version == 1:
        source_value = payload.get("source_path")
        source_data = {"absolute_path": source_value}
    if not isinstance(source_data, dict):
        raise ProjectError("项目源图信息无效。")
    candidates = _source_candidates(project_path, source_data, source_override)
    source_path = next((candidate for candidate in candidates if candidate.exists()), None)
    if source_path is None:
        raise ProjectSourceMissing(candidates)
    expected_hash = source_data.get("sha256")
    if (
        isinstance(expected_hash, str)
        and expected_hash
        and file_sha256(source_path) != expected_hash.casefold()
        and not allow_modified_source
    ):
        raise ProjectSourceModified(source_path)

    image = decode_image(source_path)
    migrated_from: int | None = None
    unsupported: tuple[str, ...] = ()
    if schema_version == 1:
        state = DocumentState.from_json(dict(payload.get("state", {})))
        timeline = timeline_from_legacy_state(state, image.size)
        migrated_from = 1
    else:
        state = DocumentState.from_json(dict(payload.get("export_state", {})))
        timeline, unsupported = _tolerant_timeline(dict(payload.get("timeline", {})))

    known_fields = {
        "format",
        "schema_version",
        "app_version",
        "source",
        "source_path",
        "timeline",
        "state",
        "export_state",
    }
    extras = {key: value for key, value in payload.items() if key not in known_fields}
    document = Document.from_decoded(image, source_path, state)
    document.timeline = timeline
    document.timeline.mark_clean()
    document.project_path = project_path
    document.project_read_only = bool(unsupported) or schema_version > PROJECT_SCHEMA_VERSION
    document.project_extra_fields = extras
    document.mark_clean()
    return LoadedProject(document, migrated_from=migrated_from, unsupported_commands=unsupported)


def timeline_from_legacy_state(state: DocumentState, source_size: tuple[int, int]) -> EditTimeline:
    timeline = EditTimeline()
    edit = state.edit_state
    if edit.rotation:
        timeline.commit(EditCommand.create("rotate", {"degrees": edit.rotation}, "迁移：旋转"))
    if edit.flip_horizontal:
        timeline.commit(EditCommand.create("flip_horizontal", {}, "迁移：水平翻转"))
    if edit.flip_vertical:
        timeline.commit(EditCommand.create("flip_vertical", {}, "迁移：垂直翻转"))
    if state.crop_box is not None:
        width, height = source_size
        left, top, right, bottom = state.crop_box
        timeline.commit(
            EditCommand.create(
                "crop",
                {
                    "box": [
                        left / width,
                        top / height,
                        right / width,
                        bottom / height,
                    ]
                },
                "迁移：裁剪",
            )
        )
    for name in (
        "brightness",
        "contrast",
        "saturation",
        "grayscale",
        "auto_contrast",
    ):
        value = getattr(edit, name)
        if value:
            timeline.commit(
                EditCommand.create("adjust", {"name": name, "value": value}, f"迁移：{name}")
            )
    if edit.sharpness:
        timeline.commit(
            EditCommand.create(
                "adjust", {"name": "sharpen", "value": max(0, edit.sharpness)}, "迁移：锐化"
            )
        )
    resize_params = _legacy_resize_params(state)
    if resize_params:
        timeline.commit(EditCommand.create("resize", resize_params, "迁移：尺寸"))
    if state.watermark_text:
        timeline.commit(
            EditCommand.create(
                "watermark",
                {
                    "text": state.watermark_text,
                    "position": state.watermark_position,
                    "opacity": state.watermark_opacity,
                    "size_percent": state.watermark_size_percent,
                },
                "迁移：水印",
            )
        )
    timeline.mark_clean()
    return timeline


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _tolerant_timeline(data: dict[str, Any]) -> tuple[EditTimeline, tuple[str, ...]]:
    raw_commands = data.get("commands", [])
    if not isinstance(raw_commands, list) or len(raw_commands) > 10_000:
        raise ProjectError("项目命令列表无效或超过安全上限。")
    commands: list[EditCommand] = []
    unsupported: list[str] = []
    raw_cursor = int(data.get("cursor", len(raw_commands)))
    if not 0 <= raw_cursor <= len(raw_commands):
        raise ProjectError("项目历史位置超出命令范围。")
    known_active_count = 0
    for index, item in enumerate(raw_commands):
        if not isinstance(item, dict):
            raise ProjectError("项目命令必须是 JSON 对象。")
        try:
            commands.append(EditCommand.from_json(item))
            if index < raw_cursor:
                known_active_count += 1
        except UnknownCommandError as error:
            unsupported.append(error.kind)
    return EditTimeline(commands=commands, cursor=known_active_count), tuple(
        dict.fromkeys(unsupported)
    )


def _source_candidates(
    project_path: Path,
    source_data: dict[str, Any],
    source_override: str | Path | None,
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if source_override is not None:
        candidates.append(Path(source_override).resolve())
    relative = source_data.get("relative_path")
    if isinstance(relative, str) and relative:
        candidates.append((project_path.parent / relative).resolve())
    absolute = source_data.get("absolute_path")
    if isinstance(absolute, str) and absolute:
        candidates.append(Path(absolute).resolve())
    return tuple(dict.fromkeys(candidates))


def _legacy_resize_params(state: DocumentState) -> dict[str, int | None]:
    try:
        options = state.export_options()
    except ValueError:
        return {}
    values = {
        "exact_width": options.exact_width,
        "exact_height": options.exact_height,
        "max_width": options.max_width,
        "max_height": options.max_height,
    }
    return values if any(value is not None for value in values.values()) else {}


def _application_version() -> str:
    from .version import __version__

    return __version__


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
