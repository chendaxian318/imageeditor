from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from image_ops import SUPPORTED_INPUTS, VIEWER_INPUTS
from light_image_editor.document import metadata_from_image
from light_image_editor.image_loader import ImageLoader, decode_image
from light_image_editor.pdf_support import PdfSupportError, pdf_page_count, render_pdf_page


def _write_minimal_psd(path: Path) -> None:
    header = b"8BPS" + struct.pack(">H", 1) + b"\0" * 6
    header += struct.pack(">HIIHH", 3, 2, 2, 8, 3)
    sections = struct.pack(">III", 0, 0, 0)
    planes = bytes((255, 0, 0, 255)) + bytes((0, 255, 0, 255)) + bytes((0, 0, 255, 255))
    path.write_bytes(header + sections + struct.pack(">H", 0) + planes)


def _write_two_page_pdf(path: Path) -> None:
    first = Image.new("RGB", (120, 80), "#245680")
    second = Image.new("RGB", (120, 80), "#a03040")
    try:
        first.save(path, save_all=True, append_images=[second])
    finally:
        first.close()
        second.close()


class DocumentFormatTests(unittest.TestCase):
    def test_psd_opens_as_a_flattened_read_only_composite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layers.psd"
            _write_minimal_psd(path)

            decoded = decode_image(path)
            try:
                metadata = metadata_from_image(decoded)
                self.assertEqual(decoded.size, (2, 2))
                self.assertEqual(decoded.getpixel((0, 0)), (255, 0, 0))
                self.assertEqual(metadata.original_format, "PSD")
            finally:
                decoded.close()

        self.assertIn(".psd", SUPPORTED_INPUTS)
        self.assertIn(".psd", VIEWER_INPUTS)

    def test_pdf_pages_render_independently_and_are_page_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pages.pdf"
            _write_two_page_pdf(path)

            self.assertEqual(pdf_page_count(path), 2)
            first = render_pdf_page(path, 0)
            second = render_pdf_page(path, 1)
            loader = ImageLoader(max_workers=1, cache_size=3)
            try:
                self.assertEqual(first.info["pdf_page_count"], 2)
                self.assertEqual(second.info["pdf_page_index"], 1)
                self.assertNotEqual(first.getpixel((20, 20)), second.getpixel((20, 20)))
                cached_first = loader.load(path, page_index=0).result(timeout=5)
                cached_second = loader.load(path, page_index=1).result(timeout=5)
                self.assertNotEqual(
                    cached_first.getpixel((20, 20)), cached_second.getpixel((20, 20))
                )
                cached_first.close()
                cached_second.close()
            finally:
                loader.shutdown()
                first.close()
                second.close()

        self.assertNotIn(".pdf", SUPPORTED_INPUTS)
        self.assertIn(".pdf", VIEWER_INPUTS)

    def test_pdf_rejects_an_invalid_page_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one-page.pdf"
            page = Image.new("RGB", (40, 30), "white")
            try:
                page.save(path)
            finally:
                page.close()
            with self.assertRaises(PdfSupportError):
                render_pdf_page(path, 1)


if __name__ == "__main__":
    unittest.main()
