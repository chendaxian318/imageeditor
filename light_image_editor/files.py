"""File-system and Windows shell services used by the editor UI."""

from __future__ import annotations

import ctypes
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Final

from PIL import Image

from image_ops import SUPPORTED_INPUTS, natural_sort_key

CF_DIB: Final = 8
GMEM_MOVEABLE: Final = 0x0002
INVALID_FILENAME_CHARS: Final = '<>:"/\\|?*'


def validate_filename_affix(label: str, value: str) -> str:
    """Trim and validate a prefix or suffix used in an output filename."""

    cleaned = value.strip()
    invalid = sorted({char for char in cleaned if char in INVALID_FILENAME_CHARS or ord(char) < 32})
    if invalid:
        raise ValueError(f"{label}不能包含这些字符：{' '.join(invalid)}")
    return cleaned


def list_folder_images(image_path: str | Path | None) -> list[Path]:
    """Return naturally sorted supported images beside *image_path*."""

    if image_path is None:
        return []
    folder = Path(image_path).parent
    if not folder.exists():
        return []
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_INPUTS
        ),
        key=natural_sort_key,
    )


def folder_position(
    image_path: str | Path | None, files: list[Path] | None = None
) -> tuple[int, int] | None:
    """Return the one-based location of *image_path* in its image folder."""

    if image_path is None:
        return None
    try:
        current = Path(image_path).resolve()
        candidates = files if files is not None else list_folder_images(image_path)
        resolved = [path.resolve() for path in candidates]
    except OSError:
        return None
    try:
        return resolved.index(current) + 1, len(resolved)
    except ValueError:
        return None


def unique_export_path(folder: str | Path, stem: str, extension: str) -> Path:
    """Create *folder* and choose the first non-conflicting output path."""

    output_folder = Path(folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    target = output_folder / f"{stem}{extension}"
    if not target.exists():
        return target
    for index in range(2, 1000):
        candidate = output_folder / f"{stem}_{index}{extension}"
        if not candidate.exists():
            return candidate
    raise FileExistsError("无法生成不重名的导出文件")


def reveal_file(path: str | Path) -> None:
    """Select a file in Windows Explorer."""

    subprocess.Popen(["explorer", "/select,", str(path)])


def open_folder(path: str | Path) -> None:
    """Open a folder in Windows Explorer."""

    subprocess.Popen(["explorer", str(path)])


def copy_image_to_clipboard(image: Image.Image) -> None:
    """Copy a Pillow image to the Windows clipboard as DIB data."""

    output = BytesIO()
    image.convert("RGB").save(output, "BMP")
    data = output.getvalue()[14:]
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.EmptyClipboard.restype = ctypes.c_bool
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.restype = ctypes.c_bool
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("无法分配剪贴板内存")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError("无法锁定剪贴板内存")
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        raise OSError("无法打开剪贴板")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(handle)
            raise OSError("无法写入剪贴板")
        handle = 0
    finally:
        user32.CloseClipboard()
