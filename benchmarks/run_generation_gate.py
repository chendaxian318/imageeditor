"""Run the M6 generation fidelity, provenance, and performance acceptance gate."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from PIL import Image, ImageChops, ImageOps, ImageStat
from PIL import __version__ as pillow_version

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.run_benchmarks import _metric
from light_image_editor.domain import (
    AssetRecord,
    AssetRevision,
    GenerationReference,
    GenerationRequest,
    JobStatus,
    Provenance,
)
from light_image_editor.providers import MOCK_MODEL_ID, AIProvider, MockProvider
from light_image_editor.version import __version__

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "image_size": (128, 96),
        "repetitions": 2,
        "candidate_count": 2,
        "reference_count": 2,
    },
    "standard": {
        "image_size": (512, 384),
        "repetitions": 3,
        "candidate_count": 4,
        "reference_count": 4,
    },
}

REFERENCE_COLORS = (
    (220, 40, 40),
    (40, 80, 220),
    (40, 220, 80),
    (225, 180, 35),
)
REFERENCE_ROLES = ("content", "style", "composition", "content")


@dataclass(frozen=True, slots=True)
class GenerationGateThresholds:
    minimum_candidates_per_request: int = 4
    minimum_reference_count: int = 4
    minimum_unique_color_count: int = 3
    minimum_luma_span: float = 20.0
    maximum_reference_color_mae: float = 2.0
    minimum_variation_change_ratio: float = 0.05
    maximum_operation_p95_ms: float = 5_000.0


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme.casefold() != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("门禁结果必须是本地文件 URI")
    value = unquote(parsed.path)
    if len(value) >= 3 and value[0] == "/" and value[2] == ":":
        value = value[1:]
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("门禁结果必须使用绝对本地路径")
    return path.resolve(strict=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pixel_hash(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    try:
        return hashlib.sha256(rgb.tobytes()).hexdigest()
    finally:
        rgb.close()


def _source_revision(path: Path, *, suffix: str) -> AssetRevision:
    with Image.open(path) as image:
        image.load()
        size = image.size
    return AssetRevision(
        asset_id=f"asset_generation_gate_{suffix}",
        sequence=1,
        content_uri=path.resolve().as_uri(),
        media_type="image/png",
        sha256=_file_sha256(path),
        provenance=Provenance.imported(source=path.name),
        revision_id=f"rev_generation_gate_{suffix}",
        byte_size=path.stat().st_size,
        width=size[0],
        height=size[1],
    )


def _submit_generation(
    provider: AIProvider,
    request: GenerationRequest,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[tuple[AssetRecord, ...], float]:
    started = time.perf_counter()
    snapshot = provider.submit_generation(request)
    deadline = time.monotonic() + timeout_seconds
    while not snapshot.status.terminal and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = provider.get_job(snapshot.job_id)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if not snapshot.status.terminal:
        raise TimeoutError(f"生成门禁任务超时：{snapshot.job_id}")
    if snapshot.status is not JobStatus.SUCCEEDED:
        detail = "" if snapshot.error is None else f"：{snapshot.error.code}"
        raise RuntimeError(f"生成门禁任务未成功{detail}")
    if not snapshot.outputs:
        raise RuntimeError("生成门禁任务成功但没有候选")
    return snapshot.outputs, elapsed_ms


def _inspect_revision(
    revision: AssetRevision,
    expected_size: tuple[int, int],
) -> dict[str, Any]:
    path = _file_uri_path(revision.content_uri)
    with Image.open(path) as image:
        image.load()
        rgb = image.convert("RGB")
        grayscale = ImageOps.grayscale(rgb)
        try:
            extrema = cast(tuple[int, int], grayscale.getextrema())
            color_count = len(rgb.getcolors(maxcolors=1_000_000) or ())
            pixel_hash = _pixel_hash(rgb)
            luma_stddev = float(ImageStat.Stat(grayscale).stddev[0])
            mode = image.mode
            size = image.size
            image_format = image.format
        finally:
            grayscale.close()
            rgb.close()
    return {
        "revision_id": revision.revision_id,
        "asset_id": revision.asset_id,
        "size": list(size),
        "mode": mode,
        "format": image_format,
        "pixel_hash": pixel_hash,
        "file_sha256_matches": _file_sha256(path) == revision.sha256,
        "byte_size_matches": revision.byte_size == path.stat().st_size,
        "declared_size_matches": (revision.width, revision.height) == expected_size,
        "decoded_size_matches": size == expected_size,
        "media_type_is_png": revision.media_type == "image/png",
        "unique_color_count": color_count,
        "luma_span": extrema[1] - extrema[0],
        "luma_stddev": round(luma_stddev, 6),
    }


def _generation_provenance_complete(
    revision: AssetRevision,
    request: GenerationRequest,
    candidate_index: int,
) -> bool:
    provenance = revision.provenance
    parameters = provenance.parameters
    return (
        provenance.origin == "generation"
        and provenance.provider_id is not None
        and provenance.model == request.model
        and provenance.request_id == request.request_id
        and provenance.source_revision_ids == ()
        and parameters.get("prompt") == request.prompt
        and parameters.get("negative_prompt") == request.negative_prompt
        and parameters.get("width") == request.width
        and parameters.get("height") == request.height
        and parameters.get("seed") == request.seed
        and parameters.get("style_preset") == request.style_preset
        and parameters.get("candidate") == candidate_index
    )


def _run_text_to_image_probe(
    provider: AIProvider,
    model_id: str,
    size: tuple[int, int],
    repetitions: int,
    candidate_count: int,
) -> tuple[dict[str, Any], AssetRevision]:
    elapsed: list[float] = []
    runs: list[list[dict[str, Any]]] = []
    revision_ids: list[str] = []
    asset_ids: list[str] = []
    provenance_checks: list[bool] = []
    candidate_counts: list[int] = []
    first_revision: AssetRevision | None = None
    for repetition in range(repetitions):
        request = GenerationRequest(
            prompt="暖色纸张上的现代中文海报，几何构图",
            negative_prompt="水印、边框、模糊文字",
            model=model_id,
            width=size[0],
            height=size[1],
            count=candidate_count,
            seed=20260805,
            style_preset="watercolor",
            request_id=f"generation-gate-text-{repetition}",
            idempotency_key=f"generation-gate-text-key-{repetition}",
        )
        records, elapsed_ms = _submit_generation(provider, request)
        elapsed.append(elapsed_ms)
        candidate_counts.append(len(records))
        inspected_run: list[dict[str, Any]] = []
        for candidate_index, record in enumerate(records):
            revision = record.current_revision
            if first_revision is None:
                first_revision = revision
            revision_ids.append(revision.revision_id)
            asset_ids.append(revision.asset_id)
            provenance_checks.append(
                _generation_provenance_complete(revision, request, candidate_index)
            )
            inspected_run.append(_inspect_revision(revision, size))
        runs.append(inspected_run)
    if first_revision is None:
        raise RuntimeError("文生图门禁没有生成结果")
    hash_runs = [tuple(item["pixel_hash"] for item in run) for run in runs]
    unique_ratios = [len(set(hashes)) / max(1, len(hashes)) for hashes in hash_runs]
    flat_outputs = [item for run in runs for item in run]
    return (
        {
            "request_count": repetitions,
            "requested_candidates_per_request": candidate_count,
            "minimum_candidates_per_request": min(candidate_counts),
            "maximum_candidates_per_request": max(candidate_counts),
            "all_candidate_counts_match": all(
                count == candidate_count for count in candidate_counts
            ),
            "output_count": len(flat_outputs),
            "runs": runs,
            "all_outputs_valid_png": all(
                item["format"] == "PNG"
                and item["media_type_is_png"]
                and item["file_sha256_matches"]
                and item["byte_size_matches"]
                and item["declared_size_matches"]
                and item["decoded_size_matches"]
                for item in flat_outputs
            ),
            "fixed_seed_reproduces_each_candidate": len(set(hash_runs)) == 1,
            "minimum_candidate_uniqueness_ratio": round(min(unique_ratios), 6),
            "distinct_asset_and_revision_identity": (
                len(set(revision_ids)) == len(revision_ids)
                and len(set(asset_ids)) == len(asset_ids)
            ),
            "provenance_complete": all(provenance_checks),
            "minimum_unique_color_count": min(
                int(item["unique_color_count"]) for item in flat_outputs
            ),
            "minimum_luma_span": min(float(item["luma_span"]) for item in flat_outputs),
            "minimum_luma_stddev": round(
                min(float(item["luma_stddev"]) for item in flat_outputs),
                6,
            ),
            "elapsed_ms": _metric(elapsed),
        },
        first_revision,
    )


def _transformed_reference_color(
    color: tuple[int, int, int],
    role: str,
) -> tuple[int, int, int]:
    if role != "composition":
        return color
    sample = Image.new("RGB", (1, 1), color)
    grayscale = ImageOps.grayscale(sample)
    try:
        value = cast(int, grayscale.getpixel((0, 0)))
        return (value, value, value)
    finally:
        grayscale.close()
        sample.close()


def _run_multi_reference_probe(
    root: Path,
    provider: AIProvider,
    model_id: str,
    size: tuple[int, int],
    repetitions: int,
    reference_count: int,
) -> dict[str, Any]:
    references: list[GenerationReference] = []
    expected_sources: list[str] = []
    transformed_colors: list[tuple[int, int, int]] = []
    strength = 1.0 / reference_count
    for index in range(reference_count):
        path = root / f"reference-{index}.png"
        color = REFERENCE_COLORS[index]
        role = REFERENCE_ROLES[index]
        image = Image.new("RGB", size, color)
        image.save(path, "PNG")
        image.close()
        revision = _source_revision(path, suffix=f"reference_{index}")
        references.append(GenerationReference(revision=revision, role=role, strength=strength))
        expected_sources.append(revision.revision_id)
        transformed_colors.append(_transformed_reference_color(color, role))

    expected_color = tuple(
        round(sum(color[channel] * strength for color in transformed_colors))
        for channel in range(3)
    )
    elapsed: list[float] = []
    inspected: list[dict[str, Any]] = []
    center_pixels: list[list[int]] = []
    color_mae: list[float] = []
    pixel_hashes: list[str] = []
    provenance_checks: list[bool] = []
    identities: list[tuple[str, str]] = []
    output_count_checks: list[bool] = []
    first_request: GenerationRequest | None = None
    for repetition in range(repetitions):
        primary = references[0]
        request = GenerationRequest(
            prompt="严格融合多张参考图",
            model=model_id,
            width=size[0],
            height=size[1],
            count=1,
            seed=314159,
            reference_revision=primary.revision,
            reference_role=primary.role,
            reference_strength=primary.strength,
            additional_references=tuple(references[1:]),
            request_id=f"generation-gate-reference-{repetition}",
            idempotency_key=f"generation-gate-reference-key-{repetition}",
        )
        if first_request is None:
            first_request = request
        records, elapsed_ms = _submit_generation(provider, request)
        elapsed.append(elapsed_ms)
        output_count_checks.append(len(records) == 1)
        revision = records[0].current_revision
        identities.append((revision.asset_id, revision.revision_id))
        inspection = _inspect_revision(revision, size)
        inspected.append(inspection)
        pixel_hashes.append(str(inspection["pixel_hash"]))
        path = _file_uri_path(revision.content_uri)
        with Image.open(path) as output:
            output.load()
            rgb = output.convert("RGB")
            try:
                pixel = cast(tuple[int, int, int], rgb.getpixel((0, 0)))
            finally:
                rgb.close()
        center_pixels.append(list(pixel))
        color_mae.append(
            sum(abs(pixel[channel] - expected_color[channel]) for channel in range(3)) / 3
        )
        parameters = revision.provenance.parameters
        provenance_checks.append(
            revision.provenance.source_revision_ids == tuple(expected_sources)
            and parameters.get("reference_count") == reference_count
            and all(
                parameters.get(f"reference_{index}_revision_id") == reference.revision.revision_id
                and parameters.get(f"reference_{index}_role") == reference.role
                and parameters.get(f"reference_{index}_strength") == reference.strength
                for index, reference in enumerate(references)
            )
        )
    assert first_request is not None
    changed_strength = replace(first_request, reference_strength=strength / 2)
    reversed_references = tuple(reversed(references))
    reversed_primary = reversed_references[0]
    changed_order = replace(
        first_request,
        reference_revision=reversed_primary.revision,
        reference_role=reversed_primary.role,
        reference_strength=reversed_primary.strength,
        additional_references=reversed_references[1:],
    )
    return {
        "reference_count": reference_count,
        "source_revision_ids": expected_sources,
        "roles": [reference.role for reference in references],
        "strengths": [reference.strength for reference in references],
        "expected_weighted_rgb": list(expected_color),
        "observed_rgb": center_pixels,
        "maximum_weighted_color_mae": round(max(color_mae), 6),
        "all_outputs_valid_png": all(
            item["format"] == "PNG" and item["file_sha256_matches"] and item["decoded_size_matches"]
            for item in inspected
        ),
        "all_output_counts_match": all(output_count_checks),
        "deterministic_pixels": len(set(pixel_hashes)) == 1,
        "distinct_asset_and_revision_identity": len(set(identities)) == len(identities),
        "provenance_records_every_reference": all(provenance_checks),
        "fingerprint_changes_with_strength": (
            first_request.fingerprint() != changed_strength.fingerprint()
        ),
        "fingerprint_changes_with_order": (
            first_request.fingerprint() != changed_order.fingerprint()
        ),
        "elapsed_ms": _metric(elapsed),
    }


def _changed_pixel_ratio(source_path: Path, output_path: Path) -> float:
    with Image.open(source_path) as source, Image.open(output_path) as output:
        source.load()
        output.load()
        source_rgb = source.convert("RGB")
        output_rgb = output.convert("RGB")
        difference = ImageChops.difference(source_rgb, output_rgb)
        try:
            pixels = cast(list[tuple[int, int, int]], list(difference.getdata()))
            return sum(any(channel > 0 for channel in pixel) for pixel in pixels) / max(
                1,
                len(pixels),
            )
        finally:
            difference.close()
            output_rgb.close()
            source_rgb.close()


def _run_variation_probe(
    provider: AIProvider,
    model_id: str,
    source_revision: AssetRevision,
    size: tuple[int, int],
    repetitions: int,
) -> dict[str, Any]:
    elapsed: list[float] = []
    hashes: list[str] = []
    ratios: list[float] = []
    provenance_checks: list[bool] = []
    identities: list[tuple[str, str]] = []
    valid_outputs: list[bool] = []
    output_count_checks: list[bool] = []
    source_path = _file_uri_path(source_revision.content_uri)
    for repetition in range(repetitions):
        request = GenerationRequest(
            prompt="在保留主体的基础上生成可追踪变体",
            model=model_id,
            width=size[0],
            height=size[1],
            count=1,
            seed=271828,
            style_preset="illustration",
            variation_revision=source_revision,
            variation_strength=0.35,
            request_id=f"generation-gate-variation-{repetition}",
            idempotency_key=f"generation-gate-variation-key-{repetition}",
        )
        records, elapsed_ms = _submit_generation(provider, request)
        elapsed.append(elapsed_ms)
        output_count_checks.append(len(records) == 1)
        revision = records[0].current_revision
        identities.append((revision.asset_id, revision.revision_id))
        inspection = _inspect_revision(revision, size)
        hashes.append(str(inspection["pixel_hash"]))
        output_path = _file_uri_path(revision.content_uri)
        ratios.append(_changed_pixel_ratio(source_path, output_path))
        valid_outputs.append(
            bool(
                inspection["format"] == "PNG"
                and inspection["file_sha256_matches"]
                and inspection["decoded_size_matches"]
            )
        )
        provenance_checks.append(
            revision.provenance.source_revision_ids == (source_revision.revision_id,)
            and revision.provenance.parameters.get("variation_of_revision_id")
            == source_revision.revision_id
            and revision.provenance.parameters.get("variation_strength") == 0.35
        )
    return {
        "source_revision_id": source_revision.revision_id,
        "minimum_changed_pixel_ratio": round(min(ratios), 6),
        "deterministic_pixels": len(set(hashes)) == 1,
        "distinct_asset_and_revision_identity": len(set(identities)) == len(identities),
        "source_lineage_and_strength_match": all(provenance_checks),
        "all_outputs_valid_png": all(valid_outputs),
        "all_output_counts_match": all(output_count_checks),
        "elapsed_ms": _metric(elapsed),
    }


def evaluate_generation_gate(
    result: dict[str, Any],
    thresholds: GenerationGateThresholds | None = None,
) -> list[str]:
    limits = thresholds or GenerationGateThresholds()
    violations: list[str] = []
    text = result["text_to_image"]
    if int(text["minimum_candidates_per_request"]) < limits.minimum_candidates_per_request:
        violations.append("文生图候选数量不足")
    if not bool(text["all_candidate_counts_match"]):
        violations.append("文生图返回的候选数量与请求不一致")
    if not bool(text["all_outputs_valid_png"]):
        violations.append("文生图存在格式、尺寸、哈希或字节数无效的候选")
    if not bool(text["fixed_seed_reproduces_each_candidate"]):
        violations.append("固定种子不能逐候选复现像素")
    if float(text["minimum_candidate_uniqueness_ratio"]) < 1.0:
        violations.append("同批文生图候选发生像素重复")
    if not bool(text["distinct_asset_and_revision_identity"]):
        violations.append("独立文生图任务复用了 Asset 或 Revision 身份")
    if not bool(text["provenance_complete"]):
        violations.append("文生图来源记录不完整")
    if int(text["minimum_unique_color_count"]) < limits.minimum_unique_color_count:
        violations.append("文生图候选颜色数量不足，疑似空白或退化输出")
    if float(text["minimum_luma_span"]) < limits.minimum_luma_span:
        violations.append("文生图候选明度跨度不足，疑似空白或退化输出")
    if float(text["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("文生图 P95 超过性能门槛")

    references = result["multi_reference"]
    if int(references["reference_count"]) < limits.minimum_reference_count:
        violations.append("多参考图样本数量不足")
    if not bool(references["all_outputs_valid_png"]):
        violations.append("多参考图输出格式、尺寸或哈希无效")
    if not bool(references["all_output_counts_match"]):
        violations.append("多参考图返回的候选数量与请求不一致")
    if not bool(references["deterministic_pixels"]):
        violations.append("多参考图像素不可复现")
    if not bool(references["distinct_asset_and_revision_identity"]):
        violations.append("多参考图任务复用了 Asset 或 Revision 身份")
    if not bool(references["provenance_records_every_reference"]):
        violations.append("多参考图来源或逐项参数记录不完整")
    if not bool(references["fingerprint_changes_with_strength"]):
        violations.append("多参考图强度没有进入请求指纹")
    if not bool(references["fingerprint_changes_with_order"]):
        violations.append("多参考图顺序没有进入请求指纹")
    if float(references["maximum_weighted_color_mae"]) > limits.maximum_reference_color_mae:
        violations.append("多参考图权重融合像素偏差超过门槛")
    if float(references["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("多参考图 P95 超过性能门槛")

    variation = result["variation"]
    if not bool(variation["all_outputs_valid_png"]):
        violations.append("候选变体输出格式、尺寸或哈希无效")
    if not bool(variation["all_output_counts_match"]):
        violations.append("候选变体返回的候选数量与请求不一致")
    if not bool(variation["deterministic_pixels"]):
        violations.append("候选变体像素不可复现")
    if not bool(variation["distinct_asset_and_revision_identity"]):
        violations.append("候选变体复用了 Asset 或 Revision 身份")
    if not bool(variation["source_lineage_and_strength_match"]):
        violations.append("候选变体来源血缘或变化强度错误")
    if float(variation["minimum_changed_pixel_ratio"]) < limits.minimum_variation_change_ratio:
        violations.append("候选变体像素变化不足")
    if float(variation["elapsed_ms"]["p95"]) > limits.maximum_operation_p95_ms:
        violations.append("候选变体 P95 超过性能门槛")
    return violations


def run(
    profile_name: str = "smoke",
    *,
    image_size: tuple[int, int] | None = None,
    repetitions: int | None = None,
    candidate_count: int | None = None,
    reference_count: int | None = None,
    provider: AIProvider | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    if profile_name not in PROFILES:
        raise ValueError(f"未知生图门禁 profile：{profile_name}")
    profile = dict(PROFILES[profile_name])
    if image_size is not None:
        profile["image_size"] = image_size
    if repetitions is not None:
        profile["repetitions"] = repetitions
    if candidate_count is not None:
        profile["candidate_count"] = candidate_count
    if reference_count is not None:
        profile["reference_count"] = reference_count
    raw_size = tuple(profile["image_size"])
    if len(raw_size) != 2 or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 32 for value in raw_size
    ):
        raise ValueError("image_size 必须包含两个不小于 32 的整数")
    normalized_size = (int(raw_size[0]), int(raw_size[1]))
    repeat_count = profile["repetitions"]
    candidates = profile["candidate_count"]
    references = profile["reference_count"]
    if isinstance(repeat_count, bool) or not isinstance(repeat_count, int) or repeat_count < 2:
        raise ValueError("repetitions 必须是不小于 2 的整数")
    if isinstance(candidates, bool) or not isinstance(candidates, int) or not 1 <= candidates <= 4:
        raise ValueError("candidate_count 必须是 1 到 4 的整数")
    if isinstance(references, bool) or not isinstance(references, int) or not 2 <= references <= 4:
        raise ValueError("reference_count 必须是 2 到 4 的整数")

    with tempfile.TemporaryDirectory(prefix="light-image-editor-generation-gate-") as raw:
        root = Path(raw)
        selected_provider = provider or MockProvider(output_directory=root / "outputs")
        selected_model = (
            MOCK_MODEL_ID
            if provider is None and model_id is None
            else selected_provider.manifest.models[0]
            if model_id is None
            else model_id
        )
        if selected_model not in selected_provider.manifest.models:
            raise ValueError(f"模型 {selected_model} 不在 Provider 声明的模型列表中")
        text_to_image, first_revision = _run_text_to_image_probe(
            selected_provider,
            selected_model,
            normalized_size,
            repeat_count,
            candidates,
        )
        multi_reference = _run_multi_reference_probe(
            root,
            selected_provider,
            selected_model,
            normalized_size,
            repeat_count,
            references,
        )
        variation = _run_variation_probe(
            selected_provider,
            selected_model,
            first_revision,
            normalized_size,
            repeat_count,
        )

    thresholds = GenerationGateThresholds(
        minimum_candidates_per_request=candidates,
        minimum_reference_count=references,
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "application_version": __version__,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "profile": profile_name,
        "provider": {
            "provider_id": selected_provider.manifest.provider_id,
            "provider_name": selected_provider.manifest.name,
            "provider_version": selected_provider.manifest.version,
            "model": selected_model,
            "offline": selected_provider.manifest.offline,
        },
        "environment": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "pillow": pillow_version,
        },
        "fixtures": {
            "image_size": list(normalized_size),
            "repetitions": repeat_count,
            "candidate_count": candidates,
            "reference_count": references,
        },
        "text_to_image": text_to_image,
        "multi_reference": multi_reference,
        "variation": variation,
        "thresholds": asdict(thresholds),
        "scope_limit": (
            "技术契约与像素基线；验证真实 PNG、非退化输出、复现、候选区分、"
            "参考权重、变体、来源和性能，不代表提示词语义或审美质量；"
            + (
                "当前为离线确定性 Provider，不代表生产模型质量"
                if selected_provider.manifest.offline
                else "生产 Provider 仍需独立人工语义与审美样本评分"
            )
        ),
    }
    result["violations"] = evaluate_generation_gate(result, thresholds)
    result["passed"] = not result["violations"]
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="smoke")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--candidate-count", type=int)
    parser.add_argument("--reference-count", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fail", action="store_true", help="记录失败但返回成功退出码")
    arguments = parser.parse_args()
    if (arguments.width is None) != (arguments.height is None):
        parser.error("--width 与 --height 必须同时提供")
    return arguments


def main() -> int:
    arguments = _parse_args()
    size = None if arguments.width is None else (arguments.width, arguments.height)
    result = run(
        arguments.profile,
        image_size=size,
        repetitions=arguments.repetitions,
        candidate_count=arguments.candidate_count,
        reference_count=arguments.reference_count,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output is not None:
        destination = arguments.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] or arguments.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
