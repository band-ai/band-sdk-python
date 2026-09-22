"""Unit tests for ``band.integrations.omp``."""

from __future__ import annotations

import pytest

from band.integrations.omp import (
    DEFAULT_OMP_ACP_COMMAND,
    DEFAULT_OMP_MODEL,
    OMP_APPROVAL_MODE_ALWAYS_ASK,
    OMP_APPROVAL_MODE_FLAG,
    OMP_FORM_APPROVE,
    OMP_FORM_DENY,
    OMP_PINNED_PACKAGE,
    finalize_omp_command,
    is_omp_approve_deny_form,
    normalize_omp_mcp_device_call,
    omp_elicitation_call_id,
    omp_model_provider,
    omp_provider_api_key_env,
    omp_provider_env,
    validate_omp_command,
)
from tests.paths import CI_SCRIPTS


def test_default_command_is_safe_after_finalize() -> None:
    assert finalize_omp_command(DEFAULT_OMP_ACP_COMMAND)[-2:] == [
        "--approval-mode",
        "always-ask",
    ]


@pytest.mark.parametrize(
    "command",
    [
        ("omp", "acp", "--yolo"),
        ("omp", "acp", "--auto-approve"),
        ("omp", "acp", "--approval-mode", "write"),
        ("omp", "acp", "--approval-mode", "yolo"),
        ("omp", "acp", "--approval-mode=write"),
        ("omp", "acp", "--approval-mode=yolo"),
    ],
)
def test_validate_rejects_unsafe_flags(command: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        validate_omp_command(command)


def test_finalize_appends_always_ask_even_when_command_looks_safe() -> None:
    command = ("omp", "acp", "--approval-mode", "always-ask", "--config", "x.json")
    finalized = finalize_omp_command(command)
    assert finalized.count("always-ask") >= 1
    assert finalized[-2:] == ["--approval-mode", "always-ask"]


def test_is_omp_approve_deny_form_exact_enum() -> None:
    schema = {
        "type": "object",
        "properties": {
            "choice": {"type": "string", "enum": [OMP_FORM_APPROVE, OMP_FORM_DENY]},
        },
        "required": ["choice"],
    }
    assert is_omp_approve_deny_form(schema) is True
    assert (
        is_omp_approve_deny_form(
            {**schema, "properties": {"choice": {"enum": ["Yes", "No"]}}}
        )
        is False
    )


@pytest.mark.parametrize(
    "path",
    [
        "xd://mcp__band__band_send_message",
        "xd://mcp__band_band_send_message",
    ],
)
def test_normalize_device_call_maps_registered_band_tool(path: str) -> None:
    own = frozenset({"band_send_message"})
    name, args = normalize_omp_mcp_device_call(
        "write",
        {
            "path": path,
            "content": '{"chat_id":"room-1","content":"hi"}',
        },
        own,
    )
    assert name == "band_send_message"
    assert args == {"chat_id": "room-1", "content": "hi"}


def test_normalize_device_call_leaves_unknown_paths() -> None:
    arguments = {"path": "xd://mcp__other__tool", "content": "{}"}
    assert normalize_omp_mcp_device_call(
        "write", arguments, frozenset({"band_send_message"})
    ) == (
        "write",
        arguments,
    )


def test_omp_elicitation_call_id_format() -> None:
    call_id = omp_elicitation_call_id("session-abc")
    assert call_id.startswith("omp-elicitation:session-abc:")


def test_provider_env_uses_gemini_for_google_models() -> None:
    assert omp_model_provider(DEFAULT_OMP_MODEL) == "google"
    assert omp_provider_api_key_env("google") == "GEMINI_API_KEY"
    env = omp_provider_env(model=DEFAULT_OMP_MODEL, api_key="secret")
    assert env["OMP_MODEL"] == DEFAULT_OMP_MODEL
    assert env["GEMINI_API_KEY"] == "secret"


def test_default_command_uses_approval_mode_constants() -> None:
    assert DEFAULT_OMP_ACP_COMMAND[-2:] == (
        OMP_APPROVAL_MODE_FLAG,
        OMP_APPROVAL_MODE_ALWAYS_ASK,
    )


def test_setup_omp_script_pins_same_package() -> None:
    script = (CI_SCRIPTS / "setup-omp.sh").read_text()
    assert OMP_PINNED_PACKAGE in script
