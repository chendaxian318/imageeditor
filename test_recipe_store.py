from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from light_image_editor.batch import BatchPreset, BatchRecipe
from light_image_editor.domain.recipe_store import (
    RecipeFileError,
    RecipeFileTooLargeError,
    RecipeStore,
)
from light_image_editor.domain.recipes import ExportProfile, TransformRecipe, TransformStep


def _recipe(name: str) -> TransformRecipe:
    return TransformRecipe(
        name=name,
        steps=(
            TransformStep.resize(max_width=1920, max_height=1080),
            TransformStep.watermark("版权所有", position="右下"),
        ),
        export_profile=ExportProfile(
            output_format="WEBP",
            quality=88,
            rename_template="{stem}_中文版_{index:03d}",
        ),
    )


class RecipeStoreTests(unittest.TestCase):
    def test_chinese_utf8_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "社交媒体.lierecipe"
            store = RecipeStore()
            recipe = _recipe("中文社交媒体配方")

            self.assertEqual(store.save(recipe, path), path)
            raw = path.read_bytes()

            self.assertIn("中文社交媒体配方".encode(), raw)
            self.assertNotIn(b"\\u4e2d", raw)
            self.assertEqual(store.load(path), recipe)

    def test_corrupt_non_utf8_duplicate_and_nonstandard_json_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = {
                "non-utf8.lierecipe": b"\xff\xfe",
                "broken.lierecipe": b'{"schema_version": 1,',
                "duplicate.lierecipe": b'{"name":"a","name":"b"}',
                "nan.lierecipe": b'{"schema_version":NaN}',
            }
            for name, content in samples.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_bytes(content)
                    with self.assertRaises(RecipeFileError):
                        RecipeStore().load(path)

    def test_size_unknown_version_and_unknown_field_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oversized = root / "oversized.lierecipe"
            oversized.write_bytes(b" " * 129)
            with self.assertRaises(RecipeFileTooLargeError):
                RecipeStore(max_file_bytes=128).load(oversized)

            for label, change in (
                ("version", {"schema_version": 999}),
                ("field", {"unknown": True}),
            ):
                with self.subTest(label=label):
                    path = root / f"{label}.lierecipe"
                    payload = _recipe("严格验证").to_json()
                    payload.update(change)
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(RecipeFileError):
                        RecipeStore().load(path)

    def test_atomic_overwrite_and_failed_replace_preserve_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "覆盖测试.lierecipe"
            store = RecipeStore()
            first = _recipe("第一版")
            second = _recipe("第二版")
            store.save(first, path)

            store.save(second, path)
            self.assertEqual(store.load(path), second)
            previous = path.read_bytes()

            with (
                patch(
                    "light_image_editor.domain.recipe_store.os.replace",
                    side_effect=OSError("simulated replace failure"),
                ),
                self.assertRaises(OSError),
            ):
                store.save(first, path)

            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_legacy_batch_preset_migrates_through_strict_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "旧预设.liebatchpreset"
            legacy = BatchPreset(
                "旧版中文配方",
                BatchRecipe(
                    output_format="PNG",
                    max_width=1280,
                    watermark_text="样张",
                    strip_metadata=True,
                ),
            )
            path.write_text(json.dumps(legacy.to_json(), ensure_ascii=False), encoding="utf-8")

            migrated = RecipeStore().load(path)

            self.assertEqual(migrated.name, "旧版中文配方")
            self.assertEqual([step.kind for step in migrated.steps], ["resize", "watermark"])
            self.assertEqual(migrated.export_profile.output_format, "PNG")
            self.assertFalse(migrated.export_profile.preserve_metadata)
            with self.assertRaises(RecipeFileError):
                RecipeStore().load(path, allow_legacy=False)


if __name__ == "__main__":
    unittest.main()
