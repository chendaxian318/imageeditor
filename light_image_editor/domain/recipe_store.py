"""Strict and atomic ``.lierecipe`` persistence for conversion recipes."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, NoReturn

from .recipes import TransformRecipe, from_batch_recipe

RECIPE_FILE_EXTENSION = ".lierecipe"
LEGACY_RECIPE_EXTENSIONS = frozenset({".liebatchpreset", ".json"})
DEFAULT_MAX_RECIPE_BYTES = 1024 * 1024


class RecipeFileError(ValueError):
    """A recipe file is malformed, unsupported, or unsafe to load."""


class RecipeFileTooLargeError(RecipeFileError):
    """A recipe file exceeds the configured byte limit."""


def _reject_json_constant(value: str) -> NoReturn:
    raise RecipeFileError(f"配方 JSON 包含非标准数值：{value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecipeFileError(f"配方 JSON 包含重复字段：{key}")
        result[key] = value
    return result


def _strict_json_object(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RecipeFileError("配方文件必须是严格 UTF-8 编码") from error
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except RecipeFileError:
        raise
    except json.JSONDecodeError as error:
        raise RecipeFileError(f"配方 JSON 损坏：{error.msg}") from error
    if not isinstance(payload, dict):
        raise RecipeFileError("配方 JSON 顶层必须是对象")
    return payload


def _migrate_legacy_preset(payload: dict[str, Any]) -> TransformRecipe:
    # Import lazily so normal .lierecipe loading has no dependency on the legacy
    # batch runner. The allow-list makes this adapter stricter than BatchPreset's
    # historical permissive parser.
    from ..batch import PRESET_SCHEMA_VERSION, BatchPreset, BatchRecipe

    allowed_root = {"schema_version", "name", "recipe"}
    unknown_root = set(payload) - allowed_root
    if unknown_root:
        raise RecipeFileError(f"旧批处理预设包含未知字段：{'、'.join(sorted(unknown_root))}")
    missing_root = allowed_root - set(payload)
    if missing_root:
        raise RecipeFileError(f"旧批处理预设缺少字段：{'、'.join(sorted(missing_root))}")
    version = payload["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise RecipeFileError("旧批处理预设 schema_version 必须是整数")
    if version != PRESET_SCHEMA_VERSION:
        raise RecipeFileError(f"不支持的旧批处理预设版本：{version}")
    recipe_payload = payload["recipe"]
    if not isinstance(recipe_payload, dict):
        raise RecipeFileError("旧批处理预设 recipe 必须是对象")
    allowed_recipe = {item.name for item in fields(BatchRecipe)}
    unknown_recipe = set(recipe_payload) - allowed_recipe
    if unknown_recipe:
        raise RecipeFileError(
            f"旧批处理预设 recipe 包含未知字段：{'、'.join(sorted(unknown_recipe))}"
        )
    try:
        preset = BatchPreset.from_json(payload)
        return from_batch_recipe(preset.recipe, name=preset.name)
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeFileError(f"旧批处理预设无法迁移：{error}") from error


@dataclass(frozen=True, slots=True)
class RecipeStore:
    """Load and save bounded, versioned transformation recipes."""

    max_file_bytes: int = DEFAULT_MAX_RECIPE_BYTES

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_file_bytes, bool)
            or not isinstance(self.max_file_bytes, int)
            or self.max_file_bytes < 1
        ):
            raise ValueError("配方文件大小上限必须是正整数")

    def load(
        self,
        path: str | os.PathLike[str],
        *,
        allow_legacy: bool = True,
    ) -> TransformRecipe:
        source = Path(path)
        suffix = source.suffix.casefold()
        allowed_extensions = {RECIPE_FILE_EXTENSION}
        if allow_legacy:
            allowed_extensions.update(LEGACY_RECIPE_EXTENSIONS)
        if suffix not in allowed_extensions:
            raise RecipeFileError(f"配方文件扩展名必须是 {RECIPE_FILE_EXTENSION}")
        with source.open("rb") as stream:
            raw = stream.read(self.max_file_bytes + 1)
        if len(raw) > self.max_file_bytes:
            raise RecipeFileTooLargeError(f"配方文件超过 {self.max_file_bytes} 字节安全上限")
        payload = _strict_json_object(raw)
        if "recipe" in payload:
            if not allow_legacy:
                raise RecipeFileError("旧批处理预设迁移已禁用")
            return _migrate_legacy_preset(payload)
        try:
            return TransformRecipe.from_json(payload)
        except (KeyError, TypeError, ValueError) as error:
            raise RecipeFileError(f"转图配方无效：{error}") from error

    def save(
        self,
        recipe: TransformRecipe,
        path: str | os.PathLike[str],
    ) -> Path:
        if not isinstance(recipe, TransformRecipe):
            raise TypeError("recipe 必须是 TransformRecipe")
        destination = Path(path)
        if destination.suffix.casefold() != RECIPE_FILE_EXTENSION:
            raise RecipeFileError(f"配方文件扩展名必须是 {RECIPE_FILE_EXTENSION}")
        recipe.validate()
        try:
            raw = (
                json.dumps(
                    recipe.to_json(),
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise RecipeFileError(f"转图配方无法序列化：{error}") from error
        if len(raw) > self.max_file_bytes:
            raise RecipeFileTooLargeError(f"配方内容超过 {self.max_file_bytes} 字节安全上限")

        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        return destination


_DEFAULT_STORE = RecipeStore()


def load_recipe(
    path: str | os.PathLike[str],
    *,
    max_file_bytes: int = DEFAULT_MAX_RECIPE_BYTES,
    allow_legacy: bool = True,
) -> TransformRecipe:
    """Load a recipe with a one-off size limit."""

    store = (
        _DEFAULT_STORE
        if max_file_bytes == DEFAULT_MAX_RECIPE_BYTES
        else RecipeStore(max_file_bytes)
    )
    return store.load(path, allow_legacy=allow_legacy)


def save_recipe(
    recipe: TransformRecipe,
    path: str | os.PathLike[str],
    *,
    max_file_bytes: int = DEFAULT_MAX_RECIPE_BYTES,
) -> Path:
    """Atomically save a recipe with a one-off size limit."""

    store = (
        _DEFAULT_STORE
        if max_file_bytes == DEFAULT_MAX_RECIPE_BYTES
        else RecipeStore(max_file_bytes)
    )
    return store.save(recipe, path)
