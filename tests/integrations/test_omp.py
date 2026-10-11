"""Unit tests for ``band.integrations.omp``."""

from __future__ import annotations

import pytest

from band.integrations.omp import (
    OMP_APPROVAL_MODE_ALWAYS_ASK,
    OMP_APPROVAL_MODE_FLAG,
    XD_MCP_PREFIX,
    finalize_omp_command,
    normalize_omp_mcp_device_call,
    normalize_omp_mcp_tool_name,
    omp_command_in_workspace,
    omp_model_provider,
    omp_provider_env,
    validate_omp_command,
)


def test_final_command_selects_the_model_before_safety_override() -> None:
    assert finalize_omp_command(
        ("omp", "--mode", "rpc"), model="openai/custom", approval_mode="yolo"
    )[-3:] == ["--model=openai/custom", OMP_APPROVAL_MODE_FLAG, "yolo"]


def test_workspace_is_selected_without_a_subprocess_cwd() -> None:
    assert omp_command_in_workspace(("bunx", "omp", "--mode", "rpc", "-v"), "/w") == [
        "bunx",
        "omp",
        "--mode",
        "rpc",
        "-v",
        "--cwd=/w",
    ]


@pytest.mark.parametrize(
    "command",
    [
        ("omp", "--mode", "rpc", "--yolo"),
        ("omp", "--mode", "rpc", "--auto-approve"),
        ("omp", "--mode", "rpc", OMP_APPROVAL_MODE_FLAG, "write"),
        ("omp", "--mode", "rpc", OMP_APPROVAL_MODE_FLAG, "yolo"),
        ("omp", "--mode", "rpc", f"{OMP_APPROVAL_MODE_FLAG}=write"),
        ("omp", "--mode", "rpc", f"{OMP_APPROVAL_MODE_FLAG}=yolo"),
        ("omp", "--mode", "rpc", OMP_APPROVAL_MODE_FLAG, "YOLO"),
        ("omp", "--mode", "rpc", OMP_APPROVAL_MODE_FLAG, "Write"),
        ("omp", "--mode", "rpc", f"{OMP_APPROVAL_MODE_FLAG}=YOLO"),
        ("omp", "--mode", "rpc", f"{OMP_APPROVAL_MODE_FLAG}=Write"),
    ],
)
def test_validate_rejects_unsafe_flags(command: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        validate_omp_command(command)


def test_finalize_appends_always_ask_even_when_command_looks_safe() -> None:
    command = (
        "omp",
        "--mode",
        "rpc",
        OMP_APPROVAL_MODE_FLAG,
        OMP_APPROVAL_MODE_ALWAYS_ASK,
        "--config",
        "x.json",
    )
    finalized = finalize_omp_command(command)
    assert finalized.count(OMP_APPROVAL_MODE_ALWAYS_ASK) >= 1
    assert finalized[-2:] == [OMP_APPROVAL_MODE_FLAG, OMP_APPROVAL_MODE_ALWAYS_ASK]


@pytest.mark.parametrize(
    "path",
    [
        "xd://mcp__band_send_message",
        "xd://mcp__band__band_send_message",
        "xd://mcp__band_band_send_message",
        "xd://mcp__band_sdk_band_band_send_message",
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


@pytest.mark.parametrize(
    "title",
    [f"{XD_MCP_PREFIX}band_send_message", "mcp__band_sdk_band_band_send_message"],
)
def test_normalize_mcp_title_maps_registered_band_tool(title: str) -> None:
    own = frozenset({"band_send_message"})
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


@pytest.mark.parametrize(
    "path", ["xd://mcp__band_send_message", "xd://mcp__band_sdk_band_band_send_message"]
)
def test_a_read_is_never_the_band_tool(path: str) -> None:
    """A call OMP reports as ``read`` is discovery, whatever arguments ride
    along with it."""
    arguments = {"path": path, "content": '{"content":"hi"}'}
    assert normalize_omp_mcp_device_call(
        "read", arguments, frozenset({"band_send_message"}), kind="read"
    ) == ("read", arguments)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        pytest.param("read", {"path": "xd://mcp__band_send_message"}, id="read"),
        pytest.param(
            "read",
            {"path": "xd://mcp__band_sdk_band_band_send_message"},
            id="owned-plugin-read",
        ),
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


def test_provider_env_routes_the_selected_model_key_only() -> None:
    assert omp_model_provider("openai/custom") == "openai"
    env = omp_provider_env(model="openai/custom", api_key="secret")
    assert env == {"OPENAI_API_KEY": "secret"}
    assert omp_provider_env(model="google/gemini-2.5-flash", api_key="secret") == {
        "GEMINI_API_KEY": "secret"
    }
