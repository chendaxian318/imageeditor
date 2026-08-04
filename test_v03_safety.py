from __future__ import annotations

import base64
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image, ImageCms

from image_ops import (
    AnimationFlatteningRequired,
    ConflictPolicy,
    EditState,
    ExportOptions,
    SourceChangedError,
    TransparencyFlatteningRequired,
    save_image,
)
from light_image_editor.document import (
    ORIENTATION_TAG,
    Document,
    DocumentState,
    SourceMetadata,
    attach_source_metadata,
    metadata_from_image,
)
from light_image_editor.image_loader import ImageLoadFailure, decode_image
from light_image_editor.presets import EXPORT_PRESETS, apply_export_preset
from light_image_editor.preview import render_preview
from light_image_editor.session import SessionStore

# CC0 DisplayP3Compat-v4 profile from saucecontrol/Compact-ICC-Profiles.
DISPLAY_P3_ICC = base64.b64decode(
    "AAAB4GxjbXMEIAAAbW50clJHQiBYWVogB+IAAwAUAAkADgAdYWNzcE1TRlQAAAAA"
    "c2F3c2N0cmwAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1oYW5kguKocouKP/clPrmS"
    "7iTWrgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAKZGVzYwAAAPwAAAAk"
    "Y3BydAAAASAAAAAid3RwdAAAAUQAAAAUY2hhZAAAAVgAAAAsclhZWgAAAYQAAAAU"
    "Z1hZWgAAAZgAAAAUYlhZWgAAAawAAAAUclRSQwAAAcAAAAAgZ1RSQwAAAcAAAAAg"
    "YlRSQwAAAcAAAAAgbWx1YwAAAAAAAAABAAAADGVuVVMAAAAIAAAAHABzAFAAMwBD"
    "bWx1YwAAAAAAAAABAAAADGVuVVMAAAAGAAAAHABDAEMAMAAAWFlaIAAAAAAAAPbW"
    "AAEAAAAA0y1zZjMyAAAAAAABDEIAAAXe///zJQAAB5MAAP2Q///7of///k4AAAOa"
    "AADAFFhZWiAAAAAAAACD3wAAPb8AAAAAWFlaIAAAAAAAAEq/AACxNwAACrVYWVog"
    "AAAAAAAAKDgAABEKAADIeHBhcmEAAAAAAAMAAAACZmkAAPKnAAANWQAAE9AAAApb"
)


class FidelityTests(unittest.TestCase):
    def test_all_eight_exif_orientations_open_and_export_normalized(self) -> None:
        transforms = {
            1: None,
            2: Image.Transpose.FLIP_LEFT_RIGHT,
            3: Image.Transpose.ROTATE_180,
            4: Image.Transpose.FLIP_TOP_BOTTOM,
            5: Image.Transpose.TRANSPOSE,
            6: Image.Transpose.ROTATE_270,
            7: Image.Transpose.TRANSVERSE,
            8: Image.Transpose.ROTATE_90,
        }
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            base = Image.new("RGB", (3, 2))
            base.putdata(
                [
                    (255, 0, 0),
                    (0, 255, 0),
                    (0, 0, 255),
                    (255, 255, 0),
                    (0, 255, 255),
                    (255, 0, 255),
                ]
            )
            for orientation, transpose in transforms.items():
                source = folder / f"orientation-{orientation}.png"
                exif = Image.Exif()
                exif[ORIENTATION_TAG] = orientation
                exif[271] = "Golden Camera"
                base.save(source, exif=exif)

                decoded = decode_image(source)
                expected = base.copy() if transpose is None else base.transpose(transpose)
                self.assertEqual(decoded.size, expected.size, orientation)
                self.assertEqual(decoded.tobytes(), expected.tobytes(), orientation)
                metadata = metadata_from_image(decoded)
                self.assertEqual(metadata.original_orientation, orientation)

                target = folder / f"export-{orientation}.png"
                save_image(
                    decoded,
                    target,
                    ExportOptions(output_format="PNG"),
                    metadata=metadata,
                )
                with Image.open(target) as exported:
                    exported.load()
                    self.assertEqual(exported.getexif().get(ORIENTATION_TAG), None, orientation)
                    self.assertEqual(exported.getexif().get(271), "Golden Camera", orientation)
                    self.assertEqual(exported.tobytes(), expected.tobytes(), orientation)

    def test_icc_exif_dpi_xmp_are_preserved_by_supported_formats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
            exif = Image.Exif()
            exif[271] = "Metadata Camera"
            metadata = SourceMetadata(
                original_format="PNG",
                exif=exif.tobytes(),
                icc_profile=icc,
                dpi=(144.0, 144.0),
                xmp=b"<x:xmpmeta>Display P3 workflow sample</x:xmpmeta>",
            )
            image = Image.new("RGB", (40, 30), "navy")

            for output_format, suffix in (("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")):
                target = folder / f"metadata{suffix}"
                save_image(
                    image,
                    target,
                    ExportOptions(output_format=output_format, quality=95),
                    metadata=metadata,
                )
                with Image.open(target) as exported:
                    exported.load()
                    self.assertEqual(exported.info.get("icc_profile"), icc, output_format)
                    self.assertEqual(exported.getexif().get(271), "Metadata Camera", output_format)
                    if output_format in {"JPEG", "PNG", "WEBP"}:
                        self.assertIn(b"Display P3", exported.info.get("xmp", b""))
                    if output_format in {"PNG", "JPEG"}:
                        dpi = exported.info.get("dpi")
                        self.assertIsNotNone(dpi, output_format)

    def test_transparency_loss_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "flat.jpg"
            image = Image.new("RGBA", (10, 10), (255, 0, 0, 80))
            metadata = SourceMetadata(has_alpha=True)
            with self.assertRaises(TransparencyFlatteningRequired):
                save_image(
                    image,
                    target,
                    ExportOptions(output_format="JPEG"),
                    metadata=metadata,
                )
            save_image(
                image,
                target,
                ExportOptions(output_format="JPEG"),
                metadata=metadata,
                allow_transparency_flatten=True,
            )
            self.assertTrue(target.exists())

    def test_original_quality_passthrough_is_byte_exact_and_keeps_animation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "animated.webp"
            target = folder / "copy.webp"
            frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
            frames[0].save(
                source,
                "WEBP",
                save_all=True,
                append_images=frames[1:],
                duration=[80, 120],
                loop=0,
                lossless=True,
            )
            document = Document.from_decoded(decode_image(source), source)
            save_image(
                document.image,
                target,
                ExportOptions(output_format="WEBP"),
                metadata=document.metadata,
                source_path=document.source_path,
                source_fingerprint=document.source_fingerprint,
                source_passthrough=True,
            )
            self.assertEqual(target.read_bytes(), source.read_bytes())
            with Image.open(target) as copied:
                self.assertTrue(copied.is_animated)
                self.assertEqual(copied.n_frames, 2)


class SafeSaveTests(unittest.TestCase):
    def test_interrupted_atomic_export_keeps_original_file_intact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "original.png"
            original = b"original-file-contents"
            target.write_bytes(original)

            def interrupt(_temporary: Path, _target: Path) -> None:
                raise RuntimeError("simulated interruption")

            with self.assertRaisesRegex(RuntimeError, "simulated"):
                save_image(
                    Image.new("RGB", (20, 20), "red"),
                    target,
                    ExportOptions(output_format="PNG"),
                    conflict_policy=ConflictPolicy.OVERWRITE,
                    before_replace=interrupt,
                )

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_conflict_policy_and_backup_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "image.png"
            target.write_bytes(b"old")
            image = Image.new("RGB", (20, 20), "blue")
            with self.assertRaises(FileExistsError):
                save_image(image, target, ExportOptions(output_format="PNG"))
            saved = save_image(
                image,
                target,
                ExportOptions(output_format="PNG"),
                conflict_policy=ConflictPolicy.OVERWRITE,
                backup_on_overwrite=True,
            )
            self.assertEqual(saved, target)
            self.assertEqual((target.parent / "image.png.bak").read_bytes(), b"old")

    def test_external_source_change_blocks_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            Image.new("RGB", (20, 20), "red").save(source)
            decoded = decode_image(source)
            document = Document.from_decoded(decoded, source)
            source.write_bytes(b"externally replaced")
            with self.assertRaises(SourceChangedError):
                save_image(
                    document.image,
                    source,
                    ExportOptions(output_format="PNG"),
                    source_path=document.source_path,
                    source_fingerprint=document.source_fingerprint,
                    conflict_policy=ConflictPolicy.OVERWRITE,
                )

    @unittest.skipUnless(os.name == "nt", "rename identity relies on Windows file identity")
    def test_document_detects_external_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "before.png"
            renamed = Path(directory) / "after.png"
            Image.new("RGB", (20, 20), "red").save(source)
            document = Document.from_decoded(decode_image(source), source)
            source.rename(renamed)
            status = document.source_status()
            self.assertEqual(status.kind, "renamed")
            self.assertEqual(status.path, renamed.resolve())

    def test_rebinding_renamed_source_does_not_hide_unsaved_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "before.png"
            renamed = Path(directory) / "after.png"
            Image.new("RGB", (20, 20), "red").save(source)
            document = Document.from_decoded(decode_image(source), source)
            document.update_state("brightness", edit_state=EditState(brightness=10))
            source.rename(renamed)
            document.rebind_source(renamed)
            self.assertTrue(document.dirty)
            self.assertEqual(document.source_status().kind, "unchanged")


class AnimationAndRecoveryTests(unittest.TestCase):
    def test_gif_apng_and_webp_cannot_be_silently_flattened(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for file_format, suffix in (("GIF", ".gif"), ("PNG", ".png"), ("WEBP", ".webp")):
                with self.subTest(file_format=file_format):
                    source = folder / f"animated-{file_format.lower()}{suffix}"
                    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
                    frames[0].save(
                        source,
                        file_format,
                        save_all=True,
                        append_images=frames[1:],
                        duration=[80, 120],
                        loop=0,
                        lossless=True,
                    )
                    decoded = decode_image(source)
                    metadata = metadata_from_image(decoded)
                    self.assertTrue(metadata.is_animated)
                    self.assertEqual(metadata.frame_count, 2)
                    if file_format == "GIF":
                        self.assertEqual(metadata.durations, (80, 120))
                    with self.assertRaises(AnimationFlatteningRequired):
                        save_image(
                            decoded,
                            folder / f"flattened-{file_format.lower()}.png",
                            ExportOptions(output_format="PNG"),
                            metadata=metadata,
                        )

    def test_state_only_session_survives_source_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            session_path = folder / "session.json"
            Image.new("RGB", (50, 30), "green").save(source)
            document = Document.from_decoded(decode_image(source), source)
            document.update_state("contrast", edit_state=EditState(contrast=20))
            store = SessionStore(session_path)
            store.save(document)
            source.unlink()

            recovered = store.load()
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertTrue(recovered.source_changed)
            self.assertEqual(recovered.document.image.size, (50, 30))
            self.assertEqual(recovered.document.state.edit_state.contrast, 20)

    def test_dirty_document_can_be_restored_after_abnormal_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            session_path = folder / "session.json"
            Image.new("RGB", (50, 30), "green").save(source)
            document = Document.from_decoded(decode_image(source), source)
            document.update_state("rotate", edit_state=EditState(rotation=90))
            store = SessionStore(session_path)
            store.save(document)

            recovered = SessionStore(session_path).load()
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertTrue(recovered.document.dirty)
            self.assertEqual(recovered.document.state.edit_state.rotation, 90)
            self.assertEqual(recovered.document.source_path, source.resolve())
            store.clear()
            self.assertFalse(session_path.exists())

    def test_autosave_snapshot_and_state_commit_as_one_recoverable_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            store = SessionStore(folder / "session.json")
            Image.new("RGB", (20, 20), "green").save(source)
            document = Document.from_decoded(decode_image(source), source)
            document.update_state("brightness", edit_state=EditState(brightness=10))
            store.save(document)
            document.update_state("brightness", edit_state=EditState(brightness=20))
            store.save(document)

            self.assertEqual(len(list(folder.glob("session.recovery-*.png"))), 1)
            recovered = store.load()
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual(recovered.document.state.edit_state.brightness, 20)


class BoundaryAndPresetTests(unittest.TestCase):
    def test_oversized_png_header_is_rejected_before_pixel_allocation(self) -> None:
        def chunk(kind: bytes, data: bytes) -> bytes:
            payload = kind + data
            return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "oversized.png"
            source.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 20_000, 20_000, 8, 2, 0, 0, 0))
                + chunk(b"IEND", b"")
            )
            with self.assertRaises(ImageLoadFailure) as raised:
                decode_image(source)
            self.assertEqual(raised.exception.kind, "too_large")

    def test_document_operation_log_stays_monotonic_when_capped(self) -> None:
        document = Document.from_decoded(Image.new("RGB", (2, 2)), None)
        for quality in range(1, 502):
            document.update_state("quality", quality=quality)
        self.assertEqual(len(document.operations), 500)
        self.assertEqual(document.operations[-1].sequence, 501)
        self.assertTrue(document.dirty)

    def test_icc_profile_is_converted_only_for_display_preview(self) -> None:
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        original = attach_source_metadata(
            Image.new("RGBA", (10, 10), (20, 40, 60, 128)),
            SourceMetadata(icc_profile=icc, has_alpha=True),
        )
        frame = render_preview(
            original,
            EditState(),
            compare_original=False,
            canvas_size=(100, 100),
            zoom_percent=100,
        )
        self.assertEqual(frame.image.mode, "RGBA")
        self.assertEqual(frame.image.getchannel("A").getextrema(), (128, 128))
        self.assertEqual(metadata_from_image(original).icc_profile, icc)

    def test_display_p3_profile_is_preserved_but_previewed_in_srgb(self) -> None:
        original = attach_source_metadata(
            Image.new("RGB", (10, 10), (80, 200, 70)),
            SourceMetadata(icc_profile=DISPLAY_P3_ICC),
        )
        frame = render_preview(
            original,
            EditState(),
            compare_original=False,
            canvas_size=(100, 100),
            zoom_percent=100,
        )
        self.assertEqual(original.getpixel((0, 0)), (80, 200, 70))
        self.assertEqual(frame.image.getpixel((0, 0)), (0, 203, 39))
        self.assertEqual(metadata_from_image(original).icc_profile, DISPLAY_P3_ICC)

    def test_truncated_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "truncated.png"
            full = Path(directory) / "full.png"
            Image.new("RGB", (100, 100), "red").save(full)
            source.write_bytes(full.read_bytes()[:50])
            with self.assertRaises(ImageLoadFailure) as raised:
                decode_image(source)
            self.assertIn(raised.exception.kind, {"truncated", "damaged", "decode"})

    def test_export_presets_are_complete_and_deterministic(self) -> None:
        self.assertEqual(EXPORT_PRESETS, ("自定义", "社交媒体", "头像", "邮件", "原质量"))
        state = DocumentState()
        social = apply_export_preset(state, "社交媒体", "PNG")
        avatar = apply_export_preset(state, "头像", "JPEG")
        original = apply_export_preset(state, "原质量", "WEBP")
        self.assertEqual((social.output_format, social.max_side_text), ("JPEG", "2048"))
        self.assertEqual((avatar.output_format, avatar.width_text), ("PNG", "1024"))
        self.assertEqual(original.output_format, "WEBP")


if __name__ == "__main__":
    unittest.main()
