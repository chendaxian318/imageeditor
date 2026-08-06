"""Provider manifests, protocols, and boundary errors."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from light_image_editor.domain import (
    ContentEditRequest,
    GenerationRequest,
    JobSnapshot,
)


class ProviderCapability(StrEnum):
    GENERATION = "generation"
    CONTENT_EDIT = "content_edit"
    NEGATIVE_PROMPT = "negative_prompt"
    REFERENCE_IMAGE = "reference_image"
    SEED = "seed"
    STYLE_PRESET = "style_preset"
    VARIATION = "variation"


@dataclass(frozen=True, slots=True)
class ProviderManifest:
    provider_id: str
    name: str
    version: str
    capabilities: tuple[ProviderCapability, ...]
    models: tuple[str, ...]
    offline: bool

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.name.strip() or not self.version.strip():
            raise ValueError("provider id, name, and version cannot be empty")
        capabilities = tuple(self.capabilities)
        models = tuple(self.models)
        if not capabilities:
            raise ValueError("provider must declare at least one capability")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("provider capabilities must be unique")
        if not models or any(not model.strip() for model in models):
            raise ValueError("provider must declare at least one non-empty model")
        if len(set(models)) != len(models):
            raise ValueError("provider models must be unique")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "models", models)

    def supports(self, capability: ProviderCapability, model: str) -> bool:
        return capability in self.capabilities and model in self.models


class ProviderError(RuntimeError):
    """Base class for failures at the provider contract boundary."""


class ProviderJobNotFoundError(ProviderError):
    pass


class IdempotencyConflictError(ProviderError):
    pass


class UnsupportedProviderRequestError(ProviderError):
    pass


@runtime_checkable
class AIProvider(Protocol):
    @property
    def manifest(self) -> ProviderManifest: ...

    def submit_generation(self, request: GenerationRequest) -> JobSnapshot: ...

    def submit_edit(self, request: ContentEditRequest) -> JobSnapshot: ...

    def get_job(self, job_id: str) -> JobSnapshot: ...

    def cancel(self, job_id: str) -> JobSnapshot: ...
