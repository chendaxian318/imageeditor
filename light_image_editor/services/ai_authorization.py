"""One-shot authorization boundary for AI provider execution.

The gate deliberately keeps consent separate from provider implementations. A
grant is an opaque, in-memory bearer token tied to one provider, one immutable
request fingerprint, and the exact risk/cost envelope that the user approved.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from enum import StrEnum

from light_image_editor.domain import AIRequest
from light_image_editor.providers import ProviderManifest

_PROVIDER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_REQUEST_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
_CURRENCY_CODE_PATTERN = re.compile(r"[A-Z]{3}")


class AuthorizationScope(StrEnum):
    """Independent permissions represented by an execution authorization."""

    PROVIDER_EXECUTION = "provider_execution"
    REMOTE_IMAGE_UPLOAD = "remote_image_upload"
    PAID_EXECUTION = "paid_execution"


class SubmissionState(StrEnum):
    """Provider submission state as known by the caller."""

    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    UNKNOWN = "unknown"


class AIAuthorizationError(RuntimeError):
    """Base class for fail-closed errors at the authorization boundary."""

    code = "ai_authorization_error"
    retryable = False


class ExplicitAuthorizationRequiredError(AIAuthorizationError):
    """Raised when one or more distinct user consents have not been supplied."""

    code = "explicit_authorization_required"

    def __init__(self, required_scopes: tuple[AuthorizationScope, ...]) -> None:
        self.required_scopes = required_scopes
        super().__init__("The requested AI operation requires explicit authorization.")


class ExecutionAuthorizationRequiredError(AIAuthorizationError):
    """Raised when an execution requires a one-shot grant but none was provided."""

    code = "execution_authorization_required"

    def __init__(self, required_scopes: tuple[AuthorizationScope, ...]) -> None:
        self.required_scopes = required_scopes
        super().__init__("A one-shot execution authorization is required.")


class InvalidExecutionAuthorizationError(AIAuthorizationError):
    """Raised for an unknown, consumed, or mismatched grant."""

    code = "invalid_execution_authorization"

    def __init__(self) -> None:
        super().__init__("The execution authorization is invalid or has already been consumed.")


class SubmissionStateUnknownError(AIAuthorizationError):
    """Expresses an ambiguous submission that must never be retried automatically."""

    code = "submission_state_unknown"
    retryable = False

    def __init__(self) -> None:
        super().__init__("The provider submission state is unknown; automatic retry is denied.")


@dataclass(frozen=True, slots=True)
class AIExecutionRequirements:
    """The complete authorization envelope for one immutable AI request."""

    provider_id: str
    request_fingerprint: str
    offline: bool
    remote_image_upload: bool = False
    estimated_cost_minor: int = 0
    currency_code: str = "CNY"

    def __post_init__(self) -> None:
        if not _PROVIDER_ID_PATTERN.fullmatch(self.provider_id):
            raise ValueError("provider id has an invalid format")
        if not _REQUEST_FINGERPRINT_PATTERN.fullmatch(self.request_fingerprint):
            raise ValueError("request fingerprint must be a lowercase SHA-256 digest")
        if type(self.offline) is not bool:
            raise TypeError("offline must be a boolean")
        if type(self.remote_image_upload) is not bool:
            raise TypeError("remote image upload must be a boolean")
        if type(self.estimated_cost_minor) is not int:
            raise TypeError("estimated cost must use integer minor units")
        if self.estimated_cost_minor < 0:
            raise ValueError("estimated cost cannot be negative")
        if not _CURRENCY_CODE_PATTERN.fullmatch(self.currency_code):
            raise ValueError("currency code must contain three uppercase ASCII letters")

    @classmethod
    def for_request(
        cls,
        manifest: ProviderManifest,
        request: AIRequest,
        *,
        remote_image_upload: bool = False,
        estimated_cost_minor: int = 0,
        currency_code: str = "CNY",
    ) -> AIExecutionRequirements:
        """Create requirements from the provider contract and request fingerprint."""

        return cls(
            provider_id=manifest.provider_id,
            request_fingerprint=request.fingerprint(),
            offline=manifest.offline,
            remote_image_upload=remote_image_upload,
            estimated_cost_minor=estimated_cost_minor,
            currency_code=currency_code,
        )

    @property
    def is_directly_allowed(self) -> bool:
        """Only a wholly local, free operation may bypass a one-shot grant."""

        return self.offline and not self.remote_image_upload and self.estimated_cost_minor == 0

    @property
    def required_scopes(self) -> tuple[AuthorizationScope, ...]:
        scopes = [AuthorizationScope.PROVIDER_EXECUTION]
        if self.remote_image_upload:
            scopes.append(AuthorizationScope.REMOTE_IMAGE_UPLOAD)
        if self.estimated_cost_minor > 0:
            scopes.append(AuthorizationScope.PAID_EXECUTION)
        return tuple(scopes)


@dataclass(frozen=True, slots=True)
class ExecutionAuthorization:
    """Opaque one-shot grant returned after explicit consent checks."""

    token: str = field(repr=False)
    provider_id: str
    request_fingerprint: str
    remote_image_upload_authorized: bool
    authorized_cost_minor: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class AuthorizedExecution:
    """Receipt proving that the gate allowed this exact execution envelope."""

    provider_id: str
    request_fingerprint: str
    used_one_shot_authorization: bool
    remote_image_upload_authorized: bool
    authorized_cost_minor: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class _GrantRecord:
    binding_digest: bytes


class AIAuthorizationGate:
    """Issues and atomically consumes request-bound execution authorizations."""

    def __init__(self) -> None:
        self._grants: dict[bytes, _GrantRecord] = {}
        self._lock = threading.Lock()

    def issue_authorization(
        self,
        requirements: AIExecutionRequirements,
        *,
        authorize_remote_image_upload: bool = False,
        authorize_paid_execution: bool = False,
    ) -> ExecutionAuthorization:
        """Issue a grant after checking each high-impact consent independently."""

        if type(authorize_remote_image_upload) is not bool:
            raise TypeError("remote image upload authorization must be a boolean")
        if type(authorize_paid_execution) is not bool:
            raise TypeError("paid execution authorization must be a boolean")

        missing: list[AuthorizationScope] = []
        if requirements.remote_image_upload and not authorize_remote_image_upload:
            missing.append(AuthorizationScope.REMOTE_IMAGE_UPLOAD)
        if requirements.estimated_cost_minor > 0 and not authorize_paid_execution:
            missing.append(AuthorizationScope.PAID_EXECUTION)
        if missing:
            raise ExplicitAuthorizationRequiredError(tuple(missing))

        token = secrets.token_urlsafe(32)
        token_digest = _token_digest(token)
        record = _GrantRecord(binding_digest=_binding_digest(requirements))
        with self._lock:
            self._grants[token_digest] = record
        return ExecutionAuthorization(
            token=token,
            provider_id=requirements.provider_id,
            request_fingerprint=requirements.request_fingerprint,
            remote_image_upload_authorized=requirements.remote_image_upload,
            authorized_cost_minor=requirements.estimated_cost_minor,
            currency_code=requirements.currency_code,
        )

    def authorize_execution(
        self,
        requirements: AIExecutionRequirements,
        authorization: ExecutionAuthorization | None = None,
    ) -> AuthorizedExecution:
        """Allow a safe direct call or atomically consume an exact one-shot grant."""

        if requirements.is_directly_allowed and authorization is None:
            return _receipt(requirements, used_one_shot_authorization=False)
        if authorization is None:
            raise ExecutionAuthorizationRequiredError(requirements.required_scopes)

        token_digest = _token_digest(authorization.token)
        with self._lock:
            record = self._grants.pop(token_digest, None)
        if record is None or not hmac.compare_digest(
            record.binding_digest, _binding_digest(requirements)
        ):
            raise InvalidExecutionAuthorizationError
        return _receipt(requirements, used_one_shot_authorization=True)

    @staticmethod
    def assert_submission_state_known(state: SubmissionState) -> SubmissionState:
        """Fail closed when a timeout leaves provider submission ambiguous."""

        if not isinstance(state, SubmissionState):
            raise TypeError("submission state must be a SubmissionState")
        if state is SubmissionState.UNKNOWN:
            raise SubmissionStateUnknownError
        return state


def _binding_digest(requirements: AIExecutionRequirements) -> bytes:
    payload = {
        "currency_code": requirements.currency_code,
        "estimated_cost_minor": requirements.estimated_cost_minor,
        "offline": requirements.offline,
        "provider_id": requirements.provider_id,
        "remote_image_upload": requirements.remote_image_upload,
        "request_fingerprint": requirements.request_fingerprint,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).digest()


def _token_digest(token: str) -> bytes:
    if not isinstance(token, str) or not token:
        return b""
    return hashlib.sha256(token.encode("utf-8")).digest()


def _receipt(
    requirements: AIExecutionRequirements,
    *,
    used_one_shot_authorization: bool,
) -> AuthorizedExecution:
    return AuthorizedExecution(
        provider_id=requirements.provider_id,
        request_fingerprint=requirements.request_fingerprint,
        used_one_shot_authorization=used_one_shot_authorization,
        remote_image_upload_authorized=requirements.remote_image_upload,
        authorized_cost_minor=requirements.estimated_cost_minor,
        currency_code=requirements.currency_code,
    )
