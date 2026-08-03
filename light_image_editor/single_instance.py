"""Per-user single-instance coordination using a named mutex and local pipe."""

from __future__ import annotations

import ctypes
import getpass
import hashlib
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any, Final

from .identity import APP_ID

ERROR_ALREADY_EXISTS: Final = 183
Message = dict[str, Any]
MessageHandler = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]


def activation_message(paths: Sequence[str | Path]) -> Message:
    return {
        "command": "open",
        "paths": [str(Path(path).expanduser().absolute()) for path in paths],
    }


class NamedMutex:
    def __init__(self, name: str) -> None:
        self.name = name
        self.handle: int | None = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.GetLastError.restype = ctypes.c_ulong
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise ctypes.WinError()
        self.handle = int(handle)
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self.handle is not None and os.name == "nt":
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
        self.handle = None


class SingleInstanceController:
    """Own the primary mutex or redirect commands to the primary process."""

    def __init__(self, app_id: str = APP_ID, connect_timeout: float = 5.0) -> None:
        user_key = hashlib.sha256(getpass.getuser().encode("utf-8")).hexdigest()[:12]
        safe_id = "".join(character if character.isalnum() else "-" for character in app_id)
        self.mutex = NamedMutex(rf"Local\{safe_id}-{user_key}")
        self.address = rf"\\.\pipe\{safe_id}-{user_key}"
        self.authkey = hashlib.sha256(f"{app_id}:{getpass.getuser()}".encode()).digest()
        self.connect_timeout = connect_timeout
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def acquire_primary(self) -> bool:
        return self.mutex.acquire()

    def start(self, handler: MessageHandler) -> None:
        self._listener = Listener(self.address, family="AF_PIPE", authkey=self.authkey)
        self._thread = threading.Thread(
            target=self._serve,
            args=(handler,),
            name="activation-pipe",
            daemon=True,
        )
        self._thread.start()

    def _serve(self, handler: MessageHandler) -> None:
        assert self._listener is not None
        while not self._stopping.is_set():
            try:
                connection = self._listener.accept()
            except (OSError, EOFError):
                break
            with connection:
                try:
                    message = connection.recv()
                    if not isinstance(message, dict):
                        response: Mapping[str, Any] = {"ok": False, "error": "invalid message"}
                    elif message.get("command") == "shutdown_listener":
                        response = {"ok": True}
                        self._stopping.set()
                    else:
                        response = handler(message) or {"ok": True}
                    connection.send(dict(response))
                except (EOFError, OSError, ValueError) as error:
                    try:
                        connection.send({"ok": False, "error": str(error)})
                    except (EOFError, OSError):
                        pass

    def send(self, message: Mapping[str, Any]) -> Message:
        deadline = time.monotonic() + self.connect_timeout
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            try:
                connection = Client(self.address, family="AF_PIPE", authkey=self.authkey)
                with connection:
                    connection.send(dict(message))
                    response = connection.recv()
                return dict(response) if isinstance(response, dict) else {"ok": False}
            except (FileNotFoundError, ConnectionRefusedError, EOFError, OSError) as error:
                last_error = error
                time.sleep(0.05)
        return {"ok": False, "error": str(last_error or "IPC connection timed out")}

    def close(self) -> None:
        if self._listener is not None and not self._stopping.is_set():
            self.send({"command": "shutdown_listener"})
        self._stopping.set()
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self.mutex.close()


def request_running_app_exit(timeout: float = 30.0) -> Message:
    controller = SingleInstanceController(connect_timeout=min(5.0, timeout))
    if controller.acquire_primary():
        controller.close()
        return {"ok": True, "running": False}
    try:
        response = controller.send(
            {"command": "request_exit", "reason": "upgrade", "timeout": timeout}
        )
    finally:
        controller.close()
    if not response.get("ok") or not response.get("accepted"):
        return response

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = SingleInstanceController(connect_timeout=0.2)
        try:
            if probe.acquire_primary():
                return response
        finally:
            probe.close()
        time.sleep(0.05)
    return {"ok": False, "accepted": False, "error": "application did not exit in time"}
