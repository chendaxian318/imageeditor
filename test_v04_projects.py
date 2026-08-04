from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from image_ops import EditState
from light_image_editor.commands import EditCommand
from light_image_editor.document import Document, DocumentState
from light_image_editor.image_loader import decode_image
from light_image_editor.projects import (
    PROJECT_FORMAT,
    ProjectSourceMissing,
    ProjectSourceModified,
    load_project,
    save_project,
)
from light_image_editor.renderer import render_image
from light_image_editor.session import SessionStore


class ProjectRoundTripTests(unittest.TestCase):
    def test_crash_session_restores_timeline_and_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            Image.new("RGB", (32, 24), "navy").save(source)
            document = Document.from_decoded(decode_image(source), source)
            document.mark_clean()
            document.commit_command(EditCommand.create("rotate", {"degrees": 90}, "右转"))
            document.commit_command(
                EditCommand.create("adjust", {"name": "temperature", "value": 20}, "色温")
            )
            document.undo_command()
            store = SessionStore(folder / "session.json")
            store.save(document)

            recovered = store.load()
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual(recovered.document.timeline.cursor, 1)
            self.assertEqual(
                [command.kind for command in recovered.document.timeline.commands],
                ["rotate", "adjust"],
            )
            self.assertTrue(recovered.document.dirty)

    def test_project_round_trip_preserves_timeline_cursor_and_render(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            project = folder / "edit.lieproj"
            Image.new("RGB", (80, 60), (60, 100, 180)).save(source)
            document = Document.from_decoded(decode_image(source), source)
            document.commit_command(EditCommand.create("rotate", {"degrees": 90}, "右转"))
            document.commit_command(
                EditCommand.create("adjust", {"name": "temperature", "value": 30}, "色温")
            )
            document.commit_command(
                EditCommand.create(
                    "curve",
                    {"channel": "red", "points": [[0.0, 0.0], [0.4, 0.5], [1.0, 1.0]]},
                    "红色曲线",
                )
            )
            document.undo_command()
            expected = render_image(document.image, document.timeline)

            save_project(document, project)
            loaded = load_project(project)
            actual = render_image(loaded.document.image, loaded.document.timeline)
            self.assertEqual(loaded.document.timeline.cursor, 2)
            self.assertEqual(actual.size, expected.size)
            self.assertEqual(actual.tobytes(), expected.tobytes())
            self.assertFalse(loaded.document.dirty)

    def test_unknown_fields_are_preserved_and_unknown_command_opens_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            project = folder / "future.lieproj"
            Image.new("RGB", (20, 20), "red").save(source)
            document = Document.from_decoded(decode_image(source), source)
            save_project(document, project)
            payload = json.loads(project.read_text(encoding="utf-8"))
            payload["future_field"] = {"kept": True}
            payload["timeline"]["commands"].append(
                {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "kind": "future_magic",
                    "stage": "color",
                    "params": {"strength": 10},
                    "label": "未来命令",
                    "created_at_utc": "2026-08-04T00:00:00+00:00",
                }
            )
            payload["timeline"]["cursor"] = 1
            project.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_project(project)
            self.assertTrue(loaded.document.project_read_only)
            self.assertEqual(loaded.unsupported_commands, ("future_magic",))
            self.assertEqual(loaded.document.project_extra_fields["future_field"], {"kept": True})

    def test_source_missing_and_changed_require_explicit_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            project = folder / "edit.lieproj"
            Image.new("RGB", (20, 20), "red").save(source)
            document = Document.from_decoded(decode_image(source), source)
            save_project(document, project)
            source.unlink()
            with self.assertRaises(ProjectSourceMissing):
                load_project(project)
            Image.new("RGB", (20, 20), "blue").save(source)
            with self.assertRaises(ProjectSourceModified):
                load_project(project)
            loaded = load_project(project, allow_modified_source=True)
            self.assertEqual(loaded.document.image.getpixel((0, 0)), (0, 0, 255))

    def test_schema_one_document_state_migrates_to_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            project = folder / "legacy.lieproj"
            Image.new("RGB", (100, 80), "green").save(source)
            legacy_state = DocumentState(
                edit_state=EditState(rotation=90, brightness=20),
                crop_box=(10, 8, 90, 72),
            )
            project.write_text(
                json.dumps(
                    {
                        "format": PROJECT_FORMAT,
                        "schema_version": 1,
                        "source_path": str(source),
                        "state": legacy_state.to_json(),
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_project(project)
            self.assertEqual(loaded.migrated_from, 1)
            self.assertEqual(
                [command.kind for command in loaded.document.timeline.commands],
                ["rotate", "crop", "adjust"],
            )


if __name__ == "__main__":
    unittest.main()
