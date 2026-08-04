"""Small keyboard-friendly RGB curve editor for the Tkinter side panel."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence

CurvePoint = tuple[float, float]


class CurveEditor(tk.Canvas):
    """Edit a monotonic set of normalized curve points.

    Left-click adds or drags a point. Right-click removes an interior point.
    The callback runs after a completed gesture, not for every pointer event.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        command: Callable[[tuple[CurvePoint, ...]], None],
        width: int = 280,
        height: int = 170,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            background="#171b22",
            highlightthickness=1,
            highlightbackground="#4b5563",
            takefocus=True,
        )
        self._command = command
        self._points: list[CurvePoint] = [(0.0, 0.0), (1.0, 1.0)]
        self._active: int | None = None
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Button-3>", self._remove)
        self.bind("<Delete>", self._remove_active)
        self.after_idle(self._draw)

    @property
    def points(self) -> tuple[CurvePoint, ...]:
        return tuple(self._points)

    def set_points(self, points: Sequence[Sequence[float]]) -> None:
        normalized = sorted((float(x), float(y)) for x, y in points)
        if len(normalized) < 2:
            normalized = [(0.0, 0.0), (1.0, 1.0)]
        normalized[0] = (0.0, max(0.0, min(1.0, normalized[0][1])))
        normalized[-1] = (1.0, max(0.0, min(1.0, normalized[-1][1])))
        self._points = normalized
        self._active = None
        self._draw()

    def reset(self) -> None:
        self.set_points(((0.0, 0.0), (1.0, 1.0)))
        self._command(self.points)

    def _press(self, event: tk.Event) -> None:
        self.focus_set()
        x, y = self._from_canvas(event.x, event.y)
        hit = self._hit(event.x, event.y)
        if hit is None:
            self._points.append((x, y))
            self._points.sort(key=lambda point: point[0])
            hit = min(range(len(self._points)), key=lambda index: abs(self._points[index][0] - x))
        self._active = hit
        self._draw()

    def _drag(self, event: tk.Event) -> None:
        if self._active is None:
            return
        x, y = self._from_canvas(event.x, event.y)
        if self._active == 0:
            x = 0.0
        elif self._active == len(self._points) - 1:
            x = 1.0
        else:
            x = max(self._points[self._active - 1][0] + 0.002, x)
            x = min(self._points[self._active + 1][0] - 0.002, x)
        self._points[self._active] = (x, y)
        self._draw()

    def _release(self, _event: tk.Event) -> None:
        if self._active is not None:
            self._command(self.points)

    def _remove(self, event: tk.Event) -> None:
        hit = self._hit(event.x, event.y)
        if hit is None or hit in {0, len(self._points) - 1}:
            return
        del self._points[hit]
        self._active = None
        self._draw()
        self._command(self.points)

    def _remove_active(self, _event: tk.Event) -> None:
        if self._active is None or self._active in {0, len(self._points) - 1}:
            return
        del self._points[self._active]
        self._active = None
        self._draw()
        self._command(self.points)

    def _draw(self) -> None:
        self.delete("all")
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        for part in range(1, 4):
            x = round(width * part / 4)
            y = round(height * part / 4)
            self.create_line(x, 0, x, height, fill="#303641")
            self.create_line(0, y, width, y, fill="#303641")
        coords: list[float] = []
        for point in self._points:
            coords.extend(self._to_canvas(*point))
        self.create_line(*coords, fill="#5eead4", width=2, smooth=True)
        for index, point in enumerate(self._points):
            x, y = self._to_canvas(*point)
            fill = "#fbbf24" if index == self._active else "#ccfbf1"
            self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=fill, outline="#0f766e")

    def _hit(self, x: int, y: int) -> int | None:
        for index, point in enumerate(self._points):
            px, py = self._to_canvas(*point)
            if abs(px - x) <= 8 and abs(py - y) <= 8:
                return index
        return None

    def _to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return (x * max(1, self.winfo_width() - 1), (1.0 - y) * max(1, self.winfo_height() - 1))

    def _from_canvas(self, x: int, y: int) -> CurvePoint:
        width = max(1, self.winfo_width() - 1)
        height = max(1, self.winfo_height() - 1)
        return (max(0.0, min(1.0, x / width)), max(0.0, min(1.0, 1.0 - y / height)))
