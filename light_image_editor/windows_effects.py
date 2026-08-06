"""Native Windows presentation effects for the main Tk window."""

from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SW_MINIMIZE = 6
SW_SHOW = 5
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
MONITOR_DEFAULTTONEAREST = 2


class _Rect(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


class _MonitorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", ctypes.c_ulong),
    )


def should_use_borderless_chrome() -> bool:
    """Use custom chrome where Windows cannot round a decorated window."""

    if os.name != "nt" or os.environ.get("IMAGE_EDITOR_NATIVE_FRAME") == "1":
        return False
    if os.environ.get("IMAGE_EDITOR_FORCE_CUSTOM_CHROME") == "1":
        return True
    try:
        return sys.getwindowsversion().build < 22_000
    except (AttributeError, OSError):
        return False


def rounded_region_metrics(
    width: int,
    height: int,
    radius: int,
    scale: float = 1.0,
) -> tuple[int, int, int]:
    """Return safe physical dimensions and corner diameter for a native region."""

    safe_width = max(1, int(width))
    safe_height = max(1, int(height))
    scaled_radius = max(1, round(radius * max(0.25, scale)))
    scaled_radius = min(scaled_radius, safe_width // 2, safe_height // 2)
    return safe_width, safe_height, scaled_radius * 2


def _root_window_handle(window: tk.Tk | tk.Toplevel) -> int:
    window.update_idletasks()
    handle = int(window.winfo_id())
    if os.name != "nt":
        return handle
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        user32.GetAncestor.restype = ctypes.c_void_p
        root_handle = user32.GetAncestor(ctypes.c_void_p(handle), GA_ROOT)
    except (AttributeError, OSError):
        return handle
    return int(root_handle or handle)


def _enable_dwm_rounding(handle: int) -> bool:
    """Use the Windows 11 compositor corner preference when it is supported."""

    try:
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        )
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        preference = ctypes.c_int(DWMWCP_ROUND)
        result = dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(handle),
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )
    except (AttributeError, OSError):
        return False
    return result == 0


def _clear_window_region(handle: int) -> bool:
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowRgn.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)
        user32.SetWindowRgn.restype = ctypes.c_int
        return bool(user32.SetWindowRgn(ctypes.c_void_p(handle), None, True))
    except (AttributeError, OSError):
        return False


def _apply_rounded_region(
    handle: int,
    radius: int,
    scale: float,
    current: tuple[int, int, int] | None = None,
) -> tuple[int, int, int] | None:
    """Clip a decorated window to a rounded region for Windows 10."""

    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        user32.GetWindowRect.argtypes = (ctypes.c_void_p, ctypes.POINTER(_Rect))
        user32.GetWindowRect.restype = ctypes.c_bool
        user32.SetWindowRgn.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)
        user32.SetWindowRgn.restype = ctypes.c_int
        user32.IsIconic.argtypes = (ctypes.c_void_p,)
        user32.IsIconic.restype = ctypes.c_bool
        gdi32.CreateRoundRectRgn.argtypes = (
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        )
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        gdi32.DeleteObject.argtypes = (ctypes.c_void_p,)
        gdi32.DeleteObject.restype = ctypes.c_bool
        native_handle = ctypes.c_void_p(handle)
        if user32.IsIconic(native_handle):
            return None
        rect = _Rect()
        if not user32.GetWindowRect(native_handle, ctypes.byref(rect)):
            return None
        width, height, diameter = rounded_region_metrics(
            rect.right - rect.left,
            rect.bottom - rect.top,
            radius,
            scale,
        )
        metrics = (width, height, diameter)
        if metrics == current:
            return metrics
        region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter)
        if not region:
            return None
        if user32.SetWindowRgn(native_handle, region, True):
            # Windows owns the region after a successful SetWindowRgn call.
            return metrics
        gdi32.DeleteObject(region)
    except (AttributeError, OSError):
        return None
    return None


def _is_zoomed(handle: int) -> bool:
    try:
        user32 = ctypes.windll.user32
        user32.IsZoomed.argtypes = (ctypes.c_void_p,)
        user32.IsZoomed.restype = ctypes.c_bool
        return bool(user32.IsZoomed(ctypes.c_void_p(handle)))
    except (AttributeError, OSError):
        return False


def _monitor_work_area(handle: int) -> _Rect | None:
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        user32.GetMonitorInfoW.restype = ctypes.c_bool
        monitor = user32.MonitorFromWindow(
            ctypes.c_void_p(handle),
            MONITOR_DEFAULTTONEAREST,
        )
        if not monitor:
            return None
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return info.rcWork
    except (AttributeError, OSError):
        return None


def _monitor_bounds(handle: int) -> _Rect | None:
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        user32.GetMonitorInfoW.restype = ctypes.c_bool
        monitor = user32.MonitorFromWindow(
            ctypes.c_void_p(handle),
            MONITOR_DEFAULTTONEAREST,
        )
        if not monitor:
            return None
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return info.rcMonitor
    except (AttributeError, OSError):
        return None


def _show_window(handle: int, command: int) -> bool:
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
        user32.ShowWindow.restype = ctypes.c_bool
        user32.ShowWindow(ctypes.c_void_p(handle), command)
        return True
    except (AttributeError, OSError):
        return False


def _promote_to_taskbar_window(handle: int) -> bool:
    """Convert Tk's override-redirect tool window into a normal taskbar app."""

    try:
        user32 = ctypes.windll.user32
        getter = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        setter = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        getter.argtypes = (ctypes.c_void_p, ctypes.c_int)
        getter.restype = ctypes.c_ssize_t
        setter.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t)
        setter.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        )
        user32.SetWindowPos.restype = ctypes.c_bool
        native_handle = ctypes.c_void_p(handle)
        current = int(getter(native_handle, GWL_EXSTYLE))
        desired = (current & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
        if desired != current:
            setter(native_handle, GWL_EXSTYLE, desired)
        _show_window(handle, SW_SHOW)
        return bool(
            user32.SetWindowPos(
                native_handle,
                None,
                0,
                0,
                0,
                0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
            )
        )
    except (AttributeError, OSError):
        return False


@dataclass
class BorderlessWindowController:
    """Window movement and sizing for the Windows 10 rounded custom frame."""

    window: tk.Tk | tk.Toplevel
    edge_size: int = 7

    def __post_init__(self) -> None:
        self._style_job: str | None = None
        self._drag_offset: tuple[int, int] | None = None
        self._drag_ratio = 0.5
        self._resize_direction = ""
        self._resize_origin: tuple[int, int, int, int, int, int] | None = None
        self._restore_rect: tuple[int, int, int, int] | None = None
        self._maximized = False
        self._fullscreen = False
        self._fullscreen_rect: tuple[int, int, int, int] | None = None
        self._fullscreen_was_maximized = False
        self._state_listener: Callable[[bool], None] | None = None
        self.window.overrideredirect(True)
        self.window.bind("<Map>", self._mapped, add="+")
        self.window.bind("<ButtonPress-1>", self._begin_resize, add="+")
        self.window.bind("<B1-Motion>", self._resize, add="+")
        self.window.bind("<ButtonRelease-1>", self._end_resize, add="+")
        self.window.bind("<Motion>", self._update_resize_cursor, add="+")
        self.schedule_native_style()

    @property
    def maximized(self) -> bool:
        return self._maximized

    @property
    def fullscreen(self) -> bool:
        return self._fullscreen

    def set_state_listener(self, listener: Callable[[bool], None]) -> None:
        self._state_listener = listener
        listener(self._maximized)

    def bind_drag_surface(self, *widgets: tk.Misc) -> None:
        for widget in widgets:
            widget.bind("<ButtonPress-1>", self._start_drag, add="+")
            widget.bind("<B1-Motion>", self._drag, add="+")
            widget.bind("<ButtonRelease-1>", self._end_drag, add="+")
            widget.bind("<Double-Button-1>", self._toggle_from_titlebar, add="+")

    def schedule_native_style(self) -> None:
        if os.name != "nt":
            return
        if self._style_job is not None:
            try:
                self.window.after_cancel(self._style_job)
            except tk.TclError:
                pass
        try:
            self._style_job = self.window.after_idle(self._ensure_native_style)
        except tk.TclError:
            self._style_job = None

    def minimize(self) -> None:
        if self._fullscreen:
            return
        try:
            handle = _root_window_handle(self.window)
        except tk.TclError:
            return
        _show_window(handle, SW_MINIMIZE)

    def toggle_maximize(self) -> None:
        if self._fullscreen:
            return
        if self._maximized:
            self.restore()
        else:
            self.maximize()

    def maximize(self) -> None:
        if self._maximized or self._fullscreen:
            return
        try:
            self.window.update_idletasks()
            handle = _root_window_handle(self.window)
            self._restore_rect = (
                self.window.winfo_x(),
                self.window.winfo_y(),
                self.window.winfo_width(),
                self.window.winfo_height(),
            )
        except tk.TclError:
            return
        work = _monitor_work_area(handle)
        if work is None:
            left = top = 0
            width = self.window.winfo_screenwidth()
            height = self.window.winfo_screenheight()
        else:
            left, top = work.left, work.top
            width = max(1, work.right - work.left)
            height = max(1, work.bottom - work.top)
        self._maximized = True
        self._notify_state()
        self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")

    def restore(self) -> None:
        if not self._maximized or self._fullscreen:
            return
        restore = self._restore_rect
        self._maximized = False
        self._notify_state()
        if restore is None:
            return
        left, top, width, height = restore
        self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")

    def set_fullscreen(self, enabled: bool) -> bool:
        if enabled == self._fullscreen:
            return True
        try:
            self.window.update_idletasks()
            handle = _root_window_handle(self.window)
        except tk.TclError:
            return False
        if enabled:
            self._fullscreen_rect = (
                self.window.winfo_x(),
                self.window.winfo_y(),
                self.window.winfo_width(),
                self.window.winfo_height(),
            )
            self._fullscreen_was_maximized = self._maximized
            bounds = _monitor_bounds(handle)
            if bounds is None:
                left = top = 0
                width = self.window.winfo_screenwidth()
                height = self.window.winfo_screenheight()
            else:
                left, top = bounds.left, bounds.top
                width = max(1, bounds.right - bounds.left)
                height = max(1, bounds.bottom - bounds.top)
            self._fullscreen = True
            self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")
            return True
        restore = self._fullscreen_rect
        self._fullscreen = False
        self._maximized = self._fullscreen_was_maximized
        self._notify_state()
        if restore is not None:
            left, top, width, height = restore
            self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")
        return True

    def _mapped(self, event: tk.Event) -> None:
        if event.widget is self.window:
            self.schedule_native_style()

    def _ensure_native_style(self) -> None:
        self._style_job = None
        try:
            if not self.window.winfo_exists():
                return
            handle = _root_window_handle(self.window)
        except tk.TclError:
            return
        _promote_to_taskbar_window(handle)

    def _notify_state(self) -> None:
        if self._state_listener is not None:
            self._state_listener(self._maximized)

    def _start_drag(self, event: tk.Event) -> str:
        if self._fullscreen:
            return "break"
        try:
            width = max(1, self.window.winfo_width())
            self._drag_ratio = max(
                0.0,
                min(1.0, (event.x_root - self.window.winfo_rootx()) / width),
            )
            self._drag_offset = (
                event.x_root - self.window.winfo_x(),
                event.y_root - self.window.winfo_y(),
            )
        except tk.TclError:
            self._drag_offset = None
        return "break"

    def _drag(self, event: tk.Event) -> str:
        if self._drag_offset is None:
            return "break"
        if self._maximized:
            restore = self._restore_rect
            if restore is None:
                return "break"
            _left, _top, width, height = restore
            left = event.x_root - round(width * self._drag_ratio)
            top = event.y_root - min(22, max(8, self._drag_offset[1]))
            self._maximized = False
            self._notify_state()
            self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")
            self._drag_offset = (event.x_root - left, event.y_root - top)
            return "break"
        left = event.x_root - self._drag_offset[0]
        top = event.y_root - self._drag_offset[1]
        self.window.geometry(f"{left:+d}{top:+d}")
        return "break"

    def _end_drag(self, _event: tk.Event) -> str:
        self._drag_offset = None
        return "break"

    def _toggle_from_titlebar(self, _event: tk.Event) -> str:
        self.toggle_maximize()
        return "break"

    def _edge_at(self, x_root: int, y_root: int) -> str:
        try:
            left = self.window.winfo_rootx()
            top = self.window.winfo_rooty()
            width = self.window.winfo_width()
            height = self.window.winfo_height()
        except tk.TclError:
            return ""
        local_x = x_root - left
        local_y = y_root - top
        horizontal = (
            "w" if local_x <= self.edge_size else "e" if local_x >= width - self.edge_size else ""
        )
        vertical = (
            "n" if local_y <= self.edge_size else "s" if local_y >= height - self.edge_size else ""
        )
        return vertical + horizontal

    def _begin_resize(self, event: tk.Event) -> str | None:
        if self._maximized or self._fullscreen:
            return None
        direction = self._edge_at(event.x_root, event.y_root)
        if not direction:
            return None
        try:
            self._resize_origin = (
                event.x_root,
                event.y_root,
                self.window.winfo_x(),
                self.window.winfo_y(),
                self.window.winfo_width(),
                self.window.winfo_height(),
            )
        except tk.TclError:
            return None
        self._resize_direction = direction
        return "break"

    def _resize(self, event: tk.Event) -> str | None:
        origin = self._resize_origin
        direction = self._resize_direction
        if origin is None or not direction:
            return None
        start_x, start_y, left, top, width, height = origin
        delta_x = event.x_root - start_x
        delta_y = event.y_root - start_y
        min_width, min_height = self.window.minsize()
        new_left, new_top, new_width, new_height = left, top, width, height
        if "e" in direction:
            new_width = max(min_width, width + delta_x)
        if "s" in direction:
            new_height = max(min_height, height + delta_y)
        if "w" in direction:
            new_width = max(min_width, width - delta_x)
            new_left = left + width - new_width
        if "n" in direction:
            new_height = max(min_height, height - delta_y)
            new_top = top + height - new_height
        self.window.geometry(f"{new_width}x{new_height}{new_left:+d}{new_top:+d}")
        return "break"

    def _end_resize(self, _event: tk.Event) -> str | None:
        if self._resize_origin is None:
            return None
        self._resize_origin = None
        self._resize_direction = ""
        return "break"

    def _update_resize_cursor(self, event: tk.Event) -> None:
        if self._maximized or self._fullscreen or self._drag_offset is not None:
            return
        direction = self._edge_at(event.x_root, event.y_root)
        cursor = {
            "n": "size_ns",
            "s": "size_ns",
            "e": "size_we",
            "w": "size_we",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }.get(direction, "")
        try:
            self.window.configure(cursor=cursor)
        except tk.TclError:
            pass


@dataclass
class RoundedWindowEffect:
    """Keep a native Tk top-level rounded across maps, restores and resizes."""

    window: tk.Tk | tk.Toplevel
    radius: int = 28
    allow_region_fallback: bool = False
    maximized_provider: Callable[[], bool] | None = None
    fullscreen_provider: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        self._job: str | None = None
        self._last_region: tuple[int, int, int] | None = None
        self._dwm_supported: bool | None = None
        self.window.bind("<Map>", self.schedule, add="+")
        self.window.bind("<Configure>", self.schedule, add="+")
        self.schedule()

    @property
    def active(self) -> bool:
        return os.name == "nt"

    def schedule(self, _event: tk.Event | None = None) -> None:
        if not self.active:
            return
        if self._job is not None:
            return
        try:
            self._job = self.window.after(16, self.apply)
        except tk.TclError:
            self._job = None

    def apply(self) -> None:
        self._job = None
        if not self.active:
            return
        try:
            if not self.window.winfo_exists():
                return
            handle = _root_window_handle(self.window)
            fullscreen = bool(self.window.attributes("-fullscreen"))
            scale = max(0.25, float(self.window.winfo_fpixels("1i")) / 96.0)
        except (tk.TclError, TypeError, ValueError):
            return
        fullscreen = fullscreen or bool(self.fullscreen_provider and self.fullscreen_provider())
        maximized = bool(self.maximized_provider and self.maximized_provider())
        if fullscreen or maximized or _is_zoomed(handle):
            if self._last_region is not None:
                _clear_window_region(handle)
                self._last_region = None
            return
        if self._dwm_supported is None:
            self._dwm_supported = _enable_dwm_rounding(handle)
        if self._dwm_supported:
            if self._last_region is not None:
                _clear_window_region(handle)
                self._last_region = None
            return
        if not self.allow_region_fallback:
            return
        region = _apply_rounded_region(handle, self.radius, scale, self._last_region)
        if region is not None:
            self._last_region = region
