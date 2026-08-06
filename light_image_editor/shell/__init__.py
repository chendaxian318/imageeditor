"""Public four-mode shell API."""

from .feature_registry import (
    BASE_FEATURE_DEFINITIONS,
    EXISTING_FEATURES,
    FeatureDefinition,
    FeatureRegistry,
    FeatureRegistryError,
    create_default_feature_registry,
    default_feature_registry,
    normalize_shortcut,
)
from .mode_router import (
    Asset,
    AssetHandoff,
    ModeChange,
    ModeContext,
    ModeId,
    ModeRouter,
    ModeTransition,
    Revision,
    RevisionHandoff,
)

__all__ = [
    "BASE_FEATURE_DEFINITIONS",
    "EXISTING_FEATURES",
    "Asset",
    "AssetHandoff",
    "FeatureDefinition",
    "FeatureRegistry",
    "FeatureRegistryError",
    "ModeChange",
    "ModeContext",
    "ModeId",
    "ModeRouter",
    "ModeTransition",
    "Revision",
    "RevisionHandoff",
    "create_default_feature_registry",
    "default_feature_registry",
    "normalize_shortcut",
]
