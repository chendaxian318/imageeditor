from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from image_ops import save_image
from light_image_editor.domain.conversion_jobs import ConversionInput, ConversionQueue
from light_image_editor.domain.conversion_runner import (
    ConversionRunner,
    ConversionTaskStatus,
)
from light_image_editor.domain.conversion_session import (
    ConversionSession,
    ConversionSessionError,
    ConversionSessionStore,
    ConversionSessionTooLargeError,
    SessionAssetIdentity,
    SessionRevisionIdentity,
)
from light_image_editor.domain.recipes import ExportProfile, TransformRecipe, TransformStep
from light_image_editor.shell.mode_router import AssetHandoff, RevisionHandoff


def _image(path: Path, color: str = "red") -> Path:
    image = Image.new("RGB", (8, 6), color)
    image.save(path, "PNG")
    image.close()
    return path.resolve()


def _recipe() -> TransformRecipe:
    return TransformRecipe(
        name="会话中文配方",
        steps=(TransformStep.rotate(90),),
        export_profile=ExportProfile(
            output_format="PNG",
            preserve_metadata=False,
            rename_template="{stem}_out",
            conflict_policy="error",
        ),
    )


def _pending_session(root: Path) -> ConversionSession:
    source = _image(root / "source.png")
    queue = ConversionQueue()
    queue.add_file(source)
    runner = ConversionRunner(queue, _recipe(), root / "output")
    return ConversionSession.capture(queue, runner)


class ConversionSessionTests(unittest.TestCase):
    def test_queue_order_enabled_and_asset_revision_identity_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = _image(root / "b.png")
            second_path = _image(root / "a.png", "blue")
            asset = AssetHandoff(
                "asset-中文",
                uri=first_path.as_uri(),
                display_name="样片.png",
                metadata={"相机": "测试机", "标签": ["一", "二"]},
            )
            revision = RevisionHandoff(
                "revision-3",
                asset.id,
                number=3,
                dirty=True,
                metadata={"操作": "旋转"},
            )
            queue = ConversionQueue.from_ordered(
                (
                    ConversionInput(
                        first_path,
                        asset=asset,
                        revision=revision,
                        metadata={"入口": "看图模式"},
                    ),
                    ConversionInput(second_path, enabled=False),
                )
            )
            runner = ConversionRunner(queue, _recipe(), root / "output", max_workers=3)
            session = ConversionSession.capture(queue, runner)
            path = root / "conversion-session.json"

            ConversionSessionStore().save(session, path)
            restored = ConversionSessionStore().load(path)

            self.assertIn("会话中文配方".encode(), path.read_bytes())
            self.assertEqual([item.path.name for item in restored.queue_items], ["b.png", "a.png"])
            self.assertEqual([item.enabled for item in restored.queue_items], [True, False])
            restored_asset = restored.queue_items[0].asset
            restored_revision = restored.queue_items[0].revision
            self.assertIsInstance(restored_asset, SessionAssetIdentity)
            self.assertIsInstance(restored_revision, SessionRevisionIdentity)
            assert isinstance(restored_asset, SessionAssetIdentity)
            assert isinstance(restored_revision, SessionRevisionIdentity)
            self.assertEqual(restored_asset.id, "asset-中文")
            self.assertEqual(restored_asset.metadata["标签"], ["一", "二"])
            self.assertEqual(restored_revision.id, "revision-3")
            self.assertEqual(restored_revision.number, 3)
            self.assertTrue(restored_revision.dirty)
            self.assertEqual(restored.recipe, _recipe())
            self.assertEqual(restored.max_workers, 3)
            self.assertEqual(restored.tasks[0].planned_output, runner.tasks[0].planned_output)

    def test_missing_and_corrupt_success_outputs_are_downgraded_to_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _image(root / "first.png")
            second = _image(root / "second.png", "green")
            queue = ConversionQueue()
            queue.add_files((first, second))
            runner = ConversionRunner(queue, _recipe(), root / "output", max_workers=2)
            result = runner.run()
            self.assertEqual(result.counts[ConversionTaskStatus.SUCCEEDED], 2)
            path = root / "session.json"
            ConversionSessionStore().save(ConversionSession.capture(queue, runner), path)
            first_output = runner.tasks[0].planned_output
            second_output = runner.tasks[1].planned_output
            first_output.unlink()
            original_size = second_output.stat().st_size
            second_output.write_bytes(b"x" * original_size)

            restored = ConversionSessionStore().load(path)

            self.assertTrue(
                all(task.status is ConversionTaskStatus.FAILED for task in restored.tasks)
            )
            self.assertTrue(all("恢复验证失败" in (task.error or "") for task in restored.tasks))

    def test_running_is_recovered_as_pending_and_cancelled_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _image(root / "first.png")
            second = _image(root / "second.png", "blue")
            queue = ConversionQueue()
            queue.add_files((first, second))
            runner = ConversionRunner(queue, _recipe(), root / "output")
            now = datetime.now(UTC)
            running = replace(
                runner.tasks[0],
                status=ConversionTaskStatus.RUNNING,
                attempts=1,
                started_at=now,
            )
            cancelled = replace(
                runner.tasks[1],
                status=ConversionTaskStatus.CANCELLED,
                error="用户取消",
                finished_at=now,
            )
            session = ConversionSession(
                queue_items=queue.items,
                recipe=runner.recipe,
                output_directory=runner.output_directory,
                tasks=(running, cancelled),
            )
            path = root / "session.json"
            ConversionSessionStore().save(session, path)

            restored = ConversionSessionStore().load(path)

            self.assertEqual(restored.tasks[0].status, ConversionTaskStatus.PENDING)
            self.assertIsNone(restored.tasks[0].started_at)
            self.assertEqual(restored.tasks[0].attempts, 1)
            self.assertEqual(restored.tasks[1].status, ConversionTaskStatus.CANCELLED)
            self.assertEqual(restored.tasks[1].error, "用户取消")

    def test_retry_only_failed_item_keeps_path_and_never_rewrites_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            damaged = root / "damaged.png"
            damaged.write_bytes(b"not an image")
            valid = _image(root / "valid.png", "green")
            queue = ConversionQueue()
            queue.add_files((damaged, valid))
            runner = ConversionRunner(queue, _recipe(), root / "output", max_workers=1)
            first_result = runner.run()
            self.assertEqual(first_result.counts[ConversionTaskStatus.SUCCEEDED], 1)
            self.assertEqual(first_result.counts[ConversionTaskStatus.FAILED], 1)
            session_path = root / "session.json"
            ConversionSessionStore().save(
                ConversionSession.capture(queue, runner),
                session_path,
            )
            failed_before = next(
                task for task in runner.tasks if task.status is ConversionTaskStatus.FAILED
            )
            success_before = next(
                task for task in runner.tasks if task.status is ConversionTaskStatus.SUCCEEDED
            )
            assert success_before.output is not None
            success_bytes = success_before.output.read_bytes()
            _image(damaged, "purple")

            restored = ConversionSessionStore().load(session_path)
            retry = restored.retry_runner(max_workers=1)
            with patch(
                "light_image_editor.domain.conversion_runner.save_image",
                wraps=save_image,
            ) as mocked_save:
                retry_result = retry.run()

            self.assertEqual(mocked_save.call_count, 1)
            self.assertEqual(
                Path(mocked_save.call_args.args[1]).resolve(),
                failed_before.planned_output,
            )
            self.assertEqual(retry_result.counts[ConversionTaskStatus.SUCCEEDED], 2)
            retried = next(task for task in retry.tasks if task.input.path == damaged.resolve())
            untouched = next(task for task in retry.tasks if task.input.path == valid)
            self.assertEqual(retried.planned_output, failed_before.planned_output)
            self.assertEqual(retried.attempts, 2)
            self.assertEqual(untouched, success_before)
            self.assertEqual(success_before.output.read_bytes(), success_bytes)

    def test_corrupt_unknown_version_and_oversized_sessions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _pending_session(root)
            valid_path = root / "valid.json"
            ConversionSessionStore().save(session, valid_path)
            payload = json.loads(valid_path.read_text(encoding="utf-8"))

            invalid_utf8 = root / "invalid-utf8.json"
            invalid_utf8.write_bytes(b"\xff\xfe")
            with self.assertRaises(ConversionSessionError):
                ConversionSessionStore().load(invalid_utf8)

            for name, change in (
                ("version", {"schema_version": 999}),
                ("unknown", {"unexpected": True}),
            ):
                with self.subTest(name=name):
                    path = root / f"{name}.json"
                    changed = dict(payload)
                    changed.update(change)
                    path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(ConversionSessionError):
                        ConversionSessionStore().load(path)

            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * 129)
            with self.assertRaises(ConversionSessionTooLargeError):
                ConversionSessionStore(max_file_bytes=128).load(oversized)

    def test_atomic_save_failure_preserves_previous_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _pending_session(root)
            path = root / "session.json"
            store = ConversionSessionStore()
            store.save(session, path)
            previous = path.read_bytes()

            with (
                patch(
                    "light_image_editor.domain.conversion_session.os.replace",
                    side_effect=OSError("simulated replace failure"),
                ),
                self.assertRaises(OSError),
            ):
                store.save(replace(session, saved_at=datetime.now(UTC)), path)

            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_retry_factory_rejects_successful_status_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _pending_session(root)

            with self.assertRaises(ValueError):
                ConversionRunner.retry_tasks(
                    session.tasks,
                    session.recipe,
                    session.output_directory,
                    statuses=(ConversionTaskStatus.SUCCEEDED,),
                )

    def test_selective_retry_merge_preserves_untouched_records_and_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = _pending_session(root)
            (original_first,) = session.tasks
            failed = replace(
                original_first,
                status=ConversionTaskStatus.FAILED,
                error="模拟失败",
                finished_at=datetime.now(UTC),
            )
            failed_session = replace(session, tasks=(failed,))
            succeeded = replace(
                failed,
                status=ConversionTaskStatus.SUCCEEDED,
                error=None,
                output=failed.planned_output,
            )

            merged = failed_session.merge_tasks([succeeded])

            self.assertEqual(merged.tasks, (succeeded,))
            with self.assertRaises(ConversionSessionError):
                failed_session.merge_tasks([replace(succeeded, planned_output=root / "other.png")])


if __name__ == "__main__":
    unittest.main()
