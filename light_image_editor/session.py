"""Crash-safe autosave session persistence and recovery."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .document import (
    Document,
    DocumentOperation,
    DocumentState,
    SourceFingerprint,
    SourceMetadata,
    attach_source_metadata,
)

SESSION_PATH = Path.home() / ".light_image_editor_session.json"


@dataclass(frozen=True)
class RecoverySession:
    document: Document
    source_changed: bool


class SessionStore:
    def __init__(self, path: str | Path = SESSION_PATH) -> None:
        self.path = Path(path)
        # Retained as a legacy cleanup target for sessions written before v0.3.
        self.recovery_image_path = self.path.with_suffix(".recovery.png")

    def save(self, document: Document) -> None:
        if not document.dirty:
            self.clear()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Always retain a pixel snapshot. A state-only edit would otherwise become
        # unrecoverable if the source is moved or deleted between the crash and restart.
        recovery_path = self._save_recovery_image(document.image)
        recovery_image = str(recovery_path)

        payload: dict[str, Any] = {
            "schema_version": 1,
            "source_path": str(document.source_path) if document.source_path else None,
            "source_fingerprint": (
                {
                    "size": document.source_fingerprint.size,
                    "mtime_ns": document.source_fingerprint.mtime_ns,
                    "device": document.source_fingerprint.device,
                    "inode": document.source_fingerprint.inode,
                }
                if document.source_fingerprint
                else None
            ),
            "metadata": document.metadata.to_json(),
            "state": document.state.to_json(),
            "operations": [operation.to_json() for operation in document.operations],
            "base_revision": document.base_revision,
            "recovery_image": recovery_image,
        }
        try:
            self._atomic_write_json(payload)
        except BaseException:
            recovery_path.unlink(missing_ok=True)
            raise
        self._remove_stale_recovery_images(recovery_path)

    def load(self) -> RecoverySession | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if payload.get("schema_version") != 1:
            return None
        try:
            metadata = SourceMetadata.from_json(dict(payload.get("metadata", {})))
            source_value = payload.get("source_path")
            source_path = Path(source_value) if isinstance(source_value, str) else None
            fingerprint_data = payload.get("source_fingerprint")
            fingerprint = (
                SourceFingerprint(**fingerprint_data)
                if isinstance(fingerprint_data, dict)
                else None
            )
        except (TypeError, ValueError):
            return None
        source_changed = bool(
            source_path is not None
            and fingerprint is not None
            and not fingerprint.matches(source_path)
        )
        recovery_value = payload.get("recovery_image")
        recovery_path = Path(recovery_value) if isinstance(recovery_value, str) else None
        if (
            recovery_path is not None
            and self._is_managed_recovery_path(recovery_path)
            and recovery_path.exists()
        ):
            try:
                from .image_loader import ImageLoadFailure, decode_image

                image = attach_source_metadata(decode_image(recovery_path), metadata)
            except (ImageLoadFailure, OSError, ValueError):
                return None
        elif source_path is not None and source_path.exists():
            from .image_loader import decode_image

            image = decode_image(source_path)
        else:
            return None

        try:
            state = DocumentState.from_json(dict(payload.get("state", {})))
            document = Document(
                image=image,
                source_path=source_path,
                metadata=metadata,
                state=state,
                source_fingerprint=fingerprint,
                base_revision=int(payload.get("base_revision", 0)),
            )
        except (TypeError, ValueError):
            return None
        operations: list[DocumentOperation] = []
        for raw_operation in payload.get("operations", []):
            if not isinstance(raw_operation, dict):
                continue
            try:
                operations.append(
                    DocumentOperation(
                        sequence=int(raw_operation["sequence"]),
                        kind=str(raw_operation["kind"]),
                        timestamp_utc=str(raw_operation["timestamp_utc"]),
                        state=DocumentState.from_json(dict(raw_operation["state"])),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        document.operations = operations[-500:]
        document.mark_recovered_dirty()
        return RecoverySession(document=document, source_changed=source_changed)

    def clear(self) -> None:
        paths = [self.path, self.recovery_image_path, *self._recovery_candidates()]
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _save_recovery_image(self, image: Image.Image) -> Path:
        recovery_path = self.path.with_name(f"{self.path.stem}.recovery-{uuid.uuid4().hex}.png")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{recovery_path.name}.",
            suffix=".tmp",
            dir=recovery_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                image.save(stream, "PNG", optimize=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, recovery_path)
            return recovery_path
        finally:
            if temporary.exists():
                temporary.unlink()

    def _recovery_candidates(self) -> list[Path]:
        return list(self.path.parent.glob(f"{self.path.stem}.recovery-*.png"))

    def _remove_stale_recovery_images(self, keep: Path) -> None:
        for candidate in self._recovery_candidates():
            if candidate != keep:
                candidate.unlink(missing_ok=True)

    def _is_managed_recovery_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            parent = self.path.parent.resolve()
        except OSError:
            return False
        return (
            resolved.parent == parent
            and resolved.name.startswith(f"{self.path.stem}.recovery-")
            and resolved.suffix.casefold() == ".png"
        )

    def _atomic_write_json(self, payload: dict[str, Any]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
