"""Unit tests for ``band.integrations.omp``."""

from __future__ import annotations

import pytest

from band.integrations.omp import (
    DEFAULT_OMP_ACP_COMMAND,
    DEFAULT_OMP_MODEL,
    OMP_APPROVAL_MODE_ALWAYS_ASK,
    OMP_APPROVAL_MODE_FLAG,
    OMP_ELICITATION_CALL_ID_PREFIX,
    OMP_FORM_APPROVE,
    OMP_FORM_DENY,
    OMP_PINNED_PACKAGE,
    XD_MCP_PREFIX,
    finalize_omp_command,
    is_omp_approve_deny_form,
    normalize_omp_mcp_device_call,
    normalize_omp_mcp_tool_name,
    omp_command_in_workspace,
    omp_elicitation_call_id,
    omp_model_provider,
    omp_provider_env,
    validate_omp_command,
)
from tests.paths import CI_SCRIPTS


def test_default_command_is_safe_after_finalize() -> None:
    assert finalize_omp_command(DEFAULT_OMP_ACP_COMMAND)[-2:] == [
        OMP_APPROVAL_MODE_FLAG,
        OMP_APPROVAL_MODE_ALWAYS_ASK,
    ]


def test_final_command_selects_the_model_before_safety_override() -> None:
    assert finalize_omp_command(DEFAULT_OMP_ACP_COMMAND, model=DEFAULT_OMP_MODEL)[
        -3:
    ] == [
        f"--model={DEFAULT_OMP_MODEL}",
        OMP_APPROVAL_MODE_FLAG,
        OMP_APPROVAL_MODE_ALWAYS_ASK,
    ]


def test_workspace_cwd_needs_the_acp_subcommand_it_belongs_to() -> None:
    assert omp_command_in_workspace(("bunx", "omp", "acp", "-v"), "/w") == [
        "bunx",
        "omp",
        "acp",
        "--cwd=/w",
        "-v",
    ]
    with pytest.raises(ValueError, match="'acp' subcommand"):
        omp_command_in_workspace(("omp-acp-wrapper",), "/w")


@pytest.mark.parametrize(
    "command",
    [
        ("omp", "acp", "--yolo"),
        ("omp", "acp", "--auto-approve"),
        ("omp", "acp", OMP_APPROVAL_MODE_FLAG, "write"),
        ("omp", "acp", OMP_APPROVAL_MODE_FLAG, "yolo"),
        ("omp", "acp", f"{OMP_APPROVAL_MODE_FLAG}=write"),
        ("omp", "acp", f"{OMP_APPROVAL_MODE_FLAG}=yolo"),
        ("omp", "acp", OMP_APPROVAL_MODE_FLAG, "YOLO"),
        ("omp", "acp", OMP_APPROVAL_MODE_FLAG, "Write"),
        ("omp", "acp", f"{OMP_APPROVAL_MODE_FLAG}=YOLO"),
        ("omp", "acp", f"{OMP_APPROVAL_MODE_FLAG}=Write"),
    ],
)
def test_validate_rejects_unsafe_flags(command: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        validate_omp_command(command)


def test_finalize_appends_always_ask_even_when_command_looks_safe() -> None:
    command = (
        "omp",
        "acp",
        OMP_APPROVAL_MODE_FLAG,
        OMP_APPROVAL_MODE_ALWAYS_ASK,
        "--config",
        "x.json",
    )
    finalized = finalize_omp_command(command)
    assert finalized.count(OMP_APPROVAL_MODE_ALWAYS_ASK) >= 1
    assert finalized[-2:] == [OMP_APPROVAL_MODE_FLAG, OMP_APPROVAL_MODE_ALWAYS_ASK]


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
        "xd://mcp__band_send_message",
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


def test_normalize_mcp_title_maps_registered_band_tool() -> None:
    own = frozenset({"band_send_message"})
    title = f"{XD_MCP_PREFIX}band_send_message"
    assert normalize_omp_mcp_tool_name(title, own) == "band_send_message"
    name, args = normalize_omp_mcp_device_call(title, {"chat_id": "r1"}, own)
    assert name == "band_send_message"
    assert args == {"chat_id": "r1"}


def test_normalize_device_call_leaves_unknown_paths() -> None:
    arguments = {"path": "xd://mcp__other__tool", "content": "{}"}
    assert normalize_omp_mcp_device_call(
        "write", arguments, frozenset({"band_send_message"})
    ) == (
        "write",
        arguments,
    )


@pytest.mark.parametrize("content", ["not json", "[1, 2]", "{"])
def test_a_malformed_write_to_a_band_tool_keeps_its_identity(content: str) -> None:
    """OMP executes the payload and the tool refuses it, so the call fails; it
    must still read as that Band tool, or its failure is lost (a failed reply
    goes unnoticed)."""
    arguments = {"path": "xd://mcp__band_send_message", "content": content}
    assert normalize_omp_mcp_device_call(
        "write", arguments, frozenset({"band_send_message"})
    ) == ("band_send_message", arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"path": "xd://mcp__band_send_message"}, id="no-content"),
        pytest.param(
            {"path": "xd://mcp__band_send_message", "content": {"content": "hi"}},
            id="object-content",
        ),
    ],
)
def test_an_executed_write_without_a_string_payload_keeps_its_identity(
    arguments: dict[str, object],
) -> None:
    """OMP reports a device write as ``execute`` (a read as ``read``) and
    refuses a missing or non-string payload; the failed call is still the
    Band tool's."""
    assert normalize_omp_mcp_device_call(
        "Sending the answer",
        arguments,
        frozenset({"band_send_message"}),
        kind="execute",
    ) == ("band_send_message", arguments)


def test_a_read_is_never_the_band_tool() -> None:
    """A call OMP reports as ``read`` is discovery, whatever arguments ride
    along with it."""
    arguments = {"path": "xd://mcp__band_send_message", "content": '{"content":"hi"}'}
    assert normalize_omp_mcp_device_call(
        "read", arguments, frozenset({"band_send_message"}), kind="read"
    ) == ("read", arguments)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        pytest.param("read", {"path": "xd://mcp__band_send_message"}, id="read"),
        pytest.param(
            "write", {"path": "xd://mcp__band_send_message", "content": ""}, id="empty"
        ),
        pytest.param(
            "write", {"path": "xd://mcp__band_send_message", "content": "?"}, id="?"
        ),
        pytest.param(
            "write",
            {"path": "xd://mcp__band_send_message", "content": " HELP \n"},
            id="help",
        ),
    ],
)
def test_a_documentation_request_is_not_the_band_tool(
    name: str, arguments: dict[str, object]
) -> None:
    """Reading a device, or writing empty, ``?`` or ``help``, shows the tool's
    docs without running it; naming it as the tool would count a reply."""
    assert normalize_omp_mcp_device_call(
        name, arguments, frozenset({"band_send_message"})
    ) == (name, arguments)


def test_omp_elicitation_call_id_format() -> None:
    call_id = omp_elicitation_call_id("session-abc")
    assert call_id.startswith(f"{OMP_ELICITATION_CALL_ID_PREFIX}session-abc:")


def test_provider_env_routes_the_selected_model_key_only() -> None:
    assert omp_model_provider(DEFAULT_OMP_MODEL) == "openai"
    env = omp_provider_env(model=DEFAULT_OMP_MODEL, api_key="secret")
    assert env == {"OPENAI_API_KEY": "secret"}
    assert omp_provider_env(model="google/gemini-2.5-flash", api_key="secret") == {
        "GEMINI_API_KEY": "secret"
    }


def test_default_command_uses_approval_mode_constants() -> None:
    assert DEFAULT_OMP_ACP_COMMAND[-2:] == (
        OMP_APPROVAL_MODE_FLAG,
        OMP_APPROVAL_MODE_ALWAYS_ASK,
    )


def test_setup_omp_script_pins_same_package() -> None:
    script = (CI_SCRIPTS / "setup-omp.sh").read_text()
    assert OMP_PINNED_PACKAGE in script
