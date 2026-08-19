"""Bounded raster previews for vector and page-description image formats."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .document import SourceMetadata, attach_source_metadata
from .pdf_support import render_pdf_page

VECTOR_EXTENSIONS = frozenset({".svg", ".ai", ".eps", ".emf", ".wmf"})
WINDOWS_METAFILE_EXTENSIONS = frozenset({".emf", ".wmf"})
POSTSCRIPT_EXTENSIONS = frozenset({".ai", ".eps"})

DEFAULT_VECTOR_DPI = 144
MAX_VECTOR_FILE_BYTES = 256 * 1024 * 1024
MAX_VECTOR_DIMENSION = 100_000
MAX_VECTOR_PIXELS = 120_000_000


class VectorSupportError(RuntimeError):
    """Raised when a vector file cannot be rendered safely."""


def _validate_source(path: Path) -> None:
    size = path.stat().st_size
    if size <= 0:
        raise VectorSupportError("矢量图文件为空，无法打开。")
    if size > MAX_VECTOR_FILE_BYTES:
        raise VectorSupportError("矢量图文件超过 256 MB 安全上限，已停止解析。")


def _bounded_size(width: float, height: float) -> tuple[int, int]:
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise VectorSupportError("矢量图画布尺寸无效，已停止渲染。")
    scale = min(
        1.0,
        MAX_VECTOR_DIMENSION / width,
        MAX_VECTOR_DIMENSION / height,
        math.sqrt(MAX_VECTOR_PIXELS / (width * height)),
    )
    if not math.isfinite(scale) or scale <= 0:
        raise VectorSupportError("矢量图尺寸超过安全上限，已停止渲染。")
    return max(1, round(width * scale)), max(1, round(height * scale))


def _attach_vector_metadata(
    image: Image.Image,
    format_name: str,
    dpi: int,
) -> Image.Image:
    image.format = format_name
    image.info["vector_source"] = True
    image.info["vector_format"] = format_name
    return attach_source_metadata(
        image,
        SourceMetadata(
            original_format=format_name,
            original_mode="Vector preview",
            dpi=(float(dpi), float(dpi)),
        ),
    )


def _render_svg(path: Path, dpi: int) -> Image.Image:
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    # Load from bytes rather than a filename so relative external resources in
    # an untrusted SVG cannot resolve against the source directory.
    renderer = QSvgRenderer(QByteArray(path.read_bytes()))
    if not renderer.isValid():
        raise VectorSupportError("无法解析 SVG，文件可能损坏或包含不受支持的内容。")
    renderer.setAnimationEnabled(False)
    size = renderer.defaultSize()
    width, height = float(size.width()), float(size.height())
    if width <= 0 or height <= 0:
        view_box = renderer.viewBoxF()
        width, height = view_box.width(), view_box.height()
    if width <= 0 or height <= 0:
        width, height = 1024.0, 1024.0
    width, height = _bounded_size(width, height)

    surface = QImage(width, height, QImage.Format.Format_RGBA8888)
    surface.fill(0)
    painter = QPainter(surface)
    try:
        renderer.render(painter, QRectF(0.0, 0.0, float(width), float(height)))
    finally:
        painter.end()
    if surface.isNull():
        raise VectorSupportError("SVG 渲染失败。")
    pixels = bytes(surface.bits())
    rendered = Image.frombuffer(
        "RGBA",
        (width, height),
        pixels,
        "raw",
        "RGBA",
        surface.bytesPerLine(),
        1,
    ).copy()
    return _attach_vector_metadata(rendered, "SVG", dpi)


def _render_windows_metafile(path: Path, dpi: int) -> Image.Image:
    try:
        with Image.open(path) as source:
            # Pillow's Windows metafile stub replaces its own state after GDI
            # rendering, so retain and close the original stream explicitly.
            source_stream = source.fp
            try:
                bbox = source.info.get("wmf_bbox")
                inch = getattr(source, "_inch", None)
                if not (
                    isinstance(bbox, tuple)
                    and len(bbox) == 4
                    and isinstance(inch, tuple)
                    and len(inch) == 2
                ):
                    raise VectorSupportError("EMF/WMF 缺少有效的画布边界。")
                extent_width = float(bbox[2] - bbox[0])
                extent_height = float(bbox[3] - bbox[1])
                inch_x, inch_y = float(inch[0]), float(inch[1])
                requested_dpi = max(72, min(600, int(dpi)))
                width, height = _bounded_size(
                    extent_width * requested_dpi / inch_x,
                    extent_height * requested_dpi / inch_y,
                )
                bounded_dpi = (
                    width * inch_x / extent_width,
                    height * inch_y / extent_height,
                )
                source.load(dpi=bounded_dpi)
                if (width, height) != source.size:
                    rendered = source.resize((width, height), Image.Resampling.LANCZOS)
                else:
                    rendered = source.copy()
            finally:
                if source_stream is not None:
                    source_stream.close()
    except VectorSupportError:
        raise
    except (OSError, TypeError, ValueError, ZeroDivisionError) as error:
        raise VectorSupportError(
            "无法渲染 EMF/WMF，文件可能损坏或包含 Windows 不支持的图元。"
        ) from error
    rgba = rendered.convert("RGBA")
    rendered.close()
    return _attach_vector_metadata(rgba, path.suffix[1:].upper(), dpi)


def _is_pdf_compatible(path: Path) -> bool:
    with path.open("rb") as stream:
        header = stream.read(4096)
    return b"%PDF-" in header[:1024]


def _find_ghostscript() -> Path | None:
    configured = os.environ.get("CSIMAGE_GHOSTSCRIPT") or os.environ.get("GHOSTSCRIPT_EXECUTABLE")
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return candidate
    for executable in ("gswin64c.exe", "gswin32c.exe", "gs.exe"):
        located = shutil.which(executable)
        if located:
            return Path(located)
    program_files = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    candidates: list[Path] = []
    for root_text in program_files:
        if root_text:
            candidates.extend(Path(root_text).glob("gs/gs*/bin/gswin*c.exe"))
    return max(candidates, key=lambda item: item.parent.parent.name, default=None)


def ghostscript_available() -> bool:
    return _find_ghostscript() is not None


def _render_postscript(path: Path, dpi: int) -> Image.Image:
    if _is_pdf_compatible(path):
        try:
            rendered = render_pdf_page(path, 0, dpi=dpi)
        except Exception as error:
            raise VectorSupportError("AI/PDF 兼容数据无法解析，文件可能已损坏。") from error
        rendered.info.pop("pdf_page_sizes", None)
        return _attach_vector_metadata(rendered, path.suffix[1:].upper(), dpi)

    ghostscript = _find_ghostscript()
    if ghostscript is None:
        raise VectorSupportError(
            "该 AI/EPS 使用 PostScript 数据，需要单独安装 Ghostscript，或在 Adobe 软件中保存为"
            "“PDF 兼容 AI”后再打开。CSImage 不会因许可限制擅自捆绑 Ghostscript。"
        )

    with tempfile.TemporaryDirectory(prefix="csimage-vector-") as directory:
        output = Path(directory) / "page.png"
        command = [
            str(ghostscript),
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-dNOPROMPT",
            "-dQUIET",
            "-dFirstPage=1",
            "-dLastPage=1",
            "-dEPSCrop",
            "-sDEVICE=pngalpha",
            f"-r{max(72, min(600, int(dpi)))}",
            f"-sOutputFile={output}",
            "-f",
            str(path),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=60,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise VectorSupportError("AI/EPS 渲染器启动失败或处理超时。") from error
        if completed.returncode != 0 or not output.is_file():
            raise VectorSupportError("Ghostscript 无法渲染该 AI/EPS 文件。")
        try:
            with Image.open(output) as source:
                source.load()
                width, height = _bounded_size(float(source.width), float(source.height))
                rendered = source.convert("RGBA")
                if rendered.size != (width, height):
                    resized = rendered.resize((width, height), Image.Resampling.LANCZOS)
                    rendered.close()
                    rendered = resized
        except (OSError, ValueError) as error:
            raise VectorSupportError("AI/EPS 渲染结果无效。") from error
    return _attach_vector_metadata(rendered, path.suffix[1:].upper(), dpi)


def render_vector_image(
    path: str | Path,
    *,
    dpi: int = DEFAULT_VECTOR_DPI,
) -> Image.Image:
    """Rasterize one supported vector file into an owned Pillow image."""

    image_path = Path(path)
    _validate_source(image_path)
    suffix = image_path.suffix.casefold()
    if suffix == ".svg":
        return _render_svg(image_path, dpi)
    if suffix in WINDOWS_METAFILE_EXTENSIONS:
        return _render_windows_metafile(image_path, dpi)
    if suffix in POSTSCRIPT_EXTENSIONS:
        return _render_postscript(image_path, dpi)
    raise VectorSupportError(f"不支持的矢量图格式：{suffix or '未知'}")
