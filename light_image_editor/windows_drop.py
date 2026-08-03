"""Minimal WM_DROPFILES integration without third-party Tk extensions."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


class WindowsFileDrop:
    def __init__(self, widget: Any, callback: Callable[[list[Path]], object]) -> None:
        self.widget = widget
        self.callback = callback
        self.hwnd: int | None = None
        self._old_proc: int | None = None
        self._window_proc: Any = None

    def enable(self) -> bool:
        if os.name != "nt":
            return False
        self.widget.update_idletasks()
        self.hwnd = int(self.widget.winfo_id())
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        is_64_bit = ctypes.sizeof(ctypes.c_void_p) == 8
        set_window_long = user32.SetWindowLongPtrW if is_64_bit else user32.SetWindowLongW
        get_window_long = user32.GetWindowLongPtrW if is_64_bit else user32.GetWindowLongW
        call_window_proc = user32.CallWindowProcW
        window_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_window_long.restype = ctypes.c_void_p
        set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        set_window_long.restype = ctypes.c_void_p
        call_window_proc.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        call_window_proc.restype = ctypes.c_ssize_t
        shell32.DragQueryFileW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        ]
        shell32.DragQueryFileW.restype = ctypes.c_uint
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        shell32.DragAcceptFiles.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        old_proc = get_window_long(ctypes.c_void_p(self.hwnd), -4)
        if not old_proc:
            return False
        self._old_proc = int(old_proc)

        def window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == 0x0233:
                drop_handle = ctypes.c_void_p(wparam)
                count = shell32.DragQueryFileW(drop_handle, 0xFFFFFFFF, None, 0)
                paths: list[Path] = []
                for index in range(count):
                    length = shell32.DragQueryFileW(drop_handle, index, None, 0)
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    shell32.DragQueryFileW(drop_handle, index, buffer, length + 1)
                    paths.append(Path(buffer.value))
                shell32.DragFinish(drop_handle)
                self.widget.after(0, lambda: self.callback(paths))
                return 0
            assert self._old_proc is not None
            return int(
                call_window_proc(
                    ctypes.c_void_p(self._old_proc),
                    ctypes.c_void_p(hwnd),
                    message,
                    wparam,
                    lparam,
                )
            )

        self._window_proc = window_proc_type(window_proc)
        previous = set_window_long(
            ctypes.c_void_p(self.hwnd),
            -4,
            ctypes.cast(self._window_proc, ctypes.c_void_p),
        )
        if not previous:
            self._old_proc = None
            self._window_proc = None
            return False
        shell32.DragAcceptFiles(ctypes.c_void_p(self.hwnd), True)
        return True

    def disable(self) -> None:
        if os.name != "nt" or self.hwnd is None:
            return
        ctypes.windll.shell32.DragAcceptFiles(ctypes.c_void_p(self.hwnd), False)
        if self._old_proc is not None:
            is_64_bit = ctypes.sizeof(ctypes.c_void_p) == 8
            set_window_long = (
                ctypes.windll.user32.SetWindowLongPtrW
                if is_64_bit
                else ctypes.windll.user32.SetWindowLongW
            )
            set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            set_window_long(ctypes.c_void_p(self.hwnd), -4, ctypes.c_void_p(self._old_proc))
        self.hwnd = None
        self._old_proc = None
        self._window_proc = None
