from __future__ import annotations

import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from image_ops import save_image
from light_image_editor.domain import (
    ConversionDraft,
    ConversionQueue,
    ConversionRunner,
    ConversionRunResult,
    ConversionSession,
    ConversionSessionStore,
    ConversionTaskStatus,
    ExportProfile,
    TransformRecipe,
    TransformStep,
)
from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.shell import ModeId
from light_image_editor.ui.main_window import ImageEditorApp
from light_image_editor.ui.task_tray import UnifiedTaskKind, UnifiedTaskStatus


def _image(path: Path, color: str) -> Path:
    image = Image.new("RGB", (24, 16), color)
    image.save(path, "PNG")
    image.close()
    return path.resolve()


def _recipe() -> TransformRecipe:
    return TransformRecipe(
        name="恢复集成配方",
        steps=(TransformStep.resize(exact_width=12, exact_height=8),),
        export_profile=ExportProfile(
            output_format="PNG",
            preserve_metadata=False,
            rename_template="{stem}_{index:04d}",
            conflict_policy="error",
        ),
    )


class ConversionSessionUiIntegrationTests(unittest.TestCase):
    @staticmethod
    def _create_app(directory: str) -> ImageEditorApp:
        root = Path(directory)
        settings = SettingsStore(root / "settings.json")
        settings.save(
            {
                "schema_version": 1,
                "onboarding_completed": True,
                "theme_mode": "light",
                "thumbnail_sidebar_visible": True,
                "edit_panel_visible": True,
            }
        )
        app = ImageEditorApp(
            settings_store=settings,
            session_store=SessionStore(root / "viewer-session.json"),
        )
        for job in app._startup_jobs:
            app.after_cancel(job)
        app._startup_jobs.clear()
        app.update_idletasks()
        app.update()
        return app

    @staticmethod
    def _close_app(app: ImageEditorApp) -> None:
        if app.winfo_exists():
            app._shutdown_services()
            app.destroy()

    @staticmethod
    def _pump_until(
        app: ImageEditorApp,
        predicate: Callable[[], bool],
        *,
        timeout: float = 8.0,
    ) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        if not predicate():
            raise AssertionError("等待转换界面状态超时")

    @staticmethod
    def _use_recipe(app: ImageEditorApp, recipe: TransformRecipe) -> None:
        app.mode_router.switch(ModeId.CONVERT)
        app.conversion_draft_history.reset(ConversionDraft.from_recipe(recipe))
        app._conversion_recipe_override = recipe
        app._apply_conversion_draft_to_controls()

    @staticmethod
    def _terminal_session(
        root: Path,
        *,
        second_succeeded: bool = False,
    ) -> ConversionSession:
        first = _image(root / "first.png", "#7a5c91")
        second = _image(root / "second.png", "#5c9172")
        queue = ConversionQueue()
        queue.add_files((first, second))
        runner = ConversionRunner(queue, _recipe(), root / "output", max_workers=1)
        runner.output_directory.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)

        first_output = runner.tasks[0].planned_output
        _image(first_output, "#4f3f60")
        first_record = replace(
            runner.tasks[0],
            status=ConversionTaskStatus.SUCCEEDED,
            attempts=1,
            started_at=now,
            finished_at=now,
            input_bytes=first.stat().st_size,
            output_bytes=first_output.stat().st_size,
            output=first_output,
        )
        second_record = replace(
            runner.tasks[1],
            status=ConversionTaskStatus.FAILED,
            error="OSError: 上次转换失败",
            attempts=1,
            started_at=now,
            finished_at=now,
            input_bytes=second.stat().st_size,
        )
        if second_succeeded:
            second_output = second_record.planned_output
            _image(second_output, "#3f6050")
            second_record = replace(
                second_record,
                status=ConversionTaskStatus.SUCCEEDED,
                error=None,
                output_bytes=second_output.stat().st_size,
                output=second_output,
            )
        return ConversionSession(
            queue_items=queue.items,
            recipe=runner.recipe,
            output_directory=runner.output_directory,
            tasks=(first_record, second_record),
            max_workers=1,
        )

    def test_fresh_run_writes_checkpoint_and_retains_it_after_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = _image(root / "valid.png", "#895a6c")
            damaged = root / "damaged.png"
            damaged.write_bytes(b"not an image")
            output = root / "fresh-output"
            app = self._create_app(directory)
            try:
                self._use_recipe(app, _recipe())
                app.conversion_workspace.add_files((valid, damaged))
                app.conversion_output_dir_var.set(str(output))

                app._start_conversion_queue()
                task_id = app._conversion_tray_task_id
                self.assertIsNotNone(task_id)
                assert task_id is not None
                running_task = app.task_tray.get(task_id)
                self.assertIsNotNone(running_task)
                assert running_task is not None
                self.assertEqual(running_task.kind, UnifiedTaskKind.CONVERSION)
                self.assertEqual(running_task.status, UnifiedTaskStatus.RUNNING)
                self._pump_until(app, lambda: app._conversion_thread is None, timeout=12.0)

                completed_task = app.task_tray.get(task_id)
                self.assertIsNotNone(completed_task)
                assert completed_task is not None
                self.assertEqual(completed_task.status, UnifiedTaskStatus.FAILED)
                self.assertEqual(completed_task.progress_current, 2)
                self.assertEqual(completed_task.progress_total, 2)
                self.assertTrue(completed_task.retryable)

                self.assertTrue(app.conversion_checkpoint_writer.flush(timeout=3.0))
                self.assertTrue(app.conversion_session_path.is_file())
                restored = ConversionSessionStore().load(app.conversion_session_path)
                statuses = {task.input.path: task.status for task in restored.tasks}
                self.assertEqual(statuses[valid], ConversionTaskStatus.SUCCEEDED)
                self.assertEqual(statuses[damaged.resolve()], ConversionTaskStatus.FAILED)
                self.assertEqual(
                    tuple(task.planned_output for task in restored.tasks),
                    tuple(task.planned_output for task in app._conversion_session.tasks),
                )
            finally:
                self._close_app(app)

    def test_restored_session_resumes_only_unfinished_and_preserves_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = self._terminal_session(root)
            success_before = session.tasks[0]
            failed_before = session.tasks[1]
            assert success_before.output is not None
            success_bytes = success_before.output.read_bytes()
            app = self._create_app(directory)
            try:
                app._restore_conversion_session(session)
                with patch(
                    "light_image_editor.domain.conversion_runner.save_image",
                    wraps=save_image,
                ) as mocked_save:
                    app._start_conversion_queue()
                    self._pump_until(app, lambda: app._conversion_thread is None, timeout=12.0)

                self.assertEqual(mocked_save.call_count, 1)
                self.assertEqual(
                    Path(mocked_save.call_args.args[1]).resolve(),
                    failed_before.planned_output,
                )
                final_session = app._conversion_session
                self.assertIsNotNone(final_session)
                assert final_session is not None
                self.assertEqual(final_session.tasks[0], success_before)
                self.assertEqual(success_before.output.read_bytes(), success_bytes)
                self.assertEqual(final_session.tasks[1].status, ConversionTaskStatus.SUCCEEDED)
                self.assertEqual(final_session.tasks[1].attempts, 2)
                self.assertEqual(
                    final_session.tasks[1].planned_output,
                    failed_before.planned_output,
                )
            finally:
                self._close_app(app)

    def test_selective_retry_merges_success_and_quarantines_corrupt_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved_session = self._terminal_session(root, second_succeeded=True)
            checkpoint = root / "damaged-session.json"
            ConversionSessionStore().save(saved_session, checkpoint)
            damaged_output = saved_session.tasks[1].planned_output
            corrupt_bytes = b"x" * damaged_output.stat().st_size
            damaged_output.write_bytes(corrupt_bytes)
            recovered = ConversionSessionStore().load(checkpoint)
            failed = recovered.tasks[1]
            self.assertEqual(failed.status, ConversionTaskStatus.FAILED)
            self.assertTrue((failed.error or "").startswith("恢复验证失败"))
            success_before = recovered.tasks[0]
            assert success_before.output is not None
            success_bytes = success_before.output.read_bytes()
            backup = damaged_output.with_name(f"{damaged_output.name}.corrupt.bak")
            app = self._create_app(directory)
            try:
                app._restore_conversion_session(recovered)
                with patch(
                    "light_image_editor.domain.conversion_runner.save_image",
                    wraps=save_image,
                ) as mocked_save:
                    app._retry_conversion_tasks((failed,))
                    self._pump_until(app, lambda: app._conversion_thread is None, timeout=12.0)

                self.assertTrue(backup.is_file())
                self.assertEqual(backup.read_bytes(), corrupt_bytes)
                self.assertEqual(mocked_save.call_count, 1)
                final_session = app._conversion_session
                self.assertIsNotNone(final_session)
                assert final_session is not None
                self.assertEqual(len(final_session.tasks), 2)
                self.assertEqual(final_session.tasks[0], success_before)
                self.assertEqual(success_before.output.read_bytes(), success_bytes)
                self.assertEqual(final_session.tasks[1].status, ConversionTaskStatus.SUCCEEDED)
                self.assertEqual(final_session.tasks[1].planned_output, damaged_output)
            finally:
                self._close_app(app)

    def test_running_generation_ignores_stale_terminal_and_freezes_queue_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _image(root / "first.png", "#73558c")
            second = _image(root / "second.png", "#55738c")
            late = _image(root / "late.png", "#558c65")
            release = threading.Event()
            started = threading.Event()
            original_run = ConversionRunner.run

            def blocked_run(runner: ConversionRunner) -> ConversionRunResult:
                started.set()
                if not release.wait(timeout=3.0):
                    raise TimeoutError("测试未释放转换执行器")
                return original_run(runner)

            app = self._create_app(directory)
            try:
                self._use_recipe(app, _recipe())
                app.conversion_workspace.add_files((first, second))
                app.conversion_output_dir_var.set(str(root / "frozen-output"))
                with patch.object(ConversionRunner, "run", new=blocked_run):
                    app._start_conversion_queue()
                    self.assertTrue(started.wait(timeout=1.0))
                    generation = app._active_conversion_generation
                    frozen_queue = app._conversion_run_queue
                    self.assertIsNotNone(generation)
                    self.assertIsNotNone(frozen_queue)
                    assert generation is not None
                    assert frozen_queue is not None

                    app.conversion_workspace.add_files((late,))
                    self.assertEqual(len(app.conversion_workspace.items), 3)
                    self.assertEqual(
                        tuple(item.path for item in frozen_queue.items),
                        (first, second),
                    )
                    self.assertIn("disabled", app.conversion_workspace.add_files_button.state())

                    poll_job = app._conversion_poll_job
                    if poll_job is not None:
                        app.after_cancel(poll_job)
                        app._conversion_poll_job = None
                    app._conversion_ui_queue.put(
                        (generation - 1, "error", RuntimeError("过时代际结果"))
                    )
                    app._poll_conversion_worker()
                    self.assertEqual(app._active_conversion_generation, generation)
                    self.assertIsNotNone(app._conversion_thread)

                    release.set()
                    self._pump_until(app, lambda: app._conversion_thread is None, timeout=12.0)

                final_session = app._conversion_session
                self.assertIsNotNone(final_session)
                assert final_session is not None
                self.assertEqual(len(final_session.tasks), 2)
                self.assertEqual(
                    {task.input.path for task in final_session.tasks},
                    {first, second},
                )
            finally:
                release.set()
                self._close_app(app)


if __name__ == "__main__":
    unittest.main()
