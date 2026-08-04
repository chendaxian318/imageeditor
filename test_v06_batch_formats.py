from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from PIL import Image

from image_ops import ExportOptions, open_image, save_image
from light_image_editor.batch import (
    BatchManifest,
    BatchPreset,
    BatchRecipe,
    BatchRunner,
    TaskStatus,
    export_batch_log,
    render_name,
)
from light_image_editor.formats import (
    codec_capabilities,
    decode_animation,
    export_animation,
)


def oversized_png(path: Path, width: int = 100_001, height: int = 2) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b""))


class FormatCapabilityTests(unittest.TestCase):
    def test_locked_pillow_can_round_trip_avif(self) -> None:
        capabilities = codec_capabilities()
        self.assertTrue(capabilities.avif)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "sample.avif"
            save_image(
                Image.new("RGB", (17, 11), "#336699"),
                target,
                ExportOptions(output_format="AVIF", quality=80),
            )
            decoded = open_image(target)
            self.assertEqual(decoded.size, (17, 11))
            self.assertEqual(
                decoded.info["_light_image_editor_source_metadata"].original_format, "AVIF"
            )

    def test_heif_is_optional_and_not_a_base_requirement(self) -> None:
        capabilities = codec_capabilities()
        base_lock = Path("requirements.lock").read_text(encoding="utf-8")
        optional_lock = Path("requirements-heif.lock").read_text(encoding="utf-8")
        self.assertNotIn("pillow-heif", base_lock)
        self.assertIn("pillow-heif==", optional_lock)
        self.assertIsInstance(capabilities.heif, bool)

    def test_gif_apng_and_webp_animation_decode_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames = [Image.new("RGBA", (12, 8), color) for color in ("red", "green", "blue")]
            source = root / "source.gif"
            frames[0].save(
                source,
                "GIF",
                save_all=True,
                append_images=frames[1:],
                duration=[30, 40, 50],
                loop=2,
            )
            sequence = decode_animation(source)
            self.assertEqual(sequence.frame_count, 3)
            self.assertEqual(sequence.durations, (30, 40, 50))
            for output_format, extension in (("GIF", ".gif"), ("PNG", ".apng"), ("WEBP", ".webp")):
                target = root / f"export{extension}"
                export_animation(sequence, target, output_format)
                with Image.open(target) as exported:
                    self.assertEqual(exported.n_frames, 3)
                    self.assertTrue(exported.is_animated)


class BatchPresetTests(unittest.TestCase):
    def test_versioned_preset_import_export_and_template_variables(self) -> None:
        recipe = BatchRecipe(rename_template="{date}_{stem}_{index:04d}", output_format="PNG")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "preset.liebatchpreset"
            BatchPreset("教程截图", recipe).save(target)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            loaded = BatchPreset.load(target)
            self.assertEqual(loaded.recipe, recipe)
        rendered = render_name(recipe.rename_template, Path("sample 2.jpg"), 7)
        self.assertRegex(rendered, r"^\d{8}_sample 2_0007$")

    def test_unknown_preset_version_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BatchPreset.from_json({"schema_version": 99, "name": "future", "recipe": {}})


class BatchRunnerTests(unittest.TestCase):
    def test_one_thousand_mixed_tasks_are_isolated_and_memory_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = root / "input"
            output = root / "output"
            sources.mkdir()
            paths: list[Path] = []
            formats = (("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp"), ("AVIF", ".avif"))
            for index in range(1_000):
                fmt, extension = formats[index % len(formats)]
                path = sources / f"image-{index:04d}{extension}"
                Image.new("RGB", (3, 2), (index % 255, 30, 90)).save(path, fmt)
                paths.append(path)
            read_only = sources / "read-only.png"
            Image.new("RGB", (3, 2), "navy").save(read_only)
            read_only.chmod(0o444)
            damaged = sources / "damaged.png"
            damaged.write_bytes(b"not an image")
            huge = sources / "huge.png"
            oversized_png(huge)
            paths.extend((read_only, damaged, huge))
            manifest_path = root / "batch.json"
            manifest = BatchManifest.create(
                paths,
                output,
                BatchRecipe(output_format="PNG", rename_template="{stem}", strip_metadata=True),
                concurrency=4,
                memory_limit_mb=32,
            )
            completed = BatchRunner(manifest, manifest_path).run()
            counts = completed.counts()
            self.assertEqual(counts[TaskStatus.SUCCEEDED.value], 1_001)
            self.assertEqual(counts[TaskStatus.FAILED.value], 2)
            self.assertLessEqual(completed.peak_reserved_bytes, 32 * 1024 * 1024)
            self.assertTrue(manifest_path.exists())
            csv_log = export_batch_log(completed, root / "report.csv")
            self.assertIn("damaged.png", csv_log.read_text(encoding="utf-8-sig"))

    def test_cancel_recover_retry_failed_and_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "broken.png"
            source.write_bytes(b"broken")
            manifest_path = root / "batch.json"
            manifest = BatchManifest.create(
                [source], root / "output", BatchRecipe(output_format="PNG")
            )
            runner = BatchRunner(manifest, manifest_path)
            completed = runner.run()
            self.assertEqual(completed.tasks[0].status, TaskStatus.FAILED.value)
            Image.new("RGB", (4, 4), "green").save(source)
            self.assertEqual(completed.retry_failed(), 1)
            completed = BatchRunner(completed, manifest_path).run()
            self.assertEqual(completed.tasks[0].status, TaskStatus.SUCCEEDED.value)
            completed.restart_all()
            cancelled_runner = BatchRunner(completed, manifest_path)
            cancelled_runner.cancel()
            cancelled = cancelled_runner.run()
            self.assertEqual(cancelled.tasks[0].status, TaskStatus.CANCELLED.value)
            payload = cancelled.to_json()
            payload["tasks"][0]["status"] = TaskStatus.RUNNING.value
            recovered = BatchManifest.from_json(payload)
            self.assertEqual(recovered.tasks[0].status, TaskStatus.PENDING.value)

    def test_same_directory_and_name_collisions_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_dir = root / "a"
            second_dir = root / "b"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "same.png"
            second = second_dir / "same.png"
            Image.new("RGB", (4, 4), "red").save(first)
            Image.new("RGB", (4, 4), "blue").save(second)
            output = root / "out"
            manifest = BatchManifest.create(
                [first, second],
                output,
                BatchRecipe(
                    output_format="PNG", rename_template="{stem}", conflict_policy="unique"
                ),
                concurrency=2,
            )
            result = BatchRunner(manifest, root / "unique.json").run()
            outputs = {task.output for task in result.tasks}
            self.assertEqual(len(outputs), 2)
            self.assertTrue(all(Path(path).exists() for path in outputs if path))

            same_dir = BatchManifest.create(
                [first],
                first_dir,
                BatchRecipe(
                    output_format="PNG",
                    rename_template="{stem}",
                    conflict_policy="overwrite",
                ),
            )
            self.assertTrue(same_dir.same_directory_detected)
            blocked = BatchRunner(same_dir, root / "same-dir.json").run()
            self.assertEqual(blocked.tasks[0].status, TaskStatus.FAILED.value)
            self.assertIn("同一文件", blocked.tasks[0].error or "")


if __name__ == "__main__":
    unittest.main()
