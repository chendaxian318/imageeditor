"""A deterministic, fully offline provider for development and contract tests."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast
from urllib.parse import unquote, urlparse

import numpy as np
from PIL import (
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageFont,
    ImageOps,
)

from light_image_editor.domain import (
    AIRequest,
    AlphaBrushStroke,
    AssetRecord,
    AssetRevision,
    ContentEditRequest,
    GenerationRequest,
    JobError,
    JobSnapshot,
    JobStatus,
    NormalizedQuad,
    Provenance,
    Scalar,
    parse_alpha_strokes,
    validate_normalized_quad,
)
from light_image_editor.services.background_analysis import estimate_border_background
from light_image_editor.services.font_matcher import FONT_FILES
from light_image_editor.services.region_repair import (
    RegionRepairSpec,
    repair_mask,
    repair_region,
)
from light_image_editor.services.smart_selection import (
    SmartSelectionError,
    decode_selection_mask,
)

from .contracts import (
    AIProvider,
    IdempotencyConflictError,
    ProviderCapability,
    ProviderJobNotFoundError,
    ProviderManifest,
    UnsupportedProviderRequestError,
)

MOCK_PROVIDER_ID: Final = "offline.mock"
MOCK_MODEL_ID: Final = "mock-image-v1"
MAX_MOCK_EDIT_DIMENSION: Final = 16_384
MAX_MOCK_EDIT_PIXELS: Final = 128_000_000


@dataclass(slots=True)
class _StoredJob:
    request: AIRequest
    fingerprint: str
    snapshot: JobSnapshot
    ready_at: float | None = None


class MockProvider(AIProvider):
    """Produce deterministic offline assets, optionally backed by real PNG files."""

    def __init__(
        self,
        *,
        auto_complete: bool = True,
        failing_request_ids: Iterable[str] = (),
        failure_errors: Mapping[str, JobError] | None = None,
        output_directory: str | Path | None = None,
        auto_run_after_seconds: float | None = None,
    ) -> None:
        if auto_run_after_seconds is not None:
            if (
                isinstance(auto_run_after_seconds, bool)
                or not isinstance(auto_run_after_seconds, (int, float))
                or not math.isfinite(float(auto_run_after_seconds))
                or auto_run_after_seconds < 0
            ):
                raise ValueError("auto_run_after_seconds must be a non-negative number")
            if auto_complete:
                raise ValueError("auto_run_after_seconds requires auto_complete=False")
        self._manifest = ProviderManifest(
            provider_id=MOCK_PROVIDER_ID,
            name="Offline Mock Provider",
            version="1.0",
            capabilities=(
                ProviderCapability.GENERATION,
                ProviderCapability.CONTENT_EDIT,
                ProviderCapability.NEGATIVE_PROMPT,
                ProviderCapability.REFERENCE_IMAGE,
                ProviderCapability.SEED,
                ProviderCapability.STYLE_PRESET,
                ProviderCapability.VARIATION,
            ),
            models=(MOCK_MODEL_ID,),
            offline=True,
        )
        self.auto_complete = auto_complete
        self._failing_request_ids = set(failing_request_ids)
        self._failure_errors = dict(failure_errors or {})
        self._jobs: dict[str, _StoredJob] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._submission_count = 0
        self._auto_run_after_seconds = auto_run_after_seconds
        self._output_directory = (
            None
            if output_directory is None
            else Path(output_directory).expanduser().resolve(strict=False)
        )

    @property
    def manifest(self) -> ProviderManifest:
        return self._manifest

    @property
    def submission_count(self) -> int:
        """Number of distinct jobs accepted, excluding idempotent replays."""

        return self._submission_count

    def submit_generation(self, request: GenerationRequest) -> JobSnapshot:
        self._require_supported(ProviderCapability.GENERATION, request.model)
        if request.negative_prompt:
            self._require_supported(ProviderCapability.NEGATIVE_PROMPT, request.model)
        if request.all_references:
            self._require_supported(ProviderCapability.REFERENCE_IMAGE, request.model)
        if request.seed is not None:
            self._require_supported(ProviderCapability.SEED, request.model)
        if request.style_preset:
            self._require_supported(ProviderCapability.STYLE_PRESET, request.model)
        if request.variation_revision is not None:
            self._require_supported(ProviderCapability.VARIATION, request.model)
        return self._submit(request)

    def submit_edit(self, request: ContentEditRequest) -> JobSnapshot:
        self._require_supported(ProviderCapability.CONTENT_EDIT, request.model)
        return self._submit(request)

    def get_job(self, job_id: str) -> JobSnapshot:
        stored = self._stored(job_id)
        if (
            stored.ready_at is not None
            and stored.snapshot.status is JobStatus.QUEUED
            and time.monotonic() >= stored.ready_at
        ):
            return self.run(job_id)
        return stored.snapshot

    def cancel(self, job_id: str) -> JobSnapshot:
        stored = self._stored(job_id)
        if stored.snapshot.status.terminal:
            return stored.snapshot
        stored.snapshot = self._transition(stored.snapshot, JobStatus.CANCELLED)
        return stored.snapshot

    def run(self, job_id: str) -> JobSnapshot:
        """Synchronously finish one queued mock job."""

        stored = self._stored(job_id)
        if stored.snapshot.status.terminal:
            return stored.snapshot
        stored.snapshot = self._transition(stored.snapshot, JobStatus.RUNNING)
        configured_error = self._failure_errors.get(stored.request.request_id)
        if configured_error is not None or stored.request.request_id in self._failing_request_ids:
            stored.snapshot = self._transition(
                stored.snapshot,
                JobStatus.FAILED,
                error=(
                    configured_error
                    or JobError(
                        code="mock_failure",
                        message=("The offline mock provider was configured to fail this request."),
                        retryable=True,
                    )
                ),
            )
            return stored.snapshot
        outputs = self._outputs(stored.request, stored.snapshot.job_id, stored.fingerprint)
        stored.snapshot = self._transition(
            stored.snapshot,
            JobStatus.SUCCEEDED,
            outputs=outputs,
        )
        return stored.snapshot

    def run_all(self) -> tuple[JobSnapshot, ...]:
        return tuple(self.run(job_id) for job_id in tuple(self._jobs))

    def _submit(self, request: AIRequest) -> JobSnapshot:
        fingerprint = request.fingerprint()
        key = request.idempotency_key
        if key is None:  # Defensive: request models normalize this during validation.
            raise ValueError("request idempotency key cannot be empty")
        replay = self._idempotency.get(key)
        if replay is not None:
            previous_fingerprint, job_id = replay
            if previous_fingerprint != fingerprint:
                raise IdempotencyConflictError(
                    "the idempotency key was already used for a different request"
                )
            return self.get_job(job_id)

        job_id = self._job_id(key)
        now = datetime.now(UTC)
        snapshot = JobSnapshot(
            job_id=job_id,
            request_id=request.request_id,
            provider_id=self.manifest.provider_id,
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        ready_at = (
            None
            if self._auto_run_after_seconds is None
            else time.monotonic() + self._auto_run_after_seconds
        )
        self._jobs[job_id] = _StoredJob(request, fingerprint, snapshot, ready_at)
        self._idempotency[key] = (fingerprint, job_id)
        self._submission_count += 1
        return self.run(job_id) if self.auto_complete else snapshot

    def _outputs(
        self,
        request: AIRequest,
        job_id: str,
        fingerprint: str,
    ) -> tuple[AssetRecord, ...]:
        if isinstance(request, GenerationRequest):
            return tuple(
                self._generated_asset(request, job_id, fingerprint, index)
                for index in range(request.count)
            )
        return (self._edited_asset(request, job_id, fingerprint),)

    def _generated_asset(
        self,
        request: GenerationRequest,
        job_id: str,
        fingerprint: str,
        index: int,
    ) -> AssetRecord:
        # A new idempotency key represents a distinct accepted job.  Include the
        # job identity so two non-replay submissions cannot claim the same
        # Asset/Revision IDs while pointing at different mock:// content URIs.
        token = hashlib.sha256(f"{fingerprint}:{job_id}:{index}".encode()).hexdigest()
        render_token = (
            token
            if request.seed is None
            else hashlib.sha256(f"{fingerprint}:{index}:pixels".encode()).hexdigest()
        )
        asset_id = f"asset_mock_{token[:24]}"
        content_uri = f"mock://{self.manifest.provider_id}/{job_id}/{index}.png"
        parameters: dict[str, Scalar] = {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "width": request.width,
            "height": request.height,
            "seed": request.seed,
            "style_preset": request.style_preset,
            "candidate": index,
            "reference_revision_id": (
                None
                if request.reference_revision is None
                else request.reference_revision.revision_id
            ),
            "reference_role": request.reference_role,
            "reference_strength": request.reference_strength,
            "variation_of_revision_id": (
                None
                if request.variation_revision is None
                else request.variation_revision.revision_id
            ),
            "variation_strength": request.variation_strength,
        }
        references = request.all_references
        parameters["reference_count"] = len(references)
        for reference_index, reference in enumerate(references):
            parameters[f"reference_{reference_index}_revision_id"] = reference.revision.revision_id
            parameters[f"reference_{reference_index}_role"] = reference.role
            parameters[f"reference_{reference_index}_strength"] = reference.strength
        parameters.update(dict(request.parameters))
        provenance = Provenance(
            origin="generation",
            actor=self.manifest.provider_id,
            request_id=request.request_id,
            provider_id=self.manifest.provider_id,
            model=request.model,
            source_revision_ids=(
                (request.variation_revision.revision_id,)
                if request.variation_revision is not None
                else tuple(reference.revision.revision_id for reference in references)
            ),
            parameters=parameters,
        )
        materialized_path = (
            self._materialize_reference_generation_png(render_token, request)
            if self._output_directory is not None
            and (bool(references) or request.variation_revision is not None)
            else (
                self._materialize_generation_png(render_token, request)
                if self._output_directory is not None
                else None
            )
        )
        revision = self._output_revision(
            token=token,
            asset_id=asset_id,
            revision_id=f"rev_mock_{token[24:48]}",
            content_uri=content_uri,
            provenance=provenance,
            width=request.width,
            height=request.height,
            materialized_path=materialized_path,
        )
        return AssetRecord(
            asset_id=asset_id,
            name=(
                f"候选变体-{index + 1}.png"
                if request.variation_revision is not None
                else f"生成图-{index + 1}.png"
            ),
            revisions=(revision,),
        )

    def _edited_asset(
        self,
        request: ContentEditRequest,
        job_id: str,
        fingerprint: str,
    ) -> AssetRecord:
        token = hashlib.sha256(f"{fingerprint}:{job_id}:edit".encode()).hexdigest()
        asset_id = f"asset_mock_{token[:24]}"
        content_uri = f"mock://{self.manifest.provider_id}/{job_id}/edit.png"
        request_parameters = dict(request.parameters)
        parameters: dict[str, Scalar] = {
            "prompt": request.prompt,
            "strength": request.strength,
            "mask_revision_id": request.mask_revision_id,
        }
        parameters.update(request_parameters)
        width = request.source_revision.width or 1024
        height = request.source_revision.height or 1024
        materialized_path: Path | None = None
        operation = request_parameters.get("operation")
        if operation == "upscale":
            scale = request_parameters.get("scale")
            if isinstance(scale, bool) or not isinstance(scale, int) or scale not in {2, 4}:
                raise UnsupportedProviderRequestError("offline upscale scale must be 2 or 4")
            width *= scale
            height *= scale
            if (
                width > MAX_MOCK_EDIT_DIMENSION
                or height > MAX_MOCK_EDIT_DIMENSION
                or width * height > MAX_MOCK_EDIT_PIXELS
            ):
                raise UnsupportedProviderRequestError("offline upscale exceeds the safe limit")
            if self._output_directory is not None:
                materialized_path = self._materialize_upscale_png(
                    token,
                    request.source_revision,
                    scale=scale,
                )
        elif operation == "remove_background":
            tolerance = request_parameters.get("tolerance", 24)
            feather = request_parameters.get("feather", 32)
            if (
                isinstance(tolerance, bool)
                or not isinstance(tolerance, int)
                or not 0 <= tolerance <= 254
            ):
                raise UnsupportedProviderRequestError(
                    "offline background tolerance must be an integer from 0 to 254"
                )
            if isinstance(feather, bool) or not isinstance(feather, int) or not 1 <= feather <= 255:
                raise UnsupportedProviderRequestError(
                    "offline background feather must be an integer from 1 to 255"
                )
            if self._output_directory is not None:
                materialized_path = self._materialize_remove_background_png(
                    token,
                    request.source_revision,
                    tolerance=tolerance,
                    feather=feather,
                )
        elif operation == "replace_text":
            region = _validated_normalized_region(request_parameters)
            perspective_quad = _validated_perspective_quad(request_parameters, region)
            text = request_parameters.get("text")
            font_family = request_parameters.get("font_family", "微软雅黑")
            font_size = request_parameters.get("font_size", 32)
            color = request_parameters.get("color", "#202124")
            align = request_parameters.get("align", "center")
            rotation = request_parameters.get("rotation", 0.0)
            curve = request_parameters.get("curve", 0.0)
            visual_effect = request_parameters.get("visual_effect", "plain")
            effect_color = request_parameters.get("effect_color", "#ffffff")
            effect_size = request_parameters.get("effect_size", 4)
            if not isinstance(text, str) or not text.strip() or len(text) > 500:
                raise UnsupportedProviderRequestError(
                    "offline text replacement requires 1 to 500 characters"
                )
            if not isinstance(font_family, str) or font_family not in FONT_FILES:
                raise UnsupportedProviderRequestError("unsupported offline font family")
            if (
                isinstance(font_size, bool)
                or not isinstance(font_size, int)
                or not 8 <= font_size <= 512
            ):
                raise UnsupportedProviderRequestError(
                    "offline replacement font size must be an integer from 8 to 512"
                )
            if not isinstance(color, str):
                raise UnsupportedProviderRequestError("offline replacement color must be text")
            try:
                ImageColor.getrgb(color)
            except ValueError as error:
                raise UnsupportedProviderRequestError(
                    "offline replacement color must be a valid color"
                ) from error
            if align not in {"left", "center", "right"}:
                raise UnsupportedProviderRequestError(
                    "offline replacement alignment must be left, center or right"
                )
            if (
                isinstance(rotation, bool)
                or not isinstance(rotation, (int, float))
                or not math.isfinite(float(rotation))
                or not -180 <= float(rotation) <= 180
            ):
                raise UnsupportedProviderRequestError(
                    "offline replacement rotation must be between -180 and 180 degrees"
                )
            if (
                isinstance(curve, bool)
                or not isinstance(curve, (int, float))
                or not math.isfinite(float(curve))
                or not -1 <= float(curve) <= 1
            ):
                raise UnsupportedProviderRequestError(
                    "offline replacement curve must be between -1 and 1"
                )
            if abs(float(curve)) > 1e-9 and "\n" in text:
                raise UnsupportedProviderRequestError(
                    "offline curved text replacement requires a single line"
                )
            if not isinstance(visual_effect, str) or visual_effect not in {
                "plain",
                "outline",
                "shadow",
                "gradient",
                "glow",
            }:
                raise UnsupportedProviderRequestError(
                    "offline replacement visual effect is unsupported"
                )
            if not isinstance(effect_color, str):
                raise UnsupportedProviderRequestError(
                    "offline replacement effect color must be text"
                )
            try:
                ImageColor.getrgb(effect_color)
            except ValueError as error:
                raise UnsupportedProviderRequestError(
                    "offline replacement effect color must be a valid color"
                ) from error
            if (
                isinstance(effect_size, bool)
                or not isinstance(effect_size, int)
                or not 0 <= effect_size <= 64
            ):
                raise UnsupportedProviderRequestError(
                    "offline replacement effect size must be an integer from 0 to 64"
                )
            if visual_effect in {"outline", "shadow", "glow"} and effect_size < 1:
                raise UnsupportedProviderRequestError(
                    "offline outline, shadow or glow effect size must be at least 1"
                )
            if self._output_directory is not None:
                materialized_path = self._materialize_replace_text_png(
                    token,
                    request.source_revision,
                    region=region,
                    text=text.strip(),
                    font_family=font_family,
                    font_size=font_size,
                    color=color,
                    align=align,
                    rotation=float(rotation),
                    curve=float(curve),
                    perspective_quad=perspective_quad,
                    visual_effect=visual_effect,
                    effect_color=effect_color,
                    effect_size=effect_size,
                )
        elif operation == "erase_region":
            region = _validated_normalized_region(request_parameters)
            feather = request_parameters.get("feather", 8)
            if isinstance(feather, bool) or not isinstance(feather, int) or not 0 <= feather <= 64:
                raise UnsupportedProviderRequestError(
                    "offline region repair feather must be an integer from 0 to 64"
                )
            if self._output_directory is not None:
                materialized_path = self._materialize_erase_region_png(
                    token,
                    request.source_revision,
                    region=region,
                    feather=feather,
                )
        elif operation == "erase_mask":
            _validated_normalized_region(request_parameters)
            mask_rle = request_parameters.get("mask_rle")
            mask_width = request_parameters.get("mask_width")
            mask_height = request_parameters.get("mask_height")
            feather = request_parameters.get("feather", 0)
            if not isinstance(mask_rle, str):
                raise UnsupportedProviderRequestError(
                    "offline mask repair requires serialized mask data"
                )
            if (
                isinstance(mask_width, bool)
                or isinstance(mask_height, bool)
                or not isinstance(mask_width, int)
                or not isinstance(mask_height, int)
            ):
                raise UnsupportedProviderRequestError(
                    "offline mask repair requires integer mask dimensions"
                )
            if isinstance(feather, bool) or not isinstance(feather, int) or not 0 <= feather <= 64:
                raise UnsupportedProviderRequestError(
                    "offline mask repair feather must be an integer from 0 to 64"
                )
            try:
                validated_mask = decode_selection_mask(
                    mask_rle,
                    (mask_width, mask_height),
                )
            except SmartSelectionError as error:
                raise UnsupportedProviderRequestError(str(error)) from error
            else:
                validated_mask.close()
            if self._output_directory is not None:
                materialized_path = self._materialize_erase_mask_png(
                    token,
                    request.source_revision,
                    mask_rle=mask_rle,
                    mask_size=(mask_width, mask_height),
                    feather=feather,
                )
        elif operation == "refine_alpha":
            strokes_payload = request_parameters.get("strokes")
            if not isinstance(strokes_payload, str):
                raise UnsupportedProviderRequestError(
                    "offline alpha refinement requires serialized strokes"
                )
            try:
                strokes = parse_alpha_strokes(strokes_payload)
            except ValueError as error:
                raise UnsupportedProviderRequestError(str(error)) from error
            if self._output_directory is not None:
                materialized_path = self._materialize_refine_alpha_png(
                    token,
                    request.source_revision,
                    strokes=strokes,
                )
        if operation == "erase_mask":
            stored_mask = parameters.pop("mask_rle", None)
            if isinstance(stored_mask, str):
                parameters["mask_sha256"] = hashlib.sha256(stored_mask.encode("ascii")).hexdigest()
        provenance = Provenance(
            origin="content_edit",
            actor=self.manifest.provider_id,
            source_revision_ids=(request.source_revision.revision_id,),
            request_id=request.request_id,
            provider_id=self.manifest.provider_id,
            model=request.model,
            parameters=parameters,
        )
        revision = self._output_revision(
            token=token,
            asset_id=asset_id,
            revision_id=f"rev_mock_{token[24:48]}",
            content_uri=content_uri,
            provenance=provenance,
            width=width,
            height=height,
            media_type="image/png",
            materialized_path=materialized_path,
        )
        return AssetRecord(asset_id=asset_id, name="AI 编辑结果.png", revisions=(revision,))

    def _output_revision(
        self,
        *,
        token: str,
        asset_id: str,
        revision_id: str,
        content_uri: str,
        provenance: Provenance,
        width: int,
        height: int,
        media_type: str = "image/png",
        materialized_path: Path | None = None,
    ) -> AssetRevision:
        if self._output_directory is None:
            return AssetRevision.from_content_uri(
                asset_id=asset_id,
                sequence=1,
                content_uri=content_uri,
                media_type=media_type,
                provenance=provenance,
                width=width,
                height=height,
                revision_id=revision_id,
            )
        path = (
            self._materialize_png(token, width=width, height=height)
            if materialized_path is None
            else materialized_path
        )
        return AssetRevision(
            asset_id=asset_id,
            sequence=1,
            content_uri=path.as_uri(),
            media_type="image/png",
            sha256=_file_sha256(path),
            provenance=provenance,
            revision_id=revision_id,
            byte_size=path.stat().st_size,
            width=width,
            height=height,
        )

    def _materialize_png(self, token: str, *, width: int, height: int) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        image = _mock_base_image(token, width=width, height=height)
        try:
            return self._write_png_atomically(image, destination, token)
        finally:
            image.close()

    def _materialize_generation_png(
        self,
        token: str,
        request: GenerationRequest,
    ) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        base = _mock_base_image(token, width=request.width, height=request.height)
        styled = _apply_mock_style(base, request.style_preset)
        try:
            return self._write_png_atomically(styled, destination, token)
        finally:
            if styled is not base:
                styled.close()
            base.close()

    def _materialize_reference_generation_png(
        self,
        token: str,
        request: GenerationRequest,
    ) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        base = _mock_base_image(token, width=request.width, height=request.height)
        if request.variation_revision is not None:
            reference = self._load_generation_reference(
                request.variation_revision,
                (request.width, request.height),
            )
            try:
                result = Image.blend(base, reference, 1.0 - request.variation_strength)
            finally:
                base.close()
                reference.close()
        else:
            references = request.all_references
            if not references:
                base.close()
                raise UnsupportedProviderRequestError(
                    "reference generation requires at least one reference"
                )
            layers: list[tuple[Image.Image, float]] = []
            try:
                for reference_value in references:
                    reference = self._load_generation_reference(
                        reference_value.revision,
                        (request.width, request.height),
                    )
                    if reference_value.role == "style":
                        variant = reference.filter(
                            ImageFilter.GaussianBlur(radius=max(0.5, min(reference.size) / 160))
                        )
                        reference.close()
                    elif reference_value.role == "composition":
                        variant = ImageOps.grayscale(reference).convert("RGB")
                        reference.close()
                    else:
                        variant = reference
                    layers.append((variant, reference_value.strength))

                total_reference_weight = sum(weight for _image, weight in layers)
                result = base.copy()
                if total_reference_weight > 0:
                    base_weight = max(0.0, 1.0 - total_reference_weight)
                    accumulated_weight = base_weight
                    handled_reference_index: int | None = None
                    if accumulated_weight == 0:
                        handled_reference_index = next(
                            (
                                index
                                for index, (_variant, weight) in enumerate(layers)
                                if weight > 0
                            ),
                            None,
                        )
                        assert handled_reference_index is not None
                        first_variant, first_weight = layers[handled_reference_index]
                        result.close()
                        result = first_variant.copy()
                        accumulated_weight = first_weight
                    for reference_index, (variant, weight) in enumerate(layers):
                        if weight <= 0:
                            continue
                        if reference_index == handled_reference_index:
                            continue
                        replacement = Image.blend(
                            result,
                            variant,
                            weight / (accumulated_weight + weight),
                        )
                        result.close()
                        result = replacement
                        accumulated_weight += weight
            finally:
                base.close()
                for variant, _weight in layers:
                    variant.close()
        styled = _apply_mock_style(result, request.style_preset)
        try:
            return self._write_png_atomically(styled, destination, token)
        finally:
            if styled is not result:
                styled.close()
            result.close()

    @staticmethod
    def _load_generation_reference(
        revision: AssetRevision,
        size: tuple[int, int],
    ) -> Image.Image:
        source_path = _local_file_uri_path(revision.content_uri)
        with Image.open(source_path) as opened:
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                rgb = oriented.convert("RGB")
            finally:
                if oriented is not opened:
                    oriented.close()
        try:
            return ImageOps.fit(
                rgb,
                size,
                Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
        finally:
            rgb.close()

    def _materialize_upscale_png(
        self,
        token: str,
        source_revision: AssetRevision,
        *,
        scale: int,
    ) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        source_path = _local_file_uri_path(source_revision.content_uri)
        with Image.open(source_path) as opened:
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                mode = "RGBA" if "A" in oriented.getbands() else "RGB"
                source = oriented.convert(mode)
            finally:
                if oriented is not opened:
                    oriented.close()
        try:
            resized = source.resize(
                (source.width * scale, source.height * scale),
                Image.Resampling.LANCZOS,
            )
        finally:
            source.close()
        try:
            return self._write_png_atomically(resized, destination, token)
        finally:
            resized.close()

    def _materialize_remove_background_png(
        self,
        token: str,
        source_revision: AssetRevision,
        *,
        tolerance: int,
        feather: int,
    ) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        source_path = _local_file_uri_path(source_revision.content_uri)
        with Image.open(source_path) as opened:
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                source = oriented.convert("RGBA")
            finally:
                if oriented is not opened:
                    oriented.close()
        background, background_spread = estimate_border_background(source)
        rgb = source.convert("RGB")
        background_image = Image.new("RGB", source.size, background)
        try:
            difference = ImageChops.difference(rgb, background_image)
            red, green, blue = difference.split()
            distance = ImageChops.lighter(ImageChops.lighter(red, green), blue)
            adaptive_tolerance = min(254, max(tolerance, background_spread + 2))
            upper = min(255, adaptive_tolerance + feather)
            span = max(1, upper - adaptive_tolerance)
            alpha = distance.point(
                tuple(
                    0
                    if value <= adaptive_tolerance
                    else 255
                    if value >= upper
                    else round((value - adaptive_tolerance) * 255 / span)
                    for value in range(256)
                )
            )
            original_alpha = source.getchannel("A")
            combined_alpha = ImageChops.multiply(alpha, original_alpha)
            result = source.copy()
            result.putalpha(combined_alpha)
        finally:
            rgb.close()
            background_image.close()
            difference.close()
            red.close()
            green.close()
            blue.close()
            distance.close()
            alpha.close()
            original_alpha.close()
            combined_alpha.close()
            source.close()
        try:
            return self._write_png_atomically(result, destination, token)
        finally:
            result.close()

    def _materialize_replace_text_png(
        self,
        token: str,
        source_revision: AssetRevision,
        *,
        region: tuple[float, float, float, float],
        text: str,
        font_family: str,
        font_size: int,
        color: str,
        align: str,
        rotation: float,
        curve: float,
        perspective_quad: NormalizedQuad | None,
        visual_effect: str,
        effect_color: str,
        effect_size: int,
    ) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        source_path = _local_file_uri_path(source_revision.content_uri)
        with Image.open(source_path) as opened:
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                source = oriented.convert("RGBA")
            finally:
                if oriented is not opened:
                    oriented.close()
        left, top, right, bottom = _pixel_region(region, source.size)
        background = _sample_region_background(source, (left, top, right, bottom))
        repaired = source.copy()
        source.close()
        draw = ImageDraw.Draw(repaired)
        if perspective_quad is not None:
            pixel_quad = tuple(
                value * (repaired.width if index % 2 == 0 else repaired.height)
                for index, value in enumerate(perspective_quad)
            )
            polygon = [(pixel_quad[index], pixel_quad[index + 1]) for index in range(0, 8, 2)]
            draw.polygon(polygon, fill=background)
            top_width = math.dist(polygon[0], polygon[1])
            bottom_width = math.dist(polygon[3], polygon[2])
            left_height = math.dist(polygon[0], polygon[3])
            right_height = math.dist(polygon[1], polygon[2])
            surface_size = (
                max(2, round((top_width + bottom_width) / 2)),
                max(2, round((left_height + right_height) / 2)),
            )
            text_surface = Image.new("RGBA", surface_size, (0, 0, 0, 0))
            try:
                _draw_replacement_text(
                    text_surface,
                    text=text,
                    font_family=font_family,
                    font_size=font_size,
                    color=color,
                    align=align,
                    curve=curve,
                    visual_effect=visual_effect,
                    effect_color=effect_color,
                    effect_size=effect_size,
                )
                warped = _warp_surface_to_quad(
                    text_surface,
                    output_size=repaired.size,
                    destination=cast(NormalizedQuad, pixel_quad),
                )
                try:
                    repaired.alpha_composite(warped)
                finally:
                    warped.close()
                return self._write_png_atomically(repaired, destination, token)
            finally:
                text_surface.close()
                repaired.close()
        draw.rectangle((left, top, right - 1, bottom - 1), fill=background)
        patch = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
        try:
            if abs(rotation) >= 60:
                text_surface = Image.new(
                    "RGBA",
                    (patch.height, patch.width),
                    (0, 0, 0, 0),
                )
                try:
                    _draw_replacement_text(
                        text_surface,
                        text=text,
                        font_family=font_family,
                        font_size=font_size,
                        color=color,
                        align=align,
                        curve=curve,
                        visual_effect=visual_effect,
                        effect_color=effect_color,
                        effect_size=effect_size,
                    )
                    rotated = text_surface.rotate(
                        rotation,
                        resample=Image.Resampling.BICUBIC,
                        expand=True,
                    )
                    try:
                        fitted = ImageOps.contain(
                            rotated,
                            patch.size,
                            Image.Resampling.LANCZOS,
                        )
                        try:
                            patch.alpha_composite(
                                fitted,
                                (
                                    (patch.width - fitted.width) // 2,
                                    (patch.height - fitted.height) // 2,
                                ),
                            )
                        finally:
                            fitted.close()
                    finally:
                        rotated.close()
                finally:
                    text_surface.close()
                rendered = patch
            else:
                _draw_replacement_text(
                    patch,
                    text=text,
                    font_family=font_family,
                    font_size=font_size,
                    color=color,
                    align=align,
                    curve=curve,
                    visual_effect=visual_effect,
                    effect_color=effect_color,
                    effect_size=effect_size,
                )
                rendered = (
                    patch.rotate(
                        rotation,
                        resample=Image.Resampling.BICUBIC,
                        expand=False,
                        center=(patch.width / 2, patch.height / 2),
                    )
                    if abs(rotation) > 1e-9
                    else patch
                )
            try:
                repaired.alpha_composite(rendered, (left, top))
            finally:
                if rendered is not patch:
                    rendered.close()
            return self._write_png_atomically(repaired, destination, token)
        finally:
            patch.close()
            repaired.close()

    def _materialize_erase_region_png(
        self,
        token: str,
        source_revision: AssetRevision,
        *,
        region: tuple[float, float, float, float],
        feather: int,
    ) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        source_path = _local_file_uri_path(source_revision.content_uri)
        with Image.open(source_path) as opened:
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                result = repair_region(
                    oriented,
                    RegionRepairSpec(region=region, feather=feather),
                )
            finally:
                if oriented is not opened:
                    oriented.close()
        try:
            return self._write_png_atomically(result, destination, token)
        finally:
            result.close()

    def _materialize_erase_mask_png(
        self,
        token: str,
        source_revision: AssetRevision,
        *,
        mask_rle: str,
        mask_size: tuple[int, int],
        feather: int,
    ) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        source_path = _local_file_uri_path(source_revision.content_uri)
        with Image.open(source_path) as opened:
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                mask = decode_selection_mask(
                    mask_rle,
                    mask_size,
                    target_size=oriented.size,
                )
                try:
                    result = repair_mask(oriented, mask, feather=feather)
                finally:
                    mask.close()
            finally:
                if oriented is not opened:
                    oriented.close()
        try:
            return self._write_png_atomically(result, destination, token)
        finally:
            result.close()

    def _materialize_refine_alpha_png(
        self,
        token: str,
        source_revision: AssetRevision,
        *,
        strokes: tuple[AlphaBrushStroke, ...],
    ) -> Path:
        assert self._output_directory is not None
        self._output_directory.mkdir(parents=True, exist_ok=True)
        destination = self._output_directory / f"{token}.png"
        if destination.exists():
            return destination
        source_path = _local_file_uri_path(source_revision.content_uri)
        with Image.open(source_path) as opened:
            opened.load()
            oriented = ImageOps.exif_transpose(opened)
            try:
                source = oriented.convert("RGBA")
            finally:
                if oriented is not opened:
                    oriented.close()
        alpha = source.getchannel("A")
        draw = ImageDraw.Draw(alpha)
        width, height = source.size
        minimum_side = min(width, height)
        for stroke in strokes:
            points = [
                (
                    round(x * max(1, width - 1)),
                    round(y * max(1, height - 1)),
                )
                for x, y in stroke.points
            ]
            radius = max(1, round(stroke.radius * minimum_side))
            value = 255 if stroke.mode == "preserve" else 0
            if len(points) > 1:
                draw.line(points, fill=value, width=radius * 2, joint="curve")
            for x, y in (points[0], points[-1]):
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=value,
                )
        result = source.copy()
        result.putalpha(alpha)
        alpha.close()
        source.close()
        try:
            return self._write_png_atomically(result, destination, token)
        finally:
            result.close()

    def _write_png_atomically(self, image: Image.Image, destination: Path, token: str) -> Path:
        descriptor = -1
        temporary_name = ""
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._output_directory,
                prefix=f".{token}.",
                suffix=".png",
            )
            os.close(descriptor)
            descriptor = -1
            image.save(temporary_name, "PNG")
            with Path(temporary_name).open("rb+") as stream:
                os.fsync(stream.fileno())
            try:
                os.link(temporary_name, destination)
            except FileExistsError:
                pass
            return destination
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

    def _require_supported(self, capability: ProviderCapability, model: str) -> None:
        if not self.manifest.supports(capability, model):
            raise UnsupportedProviderRequestError(
                f"provider {self.manifest.provider_id} does not support {capability}:{model}"
            )

    def _stored(self, job_id: str) -> _StoredJob:
        try:
            return self._jobs[job_id]
        except KeyError as error:
            raise ProviderJobNotFoundError(f"unknown provider job: {job_id}") from error

    @staticmethod
    def _job_id(idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"job_mock_{digest[:24]}"

    @staticmethod
    def _transition(
        snapshot: JobSnapshot,
        status: JobStatus,
        *,
        outputs: tuple[AssetRecord, ...] = (),
        error: JobError | None = None,
    ) -> JobSnapshot:
        return replace(
            snapshot,
            status=status,
            outputs=outputs,
            error=error,
            updated_at=datetime.now(UTC),
        )


def _mock_base_image(token: str, *, width: int, height: int) -> Image.Image:
    base = tuple(int(token[index : index + 2], 16) for index in (0, 2, 4))
    accent = tuple(int(token[index : index + 2], 16) for index in (6, 8, 10))
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)
    margin = max(1, min(width, height) // 12)
    draw.rounded_rectangle(
        (
            margin,
            margin,
            max(margin, width - margin - 1),
            max(margin, height - margin - 1),
        ),
        radius=max(1, margin // 2),
        fill=accent,
        outline=(245, 245, 245),
        width=max(1, margin // 8),
    )
    return image


def _apply_mock_style(image: Image.Image, style_preset: str) -> Image.Image:
    if not style_preset:
        return image
    if style_preset == "photography":
        contrasted = ImageEnhance.Contrast(image).enhance(1.12)
        try:
            return contrasted.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120))
        finally:
            contrasted.close()
    if style_preset == "illustration":
        colored = ImageEnhance.Color(image).enhance(1.35)
        try:
            return ImageEnhance.Contrast(colored).enhance(1.08)
        finally:
            colored.close()
    if style_preset == "watercolor":
        softened = image.filter(ImageFilter.SMOOTH_MORE)
        try:
            return ImageEnhance.Color(softened).enhance(0.78)
        finally:
            softened.close()
    if style_preset == "ink":
        grayscale = ImageOps.grayscale(image)
        try:
            contrasted = ImageOps.autocontrast(grayscale)
        finally:
            grayscale.close()
        try:
            return contrasted.convert("RGB")
        finally:
            contrasted.close()
    if style_preset == "pixel_art":
        small = image.resize(
            (max(1, image.width // 12), max(1, image.height // 12)),
            Image.Resampling.BOX,
        )
        try:
            return small.resize(image.size, Image.Resampling.NEAREST)
        finally:
            small.close()
    raise UnsupportedProviderRequestError(f"unknown offline style preset: {style_preset}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_normalized_region(
    parameters: dict[str, Scalar],
) -> tuple[float, float, float, float]:
    values: list[float] = []
    for name in ("region_left", "region_top", "region_right", "region_bottom"):
        value = parameters.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise UnsupportedProviderRequestError(
                "offline text replacement requires a finite normalized region"
            )
        values.append(float(value))
    left, top, right, bottom = values
    if (
        not 0 <= left < right <= 1
        or not 0 <= top < bottom <= 1
        or right - left < 0.002
        or bottom - top < 0.002
    ):
        raise UnsupportedProviderRequestError(
            "offline text replacement region is empty or outside the source"
        )
    return (left, top, right, bottom)


def _validated_perspective_quad(
    parameters: dict[str, Scalar],
    region: tuple[float, float, float, float],
) -> NormalizedQuad | None:
    names = (
        "perspective_tl_x",
        "perspective_tl_y",
        "perspective_tr_x",
        "perspective_tr_y",
        "perspective_br_x",
        "perspective_br_y",
        "perspective_bl_x",
        "perspective_bl_y",
    )
    present = tuple(name in parameters for name in names)
    if not any(present):
        return None
    if not all(present):
        raise UnsupportedProviderRequestError(
            "offline perspective text replacement requires all eight coordinates"
        )
    values = tuple(parameters[name] for name in names)
    try:
        return validate_normalized_quad(values, region=region)  # type: ignore[arg-type]
    except ValueError as error:
        raise UnsupportedProviderRequestError(str(error)) from error


def _pixel_region(
    region: tuple[float, float, float, float],
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = image_size
    left = max(0, min(width - 1, round(region[0] * width)))
    top = max(0, min(height - 1, round(region[1] * height)))
    right = max(left + 1, min(width, round(region[2] * width)))
    bottom = max(top + 1, min(height, round(region[3] * height)))
    return (left, top, right, bottom)


def _sample_region_background(
    image: Image.Image,
    region: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    left, top, right, bottom = region
    width, height = image.size
    margin = max(1, min(8, min(right - left, bottom - top) // 6))
    outer_left = max(0, left - margin)
    outer_top = max(0, top - margin)
    outer_right = min(width, right + margin)
    outer_bottom = min(height, bottom + margin)
    perimeter = max(1, (outer_right - outer_left) + (outer_bottom - outer_top))
    stride = max(1, perimeter // 2048)
    pixels: list[tuple[int, int, int, int]] = []
    for y in range(outer_top, outer_bottom, stride):
        for x in range(outer_left, outer_right, stride):
            if left <= x < right and top <= y < bottom:
                continue
            if x < left or x >= right or y < top or y >= bottom:
                pixels.append(cast(tuple[int, int, int, int], image.getpixel((x, y))))
    if not pixels:
        pixels = [
            cast(tuple[int, int, int, int], image.getpixel(point))
            for point in (
                (0, 0),
                (width - 1, 0),
                (0, height - 1),
                (width - 1, height - 1),
            )
        ]
    return cast(
        tuple[int, int, int, int],
        tuple(sorted(pixel[channel] for pixel in pixels)[len(pixels) // 2] for channel in range(4)),
    )


def _draw_replacement_text(
    patch: Image.Image,
    *,
    text: str,
    font_family: str,
    font_size: int,
    color: str,
    align: str,
    curve: float,
    visual_effect: str = "plain",
    effect_color: str = "#ffffff",
    effect_size: int = 4,
) -> None:
    if abs(curve) > 1e-9:
        _draw_curved_replacement_text(
            patch,
            text=text,
            font_family=font_family,
            font_size=font_size,
            color=color,
            align=align,
            curve=curve,
            visual_effect=visual_effect,
            effect_color=effect_color,
            effect_size=effect_size,
        )
        return
    draw = ImageDraw.Draw(patch)
    selected_size = min(font_size, max(8, patch.height))
    font = _replacement_font(font_family, selected_size)
    spacing = max(1, round(selected_size * 0.18))
    bounds = _styled_multiline_bounds(
        draw,
        text=text,
        font=font,
        spacing=spacing,
        align=align,
        visual_effect=visual_effect,
        effect_size=effect_size,
    )
    while (
        bounds[2] - bounds[0] > patch.width or bounds[3] - bounds[1] > patch.height
    ) and selected_size > 8:
        selected_size = max(8, selected_size - max(1, selected_size // 8))
        font = _replacement_font(font_family, selected_size)
        spacing = max(1, round(selected_size * 0.18))
        bounds = _styled_multiline_bounds(
            draw,
            text=text,
            font=font,
            spacing=spacing,
            align=align,
            visual_effect=visual_effect,
            effect_size=effect_size,
        )
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    if align == "left":
        x = float(-bounds[0])
    elif align == "right":
        x = patch.width - text_width - bounds[0]
    else:
        x = (patch.width - text_width) / 2 - bounds[0]
    y = (patch.height - text_height) / 2 - bounds[1]
    _draw_styled_multiline_text(
        patch,
        position=(x, y),
        text=text,
        font=font,
        spacing=spacing,
        align=align,
        color=color,
        visual_effect=visual_effect,
        effect_color=effect_color,
        effect_size=effect_size,
    )


def _styled_multiline_bounds(
    draw: ImageDraw.ImageDraw,
    *,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    spacing: int,
    align: str,
    visual_effect: str,
    effect_size: int,
) -> tuple[float, float, float, float]:
    stroke_width = effect_size if visual_effect == "outline" else 0
    bounds = draw.multiline_textbbox(
        (0, 0),
        text,
        font=font,
        spacing=spacing,
        align=align,
        stroke_width=stroke_width,
    )
    if visual_effect == "glow":
        glow_extent = effect_size * 3
        return (
            bounds[0] - glow_extent,
            bounds[1] - glow_extent,
            bounds[2] + glow_extent,
            bounds[3] + glow_extent,
        )
    if visual_effect != "shadow":
        return bounds
    return (
        bounds[0],
        bounds[1],
        bounds[2] + effect_size,
        bounds[3] + effect_size,
    )


def _draw_styled_multiline_text(
    patch: Image.Image,
    position: tuple[float, float],
    text: str,
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    spacing: int,
    align: str,
    color: str,
    visual_effect: str,
    effect_color: str,
    effect_size: int,
) -> None:
    draw = ImageDraw.Draw(patch)
    fill = ImageColor.getcolor(color, "RGBA")
    secondary = ImageColor.getcolor(effect_color, "RGBA")
    if visual_effect == "outline":
        draw.multiline_text(
            position,
            text,
            font=font,
            fill=fill,
            spacing=spacing,
            align=align,
            stroke_width=effect_size,
            stroke_fill=secondary,
        )
        return
    if visual_effect == "shadow":
        draw.multiline_text(
            (position[0] + effect_size, position[1] + effect_size),
            text,
            font=font,
            fill=secondary,
            spacing=spacing,
            align=align,
        )
        draw.multiline_text(
            position,
            text,
            font=font,
            fill=fill,
            spacing=spacing,
            align=align,
        )
        return
    if visual_effect == "gradient":
        mask = Image.new("L", patch.size, 0)
        try:
            ImageDraw.Draw(mask).multiline_text(
                position,
                text,
                font=font,
                fill=255,
                spacing=spacing,
                align=align,
            )
            _composite_linear_gradient(
                patch,
                mask=mask,
                start_color=color,
                end_color=effect_color,
            )
        finally:
            mask.close()
        return
    if visual_effect == "glow":
        mask = Image.new("L", patch.size, 0)
        try:
            ImageDraw.Draw(mask).multiline_text(
                position,
                text,
                font=font,
                fill=255,
                spacing=spacing,
                align=align,
            )
            _composite_glow(
                patch,
                mask=mask,
                color=effect_color,
                radius=effect_size,
            )
        finally:
            mask.close()
        draw.multiline_text(
            position,
            text,
            font=font,
            fill=fill,
            spacing=spacing,
            align=align,
        )
        return
    draw.multiline_text(
        position,
        text,
        font=font,
        fill=fill,
        spacing=spacing,
        align=align,
    )


def _composite_linear_gradient(
    target: Image.Image,
    *,
    mask: Image.Image,
    start_color: str,
    end_color: str,
) -> None:
    ramp = Image.linear_gradient("L")
    resized = ramp.resize(target.size, Image.Resampling.BILINEAR)
    colored = ImageOps.colorize(resized, black=start_color, white=end_color).convert("RGBA")
    try:
        colored.putalpha(mask)
        target.alpha_composite(colored)
    finally:
        ramp.close()
        resized.close()
        colored.close()


def _composite_glow(
    target: Image.Image,
    *,
    mask: Image.Image,
    color: str,
    radius: int,
) -> None:
    blurred = mask.filter(ImageFilter.GaussianBlur(radius=radius))
    boosted = blurred.point(lambda value: min(255, round(value * 2.25)))
    glow = Image.new("RGBA", target.size, ImageColor.getcolor(color, "RGBA"))
    try:
        glow.putalpha(boosted)
        target.alpha_composite(glow)
    finally:
        blurred.close()
        boosted.close()
        glow.close()


def _draw_curved_replacement_text(
    patch: Image.Image,
    *,
    text: str,
    font_family: str,
    font_size: int,
    color: str,
    align: str,
    curve: float,
    visual_effect: str,
    effect_color: str,
    effect_size: int,
) -> None:
    selected_size = min(font_size, max(8, round(patch.height * 0.62)))
    font = _replacement_font(font_family, selected_size)

    def layout_metrics() -> tuple[list[float], float, float]:
        spacing = max(1.0, selected_size * 0.05)
        advances = [max(1.0, float(font.getlength(character))) for character in text]
        total = sum(advances) + spacing * max(0, len(advances) - 1)
        amplitude = abs(curve) * min(patch.height * 0.28, selected_size * 1.35)
        return advances, total, amplitude

    advances, total_width, amplitude = layout_metrics()
    while (
        total_width > patch.width * 0.96
        or selected_size * 1.55 + amplitude * 2 > patch.height * 0.96
    ) and selected_size > 8:
        selected_size = max(8, selected_size - max(1, selected_size // 8))
        font = _replacement_font(font_family, selected_size)
        advances, total_width, amplitude = layout_metrics()

    if align == "left":
        start_x = 0.0
    elif align == "right":
        start_x = patch.width - total_width
    else:
        start_x = (patch.width - total_width) / 2
    start_x = max(0.0, start_x)
    spacing = max(1.0, selected_size * 0.05)
    center_y = patch.height / 2
    cursor = start_x
    for character, advance in zip(text, advances, strict=True):
        character_center = cursor + advance / 2
        progress = (character_center - start_x) / max(1.0, total_width)
        vertical_offset = curve * amplitude * (4 * (progress - 0.5) ** 2 - 1)
        tangent = curve * amplitude * 8 * (progress - 0.5) / max(1.0, total_width)
        angle = -math.degrees(math.atan(tangent))
        stroke_width = effect_size if visual_effect == "outline" else 0
        bounds = font.getbbox(character, stroke_width=stroke_width)
        if visual_effect == "shadow":
            bounds = (bounds[0], bounds[1], bounds[2] + effect_size, bounds[3] + effect_size)
        elif visual_effect == "glow":
            glow_extent = effect_size * 3
            bounds = (
                bounds[0] - glow_extent,
                bounds[1] - glow_extent,
                bounds[2] + glow_extent,
                bounds[3] + glow_extent,
            )
        glyph_width = max(1, math.ceil(bounds[2] - bounds[0]))
        glyph_height = max(1, math.ceil(bounds[3] - bounds[1]))
        effect_padding = effect_size * 3 if visual_effect == "glow" else effect_size
        padding = max(3 + effect_padding, round(selected_size * 0.35))
        glyph = Image.new(
            "RGBA",
            (glyph_width + padding * 2, glyph_height + padding * 2),
            (0, 0, 0, 0),
        )
        try:
            _draw_styled_glyph(
                glyph,
                position=(padding - bounds[0], padding - bounds[1]),
                character=character,
                font=font,
                color=color,
                visual_effect=visual_effect,
                effect_color=effect_color,
                effect_size=effect_size,
            )
            rotated = glyph.rotate(
                angle,
                resample=Image.Resampling.BICUBIC,
                expand=True,
            )
            try:
                patch.alpha_composite(
                    rotated,
                    (
                        round(character_center - rotated.width / 2),
                        round(center_y + vertical_offset - rotated.height / 2),
                    ),
                )
            finally:
                rotated.close()
        finally:
            glyph.close()
        cursor += advance + spacing


def _draw_styled_glyph(
    glyph: Image.Image,
    position: tuple[float, float],
    character: str,
    *,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    color: str,
    visual_effect: str,
    effect_color: str,
    effect_size: int,
) -> None:
    draw = ImageDraw.Draw(glyph)
    fill = ImageColor.getcolor(color, "RGBA")
    secondary = ImageColor.getcolor(effect_color, "RGBA")
    if visual_effect == "outline":
        draw.text(
            position,
            character,
            font=font,
            fill=fill,
            stroke_width=effect_size,
            stroke_fill=secondary,
        )
        return
    if visual_effect == "shadow":
        draw.text(
            (position[0] + effect_size, position[1] + effect_size),
            character,
            font=font,
            fill=secondary,
        )
        draw.text(position, character, font=font, fill=fill)
        return
    if visual_effect == "gradient":
        mask = Image.new("L", glyph.size, 0)
        try:
            ImageDraw.Draw(mask).text(position, character, font=font, fill=255)
            _composite_linear_gradient(
                glyph,
                mask=mask,
                start_color=color,
                end_color=effect_color,
            )
        finally:
            mask.close()
        return
    if visual_effect == "glow":
        mask = Image.new("L", glyph.size, 0)
        try:
            ImageDraw.Draw(mask).text(position, character, font=font, fill=255)
            _composite_glow(
                glyph,
                mask=mask,
                color=effect_color,
                radius=effect_size,
            )
        finally:
            mask.close()
        draw.text(position, character, font=font, fill=fill)
        return
    draw.text(position, character, font=font, fill=fill)


def _warp_surface_to_quad(
    surface: Image.Image,
    *,
    output_size: tuple[int, int],
    destination: NormalizedQuad,
) -> Image.Image:
    destination_points = [(destination[index], destination[index + 1]) for index in range(0, 8, 2)]
    source_points = (
        (0.0, 0.0),
        (float(surface.width - 1), 0.0),
        (float(surface.width - 1), float(surface.height - 1)),
        (0.0, float(surface.height - 1)),
    )
    matrix_rows: list[list[float]] = []
    targets: list[float] = []
    for (x, y), (source_x, source_y) in zip(
        destination_points,
        source_points,
        strict=True,
    ):
        matrix_rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -source_x * x, -source_x * y])
        targets.append(source_x)
        matrix_rows.append([0.0, 0.0, 0.0, x, y, 1.0, -source_y * x, -source_y * y])
        targets.append(source_y)
    coefficients = np.linalg.solve(
        np.asarray(matrix_rows, dtype=np.float64),
        np.asarray(targets, dtype=np.float64),
    )
    warped = surface.transform(
        output_size,
        Image.Transform.PERSPECTIVE,
        tuple(float(value) for value in coefficients),
        resample=Image.Resampling.BICUBIC,
    )
    mask = Image.new("L", output_size, 0)
    try:
        ImageDraw.Draw(mask).polygon(destination_points, fill=255)
        alpha = warped.getchannel("A")
        try:
            clipped_alpha = ImageChops.multiply(alpha, mask)
            try:
                warped.putalpha(clipped_alpha)
            finally:
                clipped_alpha.close()
        finally:
            alpha.close()
    finally:
        mask.close()
    return warped


def _replacement_font(
    font_family: str,
    size: int,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windows = Path(os.environ.get("WINDIR", "C:/Windows"))
    candidates = (
        windows / "Fonts" / FONT_FILES[font_family],
        windows / "Fonts" / "msyh.ttc",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _local_file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme.casefold() != "file" or parsed.netloc not in {"", "localhost"}:
        raise UnsupportedProviderRequestError("offline edit source must be a local file URI")
    value = unquote(parsed.path)
    if os.name == "nt" and len(value) >= 3 and value[0] == "/" and value[2] == ":":
        value = value[1:]
    path = Path(value)
    if not path.is_absolute():
        raise UnsupportedProviderRequestError("offline edit source must use an absolute path")
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise UnsupportedProviderRequestError("offline edit source is unavailable") from error
