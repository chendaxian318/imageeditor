from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw

from light_image_editor.domain import (
    AssetRecord,
    AssetRevision,
    ContentEditRequest,
    GenerationReference,
    GenerationRequest,
    JobError,
    JobStatus,
    Provenance,
    TextReplacementDraft,
)
from light_image_editor.providers import (
    MOCK_MODEL_ID,
    AIProvider,
    IdempotencyConflictError,
    MockProvider,
    ProviderCapability,
    UnsupportedProviderRequestError,
)
from light_image_editor.services.smart_selection import detect_smart_selection


def source_revision() -> AssetRevision:
    return AssetRevision.from_content_uri(
        asset_id="asset_source",
        sequence=1,
        content_uri="file:///pictures/source.png",
        media_type="image/png",
        provenance=Provenance.imported(source="source.png"),
        width=640,
        height=480,
        revision_id="rev_source",
    )


class AssetDomainTests(unittest.TestCase):
    def test_asset_record_is_append_only_and_tracks_provenance(self) -> None:
        first = source_revision()
        record = AssetRecord.from_revision(name="source.png", revision=first)
        second = AssetRevision.from_content_uri(
            asset_id=record.asset_id,
            sequence=2,
            content_uri="file:///pictures/source-edited.png",
            media_type="image/png",
            provenance=Provenance(
                origin="edit",
                actor="user",
                source_revision_ids=(first.revision_id,),
            ),
            width=640,
            height=480,
            revision_id="rev_second",
        )

        updated = record.append_revision(second)

        self.assertEqual(record.current_revision, first)
        self.assertEqual(updated.current_revision, second)
        self.assertEqual(updated.current_revision.provenance.source_revision_ids, ("rev_source",))


class OfflineMockProviderTests(unittest.TestCase):
    def test_provider_preserves_rate_limit_and_policy_failure_semantics(self) -> None:
        provider = MockProvider(
            failure_errors={
                "request-rate-limit": JobError(
                    code="rate_limited",
                    message="请求过于频繁，请稍后重试",
                    retryable=True,
                ),
                "request-policy": JobError(
                    code="policy_rejected",
                    message="请求未通过内容策略检查",
                    retryable=False,
                ),
            }
        )

        rate_limited = provider.submit_generation(
            GenerationRequest(
                prompt="限流测试",
                model=MOCK_MODEL_ID,
                request_id="request-rate-limit",
                idempotency_key="key-rate-limit",
            )
        )
        rejected = provider.submit_generation(
            GenerationRequest(
                prompt="策略测试",
                model=MOCK_MODEL_ID,
                request_id="request-policy",
                idempotency_key="key-policy",
            )
        )

        self.assertEqual(rate_limited.status, JobStatus.FAILED)
        self.assertEqual(rate_limited.error.code if rate_limited.error else "", "rate_limited")
        self.assertTrue(rate_limited.error.retryable if rate_limited.error else False)
        self.assertEqual(rejected.status, JobStatus.FAILED)
        self.assertEqual(rejected.error.code if rejected.error else "", "policy_rejected")
        self.assertFalse(rejected.error.retryable if rejected.error else True)
        self.assertEqual(rate_limited.outputs, ())
        self.assertEqual(rejected.outputs, ())

    def test_manifest_and_generation_success_are_provider_neutral(self) -> None:
        provider = MockProvider()
        self.assertIsInstance(provider, AIProvider)
        self.assertTrue(provider.manifest.offline)
        self.assertTrue(provider.manifest.supports(ProviderCapability.GENERATION, MOCK_MODEL_ID))
        self.assertTrue(
            provider.manifest.supports(ProviderCapability.NEGATIVE_PROMPT, MOCK_MODEL_ID)
        )
        self.assertTrue(
            provider.manifest.supports(ProviderCapability.REFERENCE_IMAGE, MOCK_MODEL_ID)
        )
        self.assertTrue(provider.manifest.supports(ProviderCapability.SEED, MOCK_MODEL_ID))
        self.assertTrue(provider.manifest.supports(ProviderCapability.STYLE_PRESET, MOCK_MODEL_ID))
        self.assertTrue(provider.manifest.supports(ProviderCapability.VARIATION, MOCK_MODEL_ID))
        request = GenerationRequest(
            prompt="一座安静的山谷",
            model=MOCK_MODEL_ID,
            width=768,
            height=512,
            count=2,
            seed=7,
            request_id="request-success",
            idempotency_key="success-key",
        )

        job = provider.submit_generation(request)

        self.assertEqual(job.status, JobStatus.SUCCEEDED)
        self.assertEqual(len(job.outputs), 2)
        self.assertTrue(
            all(output.current_revision.content_uri.startswith("mock://") for output in job.outputs)
        )
        self.assertEqual(job.outputs[0].current_revision.width, 768)
        self.assertEqual(
            job.outputs[0].current_revision.provenance.provider_id,
            provider.manifest.provider_id,
        )

    def test_materialized_mock_output_is_a_real_hash_verified_png(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = MockProvider(output_directory=directory)
            job = provider.submit_generation(
                GenerationRequest(
                    prompt="可安全导入的离线候选",
                    model=MOCK_MODEL_ID,
                    width=96,
                    height=64,
                    request_id="request-materialized",
                    idempotency_key="materialized-key",
                )
            )

            revision = job.outputs[0].current_revision
            path = Path(revision.content_uri.removeprefix("file:///"))
            if not path.exists():
                from urllib.parse import unquote, urlparse

                path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            self.assertTrue(path.exists())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), revision.sha256)
            with Image.open(path) as image:
                image.load()
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (96, 64))

    def test_generation_request_rejects_unsafe_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            GenerationRequest(prompt="过大", width=8_193, height=32)
        with self.assertRaises(ValueError):
            GenerationRequest(prompt="引用", reference_role="unknown")
        with self.assertRaises(ValueError):
            GenerationRequest(prompt="引用", reference_strength=1.1)
        with self.assertRaises(ValueError):
            GenerationRequest(prompt="种子", seed=-1)
        with self.assertRaises(ValueError):
            GenerationRequest(prompt="种子", seed=True)
        with self.assertRaises(ValueError):
            GenerationRequest(prompt="风格", style_preset="x" * 101)
        with self.assertRaises(ValueError):
            GenerationRequest(prompt="变体", variation_strength=1.1)
        with self.assertRaises(ValueError):
            GenerationRequest(
                prompt="冲突来源",
                reference_revision=source_revision(),
                variation_revision=source_revision(),
            )

    def test_fixed_seed_reproduces_pixels_and_style_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = MockProvider(output_directory=root)
            first = provider.submit_generation(
                GenerationRequest(
                    prompt="可复现的候选",
                    model=MOCK_MODEL_ID,
                    width=72,
                    height=48,
                    seed=20260805,
                    style_preset="watercolor",
                    request_id="seed-request-one",
                    idempotency_key="seed-key-one",
                )
            )
            second = provider.submit_generation(
                GenerationRequest(
                    prompt="可复现的候选",
                    model=MOCK_MODEL_ID,
                    width=72,
                    height=48,
                    seed=20260805,
                    style_preset="watercolor",
                    request_id="seed-request-two",
                    idempotency_key="seed-key-two",
                )
            )

            first_revision = first.outputs[0].current_revision
            second_revision = second.outputs[0].current_revision
            self.assertNotEqual(first_revision.asset_id, second_revision.asset_id)
            self.assertEqual(first_revision.sha256, second_revision.sha256)
            self.assertEqual(first_revision.provenance.parameters["seed"], 20260805)
            self.assertEqual(
                first_revision.provenance.parameters["style_preset"],
                "watercolor",
            )

            different_style = provider.submit_generation(
                GenerationRequest(
                    prompt="可复现的候选",
                    model=MOCK_MODEL_ID,
                    width=72,
                    height=48,
                    seed=20260805,
                    style_preset="pixel_art",
                    request_id="seed-request-three",
                    idempotency_key="seed-key-three",
                )
            )
            self.assertNotEqual(
                first_revision.sha256,
                different_style.outputs[0].current_revision.sha256,
            )

    def test_reference_revision_and_strength_are_bound_into_request_fingerprint(self) -> None:
        first_reference = source_revision()
        second_reference = AssetRevision.from_content_uri(
            asset_id="asset-reference-two",
            sequence=1,
            content_uri="file:///pictures/reference-two.png",
            media_type="image/png",
            provenance=Provenance.imported(source="reference-two.png"),
            width=640,
            height=480,
            revision_id="rev-reference-two",
        )
        baseline = GenerationRequest(
            prompt="参考图生成",
            model=MOCK_MODEL_ID,
            reference_revision=first_reference,
            reference_role="style",
            reference_strength=0.6,
        )

        self.assertNotEqual(
            baseline.fingerprint(),
            GenerationRequest(
                prompt="参考图生成",
                model=MOCK_MODEL_ID,
                reference_revision=second_reference,
                reference_role="style",
                reference_strength=0.6,
            ).fingerprint(),
        )
        self.assertNotEqual(
            baseline.fingerprint(),
            GenerationRequest(
                prompt="参考图生成",
                model=MOCK_MODEL_ID,
                reference_revision=first_reference,
                reference_role="style",
                reference_strength=0.7,
            ).fingerprint(),
        )

    def test_multi_reference_contract_limits_duplicates_and_binds_each_reference(self) -> None:
        primary = source_revision()
        additional = tuple(
            GenerationReference(
                AssetRevision.from_content_uri(
                    asset_id=f"asset-reference-{index}",
                    sequence=1,
                    content_uri=f"file:///pictures/reference-{index}.png",
                    media_type="image/png",
                    provenance=Provenance.imported(source=f"reference-{index}.png"),
                    width=640,
                    height=480,
                    revision_id=f"rev-reference-{index}",
                ),
                role="style" if index % 2 else "composition",
                strength=index / 10,
            )
            for index in range(1, 4)
        )
        request = GenerationRequest(
            prompt="多参考图",
            reference_revision=primary,
            reference_role="content",
            reference_strength=0.8,
            additional_references=additional,
        )

        self.assertEqual(len(request.all_references), 4)
        self.assertNotEqual(
            request.fingerprint(),
            GenerationRequest(
                prompt="多参考图",
                reference_revision=primary,
                reference_role="content",
                reference_strength=0.8,
                additional_references=additional[:-1],
            ).fingerprint(),
        )
        with self.assertRaisesRegex(ValueError, "at most 4"):
            GenerationRequest(
                prompt="过多参考图",
                reference_revision=primary,
                additional_references=(
                    *additional,
                    GenerationReference(
                        AssetRevision.from_content_uri(
                            asset_id="asset-reference-overflow",
                            sequence=1,
                            content_uri="file:///pictures/reference-overflow.png",
                            media_type="image/png",
                            provenance=Provenance.imported(source="overflow.png"),
                            revision_id="rev-reference-overflow",
                        )
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            GenerationRequest(
                prompt="重复参考图",
                reference_revision=primary,
                additional_references=(GenerationReference(primary),),
            )

    def test_multi_reference_generation_blends_pixels_and_records_all_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            red_path = root / "red.png"
            blue_path = root / "blue.png"
            Image.new("RGB", (48, 32), (240, 20, 20)).save(red_path)
            Image.new("RGB", (48, 32), (20, 20, 240)).save(blue_path)
            red = AssetRevision.from_content_uri(
                asset_id="asset-red-reference",
                sequence=1,
                content_uri=red_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=red_path.name),
                width=48,
                height=32,
                revision_id="rev-red-reference",
            )
            blue = AssetRevision.from_content_uri(
                asset_id="asset-blue-reference",
                sequence=1,
                content_uri=blue_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=blue_path.name),
                width=48,
                height=32,
                revision_id="rev-blue-reference",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_generation(
                GenerationRequest(
                    prompt="融合红蓝参考图",
                    model=MOCK_MODEL_ID,
                    width=48,
                    height=32,
                    reference_revision=red,
                    reference_role="content",
                    reference_strength=0.5,
                    additional_references=(
                        GenerationReference(blue, role="content", strength=0.5),
                    ),
                    request_id="request-multi-reference",
                    idempotency_key="key-multi-reference",
                )
            )

            revision = job.outputs[0].current_revision
            output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            self.assertEqual(
                revision.provenance.source_revision_ids,
                (red.revision_id, blue.revision_id),
            )
            self.assertEqual(revision.provenance.parameters["reference_count"], 2)
            self.assertEqual(
                revision.provenance.parameters["reference_1_revision_id"],
                blue.revision_id,
            )
            with Image.open(output_path) as output:
                output.load()
                center = output.getpixel((24, 16))
                self.assertAlmostEqual(center[0], 130, delta=1)
                self.assertEqual(center[1], 20)
                self.assertAlmostEqual(center[2], 130, delta=1)

    def test_reference_generation_uses_real_pixels_and_records_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference_path = root / "reference.png"
            Image.new("RGB", (48, 32), (35, 145, 95)).save(reference_path)
            reference = AssetRevision.from_content_uri(
                asset_id="asset-reference",
                sequence=1,
                content_uri=reference_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=reference_path.name),
                width=48,
                height=32,
                revision_id="rev-reference",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_generation(
                GenerationRequest(
                    prompt="保留参考图内容",
                    negative_prompt="不要边框",
                    model=MOCK_MODEL_ID,
                    width=48,
                    height=32,
                    reference_revision=reference,
                    reference_role="content",
                    reference_strength=1.0,
                    request_id="request-reference-generation",
                    idempotency_key="reference-generation-key",
                )
            )

            revision = job.outputs[0].current_revision
            from urllib.parse import unquote, urlparse

            output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            self.assertEqual(revision.provenance.source_revision_ids, (reference.revision_id,))
            self.assertEqual(revision.provenance.parameters["negative_prompt"], "不要边框")
            self.assertEqual(revision.provenance.parameters["reference_role"], "content")
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.getpixel((24, 16)), (35, 145, 95))

    def test_candidate_variation_records_parent_revision_and_materializes_new_pixels(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "candidate.png"
            Image.new("RGB", (48, 32), (35, 145, 95)).save(source_path)
            source = AssetRevision.from_content_uri(
                asset_id="asset-candidate",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=48,
                height=32,
                revision_id="rev-candidate",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_generation(
                GenerationRequest(
                    prompt="生成候选变体",
                    model=MOCK_MODEL_ID,
                    width=48,
                    height=32,
                    seed=81,
                    variation_revision=source,
                    variation_strength=0.35,
                    request_id="request-variation",
                    idempotency_key="key-variation",
                )
            )

            revision = job.outputs[0].current_revision
            self.assertEqual(revision.provenance.source_revision_ids, (source.revision_id,))
            self.assertEqual(
                revision.provenance.parameters["variation_of_revision_id"],
                source.revision_id,
            )
            self.assertEqual(revision.provenance.parameters["variation_strength"], 0.35)
            self.assertIn("变体", job.outputs[0].name)
            self.assertNotEqual(
                revision.sha256,
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
            )

    def test_queued_job_can_be_cancelled_idempotently(self) -> None:
        provider = MockProvider(auto_complete=False)
        request = GenerationRequest(
            prompt="等待取消",
            model=MOCK_MODEL_ID,
            request_id="request-cancel",
            idempotency_key="cancel-key",
        )
        queued = provider.submit_generation(request)

        cancelled = provider.cancel(queued.job_id)
        cancelled_again = provider.cancel(queued.job_id)
        replay = provider.submit_generation(request)

        self.assertEqual(queued.status, JobStatus.QUEUED)
        self.assertEqual(cancelled.status, JobStatus.CANCELLED)
        self.assertEqual(cancelled_again, cancelled)
        self.assertEqual(replay, cancelled)
        self.assertEqual(provider.submission_count, 1)

    def test_queued_mock_can_auto_run_when_queried_after_delay(self) -> None:
        provider = MockProvider(auto_complete=False, auto_run_after_seconds=0.01)
        queued = provider.submit_generation(
            GenerationRequest(
                prompt="后台轮询完成",
                model=MOCK_MODEL_ID,
                request_id="request-auto-run",
                idempotency_key="auto-run-key",
            )
        )
        self.assertEqual(queued.status, JobStatus.QUEUED)
        time.sleep(0.02)
        self.assertEqual(provider.get_job(queued.job_id).status, JobStatus.SUCCEEDED)

    def test_configured_failure_is_terminal_and_retryable(self) -> None:
        provider = MockProvider(failing_request_ids={"request-failure"})
        request = GenerationRequest(
            prompt="模拟失败",
            model=MOCK_MODEL_ID,
            request_id="request-failure",
            idempotency_key="failure-key",
        )

        job = provider.submit_generation(request)

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.outputs, ())
        self.assertIsNotNone(job.error)
        assert job.error is not None
        self.assertEqual(job.error.code, "mock_failure")
        self.assertTrue(job.error.retryable)

    def test_idempotent_replay_returns_one_job_and_conflicts_fail_closed(self) -> None:
        provider = MockProvider()
        first_request = GenerationRequest(
            prompt="同一个请求",
            model=MOCK_MODEL_ID,
            request_id="request-one",
            idempotency_key="shared-key",
        )
        replay_request = GenerationRequest(
            prompt="同一个请求",
            model=MOCK_MODEL_ID,
            request_id="request-two",
            idempotency_key="shared-key",
        )
        conflicting_request = GenerationRequest(
            prompt="不同请求",
            model=MOCK_MODEL_ID,
            request_id="request-three",
            idempotency_key="shared-key",
        )

        first = provider.submit_generation(first_request)
        replay = provider.submit_generation(replay_request)

        self.assertEqual(replay.job_id, first.job_id)
        self.assertEqual(provider.submission_count, 1)
        with self.assertRaises(IdempotencyConflictError):
            provider.submit_generation(conflicting_request)

    def test_distinct_jobs_never_reuse_asset_or_revision_identity(self) -> None:
        provider = MockProvider()
        first = provider.submit_generation(
            GenerationRequest(
                prompt="相同参数的独立任务",
                model=MOCK_MODEL_ID,
                request_id="request-distinct-one",
                idempotency_key="distinct-key-one",
            )
        )
        second = provider.submit_generation(
            GenerationRequest(
                prompt="相同参数的独立任务",
                model=MOCK_MODEL_ID,
                request_id="request-distinct-two",
                idempotency_key="distinct-key-two",
            )
        )

        first_output = first.outputs[0]
        second_output = second.outputs[0]
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertNotEqual(first_output.asset_id, second_output.asset_id)
        self.assertNotEqual(
            first_output.current_revision.revision_id,
            second_output.current_revision.revision_id,
        )
        self.assertNotEqual(
            first_output.current_revision.content_uri,
            second_output.current_revision.content_uri,
        )

    def test_content_edit_produces_a_derived_asset(self) -> None:
        provider = MockProvider()
        source = source_revision()
        request = ContentEditRequest(
            source_revision=source,
            prompt="把天空调成晚霞",
            model=MOCK_MODEL_ID,
            strength=0.65,
            request_id="request-edit",
            idempotency_key="edit-key",
        )

        job = provider.submit_edit(request)

        self.assertEqual(job.status, JobStatus.SUCCEEDED)
        revision = job.outputs[0].current_revision
        self.assertEqual(revision.provenance.origin, "content_edit")
        self.assertEqual(revision.provenance.source_revision_ids, (source.revision_id,))
        self.assertEqual((revision.width, revision.height), (640, 480))

    def test_materialized_upscale_uses_source_pixels_and_preserves_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source-alpha.png"
            Image.new("RGBA", (12, 8), (20, 80, 140, 96)).save(source_path)
            source = AssetRevision.from_content_uri(
                asset_id="asset-upscale-source",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=12,
                height=8,
                revision_id="rev-upscale-source",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="本地高清 4×",
                    model=MOCK_MODEL_ID,
                    parameters=(("operation", "upscale"), ("scale", 4)),
                    request_id="request-upscale",
                    idempotency_key="upscale-key",
                )
            )

            revision = job.outputs[0].current_revision
            from urllib.parse import unquote, urlparse

            output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            self.assertTrue(output_path.is_file())
            self.assertEqual((revision.width, revision.height), (48, 32))
            self.assertEqual(hashlib.sha256(output_path.read_bytes()).hexdigest(), revision.sha256)
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.size, (48, 32))
                pixel = output.getpixel((24, 16))
                self.assertTrue(
                    all(
                        abs(actual - expected) <= 1
                        for actual, expected in zip(pixel, (20, 80, 140, 96))
                    )
                )

    def test_materialized_background_removal_creates_feathered_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "product.png"
            source_image = Image.new("RGBA", (32, 24), (250, 250, 250, 255))
            for y in range(6, 18):
                for x in range(8, 24):
                    source_image.putpixel((x, y), (190, 30, 45, 255))
            source_image.putpixel((4, 4), (225, 225, 225, 255))
            source_image.putpixel((12, 10), (190, 30, 45, 128))
            source_image.save(source_path)
            source_image.close()
            source = AssetRevision.from_content_uri(
                asset_id="asset-background-source",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=32,
                height=24,
                revision_id="rev-background-source",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="离线智能去背景",
                    model=MOCK_MODEL_ID,
                    parameters=(
                        ("operation", "remove_background"),
                        ("tolerance", 20),
                        ("feather", 20),
                    ),
                    request_id="request-remove-background",
                    idempotency_key="remove-background-key",
                )
            )

            revision = job.outputs[0].current_revision
            from urllib.parse import unquote, urlparse

            output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            self.assertEqual(revision.media_type, "image/png")
            self.assertEqual((revision.width, revision.height), (32, 24))
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.getpixel((0, 0))[3], 0)
                self.assertEqual(output.getpixel((16, 12))[3], 255)
                self.assertEqual(output.getpixel((12, 10))[3], 128)
                self.assertGreater(output.getpixel((4, 4))[3], 0)
                self.assertLess(output.getpixel((4, 4))[3], 255)
                self.assertEqual(output.getpixel((16, 12))[:3], (190, 30, 45))

    def test_background_removal_handles_edge_subject_gradient_and_source_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "edge-product.png"
            width, height = 96, 64
            source_image = Image.new("RGBA", (width, height))
            for y in range(height):
                for x in range(width):
                    shade = round(190 + x * 60 / (width - 1))
                    source_image.putpixel((x, y), (shade, shade, shade, 255))
            for y in range(height):
                for x in range(32):
                    alpha = 128 if 20 <= y < 44 else 255
                    source_image.putpixel((x, y), (30, 90, 220, alpha))
            source_image.save(source_path)
            source_image.close()
            source = AssetRevision.from_content_uri(
                asset_id="asset-edge-background-source",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=width,
                height=height,
                revision_id="rev-edge-background-source",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="贴边与半透明去背景",
                    model=MOCK_MODEL_ID,
                    parameters=(
                        ("operation", "remove_background"),
                        ("tolerance", 12),
                        ("feather", 4),
                    ),
                    request_id="request-edge-background",
                    idempotency_key="edge-background-key",
                )
            )

            output_path = Path(
                unquote(urlparse(job.outputs[0].current_revision.content_uri).path.lstrip("/"))
            )
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.getpixel((8, 8))[3], 255)
                self.assertEqual(output.getpixel((8, 30))[3], 128)
                self.assertEqual(output.getpixel((40, 30))[3], 0)
                self.assertEqual(output.getpixel((95, 30))[3], 0)
                self.assertEqual(output.getpixel((8, 30))[:3], (30, 90, 220))

    def test_materialized_text_replacement_repairs_region_and_renders_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "old-title.png"
            source_image = Image.new("RGBA", (160, 80), (246, 244, 240, 220))
            for y in range(20, 60):
                for x in range(20, 140):
                    source_image.putpixel((x, y), (15, 15, 15, 220))
            source_image.save(source_path)
            source_image.close()
            source = AssetRevision.from_content_uri(
                asset_id="asset-text-source",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=160,
                height=80,
                revision_id="rev-text-source",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="替换图片文字",
                    model=MOCK_MODEL_ID,
                    parameters=(
                        ("operation", "replace_text"),
                        ("region_left", 0.125),
                        ("region_top", 0.25),
                        ("region_right", 0.875),
                        ("region_bottom", 0.75),
                        ("text", "新标题"),
                        ("font_family", "微软雅黑"),
                        ("font_size", 36),
                        ("color", "#c62828"),
                        ("align", "center"),
                        ("rotation", 0.0),
                    ),
                    request_id="request-replace-text",
                    idempotency_key="replace-text-key",
                )
            )

            revision = job.outputs[0].current_revision
            from urllib.parse import unquote, urlparse

            output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            self.assertEqual((revision.width, revision.height), (160, 80))
            self.assertEqual(revision.provenance.parameters["operation"], "replace_text")
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.getpixel((5, 5)), (246, 244, 240, 220))
                self.assertEqual(output.getpixel((21, 21)), (246, 244, 240, 220))
                replacement_pixels = tuple(output.crop((20, 20, 140, 60)).getdata())
                self.assertTrue(
                    any(
                        red > 100 and red > green * 1.5
                        for red, green, _blue, alpha in replacement_pixels
                        if alpha
                    )
                )
                self.assertFalse(
                    any(
                        red < 30 and green < 30 and blue < 30
                        for red, green, blue, _alpha in replacement_pixels
                    )
                )

    def test_materialized_vertical_text_uses_transposed_layout_without_clipping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "vertical-title.png"
            Image.new("RGBA", (120, 320), (246, 244, 240, 255)).save(source_path)
            source = AssetRevision.from_content_uri(
                asset_id="asset-vertical-text",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=120,
                height=320,
                revision_id="rev-vertical-text",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="替换竖排图片文字",
                    model=MOCK_MODEL_ID,
                    parameters=(
                        ("operation", "replace_text"),
                        ("region_left", 20 / 120),
                        ("region_top", 20 / 320),
                        ("region_right", 100 / 120),
                        ("region_bottom", 300 / 320),
                        ("text", "VERTICAL 2026"),
                        ("font_family", "微软雅黑"),
                        ("font_size", 36),
                        ("color", "#c62828"),
                        ("align", "center"),
                        ("rotation", 90.0),
                    ),
                    request_id="request-vertical-text",
                    idempotency_key="vertical-text-key",
                )
            )

            output_path = Path(
                unquote(urlparse(job.outputs[0].current_revision.content_uri).path.lstrip("/"))
            )
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.getpixel((5, 5)), (246, 244, 240, 255))
                red_pixels = [
                    (x, y)
                    for y in range(20, 300)
                    for x in range(20, 100)
                    if ((pixel := output.getpixel((x, y)))[0] > 100 and pixel[0] > pixel[1] * 1.5)
                ]
                self.assertGreater(len(red_pixels), 500)
                red_width = max(x for x, _y in red_pixels) - min(x for x, _y in red_pixels)
                red_height = max(y for _x, y in red_pixels) - min(y for _x, y in red_pixels)
                self.assertGreater(red_height, red_width * 2)

    def test_materialized_controlled_text_effects_preserve_style_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "styled-title.png"
            source_color = (246, 244, 240, 255)
            Image.new("RGBA", (520, 220), source_color).save(source_path)
            source = AssetRevision.from_content_uri(
                asset_id="asset-styled-text",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=520,
                height=220,
                revision_id="rev-styled-text",
            )
            provider = MockProvider(output_directory=root / "outputs")
            region = (40 / 520, 30 / 220, 480 / 520, 190 / 220)

            for visual_effect in ("plain", "outline", "shadow", "gradient", "glow"):
                with self.subTest(visual_effect=visual_effect):
                    draft = TextReplacementDraft(
                        region=region,
                        text="EFFECT 2026",
                        font_family="微软雅黑",
                        font_size=64,
                        color="#c62828",
                        align="center",
                        visual_effect=visual_effect,  # type: ignore[arg-type]
                        effect_color="#1565c0",
                        effect_size=6,
                    )
                    job = provider.submit_edit(
                        ContentEditRequest(
                            source_revision=source,
                            prompt="替换受控字效文字",
                            model=MOCK_MODEL_ID,
                            parameters=draft.to_parameters(),
                            request_id=f"request-styled-text-{visual_effect}",
                            idempotency_key=f"styled-text-key-{visual_effect}",
                        )
                    )
                    revision = job.outputs[0].current_revision
                    self.assertEqual(revision.provenance.parameters["visual_effect"], visual_effect)
                    self.assertEqual(revision.provenance.parameters["effect_color"], "#1565c0")
                    self.assertEqual(revision.provenance.parameters["effect_size"], 6)
                    output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
                    with Image.open(output_path) as output:
                        output.load()
                        self.assertEqual(output.getpixel((10, 10)), source_color)
                        changed = [
                            (x, y, output.getpixel((x, y)))
                            for y in range(30, 190)
                            for x in range(40, 480)
                            if output.getpixel((x, y)) != source_color
                        ]
                        self.assertGreater(len(changed), 1_000)
                        primary = [
                            (x, y)
                            for x, y, pixel in changed
                            if pixel[0] > 120 and pixel[0] > pixel[2] * 1.25
                        ]
                        secondary = [
                            (x, y)
                            for x, y, pixel in changed
                            if pixel[2] > 100 and pixel[2] > pixel[0] * 1.20
                        ]
                        self.assertGreater(len(primary), 100)
                        if visual_effect == "plain":
                            self.assertEqual(secondary, [])
                        else:
                            self.assertGreater(len(secondary), 100)
                        if visual_effect == "shadow":
                            self.assertGreater(
                                sum(y for _x, y in secondary) / len(secondary),
                                sum(y for _x, y in primary) / len(primary) + 2,
                            )
                        if visual_effect == "gradient":
                            top_red = sum(y for _x, y in primary) / len(primary)
                            bottom_blue = sum(y for _x, y in secondary) / len(secondary)
                            self.assertGreater(bottom_blue, top_red + 8)
                        if visual_effect == "glow":
                            minimum_x = min(x for x, _y in primary)
                            maximum_x = max(x for x, _y in primary)
                            minimum_y = min(y for _x, y in primary)
                            maximum_y = max(y for _x, y in primary)
                            halo = [
                                (x, y)
                                for x, y in secondary
                                if x < minimum_x or x > maximum_x or y < minimum_y or y > maximum_y
                            ]
                            self.assertGreater(len(halo), 100)

    def test_text_effect_provider_contract_rejects_untrusted_values(self) -> None:
        provider = MockProvider()
        draft = TextReplacementDraft(
            region=(0.1, 0.2, 0.9, 0.8),
            text="受控字效",
            visual_effect="outline",
            effect_color="#ffffff",
            effect_size=4,
        )
        base = dict(draft.to_parameters())
        invalid_values = (
            ("visual_effect", "emboss"),
            ("visual_effect", 1),
            ("effect_color", 7),
            ("effect_color", "not-a-color"),
            ("effect_size", True),
            ("effect_size", 65),
            ("effect_size", 0),
        )
        for index, (key, value) in enumerate(invalid_values):
            with (
                self.subTest(key=key, value=value),
                self.assertRaises(UnsupportedProviderRequestError),
            ):
                parameters = dict(base)
                parameters[key] = value
                provider.submit_edit(
                    ContentEditRequest(
                        source_revision=source_revision(),
                        prompt="拒绝无效字效",
                        model=MOCK_MODEL_ID,
                        parameters=tuple(parameters.items()),  # type: ignore[arg-type]
                        request_id=f"request-invalid-effect-{index}",
                        idempotency_key=f"invalid-effect-{index}",
                    )
                )

    def test_materialized_perspective_text_is_warped_and_quad_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "perspective-title.png"
            source_image = Image.new("RGBA", (400, 240), (246, 244, 240, 255))
            quad_pixels = ((70, 75), (330, 45), (350, 175), (50, 155))
            ImageDraw.Draw(source_image).polygon(quad_pixels, fill=(15, 15, 15, 255))
            source_image.save(source_path)
            source_image.close()
            source = AssetRevision.from_content_uri(
                asset_id="asset-perspective-text",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=400,
                height=240,
                revision_id="rev-perspective-text",
            )
            provider = MockProvider(output_directory=root / "outputs")
            quad = (
                70 / 400,
                75 / 240,
                330 / 400,
                45 / 240,
                350 / 400,
                175 / 240,
                50 / 400,
                155 / 240,
            )
            draft = TextReplacementDraft(
                region=(50 / 400, 45 / 240, 350 / 400, 175 / 240),
                text="PERSPECTIVE 2026",
                font_family="微软雅黑",
                font_size=42,
                color="#c62828",
                perspective_quad=quad,
                visual_effect="glow",
                effect_color="#20b8ff",
                effect_size=6,
            )

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="替换透视图片文字",
                    model=MOCK_MODEL_ID,
                    parameters=draft.to_parameters(),
                    request_id="request-perspective-text",
                    idempotency_key="perspective-text-key",
                )
            )

            output_path = Path(
                unquote(urlparse(job.outputs[0].current_revision.content_uri).path.lstrip("/"))
            )
            self.assertEqual(
                job.outputs[0].current_revision.provenance.parameters["visual_effect"],
                "glow",
            )
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.getpixel((55, 50)), (246, 244, 240, 255))
                red_pixels = [
                    (x, y)
                    for y in range(output.height)
                    for x in range(output.width)
                    if (
                        output.getpixel((x, y))[0] > 120
                        and output.getpixel((x, y))[0] > output.getpixel((x, y))[1] * 1.5
                    )
                ]
                self.assertGreater(len(red_pixels), 500)
                mask = Image.new("1", output.size, 0)
                try:
                    ImageDraw.Draw(mask).polygon(quad_pixels, fill=1)
                    self.assertTrue(all(mask.getpixel(point) for point in red_pixels))
                finally:
                    mask.close()

    def test_materialized_curved_text_is_deterministic_and_region_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "curved-title.png"
            source_color = (246, 244, 240, 255)
            Image.new("RGBA", (520, 260), source_color).save(source_path)
            source = AssetRevision.from_content_uri(
                asset_id="asset-curved-text",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=520,
                height=260,
                revision_id="rev-curved-text",
            )
            provider = MockProvider(output_directory=root / "outputs")
            region = (40 / 520, 30 / 260, 480 / 520, 230 / 260)
            draft = TextReplacementDraft(
                region=region,
                text="HHHHHHH",
                font_family="微软雅黑",
                font_size=72,
                color="#c62828",
                align="center",
                curve=0.8,
                visual_effect="glow",
                effect_color="#20b8ff",
                effect_size=6,
            )
            output_hashes: list[str] = []
            center_deltas: list[float] = []

            for index in range(2):
                job = provider.submit_edit(
                    ContentEditRequest(
                        source_revision=source,
                        prompt="替换为上拱弧形标题",
                        model=MOCK_MODEL_ID,
                        parameters=draft.to_parameters(),
                        request_id=f"request-curved-text-{index}",
                        idempotency_key=f"curved-text-key-{index}",
                    )
                )
                revision = job.outputs[0].current_revision
                self.assertEqual(revision.provenance.parameters["curve"], 0.8)
                self.assertEqual(revision.provenance.parameters["visual_effect"], "glow")
                output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
                output_hashes.append(hashlib.sha256(output_path.read_bytes()).hexdigest())
                with Image.open(output_path) as output:
                    output.load()
                    red_pixels = [
                        (x, y)
                        for y in range(output.height)
                        for x in range(output.width)
                        if (
                            (pixel := output.getpixel((x, y)))[0] > 120
                            and pixel[0] > pixel[1] * 1.5
                        )
                    ]
                    self.assertGreater(len(red_pixels), 500)
                    self.assertTrue(all(40 <= x < 480 and 30 <= y < 230 for x, y in red_pixels))
                    self.assertEqual(output.getpixel((20, 20)), source_color)
                    minimum_x = min(x for x, _y in red_pixels)
                    maximum_x = max(x for x, _y in red_pixels)
                    span = maximum_x - minimum_x + 1
                    edge_y = [
                        y
                        for x, y in red_pixels
                        if x < minimum_x + span * 0.22 or x > maximum_x - span * 0.22
                    ]
                    center_y = [
                        y
                        for x, y in red_pixels
                        if minimum_x + span * 0.40 <= x <= minimum_x + span * 0.60
                    ]
                    center_deltas.append(sum(edge_y) / len(edge_y) - sum(center_y) / len(center_y))

            self.assertEqual(len(set(output_hashes)), 1)
            self.assertGreater(min(center_deltas), 8.0)

            downward_draft = TextReplacementDraft(
                region=region,
                text="HHHHHHH",
                font_family="微软雅黑",
                font_size=72,
                color="#c62828",
                align="center",
                curve=-0.8,
            )
            downward_job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="替换为下弯弧形标题",
                    model=MOCK_MODEL_ID,
                    parameters=downward_draft.to_parameters(),
                    request_id="request-curved-text-downward",
                    idempotency_key="curved-text-key-downward",
                )
            )
            downward_revision = downward_job.outputs[0].current_revision
            self.assertEqual(downward_revision.provenance.parameters["curve"], -0.8)
            downward_path = Path(unquote(urlparse(downward_revision.content_uri).path.lstrip("/")))
            with Image.open(downward_path) as output:
                output.load()
                red_pixels = [
                    (x, y)
                    for y in range(output.height)
                    for x in range(output.width)
                    if ((pixel := output.getpixel((x, y)))[0] > 120 and pixel[0] > pixel[1] * 1.5)
                ]
                minimum_x = min(x for x, _y in red_pixels)
                maximum_x = max(x for x, _y in red_pixels)
                span = maximum_x - minimum_x + 1
                edge_y = [
                    y
                    for x, y in red_pixels
                    if x < minimum_x + span * 0.22 or x > maximum_x - span * 0.22
                ]
                center_y = [
                    y
                    for x, y in red_pixels
                    if minimum_x + span * 0.40 <= x <= minimum_x + span * 0.60
                ]
                downward_delta = sum(edge_y) / len(edge_y) - sum(center_y) / len(center_y)
            self.assertLess(downward_delta, -8.0)

    def test_materialized_region_erase_repairs_gradient_and_records_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "object-on-gradient.png"
            source_image = Image.new("RGBA", (80, 60))
            for y in range(60):
                for x in range(80):
                    source_image.putpixel((x, y), (40 + x, 60 + y, 120, 180 + x // 2))
            for y in range(20, 40):
                for x in range(28, 52):
                    source_image.putpixel((x, y), (245, 25, 35, 255))
            source_image.save(source_path)
            source_image.close()
            source = AssetRevision.from_content_uri(
                asset_id="asset-region-repair-source",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=80,
                height=60,
                revision_id="rev-region-repair-source",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="擦除框选物体",
                    model=MOCK_MODEL_ID,
                    parameters=(
                        ("operation", "erase_region"),
                        ("region_left", 28 / 80),
                        ("region_top", 20 / 60),
                        ("region_right", 52 / 80),
                        ("region_bottom", 40 / 60),
                        ("feather", 0),
                    ),
                    request_id="request-region-repair",
                    idempotency_key="region-repair-key",
                )
            )

            revision = job.outputs[0].current_revision
            output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            self.assertEqual(revision.provenance.parameters["operation"], "erase_region")
            self.assertEqual(revision.provenance.parameters["feather"], 0)
            self.assertEqual(revision.provenance.source_revision_ids, (source.revision_id,))
            with Image.open(output_path) as output:
                output.load()
                self.assertEqual(output.getpixel((4, 4)), (44, 64, 120, 182))
                center = output.getpixel((40, 30))
                self.assertLess(abs(center[0] - 80), 2)
                self.assertLess(abs(center[1] - 90), 3)
                self.assertLess(abs(center[3] - 200), 2)
                self.assertNotEqual(center, (245, 25, 35, 255))

    def test_materialized_mask_erase_isolates_pixels_and_records_only_mask_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "masked-object.png"
            mask_path = root / "selection.png"
            source_image = Image.new("RGBA", (80, 60))
            for y in range(60):
                for x in range(80):
                    source_image.putpixel((x, y), (40 + x, 60 + y, 120, 255))
            ImageDraw.Draw(source_image).ellipse((28, 18, 52, 42), fill=(245, 25, 35, 255))
            source_image.save(source_path)
            source_image.close()
            mask_image = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
            ImageDraw.Draw(mask_image).ellipse((28, 18, 52, 42), fill=(255, 255, 255, 255))
            mask_image.save(mask_path)
            mask_image.close()
            suggestion = detect_smart_selection(mask_path, padding_ratio=0)[0]
            assert suggestion.mask_size is not None
            assert suggestion.mask_rle is not None
            source = AssetRevision.from_content_uri(
                asset_id="asset-mask-repair-source",
                sequence=1,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=80,
                height=60,
                revision_id="rev-mask-repair-source",
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="按像素蒙版擦除主体",
                    model=MOCK_MODEL_ID,
                    parameters=(
                        ("operation", "erase_mask"),
                        ("region_left", suggestion.region[0]),
                        ("region_top", suggestion.region[1]),
                        ("region_right", suggestion.region[2]),
                        ("region_bottom", suggestion.region[3]),
                        ("feather", 0),
                        ("mask_width", suggestion.mask_size[0]),
                        ("mask_height", suggestion.mask_size[1]),
                        ("mask_rle", suggestion.mask_rle),
                    ),
                    request_id="request-mask-repair",
                    idempotency_key="mask-repair-key",
                )
            )

            revision = job.outputs[0].current_revision
            output_path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            parameters = revision.provenance.parameters
            self.assertEqual(parameters["operation"], "erase_mask")
            self.assertEqual(parameters["mask_width"], 80)
            self.assertNotIn("mask_rle", parameters)
            self.assertEqual(len(str(parameters["mask_sha256"])), 64)
            with Image.open(output_path) as output:
                output.load()
                self.assertNotEqual(output.getpixel((40, 30)), (245, 25, 35, 255))
                self.assertEqual(output.getpixel((28, 18)), (68, 78, 120, 255))
                self.assertEqual(output.getpixel((4, 4)), (44, 64, 120, 255))

    def test_mask_erase_rejects_malformed_mask_before_materialization(self) -> None:
        provider = MockProvider()
        with self.assertRaises(UnsupportedProviderRequestError):
            provider.submit_edit(
                ContentEditRequest(
                    source_revision=source_revision(),
                    prompt="拒绝损坏蒙版",
                    model=MOCK_MODEL_ID,
                    parameters=(
                        ("operation", "erase_mask"),
                        ("region_left", 0.1),
                        ("region_top", 0.1),
                        ("region_right", 0.9),
                        ("region_bottom", 0.9),
                        ("feather", 0),
                        ("mask_width", 8),
                        ("mask_height", 8),
                        ("mask_rle", "not-base64"),
                    ),
                    request_id="request-invalid-mask-repair",
                    idempotency_key="invalid-mask-repair-key",
                )
            )


if __name__ == "__main__":
    unittest.main()
