"""Application composition root and command-line entry point."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, cast

from .diagnostics import configure_diagnostics, install_crash_handlers
from .dpi import enable_process_dpi_awareness
from .single_instance import (
    SingleInstanceController,
    activation_message,
    request_running_app_exit,
)
from .ui import ImageEditorApp


class Application(Protocol):
    def after(self, delay_ms: int, callback: Callable[[], object]) -> object: ...

    def open_path(self, path: str | Path, confirm: bool = True) -> None: ...

    def mainloop(self) -> None: ...


class ViewerApplication(Application, Protocol):
    def open_paths(self, paths: Sequence[str | Path], confirm: bool = True) -> None: ...

    def handle_activation(self, paths: Sequence[str | Path]) -> None: ...

    def request_exit_for_upgrade(self) -> bool: ...


def create_application(
    argv: Sequence[str] | None = None,
    app_factory: Callable[[], Application] = ImageEditorApp,
) -> Application:
    """Create the UI and schedule command-line images to open."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    app = app_factory()
    paths = [argument for argument in arguments if Path(argument).exists()]
    if paths:
        if hasattr(app, "open_paths"):
            app.after(100, lambda: app.open_paths(paths))
        else:
            app.after(100, lambda: app.open_path(paths[0]))
    return app


def _dispatch_ui_command(app: ViewerApplication, message: dict[str, Any]) -> dict[str, Any]:
    command = message.get("command")
    if command == "open":
        paths = [str(path) for path in message.get("paths", []) if isinstance(path, str)]
        app.after(0, lambda: app.handle_activation(paths))
        return {"ok": True, "accepted": len(paths)}
    if command == "request_exit":
        completed = threading.Event()
        result: dict[str, Any] = {"ok": False, "accepted": False}

        def request_exit() -> None:
            result.update(ok=True, accepted=app.request_exit_for_upgrade())
            completed.set()

        app.after(0, request_exit)
        timeout = float(message.get("timeout", 30.0))
        if not completed.wait(max(1.0, timeout)):
            return {"ok": False, "accepted": False, "error": "user response timed out"}
        return result
    return {"ok": False, "error": f"unknown command: {command}"}


def main(
    argv: Sequence[str] | None = None,
    controller_factory: Callable[[], SingleInstanceController] = SingleInstanceController,
) -> int:
    enable_process_dpi_awareness()
    configure_diagnostics()
    install_crash_handlers()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--request-exit-for-upgrade" in arguments:
        response = request_running_app_exit()
        if response.get("ok") and response.get("accepted", True):
            return 0
        if response.get("ok") and not response.get("accepted"):
            return 2
        return 3
    paths = [argument for argument in arguments if Path(argument).exists()]
    controller = controller_factory()
    if not controller.acquire_primary():
        response = controller.send(activation_message(paths))
        controller.close()
        if not response.get("ok"):
            raise RuntimeError(f"无法将打开请求转发给现有窗口：{response.get('error', '未知错误')}")
        return 0

    app = cast(ViewerApplication, create_application(arguments))
    try:
        controller.start(lambda message: _dispatch_ui_command(app, dict(message)))
        app.mainloop()
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
