from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from light_image_editor.domain import (
    AssetRevision,
    ContentEditRequest,
    GenerationRequest,
    JobSnapshot,
    JobStatus,
    Provenance,
)
from light_image_editor.providers import (
    MOCK_MODEL_ID,
    AIProvider,
    MockProvider,
    ProviderManifest,
)
from light_image_editor.services import (
    JobManager,
    JobManagerClosedError,
    JobManagerIdempotencyConflictError,
)


def _source_revision() -> AssetRevision:
    return AssetRevision.from_content_uri(
        asset_id="asset_job_source",
        sequence=1,
        content_uri="file:///pictures/job-source.png",
        media_type="image/png",
        provenance=Provenance.imported(source="job-source.png"),
        width=320,
        height=240,
        revision_id="rev_job_source",
    )


class _RecordingProvider(AIProvider):
    def __init__(
        self,
        delegate: MockProvider,
        *,
        store_path: Path | None = None,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
        fail_submit: bool = False,
    ) -> None:
        self.delegate = delegate
        self.store_path = store_path
        self.started = started
        self.release = release
        self.fail_submit = fail_submit
        self.submit_thread_ids: list[int] = []
        self.cancel_thread_ids: list[int] = []
        self.query_thread_ids: list[int] = []
        self.persisted_before_submit = False
        self.cancel_called = threading.Event()

    @property
    def manifest(self) -> ProviderManifest:
        return self.delegate.manifest

    def submit_generation(self, request: GenerationRequest) -> JobSnapshot:
        self._before_submit()
        return self.delegate.submit_generation(request)

    def submit_edit(self, request: ContentEditRequest) -> JobSnapshot:
        self._before_submit()
        return self.delegate.submit_edit(request)

    def get_job(self, job_id: str) -> JobSnapshot:
        self.query_thread_ids.append(threading.get_ident())
        return self.delegate.get_job(job_id)

    def cancel(self, job_id: str) -> JobSnapshot:
        self.cancel_thread_ids.append(threading.get_ident())
        self.cancel_called.set()
        return self.delegate.cancel(job_id)

    def _before_submit(self) -> None:
        self.submit_thread_ids.append(threading.get_ident())
        if self.store_path is not None and self.store_path.exists():
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
            jobs = payload.get("jobs", [])
            self.persisted_before_submit = bool(jobs) and jobs[0]["status"] in {
                "submitting",
                "running",
            }
        if self.started is not None:
            self.started.set()
        if self.release is not None and not self.release.wait(timeout=10):
            raise RuntimeError("test provider release timed out")
        if self.fail_submit:
            raise RuntimeError("simulated provider failure")


class JobManagerTests(unittest.TestCase):
    def test_generation_runs_off_caller_thread_and_persists_before_provider_call(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "jobs.json"
            provider = _RecordingProvider(MockProvider(), store_path=store)
            manager = JobManager(provider, store)
            caller_thread = threading.get_ident()
            request = GenerationRequest(
                prompt="异步生成测试",
                model=MOCK_MODEL_ID,
                request_id="request-job-generation",
                idempotency_key="key-job-generation",
            )
            try:
                submitted = manager.submit_generation(request)
                completed = manager.wait(submitted.job_id, timeout=5)

                self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                self.assertEqual(len(completed.outputs), 1)
                self.assertTrue(provider.persisted_before_submit)
                self.assertEqual(len(provider.submit_thread_ids), 1)
                self.assertNotEqual(provider.submit_thread_ids[0], caller_thread)
            finally:
                manager.shutdown()

            restored_provider = _RecordingProvider(MockProvider())
            restored = JobManager(restored_provider, store)
            try:
                recovered = restored.get_job(submitted.job_id)
                self.assertEqual(recovered.status, JobStatus.SUCCEEDED)
                self.assertEqual(recovered.outputs[0].asset_id, completed.outputs[0].asset_id)
                self.assertEqual(restored_provider.submit_thread_ids, [])
            finally:
                restored.shutdown()

    def test_edit_submission_uses_the_same_async_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "jobs.json"
            provider = _RecordingProvider(MockProvider())
            manager = JobManager(provider, store)
            caller_thread = threading.get_ident()
            request = ContentEditRequest(
                source_revision=_source_revision(),
                prompt="把天空改成晚霞",
                model=MOCK_MODEL_ID,
                request_id="request-job-edit",
                idempotency_key="key-job-edit",
            )
            try:
                submitted = manager.submit_edit(request)
                completed = manager.wait(submitted.job_id, timeout=5)

                self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                revision = completed.outputs[0].current_revision
                self.assertEqual(revision.provenance.origin, "content_edit")
                self.assertEqual(
                    revision.provenance.source_revision_ids,
                    (request.source_revision.revision_id,),
                )
                self.assertNotEqual(provider.submit_thread_ids[0], caller_thread)
            finally:
                manager.shutdown()

            restored_provider = _RecordingProvider(MockProvider())
            restored = JobManager(restored_provider, store)
            try:
                recovered = restored.get_job(submitted.job_id)
                self.assertEqual(recovered.status, JobStatus.SUCCEEDED)
                self.assertEqual(recovered.request_kind.value, "edit")
                self.assertEqual(
                    recovered.outputs[0].current_revision.provenance.source_revision_ids,
                    (request.source_revision.revision_id,),
                )
                self.assertEqual(restored_provider.submit_thread_ids, [])
            finally:
                restored.shutdown()

    def test_restart_marks_interrupted_submission_unknown_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "live-jobs.json"
            restart_store = root / "restart-jobs.json"
            started = threading.Event()
            release = threading.Event()
            first_provider = _RecordingProvider(
                MockProvider(),
                store_path=store,
                started=started,
                release=release,
            )
            first = JobManager(first_provider, store)
            request = GenerationRequest(
                prompt="重启状态未知测试",
                model=MOCK_MODEL_ID,
                request_id="request-job-unknown",
                idempotency_key="key-job-unknown",
            )
            submitted = first.submit_generation(request)
            self.assertTrue(started.wait(timeout=5))
            shutil.copyfile(store, restart_store)

            restarted_provider = _RecordingProvider(MockProvider())
            restarted = JobManager(restarted_provider, restart_store)
            try:
                recovered = restarted.get_job(submitted.job_id)
                self.assertEqual(recovered.status, JobStatus.UNKNOWN)
                self.assertTrue(recovered.status.terminal)
                self.assertIsNotNone(recovered.error)
                self.assertIn(
                    "不会自动重新提交", recovered.error.message if recovered.error else ""
                )
                self.assertEqual(restarted_provider.submit_thread_ids, [])

                replay = restarted.submit_generation(request)
                self.assertEqual(replay.job_id, recovered.job_id)
                self.assertEqual(replay.status, JobStatus.UNKNOWN)
                self.assertEqual(restarted_provider.submit_thread_ids, [])

                with self.assertRaises(JobManagerIdempotencyConflictError):
                    restarted.submit_generation(
                        GenerationRequest(
                            prompt="不同内容",
                            model=MOCK_MODEL_ID,
                            request_id="request-job-conflict",
                            idempotency_key=request.idempotency_key,
                        )
                    )
            finally:
                restarted.shutdown()
                release.set()
                first.shutdown()

    def test_cancel_calls_provider_in_background_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = _RecordingProvider(MockProvider(auto_complete=False))
            manager = JobManager(provider, Path(directory) / "jobs.json")
            caller_thread = threading.get_ident()
            request = GenerationRequest(
                prompt="取消异步任务",
                model=MOCK_MODEL_ID,
                request_id="request-job-cancel",
                idempotency_key="key-job-cancel",
            )
            try:
                submitted = manager.submit_generation(request)
                queued = manager.wait(submitted.job_id, timeout=5)
                self.assertEqual(queued.status, JobStatus.QUEUED)

                cancelled = manager.cancel(submitted.job_id)
                self.assertEqual(cancelled.status, JobStatus.CANCELLED)
                self.assertEqual(manager.cancel(submitted.job_id), cancelled)
                self.assertTrue(provider.cancel_called.wait(timeout=5))
                manager.shutdown()

                final = manager.get_job(submitted.job_id)
                self.assertEqual(final.status, JobStatus.CANCELLED)
                self.assertEqual(len(provider.cancel_thread_ids), 1)
                self.assertNotEqual(provider.cancel_thread_ids[0], caller_thread)
            finally:
                manager.shutdown()

    def test_cancel_wins_race_with_late_successful_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            started = threading.Event()
            release = threading.Event()
            provider = _RecordingProvider(
                MockProvider(),
                started=started,
                release=release,
            )
            manager = JobManager(provider, Path(directory) / "jobs.json")
            request = GenerationRequest(
                prompt="取消竞态",
                model=MOCK_MODEL_ID,
                request_id="request-cancel-race",
                idempotency_key="key-cancel-race",
            )
            try:
                submitted = manager.submit_generation(request)
                self.assertTrue(started.wait(timeout=5))
                cancelled = manager.cancel(submitted.job_id)
                self.assertEqual(cancelled.status, JobStatus.CANCELLED)
                release.set()
                manager.shutdown()
                final = manager.get_job(submitted.job_id)

                self.assertEqual(final.status, JobStatus.CANCELLED)
                self.assertEqual(final.outputs, ())
                self.assertIsNotNone(final.provider_job_id)
                self.assertEqual(provider.delegate.submission_count, 1)
                self.assertEqual(provider.cancel_thread_ids, [])
            finally:
                release.set()
                manager.shutdown()

    def test_refresh_queries_provider_in_background_and_publishes_new_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            delegate = MockProvider(auto_complete=False)
            provider = _RecordingProvider(delegate)
            manager = JobManager(provider, Path(directory) / "jobs.json")
            caller_thread = threading.get_ident()
            request = GenerationRequest(
                prompt="查询异步任务",
                model=MOCK_MODEL_ID,
                request_id="request-job-query",
                idempotency_key="key-job-query",
            )
            try:
                submitted = manager.submit_generation(request)
                queued = manager.wait(submitted.job_id, timeout=5)
                self.assertEqual(queued.status, JobStatus.QUEUED)
                self.assertIsNotNone(queued.provider_job_id)
                assert queued.provider_job_id is not None
                delegate.run(queued.provider_job_id)

                manager.refresh(queued.job_id)
                completed = manager.wait_for_update(
                    queued.job_id,
                    after_version=queued.version,
                    timeout=5,
                )

                self.assertEqual(completed.status, JobStatus.SUCCEEDED)
                self.assertEqual(len(provider.query_thread_ids), 1)
                self.assertNotEqual(provider.query_thread_ids[0], caller_thread)
            finally:
                manager.shutdown()

    def test_provider_exception_becomes_isolated_terminal_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = _RecordingProvider(MockProvider(), fail_submit=True)
            manager = JobManager(provider, Path(directory) / "jobs.json")
            request = GenerationRequest(
                prompt="模拟失败",
                model=MOCK_MODEL_ID,
                request_id="request-job-failure",
                idempotency_key="key-job-failure",
            )
            try:
                submitted = manager.submit_generation(request)
                failed = manager.wait(submitted.job_id, timeout=5)

                self.assertEqual(failed.status, JobStatus.FAILED)
                self.assertTrue(failed.status.terminal)
                self.assertIsNotNone(failed.error)
                self.assertEqual(
                    failed.error.code if failed.error else None, "provider_submit_failed"
                )
                self.assertIn("不会自动重试", failed.error.message if failed.error else "")
                self.assertEqual(failed.outputs, ())
            finally:
                manager.shutdown()

    def test_clean_shutdown_rejects_new_submissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = JobManager(MockProvider(), Path(directory) / "jobs.json")
            manager.shutdown()
            with self.assertRaises(JobManagerClosedError):
                manager.submit_generation(
                    GenerationRequest(prompt="关闭后提交", model=MOCK_MODEL_ID)
                )


if __name__ == "__main__":
    unittest.main()
