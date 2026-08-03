"""Generate the application icon from deterministic vector-like primitives."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "resources" / "icons"
MASTER_SIZE = 1024
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def _gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    pixels = image.load()
    assert pixels is not None
    for y in range(size):
        ratio = y / max(1, size - 1)
        color = tuple(
            round(start + (end - start) * ratio) for start, end in zip(top, bottom, strict=True)
        )
        for x in range(size):
            pixels[x, y] = (*color, 255)
    return image


def create_master_icon() -> Image.Image:
    canvas = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE), (0, 0, 0, 0))
    rounded_mask = Image.new("L", canvas.size, 0)
    mask_draw = ImageDraw.Draw(rounded_mask)
    mask_draw.rounded_rectangle((36, 36, 988, 988), radius=228, fill=255)

    background = _gradient(MASTER_SIZE, (37, 99, 235), (67, 56, 202))
    canvas.alpha_composite(
        Image.composite(background, Image.new("RGBA", canvas.size), rounded_mask)
    )

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((174, 176, 800, 758), radius=92, fill=(255, 255, 255, 244))
    draw.ellipse((248, 252, 372, 376), fill=(251, 146, 60, 255))
    draw.polygon(
        ((228, 664), (416, 438), (552, 588), (646, 486), (764, 664)), fill=(96, 165, 250, 255)
    )
    draw.polygon(
        ((228, 664), (416, 438), (552, 588), (646, 486), (764, 664), (764, 704), (228, 704)),
        fill=(59, 130, 246, 255),
    )
    draw.polygon(
        ((416, 438), (480, 508), (442, 492), (406, 526), (372, 490)), fill=(239, 246, 255, 255)
    )

    # A compact pencil mark communicates editing while remaining legible at 16 px.
    pencil = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    pencil_draw = ImageDraw.Draw(pencil)
    pencil_draw.rounded_rectangle((628, 442, 760, 884), radius=54, fill=(249, 115, 22, 255))
    pencil_draw.rectangle((628, 500, 694, 826), fill=(251, 146, 60, 255))
    pencil_draw.polygon(((628, 884), (760, 884), (694, 984)), fill=(254, 215, 170, 255))
    pencil_draw.polygon(((674, 954), (714, 954), (694, 984)), fill=(30, 41, 59, 255))
    pencil_draw.rounded_rectangle((628, 420, 760, 522), radius=42, fill=(255, 237, 213, 255))
    pencil = pencil.rotate(-42, resample=Image.Resampling.BICUBIC, center=(694, 704))
    canvas.alpha_composite(pencil)

    return canvas


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    master = create_master_icon()
    for size in ICON_SIZES:
        resized = master.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(ICON_DIR / f"app-{size}.png", optimize=True)
    master.save(ICON_DIR / "app.ico", sizes=[(size, size) for size in ICON_SIZES])


if __name__ == "__main__":
    main()
