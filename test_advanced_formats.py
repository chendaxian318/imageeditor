from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from image_ops import ExportOptions, encode_image, save_image
from light_image_editor.document import metadata_from_image
from light_image_editor.image_loader import decode_image


class AdvancedFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = Image.new("RGBA", (17, 13), (20, 80, 190, 150))

    def tearDown(self) -> None:
        self.image.close()

    def test_professional_formats_have_valid_headers_and_round_trip(self) -> None:
        expected_headers = {
            "TGA": None,
            "DDS": b"DDS ",
            "EXR": b"v/1\x01",
            "HDR": b"#?RADIANCE\n",
            "ICO": b"\x00\x00\x01\x00",
            "KTX2": b"\xabKTX 20\xbb\r\n\x1a\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for output_format, header in expected_headers.items():
                with self.subTest(output_format=output_format):
                    target = root / f"sample.{output_format.lower()}"
                    save_image(
                        self.image,
                        target,
                        ExportOptions(output_format),
                        allow_transparency_flatten=True,
                    )
                    data = target.read_bytes()
                    if header is not None:
                        self.assertTrue(data.startswith(header))
                    decoded = decode_image(target)
                    try:
                        expected_size = (256, 256) if output_format == "ICO" else self.image.size
                        self.assertEqual(decoded.size, expected_size)
                    finally:
                        decoded.close()

    def test_dds_writes_bc_mip_chain(self) -> None:
        data = encode_image(
            self.image,
            "DDS",
            ExportOptions("DDS", dds_pixel_format="DXT5", generate_mipmaps=True),
        )
        self.assertEqual(struct.unpack_from("<I", data, 28)[0], 5)
        self.assertNotEqual(struct.unpack_from("<I", data, 108)[0] & 0x00400000, 0)

    def test_ico_contains_all_requested_sizes(self) -> None:
        data = encode_image(self.image, "ICO", ExportOptions("ICO"))
        self.assertEqual(struct.unpack_from("<H", data, 4)[0], 7)

    def test_raster_vector_containers_are_explicit_and_valid(self) -> None:
        outputs = {
            output_format: encode_image(self.image, output_format, ExportOptions(output_format))
            for output_format in ("SVG", "AI", "EPS", "EMF", "WMF")
        }
        self.assertIn(b"pixels are not vector-traced", outputs["SVG"])
        self.assertTrue(outputs["AI"].startswith(b"%!PS-Adobe-3.0"))
        self.assertIn(b"%%AI5_FileFormat", outputs["AI"])
        self.assertTrue(outputs["EPS"].startswith(b"%!PS-Adobe-3.0"))
        self.assertEqual(outputs["EMF"][40:44], b" EMF")
        self.assertTrue(outputs["WMF"].startswith(b"\xd7\xcd\xc6\x9a"))

    def test_gif_and_apng_can_preserve_or_flatten_animation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.gif"
            first = Image.new("RGBA", (8, 8), "red")
            second = Image.new("RGBA", (8, 8), "blue")
            try:
                first.save(
                    source_path,
                    "GIF",
                    save_all=True,
                    append_images=[second],
                    duration=[50, 90],
                    loop=2,
                )
            finally:
                first.close()
                second.close()

            decoded = decode_image(source_path)
            try:
                metadata = metadata_from_image(decoded)
                for output_format in ("GIF", "APNG"):
                    target = root / f"preserved.{output_format.lower()}"
                    save_image(
                        decoded,
                        target,
                        ExportOptions(output_format, animation_mode="preserve"),
                        metadata=metadata,
                        source_path=source_path,
                        allow_animation_flatten=True,
                    )
                    with Image.open(target) as result:
                        self.assertEqual(result.n_frames, 2)
                        self.assertEqual(result.info.get("loop"), 2)

                flattened = root / "current.gif"
                save_image(
                    decoded,
                    flattened,
                    ExportOptions("GIF", animation_mode="current"),
                    metadata=metadata,
                    source_path=source_path,
                    allow_animation_flatten=True,
                )
                with Image.open(flattened) as result:
                    self.assertEqual(getattr(result, "n_frames", 1), 1)
            finally:
                decoded.close()

    @unittest.skipUnless(os.name == "nt", "KTX-Software validation is Windows-only here")
    def test_ktx2_has_multiple_levels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "sample.ktx2"
            save_image(self.image, target, ExportOptions("KTX2"))
            from pyktx import KtxTexture2

            texture = KtxTexture2.create_from_named_file(str(target.resolve()))
            try:
                self.assertEqual(texture.num_levels, 5)
                self.assertEqual((texture.base_width, texture.base_height), self.image.size)
            finally:
                texture = None


if __name__ == "__main__":
    unittest.main()
