from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from light_image_editor.domain import (
    AssetRecord,
    AssetRevision,
    AssetStorageKind,
    Provenance,
    RevisionTree,
)
from light_image_editor.services import (
    AssetContentChangedError,
    AssetContentTooLargeError,
    AssetContentValidationError,
    AssetMediaTypeMismatchError,
    AssetParentError,
    AssetRepository,
    SQLiteAssetRepository,
)


def _save_image(path: Path, color: str, *, image_format: str = "PNG") -> Path:
    Image.new("RGB", (24, 16), color).save(path, image_format)
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _repository(root: Path, *, max_content_bytes: int = 1024 * 1024) -> SQLiteAssetRepository:
    return SQLiteAssetRepository(
        root / "assets.db",
        root / "blobs",
        max_content_bytes=max_content_bytes,
    )


def _row_counts(database: Path) -> tuple[int, int]:
    with closing(sqlite3.connect(database)) as connection:
        assets = int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        revisions = int(connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0])
    return assets, revisions


def _blob_files(root: Path) -> tuple[Path, ...]:
    return tuple(path for path in root.rglob("*") if path.is_file())


class RevisionTreeTests(unittest.TestCase):
    def test_parent_field_is_backward_compatible_and_tree_is_queryable(self) -> None:
        root = AssetRevision.from_content_uri(
            asset_id="asset-one",
            sequence=1,
            content_uri="file:///root.png",
            media_type="image/png",
            provenance=Provenance.imported(),
            revision_id="revision-root",
        )
        child = AssetRevision.from_content_uri(
            asset_id="asset-one",
            sequence=2,
            content_uri="file:///child.png",
            media_type="image/png",
            provenance=Provenance(origin="edit", actor="user"),
            revision_id="revision-child",
            parent_revision_id=root.revision_id,
            storage_kind=AssetStorageKind.MANAGED,
        )
        record = AssetRecord(
            asset_id="asset-one",
            name="picture.png",
            revisions=(root, child),
        )

        tree = RevisionTree.from_record(record)

        self.assertIsNone(root.parent_revision_id)
        self.assertEqual(root.storage_kind, AssetStorageKind.EXTERNAL)
        self.assertEqual(tree.roots, (root,))
        self.assertEqual(tree.children_of(root.revision_id), (child,))
        self.assertEqual(tree.parent_of(child.revision_id), root)
        self.assertEqual(tree.ancestors_of(child.revision_id), (root,))
        self.assertEqual(tree.leaves, (child,))

    def test_tree_rejects_cycles(self) -> None:
        first = AssetRevision.from_content_uri(
            asset_id="asset-cycle",
            sequence=1,
            content_uri="file:///one.png",
            media_type="image/png",
            provenance=Provenance.imported(),
            revision_id="one",
            parent_revision_id="two",
        )
        second = AssetRevision.from_content_uri(
            asset_id="asset-cycle",
            sequence=2,
            content_uri="file:///two.png",
            media_type="image/png",
            provenance=Provenance.imported(),
            revision_id="two",
            parent_revision_id="one",
        )

        with self.assertRaisesRegex(ValueError, "cycle"):
            RevisionTree(asset_id="asset-cycle", revisions=(first, second))


class SQLiteAssetRepositoryTests(unittest.TestCase):
    def test_external_revision_uses_real_hash_and_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _save_image(root / "source.png", "#345678")
            repository = _repository(root)
            self.assertIsInstance(repository, AssetRepository)

            revision = repository.import_external(source)

            self.assertEqual(revision.sha256, _sha256(source))
            self.assertEqual(revision.storage_kind, AssetStorageKind.EXTERNAL)
            self.assertEqual(revision.media_type, "image/png")
            self.assertEqual((revision.width, revision.height), (24, 16))
            self.assertEqual(repository.resolve_content(revision.revision_id), source.resolve())

            reopened = _repository(root)
            restored = reopened.get_revision(revision.revision_id)
            record = reopened.get_asset(revision.asset_id)

            self.assertEqual(restored, revision)
            self.assertEqual(record.current_revision, revision)
            self.assertEqual(reopened.resolve_content(revision.revision_id), source.resolve())
            self.assertEqual(reopened.revision_tree(revision.asset_id).roots, (revision,))

            with closing(sqlite3.connect(repository.database_path)) as connection:
                foreign_keys = connection.execute("PRAGMA foreign_key_list(revisions)").fetchall()
            self.assertEqual(
                {str(row[2]) for row in foreign_keys},
                {"assets", "revisions"},
            )

    def test_ensure_external_reuses_unchanged_identity_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _save_image(root / "stable.png", "#345678")
            first_repository = _repository(root)

            first = first_repository.ensure_external(source)
            reopened = _repository(root)
            second = reopened.ensure_external(source)

            self.assertEqual(second, first)
            self.assertEqual(_row_counts(reopened.database_path), (1, 1))
            self.assertEqual(reopened.find_external(source), first)

    def test_ensure_external_appends_revision_when_same_path_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _save_image(root / "changing.png", "#112233")
            repository = _repository(root)
            first = repository.ensure_external(source)
            _save_image(source, "#ddeeff")

            second = repository.ensure_external(source)

            self.assertEqual(second.asset_id, first.asset_id)
            self.assertEqual(second.sequence, 2)
            self.assertEqual(second.parent_revision_id, first.revision_id)
            self.assertNotEqual(second.sha256, first.sha256)
            self.assertEqual(repository.find_external(source), second)
            self.assertEqual(_row_counts(repository.database_path), (1, 2))

    def test_ensure_external_uses_parent_hint_for_a_moved_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _save_image(root / "before.png", "#6a4f82")
            target = root / "after.png"
            repository = _repository(root)
            first = repository.ensure_external(source)
            source.rename(target)

            second = repository.ensure_external(
                target,
                asset_id=first.asset_id,
                parent_revision_id=first.revision_id,
                provenance=Provenance(
                    origin="relocate",
                    actor="user",
                    source_revision_ids=(first.revision_id,),
                ),
            )

            self.assertEqual(second.asset_id, first.asset_id)
            self.assertEqual(second.parent_revision_id, first.revision_id)
            self.assertEqual(second.sequence, 2)
            self.assertEqual(repository.resolve_content(second.revision_id), target.resolve())

    def test_managed_revision_is_content_addressed_and_builds_parent_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _save_image(root / "source.png", "#71325b")
            staged = _save_image(root / "staged.png", "#1e8d74")
            repository = _repository(root)
            first = repository.import_external(source)
            provenance = Provenance(
                origin="content_edit",
                actor="offline.mock",
                source_revision_ids=(first.revision_id,),
                request_id="request-one",
                provider_id="offline.mock",
                model="mock-image-v1",
            )

            second = repository.import_managed(
                staged,
                provenance=provenance,
                asset_id=first.asset_id,
                parent_revision_id=first.revision_id,
                expected_media_type="image/png",
            )

            managed_path = repository.resolve_content(second.revision_id)
            self.assertTrue(staged.exists())
            self.assertEqual(second.storage_kind, AssetStorageKind.MANAGED)
            self.assertEqual(second.parent_revision_id, first.revision_id)
            self.assertTrue(managed_path.is_relative_to(repository.blob_root))
            self.assertEqual(managed_path.stem, second.sha256)
            self.assertEqual(managed_path.suffix, ".png")
            self.assertEqual(managed_path.read_bytes(), staged.read_bytes())
            self.assertEqual(repository.children_of(first.revision_id), (second,))
            tree = repository.revision_tree(first.asset_id)
            self.assertEqual(tree.ancestors_of(second.revision_id), (first,))

            reopened = _repository(root)
            self.assertEqual(reopened.get_revision(second.revision_id), second)
            self.assertEqual(reopened.resolve_content(second.revision_id), managed_path)

            duplicate = reopened.import_managed(
                staged,
                provenance=Provenance(origin="generation", actor="offline.mock"),
                asset_name="duplicate.png",
            )
            self.assertEqual(reopened.resolve_content(duplicate.revision_id), managed_path)
            self.assertEqual(len(_blob_files(repository.blob_root)), 1)

    def test_cross_asset_parent_is_rejected_transactionally_and_blob_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = _repository(root)
            first = repository.import_external(_save_image(root / "one.png", "#112233"))
            second = repository.import_external(_save_image(root / "two.png", "#445566"))
            staged = _save_image(root / "staged.png", "#778899")

            with self.assertRaisesRegex(AssetParentError, "different asset"):
                repository.import_managed(
                    staged,
                    provenance=Provenance(origin="content_edit", actor="offline.mock"),
                    asset_id=first.asset_id,
                    parent_revision_id=second.revision_id,
                )

            self.assertEqual(_row_counts(repository.database_path), (2, 2))
            self.assertEqual(_blob_files(repository.blob_root), ())

    def test_corrupt_managed_image_leaves_no_rows_or_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = _repository(root)
            staged = root / "corrupt.png"
            staged.write_bytes(b"not an image")

            with self.assertRaises(AssetContentValidationError):
                repository.import_managed(
                    staged,
                    provenance=Provenance(origin="generation", actor="offline.mock"),
                )

            self.assertEqual(_row_counts(repository.database_path), (0, 0))
            self.assertEqual(_blob_files(repository.blob_root), ())

    def test_byte_limit_rejects_staged_image_without_residue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = _save_image(root / "large.png", "#abcdef")
            repository = _repository(root, max_content_bytes=32)
            self.assertGreater(staged.stat().st_size, repository.max_content_bytes)

            with self.assertRaises(AssetContentTooLargeError):
                repository.import_managed(
                    staged,
                    provenance=Provenance(origin="generation", actor="offline.mock"),
                )

            self.assertEqual(_row_counts(repository.database_path), (0, 0))
            self.assertEqual(_blob_files(repository.blob_root), ())

    def test_decode_pixel_limit_rejects_staged_image_without_residue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = _save_image(root / "too-many-pixels.png", "#7b2d91")
            repository = _repository(root)

            with (
                patch("light_image_editor.image_loader.MAX_DECODE_PIXELS", 100),
                self.assertRaises(AssetContentTooLargeError),
            ):
                repository.import_managed(
                    staged,
                    provenance=Provenance(origin="generation", actor="offline.mock"),
                )

            self.assertEqual(_row_counts(repository.database_path), (0, 0))
            self.assertEqual(_blob_files(repository.blob_root), ())

    def test_wrong_declared_mime_leaves_no_rows_or_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = _save_image(root / "mismatch.bin", "#4a6392")
            repository = _repository(root)

            with self.assertRaises(AssetMediaTypeMismatchError):
                repository.import_managed(
                    staged,
                    provenance=Provenance(origin="generation", actor="offline.mock"),
                    expected_media_type="image/jpeg",
                )

            self.assertEqual(_row_counts(repository.database_path), (0, 0))
            self.assertEqual(_blob_files(repository.blob_root), ())

    def test_resolve_detects_external_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _save_image(root / "source.png", "#111111")
            repository = _repository(root)
            revision = repository.import_external(source)
            _save_image(source, "#eeeeee")

            with self.assertRaises(AssetContentChangedError):
                repository.resolve_content(revision.revision_id)


if __name__ == "__main__":
    unittest.main()
