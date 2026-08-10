"""Native Windows presentation effects for the main Tk window."""

from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

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


def _set_native_window_position(
    handle: int | None,
    left: int,
    top: int,
) -> bool:
    """Move a top-level without routing every pointer sample through Tcl."""

    if os.name != "nt" or handle is None:
        return False
    try:
        user32 = ctypes.windll.user32
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
        swp_nosize = 0x0001
        swp_nozorder = 0x0004
        swp_noactivate = 0x0010
        moved = bool(
            user32.SetWindowPos(
                ctypes.c_void_p(handle),
                None,
                int(left),
                int(top),
                0,
                0,
                swp_nosize | swp_nozorder | swp_noactivate,
            )
        )
        if moved:
            try:
                dwmapi = ctypes.windll.dwmapi
                dwmapi.DwmFlush.argtypes = ()
                dwmapi.DwmFlush.restype = ctypes.c_long
                dwmapi.DwmFlush()
            except (AttributeError, OSError):
                pass
        return moved
    except (AttributeError, OSError, tk.TclError):
        return False


def _set_native_window_bounds(
    handle: int | None,
    left: int,
    top: int,
    width: int,
    height: int,
) -> bool:
    """Resize and move a top-level without a Tcl geometry round trip."""

    if os.name != "nt" or handle is None:
        return False
    try:
        user32 = ctypes.windll.user32
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
        swp_nozorder = 0x0004
        swp_noactivate = 0x0010
        return bool(
            user32.SetWindowPos(
                ctypes.c_void_p(handle),
                None,
                int(left),
                int(top),
                max(1, int(width)),
                max(1, int(height)),
                swp_nozorder | swp_noactivate,
            )
        )
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
    edge_size: int = 9

    def __post_init__(self) -> None:
        self._style_job: str | None = None
        self._drag_offset: tuple[int, int] | None = None
        self._drag_frame_job: str | None = None
        self._pending_drag_position: tuple[int, int] | None = None
        self._drag_window_handle: int | None = None
        self._drag_ratio = 0.5
        self._resize_direction = ""
        self._resize_origin: tuple[int, int, int, int, int, int] | None = None
        self._resize_frame_job: str | None = None
        self._pending_resize_bounds: tuple[int, int, int, int] | None = None
        self._resize_window_handle: int | None = None
        self._restore_rect: tuple[int, int, int, int] | None = None
        self._maximized = False
        self._fullscreen = False
        self._fullscreen_rect: tuple[int, int, int, int] | None = None
        self._fullscreen_was_maximized = False
        self._state_listener: Callable[[bool], None] | None = None
        self._resize_handles: dict[str, tk.Frame] = {}
        self._resize_handle_layouts: dict[str, dict[str, Any]] = {}
        self._titlebar_handle_background: str | None = None
        self._body_handle_background: str | None = None
        self._handle_raise_job: str | None = None
        self.window.overrideredirect(True)
        self._create_resize_handles()
        self.window.bind("<Map>", self._mapped, add="+")
        self.window.bind("<Configure>", self._window_configured, add="+")
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

    @property
    def moving(self) -> bool:
        return self._drag_offset is not None

    @property
    def resizing(self) -> bool:
        return self._resize_origin is not None

    def set_state_listener(self, listener: Callable[[bool], None]) -> None:
        self._state_listener = listener
        listener(self._maximized)

    def set_titlebar_handle_background(self, background: str) -> None:
        """Blend the top resize hit targets into the custom title bar."""

        self._titlebar_handle_background = background
        for direction in ("n", "nw", "ne"):
            handle = self._resize_handles.get(direction)
            if handle is not None:
                handle.configure(background=background)

    def set_resize_handle_backgrounds(
        self,
        *,
        titlebar: str,
        body: str,
    ) -> None:
        """Blend invisible resize targets with the surface beneath each edge."""

        self._titlebar_handle_background = titlebar
        self._body_handle_background = body
        for direction, handle in self._resize_handles.items():
            handle.configure(background=titlebar if direction in {"n", "nw", "ne"} else body)

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
        self._set_resize_handles_visible(False)
        self._notify_state()
        self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")

    def restore(self) -> None:
        if not self._maximized or self._fullscreen:
            return
        restore = self._restore_rect
        self._maximized = False
        self._set_resize_handles_visible(True)
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
            self._set_resize_handles_visible(False)
            self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")
            return True
        restore = self._fullscreen_rect
        self._fullscreen = False
        self._maximized = self._fullscreen_was_maximized
        self._set_resize_handles_visible(not self._maximized)
        self._notify_state()
        if restore is not None:
            left, top, width, height = restore
            self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")
        return True

    def _mapped(self, event: tk.Event) -> None:
        if event.widget is self.window:
            self.schedule_native_style()
            self._set_resize_handles_visible(not self._maximized and not self._fullscreen)

    def _window_configured(self, event: tk.Event) -> None:
        if (
            event.widget is self.window
            and not self.moving
            and not self.resizing
            and not self._maximized
            and not self._fullscreen
        ):
            self._schedule_raise_resize_handles()

    def _create_resize_handles(self) -> None:
        edge = max(6, int(self.edge_size))
        corner = max(14, edge * 2)
        layouts: dict[str, dict[str, Any]] = {
            "n": {
                "relx": 0.0,
                "rely": 0.0,
                "x": corner,
                "anchor": "nw",
                "relwidth": 1.0,
                "width": -2 * corner,
                "height": edge,
            },
            "s": {
                "relx": 0.0,
                "rely": 1.0,
                "x": corner,
                "anchor": "sw",
                "relwidth": 1.0,
                "width": -2 * corner,
                "height": edge,
            },
            "w": {
                "relx": 0.0,
                "rely": 0.0,
                "y": corner,
                "anchor": "nw",
                "relheight": 1.0,
                "height": -2 * corner,
                "width": edge,
            },
            "e": {
                "relx": 1.0,
                "rely": 0.0,
                "y": corner,
                "anchor": "ne",
                "relheight": 1.0,
                "height": -2 * corner,
                "width": edge,
            },
            "nw": {"relx": 0.0, "rely": 0.0, "anchor": "nw", "width": corner, "height": corner},
            "ne": {"relx": 1.0, "rely": 0.0, "anchor": "ne", "width": corner, "height": corner},
            "sw": {"relx": 0.0, "rely": 1.0, "anchor": "sw", "width": corner, "height": corner},
            "se": {"relx": 1.0, "rely": 1.0, "anchor": "se", "width": corner, "height": corner},
        }
        cursors = {
            "n": "size_ns",
            "s": "size_ns",
            "e": "size_we",
            "w": "size_we",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }
        background = str(self.window.cget("background"))
        for direction, layout in layouts.items():
            handle = tk.Frame(
                self.window,
                borderwidth=0,
                highlightthickness=0,
                background=background,
                cursor=cursors[direction],
                takefocus=False,
            )
            handle.bind(
                "<ButtonPress-1>",
                partial(self._begin_handle_resize, direction),
            )
            handle.bind("<B1-Motion>", self._resize)
            handle.bind("<ButtonRelease-1>", self._end_resize)
            self._resize_handles[direction] = handle
            self._resize_handle_layouts[direction] = layout
            handle.place(**layout)
        self._schedule_raise_resize_handles()

    def _set_resize_handles_visible(self, visible: bool) -> None:
        for direction, handle in self._resize_handles.items():
            if visible:
                handle.place(**self._resize_handle_layouts[direction])
            else:
                handle.place_forget()
        if visible:
            self._schedule_raise_resize_handles()

    def _schedule_raise_resize_handles(self) -> None:
        if self._handle_raise_job is not None:
            try:
                self.window.after_cancel(self._handle_raise_job)
            except tk.TclError:
                pass
            self._handle_raise_job = None
        try:
            self._handle_raise_job = self.window.after(80, self._raise_resize_handles)
        except tk.TclError:
            self._handle_raise_job = None

    def _raise_resize_handles(self) -> None:
        self._handle_raise_job = None
        try:
            background = self._body_handle_background or str(self.window.cget("background"))
            for direction, handle in self._resize_handles.items():
                if handle.winfo_ismapped():
                    handle.configure(
                        background=(
                            self._titlebar_handle_background
                            if direction in {"n", "nw", "ne"}
                            and self._titlebar_handle_background is not None
                            else background
                        )
                    )
                    handle.lift()
        except tk.TclError:
            return

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
            self._drag_window_handle = _root_window_handle(self.window)
        except tk.TclError:
            self._drag_window_handle = None
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
        if (
            self._drag_offset is not None
            and self._drag_window_handle is not None
            and not self._maximized
        ):
            # A Win32 region on an override-redirect window can leave stale
            # title-bar strips while the surface moves quickly.  Use a square
            # compositor surface during the gesture and restore rounding once.
            _clear_window_region(self._drag_window_handle)
            try:
                self.window.event_generate("<<WindowMoveStarted>>")
            except tk.TclError:
                pass
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
        self._pending_drag_position = (left, top)
        if self._drag_frame_job is None:
            # Keep Tk's event loop alive, but commit no more than one position
            # per 60 Hz frame.  _set_native_window_position waits for DWM so a
            # captured/displayed frame cannot contain several stale title bars.
            self._drag_frame_job = self.window.after(16, self._flush_drag_position)
        return "break"

    def _flush_drag_position(self) -> None:
        self._drag_frame_job = None
        position = self._pending_drag_position
        self._pending_drag_position = None
        if position is None:
            return
        left, top = position
        if not _set_native_window_position(self._drag_window_handle, left, top):
            self.window.geometry(f"{left:+d}{top:+d}")

    def _end_drag(self, _event: tk.Event) -> str:
        if self._drag_frame_job is not None:
            try:
                self.window.after_cancel(self._drag_frame_job)
            except tk.TclError:
                pass
            self._drag_frame_job = None
        self._flush_drag_position()
        self._drag_offset = None
        self._drag_window_handle = None
        try:
            self.window.after_idle(lambda: self.window.event_generate("<<WindowMoveFinished>>"))
        except tk.TclError:
            pass
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
            self._resize_window_handle = _root_window_handle(self.window)
            self._resize_origin = (
                event.x_root,
                event.y_root,
                self.window.winfo_x(),
                self.window.winfo_y(),
                self.window.winfo_width(),
                self.window.winfo_height(),
            )
        except tk.TclError:
            self._resize_window_handle = None
            return None
        if self._resize_window_handle is not None:
            _clear_window_region(self._resize_window_handle)
        self._resize_direction = direction
        self._notify_resize_started()
        return "break"

    def _begin_handle_resize(self, direction: str, event: tk.Event) -> str:
        if self._maximized or self._fullscreen:
            return "break"
        try:
            self._resize_window_handle = _root_window_handle(self.window)
            self._resize_origin = (
                event.x_root,
                event.y_root,
                self.window.winfo_x(),
                self.window.winfo_y(),
                self.window.winfo_width(),
                self.window.winfo_height(),
            )
        except tk.TclError:
            self._resize_window_handle = None
            self._resize_origin = None
            self._resize_direction = ""
            return "break"
        if self._resize_window_handle is not None:
            _clear_window_region(self._resize_window_handle)
        self._resize_direction = direction
        self._notify_resize_started()
        return "break"

    def _notify_resize_started(self) -> None:
        try:
            self.window.event_generate("<<WindowResizeStarted>>")
        except tk.TclError:
            pass

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
        self._pending_resize_bounds = (new_left, new_top, new_width, new_height)
        if self._resize_frame_job is None:
            # A stable 60 Hz ceiling leaves Tk enough time to complete its
            # geometry-manager pass between native WM_SIZE notifications.
            self._resize_frame_job = self.window.after(16, self._flush_resize_bounds)
        return "break"

    def _flush_resize_bounds(self) -> None:
        self._resize_frame_job = None
        bounds = self._pending_resize_bounds
        self._pending_resize_bounds = None
        if bounds is None:
            return
        left, top, width, height = bounds
        used_native_resize = _set_native_window_bounds(
            self._resize_window_handle,
            left,
            top,
            width,
            height,
        )
        if not used_native_resize:
            self.window.geometry(f"{width}x{height}{left:+d}{top:+d}")
        try:
            # Configure/pack/place work is normally deferred until Tk is idle.
            # Dense pointer input can starve that work, leaving the title bar
            # and toolbar visibly behind the already-resized native window.
            self.window.update_idletasks()
        except tk.TclError:
            pass

    def _end_resize(self, _event: tk.Event) -> str | None:
        if self._resize_origin is None:
            return None
        if self._resize_frame_job is not None:
            try:
                self.window.after_cancel(self._resize_frame_job)
            except tk.TclError:
                pass
            self._resize_frame_job = None
        self._flush_resize_bounds()
        self._resize_origin = None
        self._resize_direction = ""
        self._resize_window_handle = None
        try:
            self.window.after_idle(lambda: self.window.event_generate("<<WindowResizeFinished>>"))
        except tk.TclError:
            pass
        return "break"

    def _update_resize_cursor(self, event: tk.Event) -> None:
        if self._maximized or self._fullscreen or self.moving or self.resizing:
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
    resizing_provider: Callable[[], bool] | None = None
    moving_provider: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        self._job: str | None = None
        self._last_region: tuple[int, int, int] | None = None
        self._last_configure_size: tuple[int, int] | None = None
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
        if self.resizing_provider is not None and self.resizing_provider():
            return
        if self.moving_provider is not None and self.moving_provider():
            return
        if _event is not None and _event.type == tk.EventType.Configure:
            current_size = (max(1, int(_event.width)), max(1, int(_event.height)))
            if current_size == self._last_configure_size:
                return
            self._last_configure_size = current_size
        if self._job is not None:
            return
        try:
            self._job = self.window.after(16, self.apply)
        except tk.TclError:
            self._job = None

    def invalidate(self) -> None:
        """Forget a cleared native region and restore it on the next frame."""

        self._last_region = None
        self.schedule()

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
