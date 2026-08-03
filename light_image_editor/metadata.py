"""Read human-friendly image and file properties."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

EXIF_FIELDS = {
    "Make": "相机厂商",
    "Model": "相机型号",
    "DateTimeOriginal": "拍摄时间",
    "DateTime": "修改时间（EXIF）",
    "Orientation": "方向",
    "Software": "软件",
    "LensModel": "镜头",
    "ExposureTime": "曝光时间",
    "FNumber": "光圈",
    "ISOSpeedRatings": "ISO",
    "FocalLength": "焦距",
}


def _display_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00")
    if isinstance(value, tuple):
        return ", ".join(map(str, value))
    return str(value)


def image_properties(path: str | Path, image: Image.Image) -> list[tuple[str, str]]:
    image_path = Path(path)
    stat = image_path.stat()
    result = [
        ("文件名", image_path.name),
        ("位置", str(image_path.parent)),
        ("格式", image.format or image_path.suffix.lstrip(".").upper() or "未知"),
        ("尺寸", f"{image.width} × {image.height} 像素"),
        ("颜色模式", image.mode),
        ("文件大小", _format_bytes(stat.st_size)),
        ("修改日期", datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")),
        ("创建日期", datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")),
    ]
    try:
        exif = image.getexif()
    except (AttributeError, OSError):
        exif = None
    if exif:
        for tag_id, value in exif.items():
            name = ExifTags.TAGS.get(tag_id, str(tag_id))
            label = EXIF_FIELDS.get(name)
            if label and value not in (None, ""):
                result.append((label, _display_value(value)))
    return result


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.2f} MB"
