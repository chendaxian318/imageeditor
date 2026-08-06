from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from light_image_editor.domain.conversion_jobs import ConversionInput, ConversionQueue
from light_image_editor.shell.mode_router import AssetHandoff, RevisionHandoff


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    return path.resolve()


class ConversionQueueTests(unittest.TestCase):
    def test_add_folder_naturally_sorts_recursively_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image10 = _touch(root / "图10.png")
            image2 = _touch(root / "图2.png")
            image1 = _touch(root / "图1.png")
            nested = _touch(root / "子目录" / "图3.jpg")
            _touch(root / "忽略.txt")
            queue = ConversionQueue()

            added = queue.add_folder(root)
            duplicate = queue.add_file(root / "." / "图2.png")

            self.assertEqual(
                [item.path.name for item in queue],
                ["图1.png", "图2.png", "图3.jpg", "图10.png"],
            )
            self.assertEqual({item.path for item in added}, {image1, image2, image10, nested})
            self.assertIsNone(duplicate)

    def test_asset_revision_metadata_survives_exclusion_and_enable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _touch(Path(directory) / "来源.png")
            asset = AssetHandoff(
                "asset-中文",
                uri=path.as_uri(),
                display_name="来源.png",
                metadata={"相机": "样机"},
            )
            revision = RevisionHandoff(
                "revision-2",
                asset.id,
                number=2,
                metadata={"操作": "旋转"},
            )
            original = ConversionInput(
                path,
                asset=asset,
                revision=revision,
                metadata={"入口": "看图模式"},
            )
            queue = ConversionQueue((original,))

            excluded = queue.exclude(path)
            enabled = queue.enable(path)

            self.assertFalse(excluded.enabled)
            self.assertTrue(enabled.enabled)
            self.assertIs(enabled.asset, asset)
            self.assertIs(enabled.revision, revision)
            self.assertEqual(enabled.asset_id, "asset-中文")
            self.assertEqual(enabled.revision_id, "revision-2")
            self.assertEqual(enabled.metadata, {"入口": "看图模式"})
            self.assertEqual(asset.metadata, {"相机": "样机"})
            self.assertEqual(revision.metadata, {"操作": "旋转"})

    def test_late_asset_identity_enriches_existing_path_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _touch(Path(directory) / "稍后登记.png")
            queue = ConversionQueue()
            original = queue.add_file(path, metadata={"入口": "立即转图"})
            assert original is not None
            queue.exclude(path)
            asset = AssetHandoff("asset-late", uri=path.as_uri())
            revision = RevisionHandoff("revision-late", asset.id, number=1)

            enriched = queue.attach_identity(path, asset=asset, revision=revision)
            unchanged = queue.attach_identity(path, asset=asset, revision=revision)

            self.assertEqual(len(queue), 1)
            self.assertFalse(enriched.enabled)
            self.assertEqual(enriched.metadata, {"入口": "立即转图"})
            self.assertIs(enriched.asset, asset)
            self.assertIs(enriched.revision, revision)
            self.assertIs(unchanged, enriched)

    def test_one_thousand_inputs_enable_remove_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [_touch(root / f"image-{index}.png") for index in range(1000)]
            queue = ConversionQueue()

            added = queue.add_files(reversed(paths))
            queue.exclude(paths[500])
            removed = queue.remove(paths[250])

            self.assertEqual(len(added), 1000)
            self.assertEqual(len(queue), 999)
            self.assertEqual(len(queue.enabled_items), 998)
            self.assertEqual(queue.excluded_items[0].path, paths[500])
            self.assertIsNotNone(removed)
            self.assertEqual(queue[0].path.name, "image-0.png")
            self.assertEqual(queue[1].path.name, "image-1.png")
            self.assertEqual(queue[-1].path.name, "image-999.png")
            self.assertEqual(len(queue.clear()), 999)
            self.assertEqual(len(queue), 0)

    def test_invalid_inputs_and_cross_asset_revision_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsupported = _touch(root / "说明.txt")
            asset = AssetHandoff("asset-a")
            revision = RevisionHandoff("revision-b", "asset-b")

            with self.assertRaises(ValueError):
                ConversionQueue().add_file(unsupported)
            with self.assertRaises(ValueError):
                ConversionInput(root / "image.png", asset=asset, revision=revision)
            with self.assertRaises(FileNotFoundError):
                ConversionQueue().add_file(root / "missing.png")


if __name__ == "__main__":
    unittest.main()
