from __future__ import annotations

import ctypes
import struct
import sys
import tempfile
import unittest
from functools import reduce
from operator import xor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from image_ops import SUPPORTED_INPUTS, VIEWER_INPUTS
from light_image_editor.document import metadata_from_image
from light_image_editor.image_loader import ImageLoadFailure, decode_image
from light_image_editor.qt_viewer import READ_ONLY_EDIT_SOURCE_EXTENSIONS
from light_image_editor.thumbnails import generate_thumbnail
from light_image_editor.vector_support import VectorSupportError, render_vector_image


def _write_svg(path: Path) -> None:
    path.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"
        viewBox="0 0 160 90">
        <rect width="160" height="90" rx="12" fill="#1b76d1"/>
        <circle cx="80" cy="45" r="24" fill="#f3ca40"/>
        </svg>""",
        encoding="utf-8",
    )


def _write_pdf_compatible_ai(path: Path) -> None:
    source = Image.new("RGB", (120, 80), "#4c9c62")
    try:
        source.save(path, format="PDF")
    finally:
        source.close()


def _write_eps(path: Path) -> None:
    path.write_text(
        "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 120 80\n"
        "0 0 1 setrgbcolor 0 0 120 80 rectfill\nshowpage\n",
        encoding="ascii",
    )


@unittest.skipUnless(sys.platform == "win32", "Windows metafile fixture needs GDI")
def _write_emf(path: Path) -> None:
    from ctypes import wintypes

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.CreateEnhMetaFileW.argtypes = (
        wintypes.HDC,
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPCWSTR,
    )
    gdi32.CreateEnhMetaFileW.restype = wintypes.HDC
    gdi32.Rectangle.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    )
    gdi32.Rectangle.restype = wintypes.BOOL
    gdi32.CloseEnhMetaFile.argtypes = (wintypes.HDC,)
    gdi32.CloseEnhMetaFile.restype = wintypes.HANDLE
    gdi32.DeleteEnhMetaFile.argtypes = (wintypes.HANDLE,)
    gdi32.DeleteEnhMetaFile.restype = wintypes.BOOL

    frame = wintypes.RECT(0, 0, 2540, 1270)
    drawing_context = gdi32.CreateEnhMetaFileW(None, str(path), ctypes.byref(frame), None)
    if not drawing_context:
        raise ctypes.WinError(ctypes.get_last_error())
    gdi32.Rectangle(drawing_context, 0, 0, 400, 200)
    metafile = gdi32.CloseEnhMetaFile(drawing_context)
    if not metafile:
        raise ctypes.WinError(ctypes.get_last_error())
    gdi32.DeleteEnhMetaFile(metafile)


@unittest.skipUnless(sys.platform == "win32", "Windows metafile fixture needs GDI")
def _write_wmf(path: Path) -> None:
    from ctypes import wintypes

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi32.CreateMetaFileW.argtypes = (wintypes.LPCWSTR,)
    gdi32.CreateMetaFileW.restype = wintypes.HDC
    gdi32.Rectangle.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    )
    gdi32.Rectangle.restype = wintypes.BOOL
    gdi32.CloseMetaFile.argtypes = (wintypes.HDC,)
    gdi32.CloseMetaFile.restype = wintypes.HANDLE
    gdi32.DeleteMetaFile.argtypes = (wintypes.HANDLE,)
    gdi32.DeleteMetaFile.restype = wintypes.BOOL

    drawing_context = gdi32.CreateMetaFileW(str(path))
    if not drawing_context:
        raise ctypes.WinError(ctypes.get_last_error())
    gdi32.Rectangle(drawing_context, 0, 0, 1440, 720)
    metafile = gdi32.CloseMetaFile(drawing_context)
    if not metafile:
        raise ctypes.WinError(ctypes.get_last_error())
    gdi32.DeleteMetaFile(metafile)

    standard_metafile = path.read_bytes()
    prefix = struct.pack("<IHhhhhHI", 0x9AC6CDD7, 0, 0, 0, 1440, 720, 1440, 0)
    checksum = reduce(xor, struct.unpack("<10H", prefix))
    path.write_bytes(prefix + struct.pack("<H", checksum) + standard_metafile)


class VectorFormatTests(unittest.TestCase):
    def test_all_requested_extensions_are_available_to_viewer_and_read_only_editor(self) -> None:
        requested = {".svg", ".ai", ".eps", ".emf", ".wmf"}
        self.assertTrue(requested <= SUPPORTED_INPUTS)
        self.assertTrue(requested <= VIEWER_INPUTS)
        self.assertTrue(requested <= READ_ONLY_EDIT_SOURCE_EXTENSIONS)

    def test_svg_decodes_with_alpha_metadata_and_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shape.svg"
            _write_svg(path)
            decoded = decode_image(path)
            thumbnail = generate_thumbnail(path)
            try:
                self.assertEqual(decoded.size, (160, 90))
                self.assertEqual(decoded.mode, "RGBA")
                self.assertEqual(decoded.format, "SVG")
                self.assertEqual(decoded.getpixel((80, 45)), (243, 202, 64, 255))
                self.assertTrue(decoded.info["vector_source"])
                self.assertEqual(metadata_from_image(decoded).original_format, "SVG")
                self.assertEqual(thumbnail.size, (66, 48))
            finally:
                decoded.close()
                thumbnail.close()

    def test_pdf_compatible_ai_uses_pdfium_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compatible.ai"
            _write_pdf_compatible_ai(path)
            decoded = decode_image(path)
            try:
                self.assertEqual(decoded.format, "AI")
                self.assertEqual(decoded.size, (240, 160))
                self.assertTrue(decoded.info["vector_source"])
                self.assertEqual(metadata_from_image(decoded).original_format, "AI")
            finally:
                decoded.close()

    @unittest.skipUnless(sys.platform == "win32", "Windows metafile decoding needs GDI")
    def test_emf_and_wmf_decode_through_windows_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = ((root / "sample.emf", _write_emf), (root / "sample.wmf", _write_wmf))
            for path, writer in fixtures:
                with self.subTest(extension=path.suffix):
                    writer(path)
                    decoded = decode_image(path)
                    thumbnail = generate_thumbnail(path)
                    try:
                        self.assertGreater(decoded.width, 0)
                        self.assertGreater(decoded.height, 0)
                        self.assertEqual(decoded.mode, "RGBA")
                        self.assertEqual(decoded.format, path.suffix[1:].upper())
                        self.assertEqual(thumbnail.size, (66, 48))
                    finally:
                        decoded.close()
                        thumbnail.close()

    def test_postscript_eps_uses_safe_external_renderer_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.eps"
            _write_eps(path)

            def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                self.assertIn("-dSAFER", command)
                self.assertIn("-dFirstPage=1", command)
                output_argument = next(item for item in command if item.startswith("-sOutputFile="))
                output = Path(output_argument.removeprefix("-sOutputFile="))
                rendered = Image.new("RGBA", (120, 80), (20, 40, 220, 255))
                try:
                    rendered.save(output)
                finally:
                    rendered.close()
                return SimpleNamespace(returncode=0)

            with (
                patch(
                    "light_image_editor.vector_support._find_ghostscript",
                    return_value=Path("gs.exe"),
                ),
                patch("light_image_editor.vector_support.subprocess.run", side_effect=fake_run),
            ):
                decoded = render_vector_image(path)
            try:
                self.assertEqual(decoded.size, (120, 80))
                self.assertEqual(decoded.format, "EPS")
            finally:
                decoded.close()

    def test_postscript_without_renderer_has_a_specific_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.ai"
            _write_eps(path)
            with patch("light_image_editor.vector_support._find_ghostscript", return_value=None):
                with self.assertRaises(ImageLoadFailure) as context:
                    decode_image(path)
            self.assertEqual(context.exception.kind, "vector")
            self.assertIn("Ghostscript", context.exception.user_message)
            self.assertIsInstance(context.exception.__cause__, VectorSupportError)


if __name__ == "__main__":
    unittest.main()
