"""Run the M7 four-mode stability, cancellation, recovery, and memory gate."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import gc
import json
import platform
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.run_benchmarks import _process_rss_bytes
from image_ops import open_image
from light_image_editor.domain import (
    ContentEditRequest,
    ConversionQueue,
    ConversionRunner,
    ConversionTaskStatus,
    ExportProfile,
    GenerationRequest,
    JobStatus,
    TransformRecipe,
    TransformStep,
)
from light_image_editor.image_loader import ImageLoader
from light_image_editor.providers import MOCK_MODEL_ID, MockProvider
from light_image_editor.services import JobManager, SQLiteAssetRepository
from light_image_editor.shell import AssetHandoff, ModeId, ModeRouter, RevisionHandoff
from light_image_editor.version import __version__

PROFILES: dict[str, dict[str, int | float]] = {
    "smoke": {
        "viewer_iterations": 200,
        "mode_switches": 400,
        "conversion_rounds": 2,
        "conversion_inputs_per_round": 32,
        "ai_success_per_kind": 4,
        "ai_cancel_per_kind": 4,
        "max_final_rss_growth_mib": 128.0,
        "max_peak_rss_growth_mib": 256.0,
    },
    "standard": {
        "viewer_iterations": 20_000,
        "mode_switches": 20_000,
        "conversion_rounds": 10,
        "conversion_inputs_per_round": 100,
        "ai_success_per_kind": 50,
        "ai_cancel_per_kind": 50,
        "max_final_rss_growth_mib": 192.0,
        "max_peak_rss_growth_mib": 384.0,
    },
}


@dataclass(frozen=True, slots=True)
class FourModeSoakThresholds:
    viewer_iterations: int = 20_000
    mode_switches: int = 20_000
    conversion_successes: int = 1_000
    conversion_failures: int = 10
    ai_success_per_kind: int = 50
    ai_cancel_per_kind: int = 50
    max_final_rss_growth_mib: float = 192.0
    max_peak_rss_growth_mib: float = 384.0
    monotonic_growth_review_mib: float = 64.0


def _create_fixture(path: Path, size: tuple[int, int], seed: int) -> Path:
    image = Image.new("RGB", size, (seed * 17 % 256, 50, 100))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        (size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4),
        fill=(40, seed * 29 % 256, 190),
    )
    image.save(path, "PNG")
    image.close()
    return path.resolve()


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme.casefold() != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("长稳门禁只接受本地文件 URI")
    value = unquote(parsed.path)
    if len(value) >= 3 and value[0] == "/" and value[2] == ":":
        value = value[1:]
    return Path(value).resolve(strict=True)


def _viewer_soak(paths: list[Path], iterations: int) -> dict[str, object]:
    loader = ImageLoader(max_workers=2, cache_size=3)
    started = time.perf_counter()
    failures: list[str] = []
    try:
        for index in range(iterations):
            try:
                image = loader.load(paths[index % len(paths)]).result(timeout=20)
            except Exception as error:
                failures.append(f"{type(error).__name__}: {error}")
                continue
            image.close()
    finally:
        cache_items = len(loader._cache)
        loader.shutdown()
    return {
        "passed": not failures and cache_items <= 3,
        "iterations": iterations,
        "failures": failures[:10],
        "cache_items": cache_items,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _mode_router_soak(source: Path, switches: int) -> dict[str, object]:
    router = ModeRouter()
    asset = AssetHandoff("asset-soak", uri=source.as_uri(), display_name=source.name)
    revision = RevisionHandoff("revision-soak", asset.id, number=1)
    router.set_handoff(asset, revision)
    modes = tuple(ModeId)
    started = time.perf_counter()
    for index in range(switches):
        mode = modes[index % len(modes)]
        router.switch(mode)
        router.update_state({"last_cycle": index, "mode": mode.value})
    contexts = router.contexts
    handoff_preserved = all(
        context.asset_id == asset.id and context.revision_id == revision.id
        for context in contexts.values()
    )
    state_preserved = all(
        context.state.get("mode") == mode.value for mode, context in contexts.items()
    )
    return {
        "passed": handoff_preserved and state_preserved,
        "switches": switches,
        "handoff_preserved": handoff_preserved,
        "per_mode_state_preserved": state_preserved,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _conversion_recipe() -> TransformRecipe:
    return TransformRecipe(
        name="M7 四模式长稳转换",
        steps=(TransformStep.rotate(90), TransformStep.resize(max_width=80, max_height=80)),
        export_profile=ExportProfile(
            output_format="PNG",
            preserve_metadata=False,
            rename_template="{stem}_{index:04d}",
            conflict_policy="error",
        ),
    )


def _conversion_soak(root: Path, rounds: int, per_round: int) -> dict[str, object]:
    inputs = root / "conversion-inputs"
    inputs.mkdir()
    valid = [
        _create_fixture(inputs / f"valid-{index:04d}.png", (64, 48), index)
        for index in range(per_round)
    ]
    corrupt = inputs / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    started = time.perf_counter()
    succeeded = failed = verified = 0
    unexpected: list[str] = []
    for round_index in range(rounds):
        queue = ConversionQueue()
        queue.add_files((*valid, corrupt))
        runner = ConversionRunner(
            queue,
            _conversion_recipe(),
            root / f"conversion-output-{round_index:03d}",
            max_workers=4,
        )
        result = runner.run()
        succeeded += result.counts[ConversionTaskStatus.SUCCEEDED]
        failed += result.counts[ConversionTaskStatus.FAILED]
        for task in result.tasks:
            if task.status is not ConversionTaskStatus.SUCCEEDED or task.output is None:
                continue
            try:
                image = open_image(task.output)
                image.close()
            except Exception as error:
                unexpected.append(f"{task.output.name}: {type(error).__name__}: {error}")
            else:
                verified += 1
    expected_success = rounds * per_round
    return {
        "passed": (
            succeeded == expected_success
            and failed == rounds
            and verified == expected_success
            and not unexpected
        ),
        "rounds": rounds,
        "inputs_per_round": per_round,
        "succeeded": succeeded,
        "injected_failures": rounds,
        "failed": failed,
        "verified_outputs": verified,
        "unexpected": unexpected[:10],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _ai_soak(root: Path, success_per_kind: int, cancel_per_kind: int) -> dict[str, object]:
    source_path = _create_fixture(root / "ai-source.png", (64, 48), 500)
    repository = SQLiteAssetRepository(root / "assets.db", root / "blobs")
    source = repository.ensure_external(source_path)
    provider = MockProvider(output_directory=root / "provider-success")
    jobs_path = root / "successful-jobs.json"
    manager = JobManager(provider, jobs_path, max_workers=4)
    imported_revision_ids: list[str] = []
    edit_succeeded = generation_succeeded = 0
    isolated_failure = False
    started = time.perf_counter()
    try:
        failed_request = GenerationRequest(
            prompt="M7 故障隔离",
            model=MOCK_MODEL_ID,
            width=64,
            height=48,
            request_id="m7-isolated-failure",
            idempotency_key="m7-isolated-failure-key",
        )
        failing_manager = JobManager(
            MockProvider(
                failing_request_ids={failed_request.request_id},
                output_directory=root / "provider-failure",
            ),
            root / "failed-job.json",
        )
        try:
            submitted = failing_manager.submit_generation(failed_request)
            isolated_failure = (
                failing_manager.wait(submitted.job_id, timeout=10).status is JobStatus.FAILED
            )
        finally:
            failing_manager.shutdown()

        for index in range(success_per_kind):
            edit_request = ContentEditRequest(
                source_revision=source,
                prompt="M7 去背景长稳",
                model=MOCK_MODEL_ID,
                parameters=(
                    ("operation", "remove_background"),
                    ("background_tolerance", 28),
                    ("edge_feather", 2),
                ),
                request_id=f"m7-edit-{index}",
                idempotency_key=f"m7-edit-key-{index}",
            )
            edit_job = manager.submit_edit(edit_request)
            edit_done = manager.wait(edit_job.job_id, timeout=20)
            if edit_done.status is JobStatus.SUCCEEDED and len(edit_done.outputs) == 1:
                revision = edit_done.outputs[0].current_revision
                imported = repository.import_managed(
                    _file_uri_path(revision.content_uri),
                    provenance=revision.provenance,
                    asset_id=source.asset_id,
                    parent_revision_id=source.revision_id,
                    expected_media_type="image/png",
                )
                imported_revision_ids.append(imported.revision_id)
                edit_succeeded += 1

            generation_request = GenerationRequest(
                prompt="M7 四模式长稳候选",
                model=MOCK_MODEL_ID,
                width=64,
                height=48,
                seed=20260805 + index,
                request_id=f"m7-generation-{index}",
                idempotency_key=f"m7-generation-key-{index}",
            )
            generation_job = manager.submit_generation(generation_request)
            generation_done = manager.wait(generation_job.job_id, timeout=20)
            if generation_done.status is JobStatus.SUCCEEDED and len(generation_done.outputs) == 1:
                revision = generation_done.outputs[0].current_revision
                imported = repository.import_managed(
                    _file_uri_path(revision.content_uri),
                    provenance=revision.provenance,
                    asset_id=revision.asset_id,
                    expected_media_type="image/png",
                )
                imported_revision_ids.append(imported.revision_id)
                generation_succeeded += 1
    finally:
        manager.shutdown()

    cancel_provider = MockProvider(auto_complete=False, output_directory=root / "provider-cancel")
    cancel_jobs_path = root / "cancelled-jobs.json"
    cancel_manager = JobManager(cancel_provider, cancel_jobs_path, max_workers=4)
    cancelled_ids: list[str] = []
    try:
        for index in range(cancel_per_kind):
            requests = (
                ContentEditRequest(
                    source_revision=source,
                    prompt="M7 取消改图",
                    model=MOCK_MODEL_ID,
                    request_id=f"m7-cancel-edit-{index}",
                    idempotency_key=f"m7-cancel-edit-key-{index}",
                ),
                GenerationRequest(
                    prompt="M7 取消生图",
                    model=MOCK_MODEL_ID,
                    width=64,
                    height=48,
                    request_id=f"m7-cancel-generation-{index}",
                    idempotency_key=f"m7-cancel-generation-key-{index}",
                ),
            )
            for request in requests:
                submitted = (
                    cancel_manager.submit_edit(request)
                    if isinstance(request, ContentEditRequest)
                    else cancel_manager.submit_generation(request)
                )
                queued = cancel_manager.wait(submitted.job_id, timeout=10)
                if queued.status is not JobStatus.QUEUED:
                    continue
                cancelled = cancel_manager.cancel(queued.job_id)
                if cancelled.status is JobStatus.CANCELLED:
                    cancelled_ids.append(cancelled.job_id)
    finally:
        cancel_manager.shutdown()

    recovered_provider = MockProvider(output_directory=root / "provider-recovery")
    recovered_manager = JobManager(recovered_provider, jobs_path)
    try:
        recovered_jobs = recovered_manager.list_jobs()
        completed_recovered = all(job.status is JobStatus.SUCCEEDED for job in recovered_jobs)
        no_success_replay = recovered_provider.submission_count == 0
    finally:
        recovered_manager.shutdown()
    recovered_cancel_provider = MockProvider(auto_complete=False)
    recovered_cancel_manager = JobManager(recovered_cancel_provider, cancel_jobs_path)
    try:
        recovered_cancel_jobs = recovered_cancel_manager.list_jobs()
        cancelled_recovered = all(
            job.status is JobStatus.CANCELLED for job in recovered_cancel_jobs
        )
        no_cancel_replay = recovered_cancel_provider.submission_count == 0
    finally:
        recovered_cancel_manager.shutdown()
    reopened_repository = SQLiteAssetRepository(root / "assets.db", root / "blobs")
    restored_resources = sum(
        reopened_repository.resolve_content(revision_id).is_file()
        for revision_id in imported_revision_ids
    )
    expected_cancelled = cancel_per_kind * 2
    expected_resources = success_per_kind * 2
    return {
        "passed": (
            isolated_failure
            and edit_succeeded == success_per_kind
            and generation_succeeded == success_per_kind
            and len(cancelled_ids) == expected_cancelled
            and completed_recovered
            and cancelled_recovered
            and no_success_replay
            and no_cancel_replay
            and restored_resources == expected_resources
        ),
        "edit_succeeded": edit_succeeded,
        "generation_succeeded": generation_succeeded,
        "cancelled_per_kind": cancel_per_kind,
        "cancelled_total": len(cancelled_ids),
        "isolated_provider_failure": isolated_failure,
        "completed_jobs_recovered": completed_recovered,
        "cancelled_jobs_recovered": cancelled_recovered,
        "recovery_did_not_replay": no_success_replay and no_cancel_replay,
        "managed_resources_expected": expected_resources,
        "managed_resources_restored": restored_resources,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def evaluate_four_mode_soak(
    result: dict[str, Any],
    thresholds: FourModeSoakThresholds | None = None,
) -> list[str]:
    limits = thresholds or FourModeSoakThresholds()
    violations: list[str] = []
    checks = (
        ("看图循环", int(result["results"]["viewer"]["iterations"]), limits.viewer_iterations),
        ("模式切换", int(result["results"]["mode_router"]["switches"]), limits.mode_switches),
        (
            "转换成功",
            int(result["results"]["conversion"]["succeeded"]),
            limits.conversion_successes,
        ),
        (
            "转换故障隔离",
            int(result["results"]["conversion"]["failed"]),
            limits.conversion_failures,
        ),
        ("改图成功", int(result["results"]["ai"]["edit_succeeded"]), limits.ai_success_per_kind),
        (
            "生图成功",
            int(result["results"]["ai"]["generation_succeeded"]),
            limits.ai_success_per_kind,
        ),
        (
            "每类 AI 取消",
            int(result["results"]["ai"]["cancelled_per_kind"]),
            limits.ai_cancel_per_kind,
        ),
    )
    for label, actual, minimum in checks:
        if actual < minimum:
            violations.append(f"{label}样本 {actual} < {minimum}")
    for name, phase in result["results"].items():
        if not bool(phase["passed"]):
            violations.append(f"{name} 阶段未通过")
    memory = result["memory"]
    final_growth = float(memory["final_growth_mib"])
    peak_growth = float(memory["peak_growth_mib"])
    if final_growth > limits.max_final_rss_growth_mib:
        violations.append(
            f"最终 RSS 增长 {final_growth:.1f} MiB > {limits.max_final_rss_growth_mib:.1f} MiB"
        )
    if peak_growth > limits.max_peak_rss_growth_mib:
        violations.append(
            f"峰值 RSS 增长 {peak_growth:.1f} MiB > {limits.max_peak_rss_growth_mib:.1f} MiB"
        )
    if (
        bool(memory["monotonic_non_decreasing"])
        and final_growth > limits.monotonic_growth_review_mib
    ):
        violations.append("阶段间 RSS 持续单调增长且超过复核阈值")
    return violations


def run(profile_name: str) -> dict[str, Any]:
    profile = PROFILES[profile_name]
    gc.collect()
    baseline = _process_rss_bytes()
    samples = [baseline]
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="light-image-editor-four-mode-soak-") as raw:
        root = Path(raw)
        viewer_dir = root / "viewer"
        viewer_dir.mkdir()
        viewer_paths = [
            _create_fixture(viewer_dir / f"viewer-{index:03d}.png", (96, 64), index)
            for index in range(24)
        ]
        results: dict[str, dict[str, object]] = {}
        results["viewer"] = _viewer_soak(viewer_paths, int(profile["viewer_iterations"]))
        gc.collect()
        samples.append(_process_rss_bytes())
        results["mode_router"] = _mode_router_soak(viewer_paths[0], int(profile["mode_switches"]))
        gc.collect()
        samples.append(_process_rss_bytes())
        results["conversion"] = _conversion_soak(
            root,
            int(profile["conversion_rounds"]),
            int(profile["conversion_inputs_per_round"]),
        )
        gc.collect()
        samples.append(_process_rss_bytes())
        results["ai"] = _ai_soak(
            root,
            int(profile["ai_success_per_kind"]),
            int(profile["ai_cancel_per_kind"]),
        )
        gc.collect()
        samples.append(_process_rss_bytes())
    final = _process_rss_bytes()
    samples.append(final)
    result: dict[str, Any] = {
        "schema_version": 1,
        "application_version": __version__,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": profile_name,
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "fixtures": dict(profile),
        "results": results,
        "memory": {
            "baseline_rss_mib": round(baseline / 1024 / 1024, 3),
            "peak_rss_mib": round(max(samples) / 1024 / 1024, 3),
            "final_rss_mib": round(final / 1024 / 1024, 3),
            "peak_growth_mib": round(max(0, max(samples) - baseline) / 1024 / 1024, 3),
            "final_growth_mib": round(max(0, final - baseline) / 1024 / 1024, 3),
            "monotonic_non_decreasing": all(
                samples[index] <= samples[index + 1] for index in range(len(samples) - 1)
            ),
            "samples_mib": [round(sample / 1024 / 1024, 3) for sample in samples],
        },
        "thresholds": asdict(FourModeSoakThresholds()),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    result["violations"] = [] if profile_name == "smoke" else evaluate_four_mode_soak(result)
    result["passed"] = (
        all(bool(item["passed"]) for item in results.values()) and not result["violations"]
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="standard")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fail", action="store_true")
    arguments = parser.parse_args()
    result = run(arguments.profile)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output is not None:
        destination = arguments.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] or arguments.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
