"""Deterministic image navigation sessions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from image_ops import VIEWER_INPUTS, natural_sort_key


def _supported_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIEWER_INPUTS


def expand_activation_paths(paths: Iterable[str | Path]) -> list[Path]:
    """Expand files and directories while preserving the caller's order."""

    result: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        candidates: Iterable[Path]
        if path.is_dir():
            candidates = sorted(
                (candidate for candidate in path.iterdir() if _supported_file(candidate)),
                key=natural_sort_key,
            )
        elif _supported_file(path):
            candidates = (path,)
        else:
            continue
        for candidate in candidates:
            try:
                normalized = candidate.resolve()
            except OSError:
                normalized = candidate.absolute()
            key = str(normalized).casefold()
            if key not in seen:
                seen.add(key)
                result.append(normalized)
    return result


@dataclass
class NavigationSession:
    """A stable ordered set of images and its current position."""

    files: list[Path]
    index: int = 0
    source_label: str = ""

    @classmethod
    def from_activation(cls, paths: Sequence[str | Path]) -> NavigationSession:
        explicit = expand_activation_paths(paths)
        if not explicit:
            return cls([])

        if len(paths) == 1 and Path(paths[0]).is_file():
            current = explicit[0]
            siblings = sorted(
                (path for path in current.parent.iterdir() if _supported_file(path)),
                key=natural_sort_key,
            )
            try:
                resolved_siblings = [path.resolve() for path in siblings]
                index = resolved_siblings.index(current.resolve())
            except (OSError, ValueError):
                resolved_siblings = explicit
                index = 0
            return cls(resolved_siblings, index, str(current.parent))

        label = "选择的文件"
        if len(paths) == 1 and Path(paths[0]).is_dir():
            label = str(Path(paths[0]).resolve())
        return cls(explicit, 0, label)

    @property
    def current(self) -> Path | None:
        if not self.files:
            return None
        return self.files[self.index]

    @property
    def position(self) -> tuple[int, int] | None:
        if not self.files:
            return None
        return self.index + 1, len(self.files)

    def select(self, path: str | Path) -> bool:
        try:
            target = Path(path).resolve()
            self.index = [item.resolve() for item in self.files].index(target)
        except (OSError, ValueError):
            return False
        return True

    def target(self, direction: int, *, wrap: bool = True) -> Path | None:
        if not self.files:
            return None
        candidate = self.index + direction
        if wrap:
            candidate %= len(self.files)
        elif not 0 <= candidate < len(self.files):
            return None
        self.index = candidate
        return self.current

    def select_edge(self, edge: str) -> Path | None:
        if not self.files:
            return None
        self.index = 0 if edge == "first" else len(self.files) - 1
        return self.current

    def neighbors(self) -> list[Path]:
        if len(self.files) <= 1:
            return []
        candidates = [
            self.files[(self.index + 1) % len(self.files)],
            self.files[(self.index - 1) % len(self.files)],
        ]
        return list(dict.fromkeys(candidates))

    def remove(self, path: str | Path) -> Path | None:
        try:
            target = Path(path).resolve()
            remove_index = [item.resolve() for item in self.files].index(target)
        except (OSError, ValueError):
            return self.current
        self.files.pop(remove_index)
        if not self.files:
            self.index = 0
            return None
        if remove_index < self.index or self.index >= len(self.files):
            self.index = max(0, self.index - 1)
        return self.current
