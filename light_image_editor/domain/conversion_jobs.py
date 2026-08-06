"""Deterministic input queue primitives for the conversion workspace."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from image_ops import SUPPORTED_INPUTS


class AssetReference(Protocol):
    """Small structural contract shared by asset records and shell handoffs."""

    @property
    def id(self) -> str: ...


class RevisionReference(Protocol):
    """Small structural contract shared by revisions and shell handoffs."""

    @property
    def id(self) -> str: ...

    @property
    def asset_id(self) -> str: ...


_NATURAL_PARTS = re.compile(r"(\d+)")


def _normalize_local_path(value: str | os.PathLike[str]) -> Path:
    if isinstance(value, str) and not value.strip():
        raise ValueError("转换输入路径不能为空")
    path = Path(value).expanduser()
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _path_key(path: Path) -> str:
    # The product is Windows-first. ``casefold`` also keeps deduplication stable
    # when queue files are prepared on another platform for a Windows machine.
    return os.path.normcase(str(path)).casefold()


def _natural_text_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in _NATURAL_PARTS.split(value)
        if part
    )


def _input_sort_key(item: ConversionInput) -> tuple[object, ...]:
    return (
        _natural_text_key(item.path.name),
        _natural_text_key(str(item.path.parent)),
        _path_key(item.path),
    )


def _freeze_metadata(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class ConversionInput:
    """One local image and its optional cross-mode identity.

    Asset and revision references are intentionally retained as objects. Both the
    immutable domain records and the shell handoff records satisfy these small
    protocols, so queue operations cannot accidentally discard their metadata.
    """

    path: Path
    enabled: bool = True
    asset: AssetReference | None = None
    revision: RevisionReference | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        path = _normalize_local_path(self.path)
        if path.suffix.casefold() not in SUPPORTED_INPUTS:
            raise ValueError(f"不支持的转换输入格式：{path.suffix or '无扩展名'}")
        if not isinstance(self.enabled, bool):
            raise ValueError("转换输入 enabled 必须是布尔值")
        if self.asset is not None:
            asset_id = self.asset.id
            if not isinstance(asset_id, str) or not asset_id.strip():
                raise ValueError("转换输入的 asset id 不能为空")
        if self.revision is not None:
            if self.asset is None:
                raise ValueError("转换输入的 revision 必须同时提供 asset")
            revision_id = self.revision.id
            if not isinstance(revision_id, str) or not revision_id.strip():
                raise ValueError("转换输入的 revision id 不能为空")
            if self.revision.asset_id != self.asset.id:
                raise ValueError("转换输入的 revision 属于其他 asset")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("转换输入 metadata 必须是映射")

        object.__setattr__(self, "path", path)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    @property
    def asset_id(self) -> str | None:
        return None if self.asset is None else self.asset.id

    @property
    def revision_id(self) -> str | None:
        return None if self.revision is None else self.revision.id

    def with_enabled(self, enabled: bool) -> ConversionInput:
        if not isinstance(enabled, bool):
            raise ValueError("转换输入 enabled 必须是布尔值")
        return self if enabled == self.enabled else replace(self, enabled=enabled)


class ConversionQueue:
    """Naturally sorted, path-deduplicated conversion inputs."""

    def __init__(self, inputs: Iterable[ConversionInput] = ()) -> None:
        self._items: list[ConversionInput] = []
        self._by_path: dict[str, ConversionInput] = {}
        self._add_candidates(inputs)

    @classmethod
    def from_ordered(cls, inputs: Iterable[ConversionInput]) -> ConversionQueue:
        """Restore an explicit persisted order without changing normal add sorting."""

        queue = cls()
        queue._add_candidates(inputs, sort_items=False)
        return queue

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[ConversionInput]:
        return iter(self._items)

    def __getitem__(self, index: int) -> ConversionInput:
        return self._items[index]

    @property
    def items(self) -> tuple[ConversionInput, ...]:
        return tuple(self._items)

    @property
    def enabled_items(self) -> tuple[ConversionInput, ...]:
        return tuple(item for item in self._items if item.enabled)

    @property
    def excluded_items(self) -> tuple[ConversionInput, ...]:
        return tuple(item for item in self._items if not item.enabled)

    def get(self, path: str | os.PathLike[str]) -> ConversionInput | None:
        return self._by_path.get(_path_key(_normalize_local_path(path)))

    def add_input(self, item: ConversionInput) -> bool:
        """Add an already-described input; keep the first item's metadata on duplicates."""

        return bool(self._add_candidates((item,)))

    def attach_identity(
        self,
        path: str | os.PathLike[str],
        *,
        asset: AssetReference,
        revision: RevisionReference | None = None,
    ) -> ConversionInput:
        """Attach a resolved asset identity without changing queue order or enablement."""

        key = _path_key(_normalize_local_path(path))
        current = self._by_path.get(key)
        if current is None:
            raise KeyError(str(path))
        revision_id = None if revision is None else revision.id
        if current.asset_id == asset.id and current.revision_id == revision_id:
            return current
        updated = replace(current, asset=asset, revision=revision)
        index = self._items.index(current)
        self._items[index] = updated
        self._by_path[key] = updated
        return updated

    def add_file(
        self,
        path: str | os.PathLike[str],
        *,
        enabled: bool = True,
        asset: AssetReference | None = None,
        revision: RevisionReference | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> ConversionInput | None:
        added = self.add_files(
            (path,),
            enabled=enabled,
            asset=asset,
            revision=revision,
            metadata=metadata,
        )
        return added[0] if added else None

    def add_files(
        self,
        paths: Iterable[str | os.PathLike[str]],
        *,
        enabled: bool = True,
        asset: AssetReference | None = None,
        revision: RevisionReference | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> tuple[ConversionInput, ...]:
        candidates: list[ConversionInput] = []
        for raw_path in paths:
            path = _normalize_local_path(raw_path)
            if not path.exists():
                raise FileNotFoundError(path)
            if not path.is_file():
                raise IsADirectoryError(path)
            candidates.append(
                ConversionInput(
                    path=path,
                    enabled=enabled,
                    asset=asset,
                    revision=revision,
                    metadata={} if metadata is None else metadata,
                )
            )
        return self._add_candidates(candidates)

    def add_folder(
        self,
        folder: str | os.PathLike[str],
        *,
        recursive: bool = True,
        enabled: bool = True,
        metadata: Mapping[str, object] | None = None,
    ) -> tuple[ConversionInput, ...]:
        directory = _normalize_local_path(folder)
        if not directory.exists():
            raise FileNotFoundError(directory)
        if not directory.is_dir():
            raise NotADirectoryError(directory)
        iterator = directory.rglob("*") if recursive else directory.iterdir()
        paths = (
            path
            for path in iterator
            if path.is_file() and path.suffix.casefold() in SUPPORTED_INPUTS
        )
        return self.add_files(paths, enabled=enabled, metadata=metadata)

    def add_paths(
        self,
        paths: Iterable[str | os.PathLike[str]],
        *,
        recursive: bool = True,
    ) -> tuple[ConversionInput, ...]:
        added: list[ConversionInput] = []
        for raw_path in paths:
            path = _normalize_local_path(raw_path)
            if path.is_dir():
                added.extend(self.add_folder(path, recursive=recursive))
            else:
                item = self.add_file(path)
                if item is not None:
                    added.append(item)
        return tuple(sorted(added, key=_input_sort_key))

    def remove(self, path: str | os.PathLike[str]) -> ConversionInput | None:
        key = _path_key(_normalize_local_path(path))
        item = self._by_path.pop(key, None)
        if item is not None:
            self._items.remove(item)
        return item

    def clear(self) -> tuple[ConversionInput, ...]:
        removed = tuple(self._items)
        self._items.clear()
        self._by_path.clear()
        return removed

    def set_enabled(self, path: str | os.PathLike[str], *, enabled: bool) -> ConversionInput:
        key = _path_key(_normalize_local_path(path))
        current = self._by_path.get(key)
        if current is None:
            raise KeyError(str(path))
        updated = current.with_enabled(enabled)
        if updated is current:
            return current
        index = self._items.index(current)
        self._items[index] = updated
        self._by_path[key] = updated
        return updated

    def enable(self, path: str | os.PathLike[str]) -> ConversionInput:
        return self.set_enabled(path, enabled=True)

    def exclude(self, path: str | os.PathLike[str]) -> ConversionInput:
        return self.set_enabled(path, enabled=False)

    def set_all_enabled(self, enabled: bool) -> tuple[ConversionInput, ...]:
        if not isinstance(enabled, bool):
            raise ValueError("转换输入 enabled 必须是布尔值")
        self._items = [item.with_enabled(enabled) for item in self._items]
        self._by_path = {_path_key(item.path): item for item in self._items}
        return tuple(self._items)

    def _add_candidates(
        self,
        candidates: Iterable[ConversionInput],
        *,
        sort_items: bool = True,
    ) -> tuple[ConversionInput, ...]:
        added: list[ConversionInput] = []
        for item in candidates:
            if not isinstance(item, ConversionInput):
                raise TypeError("转换队列只能包含 ConversionInput")
            key = _path_key(item.path)
            if key in self._by_path:
                continue
            self._items.append(item)
            self._by_path[key] = item
            added.append(item)
        if added and sort_items:
            self._items.sort(key=_input_sort_key)
            added.sort(key=_input_sort_key)
        return tuple(added)
