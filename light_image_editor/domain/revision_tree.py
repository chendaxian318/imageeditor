"""Validated parent/child views over immutable asset revisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .assets import AssetRecord, AssetRevision


@dataclass(frozen=True, slots=True)
class RevisionTree:
    """A read-only revision graph for one asset.

    Legacy records may contain more than one root because ``parent_revision_id``
    did not exist in the first domain contract. Repository-created records use
    one root and an explicit parent for every later revision.
    """

    asset_id: str
    revisions: tuple[AssetRevision, ...]
    _by_id: Mapping[str, AssetRevision] = field(init=False, repr=False, compare=False)
    _children: Mapping[str, tuple[AssetRevision, ...]] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        asset_id = self.asset_id.strip()
        if not asset_id:
            raise ValueError("revision tree asset id cannot be empty")
        revisions = tuple(self.revisions)
        if not revisions:
            raise ValueError("revision tree requires at least one revision")
        if any(revision.asset_id != asset_id for revision in revisions):
            raise ValueError("revision tree cannot mix assets")
        by_id = {revision.revision_id: revision for revision in revisions}
        if len(by_id) != len(revisions):
            raise ValueError("revision tree ids must be unique")

        children: dict[str, list[AssetRevision]] = {revision_id: [] for revision_id in by_id}
        for revision in revisions:
            parent_id = revision.parent_revision_id
            if parent_id is None:
                continue
            if parent_id not in by_id:
                raise ValueError("revision tree parent is missing")
            children[parent_id].append(revision)

        self._validate_acyclic(by_id)
        ordered_children = {
            revision_id: tuple(sorted(items, key=lambda item: item.sequence))
            for revision_id, items in children.items()
        }
        object.__setattr__(self, "asset_id", asset_id)
        object.__setattr__(
            self, "revisions", tuple(sorted(revisions, key=lambda item: item.sequence))
        )
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))
        object.__setattr__(self, "_children", MappingProxyType(ordered_children))

    @classmethod
    def from_record(cls, record: AssetRecord) -> RevisionTree:
        return cls(asset_id=record.asset_id, revisions=record.revisions)

    @property
    def roots(self) -> tuple[AssetRevision, ...]:
        return tuple(revision for revision in self.revisions if revision.parent_revision_id is None)

    @property
    def leaves(self) -> tuple[AssetRevision, ...]:
        return tuple(revision for revision in self.revisions if not self._children[revision.id])

    def revision(self, revision_id: str) -> AssetRevision:
        try:
            return self._by_id[revision_id]
        except KeyError as error:
            raise KeyError(f"unknown revision: {revision_id}") from error

    def parent_of(self, revision_id: str) -> AssetRevision | None:
        revision = self.revision(revision_id)
        if revision.parent_revision_id is None:
            return None
        return self._by_id[revision.parent_revision_id]

    def children_of(self, revision_id: str) -> tuple[AssetRevision, ...]:
        self.revision(revision_id)
        return self._children[revision_id]

    def ancestors_of(self, revision_id: str) -> tuple[AssetRevision, ...]:
        ancestors: list[AssetRevision] = []
        current = self.parent_of(revision_id)
        while current is not None:
            ancestors.append(current)
            current = self.parent_of(current.revision_id)
        return tuple(ancestors)

    @staticmethod
    def _validate_acyclic(by_id: Mapping[str, AssetRevision]) -> None:
        complete: set[str] = set()
        for revision_id in by_id:
            if revision_id in complete:
                continue
            visiting: set[str] = set()
            current_id: str | None = revision_id
            while current_id is not None and current_id not in complete:
                if current_id in visiting:
                    raise ValueError("revision tree contains a cycle")
                visiting.add(current_id)
                current_id = by_id[current_id].parent_revision_id
            complete.update(visiting)
