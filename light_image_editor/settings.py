"""Persistent application settings.

The storage location and JSON representation intentionally match the original
single-file application.  This module only separates persistence from the UI;
it does not migrate or reinterpret existing user settings.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

CONFIG_PATH = Path.home() / ".light_image_editor.json"
SETTINGS_SCHEMA_VERSION = 1


def migrate_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Return a forward-compatible copy while preserving all legacy user data."""

    migrated = dict(data)
    version = int(migrated.get("schema_version", 0))
    if version > SETTINGS_SCHEMA_VERSION:
        return migrated
    if version == 0:
        migrated.setdefault("update_channel", "stable")
        migrated.setdefault("crash_reporting_consent", False)
        migrated["schema_version"] = 1
    return migrated


class SettingsStore:
    """Read and write the editor's JSON settings file."""

    def __init__(self, path: str | Path = CONFIG_PATH) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        """Return stored settings, or an empty mapping for unreadable data."""

        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return cast(dict[str, Any], data)

    def load_migrated(self) -> dict[str, Any]:
        data = self.load()
        if not data:
            return {}
        migrated = migrate_settings(data)
        if migrated != data:
            backup = self.path.with_suffix(self.path.suffix + ".pre-v1.bak")
            if not backup.exists():
                backup.write_bytes(self.path.read_bytes())
            self.save(migrated)
        return migrated

    def save(self, data: Mapping[str, object]) -> None:
        """Persist *data* using the legacy UTF-8, indented JSON format."""

        self.path.write_text(
            json.dumps(dict(data), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
