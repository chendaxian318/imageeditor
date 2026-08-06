"""Immutable asset and provenance records shared by product modules."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

type Scalar = str | int | float | bool | None

_SHA256 = re.compile(r"[0-9a-f]{64}")


class AssetStorageKind(StrEnum):
    """Whether repository-owned bytes back a revision."""

    EXTERNAL = "external"
    MANAGED = "managed"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _frozen_parameters(values: Mapping[str, Scalar] | None) -> Mapping[str, Scalar]:
    return MappingProxyType(dict(values or {}))


@dataclass(frozen=True, slots=True)
class Provenance:
    """Describes how an asset revision was produced without storing image data."""

    origin: str
    actor: str
    created_at: datetime = field(default_factory=_utc_now)
    source_revision_ids: tuple[str, ...] = ()
    request_id: str | None = None
    provider_id: str | None = None
    model: str | None = None
    parameters: Mapping[str, Scalar] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.origin.strip():
            raise ValueError("provenance origin cannot be empty")
        if not self.actor.strip():
            raise ValueError("provenance actor cannot be empty")
        if self.created_at.tzinfo is None:
            raise ValueError("provenance created_at must be timezone-aware")
        if any(not revision_id.strip() for revision_id in self.source_revision_ids):
            raise ValueError("source revision ids cannot be empty")
        object.__setattr__(self, "source_revision_ids", tuple(self.source_revision_ids))
        object.__setattr__(self, "parameters", _frozen_parameters(self.parameters))

    @classmethod
    def imported(cls, *, actor: str = "user", source: str | None = None) -> Provenance:
        parameters: dict[str, Scalar] = {}
        if source is not None:
            parameters["source"] = source
        return cls(origin="import", actor=actor, parameters=parameters)


@dataclass(frozen=True, slots=True)
class AssetRevision:
    """One immutable content revision belonging to an asset record."""

    asset_id: str
    sequence: int
    content_uri: str
    media_type: str
    sha256: str
    provenance: Provenance
    revision_id: str = field(default_factory=lambda: _new_id("rev"))
    byte_size: int | None = None
    width: int | None = None
    height: int | None = None
    created_at: datetime = field(default_factory=_utc_now)
    # Kept at the end so existing positional construction remains compatible.
    parent_revision_id: str | None = None
    storage_kind: AssetStorageKind = AssetStorageKind.EXTERNAL

    def __post_init__(self) -> None:
        if not self.asset_id.strip():
            raise ValueError("asset id cannot be empty")
        if not self.revision_id.strip():
            raise ValueError("revision id cannot be empty")
        if self.sequence < 1:
            raise ValueError("revision sequence must be at least one")
        if not self.content_uri.strip():
            raise ValueError("content uri cannot be empty")
        if not self.media_type.strip():
            raise ValueError("media type cannot be empty")
        digest = self.sha256.casefold()
        if _SHA256.fullmatch(digest) is None:
            raise ValueError("sha256 must contain 64 hexadecimal characters")
        object.__setattr__(self, "sha256", digest)
        if self.byte_size is not None and self.byte_size < 0:
            raise ValueError("byte size cannot be negative")
        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must be provided together")
        if self.width is not None and (self.width < 1 or self.height is None or self.height < 1):
            raise ValueError("asset dimensions must be positive")
        if self.created_at.tzinfo is None:
            raise ValueError("revision created_at must be timezone-aware")
        if self.parent_revision_id is not None:
            parent_revision_id = self.parent_revision_id.strip()
            if not parent_revision_id:
                raise ValueError("parent revision id cannot be empty")
            if parent_revision_id == self.revision_id:
                raise ValueError("revision cannot be its own parent")
            object.__setattr__(self, "parent_revision_id", parent_revision_id)
        try:
            storage_kind = AssetStorageKind(self.storage_kind)
        except ValueError as error:
            raise ValueError(f"unknown asset storage kind: {self.storage_kind}") from error
        object.__setattr__(self, "storage_kind", storage_kind)

    @property
    def id(self) -> str:
        return self.revision_id

    @classmethod
    def from_content_uri(
        cls,
        *,
        asset_id: str,
        sequence: int,
        content_uri: str,
        media_type: str,
        provenance: Provenance,
        width: int | None = None,
        height: int | None = None,
        byte_size: int | None = None,
        revision_id: str | None = None,
        parent_revision_id: str | None = None,
        storage_kind: AssetStorageKind = AssetStorageKind.EXTERNAL,
    ) -> AssetRevision:
        """Build a deterministic digest for content-addressable mocks and adapters."""

        digest = hashlib.sha256(content_uri.encode("utf-8")).hexdigest()
        return cls(
            asset_id=asset_id,
            sequence=sequence,
            content_uri=content_uri,
            media_type=media_type,
            sha256=digest,
            provenance=provenance,
            width=width,
            height=height,
            byte_size=byte_size,
            revision_id=_new_id("rev") if revision_id is None else revision_id,
            parent_revision_id=parent_revision_id,
            storage_kind=storage_kind,
        )


@dataclass(frozen=True, slots=True)
class AssetRecord:
    """A stable asset identity with an append-only tuple of revisions."""

    name: str
    revisions: tuple[AssetRevision, ...]
    asset_id: str = field(default_factory=lambda: _new_id("asset"))
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.asset_id.strip():
            raise ValueError("asset id cannot be empty")
        if not self.name.strip():
            raise ValueError("asset name cannot be empty")
        revisions = tuple(self.revisions)
        if not revisions:
            raise ValueError("asset record requires at least one revision")
        if any(revision.asset_id != self.asset_id for revision in revisions):
            raise ValueError("all revisions must belong to the asset record")
        expected_sequences = tuple(range(1, len(revisions) + 1))
        if tuple(revision.sequence for revision in revisions) != expected_sequences:
            raise ValueError("asset revision sequences must be contiguous and ordered")
        if len({revision.revision_id for revision in revisions}) != len(revisions):
            raise ValueError("asset revision ids must be unique")
        seen_revision_ids: set[str] = set()
        for revision in revisions:
            if (
                revision.parent_revision_id is not None
                and revision.parent_revision_id not in seen_revision_ids
            ):
                raise ValueError("asset revision parent must precede its child")
            seen_revision_ids.add(revision.revision_id)
        if self.created_at.tzinfo is None:
            raise ValueError("asset created_at must be timezone-aware")
        object.__setattr__(self, "revisions", revisions)

    @property
    def id(self) -> str:
        return self.asset_id

    @property
    def current_revision(self) -> AssetRevision:
        return self.revisions[-1]

    def append_revision(self, revision: AssetRevision) -> AssetRecord:
        if revision.asset_id != self.asset_id:
            raise ValueError("revision belongs to a different asset")
        if revision.sequence != len(self.revisions) + 1:
            raise ValueError("revision sequence does not follow the current revision")
        if revision.parent_revision_id is not None and revision.parent_revision_id not in {
            candidate.revision_id for candidate in self.revisions
        }:
            raise ValueError("revision parent does not belong to the asset")
        return replace(self, revisions=(*self.revisions, revision))

    @classmethod
    def from_revision(cls, *, name: str, revision: AssetRevision) -> AssetRecord:
        return cls(asset_id=revision.asset_id, name=name, revisions=(revision,))
