"""Material You inspired widgets shared by the desktop editor."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from PIL import ImageDraw

from .antialias import render_photo


def rounded_rectangle_points(
    left: float,
    top: float,
    right: float,
    bottom: float,
    radius: float,
) -> tuple[float, ...]:
    """Return polygon points suitable for a smooth rounded rectangle."""

    radius = max(0.0, min(radius, (right - left) / 2, (bottom - top) / 2))
    return (
        left + radius,
        top,
        right - radius,
        top,
        right,
        top,
        right,
        top + radius,
        right,
        bottom - radius,
        right,
        bottom,
        right - radius,
        bottom,
        left + radius,
        bottom,
        left,
        bottom,
        left,
        bottom - radius,
        left,
        top + radius,
        left,
        top,
    )


class MaterialCommandDock(tk.Canvas):
    """Bottom command surface with a Material You capsule silhouette."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, height=64, highlightthickness=0, takefocus=False)
        self.content = ttk.Frame(self, style="Dock.TFrame", padding=(10, 5))
        self._content_window = self.create_window(0, 0, window=self.content, anchor="center")
        self._background = "#f7f2fa"
        self._container = "#ece6f0"
        self._border = "#cac4d0"
        self.bind("<Configure>", self._layout)

    def apply_palette(self, *, background: str, container: str, border: str) -> None:
        self._background = background
        self._container = container
        self._border = border
        self.configure(background=background)
        self._draw_capsule()

    def _layout(self, _event: tk.Event | None = None) -> None:
        self.update_idletasks()
        width = max(1, self.winfo_width())
        content_width = min(
            max(1, self.content.winfo_reqwidth()),
            max(1, width - 28),
            920,
        )
        content_height = min(max(1, self.content.winfo_reqheight()), 46)
        self.coords(self._content_window, width / 2, 32)
        self.itemconfigure(
            self._content_window,
            width=content_width,
            height=content_height,
        )
        self._draw_capsule()

    def _draw_capsule(self) -> None:
        width = max(1, self.winfo_width())
        height = max(64, self.winfo_height())
        requested = max(140, self.content.winfo_reqwidth() + 20)
        capsule_width = min(requested, max(1, width - 20), 940)
        left = (width - capsule_width) / 2
        top = 4
        right = left + capsule_width
        bottom = 60

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            draw.rounded_rectangle(
                (
                    round(left * scale),
                    top * scale,
                    round(right * scale),
                    bottom * scale,
                ),
                radius=28 * scale,
                fill=self._container,
                outline=self._border,
                width=max(1, scale),
            )

        self.delete("dock_capsule")
        self._capsule_photo = render_photo(width, height, paint, master=self)
        self.create_image(
            0,
            0,
            image=self._capsule_photo,
            anchor="nw",
            tags=("dock_capsule",),
        )
        self.tag_lower("dock_capsule", self._content_window)


class MaterialFloatingBar(tk.Canvas):
    """Compact floating surface used for canvas-local tools."""

    def __init__(self, master: tk.Misc, *, max_width: int = 220) -> None:
        super().__init__(master, height=52, highlightthickness=0, takefocus=False)
        self.max_width = max_width
        self.content = ttk.Frame(self, style="FloatingBar.TFrame", padding=(7, 5))
        self._content_window = self.create_window(0, 0, window=self.content, anchor="center")
        self._background = "#ded9df"
        self._container = "#fffbfe"
        self._border = "#cac4d0"
        self._shadow = "#c4bec8"
        self.bind("<Configure>", self._layout)

    def apply_palette(
        self,
        *,
        background: str,
        container: str,
        border: str,
        shadow: str | None = None,
    ) -> None:
        self._background = background
        self._container = container
        self._border = border
        self._shadow = shadow or border
        self.configure(background=background)
        self._draw_surface()

    def _layout(self, _event: tk.Event | None = None) -> None:
        self.update_idletasks()
        width = min(self.max_width, max(90, self.content.winfo_reqwidth() + 18))
        self.configure(width=width)
        self.coords(self._content_window, width / 2, 24)
        self.itemconfigure(self._content_window, width=width - 14, height=40)
        self._draw_surface()

    def _draw_surface(self) -> None:
        width = max(90, self.winfo_width())
        height = max(52, self.winfo_height())

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            draw.rounded_rectangle(
                (3 * scale, 6 * scale, (width - 3) * scale, 50 * scale),
                radius=22 * scale,
                fill=self._shadow,
            )
            draw.rounded_rectangle(
                (3 * scale, 3 * scale, (width - 3) * scale, 47 * scale),
                radius=22 * scale,
                fill=self._container,
                outline=self._border,
                width=max(1, scale),
            )

        self.delete("floating_surface")
        self._surface_photo = render_photo(width, height, paint, master=self)
        self.create_image(
            0,
            0,
            image=self._surface_photo,
            anchor="nw",
            tags=("floating_surface",),
        )
        self.tag_lower("floating_surface", self._content_window)


class MaterialSlider(tk.Canvas):
    """Keyboard-accessible Material track and thumb bound to a Tk variable."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        variable: tk.Variable,
        from_: float,
        to: float,
        command: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, height=26, highlightthickness=0, takefocus=True, cursor="hand2")
        self.variable = variable
        self.minimum = float(from_)
        self.maximum = float(to)
        self.command = command
        self._surface = "#fffbfe"
        self._track = "#e6e0e9"
        self._accent = "#6750a4"
        self._outline = "#79747e"
        self._trace = self.variable.trace_add("write", self._variable_changed)
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._pointer_pressed)
        self.bind("<B1-Motion>", self._pointer_changed)
        self.bind("<ButtonRelease-1>", self._pointer_released)
        self.bind("<Left>", lambda _event: self._step(-1))
        self.bind("<Right>", lambda _event: self._step(1))
        self.bind("<Home>", lambda _event: self._set_value(self.minimum))
        self.bind("<End>", lambda _event: self._set_value(self.maximum))

    def apply_palette(
        self,
        *,
        surface: str,
        track: str,
        accent: str,
        outline: str,
    ) -> None:
        self._surface = surface
        self._track = track
        self._accent = accent
        self._outline = outline
        self.configure(background=surface)
        self._draw()

    def _variable_changed(self, *_args: object) -> None:
        self._draw()

    def _fraction(self) -> float:
        span = max(1e-9, self.maximum - self.minimum)
        try:
            value = float(self.variable.get())
        except (tk.TclError, TypeError, ValueError):
            value = self.minimum
        return max(0.0, min(1.0, (value - self.minimum) / span))

    def _draw(self, _event: tk.Event | None = None) -> None:
        width = max(32, self.winfo_width())
        height = max(26, self.winfo_height())
        left, right, center = 10, width - 10, 13
        thumb = left + (right - left) * self._fraction()

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            draw.rounded_rectangle(
                (
                    left * scale,
                    (center - 2) * scale,
                    right * scale,
                    (center + 2) * scale,
                ),
                radius=2 * scale,
                fill=self._track,
            )
            draw.rounded_rectangle(
                (
                    left * scale,
                    (center - 2) * scale,
                    round(thumb * scale),
                    (center + 2) * scale,
                ),
                radius=2 * scale,
                fill=self._accent,
            )
            draw.ellipse(
                (
                    round((thumb - 7) * scale),
                    (center - 7) * scale,
                    round((thumb + 7) * scale),
                    (center + 7) * scale,
                ),
                fill=self._surface,
                outline=self._outline,
                width=max(1, scale),
            )
            draw.ellipse(
                (
                    round((thumb - 4) * scale),
                    (center - 4) * scale,
                    round((thumb + 4) * scale),
                    (center + 4) * scale,
                ),
                fill=self._accent,
            )

        self.delete("all")
        self._track_photo = render_photo(width, height, paint, master=self)
        self.create_image(0, 0, image=self._track_photo, anchor="nw")

    def _pointer_pressed(self, event: tk.Event) -> str:
        self.event_generate("<<MaterialSliderStart>>")
        return self._pointer_changed(event)

    def _pointer_released(self, _event: tk.Event) -> str:
        self.event_generate("<<MaterialSliderEnd>>")
        return "break"

    def _pointer_changed(self, event: tk.Event) -> str:
        self.focus_set()
        width = max(32, self.winfo_width())
        fraction = max(0.0, min(1.0, (event.x - 10) / max(1, width - 20)))
        self._set_value(self.minimum + fraction * (self.maximum - self.minimum))
        return "break"

    def _step(self, direction: int) -> str:
        step = 1.0 if isinstance(self.variable, tk.IntVar) else (self.maximum - self.minimum) / 100
        self._set_value(float(self.variable.get()) + direction * step)
        return "break"

    def _set_value(self, value: float) -> None:
        bounded = max(self.minimum, min(self.maximum, value))
        self.variable.set(round(bounded) if isinstance(self.variable, tk.IntVar) else bounded)
        if self.command is not None:
            self.command()


class MaterialLookStrip(tk.Canvas):
    """Four compact visual filter swatches matching the Material prototype."""

    LOOKS = ("original", "vivid", "warm", "mono")
    LABELS = ("原图", "鲜明", "暖阳", "黑白")

    def __init__(self, master: tk.Misc, command: Callable[[str], None]) -> None:
        super().__init__(master, height=70, highlightthickness=0, takefocus=True, cursor="hand2")
        self.command = command
        self.selected = "original"
        self._surface = "#fffbfe"
        self._accent = "#6750a4"
        self._muted = "#79747e"
        self._border = "#cac4d0"
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._clicked)
        self.bind("<Left>", lambda _event: self._move(-1))
        self.bind("<Right>", lambda _event: self._move(1))

    def apply_palette(self, *, surface: str, accent: str, muted: str, border: str) -> None:
        self._surface = surface
        self._accent = accent
        self._muted = muted
        self._border = border
        self.configure(background=surface)
        self._draw()

    def _draw(self, _event: tk.Event | None = None) -> None:
        width = max(180, self.winfo_width())
        height = max(70, self.winfo_height())
        cell = width / 4
        palettes = (
            ("#8eb7b1", "#3d5f55", "#9b9660"),
            ("#8cc7c0", "#315f55", "#a4a15d"),
            ("#a8c7b8", "#4c6757", "#c2a968"),
            ("#b8b8b8", "#555555", "#888888"),
        )

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            for index, (look, colors) in enumerate(zip(self.LOOKS, palettes)):
                left = index * cell + 4
                right = (index + 1) * cell - 4
                inset_left = left + 1
                inset_right = right - 1
                outline = self._accent if look == self.selected else self._border
                line_width = 2 if look == self.selected else 1
                box = (
                    round(left * scale),
                    4 * scale,
                    round(right * scale),
                    44 * scale,
                )
                draw.rounded_rectangle(
                    box,
                    radius=11 * scale,
                    fill=colors[0],
                )
                draw.polygon(
                    (
                        (round(inset_left * scale), 34 * scale),
                        (round((left + (right - left) * 0.56) * scale), 18 * scale),
                        (round(inset_right * scale), 34 * scale),
                        (round(inset_right * scale), 43 * scale),
                        (round(inset_left * scale), 43 * scale),
                    ),
                    fill=colors[1],
                )
                draw.polygon(
                    (
                        (round(inset_left * scale), 36 * scale),
                        (round(inset_right * scale), 31 * scale),
                        (round(inset_right * scale), 43 * scale),
                        (round(inset_left * scale), 43 * scale),
                    ),
                    fill=colors[2],
                )
                draw.rounded_rectangle(
                    box,
                    radius=11 * scale,
                    outline=outline,
                    width=max(1, line_width * scale),
                )

        self.delete("all")
        self._looks_photo = render_photo(width, height, paint, master=self)
        self.create_image(0, 0, image=self._looks_photo, anchor="nw")
        for index, (look, label, colors) in enumerate(zip(self.LOOKS, self.LABELS, palettes)):
            left = index * cell + 4
            right = (index + 1) * cell - 4
            self.create_text(
                (left + right) / 2,
                59,
                text=label,
                fill=self._accent if look == self.selected else self._muted,
                font=("Microsoft YaHei UI", 9),
            )

    def _index_at(self, x: int) -> int:
        return max(0, min(3, int(x / max(1, self.winfo_width() / 4))))

    def _clicked(self, event: tk.Event) -> str:
        self.focus_set()
        self._select(self._index_at(event.x))
        return "break"

    def _move(self, direction: int) -> str:
        current = self.LOOKS.index(self.selected)
        self._select((current + direction) % len(self.LOOKS))
        return "break"

    def _select(self, index: int) -> None:
        self.selected = self.LOOKS[index]
        self._draw()
        self.command(self.selected)


class MaterialButton(tk.Canvas):
    """Rounded text/icon button with hover, keyboard and filled variants."""

    _VALID_STATES = frozenset(
        {
            "active",
            "alternate",
            "background",
            "disabled",
            "focus",
            "hover",
            "invalid",
            "pressed",
            "readonly",
            "selected",
        }
    )

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str,
        command: Callable[[], object] | None,
        width: int = 52,
        height: int = 38,
        variant: str = "ghost",
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            highlightthickness=0,
            takefocus=True,
            cursor="hand2",
        )
        self.text = text
        self.accessible_name = text
        self.command = command
        self.variant = variant
        self.selected = False
        self._states: set[str] = set()
        self._hovered = False
        self._pressed = False
        self._focused = False
        self._tooltip_job: str | None = None
        self._tooltip_window: tk.Toplevel | None = None
        self._surface = "#fffbfe"
        self._container = "#f3edf7"
        self._hover = "#e6e0e9"
        self._foreground = "#1d1b20"
        self._accent = "#6750a4"
        self._on_accent = "#ffffff"
        self._disabled_container = "#e6e0e9"
        self._disabled_foreground = "#79747e"
        self._focus = "#6750a4"
        self._border = "#cac4d0"
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._pointer_pressed)
        self.bind("<ButtonRelease-1>", self._pointer_released)
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<KeyPress-Return>", self._keyboard_pressed)
        self.bind("<KeyRelease-Return>", self._keyboard_released)
        self.bind("<KeyPress-space>", self._keyboard_pressed)
        self.bind("<KeyRelease-space>", self._keyboard_released)
        self.bind("<Destroy>", self._destroy_tooltip, add="+")

    def apply_palette(
        self,
        *,
        surface: str,
        container: str,
        hover: str,
        foreground: str,
        accent: str,
        on_accent: str,
        disabled_container: str | None = None,
        disabled_foreground: str | None = None,
        focus: str | None = None,
        border: str | None = None,
    ) -> None:
        self._surface = surface
        self._container = container
        self._hover = hover
        self._foreground = foreground
        self._accent = accent
        self._on_accent = on_accent
        self._disabled_container = disabled_container or hover
        self._disabled_foreground = disabled_foreground or foreground
        self._focus = focus or accent
        self._border = border or container
        self.configure(background=surface)
        self._draw()

    def set_selected(self, selected: bool) -> None:
        if not isinstance(selected, bool):
            raise TypeError("selected 必须是布尔值")
        self._set_state("selected", selected)
        self._draw()

    def set_text(self, text: str) -> None:
        if not isinstance(text, str):
            raise TypeError("text 必须是字符串")
        previous = self.text
        self.text = text
        if self.accessible_name == previous:
            self.accessible_name = text
        self._draw()

    def configure(
        self,
        cnf: dict[str, object] | str | None = None,
        **kwargs: object,
    ) -> object:
        """Support the ttk button options used by existing host integrations."""

        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)
        if cnf is None and not kwargs:
            return super().configure()
        options = {} if cnf is None else dict(cnf)
        options.update(kwargs)
        redraw = False
        if "text" in options:
            text = options.pop("text")
            self.set_text(str(text))
            redraw = True
        if "command" in options:
            command = options.pop("command")
            if command is not None and not callable(command):
                raise TypeError("command 必须可调用或为 None")
            self.command = command
        if "state" in options:
            state = str(options.pop("state"))
            if state not in {"normal", "disabled"}:
                raise tk.TclError(f"bad state {state!r}: must be normal or disabled")
            self.state(("disabled",) if state == "disabled" else ("!disabled",))
            redraw = True
        result = super().configure(**options) if options else None
        if redraw:
            self._draw()
        return result

    config = configure

    def cget(self, key: str) -> Any:
        if key == "text":
            return self.text
        if key == "command":
            return self.command
        if key == "state":
            return "disabled" if self.instate(("disabled",)) else "normal"
        return super().cget(key)

    @staticmethod
    def _normalize_state_spec(statespec: str | tuple[str, ...] | list[str]) -> tuple[str, ...]:
        if isinstance(statespec, str):
            return tuple(statespec.split())
        return tuple(statespec)

    def state(
        self,
        statespec: str | tuple[str, ...] | list[str] | None = None,
    ) -> tuple[str, ...]:
        """Query or modify state flags using ttk-compatible state specs."""

        if statespec is None:
            return tuple(sorted(self._states))
        changed: list[str] = []
        for specification in self._normalize_state_spec(statespec):
            if not isinstance(specification, str):
                raise TypeError("状态说明必须是字符串")
            enabled = not specification.startswith("!")
            name = specification[1:] if not enabled else specification
            if name not in self._VALID_STATES:
                raise tk.TclError(f"Invalid state name {name}")
            was_enabled = name in self._states
            if was_enabled == enabled:
                continue
            self._set_state(name, enabled)
            changed.append(name if was_enabled else f"!{name}")
        self._sync_interaction_options()
        self._draw()
        return tuple(changed)

    def instate(
        self,
        statespec: str | tuple[str, ...] | list[str],
        callback: Callable[..., object] | None = None,
        *args: object,
    ) -> object:
        """Return whether every state condition matches, optionally invoking a callback."""

        matches = True
        for specification in self._normalize_state_spec(statespec):
            if not isinstance(specification, str):
                raise TypeError("状态说明必须是字符串")
            expected = not specification.startswith("!")
            name = specification[1:] if not expected else specification
            if name not in self._VALID_STATES:
                raise tk.TclError(f"Invalid state name {name}")
            if (name in self._states) != expected:
                matches = False
                break
        if matches and callback is not None:
            return callback(*args)
        return matches

    def invoke(self) -> object | None:
        """Invoke the command once unless the button is disabled."""

        if self.instate(("disabled",)) or self.command is None:
            return None
        return self.command()

    def _set_state(self, name: str, enabled: bool) -> None:
        if enabled:
            self._states.add(name)
        else:
            self._states.discard(name)
        if name == "selected":
            self.selected = enabled
        elif name in {"active", "hover"}:
            self._hovered = bool({"active", "hover"} & self._states)
        elif name == "pressed":
            self._pressed = enabled
        elif name == "focus":
            self._focused = enabled

    def _sync_interaction_options(self) -> None:
        disabled = "disabled" in self._states
        if disabled:
            self._set_state("pressed", False)
            self._set_state("active", False)
            self._set_state("hover", False)
        super().configure(
            cursor="" if disabled else "hand2",
            takefocus=False if disabled else True,
        )

    def _colors(self) -> tuple[str, str, str | None]:
        if self.instate(("disabled",)):
            fill = (
                self._disabled_container if self.variant in {"filled", "tonal"} else self._surface
            )
            return fill, self._disabled_foreground, None
        if self.variant == "filled":
            return self._accent, self._on_accent, None
        if self.variant == "tonal":
            fill = self._hover if self._pressed or self._hovered else self._container
            return fill, self._foreground, None
        if self.variant == "text":
            fill = self._hover if self._pressed or self._hovered else self._surface
            return fill, self._accent, None
        if self.selected:
            return self._container, self._accent, None
        if self._pressed or self._hovered:
            return self._hover, self._foreground, None
        return self._surface, self._foreground, self._border if self._focused else None

    def _draw(self, _event: tk.Event | None = None) -> None:
        width = max(8, self.winfo_width())
        height = max(8, self.winfo_height())
        fill, foreground, outline = self._colors()
        focused = self._focused and not self.instate(("disabled",))
        font = (
            ("Segoe UI Symbol", 13, "normal")
            if self.variant == "icon"
            else (
                "Microsoft YaHei UI",
                10,
                "bold" if self.variant == "filled" else "normal",
            )
        )

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            if focused and width >= 10 and height >= 10:
                draw.rounded_rectangle(
                    (scale, scale, (width - 1) * scale, (height - 1) * scale),
                    radius=round(height * scale / 2),
                    fill=self._focus,
                )
                draw.rounded_rectangle(
                    (2 * scale, 2 * scale, (width - 2) * scale, (height - 2) * scale),
                    radius=round((height - 2) * scale / 2),
                    fill=self._surface,
                )
                draw.rounded_rectangle(
                    (3 * scale, 3 * scale, (width - 3) * scale, (height - 3) * scale),
                    radius=round((height - 4) * scale / 2),
                    fill=fill,
                )
            else:
                draw.rounded_rectangle(
                    (scale, scale, (width - 1) * scale, (height - 1) * scale),
                    radius=round(height * scale / 2),
                    fill=fill,
                    outline=outline,
                    width=max(1, scale),
                )

        self.delete("all")
        self._button_photo = render_photo(width, height, paint, master=self)
        self.create_image(0, 0, image=self._button_photo, anchor="nw")
        self.create_text(
            width / 2,
            height / 2,
            text=self.text,
            fill=foreground,
            font=font,
        )

    def _enter(self, _event: tk.Event) -> None:
        if self.instate(("disabled",)):
            return
        self._set_state("hover", True)
        self._draw()
        self._schedule_tooltip()

    def _leave(self, _event: tk.Event) -> None:
        self._set_state("hover", False)
        self._draw()
        self._hide_tooltip()

    def _pointer_pressed(self, _event: tk.Event) -> str:
        if self.instate(("disabled",)):
            return "break"
        self._hide_tooltip()
        self.focus_set()
        self._set_state("pressed", True)
        self._draw()
        return "break"

    def _tooltip_text(self) -> str:
        if self.variant != "icon":
            return ""
        label = str(getattr(self, "accessible_name", "")).strip()
        return label if label and label != self.text else ""

    def _schedule_tooltip(self) -> None:
        self._hide_tooltip()
        if not self._tooltip_text() or self.instate(("disabled",)):
            return
        self._tooltip_job = self.after(450, self._show_tooltip)

    def _show_tooltip(self) -> None:
        self._tooltip_job = None
        label = self._tooltip_text()
        if not label or not self._hovered or self.instate(("disabled",)):
            return
        window = tk.Toplevel(self)
        window.withdraw()
        window.overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        text = tk.Label(
            window,
            text=label,
            background="#313033",
            foreground="#ffffff",
            borderwidth=0,
            padx=10,
            pady=6,
            font=("Microsoft YaHei UI", 9),
        )
        text.pack()
        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        x = self.winfo_rootx() + (self.winfo_width() - width) // 2
        y = self.winfo_rooty() + self.winfo_height() + 8
        screen_width = max(1, self.winfo_screenwidth())
        screen_height = max(1, self.winfo_screenheight())
        x = max(4, min(x, screen_width - width - 4))
        if y + height + 4 > screen_height:
            y = max(4, self.winfo_rooty() - height - 8)
        window.geometry(f"+{x}+{y}")
        window.deiconify()
        self._tooltip_window = window

    def _hide_tooltip(self) -> None:
        if self._tooltip_job is not None:
            try:
                self.after_cancel(self._tooltip_job)
            except tk.TclError:
                pass
            self._tooltip_job = None
        window = self._tooltip_window
        self._tooltip_window = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _destroy_tooltip(self, _event: tk.Event | None = None) -> None:
        self._hide_tooltip()

    def _pointer_released(self, event: tk.Event) -> str:
        was_pressed = self._pressed
        self._set_state("pressed", False)
        self._draw()
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if was_pressed and inside:
            self.invoke()
        return "break"

    def _clicked(self, _event: tk.Event) -> str:
        """Compatibility helper retained for callers of the original private handler."""

        if not self.instate(("disabled",)):
            self.focus_set()
            self.invoke()
        return "break"

    def _keyboard_pressed(self, _event: tk.Event) -> str:
        if not self.instate(("disabled",)):
            self._set_state("pressed", True)
            self._draw()
        return "break"

    def _keyboard_released(self, _event: tk.Event) -> str:
        was_pressed = self._pressed
        self._set_state("pressed", False)
        self._draw()
        if was_pressed:
            self.invoke()
        return "break"

    def _invoke(self, _event: tk.Event) -> str:
        """Compatibility event handler retained for older bindings/tests."""

        self.invoke()
        return "break"

    def _focus_in(self, _event: tk.Event) -> None:
        self._set_state("focus", True)
        self._draw()

    def _focus_out(self, _event: tk.Event) -> None:
        self._set_state("focus", False)
        self._set_state("pressed", False)
        self._draw()

        return "break"


class MaterialTabStrip(tk.Canvas):
    """Underline tab strip used by the inspector."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        tabs: tuple[tuple[str, str], ...],
        command: Callable[[str], None],
        selected: str,
    ) -> None:
        super().__init__(master, height=43, highlightthickness=0, takefocus=True, cursor="hand2")
        self.tabs = tabs
        self.command = command
        self.selected = selected
        self._surface = "#fffbfe"
        self._foreground = "#1d1b20"
        self._muted = "#79747e"
        self._accent = "#6750a4"
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._clicked)
        self.bind("<Left>", lambda _event: self._move(-1))
        self.bind("<Right>", lambda _event: self._move(1))

    def apply_palette(
        self,
        *,
        surface: str,
        foreground: str,
        muted: str,
        accent: str,
    ) -> None:
        self._surface = surface
        self._foreground = foreground
        self._muted = muted
        self._accent = accent
        self.configure(background=surface)
        self._draw()

    def select(self, name: str, *, invoke: bool = False) -> None:
        if name not in {key for key, _label in self.tabs}:
            return
        self.selected = name
        self._draw()
        if invoke:
            self.command(name)

    def _draw(self, _event: tk.Event | None = None) -> None:
        width = max(120, self.winfo_width())
        height = max(43, self.winfo_height())
        cell = width / len(self.tabs)

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            for index, (name, _label) in enumerate(self.tabs):
                if name != self.selected:
                    continue
                center = cell * (index + 0.5)
                half_width = min(25, cell * 0.25)
                draw.rounded_rectangle(
                    (
                        round((center - half_width) * scale),
                        37 * scale,
                        round((center + half_width) * scale),
                        41 * scale,
                    ),
                    radius=2 * scale,
                    fill=self._accent,
                )

        self.delete("all")
        self._indicator_photo = render_photo(width, height, paint, master=self)
        self.create_image(0, 0, image=self._indicator_photo, anchor="nw")
        for index, (name, label) in enumerate(self.tabs):
            center = cell * (index + 0.5)
            active = name == self.selected
            self.create_text(
                center,
                18,
                text=label,
                fill=self._foreground if active else self._muted,
                font=("Microsoft YaHei UI", 10, "bold" if active else "normal"),
            )

    def _clicked(self, event: tk.Event) -> str:
        self.focus_set()
        cell_width = self.winfo_width() / max(1, len(self.tabs))
        index = max(0, min(len(self.tabs) - 1, int(event.x / max(1, cell_width))))
        self.select(self.tabs[index][0], invoke=True)
        return "break"

    def _move(self, direction: int) -> str:
        names = [name for name, _label in self.tabs]
        index = (names.index(self.selected) + direction) % len(names)
        self.select(names[index], invoke=True)
        return "break"


class MaterialSearchField(tk.Canvas):
    """Rounded search container exposing a standard Entry for text input."""

    def __init__(self, master: tk.Misc, *, textvariable: tk.StringVar) -> None:
        super().__init__(master, height=32, highlightthickness=0)
        self.textvariable = textvariable
        self._background = "#fffbfe"
        self._surface = "#f3edf7"
        self._border = "#cac4d0"
        self._foreground = "#1d1b20"
        self._muted = "#79747e"
        self.entry = tk.Entry(
            self,
            textvariable=textvariable,
            borderwidth=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 10),
        )
        self._entry_window = self.create_window(31, 16, window=self.entry, anchor="w")
        self.placeholder = tk.Label(
            self,
            text="搜索当前文件夹",
            anchor="w",
            borderwidth=0,
            cursor="xterm",
            font=("Microsoft YaHei UI", 9),
        )
        self.placeholder.bind("<Button-1>", self._focus_entry)
        self.entry.bind("<FocusIn>", self._sync_placeholder, add="+")
        self.entry.bind("<FocusOut>", self._sync_placeholder, add="+")
        self._text_trace = textvariable.trace_add("write", self._sync_placeholder)
        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", lambda _event: self.entry.focus_set())
        self.after_idle(self._sync_placeholder)

    def apply_palette(
        self,
        *,
        background: str,
        surface: str,
        border: str,
        foreground: str,
        muted: str,
    ) -> None:
        self._background = background
        self._surface = surface
        self._border = border
        self._foreground = foreground
        self._muted = muted
        self.configure(background=background)
        self.entry.configure(
            background=surface,
            foreground=foreground,
            insertbackground=foreground,
        )
        self.placeholder.configure(background=surface, foreground=muted)
        self._draw()

    def _draw(self, _event: tk.Event | None = None) -> None:
        width = max(80, self.winfo_width())
        height = max(32, self.winfo_height())

        def paint(draw: ImageDraw.ImageDraw, scale: int) -> None:
            draw.rounded_rectangle(
                (scale, scale, (width - 1) * scale, 31 * scale),
                radius=15 * scale,
                fill=self._surface,
                outline=self._border,
                width=max(1, scale),
            )
            draw.ellipse(
                (12 * scale, 10 * scale, 19 * scale, 17 * scale),
                outline=self._muted,
                width=max(1, scale),
            )
            draw.line(
                (18 * scale, 16 * scale, 21 * scale, 19 * scale),
                fill=self._muted,
                width=max(1, scale),
            )

        self.delete("search_surface")
        self._search_photo = render_photo(width, height, paint, master=self)
        self.create_image(
            0,
            0,
            image=self._search_photo,
            anchor="nw",
            tags=("search_surface",),
        )
        self.coords(self._entry_window, 31, 16)
        self.itemconfigure(self._entry_window, width=max(30, width - 44), height=22)
        self.tag_lower("search_surface", self._entry_window)
        self._sync_placeholder()

    def _focus_entry(self, _event: tk.Event) -> str:
        self.entry.focus_set()
        self._sync_placeholder()
        return "break"

    def _sync_placeholder(self, *_args: object) -> None:
        if self.textvariable.get() or self.focus_get() == self.entry:
            self.placeholder.place_forget()
            return
        width = max(30, self.winfo_width() - 48)
        self.placeholder.place(x=31, y=5, width=width, height=22)
