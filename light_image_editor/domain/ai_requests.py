"""Provider-neutral requests and immutable AI job snapshots."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from .assets import AssetRecord, AssetRevision, Scalar

MAX_GENERATION_DIMENSION = 8_192
MAX_GENERATION_PIXELS = 64_000_000
MAX_GENERATION_REFERENCES = 4


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_common(request_id: str, idempotency_key: str, prompt: str, model: str) -> None:
    if not request_id.strip():
        raise ValueError("request id cannot be empty")
    if not idempotency_key.strip():
        raise ValueError("idempotency key cannot be empty")
    if not prompt.strip():
        raise ValueError("prompt cannot be empty")
    if not model.strip():
        raise ValueError("model cannot be empty")


class JobStatus(StrEnum):
    SUBMITTING = "submitting"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.CANCELLED, self.FAILED, self.UNKNOWN}


@dataclass(frozen=True, slots=True)
class GenerationReference:
    revision: AssetRevision
    role: str = "content"
    strength: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.revision, AssetRevision):
            raise TypeError("generation reference revision must be an AssetRevision")
        if self.role not in {"content", "style", "composition"}:
            raise ValueError("generation reference role must be content, style, or composition")
        if (
            isinstance(self.strength, bool)
            or not isinstance(self.strength, (int, float))
            or not 0.0 <= float(self.strength) <= 1.0
        ):
            raise ValueError("generation reference strength must be between zero and one")
        object.__setattr__(self, "strength", float(self.strength))


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    prompt: str
    model: str = "default"
    width: int = 1024
    height: int = 1024
    count: int = 1
    seed: int | None = None
    style_preset: str = ""
    negative_prompt: str = ""
    reference_revision: AssetRevision | None = None
    reference_role: str = "content"
    reference_strength: float = 0.5
    additional_references: tuple[GenerationReference, ...] = ()
    variation_revision: AssetRevision | None = None
    variation_strength: float = 0.35
    parameters: tuple[tuple[str, Scalar], ...] = ()
    request_id: str = field(default_factory=_new_request_id)
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        key = self.idempotency_key or self.request_id
        parameters = tuple(sorted(self.parameters, key=lambda item: item[0]))
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "parameters", parameters)
        _validate_common(self.request_id, key, self.prompt, self.model)
        if self.width < 1 or self.height < 1:
            raise ValueError("generation dimensions must be positive")
        if self.width > MAX_GENERATION_DIMENSION or self.height > MAX_GENERATION_DIMENSION:
            raise ValueError("generation dimensions exceed the safe limit")
        if self.width * self.height > MAX_GENERATION_PIXELS:
            raise ValueError("generation pixel count exceeds the safe limit")
        if not 1 <= self.count <= 16:
            raise ValueError("generation count must be between 1 and 16")
        if self.seed is not None and (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or not 0 <= self.seed <= 4_294_967_295
        ):
            raise ValueError("generation seed must be between 0 and 4,294,967,295")
        if len(self.style_preset) > 100:
            raise ValueError("style preset cannot exceed 100 characters")
        if len(self.negative_prompt) > 10_000:
            raise ValueError("negative prompt cannot exceed 10,000 characters")
        if self.reference_revision is not None and not isinstance(
            self.reference_revision, AssetRevision
        ):
            raise TypeError("reference revision must be an AssetRevision")
        if self.variation_revision is not None and not isinstance(
            self.variation_revision, AssetRevision
        ):
            raise TypeError("variation revision must be an AssetRevision")
        if self.reference_revision is not None and self.variation_revision is not None:
            raise ValueError("reference and variation revisions cannot be combined")
        if self.reference_role not in {"content", "style", "composition"}:
            raise ValueError("reference role must be content, style, or composition")
        if (
            isinstance(self.reference_strength, bool)
            or not isinstance(self.reference_strength, (int, float))
            or not 0.0 <= float(self.reference_strength) <= 1.0
        ):
            raise ValueError("reference strength must be between zero and one")
        object.__setattr__(self, "reference_strength", float(self.reference_strength))
        additional_references = tuple(self.additional_references)
        if any(
            not isinstance(reference, GenerationReference) for reference in additional_references
        ):
            raise TypeError("additional references must contain GenerationReference values")
        object.__setattr__(self, "additional_references", additional_references)
        reference_count = len(additional_references) + (
            1 if self.reference_revision is not None else 0
        )
        if reference_count > MAX_GENERATION_REFERENCES:
            raise ValueError(
                f"generation requests support at most {MAX_GENERATION_REFERENCES} references"
            )
        reference_ids = [reference.revision.revision_id for reference in additional_references]
        if self.reference_revision is not None:
            reference_ids.insert(0, self.reference_revision.revision_id)
        if len(set(reference_ids)) != len(reference_ids):
            raise ValueError("generation references cannot contain duplicate revisions")
        if additional_references and self.reference_revision is None:
            raise ValueError("additional references require a primary reference revision")
        if additional_references and self.variation_revision is not None:
            raise ValueError("additional references cannot be combined with a variation")
        if (
            isinstance(self.variation_strength, bool)
            or not isinstance(self.variation_strength, (int, float))
            or not 0.0 <= float(self.variation_strength) <= 1.0
        ):
            raise ValueError("variation strength must be between zero and one")
        object.__setattr__(self, "variation_strength", float(self.variation_strength))
        if any(not name.strip() for name, _value in parameters):
            raise ValueError("generation parameter names cannot be empty")
        if len({name for name, _value in parameters}) != len(parameters):
            raise ValueError("generation parameter names must be unique")

    def fingerprint(self) -> str:
        payload = {
            "type": "generation",
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "model": self.model,
            "width": self.width,
            "height": self.height,
            "count": self.count,
            "seed": self.seed,
            "style_preset": self.style_preset,
            "reference_revision_id": (
                None if self.reference_revision is None else self.reference_revision.revision_id
            ),
            "reference_sha256": (
                None if self.reference_revision is None else self.reference_revision.sha256
            ),
            "reference_role": self.reference_role,
            "reference_strength": self.reference_strength,
            "variation_revision_id": (
                None if self.variation_revision is None else self.variation_revision.revision_id
            ),
            "variation_sha256": (
                None if self.variation_revision is None else self.variation_revision.sha256
            ),
            "variation_strength": self.variation_strength,
            "parameters": self.parameters,
        }
        if self.additional_references:
            payload["additional_references"] = [
                {
                    "revision_id": reference.revision.revision_id,
                    "sha256": reference.revision.sha256,
                    "role": reference.role,
                    "strength": reference.strength,
                }
                for reference in self.additional_references
            ]
        return _fingerprint(payload)

    @property
    def all_references(self) -> tuple[GenerationReference, ...]:
        primary = (
            ()
            if self.reference_revision is None
            else (
                GenerationReference(
                    revision=self.reference_revision,
                    role=self.reference_role,
                    strength=self.reference_strength,
                ),
            )
        )
        return (*primary, *self.additional_references)


@dataclass(frozen=True, slots=True)
class ContentEditRequest:
    source_revision: AssetRevision
    prompt: str
    model: str = "default"
    strength: float = 1.0
    mask_revision_id: str | None = None
    parameters: tuple[tuple[str, Scalar], ...] = ()
    request_id: str = field(default_factory=_new_request_id)
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        key = self.idempotency_key or self.request_id
        parameters = tuple(sorted(self.parameters, key=lambda item: item[0]))
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "parameters", parameters)
        _validate_common(self.request_id, key, self.prompt, self.model)
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError("edit strength must be between zero and one")
        if self.mask_revision_id is not None and not self.mask_revision_id.strip():
            raise ValueError("mask revision id cannot be empty")
        if any(not name.strip() for name, _value in parameters):
            raise ValueError("edit parameter names cannot be empty")
        if len({name for name, _value in parameters}) != len(parameters):
            raise ValueError("edit parameter names must be unique")

    def fingerprint(self) -> str:
        payload = {
            "type": "content_edit",
            "source_revision_id": self.source_revision.revision_id,
            "source_sha256": self.source_revision.sha256,
            "prompt": self.prompt,
            "model": self.model,
            "strength": self.strength,
            "mask_revision_id": self.mask_revision_id,
            "parameters": self.parameters,
        }
        return _fingerprint(payload)


AIRequest = GenerationRequest | ContentEditRequest


@dataclass(frozen=True, slots=True)
class JobError:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("job error code and message cannot be empty")


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    request_id: str
    provider_id: str
    status: JobStatus
    outputs: tuple[AssetRecord, ...] = ()
    error: JobError | None = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.job_id.strip() or not self.request_id.strip() or not self.provider_id.strip():
            raise ValueError("job, request, and provider ids cannot be empty")
        object.__setattr__(self, "outputs", tuple(self.outputs))
        if self.status is JobStatus.SUCCEEDED and not self.outputs:
            raise ValueError("successful jobs require at least one output")
        if self.status is not JobStatus.SUCCEEDED and self.outputs:
            raise ValueError("only successful jobs can expose outputs")
        if self.status in {JobStatus.FAILED, JobStatus.UNKNOWN} and self.error is None:
            raise ValueError("failed or unknown jobs require an error")
        if self.status not in {JobStatus.FAILED, JobStatus.UNKNOWN} and self.error is not None:
            raise ValueError("only failed or unknown jobs can expose an error")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("job timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("job update time cannot precede creation")


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
