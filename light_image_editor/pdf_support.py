"""Safe, bounded PDF page rasterization for the image viewer."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image

from .document import SourceMetadata, attach_source_metadata

PDF_EXTENSION = ".pdf"
DEFAULT_RENDER_DPI = 144
MAX_PDF_PAGES = 10_000
MAX_RENDER_DIMENSION = 100_000
MAX_RENDER_PIXELS = 120_000_000


class PdfSupportError(RuntimeError):
    """Raised when a PDF cannot be opened or rendered safely."""


def _pdfium_module():
    try:
        import pypdfium2 as pdfium
    except ImportError as error:  # pragma: no cover - guarded by the locked build
        raise PdfSupportError("PDF 渲染组件未安装，请重新安装 CSImage。") from error
    return pdfium


def _validated_page_count(document: object) -> int:
    page_count = len(document)  # type: ignore[arg-type]
    if page_count <= 0:
        raise PdfSupportError("PDF 不包含可显示的页面。")
    if page_count > MAX_PDF_PAGES:
        raise PdfSupportError(f"PDF 包含 {page_count} 页，超过 {MAX_PDF_PAGES} 页安全上限。")
    return page_count


def pdf_page_count(path: str | Path) -> int:
    """Return the bounded number of pages in *path*."""

    pdfium = _pdfium_module()
    try:
        with pdfium.PdfDocument(Path(path)) as document:
            return _validated_page_count(document)
    except PdfSupportError:
        raise
    except Exception as error:
        raise PdfSupportError("无法打开 PDF，文件可能已损坏、受密码保护或格式不受支持。") from error


def _bounded_scale(width: float, height: float, dpi: int) -> float:
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise PdfSupportError("PDF 页面尺寸无效，已停止渲染。")
    requested = max(1, dpi) / 72.0
    dimension_scale = min(MAX_RENDER_DIMENSION / width, MAX_RENDER_DIMENSION / height)
    pixel_scale = math.sqrt(MAX_RENDER_PIXELS / (width * height))
    scale = min(requested, dimension_scale, pixel_scale)
    if not math.isfinite(scale) or scale <= 0:
        raise PdfSupportError("PDF 页面尺寸超过安全上限，已停止渲染。")
    return scale


def render_pdf_page(
    path: str | Path,
    page_index: int = 0,
    *,
    dpi: int = DEFAULT_RENDER_DPI,
) -> Image.Image:
    """Rasterize one zero-based PDF page to an owned RGB Pillow image."""

    image_path = Path(path)
    pdfium = _pdfium_module()
    try:
        with pdfium.PdfDocument(image_path) as document:
            page_count = _validated_page_count(document)
            if not 0 <= page_index < page_count:
                raise PdfSupportError(f"PDF 页码 {page_index + 1} 无效，文档共 {page_count} 页。")
            page = document[page_index]
            try:
                width, height = page.get_size()
                scale = _bounded_scale(float(width), float(height), dpi)
                bitmap = page.render(
                    scale=scale,
                    draw_annots=True,
                    fill_color=(255, 255, 255, 255),
                    prefer_bgrx=True,
                    rev_byteorder=True,
                )
                try:
                    rendered = bitmap.to_pil().convert("RGB")
                    rendered.load()
                    result = rendered.copy()
                    rendered.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
    except PdfSupportError:
        raise
    except FileNotFoundError:
        raise
    except PermissionError:
        raise
    except Exception as error:
        raise PdfSupportError(
            f"无法渲染 PDF 第 {page_index + 1} 页，文件可能已损坏或受密码保护。"
        ) from error

    result.info["pdf_page_index"] = page_index
    result.info["pdf_page_count"] = page_count
    result.info["pdf_render_dpi"] = dpi
    return attach_source_metadata(
        result,
        SourceMetadata(
            original_format="PDF",
            original_mode="PDF page",
            dpi=(float(dpi), float(dpi)),
        ),
    )
