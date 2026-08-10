"""Bounded, read-only PSD/PSDT composite rendering."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from psd_tools import PSDImage

from .document import SourceMetadata, attach_source_metadata

PSD_EXTENSIONS = frozenset({".psd", ".psdt"})
MAX_PSD_PIXELS = 120_000_000
MAX_PSD_DIMENSION = 30_000
MAX_PSD_ALLOCATION_BYTES = 512 * 1024 * 1024


class PsdSupportError(Exception):
    """Raised when a Photoshop document cannot be rendered safely."""


def _validate_canvas(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise PsdSupportError("Photoshop 文档画布尺寸无效。")
    if width > MAX_PSD_DIMENSION or height > MAX_PSD_DIMENSION or width * height > MAX_PSD_PIXELS:
        raise PsdSupportError(f"Photoshop 文档尺寸 {width} × {height} 超过安全上限，已停止解码。")


def render_psd_composite(path: str | Path) -> Image.Image:
    """Render a PSD or PSDT as a flattened, owned Pillow image.

    The cached Photoshop composite is preferred because it most closely matches
    Photoshop's own rendering. If that preview is malformed, psd-tools rebuilds
    a best-effort composite from the layer tree instead.
    """

    image_path = Path(path)
    try:
        document = PSDImage.open(
            image_path,
            max_alloc_bytes=MAX_PSD_ALLOCATION_BYTES,
        )
        width, height = document.size
        _validate_canvas(int(width), int(height))
        try:
            composite = document.composite(apply_icc=True)
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as preview_error:
            if "configured budget" in str(preview_error).casefold():
                raise
            composite = document.composite(ignore_preview=True, apply_icc=True)
        try:
            composite.load()
            source_mode = composite.mode
            has_alpha = "A" in composite.getbands() or "transparency" in composite.info
            target_mode = "RGBA" if has_alpha else "RGB"
            rendered = composite.convert(target_mode)
            rendered.load()
        finally:
            composite.close()
    except PsdSupportError:
        raise
    except FileNotFoundError:
        raise
    except PermissionError:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except MemoryError as error:
        raise PsdSupportError("Photoshop 文档需要的内存过大，已停止解码。") from error
    except Exception as error:
        raise PsdSupportError(
            "无法读取 Photoshop 文档。文件可能已损坏、未保存合成预览，"
            "或使用了暂不支持的 Photoshop 功能。"
        ) from error

    format_name = "PSDT" if image_path.suffix.casefold() == ".psdt" else "PSD"
    return attach_source_metadata(
        rendered,
        SourceMetadata(
            original_format=format_name,
            original_mode=source_mode,
            has_alpha=has_alpha,
        ),
    )
