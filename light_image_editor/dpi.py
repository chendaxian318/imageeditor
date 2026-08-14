"""Windows per-monitor DPI awareness and bounded window geometry."""

from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk


def _tk_module() -> ModuleType:
    import tkinter as tk

    return tk


@dataclass(frozen=True)
class WorkArea:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


def enable_process_dpi_awareness() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return True
        except (AttributeError, OSError):
            return False


def dpi_scale(dpi: int) -> float:
    return max(1.0, dpi / 96.0)


def clamp_geometry(
    width: int,
    height: int,
    x: int,
    y: int,
    work: WorkArea,
    *,
    margin: int = 16,
) -> tuple[int, int, int, int]:
    usable_width = max(320, work.width - margin * 2)
    usable_height = max(240, work.height - margin * 2)
    bounded_width = min(max(320, width), usable_width)
    bounded_height = min(max(240, height), usable_height)
    bounded_x = min(max(x, work.left + margin), work.right - bounded_width - margin)
    bounded_y = min(max(y, work.top + margin), work.bottom - bounded_height - margin)
    return bounded_width, bounded_height, bounded_x, bounded_y


def parse_geometry(value: str) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", value)
    return tuple(map(int, match.groups())) if match else None  # type: ignore[return-value]


def fit_geometry_to_work_area(
    geometry: str,
    work: WorkArea,
    *,
    margin: int = 16,
) -> str | None:
    """Clamp a Tk geometry string to one monitor, including negative coordinates."""

    parsed = parse_geometry(geometry)
    if parsed is None:
        return None
    width, height, x, y = clamp_geometry(*parsed, work, margin=margin)
    return f"{width}x{height}+{x}+{y}"


def monitor_work_area(root: tk.Misc) -> WorkArea:
    tk = _tk_module()
    if os.name != "nt":
        return WorkArea(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", ctypes.c_ulong),
        ]

    try:
        monitor = ctypes.windll.user32.MonitorFromWindow(root.winfo_id(), 2)
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rect = info.rcWork
            return WorkArea(rect.left, rect.top, rect.right, rect.bottom)
    except (AttributeError, OSError, tk.TclError):
        pass
    return WorkArea(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())


class DpiManager:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.current_dpi = 96

    def refresh(self) -> bool:
        tk = _tk_module()
        try:
            dpi = max(96, round(float(self.root.winfo_fpixels("1i"))))
        except tk.TclError:
            dpi = 96
        changed = dpi != self.current_dpi
        self.current_dpi = dpi
        if changed:
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        work = monitor_work_area(self.root)
        self.root.maxsize(work.width, work.height)
        return changed

    def fit_saved_geometry(self, geometry: str, default: tuple[int, int] = (1240, 780)) -> str:
        work = monitor_work_area(self.root)
        parsed = parse_geometry(geometry)
        if parsed is None:
            width = min(default[0], round(work.width * 0.9))
            height = min(default[1], round(work.height * 0.9))
            x = work.left + max(0, (work.width - width) // 2)
            y = work.top + max(0, (work.height - height) // 2)
        else:
            width, height, x, y = parsed
        bounded = clamp_geometry(width, height, x, y, work)
        return f"{bounded[0]}x{bounded[1]}+{bounded[2]}+{bounded[3]}"

    def constrain_to_current_work_area(self) -> bool:
        """Keep a window fully visible after Windows reports a monitor/DPI transition."""

        tk = _tk_module()
        try:
            current = self.root.geometry()
        except tk.TclError:
            return False
        fitted = fit_geometry_to_work_area(current, monitor_work_area(self.root))
        if fitted is None or fitted == current:
            return False
        try:
            self.root.geometry(fitted)
        except tk.TclError:
            return False
        return True
