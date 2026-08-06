from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from image_ops import open_image
from light_image_editor.domain.conversion_jobs import ConversionQueue
from light_image_editor.domain.conversion_runner import (
    ConversionRunner,
    ConversionTaskStatus,
)
from light_image_editor.domain.recipe_executor import execute_transform_recipe
from light_image_editor.domain.recipes import ExportProfile, TransformRecipe, TransformStep
from light_image_editor.formats import export_animation as real_export_animation


def _pattern(path: Path, *, offset: int = 0) -> Path:
    image = Image.new("RGB", (5, 4))
    pixels = image.load()
    assert pixels is not None
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = ((x * 35 + offset) % 256, y * 45, (x + y) * 25)
    image.save(path, "PNG")
    image.close()
    return path.resolve()


def _png_recipe(*, rename_template: str = "{stem}_out") -> TransformRecipe:
    return TransformRecipe(
        name="统一静态转换",
        steps=(
            TransformStep.crop((0.0, 0.0, 0.8, 1.0)),
            TransformStep.rotate(90),
            TransformStep.flip("horizontal"),
            TransformStep.resize(exact_width=8, exact_height=6),
        ),
        export_profile=ExportProfile(
            output_format="PNG",
            rename_template=rename_template,
            preserve_metadata=False,
        ),
    )


def _animated_gif(path: Path) -> Path:
    frames = [Image.new("RGB", (9, 6), color) for color in ("red", "green", "blue")]
    try:
        frames[0].save(
            path,
            "GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 120, 160],
            loop=2,
        )
    finally:
        for frame in frames:
            frame.close()
    return path.resolve()


def _webp_durations(path: Path) -> list[int]:
    payload = path.read_bytes()
    durations: list[int] = []
    offset = 12
    while offset + 8 <= len(payload):
        chunk_type = payload[offset : offset + 4]
        chunk_size = int.from_bytes(payload[offset + 4 : offset + 8], "little")
        start = offset + 8
        if chunk_type == b"ANMF":
            durations.append(int.from_bytes(payload[start + 12 : start + 15], "little"))
        offset = start + chunk_size + (chunk_size % 2)
    return durations


def _partial_alpha_apng(path: Path) -> Path:
    frames = [
        Image.new("RGBA", (8, 5), (255, 0, 0, 128)),
        Image.new("RGBA", (8, 5), (0, 0, 255, 96)),
    ]
    try:
        frames[0].save(
            path,
            "PNG",
            save_all=True,
            append_images=frames[1:],
            duration=[90, 130],
            loop=1,
        )
    finally:
        for frame in frames:
            frame.close()
    return path.resolve()


class ConversionRunnerTests(unittest.TestCase):
    def test_single_and_batch_use_identical_recipe_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _pattern(root / "source.png")
            other = _pattern(root / "other.png", offset=17)
            recipe = _png_recipe()

            single_queue = ConversionQueue()
            single_queue.add_file(source)
            single = ConversionRunner(single_queue, recipe, root / "single").run()

            batch_queue = ConversionQueue()
            batch_queue.add_files((source, other))
            batch = ConversionRunner(batch_queue, recipe, root / "batch", max_workers=2).run()

            single_output = single.successful_outputs[0]
            batch_source = next(
                task.output
                for task in batch.tasks
                if task.input.path == source and task.output is not None
            )
            single_image = open_image(single_output)
            batch_image = open_image(batch_source)
            try:
                self.assertEqual(single_image.size, (8, 6))
                self.assertEqual(batch_image.size, single_image.size)
                self.assertEqual(batch_image.mode, single_image.mode)
                self.assertEqual(batch_image.tobytes(), single_image.tobytes())
            finally:
                single_image.close()
                batch_image.close()
            self.assertEqual(single.counts[ConversionTaskStatus.SUCCEEDED], 1)
            self.assertEqual(batch.counts[ConversionTaskStatus.SUCCEEDED], 2)

    def test_corrupt_input_does_not_block_later_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            damaged = root / "00-damaged.png"
            damaged.write_bytes(b"not an image")
            valid = _pattern(root / "01-valid.png")
            queue = ConversionQueue()
            queue.add_files((damaged, valid))

            result = ConversionRunner(queue, _png_recipe(), root / "output", max_workers=1).run()

            self.assertEqual(result.counts[ConversionTaskStatus.FAILED], 1)
            self.assertEqual(result.counts[ConversionTaskStatus.SUCCEEDED], 1)
            failed = next(
                task for task in result.tasks if task.status is ConversionTaskStatus.FAILED
            )
            succeeded = next(
                task for task in result.tasks if task.status is ConversionTaskStatus.SUCCEEDED
            )
            self.assertIn("ImageLoadFailure", failed.error or "")
            self.assertEqual(succeeded.input.path, valid)
            self.assertTrue(succeeded.planned_output.exists())

    def test_cancel_stops_dispatch_and_bounds_in_flight_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [_pattern(root / f"image-{index}.png", offset=index) for index in range(8)]
            queue = ConversionQueue()
            queue.add_files(sources)
            runner = ConversionRunner(queue, _png_recipe(), root / "output", max_workers=2)
            both_started = threading.Event()
            release = threading.Event()
            started: list[int] = []
            started_lock = threading.Lock()

            def blocked_execute(image: Image.Image, recipe: TransformRecipe) -> Image.Image:
                with started_lock:
                    started.append(len(started))
                    if len(started) == 2:
                        both_started.set()
                if not release.wait(10):
                    raise TimeoutError("test worker was not released")
                return execute_transform_recipe(image, recipe)

            results: list[object] = []

            def run() -> None:
                results.append(runner.run())

            with patch(
                "light_image_editor.domain.conversion_runner.execute_transform_recipe",
                side_effect=blocked_execute,
            ):
                thread = threading.Thread(target=run)
                thread.start()
                self.assertTrue(both_started.wait(10))
                runner.cancel()
                release.set()
                thread.join(10)

            self.assertFalse(thread.is_alive())
            self.assertEqual(len(results), 1)
            self.assertEqual(len(started), 2)
            self.assertLessEqual(runner.peak_in_flight, 2)
            self.assertTrue(
                all(task.status is ConversionTaskStatus.CANCELLED for task in runner.tasks)
            )
            self.assertFalse((root / "output").exists())

    def test_written_output_must_pass_redecode_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _pattern(root / "source.png")
            output_directory = (root / "output").resolve()
            queue = ConversionQueue()
            queue.add_file(source)
            runner = ConversionRunner(queue, _png_recipe(), output_directory)
            real_open = open_image

            def fail_output_decode(path: str | Path) -> Image.Image:
                candidate = Path(path).resolve()
                if candidate.parent == output_directory:
                    raise OSError("simulated output decode failure")
                return real_open(candidate)

            with patch(
                "light_image_editor.domain.conversion_runner.open_image",
                side_effect=fail_output_decode,
            ):
                result = runner.run()

            task = result.tasks[0]
            self.assertEqual(task.status, ConversionTaskStatus.FAILED)
            self.assertIn("ConversionOutputValidationError", task.error or "")
            self.assertTrue(task.planned_output.exists())
            self.assertIsNone(task.output)

    def test_planned_unique_output_does_not_change_after_run_starts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _pattern(root / "photo.png")
            output = root / "output"
            output.mkdir()
            (output / "photo.png").write_bytes(b"occupied before planning")
            queue = ConversionQueue()
            queue.add_file(source)
            recipe = TransformRecipe(
                name="稳定路径",
                export_profile=ExportProfile(
                    output_format="PNG",
                    rename_template="{stem}",
                    conflict_policy="unique",
                ),
            )
            runner = ConversionRunner(queue, recipe, output)
            planned = runner.tasks[0].planned_output
            self.assertEqual(planned.name, "photo_1.png")
            planned.write_bytes(b"occupied after planning")

            result = runner.run()

            self.assertEqual(result.tasks[0].status, ConversionTaskStatus.FAILED)
            self.assertEqual(result.tasks[0].planned_output, planned)
            self.assertIn("FileExistsError", result.tasks[0].error or "")
            self.assertFalse((output / "photo_2.png").exists())

    def test_animation_can_be_explicitly_flattened_to_first_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _animated_gif(root / "animated.gif")
            queue = ConversionQueue()
            queue.add_file(source)
            recipe = TransformRecipe(
                steps=(TransformStep.resize(exact_width=6, exact_height=4),),
                export_profile=ExportProfile(
                    output_format="PNG",
                    preserve_animation=False,
                    rename_template="{stem}_flat",
                ),
            )

            result = ConversionRunner(queue, recipe, root / "output").run()

            task = result.tasks[0]
            self.assertEqual(task.status, ConversionTaskStatus.SUCCEEDED)
            self.assertEqual(task.output, task.planned_output)
            with Image.open(task.planned_output) as decoded:
                self.assertEqual(int(getattr(decoded, "n_frames", 1)), 1)
                self.assertEqual(decoded.size, (6, 4))
                red, green, blue = decoded.convert("RGB").getpixel((3, 2))
                self.assertGreater(red, 200)
                self.assertLess(green, 40)
                self.assertLess(blue, 40)

    def test_animation_is_transformed_frame_by_frame_for_gif_apng_and_webp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _animated_gif(root / "animated.gif")

            for output_format, extension in (
                ("GIF", ".gif"),
                ("PNG", ".png"),
                ("WEBP", ".webp"),
            ):
                with self.subTest(output_format=output_format):
                    queue = ConversionQueue()
                    queue.add_file(source)
                    recipe = TransformRecipe(
                        steps=(
                            TransformStep.rotate(90),
                            TransformStep.resize(exact_width=7, exact_height=5),
                        ),
                        export_profile=ExportProfile(
                            output_format=output_format,
                            quality=90,
                            webp_lossless=output_format == "WEBP",
                            png_palette_colors=16 if output_format == "PNG" else None,
                            preserve_animation=True,
                            rename_template="{stem}_animated",
                        ),
                    )

                    result = ConversionRunner(
                        queue,
                        recipe,
                        root / output_format.casefold(),
                    ).run()

                    task = result.tasks[0]
                    self.assertEqual(task.status, ConversionTaskStatus.SUCCEEDED, task.error)
                    self.assertEqual(task.planned_output.suffix, extension)
                    with Image.open(task.planned_output) as decoded:
                        self.assertTrue(bool(getattr(decoded, "is_animated", False)))
                        self.assertEqual(int(getattr(decoded, "n_frames", 1)), 3)
                        self.assertEqual(decoded.size, (7, 5))
                        self.assertEqual(decoded.info.get("loop"), 2)
                        durations: list[int] = []
                        colors: list[tuple[int, int, int]] = []
                        for index in range(3):
                            decoded.seek(index)
                            durations.append(round(float(decoded.info.get("duration", 0))))
                            colors.append(decoded.convert("RGB").getpixel((3, 2)))
                        if output_format == "WEBP":
                            durations = _webp_durations(task.planned_output)
                            self.assertEqual(
                                colors,
                                [(255, 0, 0), (0, 128, 0), (0, 0, 255)],
                            )
                        self.assertEqual(durations, [80, 120, 160])
                        self.assertEqual(len(set(colors)), 3)

    def test_advanced_format_profile_reaches_static_encoder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _pattern(root / "source.png")
            profiles = (
                ExportProfile(output_format="WEBP", webp_lossless=True),
                ExportProfile(output_format="PNG", png_palette_colors=16),
                ExportProfile(output_format="TIFF", tiff_compression="tiff_lzw"),
            )

            for profile in profiles:
                with self.subTest(output_format=profile.output_format):
                    queue = ConversionQueue()
                    queue.add_file(source)
                    task = (
                        ConversionRunner(
                            queue,
                            TransformRecipe(export_profile=profile),
                            root / profile.output_format.casefold(),
                        )
                        .run()
                        .tasks[0]
                    )

                    self.assertEqual(task.status, ConversionTaskStatus.SUCCEEDED, task.error)
                    with Image.open(task.planned_output) as decoded:
                        if profile.output_format == "WEBP":
                            with Image.open(source) as original:
                                self.assertEqual(
                                    decoded.convert("RGB").tobytes(),
                                    original.convert("RGB").tobytes(),
                                )
                        elif profile.output_format == "PNG":
                            self.assertEqual(decoded.mode, "P")
                            self.assertLessEqual(len(decoded.getcolors(maxcolors=256) or ()), 16)
                        else:
                            self.assertEqual(decoded.info.get("compression"), "tiff_lzw")

    def test_preserved_animation_rejects_non_animation_target_per_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _animated_gif(root / "animated.gif")
            queue = ConversionQueue()
            queue.add_file(source)
            recipe = TransformRecipe(
                export_profile=ExportProfile(
                    output_format="JPEG",
                    preserve_animation=True,
                )
            )

            result = ConversionRunner(queue, recipe, root / "output").run()

            task = result.tasks[0]
            self.assertEqual(task.status, ConversionTaskStatus.FAILED)
            self.assertIn("UnsupportedAnimationError", task.error or "")
            self.assertIn("GIF、PNG（APNG）或 WEBP", task.error or "")
            self.assertFalse(task.planned_output.exists())

    def test_partial_alpha_animation_is_preserved_or_requires_explicit_gif_background(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _partial_alpha_apng(root / "partial.apng")

            for output_format in ("PNG", "WEBP"):
                with self.subTest(output_format=output_format):
                    queue = ConversionQueue()
                    queue.add_file(source)
                    recipe = TransformRecipe(
                        export_profile=ExportProfile(
                            output_format=output_format,
                            preserve_animation=True,
                        )
                    )
                    task = (
                        ConversionRunner(
                            queue,
                            recipe,
                            root / output_format.casefold(),
                        )
                        .run()
                        .tasks[0]
                    )
                    self.assertEqual(task.status, ConversionTaskStatus.SUCCEEDED, task.error)
                    with Image.open(task.planned_output) as decoded:
                        self.assertEqual(int(getattr(decoded, "n_frames", 1)), 2)
                        alphas: list[int] = []
                        for index in range(2):
                            decoded.seek(index)
                            alphas.append(decoded.convert("RGBA").getpixel((3, 2))[3])
                        self.assertEqual(alphas, [128, 96])

            queue = ConversionQueue()
            queue.add_file(source)
            gif_without_background = TransformRecipe(
                export_profile=ExportProfile(
                    output_format="GIF",
                    preserve_animation=True,
                )
            )
            failed = (
                ConversionRunner(
                    queue,
                    gif_without_background,
                    root / "gif-failed",
                )
                .run()
                .tasks[0]
            )
            self.assertEqual(failed.status, ConversionTaskStatus.FAILED)
            self.assertIn("TransparencyPolicyError", failed.error or "")
            self.assertIn("GIF 只能保留二值透明", failed.error or "")
            self.assertFalse(failed.planned_output.exists())

            queue = ConversionQueue()
            queue.add_file(source)
            gif_with_background = TransformRecipe(
                export_profile=ExportProfile(
                    output_format="GIF",
                    preserve_animation=True,
                    transparency_background="#112233",
                )
            )
            succeeded = (
                ConversionRunner(
                    queue,
                    gif_with_background,
                    root / "gif-background",
                )
                .run()
                .tasks[0]
            )
            self.assertEqual(succeeded.status, ConversionTaskStatus.SUCCEEDED, succeeded.error)
            with Image.open(succeeded.planned_output) as decoded:
                self.assertEqual(int(getattr(decoded, "n_frames", 1)), 2)
                for index in range(2):
                    decoded.seek(index)
                    self.assertEqual(decoded.convert("RGBA").getpixel((3, 2))[3], 255)

    def test_transparent_static_inputs_require_and_apply_recipe_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "transparent.png"
            image = Image.new("RGBA", (64, 64), (255, 0, 0, 0))
            for y in range(28, 36):
                for x in range(28, 36):
                    image.putpixel((x, y), (255, 0, 0, 255))
            image.save(source, "PNG")
            image.close()

            for output_format in ("JPEG", "BMP"):
                with self.subTest(output_format=output_format, policy="missing"):
                    queue = ConversionQueue()
                    queue.add_file(source)
                    recipe = TransformRecipe(
                        export_profile=ExportProfile(
                            output_format=output_format,
                            quality=100,
                            rename_template="{stem}_missing",
                        )
                    )
                    task = ConversionRunner(queue, recipe, root / "missing").run().tasks[0]
                    self.assertEqual(task.status, ConversionTaskStatus.FAILED)
                    self.assertIn("TransparencyPolicyError", task.error or "")
                    self.assertIn("transparency_background", task.error or "")
                    self.assertFalse(task.planned_output.exists())

                with self.subTest(output_format=output_format, policy="explicit"):
                    queue = ConversionQueue()
                    queue.add_file(source)
                    recipe = TransformRecipe(
                        export_profile=ExportProfile(
                            output_format=output_format,
                            quality=100,
                            transparency_background="#2468ac",
                            rename_template="{stem}_background",
                        )
                    )
                    task = ConversionRunner(queue, recipe, root / "explicit").run().tasks[0]
                    self.assertEqual(task.status, ConversionTaskStatus.SUCCEEDED, task.error)
                    with Image.open(task.planned_output) as decoded:
                        pixel = decoded.convert("RGB").getpixel((2, 2))
                    for actual, expected in zip(pixel, (0x24, 0x68, 0xAC), strict=True):
                        self.assertLessEqual(abs(actual - expected), 6)

            queue = ConversionQueue()
            queue.add_file(source)
            png_recipe = TransformRecipe(
                export_profile=ExportProfile(
                    output_format="PNG",
                    transparency_background=None,
                )
            )
            png_task = ConversionRunner(queue, png_recipe, root / "alpha").run().tasks[0]
            self.assertEqual(png_task.status, ConversionTaskStatus.SUCCEEDED, png_task.error)
            with Image.open(png_task.planned_output) as decoded:
                self.assertEqual(decoded.convert("RGBA").getpixel((2, 2))[3], 0)

    def test_animation_cancel_during_encode_never_replaces_planned_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _animated_gif(root / "animated.gif")
            output = root / "output"
            output.mkdir()
            target = output / "animated.gif"
            target.write_bytes(b"existing-target")
            queue = ConversionQueue()
            queue.add_file(source)
            recipe = TransformRecipe(
                export_profile=ExportProfile(
                    output_format="GIF",
                    preserve_animation=True,
                    rename_template="{stem}",
                    conflict_policy="overwrite",
                    backup_on_overwrite=True,
                )
            )
            runner = ConversionRunner(queue, recipe, output)
            started = threading.Event()
            release = threading.Event()

            def blocked_export(*args: object, **kwargs: object) -> Path:
                started.set()
                if not release.wait(10):
                    raise TimeoutError("test animation encoder was not released")
                return real_export_animation(*args, **kwargs)  # type: ignore[arg-type]

            thread = threading.Thread(target=runner.run)
            with patch(
                "light_image_editor.domain.conversion_runner.export_animation",
                side_effect=blocked_export,
            ):
                thread.start()
                self.assertTrue(started.wait(10))
                runner.cancel()
                release.set()
                thread.join(10)

            self.assertFalse(thread.is_alive())
            self.assertEqual(runner.tasks[0].status, ConversionTaskStatus.CANCELLED)
            self.assertEqual(runner.tasks[0].planned_output, target.resolve())
            self.assertEqual(target.read_bytes(), b"existing-target")
            self.assertFalse((output / "animated.gif.bak").exists())
            self.assertEqual(list(output.glob(".animated.gif.*")), [])

    def test_overwrite_delegates_to_atomic_backup_safety(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _pattern(root / "source.png")
            output = root / "output"
            output.mkdir()
            existing = output / "source_out.png"
            previous = Image.new("RGB", (3, 3), "navy")
            previous.save(existing, "PNG")
            previous.close()
            previous_bytes = existing.read_bytes()
            queue = ConversionQueue()
            queue.add_file(source)
            recipe = TransformRecipe(
                name="安全覆盖",
                export_profile=ExportProfile(
                    output_format="PNG",
                    rename_template="{stem}_out",
                    conflict_policy="overwrite",
                    backup_on_overwrite=True,
                ),
            )

            result = ConversionRunner(queue, recipe, output).run()

            task = result.tasks[0]
            self.assertEqual(task.status, ConversionTaskStatus.SUCCEEDED)
            self.assertEqual(task.output, existing.resolve())
            self.assertEqual((output / "source_out.png.bak").read_bytes(), previous_bytes)
            decoded = open_image(existing)
            try:
                self.assertEqual(decoded.size, (5, 4))
            finally:
                decoded.close()


if __name__ == "__main__":
    unittest.main()
