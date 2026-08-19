"""Encoders for professional, game-texture and raster-container outputs."""

from __future__ import annotations

import base64
import ctypes
import io
import os
import struct
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

import numpy as np
from PIL import Image

ADVANCED_OUTPUT_FORMATS = frozenset(
    {"TGA", "DDS", "EXR", "HDR", "ICO", "KTX2", "SVG", "AI", "EPS", "EMF", "WMF"}
)


def _mip_chain(image: Image.Image, enabled: bool) -> list[Image.Image]:
    current = image.convert("RGBA")
    levels = [current]
    if not enabled:
        return levels
    while current.size != (1, 1):
        current = current.resize(
            (max(1, current.width // 2), max(1, current.height // 2)),
            Image.Resampling.LANCZOS,
        )
        levels.append(current)
    return levels


def _flatten_alpha(image: Image.Image, color: str = "#ffffff") -> Image.Image:
    if image.mode not in {"RGBA", "LA", "P"}:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, color)
    try:
        background.alpha_composite(rgba)
        return background.convert("RGB")
    finally:
        rgba.close()
        background.close()


def encode_tga(image: Image.Image, *, rle: bool) -> bytes:
    buffer = io.BytesIO()
    source = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    try:
        kwargs: dict[str, object] = {"orientation": 1}
        if rle:
            kwargs["compression"] = "tga_rle"
        source.save(buffer, "TGA", **kwargs)
        return buffer.getvalue()
    finally:
        source.close()


def _dds_level(image: Image.Image, pixel_format: str) -> tuple[bytes, int]:
    source = image.convert("RGB" if pixel_format == "BC5" else "RGBA")
    buffer = io.BytesIO()
    try:
        kwargs: dict[str, object] = {}
        if pixel_format != "RGBA8":
            kwargs["pixel_format"] = pixel_format
        source.save(buffer, "DDS", **kwargs)
    finally:
        source.close()
    data = buffer.getvalue()
    if not data.startswith(b"DDS "):
        raise ValueError("DDS 编码器没有生成有效文件头")
    header_size = 148 if data[84:88] == b"DX10" else 128
    return data, header_size


def encode_dds(
    image: Image.Image,
    *,
    pixel_format: str = "DXT5",
    generate_mipmaps: bool = True,
) -> bytes:
    supported = {"RGBA8", "DXT1", "DXT3", "DXT5", "BC2", "BC3", "BC5"}
    if pixel_format not in supported:
        raise ValueError(f"不支持的 DDS 压缩格式：{pixel_format}")
    levels = _mip_chain(image, generate_mipmaps)
    try:
        encoded_levels = [_dds_level(level, pixel_format) for level in levels]
        base_data, header_size = encoded_levels[0]
        header = bytearray(base_data[:header_size])
        if len(levels) > 1:
            flags = struct.unpack_from("<I", header, 8)[0] | 0x00020000
            struct.pack_into("<I", header, 8, flags)
            struct.pack_into("<I", header, 28, len(levels))
            caps = struct.unpack_from("<I", header, 108)[0] | 0x00000008 | 0x00400000
            struct.pack_into("<I", header, 108, caps)
        if header_size == 148 and struct.unpack_from("<I", header, 140)[0] == 0:
            struct.pack_into("<I", header, 140, 1)
        payload = b"".join(data[size:] for data, size in encoded_levels)
        return bytes(header) + payload
    finally:
        for level in levels:
            level.close()


def _srgb_to_linear(values: np.ndarray) -> np.ndarray:
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def encode_exr(image: Image.Image) -> bytes:
    try:
        import OpenEXR
    except ImportError as error:  # pragma: no cover - guarded by release dependency checks
        raise RuntimeError("当前安装缺少 OpenEXR 编码组件") from error

    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
    rgba[..., :3] = _srgb_to_linear(rgba[..., :3])
    pixels = rgba.astype(np.float16)
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        "comments": "CSImage linear RGBA export",
    }
    descriptor, temporary_name = tempfile.mkstemp(suffix=".exr")
    os.close(descriptor)
    try:
        with OpenEXR.File(header, {"RGBA": pixels}) as output:
            output.write(temporary_name)
        return Path(temporary_name).read_bytes()
    finally:
        try:
            Path(temporary_name).unlink()
        except FileNotFoundError:
            pass


def decode_exr(path: str | Path) -> Image.Image:
    try:
        import OpenEXR
    except ImportError as error:  # pragma: no cover - guarded by release dependency checks
        raise RuntimeError("当前安装缺少 OpenEXR 解码组件") from error

    with OpenEXR.File(str(path)) as source:
        channels = source.channels()
        if "RGBA" in channels:
            rgba = np.asarray(channels["RGBA"].pixels, dtype=np.float32)
        elif "RGB" in channels:
            rgb = np.asarray(channels["RGB"].pixels, dtype=np.float32)
            alpha = np.ones((*rgb.shape[:2], 1), dtype=np.float32)
            rgba = np.concatenate((rgb[..., :3], alpha), axis=2)
        else:
            red = np.asarray(channels["R"].pixels, dtype=np.float32)
            green = np.asarray(channels["G"].pixels, dtype=np.float32)
            blue = np.asarray(channels["B"].pixels, dtype=np.float32)
            alpha_channel = channels.get("A")
            alpha = (
                np.asarray(alpha_channel.pixels, dtype=np.float32)
                if alpha_channel is not None
                else np.ones_like(red)
            )
            rgba = np.stack((red, green, blue, alpha), axis=2)
    rgb = np.clip(rgba[..., :3], 0.0, None)
    srgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)
    pixels = np.empty(rgba.shape, dtype=np.uint8)
    pixels[..., :3] = np.clip(srgb * 255.0, 0, 255).astype(np.uint8)
    pixels[..., 3] = np.clip(rgba[..., 3] * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(pixels)


def _rgbe_pixels(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    rgb = _srgb_to_linear(rgb)
    maximum = np.max(rgb, axis=2)
    mantissa, exponent = np.frexp(maximum)
    scale = np.zeros_like(maximum)
    valid = maximum > 1.0e-32
    scale[valid] = mantissa[valid] * 256.0 / maximum[valid]
    rgbe = np.zeros((*maximum.shape, 4), dtype=np.uint8)
    rgbe[..., :3] = np.clip(rgb * scale[..., None], 0, 255).astype(np.uint8)
    rgbe[..., 3][valid] = np.clip(exponent[valid] + 128, 0, 255).astype(np.uint8)
    return rgbe


def _radiance_rle_channel(channel: bytes) -> bytes:
    output = bytearray()
    index = 0
    while index < len(channel):
        run = 1
        while index + run < len(channel) and channel[index + run] == channel[index] and run < 127:
            run += 1
        if run >= 4:
            output.extend((128 + run, channel[index]))
            index += run
            continue
        literal_start = index
        index += run
        while index < len(channel) and index - literal_start < 128:
            next_run = 1
            while (
                index + next_run < len(channel)
                and channel[index + next_run] == channel[index]
                and next_run < 127
            ):
                next_run += 1
            if next_run >= 4:
                break
            index += next_run
        literal = channel[literal_start:index]
        output.append(len(literal))
        output.extend(literal)
    return bytes(output)


def encode_hdr(image: Image.Image) -> bytes:
    rgbe = _rgbe_pixels(image)
    height, width = rgbe.shape[:2]
    output = bytearray(
        f"#?RADIANCE\nSOFTWARE=CSImage\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n".encode(
            "ascii"
        )
    )
    if width < 8 or width > 32767:
        output.extend(rgbe.tobytes())
        return bytes(output)
    for row in rgbe:
        output.extend((2, 2, (width >> 8) & 0xFF, width & 0xFF))
        for channel in range(4):
            output.extend(_radiance_rle_channel(row[:, channel].tobytes()))
    return bytes(output)


def decode_hdr(path: str | Path) -> Image.Image:
    data = Path(path).read_bytes()
    marker = b"\n\n"
    header_end = data.find(marker)
    if header_end < 0 or not data.startswith((b"#?RADIANCE", b"#?RGBE")):
        raise ValueError("无效的 Radiance HDR 文件")
    resolution_end = data.find(b"\n", header_end + len(marker))
    if resolution_end < 0:
        raise ValueError("HDR 文件缺少尺寸信息")
    resolution = data[header_end + len(marker) : resolution_end].decode("ascii").split()
    if len(resolution) != 4 or resolution[0] != "-Y" or resolution[2] != "+X":
        raise ValueError("暂不支持该 HDR 像素方向")
    height, width = int(resolution[1]), int(resolution[3])
    cursor = resolution_end + 1
    rgbe = np.empty((height, width, 4), dtype=np.uint8)
    for row_index in range(height):
        if width < 8 or width > 32767:
            row_size = width * 4
            rgbe[row_index] = np.frombuffer(data[cursor : cursor + row_size], np.uint8).reshape(
                width, 4
            )
            cursor += row_size
            continue
        if data[cursor : cursor + 2] != b"\x02\x02":
            raise ValueError("HDR 扫描线格式无效")
        encoded_width = data[cursor + 2] * 256 + data[cursor + 3]
        if encoded_width != width:
            raise ValueError("HDR 扫描线宽度不一致")
        cursor += 4
        for channel_index in range(4):
            values = bytearray()
            while len(values) < width:
                count = data[cursor]
                cursor += 1
                if count > 128:
                    values.extend((data[cursor],) * (count - 128))
                    cursor += 1
                else:
                    values.extend(data[cursor : cursor + count])
                    cursor += count
            rgbe[row_index, :, channel_index] = np.frombuffer(values, np.uint8)
    exponent = rgbe[..., 3].astype(np.int32)
    scale = np.zeros((height, width), dtype=np.float32)
    valid = exponent > 0
    scale[valid] = np.ldexp(
        np.ones(np.count_nonzero(valid), dtype=np.float32), exponent[valid] - 136
    )
    linear = rgbe[..., :3].astype(np.float32) * scale[..., None]
    srgb = np.where(
        linear <= 0.0031308,
        linear * 12.92,
        1.055 * np.maximum(linear, 0.0) ** (1 / 2.4) - 0.055,
    )
    return Image.fromarray(np.clip(srgb * 255.0, 0, 255).astype(np.uint8))


def encode_ico(image: Image.Image, sizes: tuple[int, ...]) -> bytes:
    normalized = tuple(sorted({int(size) for size in sizes if 1 <= int(size) <= 256}))
    if not normalized:
        normalized = (16, 24, 32, 48, 64, 128, 256)
    largest = max(normalized)
    source = image.convert("RGBA")
    if source.size != (largest, largest):
        canvas = Image.new("RGBA", (largest, largest), (0, 0, 0, 0))
        fitted = source.copy()
        fitted.thumbnail((largest, largest), Image.Resampling.LANCZOS)
        canvas.alpha_composite(
            fitted,
            ((largest - fitted.width) // 2, (largest - fitted.height) // 2),
        )
        fitted.close()
        source.close()
        source = canvas
    buffer = io.BytesIO()
    try:
        source.save(buffer, "ICO", sizes=[(size, size) for size in normalized])
        return buffer.getvalue()
    finally:
        source.close()


def _configure_libktx() -> None:
    if os.name != "nt":
        return
    configured = os.environ.get("LIBKTX_LIB_DIR")
    if configured and (Path(configured) / "ktx.dll").is_file():
        return
    candidates = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "ktx")
    candidates.append(Path(__file__).resolve().parents[1] / "resources" / "runtime" / "ktx")
    candidates.append(Path(sys.executable).resolve().parent / "_internal" / "ktx")
    for candidate in candidates:
        if (candidate / "ktx.dll").is_file():
            os.environ["LIBKTX_LIB_DIR"] = str(candidate)
            return
    raise RuntimeError("当前安装缺少 KTX2 编码组件 ktx.dll")


def encode_ktx2(
    image: Image.Image,
    *,
    mode: str = "uastc",
    generate_mipmaps: bool = True,
    quality: int = 92,
) -> bytes:
    _configure_libktx()
    try:
        from pyktx import (
            KtxBasisParams,
            KtxPackUastcFlagBits,
            KtxTexture2,
            KtxTextureCreateInfo,
            KtxTextureCreateStorage,
            VkFormat,
        )
    except (ImportError, OSError) as error:  # pragma: no cover - guarded by bundle tests
        raise RuntimeError("KTX2 编码组件无法加载") from error

    if mode not in {"rgba8", "uastc", "etc1s"}:
        raise ValueError(f"不支持的 KTX2 编码模式：{mode}")
    levels = _mip_chain(image, generate_mipmaps)
    texture = None
    try:
        info = KtxTextureCreateInfo(
            None,
            levels[0].width,
            levels[0].height,
            1,
            vk_format=VkFormat.VK_FORMAT_R8G8B8A8_SRGB,
            num_levels=len(levels),
            num_layers=1,
            num_faces=1,
        )
        texture = KtxTexture2.create(info, KtxTextureCreateStorage.ALLOC)
        for level_index, level in enumerate(levels):
            texture.set_image_from_memory(level_index, 0, 0, level.tobytes())
        if mode != "rgba8":
            params = KtxBasisParams(
                uastc=mode == "uastc",
                thread_count=max(1, min(8, os.cpu_count() or 1)),
                compression_level=2,
                quality_level=max(1, min(255, round(quality * 2.55))),
                uastc_flags=KtxPackUastcFlagBits.DEFAULT,
            )
            texture.compress_basis(params)
            if mode == "uastc":
                texture.deflate_zstd(10)
        return texture.write_to_memory()
    finally:
        texture = None
        for level in levels:
            level.close()


def decode_ktx2(path: str | Path) -> Image.Image:
    _configure_libktx()
    try:
        from pyktx import KtxTexture2, KtxTranscodeFmt
    except (ImportError, OSError) as error:  # pragma: no cover - guarded by bundle tests
        raise RuntimeError("KTX2 解码组件无法加载") from error

    texture = KtxTexture2.create_from_named_file(str(Path(path).resolve()))
    try:
        if texture.needs_transcoding:
            texture.transcode_basis(KtxTranscodeFmt.RGBA32)
        width, height = int(texture.base_width), int(texture.base_height)
        offset = int(texture.image_offset(0, 0, 0))
        row_pitch = int(texture.row_pitch(0))
        raw = bytes(texture.data())
        rows = [
            raw[offset + row * row_pitch : offset + row * row_pitch + width * 4]
            for row in range(height)
        ]
        return Image.frombytes("RGBA", (width, height), b"".join(rows))
    finally:
        texture = None


def encode_svg(image: Image.Image) -> bytes:
    png = io.BytesIO()
    source = image.convert("RGBA")
    try:
        source.save(png, "PNG", optimize=True)
    finally:
        source.close()
    encoded = base64.b64encode(png.getvalue()).decode("ascii")
    width, height = image.size
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
        "  <!-- CSImage raster container; pixels are not vector-traced. -->\n"
        f'  <image width="{width}" height="{height}" '
        f'href="data:image/png;base64,{encoded}" xlink:href="data:image/png;base64,{encoded}"/>\n'
        "</svg>\n"
    )
    return document.encode("utf-8")


def encode_eps(image: Image.Image, *, illustrator: bool = False) -> bytes:
    source = _flatten_alpha(image)
    try:
        width, height = source.size
        prefix = (
            "%!PS-Adobe-3.0 EPSF-3.0\n"
            + ("%%AI5_FileFormat 3\n" if illustrator else "")
            + f"%%Creator: CSImage\n%%BoundingBox: 0 0 {width} {height}\n"
            "%%LanguageLevel: 2\n%%EndComments\n"
            f"/picstr {width * 3} string def\n"
            f"{width} {height} 8\n[{width} 0 0 -{height} 0 {height}]\n"
            "{currentfile picstr readhexstring pop} false 3 colorimage\n"
        ).encode("ascii")
        raw_hex = source.tobytes().hex().encode("ascii")
        lines = b"\n".join(raw_hex[index : index + 120] for index in range(0, len(raw_hex), 120))
        return prefix + lines + b"\nshowpage\n%%EOF\n"
    finally:
        source.close()


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _placeable_wmf_header(width: int, height: int) -> bytes:
    scale = min(1.0, 32767 / max(1, width, height))
    right = max(1, round(width * scale))
    bottom = max(1, round(height * scale))
    units_per_inch = 72
    words = [0xCDD7, 0x9AC6, 0, 0, 0, right, bottom, units_per_inch, 0, 0]
    checksum = 0
    for word in words:
        checksum ^= word & 0xFFFF
    return struct.pack(
        "<I H hhhh H I H",
        0x9AC6CDD7,
        0,
        0,
        0,
        right,
        bottom,
        units_per_inch,
        0,
        checksum,
    )


def encode_windows_metafile(image: Image.Image, *, enhanced: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("EMF/WMF 输出仅在 Windows 上可用")
    from PIL import ImageWin

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32.CreateEnhMetaFileW.argtypes = [
        wintypes.HDC,
        wintypes.LPCWSTR,
        ctypes.POINTER(_Rect),
        wintypes.LPCWSTR,
    ]
    gdi32.CreateEnhMetaFileW.restype = wintypes.HDC
    gdi32.CloseEnhMetaFile.argtypes = [wintypes.HDC]
    gdi32.CloseEnhMetaFile.restype = wintypes.HANDLE
    gdi32.GetEnhMetaFileBits.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p]
    gdi32.GetEnhMetaFileBits.restype = wintypes.UINT
    gdi32.GetWinMetaFileBits.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_int,
        wintypes.HDC,
    ]
    gdi32.GetWinMetaFileBits.restype = wintypes.UINT
    gdi32.DeleteEnhMetaFile.argtypes = [wintypes.HANDLE]
    gdi32.DeleteEnhMetaFile.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    source = _flatten_alpha(image)
    width, height = source.size
    frame = _Rect(0, 0, max(1, width * 26), max(1, height * 26))
    hdc = gdi32.CreateEnhMetaFileW(None, None, ctypes.byref(frame), "CSImage\0Raster export\0\0")
    if not hdc:
        source.close()
        raise OSError(ctypes.get_last_error(), "无法创建 Windows 图元文件")
    metafile = None
    try:
        ImageWin.Dib(source).draw(hdc, (0, 0, width + 1, height + 1))
        metafile = gdi32.CloseEnhMetaFile(hdc)
        hdc = None
        if not metafile:
            raise OSError(ctypes.get_last_error(), "无法完成 Windows 图元文件")
        if enhanced:
            size = gdi32.GetEnhMetaFileBits(metafile, 0, None)
            buffer = ctypes.create_string_buffer(size)
            written = gdi32.GetEnhMetaFileBits(metafile, size, buffer)
            if not written:
                raise OSError(ctypes.get_last_error(), "无法读取 EMF 数据")
            return buffer.raw[:written]
        reference_dc = user32.GetDC(None)
        try:
            size = gdi32.GetWinMetaFileBits(metafile, 0, None, 8, reference_dc)
            buffer = ctypes.create_string_buffer(size)
            written = gdi32.GetWinMetaFileBits(metafile, size, buffer, 8, reference_dc)
            if not written:
                raise OSError(ctypes.get_last_error(), "无法读取 WMF 数据")
            return _placeable_wmf_header(width, height) + buffer.raw[:written]
        finally:
            if reference_dc:
                user32.ReleaseDC(None, reference_dc)
    finally:
        source.close()
        if hdc:
            closed = gdi32.CloseEnhMetaFile(hdc)
            if closed:
                gdi32.DeleteEnhMetaFile(closed)
        if metafile:
            gdi32.DeleteEnhMetaFile(metafile)


def encode_advanced_image(
    image: Image.Image,
    output_format: str,
    *,
    quality: int = 92,
    tga_rle: bool = True,
    dds_pixel_format: str = "DXT5",
    generate_mipmaps: bool = True,
    ico_sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256),
    ktx2_mode: str = "uastc",
) -> bytes:
    if output_format == "TGA":
        return encode_tga(image, rle=tga_rle)
    if output_format == "DDS":
        return encode_dds(
            image,
            pixel_format=dds_pixel_format,
            generate_mipmaps=generate_mipmaps,
        )
    if output_format == "EXR":
        return encode_exr(image)
    if output_format == "HDR":
        return encode_hdr(image)
    if output_format == "ICO":
        return encode_ico(image, ico_sizes)
    if output_format == "KTX2":
        return encode_ktx2(
            image,
            mode=ktx2_mode,
            generate_mipmaps=generate_mipmaps,
            quality=quality,
        )
    if output_format == "SVG":
        return encode_svg(image)
    if output_format == "AI":
        return encode_eps(image, illustrator=True)
    if output_format == "EPS":
        return encode_eps(image)
    if output_format == "EMF":
        return encode_windows_metafile(image, enhanced=True)
    if output_format == "WMF":
        return encode_windows_metafile(image, enhanced=False)
    raise ValueError(f"没有可用的高级格式编码器：{output_format}")
