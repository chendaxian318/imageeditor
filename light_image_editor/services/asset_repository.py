"""Persistence boundary for immutable image assets and revision trees."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from light_image_editor.domain.assets import AssetRecord, AssetRevision, Provenance
from light_image_editor.domain.revision_tree import RevisionTree


class AssetRepositoryError(RuntimeError):
    """Base class for repository, content, and revision integrity failures."""


class AssetNotFoundError(AssetRepositoryError):
    pass


class AssetRevisionNotFoundError(AssetRepositoryError):
    pass


class AssetParentError(AssetRepositoryError):
    pass


class AssetContentValidationError(AssetRepositoryError):
    pass


class AssetContentTooLargeError(AssetContentValidationError):
    pass


class AssetMediaTypeMismatchError(AssetContentValidationError):
    pass


class AssetContentMissingError(AssetRepositoryError):
    pass


class AssetContentChangedError(AssetRepositoryError):
    pass


class AssetRepositoryCorruptionError(AssetRepositoryError):
    pass


@runtime_checkable
class AssetRepository(Protocol):
    """Application-owned source of truth for assets, revisions, and bytes."""

    @property
    def database_path(self) -> Path: ...

    @property
    def blob_root(self) -> Path: ...

    def import_external(
        self,
        path: str | Path,
        *,
        provenance: Provenance | None = None,
        asset_id: str | None = None,
        parent_revision_id: str | None = None,
        asset_name: str | None = None,
        expected_media_type: str | None = None,
    ) -> AssetRevision: ...

    def find_external(self, path: str | Path) -> AssetRevision | None: ...

    def ensure_external(
        self,
        path: str | Path,
        *,
        provenance: Provenance | None = None,
        asset_id: str | None = None,
        parent_revision_id: str | None = None,
        asset_name: str | None = None,
        expected_media_type: str | None = None,
    ) -> AssetRevision: ...

    def import_managed(
        self,
        staged_path: str | Path,
        *,
        provenance: Provenance,
        asset_id: str | None = None,
        parent_revision_id: str | None = None,
        asset_name: str | None = None,
        expected_media_type: str | None = None,
    ) -> AssetRevision: ...

    def get_asset(self, asset_id: str) -> AssetRecord: ...

    def get_revision(self, revision_id: str) -> AssetRevision: ...

    def children_of(self, revision_id: str) -> tuple[AssetRevision, ...]: ...

    def revision_tree(self, asset_id: str) -> RevisionTree: ...

    def resolve_content(self, revision_id: str, *, verify: bool = True) -> Path: ...
