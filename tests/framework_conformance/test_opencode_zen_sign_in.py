"""The OpenCode Zen sign-in script exports a masked key only when both exchanges succeed."""

from __future__ import annotations

from pathlib import Path

from tests.framework_conformance.ciscriptkit import (
    OIDC_OK,
    OIDC_TOKEN,
    SIGN_IN_FAILED,
    UNREACHED,
    ZEN_KEY,
    SignInEndpoints,
    posix_shell_only,
    sign_in,
)


@posix_shell_only
class TestOpencodeZenSignIn:
    def test_masks_and_exports_a_granted_key(self, tmp_path: Path) -> None:
        endpoints = SignInEndpoints(
            OIDC_OK, (200, {"access_token": ZEN_KEY, "expires_in": 3600})
        )

        result, github_env = sign_in(tmp_path, endpoints)

        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            f"::add-mask::{ZEN_KEY}",
            "OpenCode Zen token expires_in=3600",
        ]
        assert github_env == f"OPENCODE_ZEN_API_KEY={ZEN_KEY}\n"
        assert endpoints.oidc_requests == [
            ("/oidc?v=1&audience=https://opencode.ai/console", "Bearer request-token")
        ]
        assert endpoints.exchanges == [
            {
                "grant_type": ["urn:ietf:params:oauth:grant-type:token-exchange"],
                "subject_token_type": ["urn:ietf:params:oauth:token-type:jwt"],
                "client_id": ["client-1"],
                "subject_token": [OIDC_TOKEN],
            }
        ]

    def test_logs_a_rejection_reason_and_exports_nothing(self, tmp_path: Path) -> None:
        rejection = {
            "error": "invalid_grant",
            "error_description": "branch not trusted",
        }

        result, github_env = sign_in(
            tmp_path, SignInEndpoints(OIDC_OK, (400, rejection))
        )

        assert result.returncode == 1
        assert f"{SIGN_IN_FAILED}token exchange:" in result.stderr
        assert "branch not trusted" in result.stderr
        assert github_env == ""

    def test_rejects_an_exchange_that_grants_no_key(self, tmp_path: Path) -> None:
        result, github_env = sign_in(
            tmp_path, SignInEndpoints(OIDC_OK, (200, {"expires_in": 3600}))
        )

        assert result.returncode == 1
        assert (
            f'{SIGN_IN_FAILED}token exchange returned no access_token (keys: ["expires_in"])'
            in result.stderr
        )
        assert github_env == ""

    def test_stops_before_the_exchange_without_an_oidc_token(
        self, tmp_path: Path
    ) -> None:
        endpoints = SignInEndpoints((200, {}), UNREACHED)

        result, github_env = sign_in(tmp_path, endpoints)

        assert result.returncode == 1
        assert f"{SIGN_IN_FAILED}GitHub OIDC response has no token" in result.stderr
        assert endpoints.exchanges == []
        assert github_env == ""

    def test_logs_a_refused_oidc_request_and_skips_the_exchange(
        self, tmp_path: Path
    ) -> None:
        refusal = {"message": "Unable to get ACTIONS_ID_TOKEN_REQUEST_URL"}
        endpoints = SignInEndpoints((403, refusal), UNREACHED)

        result, github_env = sign_in(tmp_path, endpoints)

        assert result.returncode == 1
        assert f"{SIGN_IN_FAILED}GitHub OIDC request:" in result.stderr
        assert refusal["message"] in result.stderr
        assert endpoints.exchanges == []
        assert github_env == ""
