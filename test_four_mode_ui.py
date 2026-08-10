from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from light_image_editor.domain import (
    AlphaBrushStroke,
    AssetRecord,
    AssetRevision,
    GenerationRequest,
    JobError,
    JobRequestKind,
    JobStatus,
    ManagedJobSnapshot,
    Provenance,
)
from light_image_editor.providers import MOCK_MODEL_ID, ProviderCapability, ProviderManifest
from light_image_editor.services.ai_authorization import AIExecutionRequirements
from light_image_editor.services.asset_repository import (
    AssetContentTooLargeError,
    AssetContentValidationError,
)
from light_image_editor.services.font_matcher import FontMatchSuggestion
from light_image_editor.services.provider_catalog import GenerationProviderProfile
from light_image_editor.services.smart_selection import (
    SmartSelectionSuggestion,
    decode_selection_mask,
)
from light_image_editor.services.text_ocr import (
    TextOcrBackend,
    TextOcrResult,
    UnavailableTextOcrBackend,
)
from light_image_editor.services.text_region_detector import TextRegionSuggestion
from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.shell import AssetHandoff, ModeId, RevisionHandoff
from light_image_editor.ui.main_window import ImageEditorApp
from light_image_editor.ui.task_tray import (
    UnifiedTaskItem,
    UnifiedTaskKind,
    UnifiedTaskStatus,
)


class FourModeUiTests(unittest.TestCase):
    @staticmethod
    def _test_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for candidate in (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ):
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size)
        return ImageFont.load_default()

    @staticmethod
    def _create_app(
        directory: str,
        *,
        initialize_settings: bool = True,
        text_ocr_backend: TextOcrBackend | None = None,
    ) -> ImageEditorApp:
        root = Path(directory)
        settings = SettingsStore(root / "settings.json")
        if initialize_settings:
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
            session_store=SessionStore(root / "session.json"),
            text_ocr_backend=text_ocr_backend,
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
    def _wait_for_asset(app: ImageEditorApp, timeout: float = 5.0) -> AssetRevision:
        deadline = time.monotonic() + timeout
        while app._current_asset_revision is None and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        if app._current_asset_revision is None:
            raise AssertionError(f"资源登记未完成：{app._last_asset_registration_error!r}")
        return app._current_asset_revision

    def test_viewer_shell_hides_other_modes_and_keeps_internal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                self.assertEqual(app.mode_router.active_mode, ModeId.VIEWER)
                self.assertEqual(tuple(app.mode_buttons), tuple(ModeId))
                self.assertGreaterEqual(len(app.feature_registry), 83)
                self.assertTrue(app.feature_registry.global_features)
                self.assertTrue(
                    all(
                        callable(app.resolve_feature_callback(feature.id))
                        for feature in app.feature_registry
                    )
                )
                self.assertFalse(app.mode_bar.winfo_ismapped())
                self.assertTrue(
                    all(not button.winfo_ismapped() for button in app.mode_buttons.values())
                )
                self.assertFalse(app.viewer_mode_actions.winfo_ismapped())
                self.assertFalse(app.favorite_toolbar.winfo_ismapped())
                self.assertFalse(app.viewer_open_folder_button.winfo_ismapped())
                self.assertTrue(
                    all(
                        not widget.winfo_ismapped()
                        for widget in app._viewer_secondary_action_widgets
                    )
                )
                self.assertEqual(
                    app.viewer_to_convert_button.accessible_name,
                    "将当前图片送到转图模式",
                )
                self.assertEqual(
                    app.viewer_to_edit_button.accessible_name,
                    "将当前图片送到改图模式",
                )
                self.assertFalse(app.panel_shell.winfo_ismapped())
                self.assertFalse(app.generate_workspace.winfo_ismapped())

                app.viewer_to_convert_button.command()
                app.update_idletasks()
                self.assertEqual(app.mode_router.active_mode, ModeId.CONVERT)
                self.assertEqual(app.active_inspector_tab, "convert")
                self.assertTrue(app.panel_shell.winfo_ismapped())
                self.assertTrue(app.resize_box.winfo_ismapped())
                self.assertTrue(app.export_box.winfo_ismapped())
                self.assertFalse(app.viewer_mode_actions.winfo_ismapped())
                self.assertEqual(app.thumbnail_sidebar.title_var.get(), "转换队列")

                app._show_inspector_tab("annotation")
                app.update_idletasks()
                self.assertEqual(app.mode_router.active_mode, ModeId.EDIT)
                self.assertEqual(app.active_inspector_tab, "annotation")
                self.assertFalse(app.resize_box.winfo_ismapped())
                self.assertEqual(app.thumbnail_sidebar.title_var.get(), "资源与版本")

                app.mode_buttons[ModeId.GENERATE].command()
                app.update_idletasks()
                self.assertEqual(app.mode_router.active_mode, ModeId.GENERATE)
                self.assertTrue(app.generate_workspace.winfo_ismapped())
                self.assertGreater(
                    app.generation_candidate_panel.winfo_rootx(),
                    app.generation_setup_panel.winfo_rootx(),
                )
                self.assertGreaterEqual(app.generation_setup_panel.winfo_width(), 320)
                self.assertGreaterEqual(app.generation_candidate_panel.winfo_width(), 240)
                self.assertLessEqual(
                    app.generation_candidate_panel.winfo_rootx()
                    + app.generation_candidate_panel.winfo_width(),
                    app.generate_workspace.winfo_rootx() + app.generate_workspace.winfo_width(),
                )
                self.assertTrue(app.generated_variation_button.winfo_ismapped())
                self.assertTrue(app.generated_favorite_button.winfo_ismapped())
                self.assertTrue(app.generated_remove_button.winfo_ismapped())
                self.assertTrue(app.generated_favorites_only_checkbutton.winfo_ismapped())
                self.assertLessEqual(
                    app.generated_remove_button.winfo_rooty()
                    + app.generated_remove_button.winfo_height(),
                    app.generate_workspace.winfo_rooty() + app.generate_workspace.winfo_height(),
                )
                self.assertFalse(app.panel_shell.winfo_ismapped())
                self.assertEqual(app.thumbnail_sidebar.title_var.get(), "参考资源")

                app.mode_buttons[ModeId.EDIT].command()
                app.update_idletasks()
                self.assertEqual(app.active_inspector_tab, "annotation")
                self.assertTrue(app.panel_shell.winfo_ismapped())

                app.mode_buttons[ModeId.VIEWER].command()
                app.update_idletasks()
                self.assertFalse(app.panel_shell.winfo_ismapped())
                self.assertFalse(app.thumbnail_sidebar.winfo_ismapped())
                self.assertFalse(app.viewer_mode_actions.winfo_ismapped())
                self.assertEqual(app.thumbnail_sidebar.title_var.get(), "资源库")
            finally:
                self._close_app(app)

    def test_recovered_unknown_ai_jobs_are_visible_without_automatic_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                unknown_edit = ManagedJobSnapshot(
                    job_id="local_job_recovered_edit",
                    request_kind=JobRequestKind.EDIT,
                    request_id="request-recovered-edit",
                    provider_id=app.mock_provider.manifest.provider_id,
                    idempotency_key="key-recovered-edit",
                    request_fingerprint="b" * 64,
                    status=JobStatus.UNKNOWN,
                    error=JobError(
                        code="interrupted_submission_unknown",
                        message="上次退出时任务状态未知",
                    ),
                )
                with patch.object(
                    app.ai_job_manager,
                    "list_jobs",
                    return_value=(unknown_edit,),
                ):
                    app._restore_ai_job_summary()

                self.assertIn("1 个状态未知", app.smart_edit_status_var.get())
                self.assertIn("未自动重试", app.smart_edit_status_var.get())
                self.assertIn("完成 0 · 未知 1", app.smart_edit_result_var.get())

                app._ai_job_recovery_error = RuntimeError("corrupt store")
                app._restore_ai_job_summary()
                self.assertIn("已损坏并隔离", app.smart_edit_status_var.get())
                self.assertIn("未自动重试", app.generate_status_var.get())
            finally:
                self._close_app(app)

    def test_generate_workspace_can_complete_and_cancel_offline_mock_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                app.mode_buttons[ModeId.GENERATE].command()
                app.generate_prompt_var.set("一张用于离线契约测试的山谷插画")
                app.generate_size_var.set("768 × 768")
                app.generate_count_var.set(2)

                app._start_mock_generation()
                first_job_id = app._mock_generation_job_id
                self.assertIsNotNone(first_job_id)
                assert first_job_id is not None
                first_task = app.task_tray.get(first_job_id)
                self.assertIsNotNone(first_task)
                assert first_task is not None
                self.assertEqual(first_task.kind, UnifiedTaskKind.GENERATION)
                self.assertTrue(first_task.cancellable)
                self.assertIn(
                    app.ai_job_manager.get_job(first_job_id).status,
                    {JobStatus.SUBMITTING, JobStatus.QUEUED, JobStatus.RUNNING},
                )

                self.assertTrue(app.task_tray.select_task(first_job_id))
                self.assertTrue(app.task_tray.cancel_selected())
                self.assertEqual(
                    app.ai_job_manager.get_job(first_job_id).status,
                    JobStatus.CANCELLED,
                )
                cancelled_task = app.task_tray.get(first_job_id)
                self.assertIsNotNone(cancelled_task)
                assert cancelled_task is not None
                self.assertEqual(cancelled_task.status, UnifiedTaskStatus.CANCELLED)
                self.assertIn("已取消", app.generate_status_var.get())

                app.generate_prompt_var.set("第二个离线候选任务")
                app._start_mock_generation()
                second_job_id = app._mock_generation_job_id
                self.assertIsNotNone(second_job_id)
                assert second_job_id is not None
                self.assertNotEqual(first_job_id, second_job_id)
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != second_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                snapshot = app.ai_job_manager.get_job(second_job_id)
                self.assertEqual(snapshot.status, JobStatus.SUCCEEDED)
                completed_task = app.task_tray.get(second_job_id)
                self.assertIsNotNone(completed_task)
                assert completed_task is not None
                self.assertEqual(completed_task.status, UnifiedTaskStatus.SUCCEEDED)
                app.mode_buttons[ModeId.VIEWER].command()
                self.assertTrue(app.task_tray.select_task(second_job_id))
                self.assertTrue(app.task_tray.activate_selected())
                self.assertEqual(app.mode_router.active_mode, ModeId.GENERATE)
                self.assertEqual(len(snapshot.outputs), 2)
                self.assertEqual(app._mock_generation_presented_job_id, second_job_id)
                self.assertIn("2 个候选", app.generate_status_var.get())
                context = app.mode_router.context_for(ModeId.GENERATE)
                self.assertIsNotNone(context.asset)
                self.assertIsNotNone(context.revision)
                self.assertEqual(context.asset_id, snapshot.outputs[0].asset_id)
                assert context.revision is not None
                generated_path = app.asset_repository.resolve_content(context.revision.id)
                self.assertTrue(generated_path.is_file())
                self.assertEqual(context.state["candidate_count"], 2)
                self.assertEqual(len(context.state["candidate_revision_ids"]), 2)
                self.assertEqual(len(app.generation_candidate_grid.items), 2)
                self.assertEqual(
                    app.generation_candidate_grid.selected_revision_id,
                    context.state["candidate_revision_ids"][0],
                )
                self.assertEqual(app.generation_prompt_history[0], "第二个离线候选任务")
                second_revision_id = context.state["candidate_revision_ids"][1]
                self.assertTrue(app.generation_candidate_grid.select(second_revision_id))
                self.assertEqual(
                    app.mode_router.context_for(ModeId.GENERATE).revision_id,
                    second_revision_id,
                )
            finally:
                self._close_app(app)

    def test_generation_history_restores_and_routes_selected_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            selected_revision_id: str | None = None
            selected_asset_id: str | None = None
            saved_job_id: str | None = None
            try:
                app.mode_buttons[ModeId.GENERATE].command()
                app.generate_prompt_var.set("用于候选历史恢复的离线海报")
                app.generate_size_var.set("768 × 768")
                app.generate_count_var.set(1)
                app._start_mock_generation()
                job_id = app._mock_generation_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                saved_job_id = job_id
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != job_id and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                selected_revision_id = app._selected_generated_revision_id
                self.assertIsNotNone(selected_revision_id)
                assert selected_revision_id is not None
                selected_asset_id = app.asset_repository.get_revision(selected_revision_id).asset_id
                app._save_config()
            finally:
                self._close_app(app)

            restored = self._create_app(directory, initialize_settings=False)
            try:
                restored.update_idletasks()
                restored.update()
                self.assertIsNotNone(selected_revision_id)
                assert selected_revision_id is not None
                self.assertEqual(
                    restored.generation_candidate_grid.selected_revision_id,
                    selected_revision_id,
                )
                self.assertIn(
                    "用于候选历史恢复的离线海报",
                    restored.generation_prompt_history,
                )
                self.assertIsNotNone(saved_job_id)
                assert saved_job_id is not None
                restored_task = restored.task_tray.get(saved_job_id)
                self.assertIsNotNone(restored_task)
                assert restored_task is not None
                self.assertEqual(restored_task.status, UnifiedTaskStatus.SUCCEEDED)
                self.assertEqual(restored_task.kind, UnifiedTaskKind.GENERATION)

                restored._send_generated_candidate_to_mode(ModeId.CONVERT)
                self.assertEqual(restored.mode_router.active_mode, ModeId.CONVERT)
                self.assertEqual(len(restored.conversion_workspace.items), 1)
                queued = restored.conversion_workspace.items[0]
                self.assertEqual(queued.revision_id, selected_revision_id)
                self.assertEqual(queued.asset_id, selected_asset_id)

                restored.mode_buttons[ModeId.GENERATE].command()
                restored._send_generated_candidate_to_mode(ModeId.EDIT)
                deadline = time.monotonic() + 5
                while (
                    restored.document is None
                    or restored._current_asset_revision is None
                    or restored._current_asset_revision.revision_id != selected_revision_id
                ) and time.monotonic() < deadline:
                    restored.update()
                    time.sleep(0.01)
                self.assertEqual(restored.mode_router.active_mode, ModeId.EDIT)
                self.assertIsNotNone(restored._current_asset_revision)
                assert restored._current_asset_revision is not None
                self.assertEqual(
                    restored._current_asset_revision.revision_id,
                    selected_revision_id,
                )
                self.assertEqual(
                    restored._current_asset_revision.asset_id,
                    selected_asset_id,
                )

                restored.mode_buttons[ModeId.GENERATE].command()
                restored._send_generated_candidate_to_mode(ModeId.VIEWER)
                deadline = time.monotonic() + 5
                while (
                    restored._current_asset_revision is None
                    or restored._current_asset_revision.revision_id != selected_revision_id
                ) and time.monotonic() < deadline:
                    restored.update()
                    time.sleep(0.01)
                self.assertEqual(restored.mode_router.active_mode, ModeId.VIEWER)
                self.assertIsNotNone(restored._current_asset_revision)
                assert restored._current_asset_revision is not None
                self.assertEqual(
                    restored._current_asset_revision.revision_id,
                    selected_revision_id,
                )
            finally:
                self._close_app(restored)

    def test_generation_favorite_survives_recent_history_eviction_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            favorite_revision_id = ""
            try:
                app.mode_buttons[ModeId.GENERATE].command()
                app.generate_prompt_var.set("需要长期收藏的候选")
                app.generate_size_var.set("768 × 768")
                app.generate_count_var.set(2)
                app._start_mock_generation()
                job_id = app._mock_generation_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != job_id and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                favorite_revision_id = app._selected_generated_revision_id or ""
                self.assertTrue(favorite_revision_id)
                app._toggle_generated_candidate_favorite()
                self.assertEqual(
                    app.generation_favorite_revision_ids,
                    [favorite_revision_id],
                )
                self.assertTrue(app.generation_candidate_grid.items[0].favorite)
                self.assertEqual(app.generated_favorite_button.cget("text"), "取消收藏")

                app.generate_favorites_only_var.set(True)
                app._generation_favorites_filter_changed()
                self.assertEqual(len(app.generation_candidate_grid.items), 1)
                self.assertEqual(
                    app.generation_candidate_grid.items[0].revision_id,
                    favorite_revision_id,
                )

                app.generation_candidate_revision_ids.clear()
                app._restore_generation_candidate_history(selected_revision_id=favorite_revision_id)
                self.assertEqual(
                    app.generation_candidate_grid.selected_revision_id,
                    favorite_revision_id,
                )
                self.assertEqual(len(app.generation_candidate_grid.items), 1)
                app._save_config()
            finally:
                self._close_app(app)

            restored = self._create_app(directory, initialize_settings=False)
            try:
                restored.update_idletasks()
                restored.update()
                self.assertEqual(
                    restored.generation_favorite_revision_ids,
                    [favorite_revision_id],
                )
                self.assertTrue(restored.generate_favorites_only_var.get())
                self.assertEqual(
                    restored.generation_candidate_grid.selected_revision_id,
                    favorite_revision_id,
                )
                self.assertTrue(restored.generation_candidate_grid.items[0].favorite)
            finally:
                self._close_app(restored)

    def test_generation_candidate_settings_discard_unknown_or_malformed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsStore(Path(directory) / "settings.json")
            settings.save(
                {
                    "schema_version": 1,
                    "onboarding_completed": True,
                    "generation_candidate_revision_ids": [
                        "missing-recent",
                        "missing-recent",
                        42,
                    ],
                    "generation_favorite_revision_ids": [
                        "missing-favorite",
                        "",
                        None,
                    ],
                    "generation_favorites_only": "true",
                }
            )
            app = self._create_app(directory, initialize_settings=False)
            try:
                app.update_idletasks()
                app.update()
                self.assertEqual(app.generation_candidate_revision_ids, [])
                self.assertEqual(app.generation_favorite_revision_ids, [])
                self.assertEqual(app.generation_candidate_grid.items, ())
                self.assertIsNone(app._selected_generated_revision_id)
                self.assertFalse(app.generate_favorites_only_var.get())
                self.assertIn("尚无候选", app.generate_candidates_var.get())
            finally:
                self._close_app(app)

    def test_generation_candidate_remove_is_non_destructive_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            revision_id = ""
            saved_job_id = ""
            managed_path: Path | None = None
            try:
                app.mode_buttons[ModeId.GENERATE].command()
                app.generate_prompt_var.set("准备从候选区删除的图片")
                app.generate_size_var.set("768 × 768")
                app.generate_count_var.set(1)
                app._start_mock_generation()
                job_id = app._mock_generation_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                saved_job_id = job_id
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != job_id and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                revision_id = app._selected_generated_revision_id or ""
                self.assertTrue(revision_id)
                managed_path = app.asset_repository.resolve_content(revision_id)
                app._toggle_generated_candidate_favorite()
                app._remove_generated_candidate()

                self.assertEqual(app.generation_candidate_revision_ids, [])
                self.assertEqual(app.generation_favorite_revision_ids, [])
                self.assertEqual(app.generation_candidate_grid.items, ())
                self.assertIsNone(app._selected_generated_revision_id)
                self.assertIsNone(app.mode_router.context_for(ModeId.GENERATE).revision_id)
                self.assertEqual(
                    app.asset_repository.get_revision(revision_id).revision_id,
                    revision_id,
                )
                self.assertTrue(managed_path.is_file())
                self.assertIn("安全保留", app.generate_candidates_var.get())
                app._save_config()
            finally:
                self._close_app(app)

            restored = self._create_app(directory, initialize_settings=False)
            try:
                restored.update_idletasks()
                restored.update()
                self.assertEqual(restored.generation_candidate_grid.items, ())
                self.assertEqual(restored.generation_favorite_revision_ids, [])
                self.assertEqual(
                    restored.asset_repository.get_revision(revision_id).revision_id,
                    revision_id,
                )
                self.assertIsNotNone(restored.task_tray.get(saved_job_id))
                self.assertIsNotNone(managed_path)
                assert managed_path is not None
                self.assertTrue(managed_path.is_file())
            finally:
                self._close_app(restored)

    def test_task_tray_restores_interrupted_local_task_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                app.task_tray.upsert(
                    UnifiedTaskItem(
                        task_id="conversion:interrupted",
                        kind=UnifiedTaskKind.CONVERSION,
                        title="批量转换 3 项",
                        status=UnifiedTaskStatus.RUNNING,
                        mode=ModeId.CONVERT,
                        detail="完成 1/3",
                        progress_current=1,
                        progress_total=3,
                        cancellable=True,
                    )
                )
                app._save_config()
            finally:
                self._close_app(app)

            restored = self._create_app(directory, initialize_settings=False)
            try:
                task = restored.task_tray.get("conversion:interrupted")
                self.assertIsNotNone(task)
                assert task is not None
                self.assertEqual(task.status, UnifiedTaskStatus.UNKNOWN)
                self.assertFalse(task.cancellable)
                self.assertFalse(task.retryable)
                self.assertIn("上次退出", task.detail)
            finally:
                self._close_app(restored)

    def test_generation_reference_contract_uses_pixels_and_restores_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "生成参考图.png"
            expected_pixel = (35, 145, 95)
            Image.new("RGB", (48, 32), expected_pixel).save(source)
            app = self._create_app(directory)
            reference_revision_id = ""
            try:
                app.open_path(source, confirm=False)
                reference = self._wait_for_asset(app)
                reference_revision_id = reference.revision_id
                app.mode_buttons[ModeId.GENERATE].command()
                app._use_current_generation_reference()
                self.assertEqual(
                    app.generation_reference_revision_id,
                    reference_revision_id,
                )
                self.assertIn(source.name, app.generate_reference_status_var.get())

                app.generate_prompt_var.set("保留参考图主体的离线候选")
                app.generate_negative_prompt_var.set("不要边框")
                app.generate_reference_role_var.set("内容")
                app.generate_reference_strength_var.set(100)
                app.generate_style_preset_var.set("水彩")
                app.generate_seed_enabled_var.set(True)
                app.generate_seed_var.set("20260805")
                app.generate_size_var.set("48 × 32")
                app.generate_count_var.set(1)
                app._start_mock_generation()
                job_id = app._mock_generation_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != job_id and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                selected_revision_id = app._selected_generated_revision_id
                self.assertIsNotNone(selected_revision_id)
                assert selected_revision_id is not None
                generated = app.asset_repository.get_revision(selected_revision_id)
                self.assertEqual(
                    generated.provenance.source_revision_ids,
                    (reference_revision_id,),
                )
                self.assertEqual(
                    generated.provenance.parameters["negative_prompt"],
                    "不要边框",
                )
                self.assertEqual(
                    generated.provenance.parameters["reference_role"],
                    "content",
                )
                self.assertEqual(
                    generated.provenance.parameters["reference_strength"],
                    1.0,
                )
                self.assertEqual(generated.provenance.parameters["seed"], 20260805)
                self.assertEqual(
                    generated.provenance.parameters["style_preset"],
                    "watercolor",
                )
                generated_path = app.asset_repository.resolve_content(selected_revision_id)
                with Image.open(generated_path) as output:
                    output.load()
                    self.assertEqual(output.size, (48, 32))
                    self.assertNotEqual(output.getpixel((24, 16)), (0, 0, 0))
                mode_state = app.mode_router.state_for(ModeId.GENERATE)
                self.assertEqual(
                    mode_state["reference_revision_id"],
                    reference_revision_id,
                )
                app._save_config()
            finally:
                self._close_app(app)

            restored = self._create_app(directory, initialize_settings=False)
            try:
                restored.update_idletasks()
                restored.update()
                self.assertEqual(
                    restored.generation_reference_revision_id,
                    reference_revision_id,
                )
                self.assertIsNotNone(restored._generation_reference_revision)
                self.assertEqual(restored.generate_negative_prompt_var.get(), "不要边框")
                self.assertEqual(restored.generate_reference_role_var.get(), "内容")
                self.assertEqual(restored.generate_reference_strength_var.get(), 100)
                self.assertEqual(restored.generate_style_preset_var.get(), "水彩")
                self.assertTrue(restored.generate_seed_enabled_var.get())
                self.assertEqual(restored.generate_seed_var.get(), "20260805")
                self.assertIn(source.name, restored.generate_reference_status_var.get())
                self.assertIn("参考图", restored.generate_provider_capability_var.get())
            finally:
                self._close_app(restored)

    def test_generation_reference_can_import_an_external_file_asynchronously(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "外部参考图.png"
            Image.new("RGB", (36, 24), "#7a5d88").save(source)
            app = self._create_app(directory)
            try:
                app._begin_generation_reference_import(source)
                deadline = time.monotonic() + 5
                while app._generation_reference_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNone(app._generation_reference_future)
                self.assertIsNotNone(app._generation_reference_revision)
                assert app._generation_reference_revision is not None
                self.assertEqual(
                    app.generation_reference_revision_id,
                    app._generation_reference_revision.revision_id,
                )
                self.assertIn(source.name, app.generate_reference_status_var.get())
                self.assertFalse(app.generate_reference_choose_button.instate(["disabled"]))
            finally:
                self._close_app(app)

    def test_generation_workspace_edits_and_restores_multiple_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [
                root / "内容参考.png",
                root / "风格参考.png",
                root / "待移除参考.png",
                root / "构图参考.png",
            ]
            for path, color in zip(
                sources,
                ("#d94f4f", "#4f79d9", "#62ad6f", "#e2b14d"),
                strict=True,
            ):
                Image.new("RGB", (48, 32), color).save(path)

            app = self._create_app(directory)
            retained_revision_ids: tuple[str, ...] = ()
            try:
                revisions = tuple(app.asset_repository.ensure_external(path) for path in sources)
                app._set_generation_reference(revisions[0], role="content", strength=0.25)
                app._set_generation_reference(revisions[1], role="style", strength=0.5)
                app._set_generation_reference(revisions[2], role="composition", strength=0.75)
                app._set_generation_reference(revisions[3], role="content", strength=1.0)
                self.assertEqual(app.generate_reference_listbox.size(), 4)
                self.assertTrue(app.generate_reference_choose_button.instate(["disabled"]))

                app.generate_reference_listbox.selection_clear(0, "end")
                app.generate_reference_listbox.selection_set(2)
                app._generation_reference_selected()
                app._remove_selected_generation_reference()
                self.assertEqual(app.generate_reference_listbox.size(), 3)
                self.assertFalse(app.generate_reference_choose_button.instate(["disabled"]))

                app.generate_reference_listbox.selection_clear(0, "end")
                app.generate_reference_listbox.selection_set(1)
                app._generation_reference_selected()
                app.generate_reference_role_var.set("风格")
                app.generate_reference_strength_var.set(65)
                self.assertTrue(app._update_selected_generation_reference_settings())

                retained_revision_ids = (
                    revisions[0].revision_id,
                    revisions[1].revision_id,
                    revisions[3].revision_id,
                )
                app.generate_prompt_var.set("融合三张参考图的离线候选")
                app.generate_size_var.set("48 × 32")
                app.generate_count_var.set(1)
                app._start_mock_generation()
                job_id = app._mock_generation_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != job_id and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                generated_revision_id = app._selected_generated_revision_id
                self.assertIsNotNone(generated_revision_id)
                assert generated_revision_id is not None
                generated = app.asset_repository.get_revision(generated_revision_id)
                self.assertEqual(
                    generated.provenance.source_revision_ids,
                    retained_revision_ids,
                )
                self.assertEqual(generated.provenance.parameters["reference_count"], 3)
                self.assertEqual(
                    generated.provenance.parameters["reference_1_role"],
                    "style",
                )
                self.assertEqual(
                    generated.provenance.parameters["reference_1_strength"],
                    0.65,
                )
                mode_state = app.mode_router.state_for(ModeId.GENERATE)
                self.assertEqual(
                    mode_state["reference_revision_ids"],
                    retained_revision_ids,
                )
                self.assertEqual(mode_state["reference_count"], 3)
                app._save_config()
            finally:
                self._close_app(app)

            restored = self._create_app(directory, initialize_settings=False)
            try:
                restored.update_idletasks()
                restored.update()
                self.assertEqual(restored.generate_reference_listbox.size(), 3)
                self.assertEqual(
                    tuple(
                        reference.revision.revision_id
                        for reference in restored._generation_references
                    ),
                    retained_revision_ids,
                )
                self.assertEqual(
                    tuple(reference.role for reference in restored._generation_references),
                    ("content", "style", "content"),
                )
                self.assertEqual(
                    tuple(reference.strength for reference in restored._generation_references),
                    (0.25, 0.65, 1.0),
                )
                self.assertEqual(
                    restored.generation_reference_revision_id,
                    retained_revision_ids[0],
                )
                self.assertIn("内容参考.png", restored.generate_reference_listbox.get(0))
            finally:
                self._close_app(restored)

    def test_selected_generated_candidate_creates_a_traceable_variation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                app.mode_buttons[ModeId.GENERATE].command()
                app.generate_prompt_var.set("用于变体测试的海报")
                app.generate_size_var.set("48 × 32")
                app.generate_count_var.set(1)
                app.generate_seed_enabled_var.set(True)
                app.generate_seed_var.set("80")
                app._start_mock_generation()
                first_job_id = app._mock_generation_job_id
                self.assertIsNotNone(first_job_id)
                assert first_job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != first_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                source_revision_id = app._selected_generated_revision_id
                self.assertIsNotNone(source_revision_id)
                assert source_revision_id is not None
                source = app.asset_repository.get_revision(source_revision_id)

                app._generate_selected_candidate_variation()
                variation_job_id = app._mock_generation_job_id
                self.assertIsNotNone(variation_job_id)
                self.assertNotEqual(variation_job_id, first_job_id)
                assert variation_job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != variation_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                variation_revision_id = app._selected_generated_revision_id
                self.assertIsNotNone(variation_revision_id)
                self.assertNotEqual(variation_revision_id, source_revision_id)
                assert variation_revision_id is not None
                variation = app.asset_repository.get_revision(variation_revision_id)
                self.assertNotEqual(variation.asset_id, source.asset_id)
                self.assertEqual(
                    variation.provenance.source_revision_ids,
                    (source_revision_id,),
                )
                self.assertEqual(
                    variation.provenance.parameters["variation_of_revision_id"],
                    source_revision_id,
                )
                self.assertEqual(
                    variation.provenance.parameters["variation_strength"],
                    0.35,
                )
                self.assertEqual(variation.provenance.parameters["seed"], 81)
                self.assertEqual(
                    app.generation_candidate_revision_ids[:2],
                    [variation_revision_id, source_revision_id],
                )
                mode_state = app.mode_router.state_for(ModeId.GENERATE)
                self.assertEqual(
                    mode_state["variation_of_revision_id"],
                    source_revision_id,
                )
            finally:
                self._close_app(app)

    def test_remote_generation_requires_separate_upload_and_cost_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "待上传参考图.png"
            Image.new("RGB", (36, 24), "#7a5d88").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                reference = self._wait_for_asset(app)
                remote_manifest = ProviderManifest(
                    provider_id="cloud.contract-test",
                    name="云端契约测试服务",
                    version="1",
                    capabilities=(
                        ProviderCapability.GENERATION,
                        ProviderCapability.REFERENCE_IMAGE,
                    ),
                    models=("cloud-image-v1",),
                    offline=False,
                )
                profile = GenerationProviderProfile(
                    manifest=remote_manifest,
                    model_id="cloud-image-v1",
                    display_name="云端契约测试服务",
                    estimated_cost_minor_per_image=12,
                )
                request = app._last_generation_request = GenerationRequest(
                    prompt="远程授权边界",
                    model=profile.model_id,
                    width=36,
                    height=24,
                    count=2,
                    reference_revision=reference,
                )
                requirements = AIExecutionRequirements.for_request(
                    remote_manifest,
                    request,
                    remote_image_upload=True,
                    estimated_cost_minor=profile.estimated_cost_minor(request.count),
                )
                with patch(
                    "light_image_editor.ui.main_window.messagebox.askyesno",
                    side_effect=(True, True),
                ) as confirm:
                    self.assertTrue(
                        app._authorize_generation_request(profile, request, requirements)
                    )
                self.assertEqual(confirm.call_count, 2)
                self.assertIn("上传参考图", confirm.call_args_list[0].args[0])
                self.assertIn("0.24", confirm.call_args_list[1].args[1])

                with patch(
                    "light_image_editor.ui.main_window.messagebox.askyesno",
                    return_value=False,
                ) as decline:
                    self.assertFalse(
                        app._authorize_generation_request(profile, request, requirements)
                    )
                self.assertEqual(decline.call_count, 1)
                self.assertIn("图片没有离开本机", app.generate_candidates_var.get())
            finally:
                self._close_app(app)

    def test_failed_generation_retries_only_after_explicit_user_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                profile = app.generation_provider_catalog.default()
                request = GenerationRequest(
                    prompt="首次失败后显式重试",
                    model=MOCK_MODEL_ID,
                    width=48,
                    height=32,
                    request_id="request-ui-retry-failure",
                    idempotency_key="key-ui-retry-failure",
                )
                app.mock_provider._failing_request_ids.add(request.request_id)
                snapshot = app._submit_generation_request(profile, request)
                self.assertIsNotNone(snapshot)
                assert snapshot is not None
                app._begin_generation_tracking(snapshot, request, profile)
                deadline = time.monotonic() + 5
                while (
                    app.ai_job_manager.get_job(snapshot.job_id).status is not JobStatus.FAILED
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(
                    app.ai_job_manager.get_job(snapshot.job_id).status,
                    JobStatus.FAILED,
                )
                deadline = time.monotonic() + 2
                while app.mock_retry_button.instate(["disabled"]) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertFalse(app.mock_retry_button.instate(["disabled"]))
                first_job_id = snapshot.job_id
                app._retry_failed_generation()
                retry_job_id = app._mock_generation_job_id
                self.assertIsNotNone(retry_job_id)
                self.assertNotEqual(retry_job_id, first_job_id)
                assert retry_job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._mock_generation_presented_job_id != retry_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(
                    app.ai_job_manager.get_job(retry_job_id).status,
                    JobStatus.SUCCEEDED,
                )
                self.assertNotEqual(
                    app._last_generation_request.request_id
                    if app._last_generation_request is not None
                    else "",
                    request.request_id,
                )
            finally:
                self._close_app(app)

    def test_smart_upscale_creates_child_revision_and_sends_it_to_convert(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "高清来源.png"
            Image.new("RGBA", (24, 16), (36, 92, 148, 180)).save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                source_revision = self._wait_for_asset(app)
                app.mode_buttons[ModeId.EDIT].command()
                app._show_inspector_tab("smart")
                app._start_smart_upscale(2)
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None

                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(app.ai_job_manager.get_job(job_id).status, JobStatus.SUCCEEDED)
                tray_task = app.task_tray.get(job_id)
                self.assertIsNotNone(tray_task)
                assert tray_task is not None
                self.assertEqual(tray_task.kind, UnifiedTaskKind.SMART_EDIT)
                self.assertEqual(tray_task.status, UnifiedTaskStatus.SUCCEEDED)
                self.assertEqual(result.asset_id, source_revision.asset_id)
                self.assertEqual(result.parent_revision_id, source_revision.revision_id)
                self.assertEqual(result.sequence, source_revision.sequence + 1)
                self.assertEqual((result.width, result.height), (48, 32))
                self.assertTrue(app.asset_repository.resolve_content(result.revision_id).is_file())
                self.assertIn("新的托管修订", app.smart_edit_status_var.get())

                app._compare_smart_result()
                deadline = time.monotonic() + 5
                while app._comparison_image is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(app.mode_router.active_mode, ModeId.EDIT)
                self.assertTrue(app.file_compare_var.get())
                self.assertIsNotNone(app._comparison_image)
                assert app._comparison_image is not None
                self.assertEqual(app._comparison_image.size, (48, 32))

                app._send_smart_result_to_conversion()

                self.assertEqual(app.mode_router.active_mode, ModeId.CONVERT)
                self.assertFalse(app.file_compare_var.get())
                self.assertEqual(len(app.conversion_workspace.items), 1)
                queued = app.conversion_workspace.items[0]
                self.assertEqual(queued.asset_id, result.asset_id)
                self.assertEqual(queued.revision_id, result.revision_id)
                convert_context = app.mode_router.context_for(ModeId.CONVERT)
                self.assertEqual(convert_context.asset_id, result.asset_id)
                self.assertEqual(convert_context.revision_id, result.revision_id)
            finally:
                self._close_app(app)

    def test_smart_background_removal_creates_alpha_child_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "去背景来源.png"
            source_image = Image.new("RGB", (32, 24), (250, 250, 250))
            for y in range(6, 18):
                for x in range(8, 24):
                    source_image.putpixel((x, y), (40, 115, 190))
            source_image.putpixel((4, 4), (225, 225, 225))
            source_image.save(source)
            source_image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                source_revision = self._wait_for_asset(app)
                app.mode_buttons[ModeId.EDIT].command()
                app._show_inspector_tab("smart")
                self.assertEqual(
                    app.smart_background_tolerance_spinbox.accessible_name,
                    "去背景容差",
                )
                self.assertEqual(
                    app.smart_remove_background_button.accessible_name,
                    "开始智能去背景",
                )
                app.smart_background_tolerance_var.set(20)
                app.smart_background_feather_var.set(20)

                app._start_smart_remove_background()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                result_path = app.asset_repository.resolve_content(result.revision_id)
                self.assertEqual(result.asset_id, source_revision.asset_id)
                self.assertEqual(result.parent_revision_id, source_revision.revision_id)
                self.assertEqual(result.sequence, source_revision.sequence + 1)
                self.assertEqual((result.width, result.height), (32, 24))
                self.assertEqual(result.media_type, "image/png")
                self.assertTrue(result_path.is_file())
                with Image.open(result_path) as output:
                    output.load()
                    self.assertEqual(output.mode, "RGBA")
                    self.assertEqual(output.getpixel((0, 0))[3], 0)
                    self.assertEqual(output.getpixel((16, 12))[3], 255)
                    self.assertGreater(output.getpixel((4, 4))[3], 0)
                    self.assertLess(output.getpixel((4, 4))[3], 255)

                edit_state = app.mode_router.state_for(ModeId.EDIT)
                self.assertEqual(edit_state["smart_operation"], "remove_background")
                self.assertEqual(edit_state["smart_result_operation"], "remove_background")
                self.assertIn("透明 PNG", app.smart_edit_result_var.get())

                app._discard_smart_result()
                self.assertIsNone(app._smart_edit_result_revision)
                self.assertTrue(result_path.is_file())
                self.assertTrue(app.mode_router.state_for(ModeId.EDIT)["smart_result_discarded"])
            finally:
                self._close_app(app)

    def test_smart_background_replacement_creates_solid_and_custom_child_revisions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "背景替换来源.png"
            background = root / "自定义背景.png"
            source_image = Image.new("RGB", (32, 24), (250, 250, 250))
            for y in range(6, 18):
                for x in range(8, 24):
                    source_image.putpixel((x, y), (35, 105, 185))
            source_image.save(source)
            source_image.close()
            Image.new("RGB", (12, 8), (18, 160, 90)).save(background)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app.mode_buttons[ModeId.EDIT].command()
                app._show_inspector_tab("smart")
                app.update_idletasks()
                app.update_idletasks()
                self.assertTrue(app.smart_repair_select_button.winfo_ismapped())
                self.assertTrue(app.smart_repair_apply_button.winfo_ismapped())
                self.assertEqual(
                    app.smart_background_replace_button.accessible_name,
                    "应用背景替换",
                )
                self.assertTrue(app.smart_background_replace_button.instate(("disabled",)))
                app.smart_background_tolerance_var.set(20)
                app.smart_background_feather_var.set(20)
                app._start_smart_remove_background()
                removal_job_id = app._smart_edit_job_id
                self.assertIsNotNone(removal_job_id)
                assert removal_job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._smart_edit_presented_job_id != removal_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                removal = app._smart_edit_result_revision
                self.assertIsNotNone(removal)
                assert removal is not None
                self.assertIs(app._smart_background_alpha_revision, removal)
                self.assertFalse(app.smart_background_replace_button.instate(("disabled",)))

                app.smart_background_replace_mode_var.set("纯色背景")
                app.smart_background_replace_color_var.set("#ffcc00")
                app._apply_smart_background_replacement()
                deadline = time.monotonic() + 5
                while (
                    app._smart_background_compose_future is not None and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                solid = app._smart_edit_result_revision
                self.assertIsNotNone(solid)
                assert solid is not None
                self.assertNotEqual(solid.revision_id, removal.revision_id)
                self.assertEqual(solid.asset_id, source_revision.asset_id)
                self.assertEqual(solid.parent_revision_id, removal.revision_id)
                self.assertEqual(
                    solid.provenance.parameters["operation"],
                    "replace_background",
                )
                self.assertEqual(solid.provenance.parameters["background_kind"], "color")
                self.assertEqual(solid.provenance.parameters["background_color"], "#ffcc00")
                solid_path = app.asset_repository.resolve_content(solid.revision_id)
                with Image.open(solid_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((0, 0)), (255, 204, 0, 255))
                    self.assertEqual(output.getpixel((16, 12))[:3], (35, 105, 185))
                    self.assertTrue(all(pixel[3] == 255 for pixel in output.getdata()))

                app.smart_background_replace_mode_var.set("自定义图片")
                app.smart_background_replace_image_path = background
                app.smart_background_replace_image_var.set(f"背景图：{background.name}")
                app._apply_smart_background_replacement()
                deadline = time.monotonic() + 5
                while (
                    app._smart_background_compose_future is not None and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                custom = app._smart_edit_result_revision
                self.assertIsNotNone(custom)
                assert custom is not None
                self.assertNotEqual(custom.revision_id, solid.revision_id)
                self.assertEqual(custom.parent_revision_id, removal.revision_id)
                self.assertEqual(custom.sequence, solid.sequence + 1)
                self.assertEqual(custom.provenance.parameters["background_kind"], "image")
                background_hash = custom.provenance.parameters["background_sha256"]
                self.assertIsInstance(background_hash, str)
                self.assertEqual(len(background_hash), 64)
                custom_path = app.asset_repository.resolve_content(custom.revision_id)
                with Image.open(custom_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((0, 0)), (18, 160, 90, 255))
                    self.assertEqual(output.getpixel((16, 12))[:3], (35, 105, 185))
                self.assertTrue(solid_path.is_file())
                self.assertIn("不透明 PNG", app.smart_edit_result_var.get())
                edit_state = app.mode_router.state_for(ModeId.EDIT)
                self.assertEqual(edit_state["smart_result_operation"], "replace_background")
                self.assertEqual(edit_state["smart_background_kind"], "image")

                app._send_smart_result_to_conversion()
                self.assertEqual(app.mode_router.active_mode, ModeId.CONVERT)
                queued = app.conversion_workspace.items[-1]
                self.assertEqual(queued.revision_id, custom.revision_id)
            finally:
                self._close_app(app)

    def test_smart_background_replacement_validation_and_pending_cancel_are_safe(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "背景替换取消来源.png"
            source_image = Image.new("RGB", (24, 20), (248, 248, 248))
            for y in range(5, 15):
                for x in range(6, 18):
                    source_image.putpixel((x, y), (70, 120, 185))
            source_image.save(source)
            source_image.close()
            app = self._create_app(directory)
            release = threading.Event()
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                app.smart_background_tolerance_var.set(20)
                app.smart_background_feather_var.set(20)
                app._start_smart_remove_background()
                removal_job_id = app._smart_edit_job_id
                assert removal_job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._smart_edit_presented_job_id != removal_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)
                removal = app._smart_edit_result_revision
                self.assertIsNotNone(removal)
                assert removal is not None

                app.smart_background_replace_color_var.set("无效颜色")
                app._apply_smart_background_replacement()
                self.assertIsNone(app._smart_background_compose_future)
                self.assertIn("#RRGGBB", app.smart_edit_status_var.get())
                self.assertIs(app._smart_edit_result_revision, removal)

                started = threading.Event()

                def block_asset_executor() -> None:
                    started.set()
                    release.wait(3)

                blocker = app._asset_executor.submit(block_asset_executor)
                self.assertTrue(started.wait(1))
                app.smart_background_replace_color_var.set("#334455")
                app._apply_smart_background_replacement()
                pending = app._smart_background_compose_future
                self.assertIsNotNone(pending)
                assert pending is not None
                self.assertFalse(pending.running())
                self.assertFalse(app.smart_edit_cancel_button.instate(("disabled",)))
                app._cancel_smart_edit()
                self.assertTrue(pending.cancelled())
                self.assertIsNone(app._smart_background_compose_future)
                self.assertIn("背景替换已取消", app.smart_edit_status_var.get())
                self.assertIs(app._smart_edit_result_revision, removal)
                self.assertEqual(app.asset_repository.children_of(removal.revision_id), ())
                release.set()
                blocker.result(timeout=2)
            finally:
                release.set()
                self._close_app(app)

    def test_smart_region_repair_creates_real_child_revision_and_sends_to_convert(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "物体擦除来源.png"
            image = Image.new("RGBA", (80, 60))
            for y in range(60):
                for x in range(80):
                    image.putpixel((x, y), (40 + x, 60 + y, 120, 180 + x // 2))
            for y in range(20, 40):
                for x in range(28, 52):
                    image.putpixel((x, y), (245, 25, 35, 255))
            image.save(source)
            image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                self.assertEqual(
                    app.smart_repair_select_button.accessible_name,
                    "在画布框选需要擦除的物体",
                )
                self.assertTrue(app.smart_repair_apply_button.instate(("disabled",)))

                app._select_smart_repair_region()
                self.assertTrue(app._smart_repair_selecting_region)
                self.assertEqual(app.annotation_tool_var.get(), "crop")
                app.crop_box = (28, 20, 52, 40)
                app._finish_crop(None)  # type: ignore[arg-type]
                self.assertFalse(app._smart_repair_selecting_region)
                self.assertEqual(app._smart_repair_region_box, (28, 20, 52, 40))
                self.assertIn("24 × 20", app.smart_repair_region_var.get())
                self.assertFalse(app.smart_repair_apply_button.instate(("disabled",)))

                app.smart_repair_feather_var.set(0)
                app._start_smart_region_repair()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                revision = app._smart_edit_result_revision
                self.assertIsNotNone(revision)
                assert revision is not None
                self.assertEqual(revision.asset_id, source_revision.asset_id)
                self.assertEqual(revision.parent_revision_id, source_revision.revision_id)
                self.assertEqual(
                    revision.provenance.parameters["operation"],
                    "erase_region",
                )
                self.assertEqual(revision.provenance.parameters["feather"], 0)
                output_path = app.asset_repository.resolve_content(revision.revision_id)
                with Image.open(output_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((4, 4)), (44, 64, 120, 182))
                    center = output.getpixel((40, 30))
                    self.assertLess(abs(center[0] - 80), 2)
                    self.assertLess(abs(center[1] - 90), 3)
                    self.assertNotEqual(center, (245, 25, 35, 255))
                with Image.open(source) as original:
                    original.load()
                    self.assertEqual(original.getpixel((40, 30)), (245, 25, 35, 255))

                app._send_smart_result_to_conversion()
                self.assertEqual(app.mode_router.active_mode, ModeId.CONVERT)
                queued = app.conversion_workspace.items[-1]
                self.assertEqual(queued.asset_id, revision.asset_id)
                self.assertEqual(queued.revision_id, revision.revision_id)
            finally:
                self._close_app(app)

    def test_smart_selection_detects_adjustable_region_and_feeds_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "智能选区来源.png"
            image = Image.new("RGBA", (240, 160), (245, 245, 245, 255))
            ImageDraw.Draw(image).rounded_rectangle(
                (70, 38, 174, 132),
                radius=16,
                fill=(36, 104, 216, 255),
            )
            image.save(source)
            image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app.mode_buttons[ModeId.EDIT].command()
                app._show_inspector_tab("smart")
                self.assertEqual(
                    app.smart_repair_detect_button.accessible_name,
                    "离线智能识别需要擦除的主体区域",
                )
                self.assertEqual(app.smart_repair_detect_button.winfo_manager(), "grid")
                self.assertEqual(
                    app.asset_repository.children_of(source_revision.revision_id),
                    (),
                )

                app._detect_smart_repair_region()
                deadline = time.monotonic() + 5
                while app._smart_selection_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                detected = app._smart_repair_region_box
                self.assertIsNotNone(detected)
                assert detected is not None
                self.assertEqual(app.crop_box, detected)
                self.assertTrue(app._smart_repair_selecting_region)
                self.assertEqual(app.annotation_tool_var.get(), "crop")
                self.assertLessEqual(detected[0], 70)
                self.assertLessEqual(detected[1], 38)
                self.assertGreaterEqual(detected[2], 175)
                self.assertGreaterEqual(detected[3], 133)
                self.assertIn("背景对比", app.smart_repair_region_var.get())
                self.assertIn("可拖动边框校正", app.smart_edit_status_var.get())
                self.assertTrue(app.canvas.find_withtag("smart-selection-mask"))
                self.assertIsNotNone(app._smart_selection_mask_preview)
                self.assertEqual(
                    app.asset_repository.children_of(source_revision.revision_id),
                    (),
                )

                app.crop_box = (detected[0] + 1, detected[1], detected[2], detected[3])
                app._finish_crop(None)  # type: ignore[arg-type]
                self.assertFalse(app._smart_repair_selecting_region)
                self.assertIsNone(app._smart_selection_mask_preview)
                self.assertFalse(app.canvas.find_withtag("smart-selection-mask"))
                self.assertIn("矩形擦除区域", app.smart_edit_status_var.get())
                app.smart_repair_feather_var.set(0)
                app._start_smart_region_repair()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.parent_revision_id, source_revision.revision_id)
                self.assertEqual(result.provenance.parameters["operation"], "erase_region")
                self.assertNotIn("mask_sha256", result.provenance.parameters)
                output_path = app.asset_repository.resolve_content(result.revision_id)
                with Image.open(output_path) as output:
                    output.load()
                    self.assertNotEqual(output.getpixel((120, 80)), (36, 104, 216, 255))
                    self.assertEqual(output.getpixel((230, 150)), (245, 245, 245, 255))
            finally:
                self._close_app(app)

    def test_smart_selection_cancel_and_new_picture_ignore_late_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "选区分析中.png"
            second = root / "选区切换后.png"
            Image.new("RGB", (160, 100), "white").save(first)
            Image.new("RGB", (120, 90), "#5f8bd5").save(second)
            starts = (threading.Event(), threading.Event())
            releases = (threading.Event(), threading.Event())
            call_index = 0

            def slow_selection(
                *_args: object,
                **_kwargs: object,
            ) -> tuple[SmartSelectionSuggestion, ...]:
                nonlocal call_index
                index = call_index
                call_index += 1
                starts[index].set()
                if not releases[index].wait(timeout=5):
                    raise TimeoutError("智能选区测试等待超时")
                return (
                    SmartSelectionSuggestion(
                        (0.1, 0.1, 0.8, 0.8),
                        0.9,
                        0.3,
                        "contrast",
                    ),
                )

            app = self._create_app(directory)
            try:
                app.open_path(first, confirm=False)
                first_revision = self._wait_for_asset(app)
                with patch(
                    "light_image_editor.ui.main_window.detect_smart_selection",
                    side_effect=slow_selection,
                ):
                    app._detect_smart_repair_region()
                    self.assertTrue(starts[0].wait(timeout=2))
                    app._cancel_smart_edit()
                    self.assertIsNone(app._smart_selection_future)
                    self.assertIsNone(app._smart_repair_region_box)
                    self.assertIsNone(app._smart_selection_mask_preview)
                    self.assertFalse(app.canvas.find_withtag("smart-selection-mask"))
                    releases[0].set()
                    for _index in range(8):
                        app.update()
                        time.sleep(0.01)

                    app._detect_smart_repair_region()
                    self.assertTrue(starts[1].wait(timeout=2))
                    app.open_path(second, confirm=False)
                    deadline = time.monotonic() + 5
                    while (
                        app.image_path is None or not app.image_path.samefile(second)
                    ) and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)
                    second_revision = self._wait_for_asset(app)
                    releases[1].set()
                    for _index in range(8):
                        app.update()
                        time.sleep(0.01)

                self.assertIsNone(app._smart_selection_future)
                self.assertIsNone(app._smart_repair_region_box)
                self.assertEqual(
                    app.asset_repository.children_of(first_revision.revision_id),
                    (),
                )
                self.assertEqual(
                    app.asset_repository.children_of(second_revision.revision_id),
                    (),
                )
            finally:
                releases[0].set()
                releases[1].set()
                self._close_app(app)

    def test_smart_selection_cycles_multiple_subjects_and_repairs_only_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "多主体智能选区.png"
            image = Image.new("RGBA", (320, 180), (250, 250, 250, 255))
            draw = ImageDraw.Draw(image)
            draw.rectangle((24, 30, 144, 150), fill=(36, 95, 210, 255))
            draw.ellipse((224, 62, 288, 126), fill=(220, 60, 80, 255))
            image.save(source)
            image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                app._detect_smart_repair_region()
                deadline = time.monotonic() + 5
                while app._smart_selection_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                self.assertEqual(len(app._smart_selection_suggestions), 2)
                self.assertEqual(app._smart_selection_index, 0)
                first_box = app._smart_repair_region_box
                self.assertIsNotNone(first_box)
                assert first_box is not None
                self.assertLess(first_box[0], 100)
                self.assertIn("候选 1/2", app.smart_repair_region_var.get())
                self.assertFalse(app.smart_repair_next_button.instate(("disabled",)))

                app._select_next_smart_repair_suggestion()
                second_box = app._smart_repair_region_box
                self.assertIsNotNone(second_box)
                assert second_box is not None
                self.assertGreater(second_box[0], 180)
                self.assertNotEqual(second_box, first_box)
                self.assertEqual(app.crop_box, second_box)
                self.assertIn("候选 2/2", app.smart_repair_region_var.get())
                self.assertEqual(
                    app.asset_repository.children_of(source_revision.revision_id),
                    (),
                )

                app.smart_mask_brush_size_var.set(12)
                center_x, center_y = app._image_to_canvas(256, 94)
                app._activate_smart_mask_brush("remove")
                app._canvas_press(SimpleNamespace(x=center_x, y=center_y))
                self.assertTrue(app.canvas.find_withtag("smart-mask-brush"))
                app._canvas_release(SimpleNamespace(x=center_x, y=center_y))
                self.assertFalse(app.canvas.find_withtag("smart-mask-brush"))
                self.assertEqual(len(app._smart_mask_strokes), 1)
                self.assertIsNotNone(app._smart_selection_refined_mask_rle)
                self.assertTrue(app.canvas.find_withtag("smart-selection-mask"))
                removed_mask = app._smart_selection_refined_mask_rle
                self.assertFalse(app.smart_mask_undo_button.instate(("disabled",)))
                self.assertFalse(app.smart_mask_reset_button.instate(("disabled",)))

                app._activate_smart_mask_brush("add")
                app._canvas_press(SimpleNamespace(x=center_x, y=center_y))
                app._canvas_release(SimpleNamespace(x=center_x, y=center_y))
                self.assertEqual(len(app._smart_mask_strokes), 2)
                app._undo_smart_mask_stroke()
                self.assertEqual(len(app._smart_mask_strokes), 1)
                self.assertEqual(app._smart_selection_refined_mask_rle, removed_mask)

                app._smart_repair_region_box = app._smart_selection_mask_box
                app.smart_repair_feather_var.set(0)
                app._start_smart_region_repair()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.provenance.parameters["operation"], "erase_mask")
                self.assertEqual(result.provenance.parameters["mask_refined"], True)
                self.assertEqual(result.provenance.parameters["mask_stroke_count"], 1)
                self.assertEqual(result.provenance.parameters["mask_point_count"], 1)
                self.assertIn("mask_sha256", result.provenance.parameters)
                self.assertNotIn("mask_rle", result.provenance.parameters)
                output_path = app.asset_repository.resolve_content(result.revision_id)
                with Image.open(output_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((80, 90)), (36, 95, 210, 255))
                    self.assertEqual(output.getpixel((256, 94)), (220, 60, 80, 255))
                    self.assertNotEqual(output.getpixel((256, 110)), (220, 60, 80, 255))
                    self.assertEqual(output.getpixel((224, 62)), (250, 250, 250, 255))
                with Image.open(source) as original:
                    original.load()
                    self.assertEqual(original.getpixel((256, 94)), (220, 60, 80, 255))

                app._select_next_smart_repair_suggestion()
                self.assertEqual(app._smart_selection_index, 0)
                self.assertEqual(app._smart_mask_strokes, [])
                self.assertIsNone(app._smart_selection_refined_mask_rle)
                self.assertTrue(app.smart_mask_undo_button.instate(("disabled",)))
                self.assertTrue(app.smart_mask_reset_button.instate(("disabled",)))
            finally:
                self._close_app(app)

    def test_touching_color_split_candidate_repairs_only_selected_visible_object(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "相接重叠主体修复.png"
            image = Image.new("RGBA", (260, 160), (250, 250, 250, 255))
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 35, 135, 130), fill=(36, 95, 210, 255))
            draw.ellipse((110, 45, 210, 135), fill=(220, 60, 80, 255))
            image.save(source)
            image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                app._detect_smart_repair_region()
                deadline = time.monotonic() + 5
                while app._smart_selection_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                suggestions = app._smart_selection_suggestions
                self.assertGreaterEqual(len(suggestions), 3)
                target_index = -1
                for index, suggestion in enumerate(suggestions):
                    if suggestion.selection_kind != "color_split":
                        continue
                    mask = decode_selection_mask(
                        suggestion.mask_rle or "",
                        suggestion.mask_size or (0, 0),
                        target_size=(260, 160),
                    )
                    try:
                        if mask.getpixel((175, 80)) and not mask.getpixel((60, 80)):
                            target_index = index
                            break
                    finally:
                        mask.close()
                self.assertGreaterEqual(target_index, 1)
                while app._smart_selection_index != target_index:
                    app._select_next_smart_repair_suggestion()
                self.assertIn("颜色分离", app.smart_repair_region_var.get())

                app.smart_repair_feather_var.set(0)
                app._start_smart_region_repair()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.parent_revision_id, source_revision.revision_id)
                self.assertEqual(result.provenance.parameters["operation"], "erase_mask")
                output_path = app.asset_repository.resolve_content(result.revision_id)
                with Image.open(output_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((60, 80)), (36, 95, 210, 255))
                    self.assertNotEqual(output.getpixel((175, 80)), (220, 60, 80, 255))
                    self.assertEqual(output.getpixel((240, 145)), (250, 250, 250, 255))
            finally:
                self._close_app(app)

    def test_same_color_shape_split_repairs_only_selected_lobe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "同色中等宽接触主体修复.png"
            image = Image.new("RGBA", (260, 160), (250, 250, 250, 255))
            draw = ImageDraw.Draw(image)
            draw.ellipse((30, 35, 125, 130), fill=(36, 95, 210, 255))
            draw.ellipse((100, 35, 195, 130), fill=(36, 95, 210, 255))
            image.save(source)
            image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                app._detect_smart_repair_region()
                deadline = time.monotonic() + 5
                while app._smart_selection_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                suggestions = app._smart_selection_suggestions
                target_index = -1
                for index, suggestion in enumerate(suggestions):
                    if suggestion.selection_kind != "shape_split":
                        continue
                    mask = decode_selection_mask(
                        suggestion.mask_rle or "",
                        suggestion.mask_size or (0, 0),
                        target_size=(260, 160),
                    )
                    try:
                        if mask.getpixel((170, 80)) and not mask.getpixel((75, 80)):
                            target_index = index
                            break
                    finally:
                        mask.close()
                self.assertGreaterEqual(target_index, 1)
                while app._smart_selection_index != target_index:
                    app._select_next_smart_repair_suggestion()
                self.assertIn("形状分离", app.smart_repair_region_var.get())

                app.smart_repair_feather_var.set(0)
                app._start_smart_region_repair()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.parent_revision_id, source_revision.revision_id)
                self.assertEqual(result.provenance.parameters["operation"], "erase_mask")
                output_path = app.asset_repository.resolve_content(result.revision_id)
                with Image.open(output_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((75, 80)), (36, 95, 210, 255))
                    self.assertNotEqual(output.getpixel((170, 80)), (36, 95, 210, 255))
                    self.assertEqual(output.getpixel((240, 145)), (250, 250, 250, 255))
                with Image.open(source) as original:
                    original.load()
                    self.assertEqual(original.getpixel((170, 80)), (36, 95, 210, 255))
            finally:
                self._close_app(app)

    def test_smart_edit_import_rejects_wrong_lineage_corruption_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "智能入库来源.png"
            other = root / "其他资源.png"
            Image.new("RGB", (32, 24), "#f2f2f2").save(source)
            Image.new("RGB", (16, 16), "#713b82").save(other)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                other_revision = app.asset_repository.import_external(other)
                provider_id = app.mock_provider.manifest.provider_id
                request_id = "request-smart-import-validation"

                def snapshot_for(
                    path: Path,
                    provenance: Provenance,
                    *,
                    suffix: str,
                ) -> ManagedJobSnapshot:
                    provider_revision = AssetRevision.from_content_uri(
                        asset_id=f"asset_provider_{suffix}",
                        sequence=1,
                        content_uri=path.resolve().as_uri(),
                        media_type="image/png",
                        provenance=provenance,
                        revision_id=f"rev_provider_{suffix}",
                    )
                    record = AssetRecord(
                        name=f"provider-{suffix}.png",
                        revisions=(provider_revision,),
                        asset_id=provider_revision.asset_id,
                    )
                    return ManagedJobSnapshot(
                        job_id=f"local_job_{suffix}",
                        request_kind=JobRequestKind.EDIT,
                        request_id=request_id,
                        provider_id=provider_id,
                        idempotency_key=f"key-{suffix}",
                        request_fingerprint="a" * 64,
                        status=JobStatus.SUCCEEDED,
                        outputs=(record,),
                    )

                valid_provenance = Provenance(
                    origin="content_edit",
                    actor=provider_id,
                    source_revision_ids=(source_revision.revision_id,),
                    request_id=request_id,
                    provider_id=provider_id,
                    model=MOCK_MODEL_ID,
                    parameters={"operation": "remove_background"},
                )
                valid_result = root / "有效候选.png"
                Image.new("RGBA", (32, 24), (20, 80, 140, 0)).save(valid_result)
                wrong_lineage = replace(
                    valid_provenance,
                    source_revision_ids=(other_revision.revision_id,),
                )
                with self.assertRaisesRegex(ValueError, "源修订血缘不匹配"):
                    app._import_smart_edit_candidate(
                        snapshot_for(valid_result, wrong_lineage, suffix="lineage"),
                        source_revision.revision_id,
                    )

                with self.assertRaisesRegex(ValueError, "请求身份不匹配"):
                    app._import_smart_edit_candidate(
                        snapshot_for(
                            valid_result,
                            replace(valid_provenance, request_id="another-request"),
                            suffix="request",
                        ),
                        source_revision.revision_id,
                    )
                with self.assertRaisesRegex(ValueError, "服务身份不匹配"):
                    app._import_smart_edit_candidate(
                        snapshot_for(
                            valid_result,
                            replace(valid_provenance, provider_id="another-provider"),
                            suffix="provider",
                        ),
                        source_revision.revision_id,
                    )
                with self.assertRaisesRegex(ValueError, "操作类型无效"):
                    app._import_smart_edit_candidate(
                        snapshot_for(
                            valid_result,
                            replace(
                                valid_provenance,
                                parameters={"operation": "unknown-edit"},
                            ),
                            suffix="operation",
                        ),
                        source_revision.revision_id,
                    )

                corrupt_result = root / "损坏候选.png"
                corrupt_result.write_bytes(b"not a png")
                with self.assertRaises(AssetContentValidationError):
                    app._import_smart_edit_candidate(
                        snapshot_for(corrupt_result, valid_provenance, suffix="corrupt"),
                        source_revision.revision_id,
                    )

                oversized_result = root / "超大候选.png"
                oversized = Image.effect_noise((96, 96), 100).convert("RGBA")
                oversized.save(oversized_result)
                oversized.close()
                source_bytes = source.stat().st_size
                self.assertGreater(oversized_result.stat().st_size, source_bytes)
                app.asset_repository.max_content_bytes = source_bytes + 16
                with self.assertRaises(AssetContentTooLargeError):
                    app._import_smart_edit_candidate(
                        snapshot_for(oversized_result, valid_provenance, suffix="oversize"),
                        source_revision.revision_id,
                    )

                self.assertEqual(app.asset_repository.children_of(source_revision.revision_id), ())
                self.assertEqual(app.asset_repository.children_of(other_revision.revision_id), ())
            finally:
                self._close_app(app)

    def test_alpha_brush_refinement_creates_child_and_sends_it_to_convert(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "边缘细化来源.png"
            source_image = Image.new("RGB", (64, 48), (250, 250, 250))
            for y in range(12, 36):
                for x in range(16, 48):
                    source_image.putpixel((x, y), (45, 105, 185))
            source_image.save(source)
            source_image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app.mode_buttons[ModeId.EDIT].command()
                app._show_inspector_tab("smart")
                app.smart_background_tolerance_var.set(20)
                app.smart_background_feather_var.set(20)
                app._start_smart_remove_background()
                removal_job_id = app._smart_edit_job_id
                self.assertIsNotNone(removal_job_id)
                assert removal_job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._smart_edit_presented_job_id != removal_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                removal_revision = app._smart_edit_result_revision
                self.assertIsNotNone(removal_revision)
                assert removal_revision is not None
                removal_path = app.asset_repository.resolve_content(removal_revision.revision_id)
                self.assertFalse(app.smart_alpha_preserve_button.instate(["disabled"]))
                self.assertTrue(app.smart_alpha_apply_button.instate(["disabled"]))
                with Image.open(removal_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((6, 6))[3], 0)
                    self.assertEqual(output.getpixel((32, 24))[3], 255)

                app.smart_alpha_brush_size_var.set(8)
                preserve_x, preserve_y = app._image_to_canvas(6, 6)
                app._activate_smart_alpha_brush("preserve")
                app._canvas_press(SimpleNamespace(x=preserve_x, y=preserve_y))
                app._canvas_release(SimpleNamespace(x=preserve_x, y=preserve_y))

                remove_x, remove_y = app._image_to_canvas(32, 24)
                app._activate_smart_alpha_brush("remove")
                app._canvas_press(SimpleNamespace(x=remove_x, y=remove_y))
                app._canvas_release(SimpleNamespace(x=remove_x, y=remove_y))
                self.assertEqual(len(app._smart_alpha_strokes), 2)
                self.assertFalse(app.smart_alpha_apply_button.instate(["disabled"]))
                self.assertTrue(app.canvas.find_withtag("smart-alpha-brush"))

                app._apply_smart_alpha_refinement()
                refinement_job_id = app._smart_edit_job_id
                self.assertIsNotNone(refinement_job_id)
                assert refinement_job_id is not None
                self.assertNotEqual(refinement_job_id, removal_job_id)
                deadline = time.monotonic() + 8
                while (
                    app._smart_edit_presented_job_id != refinement_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                refined_revision = app._smart_edit_result_revision
                self.assertIsNotNone(refined_revision)
                assert refined_revision is not None
                refined_path = app.asset_repository.resolve_content(refined_revision.revision_id)
                self.assertEqual(refined_revision.asset_id, source_revision.asset_id)
                self.assertEqual(
                    refined_revision.parent_revision_id,
                    removal_revision.revision_id,
                )
                self.assertEqual(
                    refined_revision.provenance.parameters["operation"],
                    "refine_alpha",
                )
                self.assertTrue(removal_path.is_file())
                with Image.open(removal_path) as previous_output:
                    previous_output.load()
                    self.assertEqual(previous_output.getpixel((6, 6))[3], 0)
                    self.assertEqual(previous_output.getpixel((32, 24))[3], 255)
                with Image.open(refined_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((6, 6))[3], 255)
                    self.assertEqual(output.getpixel((32, 24))[3], 0)
                self.assertFalse(app.canvas.find_withtag("smart-alpha-brush"))
                self.assertIn("边缘细化已应用", app.smart_alpha_refinement_var.get())

                app._send_smart_result_to_conversion()
                self.assertEqual(app.mode_router.active_mode, ModeId.CONVERT)
                self.assertEqual(len(app.conversion_workspace.items), 1)
                queued = app.conversion_workspace.items[0]
                self.assertEqual(queued.asset_id, refined_revision.asset_id)
                self.assertEqual(queued.revision_id, refined_revision.revision_id)
            finally:
                self._close_app(app)

    def test_opening_another_picture_clears_stale_alpha_brush_strokes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "旧去背景图片.png"
            second = root / "新去背景图片.png"
            Image.new("RGB", (48, 32), "white").save(first)
            Image.new("RGB", (40, 30), "#5f8bd5").save(second)
            app = self._create_app(directory)
            try:
                app.open_path(first, confirm=False)
                first_revision = self._wait_for_asset(app)
                app._smart_background_alpha_revision = first_revision
                app._smart_repair_region_box = (4, 4, 20, 20)
                app._smart_repair_selecting_region = True
                app._update_smart_edit_buttons()
                self.assertFalse(app.smart_background_replace_button.instate(("disabled",)))
                app._smart_alpha_brush_active = True
                app._smart_alpha_strokes.append(
                    AlphaBrushStroke(
                        mode="preserve",
                        radius=0.05,
                        points=((0.2, 0.2), (0.3, 0.3)),
                    )
                )
                app._draw_smart_alpha_strokes()
                self.assertTrue(app.canvas.find_withtag("smart-alpha-brush"))

                app.open_path(second, confirm=False)
                deadline = time.monotonic() + 5
                while (
                    app.image_path is None or not app.image_path.samefile(second)
                ) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                self.assertFalse(app._smart_alpha_brush_active)
                self.assertEqual(app._smart_alpha_strokes, [])
                self.assertEqual(app._smart_alpha_current_points, [])
                self.assertFalse(app.canvas.find_withtag("smart-alpha-brush"))
                self.assertIsNone(app._smart_background_alpha_revision)
                self.assertIsNone(app._smart_repair_region_box)
                self.assertFalse(app._smart_repair_selecting_region)
                self.assertTrue(app.smart_repair_apply_button.instate(("disabled",)))
                self.assertTrue(app.smart_background_replace_button.instate(("disabled",)))
                self.assertEqual(
                    app.smart_alpha_refinement_var.get(),
                    "边缘画笔：等待去背景结果",
                )
            finally:
                self._close_app(app)

    def test_smart_text_replacement_can_be_corrected_and_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "旧文字.png"
            source_image = Image.new("RGBA", (160, 80), (246, 244, 240, 255))
            for y in range(20, 60):
                for x in range(20, 140):
                    source_image.putpixel((x, y), (12, 12, 12, 255))
            source_image.save(source)
            source_image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                source_revision = self._wait_for_asset(app)
                app.mode_buttons[ModeId.EDIT].command()
                app._show_inspector_tab("smart")
                self.assertEqual(
                    app.smart_text_replace_button.accessible_name,
                    "修复背景并替换文字",
                )
                self.assertEqual(
                    app.smart_text_curve_spinbox.accessible_name,
                    "替换文字弧度百分比",
                )
                self.assertEqual(app.smart_text_effect_box.accessible_name, "替换文字字效")
                self.assertEqual(
                    app.smart_text_effect_size_spinbox.accessible_name,
                    "替换文字字效强度",
                )

                app._select_smart_text_region()
                self.assertEqual(app.annotation_tool_var.get(), "crop")
                app.crop_box = (20, 20, 140, 60)
                app._finish_crop(SimpleNamespace())
                self.assertEqual(app._smart_text_region_box, (20, 20, 140, 60))
                self.assertIn("120 × 40", app.smart_text_region_var.get())
                app.smart_text_value_var.set("新标题")
                app.smart_text_font_var.set("微软雅黑")
                app.smart_text_font_size_var.set(36)
                app.smart_text_color_var.set("#c62828")
                app.smart_text_align_var.set("居中")
                app.smart_text_rotation_var.set(0)
                app.smart_text_curve_var.set(65)
                app.smart_text_effect_var.set("描边")
                app.smart_text_effect_color_var.set("#1565c0")
                app.smart_text_effect_size_var.set(3)

                app._start_smart_text_replacement()
                first_job_id = app._smart_edit_job_id
                self.assertIsNotNone(first_job_id)
                assert first_job_id is not None
                deadline = time.monotonic() + 8
                while (
                    app._smart_edit_presented_job_id != first_job_id and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                first_result = app._smart_edit_result_revision
                self.assertIsNotNone(first_result)
                assert first_result is not None
                first_path = app.asset_repository.resolve_content(first_result.revision_id)
                self.assertEqual(first_result.asset_id, source_revision.asset_id)
                self.assertEqual(first_result.parent_revision_id, source_revision.revision_id)
                self.assertEqual((first_result.width, first_result.height), (160, 80))
                self.assertEqual(first_result.provenance.parameters["visual_effect"], "outline")
                self.assertEqual(first_result.provenance.parameters["effect_color"], "#1565c0")
                self.assertEqual(first_result.provenance.parameters["effect_size"], 3)
                with Image.open(first_path) as output:
                    output.load()
                    self.assertEqual(output.getpixel((21, 21)), (246, 244, 240, 255))
                    region_pixels = tuple(output.crop((20, 20, 140, 60)).getdata())
                    self.assertTrue(
                        any(
                            red > 100 and red > green * 1.5
                            for red, green, _blue, alpha in region_pixels
                            if alpha
                        )
                    )

                app.smart_text_value_var.set("二次修改")
                app.smart_text_align_var.set("左对齐")
                app.smart_text_curve_var.set(-35)
                app.smart_text_effect_var.set("线性渐变")
                app.smart_text_effect_color_var.set("#0080ff")
                app.smart_text_effect_size_var.set(0)
                app._start_smart_text_replacement()
                second_job_id = app._smart_edit_job_id
                self.assertIsNotNone(second_job_id)
                assert second_job_id is not None
                self.assertNotEqual(first_job_id, second_job_id)
                deadline = time.monotonic() + 8
                while (
                    app._smart_edit_presented_job_id != second_job_id
                    and time.monotonic() < deadline
                ):
                    app.update()
                    time.sleep(0.01)

                second_result = app._smart_edit_result_revision
                self.assertIsNotNone(second_result)
                assert second_result is not None
                self.assertNotEqual(second_result.revision_id, first_result.revision_id)
                self.assertEqual(second_result.sequence, first_result.sequence + 1)
                self.assertTrue(first_path.is_file())
                edit_state = app.mode_router.state_for(ModeId.EDIT)
                self.assertEqual(edit_state["smart_operation"], "replace_text")
                self.assertEqual(edit_state["smart_parameters"]["text"], "二次修改")
                self.assertEqual(edit_state["smart_parameters"]["align"], "left")
                self.assertEqual(edit_state["smart_parameters"]["curve"], -0.35)
                self.assertEqual(edit_state["smart_parameters"]["visual_effect"], "gradient")
                self.assertEqual(edit_state["smart_parameters"]["effect_color"], "#0080ff")
                self.assertEqual(edit_state["smart_parameters"]["effect_size"], 0)

                self.assertTrue(app.smart_text_history.can_undo)
                app._undo_smart_text_draft()
                self.assertEqual(app.smart_text_value_var.get(), "新标题")
                self.assertEqual(app.smart_text_align_var.get(), "居中")
                self.assertEqual(app.smart_text_curve_var.get(), 65)
                self.assertEqual(app.smart_text_effect_var.get(), "描边")
                self.assertEqual(app.smart_text_effect_color_var.get(), "#1565c0")
                self.assertEqual(app.smart_text_effect_size_var.get(), 3)
                self.assertTrue(app.smart_text_history.can_redo)
                app._redo_smart_text_draft()
                self.assertEqual(app.smart_text_value_var.get(), "二次修改")
                self.assertEqual(app.smart_text_align_var.get(), "左对齐")
                self.assertEqual(app.smart_text_curve_var.get(), -35)
                self.assertEqual(app.smart_text_effect_var.get(), "线性渐变")
                self.assertEqual(app.smart_text_effect_color_var.get(), "#0080ff")
                self.assertEqual(app.smart_text_effect_size_var.get(), 0)
            finally:
                self._close_app(app)

    def test_font_recommendation_ranks_real_font_and_waits_for_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            font_path = Path("C:/Windows/Fonts/msyh.ttc")
            if not font_path.is_file():
                self.skipTest("本机没有微软雅黑字体")
            source = root / "字体推荐.png"
            image = Image.new("RGB", (300, 120), "#f7f5f0")
            draw = ImageDraw.Draw(image)
            draw.text(
                (18, 20),
                "轻量图片",
                font=ImageFont.truetype(str(font_path), 56),
                fill="#202020",
            )
            image.save(source)
            image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                app._smart_text_region_box = (0, 0, 300, 120)
                app.crop_box = app._smart_text_region_box
                app.smart_text_value_var.set("轻量图片")
                app.smart_text_font_var.set("宋体")
                app.smart_text_rotation_var.set(0)

                self.assertEqual(
                    app.smart_text_font_match_button.accessible_name,
                    "离线推荐框选原文字的相似字体",
                )
                self.assertEqual(app.smart_text_font_match_button.winfo_manager(), "grid")
                self.assertEqual(
                    app.asset_repository.children_of(source_revision.revision_id),
                    (),
                )

                app._recommend_smart_text_font()
                deadline = time.monotonic() + 5
                while app._smart_font_match_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                self.assertIsNone(app._smart_font_match_future)
                self.assertEqual(app.smart_text_font_var.get(), "微软雅黑")
                self.assertIn("微软雅黑", app.smart_text_font_match_var.get())
                self.assertIn("%", app.smart_text_font_match_var.get())
                self.assertIn("已选用", app.smart_edit_status_var.get())
                self.assertEqual(app._current_asset_revision, source_revision)
                self.assertEqual(
                    app.asset_repository.children_of(source_revision.revision_id),
                    (),
                )

                app._start_smart_text_replacement()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.parent_revision_id, source_revision.revision_id)
                self.assertEqual(
                    result.provenance.parameters["font_family"],
                    "微软雅黑",
                )
            finally:
                self._close_app(app)

    def test_font_recommendation_cancel_and_new_picture_ignore_late_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "字体分析中.png"
            second = root / "切换后的图片.png"
            Image.new("RGB", (160, 100), "white").save(first)
            Image.new("RGB", (120, 90), "#5f8bd5").save(second)
            starts = (threading.Event(), threading.Event())
            releases = (threading.Event(), threading.Event())
            call_index = 0

            def slow_match(*_args: object, **_kwargs: object) -> tuple[FontMatchSuggestion, ...]:
                nonlocal call_index
                index = call_index
                call_index += 1
                starts[index].set()
                if not releases[index].wait(timeout=5):
                    raise TimeoutError("字体推荐测试等待超时")
                return (FontMatchSuggestion("黑体", 0.9),)

            app = self._create_app(directory)
            try:
                app.open_path(first, confirm=False)
                first_revision = self._wait_for_asset(app)
                app._smart_text_region_box = (10, 10, 150, 80)
                app.smart_text_value_var.set("原文字")
                app.smart_text_font_var.set("宋体")
                with patch(
                    "light_image_editor.ui.main_window.match_fonts",
                    side_effect=slow_match,
                ):
                    app._recommend_smart_text_font()
                    self.assertTrue(starts[0].wait(timeout=2))
                    app._cancel_smart_edit()
                    self.assertIsNone(app._smart_font_match_future)
                    self.assertEqual(app.smart_text_font_var.get(), "宋体")
                    releases[0].set()
                    for _index in range(8):
                        app.update()
                        time.sleep(0.01)

                    app._recommend_smart_text_font()
                    self.assertTrue(starts[1].wait(timeout=2))
                    app.open_path(second, confirm=False)
                    deadline = time.monotonic() + 5
                    while (
                        app.image_path is None or not app.image_path.samefile(second)
                    ) and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)
                    second_revision = self._wait_for_asset(app)
                    releases[1].set()
                    for _index in range(8):
                        app.update()
                        time.sleep(0.01)

                self.assertIsNone(app._smart_font_match_future)
                self.assertEqual(app.smart_text_font_var.get(), "宋体")
                self.assertNotIn("黑体", app.smart_text_font_match_var.get())
                self.assertEqual(
                    app.asset_repository.children_of(first_revision.revision_id),
                    (),
                )
                self.assertEqual(
                    app.asset_repository.children_of(second_revision.revision_id),
                    (),
                )
            finally:
                releases[0].set()
                releases[1].set()
                self._close_app(app)

    def test_offline_text_detection_cycles_regions_and_feeds_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "多行文字海报.png"
            image = Image.new("RGB", (480, 240), "#f7f5f0")
            draw = ImageDraw.Draw(image)
            draw.text(
                (44, 42),
                "第一行标题 ABC",
                font=self._test_font(36),
                fill="#202020",
            )
            draw.text(
                (80, 146),
                "第二行说明 2026",
                font=self._test_font(28),
                fill="#303030",
            )
            image.save(source)
            image.close()
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                source_revision = self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                app._detect_smart_text_regions()
                deadline = time.monotonic() + 5
                while app._smart_text_detection_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                self.assertGreaterEqual(len(app._smart_text_suggestions), 2)
                first_region = app._smart_text_region_box
                self.assertIsNotNone(first_region)
                self.assertFalse(app.smart_text_next_region_button.instate(["disabled"]))
                app._select_next_smart_text_suggestion()
                second_region = app._smart_text_region_box
                self.assertIsNotNone(second_region)
                self.assertNotEqual(second_region, first_region)
                self.assertEqual(app.crop_box, second_region)
                self.assertIn("2/", app.smart_edit_status_var.get())

                assert second_region is not None
                adjusted_region = (
                    second_region[0] + 3,
                    second_region[1] + 2,
                    second_region[2] - 4,
                    second_region[3] - 2,
                )
                app.crop_box = adjusted_region
                app._smart_text_adjusting_region = True
                app._finish_crop(SimpleNamespace())  # type: ignore[arg-type]
                self.assertEqual(app._smart_text_region_box, adjusted_region)
                self.assertIn("校正已同步", app.smart_edit_status_var.get())

                app.smart_text_value_var.set("自动区域新文字")
                app.smart_text_font_size_var.set(24)
                app._start_smart_text_replacement()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.parent_revision_id, source_revision.revision_id)
                self.assertEqual(
                    result.provenance.parameters["operation"],
                    "replace_text",
                )
                self.assertAlmostEqual(
                    result.provenance.parameters["region_left"],
                    adjusted_region[0] / 480,
                )
            finally:
                self._close_app(app)

    def test_opening_another_picture_discards_stale_text_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "检测中的图片.png"
            second = root / "新图片.png"
            Image.new("RGB", (160, 100), "white").save(first)
            Image.new("RGB", (120, 90), "#5f8bd5").save(second)
            started = threading.Event()
            release = threading.Event()

            def slow_detection(_path: Path) -> tuple[TextRegionSuggestion, ...]:
                started.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("文字检测测试等待超时")
                return (TextRegionSuggestion((0.1, 0.1, 0.8, 0.3), 0.8),)

            app = self._create_app(directory)
            try:
                app.open_path(first, confirm=False)
                self._wait_for_asset(app)
                with patch(
                    "light_image_editor.ui.main_window.detect_text_regions",
                    side_effect=slow_detection,
                ):
                    app._detect_smart_text_regions()
                    self.assertTrue(started.wait(timeout=2))
                    app.open_path(second, confirm=False)
                    deadline = time.monotonic() + 5
                    while (
                        app.image_path is None or not app.image_path.samefile(second)
                    ) and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)
                    release.set()
                    for _index in range(8):
                        app.update()
                        time.sleep(0.01)

                self.assertIsNotNone(app.image_path)
                assert app.image_path is not None
                self.assertTrue(app.image_path.samefile(second))
                self.assertIsNone(app._smart_text_detection_future)
                self.assertEqual(app._smart_text_suggestions, ())
                self.assertIsNone(app._smart_text_region_box)
            finally:
                release.set()
                self._close_app(app)

    def test_rotated_text_suggestion_updates_replacement_angle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "旋转标题.png"
            Image.new("RGB", (240, 160), "white").save(source)
            suggestion = TextRegionSuggestion(
                (0.15, 0.20, 0.85, 0.70),
                0.87,
                30,
            )
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                with patch(
                    "light_image_editor.ui.main_window.detect_text_regions",
                    return_value=(suggestion,),
                ):
                    app._detect_smart_text_regions()
                    deadline = time.monotonic() + 5
                    while (
                        app._smart_text_detection_future is not None and time.monotonic() < deadline
                    ):
                        app.update()
                        time.sleep(0.01)

                self.assertAlmostEqual(app.smart_text_rotation_var.get(), 30)
                self.assertIn("检测角度 +30°", app.smart_edit_status_var.get())
                self.assertEqual(app._smart_text_region_box, (36, 32, 204, 112))
            finally:
                self._close_app(app)

    def test_curved_text_suggestion_updates_layout_and_feeds_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "弧形标题.png"
            Image.new("RGB", (320, 200), "white").save(source)
            observed_curves: list[float] = []

            class FixtureOcrBackend:
                backend_id = "fixture-curved"
                display_name = "测试弧形文字识别"
                available = True
                unavailable_reason = ""

                def recognize(
                    self,
                    _source: str | Path,
                    *,
                    region: tuple[float, float, float, float],
                    rotation: float = 0.0,
                    quadrilateral=None,
                    curve: float = 0.0,
                ) -> TextOcrResult:
                    del region, rotation, quadrilateral
                    observed_curves.append(curve)
                    return TextOcrResult(
                        "弧形原文",
                        0.93,
                        "chi_sim",
                        "简体中文",
                        self.backend_id,
                        geometry_correction="curve",
                    )

            suggestion = TextRegionSuggestion(
                (0.10, 0.15, 0.90, 0.85),
                0.91,
                curve=0.72,
            )
            app = self._create_app(directory, text_ocr_backend=FixtureOcrBackend())
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                with patch(
                    "light_image_editor.ui.main_window.detect_text_regions",
                    return_value=(suggestion,),
                ):
                    app._detect_smart_text_regions()
                    deadline = time.monotonic() + 5
                    while (
                        app._smart_text_detection_future is not None and time.monotonic() < deadline
                    ):
                        app.update()
                        time.sleep(0.01)

                self.assertAlmostEqual(app.smart_text_curve_var.get(), 72)
                self.assertIn("弧形文字 上拱 +72%", app.smart_edit_status_var.get())
                self.assertIn("弧度 +72%", app.smart_text_region_var.get())
                app._recognize_smart_text_region()
                deadline = time.monotonic() + 5
                while app._smart_text_ocr_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                self.assertEqual(observed_curves, [0.72])
                self.assertEqual(app.smart_text_value_var.get(), "弧形原文")
                self.assertIn("已校直弧形文字", app.smart_text_ocr_var.get())
                self.assertIn("弧形文字已校直", app.smart_edit_status_var.get())
                app.smart_text_value_var.set("弧形新标题")
                app.smart_text_font_size_var.set(40)
                app.smart_text_color_var.set("#c62828")
                app._start_smart_text_replacement()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                assert job_id is not None
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.provenance.parameters["curve"], 0.72)
                app._select_smart_text_region()
                self.assertEqual(app.smart_text_curve_var.get(), 0)
            finally:
                self._close_app(app)

    def test_styled_text_suggestion_syncs_replacement_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "描边文字.png"
            Image.new("RGB", (320, 200), "white").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                app._smart_text_suggestions = (
                    TextRegionSuggestion(
                        (0.1, 0.2, 0.9, 0.7),
                        0.91,
                        visual_effect="outline",
                        estimated_fill_color="#f8f8f8",
                        estimated_effect_color="#19376e",
                        estimated_effect_size=5,
                    ),
                    TextRegionSuggestion(
                        (0.1, 0.2, 0.9, 0.7),
                        0.905,
                        visual_effect="glow",
                        estimated_fill_color="#f8f8ff",
                        estimated_effect_color="#20b8ff",
                        estimated_effect_size=8,
                    ),
                    TextRegionSuggestion((0.1, 0.2, 0.9, 0.7), 0.90),
                )
                app._smart_text_suggestion_index = -1
                app.smart_text_effect_size_var.set(0)

                app._select_next_smart_text_suggestion()

                self.assertIn("描边文字", app.smart_edit_status_var.get())
                self.assertIn("已估计主色、辅助色和强度", app.smart_edit_status_var.get())
                self.assertEqual(app.smart_text_effect_var.get(), "描边")
                self.assertEqual(app.smart_text_color_var.get(), "#f8f8f8")
                self.assertEqual(app.smart_text_effect_color_var.get(), "#19376e")
                self.assertEqual(app.smart_text_effect_size_var.get(), 5)
                app._select_next_smart_text_suggestion()
                self.assertIn("发光文字", app.smart_edit_status_var.get())
                self.assertEqual(app.smart_text_effect_var.get(), "发光")
                self.assertEqual(app.smart_text_color_var.get(), "#f8f8ff")
                self.assertEqual(app.smart_text_effect_color_var.get(), "#20b8ff")
                self.assertEqual(app.smart_text_effect_size_var.get(), 8)
                app._select_next_smart_text_suggestion()
                self.assertEqual(app.smart_text_effect_var.get(), "普通")
                self.assertNotIn("描边文字", app.smart_edit_status_var.get())
            finally:
                self._close_app(app)

    def test_vertical_text_direction_candidates_update_replacement_angle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "竖排标题.png"
            Image.new("RGB", (240, 320), "white").save(source)
            positive = TextRegionSuggestion(
                (0.30, 0.10, 0.70, 0.90),
                0.89,
                90,
            )
            negative = TextRegionSuggestion(
                (0.30, 0.10, 0.70, 0.90),
                0.89,
                -90,
            )
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                with patch(
                    "light_image_editor.ui.main_window.detect_text_regions",
                    return_value=(positive, negative),
                ):
                    app._detect_smart_text_regions()
                    deadline = time.monotonic() + 5
                    while (
                        app._smart_text_detection_future is not None and time.monotonic() < deadline
                    ):
                        app.update()
                        time.sleep(0.01)

                self.assertAlmostEqual(app.smart_text_rotation_var.get(), 90)
                self.assertIn("竖排文字方向 +90°", app.smart_edit_status_var.get())
                self.assertEqual(app._smart_text_region_box, (72, 32, 168, 288))
                app._select_next_smart_text_suggestion()
                self.assertAlmostEqual(app.smart_text_rotation_var.get(), -90)
                self.assertIn("竖排文字方向 -90°", app.smart_edit_status_var.get())
            finally:
                self._close_app(app)

    def test_perspective_text_suggestion_feeds_ocr_and_replacement_quad(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "透视文字海报.png"
            Image.new("RGB", (400, 240), "white").save(source)
            quad = (0.18, 0.30, 0.82, 0.20, 0.87, 0.73, 0.13, 0.65)
            observed_quads = []

            class FixtureOcrBackend:
                backend_id = "fixture-perspective"
                display_name = "测试透视文字识别"
                available = True
                unavailable_reason = ""

                def recognize(
                    self,
                    _source: str | Path,
                    *,
                    region: tuple[float, float, float, float],
                    rotation: float = 0.0,
                    quadrilateral=None,
                ) -> TextOcrResult:
                    del region, rotation
                    observed_quads.append(quadrilateral)
                    return TextOcrResult(
                        "透视原文",
                        0.91,
                        "chi_sim",
                        "简体中文",
                        self.backend_id,
                    )

            suggestion = TextRegionSuggestion(
                (0.13, 0.20, 0.87, 0.73),
                0.88,
                0.0,
                perspective_quad=quad,
            )
            app = self._create_app(directory, text_ocr_backend=FixtureOcrBackend())
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                with patch(
                    "light_image_editor.ui.main_window.detect_text_regions",
                    return_value=(suggestion,),
                ):
                    app._detect_smart_text_regions()
                    deadline = time.monotonic() + 5
                    while (
                        app._smart_text_detection_future is not None and time.monotonic() < deadline
                    ):
                        app.update()
                        time.sleep(0.01)

                self.assertEqual(app._smart_text_perspective_quad, quad)
                self.assertIn("透视文字", app.smart_edit_status_var.get())
                handle_x, handle_y = app._image_to_canvas(
                    round(quad[0] * 400),
                    round(quad[1] * 240),
                )
                app._start_crop(SimpleNamespace(x=handle_x, y=handle_y))
                self.assertEqual(app.crop_action, "perspective:0")
                target_x, target_y = app._image_to_canvas(80, 76)
                app._drag_crop(SimpleNamespace(x=target_x, y=target_y))
                app._finish_crop(SimpleNamespace())
                adjusted_quad = app._smart_text_perspective_quad
                self.assertIsNotNone(adjusted_quad)
                assert adjusted_quad is not None
                self.assertAlmostEqual(adjusted_quad[0], 0.20)
                self.assertAlmostEqual(adjusted_quad[1], 76 / 240)
                self.assertEqual(app._smart_text_region_box, (52, 48, 348, 176))
                self.assertIn("透视角点校正已同步", app.smart_edit_status_var.get())
                self.assertFalse(app._set_smart_text_perspective_corner(0, (390, 230)))
                self.assertEqual(app._smart_text_perspective_quad, adjusted_quad)
                app._recognize_smart_text_region()
                deadline = time.monotonic() + 5
                while app._smart_text_ocr_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(observed_quads, [adjusted_quad])
                self.assertEqual(app.smart_text_value_var.get(), "透视原文")

                app.smart_text_value_var.set("透视新标题")
                app.smart_text_font_size_var.set(32)
                app._start_smart_text_replacement()
                job_id = app._smart_edit_job_id
                self.assertIsNotNone(job_id)
                deadline = time.monotonic() + 8
                while app._smart_edit_presented_job_id != job_id and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                result = app._smart_edit_result_revision
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(
                    result.provenance.parameters["perspective_tl_x"],
                    adjusted_quad[0],
                )
                self.assertEqual(
                    result.provenance.parameters["perspective_bl_y"],
                    adjusted_quad[7],
                )
            finally:
                self._close_app(app)

    def test_two_perspective_text_planes_cycle_in_reading_order_and_feed_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "双平面文字.png"
            Image.new("RGB", (400, 300), "white").save(source)
            first_quad = (0.08, 0.12, 0.44, 0.08, 0.47, 0.39, 0.06, 0.34)
            second_quad = (0.55, 0.56, 0.91, 0.61, 0.94, 0.88, 0.52, 0.91)
            suggestions = (
                TextRegionSuggestion(
                    (0.06, 0.08, 0.47, 0.39),
                    0.91,
                    1.0,
                    perspective_quad=first_quad,
                ),
                TextRegionSuggestion(
                    (0.52, 0.56, 0.94, 0.91),
                    0.89,
                    -1.0,
                    perspective_quad=second_quad,
                ),
            )
            observed_quads = []

            class FixtureOcrBackend:
                backend_id = "fixture-two-planes"
                display_name = "测试双平面文字识别"
                available = True
                unavailable_reason = ""

                def recognize(
                    self,
                    _source: str | Path,
                    *,
                    region: tuple[float, float, float, float],
                    rotation: float = 0.0,
                    quadrilateral=None,
                ) -> TextOcrResult:
                    del region, rotation
                    observed_quads.append(quadrilateral)
                    return TextOcrResult(
                        "第二平面原文",
                        0.90,
                        "chi_sim",
                        "简体中文",
                        self.backend_id,
                    )

            app = self._create_app(directory, text_ocr_backend=FixtureOcrBackend())
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                with patch(
                    "light_image_editor.ui.main_window.detect_text_regions",
                    return_value=suggestions,
                ):
                    app._detect_smart_text_regions()
                    deadline = time.monotonic() + 5
                    while (
                        app._smart_text_detection_future is not None and time.monotonic() < deadline
                    ):
                        app.update()
                        time.sleep(0.01)

                self.assertEqual(app._smart_text_perspective_quad, first_quad)
                self.assertIn("透视文字平面 1/2", app.smart_edit_status_var.get())
                app._select_next_smart_text_suggestion()
                self.assertEqual(app._smart_text_perspective_quad, second_quad)
                self.assertIn("透视文字平面 2/2", app.smart_edit_status_var.get())
                app._recognize_smart_text_region()
                deadline = time.monotonic() + 5
                while app._smart_text_ocr_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(observed_quads, [second_quad])
                self.assertEqual(app.smart_text_value_var.get(), "第二平面原文")
            finally:
                self._close_app(app)

    def test_local_ocr_fills_recognized_text_for_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "待识别文字.png"
            Image.new("RGB", (200, 100), "white").save(source)
            observed_regions: list[tuple[float, float, float, float]] = []
            observed_rotations: list[float] = []

            class FixtureOcrBackend:
                backend_id = "fixture"
                display_name = "测试文字识别"
                available = True
                unavailable_reason = ""

                def recognize(
                    self,
                    _source: str | Path,
                    *,
                    region: tuple[float, float, float, float],
                    rotation: float = 0.0,
                    quadrilateral: tuple[float, float, float, float, float, float, float, float]
                    | None = None,
                ) -> TextOcrResult:
                    del quadrilateral
                    observed_regions.append(region)
                    observed_rotations.append(rotation)
                    return TextOcrResult(
                        "原始标题 2026",
                        0.92,
                        "chi_sim+eng",
                        "简体中文和英文",
                        self.backend_id,
                    )

            app = self._create_app(directory, text_ocr_backend=FixtureOcrBackend())
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                app._show_inspector_tab("smart")
                app._smart_text_region_box = (20, 10, 180, 60)
                app.crop_box = app._smart_text_region_box
                app.smart_text_rotation_var.set(18)
                app._recognize_smart_text_region()
                deadline = time.monotonic() + 5
                while app._smart_text_ocr_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                self.assertEqual(observed_regions, [(0.1, 0.1, 0.9, 0.6)])
                self.assertEqual(observed_rotations, [18])
                self.assertEqual(app.smart_text_value_var.get(), "原始标题 2026")
                self.assertIn("可信度 92%", app.smart_text_ocr_var.get())
                self.assertIn("请校对", app.smart_edit_status_var.get())

                class SystemOcrBackend(FixtureOcrBackend):
                    backend_id = "windows-system-ocr"
                    display_name = "Windows 系统文字识别"

                    def recognize(
                        self,
                        _source: str | Path,
                        *,
                        region: tuple[float, float, float, float],
                        rotation: float = 0.0,
                        quadrilateral: tuple[float, float, float, float, float, float, float, float]
                        | None = None,
                    ) -> TextOcrResult:
                        del region, rotation, quadrilateral
                        return TextOcrResult(
                            "系统识别标题",
                            None,
                            "zh-Hans-CN",
                            "简体中文（Windows）",
                            self.backend_id,
                            "adaptive-contrast",
                        )

                app.text_ocr_backend = SystemOcrBackend()
                app._recognize_smart_text_region()
                deadline = time.monotonic() + 5
                while app._smart_text_ocr_future is not None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(app.smart_text_value_var.get(), "系统识别标题")
                self.assertIn("系统未提供可信度", app.smart_text_ocr_var.get())
                self.assertIn("已增强低对比文字", app.smart_text_ocr_var.get())
            finally:
                self._close_app(app)

    def test_opening_another_picture_discards_stale_ocr_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "识别中的图片.png"
            second = root / "切换后的图片.png"
            Image.new("RGB", (160, 100), "white").save(first)
            Image.new("RGB", (120, 90), "#5f8bd5").save(second)
            started = threading.Event()
            release = threading.Event()

            class SlowOcrBackend:
                backend_id = "slow-fixture"
                display_name = "慢速测试识别"
                available = True
                unavailable_reason = ""

                def recognize(
                    self,
                    _source: str | Path,
                    *,
                    region: tuple[float, float, float, float],
                    rotation: float = 0.0,
                    quadrilateral: tuple[float, float, float, float, float, float, float, float]
                    | None = None,
                ) -> TextOcrResult:
                    del region, rotation, quadrilateral
                    started.set()
                    if not release.wait(timeout=5):
                        raise TimeoutError("文字识别测试等待超时")
                    return TextOcrResult(
                        "不应写入的新结果",
                        0.88,
                        "chi_sim",
                        "简体中文",
                        self.backend_id,
                    )

            app = self._create_app(directory, text_ocr_backend=SlowOcrBackend())
            try:
                app.open_path(first, confirm=False)
                self._wait_for_asset(app)
                app._smart_text_region_box = (10, 10, 130, 50)
                app._recognize_smart_text_region()
                self.assertTrue(started.wait(timeout=2))
                app.open_path(second, confirm=False)
                deadline = time.monotonic() + 5
                while (
                    app.image_path is None or not app.image_path.samefile(second)
                ) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                release.set()
                for _index in range(8):
                    app.update()
                    time.sleep(0.01)

                self.assertIsNone(app._smart_text_ocr_future)
                self.assertEqual(app.smart_text_value_var.get(), "新文字")
                self.assertNotIn("不应写入", app.smart_text_ocr_var.get())
            finally:
                release.set()
                self._close_app(app)

    def test_unavailable_local_ocr_keeps_manual_text_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "手工输入.png"
            Image.new("RGB", (100, 60), "white").save(source)
            backend = UnavailableTextOcrBackend("未安装本地文字识别程序")
            app = self._create_app(directory, text_ocr_backend=backend)
            try:
                app.open_path(source, confirm=False)
                self._wait_for_asset(app)
                app._smart_text_region_box = (5, 5, 80, 40)
                app.smart_text_value_var.set("继续手工输入")
                app._recognize_smart_text_region()

                self.assertEqual(app.smart_text_value_var.get(), "继续手工输入")
                self.assertIn("本机不可用", app.smart_text_ocr_var.get())
                self.assertIn("手工输入", app.smart_edit_status_var.get())
            finally:
                self._close_app(app)

    def test_opening_another_picture_cancels_stale_smart_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "第一张.png"
            second = root / "第二张.png"
            Image.new("RGB", (20, 16), (30, 80, 140)).save(first)
            Image.new("RGB", (22, 18), (180, 90, 40)).save(second)
            app = self._create_app(directory)
            try:
                app.open_path(first, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self._wait_for_asset(app)
                app.mock_provider._auto_run_after_seconds = 5.0
                app.mode_buttons[ModeId.EDIT].command()
                app._smart_repair_region_box = (4, 4, 16, 12)
                app.smart_repair_feather_var.set(0)
                app._start_smart_region_repair()
                stale_job_id = app._smart_edit_job_id
                self.assertIsNotNone(stale_job_id)
                assert stale_job_id is not None

                app.open_path(second, confirm=False)
                deadline = time.monotonic() + 5
                while (
                    app.document is None
                    or app.document.source_path is None
                    or not app._same_path(app.document.source_path, second)
                ) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                self.assertIsNotNone(app.document)
                assert app.document is not None
                self.assertIsNotNone(app.document.source_path)
                assert app.document.source_path is not None
                self.assertTrue(app._same_path(app.document.source_path, second))
                self.assertEqual(
                    app.ai_job_manager.get_job(stale_job_id).status,
                    JobStatus.CANCELLED,
                )
                self.assertIsNone(app._smart_edit_job_id)
                self.assertIsNone(app._smart_edit_result_revision)
                self.assertIsNone(app._smart_text_region_box)
                self.assertIsNone(app._smart_repair_region_box)
            finally:
                self._close_app(app)

    def test_favorite_actions_route_to_their_canonical_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                app._action_callback("crop")()
                app.update_idletasks()
                self.assertEqual(app.mode_router.active_mode, ModeId.CONVERT)
                self.assertEqual(app.annotation_tool_var.get(), "crop")

                app._action_callback("text")()
                app.update_idletasks()
                self.assertEqual(app.mode_router.active_mode, ModeId.EDIT)
                self.assertEqual(app.active_inspector_tab, "annotation")
                self.assertEqual(app.annotation_tool_var.get(), "text")

                app.invoke_feature("generate.workspace")
                app.update_idletasks()
                self.assertEqual(app.mode_router.active_mode, ModeId.GENERATE)
                self.assertTrue(app.generate_workspace.winfo_ismapped())
            finally:
                self._close_app(app)

    def test_convert_crop_has_visible_confirmation_and_cancel_preserves_applied_crop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "裁剪交互.png"
            Image.new("RGB", (120, 80), "#6750a4").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.base_preview_image is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.base_preview_image)

                app._enter_convert_crop()
                app.update_idletasks()
                self.assertEqual(app.mode_router.active_mode, ModeId.CONVERT)
                self.assertTrue(app.crop_action_bar.winfo_ismapped())
                self.assertTrue(app.crop_action_apply_button.instate(("disabled",)))
                self.assertEqual(app.crop_action_hint_var.get(), "拖动图片创建裁剪框")

                app.crop_box = (12, 8, 96, 64)
                app._finish_crop(SimpleNamespace())
                app.update_idletasks()
                self.assertFalse(app.crop_action_apply_button.instate(("disabled",)))
                self.assertEqual(app.crop_action_hint_var.get(), "已选择 84 × 56")

                app.crop_action_apply_button.invoke()
                applied = app.conversion_draft_history.current.crop_box
                self.assertEqual(applied, (0.1, 0.1, 0.8, 0.8))
                self.assertIsNone(app.crop_box)
                self.assertEqual(app.crop_var.get(), "配方中已有裁切")

                app.crop_box = (8, 6, 70, 52)
                app._finish_crop(SimpleNamespace())
                app.crop_action_cancel_button.invoke()
                app.update_idletasks()
                self.assertEqual(app.conversion_draft_history.current.crop_box, applied)
                self.assertIsNone(app.crop_box)
                self.assertEqual(app.annotation_tool_var.get(), "select")
                self.assertFalse(app.crop_action_bar.winfo_ismapped())
                self.assertIn("已应用的裁剪保持不变", app.status_var.get())
            finally:
                self._close_app(app)

    def test_real_asset_and_revision_survive_mode_handoffs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "handoff.png"
            Image.new("RGB", (96, 64), "#6b7f9e").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.document)
                assert app.document is not None
                self._wait_for_asset(app)
                document = app.document
                viewer_context = app.mode_router.context_for(ModeId.VIEWER)
                self.assertIsNotNone(viewer_context.asset)
                assert viewer_context.asset is not None
                self.assertIsNotNone(viewer_context.asset.uri)
                assert viewer_context.asset.uri is not None
                self.assertTrue(viewer_context.asset.uri.endswith("/handoff.png"))
                self.assertEqual(viewer_context.asset.display_name, "handoff.png")

                app.viewer_to_edit_button.command()
                app.rotate(90)
                edited_context = app.mode_router.context_for(ModeId.EDIT)
                self.assertIs(app.document, document)
                self.assertIsNotNone(edited_context.revision)
                self.assertTrue(document.dirty)

                app.mode_buttons[ModeId.CONVERT].command()
                converted_context = app.mode_router.context_for(ModeId.CONVERT)
                self.assertIs(app.document, document)
                self.assertEqual(converted_context.asset_id, viewer_context.asset_id)
                self.assertEqual(converted_context.revision_id, edited_context.revision_id)

                app.mode_buttons[ModeId.VIEWER].command()
                self.assertIs(app.document, document)
                self.assertEqual(app.mode_router.context.asset_id, viewer_context.asset_id)
            finally:
                self._close_app(app)

    def test_external_asset_identity_is_reused_after_application_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "跨重启.png"
            Image.new("RGB", (84, 56), "#57748c").save(source)

            first_app = self._create_app(directory)
            try:
                first_app.open_path(source, confirm=False)
                first_revision = self._wait_for_asset(first_app)
            finally:
                self._close_app(first_app)

            second_app = self._create_app(directory)
            try:
                second_app.open_path(source, confirm=False)
                second_revision = self._wait_for_asset(second_app)
                self.assertEqual(second_revision.asset_id, first_revision.asset_id)
                self.assertEqual(second_revision.revision_id, first_revision.revision_id)
                self.assertEqual(
                    second_app.mode_router.context.asset.uri,
                    source.resolve().as_uri(),
                )
            finally:
                self._close_app(second_app)

    def test_convert_queue_rejects_identity_for_a_different_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "真实输入.png"
            Image.new("RGB", (72, 48), "#59708f").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.document)
                self._wait_for_asset(app)

                app.mode_buttons[ModeId.GENERATE].command()
                app.mode_router.set_handoff(
                    AssetHandoff(
                        id="asset-generated",
                        uri="mock://offline.mock/job/candidate.png",
                        display_name="候选.png",
                    ),
                    RevisionHandoff(
                        id="revision-generated",
                        asset_id="asset-generated",
                        number=1,
                    ),
                    mode=ModeId.GENERATE,
                )
                app.mode_buttons[ModeId.CONVERT].command()

                queued = app.conversion_workspace.queue.get(source)
                self.assertIsNotNone(queued)
                assert queued is not None
                self.assertIsNone(queued.asset)
                self.assertIsNone(queued.revision)
                self.assertIn("真实文件", app.conversion_job_status_var.get())
            finally:
                self._close_app(app)

    def test_convert_queue_keeps_identity_when_uri_matches_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "匹配输入.png"
            Image.new("RGB", (72, 48), "#7a5d88").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.document)
                revision = self._wait_for_asset(app)

                app.mode_buttons[ModeId.GENERATE].command()
                app.mode_buttons[ModeId.CONVERT].command()

                queued = app.conversion_workspace.queue.get(source)
                self.assertIsNotNone(queued)
                assert queued is not None
                self.assertEqual(queued.asset_id, revision.asset_id)
                self.assertEqual(queued.revision_id, revision.revision_id)
            finally:
                self._close_app(app)

    def test_viewer_animation_controls_pause_step_and_follow_mode_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "animated.gif"
            frames = [
                Image.new("RGB", (48, 32), color) for color in ("#d55f5f", "#5f8bd5", "#65a86e")
            ]
            frames[0].save(
                source,
                save_all=True,
                append_images=frames[1:],
                duration=[1000, 1000, 1000],
                loop=0,
            )
            for frame in frames:
                frame.close()

            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app._animation_sequence is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app._animation_sequence)
                app.update_idletasks()
                self.assertTrue(app.animation_controls.winfo_ismapped())
                self.assertTrue(app._animation_playing)

                app._toggle_animation_playback()
                self.assertFalse(app._animation_playing)
                self.assertIsNone(app._animation_job)
                self.assertEqual(app.animation_play_button.text, "播放")
                displayed = app._animation_displayed_index

                app._step_animation(1)
                self.assertEqual(app._animation_displayed_index, (displayed + 1) % 3)
                self.assertIn("/ 3 帧", app.animation_position_var.get())
                app._toggle_animation_playback()
                self.assertTrue(app._animation_playing)

                app.mode_buttons[ModeId.EDIT].command()
                app.update_idletasks()
                self.assertFalse(app.animation_controls.winfo_ismapped())
                self.assertFalse(app._animation_playing)
                self.assertIsNone(app._animation_job)
                app.mode_buttons[ModeId.VIEWER].command()
                app.update_idletasks()
                self.assertTrue(app.animation_controls.winfo_ismapped())
                self.assertTrue(app._animation_playing)

                app._toggle_animation_playback()
                self.assertFalse(app._animation_playing)
                self.assertIsNone(app._animation_job)
                self.assertEqual(app.animation_play_button.text, "播放")
            finally:
                self._close_app(app)

    def test_viewer_compares_two_files_and_preserves_selection_across_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "主图.png"
            comparison = root / "参考图.png"
            Image.new("RGB", (96, 64), "#d55f5f").save(source)
            Image.new("RGB", (64, 96), "#5f8bd5").save(comparison)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.document)

                self.assertTrue(app._begin_file_comparison(comparison))
                preview_generation = app.preview_ready_generation
                deadline = time.monotonic() + 5
                while (
                    not app.file_compare_var.get()
                    or app.preview_ready_generation <= preview_generation
                ) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertTrue(app.file_compare_var.get())
                self.assertEqual(app._comparison_path, comparison)
                self.assertTrue(app.viewer_file_compare_button.selected)
                viewer_state = app.mode_router.state_for(ModeId.VIEWER)
                self.assertTrue(viewer_state["file_compare_enabled"])
                self.assertEqual(viewer_state["file_compare_path"], str(comparison))
                canvas_text = {
                    app.canvas.itemcget(item, "text")
                    for item in app.canvas.find_all()
                    if app.canvas.type(item) == "text"
                }
                self.assertTrue({source.name, comparison.name} <= canvas_text)

                app.mode_buttons[ModeId.EDIT].command()
                app.update_idletasks()
                self.assertTrue(app.file_compare_var.get())
                self.assertIsNotNone(app._comparison_image)
                app.mode_buttons[ModeId.VIEWER].command()
                app.update_idletasks()
                self.assertTrue(app.viewer_file_compare_button.selected)

                cursor = app.document.timeline.cursor if app.document is not None else -1
                app._canvas_press(SimpleNamespace(x=20, y=20))
                self.assertEqual(app.document.timeline.cursor if app.document else -1, cursor)
                self.assertIn("退出并排对比", app.status_var.get())

                self.assertTrue(app.navigation.select(comparison))
                app._begin_image_load(comparison, notify_error=True)
                deadline = time.monotonic() + 5
                while (
                    app.image_path != comparison or app.file_compare_var.get()
                ) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertFalse(app.file_compare_var.get())
                self.assertIsNone(app._comparison_path)
                self.assertFalse(app.viewer_file_compare_button.selected)
                self.assertFalse(app.mode_router.state_for(ModeId.VIEWER)["file_compare_enabled"])
            finally:
                self._close_app(app)

    def test_recent_file_persistence_is_debounced_during_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "001.jpg"
            second = root / "002.jpg"
            first.touch()
            second.touch()
            app = self._create_app(directory)
            try:
                with patch.object(app, "_save_config", wraps=app._save_config) as save_config:
                    app._remember_file(first)
                    app._remember_file(second)
                    self.assertEqual(save_config.call_count, 0)

                    deadline = time.monotonic() + 1.0
                    while save_config.call_count == 0 and time.monotonic() < deadline:
                        app.update()
                        time.sleep(0.01)

                    self.assertEqual(save_config.call_count, 1)
                    self.assertIsNone(app._config_save_job)
                    self.assertEqual(app.recent_files[:2], [str(second), str(first)])
                    self.assertEqual(
                        app.settings_store.load_migrated()["recent_files"][:2],
                        [str(second), str(first)],
                    )
            finally:
                self._close_app(app)

    def test_file_rename_and_clear_keep_mode_handoff_truthful(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "旧名称.png"
            Image.new("RGB", (80, 60), "#6b7f9e").save(source)
            app = self._create_app(directory)
            try:
                app.open_path(source, confirm=False)
                deadline = time.monotonic() + 5
                while app.document is None and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertIsNotNone(app.document)
                original_revision = self._wait_for_asset(app)

                with patch(
                    "light_image_editor.ui.main_window.simpledialog.askstring",
                    return_value="新名称.png",
                ):
                    app.rename_current()
                renamed = app.image_path
                self.assertIsNotNone(renamed)
                assert renamed is not None
                self.assertEqual(renamed.name, "新名称.png")
                self.assertTrue(renamed.exists())
                deadline = time.monotonic() + 5
                while (
                    app._current_asset_revision is None
                    or app._current_asset_revision.revision_id == original_revision.revision_id
                ) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                self.assertEqual(app.mode_router.context.asset.display_name, renamed.name)
                self.assertEqual(app.mode_router.context.asset.uri, renamed.as_uri())

                app._clear_document_after_load_failure(renamed)
                self.assertIsNone(app.mode_router.context.asset)
                self.assertIsNone(app.mode_router.context.revision)
            finally:
                self._close_app(app)


if __name__ == "__main__":
    unittest.main()
