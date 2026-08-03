"""Application composition root and command-line entry point."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from .ui import ImageEditorApp


class Application(Protocol):
    def after(self, delay_ms: int, callback: Callable[[], object]) -> object: ...

    def open_path(self, path: str | Path, confirm: bool = True) -> None: ...

    def mainloop(self) -> None: ...


def create_application(
    argv: Sequence[str] | None = None,
    app_factory: Callable[[], Application] = ImageEditorApp,
) -> Application:
    """Create the UI and schedule an optional command-line image to open."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    app = app_factory()
    if arguments and Path(arguments[0]).exists():
        initial_path = arguments[0]
        app.after(100, lambda: app.open_path(initial_path))
    return app


def main(argv: Sequence[str] | None = None) -> None:
    app = create_application(argv)
    app.mainloop()


if __name__ == "__main__":
    main()
