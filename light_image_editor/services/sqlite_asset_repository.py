"""SQLite metadata and content-addressed storage for image revisions."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from light_image_editor.document import metadata_from_image
from light_image_editor.domain.assets import (
    AssetRecord,
    AssetRevision,
    AssetStorageKind,
    Provenance,
    Scalar,
)
from light_image_editor.domain.revision_tree import RevisionTree
from light_image_editor.image_loader import ImageLoadFailure, decode_image

from .asset_repository import (
    AssetContentChangedError,
    AssetContentMissingError,
    AssetContentTooLargeError,
    AssetContentValidationError,
    AssetMediaTypeMismatchError,
    AssetNotFoundError,
    AssetParentError,
    AssetRepositoryCorruptionError,
    AssetRevisionNotFoundError,
)

ASSET_REPOSITORY_SCHEMA_VERSION = 1
DEFAULT_MAX_CONTENT_BYTES = 512 * 1024 * 1024
_COPY_BLOCK_BYTES = 1024 * 1024

_FORMAT_DETAILS: dict[str, tuple[str, str]] = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
    "BMP": ("image/bmp", ".bmp"),
    "TIFF": ("image/tiff", ".tiff"),
    "GIF": ("image/gif", ".gif"),
    "AVIF": ("image/avif", ".avif"),
    "HEIF": ("image/heif", ".heif"),
}
_MEDIA_TYPE_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
    "image/x-ms-bmp": "image/bmp",
    "image/x-tiff": "image/tiff",
}

_REVISION_COLUMNS = """
    revision_id,
    asset_id,
    sequence,
    parent_revision_id,
    storage_kind,
    content_locator,
    media_type,
    sha256,
    byte_size,
    width,
    height,
    provenance_json,
    created_at
"""


@dataclass(frozen=True, slots=True)
class _InspectedContent:
    sha256: str
    byte_size: int
    width: int
    height: int
    media_type: str
    extension: str


@dataclass(frozen=True, slots=True)
class _InstalledBlob:
    path: Path
    locator: str
    content: _InspectedContent
    newly_created: bool


class SQLiteAssetRepository:
    """Persistent repository with transactional metadata and managed image bytes."""

    def __init__(
        self,
        database_path: str | Path,
        blob_root: str | Path,
        *,
        max_content_bytes: int = DEFAULT_MAX_CONTENT_BYTES,
    ) -> None:
        if isinstance(max_content_bytes, bool) or not isinstance(max_content_bytes, int):
            raise ValueError("max_content_bytes must be a positive integer")
        if max_content_bytes < 1:
            raise ValueError("max_content_bytes must be a positive integer")
        self._database_path = _absolute_path(database_path)
        self._blob_root = _absolute_path(blob_root)
        self.max_content_bytes = max_content_bytes
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._blob_root.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def blob_root(self) -> Path:
        return self._blob_root

    def import_external(
        self,
        path: str | Path,
        *,
        provenance: Provenance | None = None,
        asset_id: str | None = None,
        parent_revision_id: str | None = None,
        asset_name: str | None = None,
        expected_media_type: str | None = None,
    ) -> AssetRevision:
        source = _existing_file(path)
        content = self._inspect_existing(source, expected_media_type=expected_media_type)
        revision_provenance = provenance or Provenance.imported(source=str(source))
        return self._commit_revision(
            storage_kind=AssetStorageKind.EXTERNAL,
            content_locator=str(source),
            content=content,
            provenance=revision_provenance,
            asset_id=asset_id,
            parent_revision_id=parent_revision_id,
            asset_name=asset_name or source.name,
        )

    def find_external(self, path: str | Path) -> AssetRevision | None:
        """Return the newest external revision registered for a canonical path."""

        source = _absolute_path(path)
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_REVISION_COLUMNS} FROM revisions "
                "WHERE storage_kind = 'external' AND content_locator = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (str(source),),
            ).fetchone()
        return None if row is None else self._revision_from_row(row)

    def ensure_external(
        self,
        path: str | Path,
        *,
        provenance: Provenance | None = None,
        asset_id: str | None = None,
        parent_revision_id: str | None = None,
        asset_name: str | None = None,
        expected_media_type: str | None = None,
    ) -> AssetRevision:
        """Reuse unchanged content or append a revision when the path changed bytes."""

        source = _existing_file(path)
        content = self._inspect_existing(source, expected_media_type=expected_media_type)
        existing = self.find_external(source)
        if existing is not None and _content_matches_revision(content, existing):
            return existing

        target_asset_id = asset_id
        target_parent_id = parent_revision_id
        if existing is not None:
            # The canonical path owns continuity.  A caller hint is used only for
            # a newly-seen path such as a successful rename or move.
            target_asset_id = existing.asset_id
            target_parent_id = existing.revision_id

        revision_provenance = provenance
        if revision_provenance is None:
            revision_provenance = (
                Provenance(
                    origin="external_change",
                    actor="filesystem",
                    source_revision_ids=(existing.revision_id,),
                    parameters={"path": str(source)},
                )
                if existing is not None
                else Provenance.imported(source=str(source))
            )
        return self._commit_revision(
            storage_kind=AssetStorageKind.EXTERNAL,
            content_locator=str(source),
            content=content,
            provenance=revision_provenance,
            asset_id=target_asset_id,
            parent_revision_id=target_parent_id,
            asset_name=asset_name or source.name,
        )

    def import_managed(
        self,
        staged_path: str | Path,
        *,
        provenance: Provenance,
        asset_id: str | None = None,
        parent_revision_id: str | None = None,
        asset_name: str | None = None,
        expected_media_type: str | None = None,
    ) -> AssetRevision:
        if not isinstance(provenance, Provenance):
            raise TypeError("provenance must be Provenance")
        source = _existing_file(staged_path)
        installed = self._install_managed_blob(
            source,
            expected_media_type=expected_media_type,
        )
        try:
            return self._commit_revision(
                storage_kind=AssetStorageKind.MANAGED,
                content_locator=installed.locator,
                content=installed.content,
                provenance=provenance,
                asset_id=asset_id,
                parent_revision_id=parent_revision_id,
                asset_name=asset_name or source.name,
            )
        except BaseException:
            if installed.newly_created and not self._locator_is_referenced(installed.locator):
                installed.path.unlink(missing_ok=True)
            raise

    def get_asset(self, asset_id: str) -> AssetRecord:
        normalized_id = _required_id(asset_id, "asset id")
        with closing(self._connect()) as connection:
            asset_row = connection.execute(
                "SELECT asset_id, name, created_at FROM assets WHERE asset_id = ?",
                (normalized_id,),
            ).fetchone()
            if asset_row is None:
                raise AssetNotFoundError(f"unknown asset: {normalized_id}")
            revision_rows = connection.execute(
                f"SELECT {_REVISION_COLUMNS} FROM revisions WHERE asset_id = ? ORDER BY sequence",
                (normalized_id,),
            ).fetchall()
        if not revision_rows:
            raise AssetRepositoryCorruptionError(f"asset has no revisions: {normalized_id}")
        return AssetRecord(
            asset_id=str(asset_row["asset_id"]),
            name=str(asset_row["name"]),
            revisions=tuple(self._revision_from_row(row) for row in revision_rows),
            created_at=_parse_datetime(asset_row["created_at"], "asset created_at"),
        )

    def get_revision(self, revision_id: str) -> AssetRevision:
        normalized_id = _required_id(revision_id, "revision id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT {_REVISION_COLUMNS} FROM revisions WHERE revision_id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise AssetRevisionNotFoundError(f"unknown revision: {normalized_id}")
        return self._revision_from_row(row)

    def children_of(self, revision_id: str) -> tuple[AssetRevision, ...]:
        normalized_id = _required_id(revision_id, "revision id")
        self.get_revision(normalized_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT {_REVISION_COLUMNS} FROM revisions "
                "WHERE parent_revision_id = ? ORDER BY sequence",
                (normalized_id,),
            ).fetchall()
        return tuple(self._revision_from_row(row) for row in rows)

    def revision_tree(self, asset_id: str) -> RevisionTree:
        return RevisionTree.from_record(self.get_asset(asset_id))

    def resolve_content(self, revision_id: str, *, verify: bool = True) -> Path:
        revision = self.get_revision(revision_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT storage_kind, content_locator FROM revisions WHERE revision_id = ?",
                (revision.revision_id,),
            ).fetchone()
        if row is None:
            raise AssetRevisionNotFoundError(f"unknown revision: {revision.revision_id}")
        path = self._path_from_locator(
            AssetStorageKind(str(row["storage_kind"])),
            str(row["content_locator"]),
        )
        if not path.exists() or not path.is_file():
            raise AssetContentMissingError(f"revision content is missing: {revision.revision_id}")
        if verify:
            try:
                size = path.stat().st_size
            except OSError as error:
                raise AssetContentMissingError(
                    f"revision content cannot be read: {revision.revision_id}"
                ) from error
            if revision.byte_size is not None and size != revision.byte_size:
                raise AssetContentChangedError(
                    f"revision content size changed: {revision.revision_id}"
                )
            digest, _size = self._hash_file(path)
            if digest != revision.sha256:
                raise AssetContentChangedError(
                    f"revision content hash changed: {revision.revision_id}"
                )
        return path

    def _initialize_database(self) -> None:
        with closing(self._connect()) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, ASSET_REPOSITORY_SCHEMA_VERSION}:
                raise AssetRepositoryCorruptionError(
                    f"unsupported asset repository schema: {version}"
                )
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE assets (
                        asset_id TEXT PRIMARY KEY NOT NULL,
                        name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE revisions (
                        revision_id TEXT PRIMARY KEY NOT NULL,
                        asset_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL CHECK(sequence >= 1),
                        parent_revision_id TEXT,
                        storage_kind TEXT NOT NULL
                            CHECK(storage_kind IN ('external', 'managed')),
                        content_locator TEXT NOT NULL
                            CHECK(length(trim(content_locator)) > 0),
                        media_type TEXT NOT NULL CHECK(length(trim(media_type)) > 0),
                        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
                        byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                        width INTEGER NOT NULL CHECK(width >= 1),
                        height INTEGER NOT NULL CHECK(height >= 1),
                        provenance_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(asset_id, sequence),
                        FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
                            ON UPDATE RESTRICT ON DELETE RESTRICT,
                        FOREIGN KEY(parent_revision_id) REFERENCES revisions(revision_id)
                            ON UPDATE RESTRICT ON DELETE RESTRICT
                    );

                    CREATE INDEX revisions_parent_idx
                        ON revisions(parent_revision_id, sequence);
                    CREATE INDEX revisions_asset_idx
                        ON revisions(asset_id, sequence);
                    PRAGMA user_version = 1;
                    """
                )
                connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            connection.close()
            raise AssetRepositoryCorruptionError("SQLite foreign keys are unavailable")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _inspect_existing(
        self,
        path: Path,
        *,
        expected_media_type: str | None,
    ) -> _InspectedContent:
        try:
            before = path.stat()
        except OSError as error:
            raise AssetContentMissingError(f"cannot inspect image: {path}") from error
        self._check_size(before.st_size)
        media_type, extension, width, height = self._decode_details(path)
        self._check_expected_media_type(media_type, expected_media_type)
        digest, byte_size = self._hash_file(path)
        try:
            after = path.stat()
        except OSError as error:
            raise AssetContentChangedError(f"image changed during import: {path}") from error
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or byte_size != after.st_size
        ):
            raise AssetContentChangedError(f"image changed during import: {path}")
        return _InspectedContent(
            sha256=digest,
            byte_size=byte_size,
            width=width,
            height=height,
            media_type=media_type,
            extension=extension,
        )

    def _install_managed_blob(
        self,
        source: Path,
        *,
        expected_media_type: str | None,
    ) -> _InstalledBlob:
        self._check_size(source.stat().st_size)
        incoming_root = self._blob_root / ".incoming"
        incoming_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=incoming_root,
            prefix="asset-",
            suffix=".incoming",
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with source.open("rb") as source_stream, os.fdopen(descriptor, "wb") as target_stream:
                descriptor = -1
                while block := source_stream.read(_COPY_BLOCK_BYTES):
                    byte_size += len(block)
                    self._check_size(byte_size)
                    digest.update(block)
                    target_stream.write(block)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            media_type, extension, width, height = self._decode_details(temporary)
            self._check_expected_media_type(media_type, expected_media_type)
            content = _InspectedContent(
                sha256=digest.hexdigest(),
                byte_size=byte_size,
                width=width,
                height=height,
                media_type=media_type,
                extension=extension,
            )
            relative = (
                Path("sha256")
                / content.sha256[:2]
                / content.sha256[2:4]
                / f"{content.sha256}{content.extension}"
            )
            destination = (self._blob_root / relative).resolve(strict=False)
            if not destination.is_relative_to(self._blob_root):
                raise AssetRepositoryCorruptionError("managed blob path escaped its root")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                existing_digest, existing_size = self._hash_file(destination)
                if existing_digest != content.sha256 or existing_size != content.byte_size:
                    raise AssetContentChangedError(
                        f"managed blob does not match its address: {destination}"
                    )
                temporary.unlink()
                newly_created = False
            else:
                os.replace(temporary, destination)
                newly_created = True
            return _InstalledBlob(
                path=destination,
                locator=relative.as_posix(),
                content=content,
                newly_created=newly_created,
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def _commit_revision(
        self,
        *,
        storage_kind: AssetStorageKind,
        content_locator: str,
        content: _InspectedContent,
        provenance: Provenance,
        asset_id: str | None,
        parent_revision_id: str | None,
        asset_name: str,
    ) -> AssetRevision:
        if not isinstance(provenance, Provenance):
            raise TypeError("provenance must be Provenance")
        normalized_asset_id = _optional_id(asset_id, "asset id")
        normalized_parent_id = _optional_id(parent_revision_id, "parent revision id")
        normalized_name = asset_name.strip()
        if not normalized_name:
            raise ValueError("asset name cannot be empty")
        revision_id = f"rev_{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        provenance_json = _provenance_to_json(provenance)
        content_path = self._path_from_locator(storage_kind, content_locator)

        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                resolved_asset_id, sequence, asset_created_at = self._resolve_target_asset(
                    connection,
                    asset_id=normalized_asset_id,
                    parent_revision_id=normalized_parent_id,
                    asset_name=normalized_name,
                    now=now,
                )
                revision = AssetRevision(
                    asset_id=resolved_asset_id,
                    sequence=sequence,
                    content_uri=content_path.as_uri(),
                    media_type=content.media_type,
                    sha256=content.sha256,
                    provenance=provenance,
                    revision_id=revision_id,
                    byte_size=content.byte_size,
                    width=content.width,
                    height=content.height,
                    created_at=now,
                    parent_revision_id=normalized_parent_id,
                    storage_kind=storage_kind,
                )
                # Validate a newly-created aggregate before its transaction is committed.
                if sequence == 1:
                    AssetRecord(
                        asset_id=resolved_asset_id,
                        name=normalized_name,
                        revisions=(revision,),
                        created_at=asset_created_at,
                    )
                connection.execute(
                    """
                    INSERT INTO revisions (
                        revision_id, asset_id, sequence, parent_revision_id,
                        storage_kind, content_locator, media_type, sha256,
                        byte_size, width, height, provenance_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        resolved_asset_id,
                        sequence,
                        normalized_parent_id,
                        storage_kind.value,
                        content_locator,
                        content.media_type,
                        content.sha256,
                        content.byte_size,
                        content.width,
                        content.height,
                        provenance_json,
                        now.isoformat(),
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return revision

    def _resolve_target_asset(
        self,
        connection: sqlite3.Connection,
        *,
        asset_id: str | None,
        parent_revision_id: str | None,
        asset_name: str,
        now: datetime,
    ) -> tuple[str, int, datetime]:
        parent_asset_id: str | None = None
        if parent_revision_id is not None:
            parent_row = connection.execute(
                "SELECT asset_id FROM revisions WHERE revision_id = ?",
                (parent_revision_id,),
            ).fetchone()
            if parent_row is None:
                raise AssetParentError(f"unknown parent revision: {parent_revision_id}")
            parent_asset_id = str(parent_row["asset_id"])
            if asset_id is not None and asset_id != parent_asset_id:
                raise AssetParentError("parent revision belongs to a different asset")

        resolved_asset_id = parent_asset_id or asset_id or f"asset_{uuid.uuid4().hex}"
        asset_row = connection.execute(
            "SELECT name, created_at FROM assets WHERE asset_id = ?",
            (resolved_asset_id,),
        ).fetchone()
        if asset_row is None:
            if parent_revision_id is not None:
                raise AssetRepositoryCorruptionError("parent asset is missing")
            connection.execute(
                "INSERT INTO assets (asset_id, name, created_at) VALUES (?, ?, ?)",
                (resolved_asset_id, asset_name, now.isoformat()),
            )
            return resolved_asset_id, 1, now
        if parent_revision_id is None:
            raise AssetParentError("appending to an existing asset requires a parent revision")
        next_sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM revisions WHERE asset_id = ?",
                (resolved_asset_id,),
            ).fetchone()[0]
        )
        created_at = _parse_datetime(asset_row["created_at"], "asset created_at")
        return resolved_asset_id, next_sequence, created_at

    def _decode_details(self, path: Path) -> tuple[str, str, int, int]:
        try:
            decoded = decode_image(path)
        except ImageLoadFailure as error:
            if error.kind == "too_large":
                raise AssetContentTooLargeError(str(error)) from error
            raise AssetContentValidationError(str(error)) from error
        try:
            source_format = (metadata_from_image(decoded).original_format or "").upper()
            details = _FORMAT_DETAILS.get(source_format)
            if details is None:
                raise AssetContentValidationError(
                    f"unsupported decoded image format: {source_format or 'unknown'}"
                )
            media_type, extension = details
            return media_type, extension, decoded.width, decoded.height
        finally:
            decoded.close()

    def _hash_file(self, path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with path.open("rb") as stream:
                while block := stream.read(_COPY_BLOCK_BYTES):
                    byte_size += len(block)
                    self._check_size(byte_size)
                    digest.update(block)
        except AssetContentTooLargeError:
            raise
        except OSError as error:
            raise AssetContentMissingError(f"cannot read image content: {path}") from error
        return digest.hexdigest(), byte_size

    def _check_size(self, byte_size: int) -> None:
        if byte_size > self.max_content_bytes:
            raise AssetContentTooLargeError(
                f"image exceeds the {self.max_content_bytes}-byte repository limit"
            )

    @staticmethod
    def _check_expected_media_type(
        actual_media_type: str,
        expected_media_type: str | None,
    ) -> None:
        if expected_media_type is None:
            return
        expected = _normalize_media_type(expected_media_type)
        if expected != actual_media_type:
            raise AssetMediaTypeMismatchError(
                f"declared media type {expected} does not match decoded {actual_media_type}"
            )

    def _path_from_locator(self, storage_kind: AssetStorageKind, locator: str) -> Path:
        if storage_kind is AssetStorageKind.EXTERNAL:
            path = Path(locator).expanduser()
            if not path.is_absolute():
                raise AssetRepositoryCorruptionError("external content path is not absolute")
            return path.resolve(strict=False)
        candidate = (self._blob_root / Path(locator)).resolve(strict=False)
        if not candidate.is_relative_to(self._blob_root):
            raise AssetRepositoryCorruptionError("managed content path escaped its root")
        return candidate

    def _revision_from_row(self, row: sqlite3.Row) -> AssetRevision:
        try:
            storage_kind = AssetStorageKind(str(row["storage_kind"]))
            locator = str(row["content_locator"])
            content_path = self._path_from_locator(storage_kind, locator)
            provenance = _provenance_from_json(str(row["provenance_json"]))
            parent_value = row["parent_revision_id"]
            return AssetRevision(
                asset_id=str(row["asset_id"]),
                sequence=int(row["sequence"]),
                content_uri=content_path.as_uri(),
                media_type=str(row["media_type"]),
                sha256=str(row["sha256"]),
                provenance=provenance,
                revision_id=str(row["revision_id"]),
                byte_size=int(row["byte_size"]),
                width=int(row["width"]),
                height=int(row["height"]),
                created_at=_parse_datetime(row["created_at"], "revision created_at"),
                parent_revision_id=(None if parent_value is None else str(parent_value)),
                storage_kind=storage_kind,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AssetRepositoryCorruptionError(f"invalid revision row: {error}") from error

    def _locator_is_referenced(self, locator: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM revisions "
                "WHERE storage_kind = 'managed' AND content_locator = ? LIMIT 1",
                (locator,),
            ).fetchone()
        return row is not None


def _absolute_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def _existing_file(path: str | Path) -> Path:
    candidate = _absolute_path(path)
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    if not candidate.is_file():
        raise IsADirectoryError(candidate)
    return candidate


def _content_matches_revision(
    content: _InspectedContent,
    revision: AssetRevision,
) -> bool:
    return (
        content.sha256 == revision.sha256
        and content.byte_size == revision.byte_size
        and content.width == revision.width
        and content.height == revision.height
        and content.media_type == revision.media_type
    )


def _required_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} cannot be empty")
    return value.strip()


def _optional_id(value: str | None, label: str) -> str | None:
    return None if value is None else _required_id(value, label)


def _normalize_media_type(value: str) -> str:
    normalized = value.split(";", 1)[0].strip().casefold()
    if not normalized:
        raise ValueError("media type cannot be empty")
    return _MEDIA_TYPE_ALIASES.get(normalized, normalized)


def _parse_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise AssetRepositoryCorruptionError(f"{label} is not a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise AssetRepositoryCorruptionError(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise AssetRepositoryCorruptionError(f"{label} is not timezone-aware")
    return parsed


def _provenance_to_json(provenance: Provenance) -> str:
    payload = {
        "origin": provenance.origin,
        "actor": provenance.actor,
        "created_at": provenance.created_at.isoformat(),
        "source_revision_ids": list(provenance.source_revision_ids),
        "request_id": provenance.request_id,
        "provider_id": provenance.provider_id,
        "model": provenance.model,
        "parameters": dict(provenance.parameters),
    }
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise AssetContentValidationError(f"provenance cannot be serialized: {error}") from error


def _provenance_from_json(raw: str) -> Provenance:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("provenance must be an object")
        source_ids = payload["source_revision_ids"]
        parameters = payload["parameters"]
        if not isinstance(source_ids, list) or not all(
            isinstance(item, str) for item in source_ids
        ):
            raise TypeError("source revision ids must be strings")
        if not isinstance(parameters, dict) or not all(isinstance(key, str) for key in parameters):
            raise TypeError("provenance parameters must be an object")
        scalar_parameters: dict[str, Scalar] = {}
        for key, value in parameters.items():
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise TypeError("provenance parameters must be scalar")
            scalar_parameters[key] = value
        return Provenance(
            origin=_required_database_text(payload["origin"], "provenance origin"),
            actor=_required_database_text(payload["actor"], "provenance actor"),
            created_at=_parse_datetime(payload["created_at"], "provenance created_at"),
            source_revision_ids=tuple(source_ids),
            request_id=_optional_database_text(payload.get("request_id"), "request id"),
            provider_id=_optional_database_text(payload.get("provider_id"), "provider id"),
            model=_optional_database_text(payload.get("model"), "model"),
            parameters=scalar_parameters,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, AssetRepositoryCorruptionError):
            raise
        raise AssetRepositoryCorruptionError(f"invalid provenance: {error}") from error


def _optional_database_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string or null")
    return value


def _required_database_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    return value
