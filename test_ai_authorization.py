from __future__ import annotations

import unittest
from dataclasses import replace

from light_image_editor.domain import GenerationRequest
from light_image_editor.providers import ProviderCapability, ProviderManifest
from light_image_editor.services.ai_authorization import (
    AIAuthorizationGate,
    AIExecutionRequirements,
    AuthorizationScope,
    ExecutionAuthorization,
    ExecutionAuthorizationRequiredError,
    ExplicitAuthorizationRequiredError,
    InvalidExecutionAuthorizationError,
    SubmissionState,
    SubmissionStateUnknownError,
)


def requirements(
    *,
    provider_id: str = "remote.test",
    request_fingerprint: str = "a" * 64,
    offline: bool = False,
    remote_image_upload: bool = False,
    estimated_cost_minor: int = 0,
    currency_code: str = "CNY",
) -> AIExecutionRequirements:
    return AIExecutionRequirements(
        provider_id=provider_id,
        request_fingerprint=request_fingerprint,
        offline=offline,
        remote_image_upload=remote_image_upload,
        estimated_cost_minor=estimated_cost_minor,
        currency_code=currency_code,
    )


class AIAuthorizationGateTests(unittest.TestCase):
    def test_offline_zero_cost_request_is_allowed_without_a_grant(self) -> None:
        gate = AIAuthorizationGate()
        request = requirements(provider_id="offline.test", offline=True)

        receipt = gate.authorize_execution(request)

        self.assertFalse(receipt.used_one_shot_authorization)
        self.assertEqual(receipt.authorized_cost_minor, 0)
        self.assertFalse(receipt.remote_image_upload_authorized)

    def test_remote_execution_requires_and_consumes_a_one_shot_grant(self) -> None:
        gate = AIAuthorizationGate()
        request = requirements()

        with self.assertRaises(ExecutionAuthorizationRequiredError) as missing:
            gate.authorize_execution(request)
        self.assertEqual(
            missing.exception.required_scopes,
            (AuthorizationScope.PROVIDER_EXECUTION,),
        )

        grant = gate.issue_authorization(request)
        receipt = gate.authorize_execution(request, grant)

        self.assertTrue(receipt.used_one_shot_authorization)
        with self.assertRaises(InvalidExecutionAuthorizationError):
            gate.authorize_execution(request, grant)

    def test_upload_and_paid_execution_need_separate_explicit_consents(self) -> None:
        gate = AIAuthorizationGate()
        request = requirements(remote_image_upload=True, estimated_cost_minor=129)

        with self.assertRaises(ExplicitAuthorizationRequiredError) as neither:
            gate.issue_authorization(request)
        self.assertEqual(
            neither.exception.required_scopes,
            (AuthorizationScope.REMOTE_IMAGE_UPLOAD, AuthorizationScope.PAID_EXECUTION),
        )

        with self.assertRaises(ExplicitAuthorizationRequiredError) as upload_only:
            gate.issue_authorization(request, authorize_remote_image_upload=True)
        self.assertEqual(
            upload_only.exception.required_scopes,
            (AuthorizationScope.PAID_EXECUTION,),
        )

        with self.assertRaises(ExplicitAuthorizationRequiredError) as payment_only:
            gate.issue_authorization(request, authorize_paid_execution=True)
        self.assertEqual(
            payment_only.exception.required_scopes,
            (AuthorizationScope.REMOTE_IMAGE_UPLOAD,),
        )

        grant = gate.issue_authorization(
            request,
            authorize_remote_image_upload=True,
            authorize_paid_execution=True,
        )
        receipt = gate.authorize_execution(request, grant)
        self.assertTrue(receipt.remote_image_upload_authorized)
        self.assertEqual(receipt.authorized_cost_minor, 129)

    def test_grant_cannot_cross_provider_or_request_and_mismatch_revokes_it(self) -> None:
        gate = AIAuthorizationGate()
        original = requirements()

        provider_grant = gate.issue_authorization(original)
        with self.assertRaises(InvalidExecutionAuthorizationError):
            gate.authorize_execution(replace(original, provider_id="remote.other"), provider_grant)
        with self.assertRaises(InvalidExecutionAuthorizationError):
            gate.authorize_execution(original, provider_grant)

        request_grant = gate.issue_authorization(original)
        with self.assertRaises(InvalidExecutionAuthorizationError):
            gate.authorize_execution(
                replace(original, request_fingerprint="b" * 64),
                request_grant,
            )

    def test_grant_binds_exact_upload_cost_and_currency_envelope(self) -> None:
        gate = AIAuthorizationGate()
        original = requirements(estimated_cost_minor=100, currency_code="USD")
        grant = gate.issue_authorization(original, authorize_paid_execution=True)

        with self.assertRaises(InvalidExecutionAuthorizationError):
            gate.authorize_execution(replace(original, estimated_cost_minor=101), grant)

        grant = gate.issue_authorization(original, authorize_paid_execution=True)
        with self.assertRaises(InvalidExecutionAuthorizationError):
            gate.authorize_execution(replace(original, currency_code="CNY"), grant)

    def test_cost_requires_nonnegative_integer_minor_units(self) -> None:
        for invalid in (1.5, True, "100"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    requirements(estimated_cost_minor=invalid)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            requirements(estimated_cost_minor=-1)

    def test_requirements_can_be_derived_from_manifest_and_request(self) -> None:
        manifest = ProviderManifest(
            provider_id="offline.mock",
            name="Offline Mock",
            version="1",
            capabilities=(ProviderCapability.GENERATION,),
            models=("mock",),
            offline=True,
        )
        request = GenerationRequest(prompt="测试", model="mock")

        result = AIExecutionRequirements.for_request(manifest, request)

        self.assertEqual(result.provider_id, manifest.provider_id)
        self.assertEqual(result.request_fingerprint, request.fingerprint())
        self.assertTrue(result.is_directly_allowed)

    def test_token_is_hidden_from_repr_and_forged_grants_fail_closed(self) -> None:
        gate = AIAuthorizationGate()
        request = requirements()
        grant = gate.issue_authorization(request)

        self.assertNotIn(grant.token, repr(grant))
        forged = ExecutionAuthorization(
            token="forged",
            provider_id=request.provider_id,
            request_fingerprint=request.request_fingerprint,
            remote_image_upload_authorized=False,
            authorized_cost_minor=0,
            currency_code="CNY",
        )
        with self.assertRaises(InvalidExecutionAuthorizationError):
            gate.authorize_execution(request, forged)

    def test_unknown_submission_state_is_explicitly_non_retryable(self) -> None:
        self.assertIs(
            AIAuthorizationGate.assert_submission_state_known(SubmissionState.NOT_SUBMITTED),
            SubmissionState.NOT_SUBMITTED,
        )
        with self.assertRaises(SubmissionStateUnknownError) as caught:
            AIAuthorizationGate.assert_submission_state_known(SubmissionState.UNKNOWN)
        self.assertEqual(caught.exception.code, "submission_state_unknown")
        self.assertFalse(caught.exception.retryable)


if __name__ == "__main__":
    unittest.main()
