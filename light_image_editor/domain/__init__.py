"""Stable domain contracts for assets and AI-assisted work."""

from .ai_requests import (
    AIRequest,
    ContentEditRequest,
    GenerationReference,
    GenerationRequest,
    JobError,
    JobSnapshot,
    JobStatus,
)
from .alpha_refinement import (
    AlphaBrushStroke,
    parse_alpha_strokes,
    serialize_alpha_strokes,
)
from .assets import AssetRecord, AssetRevision, AssetStorageKind, Provenance, Scalar
from .conversion_checkpoint import (
    ConversionCheckpointErrorCallback,
    ConversionCheckpointFailure,
    ConversionSessionCheckpointWriter,
)
from .conversion_draft import ConversionControlState, ConversionDraft, ConversionDraftHistory
from .conversion_jobs import ConversionInput, ConversionQueue
from .conversion_runner import (
    ConversionOutputValidationError,
    ConversionRunner,
    ConversionRunResult,
    ConversionTaskRecord,
    ConversionTaskStatus,
    TransparencyPolicyError,
    UnsupportedAnimationError,
)
from .conversion_session import (
    CONVERSION_SESSION_SCHEMA_VERSION,
    ConversionSession,
    ConversionSessionError,
    ConversionSessionStore,
    ConversionSessionTooLargeError,
    SessionAssetIdentity,
    SessionRevisionIdentity,
)
from .job_management import JobRequestKind, ManagedJobSnapshot
from .perspective_quad import NormalizedQuad, quad_bounds, validate_normalized_quad
from .recipe_executor import execute_transform_recipe
from .recipe_store import (
    RECIPE_FILE_EXTENSION,
    RecipeFileError,
    RecipeFileTooLargeError,
    RecipeStore,
    load_recipe,
    save_recipe,
)
from .recipes import ExportProfile, TransformRecipe, TransformStep, from_batch_recipe
from .revision_tree import RevisionTree
from .selection_mask_refinement import (
    SelectionMaskStroke,
    parse_selection_mask_strokes,
    serialize_selection_mask_strokes,
)
from .smart_text import (
    SMART_TEXT_ALIGNMENTS,
    SMART_TEXT_FONT_FAMILIES,
    SMART_TEXT_VISUAL_EFFECTS,
    TextReplacementDraft,
    TextReplacementHistory,
    TextVisualEffect,
)

__all__ = [
    "AIRequest",
    "AlphaBrushStroke",
    "AssetRecord",
    "AssetRevision",
    "AssetStorageKind",
    "ContentEditRequest",
    "CONVERSION_SESSION_SCHEMA_VERSION",
    "ConversionCheckpointErrorCallback",
    "ConversionCheckpointFailure",
    "ConversionControlState",
    "ConversionDraft",
    "ConversionDraftHistory",
    "ConversionInput",
    "ConversionOutputValidationError",
    "ConversionQueue",
    "ConversionRunResult",
    "ConversionRunner",
    "ConversionSession",
    "ConversionSessionCheckpointWriter",
    "ConversionSessionError",
    "ConversionSessionStore",
    "ConversionSessionTooLargeError",
    "ConversionTaskRecord",
    "ConversionTaskStatus",
    "ExportProfile",
    "GenerationRequest",
    "GenerationReference",
    "JobError",
    "JobRequestKind",
    "JobSnapshot",
    "JobStatus",
    "ManagedJobSnapshot",
    "NormalizedQuad",
    "Provenance",
    "RECIPE_FILE_EXTENSION",
    "RecipeFileError",
    "RecipeFileTooLargeError",
    "RecipeStore",
    "RevisionTree",
    "Scalar",
    "SelectionMaskStroke",
    "SessionAssetIdentity",
    "SessionRevisionIdentity",
    "SMART_TEXT_ALIGNMENTS",
    "SMART_TEXT_FONT_FAMILIES",
    "SMART_TEXT_VISUAL_EFFECTS",
    "TextReplacementDraft",
    "TextReplacementHistory",
    "TextVisualEffect",
    "TransformRecipe",
    "TransformStep",
    "TransparencyPolicyError",
    "UnsupportedAnimationError",
    "execute_transform_recipe",
    "from_batch_recipe",
    "load_recipe",
    "parse_alpha_strokes",
    "parse_selection_mask_strokes",
    "quad_bounds",
    "save_recipe",
    "serialize_alpha_strokes",
    "serialize_selection_mask_strokes",
    "validate_normalized_quad",
]
