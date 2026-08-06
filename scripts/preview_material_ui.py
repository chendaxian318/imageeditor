"""Launch an isolated Material You UI preview without touching user settings."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _build_sample_images(directory: Path) -> Path:
    """Create a small photo-like library so the UI preview matches the design context."""

    directory.mkdir(parents=True, exist_ok=True)
    palettes = (
        ("山谷清晨.png", "#b8d8e8", "#f5dfbf", "#344f50", "#75917e", "#a8a56c"),
        ("海岸午后.png", "#8fc7d8", "#f0ceb4", "#315d66", "#6e8b88", "#c2a56f"),
        ("城市暮色.png", "#4d5c87", "#d47f83", "#303449", "#56566d", "#45475a"),
        ("林间小路.png", "#92b7a0", "#d8c08c", "#2e4a38", "#66806b", "#66734d"),
        ("白色建筑.png", "#82aec5", "#e2c99d", "#516b70", "#83938a", "#b3a273"),
    )
    for index, (name, sky_top, sky_bottom, ridge, mountain, field) in enumerate(palettes):
        image = Image.new("RGB", (1200, 800), sky_top)
        draw = ImageDraw.Draw(image)
        top = ImageColor.getrgb(sky_top)
        bottom = ImageColor.getrgb(sky_bottom)
        for y in range(800):
            fraction = y / 799
            color = tuple(round(a + (b - a) * fraction) for a, b in zip(top, bottom))
            draw.line((0, y, 1200, y), fill=color)
        sun_x = 820 - index * 54
        draw.ellipse((sun_x - 52, 174, sun_x + 52, 278), fill="#fff3cd")
        draw.polygon(
            (
                (0, 505),
                (210, 298),
                (390, 478),
                (624, 242),
                (870, 478),
                (1030, 344),
                (1200, 480),
                (1200, 800),
                (0, 800),
            ),
            fill=ridge,
        )
        draw.polygon(
            (
                (0, 552),
                (228, 352),
                (418, 516),
                (650, 294),
                (874, 510),
                (1040, 390),
                (1200, 512),
                (1200, 800),
                (0, 800),
            ),
            fill=mountain,
        )
        draw.polygon(
            (
                (0, 548),
                (205, 518),
                (394, 560),
                (612, 532),
                (820, 558),
                (1030, 522),
                (1200, 538),
                (1200, 800),
                (0, 800),
            ),
            fill=field,
        )
        draw.polygon(
            ((522, 800), (565, 690), (690, 568), (803, 486), (714, 612), (666, 800)), fill="#d4b987"
        )
        for x, base, height in (
            (112, 660, 112),
            (160, 640, 142),
            (982, 618, 172),
            (1062, 668, 118),
        ):
            draw.polygon(((x, base - height), (x - 31, base), (x + 31, base)), fill="#263d35")
            draw.rectangle((x - 4, base - 8, x + 4, base + 24), fill="#263d35")
        image.save(directory / name, optimize=True)
        image.close()
    return directory / palettes[0][0]


def main() -> None:
    from light_image_editor.session import SessionStore
    from light_image_editor.settings import SettingsStore
    from light_image_editor.ui.main_window import ImageEditorApp

    with tempfile.TemporaryDirectory(prefix="light-image-editor-material-") as directory:
        preview_root = Path(directory)
        sample_image = _build_sample_images(preview_root / "示例图片")
        settings = SettingsStore(preview_root / "settings.json")
        settings.save(
            {
                "schema_version": 1,
                "onboarding_completed": True,
                "theme_mode": "light",
                "edit_panel_visible": True,
                "thumbnail_sidebar_visible": True,
            }
        )
        app = ImageEditorApp(
            settings_store=settings,
            session_store=SessionStore(preview_root / "session.json"),
        )

        def apply_demo_state() -> None:
            if app.document is None:
                app.after(100, apply_demo_state)
                return
            for variable, value in (
                (app.brightness_var, 12),
                (app.contrast_var, 8),
                (app.highlights_var, -18),
                (app.shadows_var, 26),
                (app.temperature_var, 6),
                (app.saturation_var, 14),
                (app.tint_var, 0),
            ):
                variable.set(value)
            app.look_strip.selected = "vivid"
            app.look_strip._draw()
            app._live_adjust()
            app.status_var.set("● 已自动保存")

        def open_demo() -> None:
            app.open_path(sample_image, confirm=False)
            app.after(200, apply_demo_state)

        app.after(120, open_demo)
        app.mainloop()


if __name__ == "__main__":
    main()
