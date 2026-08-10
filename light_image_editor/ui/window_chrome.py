"""Material You title bar for the rounded Windows 10 application frame."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from PIL import ImageDraw

from ..accessibility import set_accessible_name
from ..windows_effects import BorderlessWindowController
from .antialias import render_photo


class MaterialWindowButton(tk.Canvas):
    """Compact, rounded window-control button with line-drawn icons."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        kind: str,
        accessible_name: str,
        command: Callable[[], None],
    ) -> None:
        super().__init__(
            master,
            width=42,
            height=32,
            highlightthickness=0,
            takefocus=True,
            cursor="hand2",
        )
        self.kind = kind
        self.command = command
        self.accessible_name = accessible_name
        self._hovered = False
        self._surface = "#f7f2fa"
        self._hover = "#e0d7e4"
        self._foreground = "#302d33"
        self._danger = "#b3261e"
        self._on_danger = "#ffffff"
        set_accessible_name(self, accessible_name)
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._invoke)
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)

    def apply_palette(
        self,
        *,
        surface: str,
        hover: str,
        foreground: str,
        danger: str,
        on_danger: str,
    ) -> None:
        self._surface = surface
        self._hover = hover
        self._foreground = foreground
        self._danger = danger
        self._on_danger = on_danger
        self.configure(background=surface)
        self._draw()

    def set_maximized(self, maximized: bool) -> None:
        if self.kind not in {"maximize", "restore"}:
            return
        self.kind = "restore" if maximized else "maximize"
        self.accessible_name = "还原窗口" if maximized else "最大化窗口"
        set_accessible_name(self, self.accessible_name)
        self._draw()

    def _draw(self, _event: tk.Event | None = None) -> None:
        width = max(8, self.winfo_width())
        height = max(8, self.winfo_height())
        is_danger = self.kind == "close" and self._hovered
        fill = self._danger if is_danger else self._hover if self._hovered else self._surface
        foreground = self._on_danger if is_danger else self._foreground
        center_x = width / 2
        center_y = height / 2

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            # Tk can composite transparent PhotoImage pixels as black on some
            # Windows/DPI combinations. Paint the title-bar surface first so
            # the control margins always blend into the surrounding bar.
            draw.rectangle(
                (0, 0, width * scale, height * scale),
                fill=self._surface,
            )
            line_width = max(2, round(1.5 * scale))
            draw.rounded_rectangle(
                (2 * scale, 2 * scale, (width - 2) * scale, (height - 2) * scale),
                radius=14 * scale,
                fill=fill,
            )
            if self.kind == "minimize":
                draw.line(
                    (
                        round((center_x - 6) * scale),
                        round(center_y * scale),
                        round((center_x + 6) * scale),
                        round(center_y * scale),
                    ),
                    fill=foreground,
                    width=line_width,
                )
            elif self.kind == "maximize":
                draw.rectangle(
                    (
                        round((center_x - 5) * scale),
                        round((center_y - 5) * scale),
                        round((center_x + 5) * scale),
                        round((center_y + 5) * scale),
                    ),
                    outline=foreground,
                    width=line_width,
                )
            elif self.kind == "restore":
                draw.rectangle(
                    (
                        round((center_x - 3) * scale),
                        round((center_y - 6) * scale),
                        round((center_x + 6) * scale),
                        round((center_y + 3) * scale),
                    ),
                    outline=foreground,
                    width=line_width,
                )
                draw.rectangle(
                    (
                        round((center_x - 6) * scale),
                        round((center_y - 3) * scale),
                        round((center_x + 3) * scale),
                        round((center_y + 6) * scale),
                    ),
                    fill=fill,
                    outline=foreground,
                    width=line_width,
                )
            else:
                draw.line(
                    (
                        round((center_x - 5) * scale),
                        round((center_y - 5) * scale),
                        round((center_x + 5) * scale),
                        round((center_y + 5) * scale),
                    ),
                    fill=foreground,
                    width=line_width,
                )
                draw.line(
                    (
                        round((center_x + 5) * scale),
                        round((center_y - 5) * scale),
                        round((center_x - 5) * scale),
                        round((center_y + 5) * scale),
                    ),
                    fill=foreground,
                    width=line_width,
                )

        self.delete("all")
        self._control_photo = render_photo(width, height, paint, master=self)
        self.create_image(0, 0, image=self._control_photo, anchor="nw")

    def _enter(self, _event: tk.Event) -> None:
        self._hovered = True
        self._draw()

    def _leave(self, _event: tk.Event) -> None:
        self._hovered = False
        self._draw()

    def _invoke(self, _event: tk.Event) -> str:
        self.focus_set()
        self.command()
        return "break"


class MaterialTitleBar(tk.Frame):
    """Chinese-only application title bar for the rounded custom window."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        titlevariable: tk.StringVar,
        controller: BorderlessWindowController,
        close_command: Callable[[], None],
    ) -> None:
        super().__init__(master, height=42, borderwidth=0, highlightthickness=0)
        self.controller = controller
        self.pack_propagate(False)
        self._surface = "#f7f2fa"
        self._foreground = "#302d33"
        self._muted = "#79727d"
        self._accent = "#67507a"
        self._border = "#ddd8df"

        self.content = tk.Frame(self, borderwidth=0, highlightthickness=0)
        self.content.pack(fill="both", expand=True, padx=(12, 7), pady=(4, 4))
        self.app_mark = tk.Canvas(
            self.content,
            width=26,
            height=26,
            highlightthickness=0,
            takefocus=False,
        )
        self.app_mark.pack(side="left", padx=(2, 8))
        self.title_label = tk.Label(
            self.content,
            textvariable=titlevariable,
            anchor="w",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.title_label.pack(side="left")
        self.drag_space = tk.Frame(self.content, borderwidth=0, highlightthickness=0)
        self.drag_space.pack(side="left", fill="both", expand=True)

        self.close_button = MaterialWindowButton(
            self.content,
            kind="close",
            accessible_name="关闭窗口",
            command=close_command,
        )
        self.close_button.pack(side="right", padx=(2, 0))
        self.maximize_button = MaterialWindowButton(
            self.content,
            kind="maximize",
            accessible_name="最大化窗口",
            command=controller.toggle_maximize,
        )
        self.maximize_button.pack(side="right", padx=2)
        self.minimize_button = MaterialWindowButton(
            self.content,
            kind="minimize",
            accessible_name="最小化窗口",
            command=controller.minimize,
        )
        self.minimize_button.pack(side="right", padx=2)
        self.bottom_border = tk.Frame(self, height=1, borderwidth=0)
        self.bottom_border.place(relx=0, rely=1, relwidth=1, y=-1)

        controller.bind_drag_surface(
            self,
            self.content,
            self.app_mark,
            self.title_label,
            self.drag_space,
        )
        controller.set_state_listener(self.set_maximized)

    def apply_palette(
        self,
        *,
        surface: str,
        foreground: str,
        muted: str,
        accent: str,
        hover: str,
        border: str,
        danger: str,
        on_danger: str,
    ) -> None:
        self._surface = surface
        self._foreground = foreground
        self._muted = muted
        self._accent = accent
        self._border = border
        self.controller.set_titlebar_handle_background(surface)
        for widget in (self, self.content, self.drag_space):
            widget.configure(background=surface)
        self.app_mark.configure(background=surface)
        self.title_label.configure(background=surface, foreground=foreground)
        self.bottom_border.configure(background=border)
        for button in (
            self.minimize_button,
            self.maximize_button,
            self.close_button,
        ):
            button.apply_palette(
                surface=surface,
                hover=hover,
                foreground=foreground,
                danger=danger,
                on_danger=on_danger,
            )
        self._draw_mark()

    def set_maximized(self, maximized: bool) -> None:
        self.maximize_button.set_maximized(maximized)

    def _draw_mark(self) -> None:
        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            # Keep the antialiased mark opaque at its outer pixels. Transparent
            # margins can otherwise show up as a black square in Tk on Windows.
            draw.rectangle(
                (0, 0, 26 * scale, 26 * scale),
                fill=self._surface,
            )
            draw.ellipse(
                (2 * scale, 2 * scale, 24 * scale, 24 * scale),
                fill=self._accent,
            )
            draw.polygon(
                (
                    (6 * scale, 18 * scale),
                    (11 * scale, 11 * scale),
                    (15 * scale, 15 * scale),
                    (19 * scale, 9 * scale),
                    (22 * scale, 18 * scale),
                ),
                fill="#ffffff",
            )
            draw.ellipse(
                (7 * scale, 6 * scale, 10 * scale, 9 * scale),
                fill="#ffffff",
            )

        self.app_mark.delete("all")
        self._mark_photo = render_photo(26, 26, paint, master=self.app_mark)
        self.app_mark.create_image(0, 0, image=self._mark_photo, anchor="nw")
