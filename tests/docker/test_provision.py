"""Unit coverage for `band-kit provision`: register/secret/create orchestration,
idempotency, error mapping, and the "no secret ever on argv/in logs" guarantee.

Reuses tests/docker/launcher/fakes.py's workspace builder rather than
hand-rolling another band.yaml fixture — same shape the launcher itself tests
against.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from band_rest.core.api_error import ApiError

from band.docker.launcher.errors import LaunchError
from band.docker.provision import (
    NO_RETRY_REQUEST_OPTIONS,
    PLACEHOLDER_AGENT_ID,
    ProvisionSettings,
    RegistrationTimeoutOutcome,
    _check_registration_after_timeout,
    _describe_register_error,
    _describe_registration_timeout,
    _table_targets,
    build_parser,
    inject_agent_key,
    main,
    read_agent_id,
    run,
    sandbox_exists,
    sandbox_has_band_secret,
    write_agent_id,
)
from tests.docker.launcher.fakes import (
    Workspace,
    default_config,
    make_workspace,
    write_config,
)

# --- Helpers ---


def _empty_ls_output(scope: str) -> str:
    return f'No secrets found for scope "{scope}".\n'


def _ls_table(*, scope: str, host: str) -> str:
    """A realistic `sbx secret ls` table (verified against sbx v0.35.0)."""
    return (
        "CUSTOM SECRETS\n"
        "SCOPE   TARGETS   ENV            PLACEHOLDER     SECRET\n"
        f"{scope}   {host}   BAND_API_KEY   proxy-managed   ******\n"
    )


@dataclass
class FakeSbx:
    """Records every `sbx` invocation and returns scripted results."""

    ls_output: str = ""
    sandbox_ls_json: str = '{"sandboxes": []}'
    set_custom_returncode: int = 0
    set_custom_stderr: str = ""
    create_returncode: int = 0
    create_stderr: str = ""
    calls: list[dict] = field(default_factory=list)

    def __call__(
        self,
        argv,
        *,
        input=None,
        capture_output=True,
        text=True,
        check=False,
        timeout=None,
    ):
        self.calls.append({"argv": list(argv), "input": input})
        if argv[1:3] == ["secret", "ls"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout=self.ls_output, stderr=""
            )
        if argv[1:3] == ["secret", "set-custom"]:
            return subprocess.CompletedProcess(
                argv,
                self.set_custom_returncode,
                stdout="",
                stderr=self.set_custom_stderr,
            )
        if argv[1:3] == ["ls", "--json"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout=self.sandbox_ls_json, stderr=""
            )
        if argv[1] == "create":
            return subprocess.CompletedProcess(
                argv, self.create_returncode, stdout="", stderr=self.create_stderr
            )
        raise AssertionError(f"unexpected sbx invocation: {argv}")


def _register_response(
    agent_id: str = "agent-1", api_key: str = "band-agent-secret-key"
):
    return SimpleNamespace(
        data=SimpleNamespace(
            agent=SimpleNamespace(id=agent_id),
            credentials=SimpleNamespace(api_key=api_key),
        )
    )


def _make_mock_client(register_return=None, register_side_effect=None):
    mock_client = AsyncMock()
    mock_client._client_wrapper.httpx_client.httpx_client.aclose = AsyncMock()
    if register_side_effect is not None:
        mock_client.human_api_agents.register_my_agent.side_effect = (
            register_side_effect
        )
    else:
        mock_client.human_api_agents.register_my_agent.return_value = (
            register_return or _register_response()
        )
    return mock_client


def _agents_response(*agents: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(data=list(agents))


def _make_args(workspace: Path, **overrides) -> argparse.Namespace:
    defaults = {
        "name": "my-agent",
        "agent_name": None,
        "description": "A self-registered Band agent.",
        "workspace": workspace,
        "host": "**.band.ai",
        "api_key": "register-only-user-key",
        "rest_url": None,
        "create": False,
        "kit": None,
        "timeout": 30,
        "verbose": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _placeholder_workspace(tmp_path: Path) -> Workspace:
    workspace = make_workspace(tmp_path)
    config = default_config(workspace)
    config["agent"]["id"] = PLACEHOLDER_AGENT_ID
    write_config(workspace, config)
    return workspace


def _fake_asyncio_run(return_value=None, side_effect=None):
    def _run(coro):
        coro.close()
        if side_effect is not None:
            raise side_effect
        return return_value

    return _run


# --- build_parser ---


class TestBuildParser:
    def test_provision_requires_name_description_workspace(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["provision"])

    def test_create_defaults_true(self):
        parser = build_parser()
        args = parser.parse_args(
            ["provision", "--name", "a", "--description", "d", "--workspace", "."]
        )
        assert args.create is True

    def test_no_create_flag(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "provision",
                "--name",
                "a",
                "--description",
                "d",
                "--workspace",
                ".",
                "--no-create",
            ]
        )
        assert args.create is False

    def test_default_host(self):
        parser = build_parser()
        args = parser.parse_args(
            ["provision", "--name", "a", "--description", "d", "--workspace", "."]
        )
        assert args.host == "**.band.ai"


# --- ProvisionSettings ---


class TestProvisionSettings:
    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("BAND_API_KEY_USER", "env-user-key")
        monkeypatch.setenv("BAND_REST_URL", "https://staging.band.ai")
        settings = ProvisionSettings()
        assert settings.band_api_key_user == "env-user-key"
        assert settings.band_rest_url == "https://staging.band.ai"

    def test_empty_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("BAND_API_KEY_USER", "")
        settings = ProvisionSettings()
        assert settings.band_api_key_user == ""


# --- _describe_register_error ---


class TestDescribeRegisterError:
    def test_403_is_plan_cap(self):
        err = ApiError(status_code=403, body=None)
        assert "cap reached" in _describe_register_error(err)

    def test_422_names_reregistration_undecided(self):
        err = ApiError(status_code=422, body=None)
        message = _describe_register_error(err)
        assert "already exists" in message
        assert "not decided yet" in message

    def test_falls_back_to_body_message(self):
        body = SimpleNamespace(error=SimpleNamespace(message="server exploded"))
        err = ApiError(status_code=500, body=body)
        assert (
            _describe_register_error(err) == "Failed to register agent: server exploded"
        )

    def test_falls_back_to_status_code(self):
        err = ApiError(status_code=500, body=None)
        assert _describe_register_error(err) == "Failed to register agent: HTTP 500"


class TestDescribeRegistrationTimeout:
    """Pure formatting, mirroring TestDescribeRegisterError -- no I/O, no
    mocking. What each outcome actually gets *determined* is
    TestCheckRegistrationAfterTimeout's job, below."""

    def test_confirmed_absent_says_safe_to_retry(self):
        message = _describe_registration_timeout(
            RegistrationTimeoutOutcome.CONFIRMED_ABSENT,
            agent_name="my-agent",
            agent_id=None,
            timeout=30,
        )
        assert "safe to retry" in message

    def test_confirmed_present_names_the_orphan(self):
        message = _describe_registration_timeout(
            RegistrationTimeoutOutcome.CONFIRMED_PRESENT,
            agent_name="my-agent",
            agent_id="orphan-agent-1",
            timeout=30,
        )
        assert "orphan-agent-1" in message
        assert "cannot be retrieved" in message

    def test_unknown_says_could_not_be_confirmed(self):
        message = _describe_registration_timeout(
            RegistrationTimeoutOutcome.UNKNOWN,
            agent_name="my-agent",
            agent_id=None,
            timeout=30,
        )
        assert "could not be confirmed" in message


# --- band.yaml read/write ---


class TestAgentIdRoundTrip:
    def test_read_placeholder(self, tmp_path: Path):
        workspace = _placeholder_workspace(tmp_path)
        assert read_agent_id(workspace.root) == PLACEHOLDER_AGENT_ID

    def test_write_updates_id_and_preserves_comments(self, tmp_path: Path):
        workspace = _placeholder_workspace(tmp_path)
        workspace.config_path.write_text(
            "# a hand-written comment\n"
            + workspace.config_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        write_agent_id(workspace.root, "agent-abc-123")

        text = workspace.config_path.read_text(encoding="utf-8")
        assert "# a hand-written comment" in text
        assert read_agent_id(workspace.root) == "agent-abc-123"

    def test_read_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="no band.yaml"):
            read_agent_id(tmp_path)

    def test_write_on_empty_file_raises_clear_error(self, tmp_path: Path):
        (tmp_path / "band.yaml").write_text("", encoding="utf-8")

        with pytest.raises(LaunchError, match="invalid"):
            write_agent_id(tmp_path, "agent-abc-123")


# --- sbx secret ls table parsing ---


class TestTableTargets:
    def test_empty_when_no_secrets(self):
        assert _table_targets(_empty_ls_output("my-agent")) == set()

    def test_finds_target_host(self):
        targets = _table_targets(_ls_table(scope="my-agent", host="**.band.ai"))
        assert "**.band.ai" in targets

    def test_garbage_output_is_empty(self):
        assert _table_targets("not a table\njust noise\n") == set()


class TestSandboxHasBandSecret:
    def test_true_when_present(self, monkeypatch):
        fake = FakeSbx(ls_output=_ls_table(scope="my-agent", host="**.band.ai"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake)
        assert sandbox_has_band_secret("my-agent", "**.band.ai") is True

    def test_false_when_absent(self, monkeypatch):
        fake = FakeSbx(ls_output=_empty_ls_output("my-agent"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake)
        assert sandbox_has_band_secret("my-agent", "**.band.ai") is False

    def test_raises_on_sbx_failure(self, monkeypatch):
        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="daemon down")

        monkeypatch.setattr("band.docker.provision.subprocess.run", failing)
        with pytest.raises(RuntimeError, match="daemon down"):
            sandbox_has_band_secret("my-agent", "**.band.ai")


class TestSandboxExists:
    def test_true_when_present(self, monkeypatch):
        fake = FakeSbx(sandbox_ls_json='{"sandboxes": [{"name": "my-agent"}]}')
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake)
        assert sandbox_exists("my-agent") is True

    def test_false_when_absent(self, monkeypatch):
        fake = FakeSbx(sandbox_ls_json='{"sandboxes": []}')
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake)
        assert sandbox_exists("my-agent") is False

    def test_raises_on_sbx_failure(self, monkeypatch):
        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="daemon down")

        monkeypatch.setattr("band.docker.provision.subprocess.run", failing)
        with pytest.raises(RuntimeError, match="daemon down"):
            sandbox_exists("my-agent")


# --- inject_agent_key: key delivery ---


class TestInjectAgentKey:
    def test_key_never_on_argv(self, monkeypatch):
        fake = FakeSbx()
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake)

        inject_agent_key(
            name="my-agent", host="**.band.ai", agent_key="super-secret-key"
        )

        [call] = fake.calls
        assert "super-secret-key" not in call["argv"]
        assert not any("super-secret-key" in str(arg) for arg in call["argv"])

    def test_key_delivered_via_stdin(self, monkeypatch):
        fake = FakeSbx()
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake)

        inject_agent_key(
            name="my-agent", host="**.band.ai", agent_key="super-secret-key"
        )

        assert fake.calls[0]["input"] == "super-secret-key"

    def test_placeholder_is_the_sentinel(self, monkeypatch):
        fake = FakeSbx()
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake)

        inject_agent_key(name="my-agent", host="**.band.ai", agent_key="k")

        argv = fake.calls[0]["argv"]
        assert argv[argv.index("--placeholder") + 1] == "proxy-managed"

    def test_failure_redacts_key_from_error(self, monkeypatch):
        fake = FakeSbx(
            set_custom_returncode=1, set_custom_stderr="failed near super-secret-key"
        )
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake)

        with pytest.raises(RuntimeError) as exc_info:
            inject_agent_key(
                name="my-agent", host="**.band.ai", agent_key="super-secret-key"
            )

        assert "super-secret-key" not in str(exc_info.value)


class TestCheckRegistrationAfterTimeout:
    """Direct coverage of the post-timeout lookup -- no sleep race needed,
    since this calls the checker itself rather than inducing a real
    asyncio.wait_for timeout through run()."""

    @staticmethod
    def _mock_client(**list_my_agents_kwargs):
        mock_client = AsyncMock()
        mock_client._client_wrapper.httpx_client.httpx_client.aclose = AsyncMock()
        mock_client.human_api_agents.list_my_agents.configure_mock(
            **list_my_agents_kwargs
        )
        return mock_client

    @pytest.mark.asyncio
    async def test_confirmed_absent_when_no_match(self):
        mock_client = self._mock_client(return_value=_agents_response())
        with patch("band.docker.provision.AsyncRestClient", return_value=mock_client):
            outcome, agent_id = await _check_registration_after_timeout(
                api_key="k", rest_url="https://x", agent_name="my-agent"
            )

        assert outcome is RegistrationTimeoutOutcome.CONFIRMED_ABSENT
        assert agent_id is None
        assert (
            mock_client.human_api_agents.list_my_agents.call_args.kwargs["name"]
            == "my-agent"
        )

    @pytest.mark.asyncio
    async def test_confirmed_present_when_name_matches(self):
        mock_client = self._mock_client(
            return_value=_agents_response(
                SimpleNamespace(name="my-agent", id="orphan-agent-1")
            )
        )
        with patch("band.docker.provision.AsyncRestClient", return_value=mock_client):
            outcome, agent_id = await _check_registration_after_timeout(
                api_key="k", rest_url="https://x", agent_name="my-agent"
            )

        assert outcome is RegistrationTimeoutOutcome.CONFIRMED_PRESENT
        assert agent_id == "orphan-agent-1"

    @pytest.mark.asyncio
    async def test_unknown_when_lookup_itself_fails(self):
        mock_client = self._mock_client(side_effect=RuntimeError("network down"))
        with patch("band.docker.provision.AsyncRestClient", return_value=mock_client):
            outcome, agent_id = await _check_registration_after_timeout(
                api_key="k", rest_url="https://x", agent_name="my-agent"
            )

        assert outcome is RegistrationTimeoutOutcome.UNKNOWN
        assert agent_id is None


# --- run() orchestration ---


class TestRun:
    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("BAND_API_KEY_USER", raising=False)
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root, api_key=None)
        with pytest.raises(ValueError, match="register_only-scoped"):
            await run(args)

    @pytest.mark.asyncio
    async def test_create_without_kit_raises(self, tmp_path: Path):
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root, create=True, kit=None)
        with pytest.raises(ValueError, match="--kit is required"):
            await run(args)

    @pytest.mark.asyncio
    async def test_happy_path_registers_writes_and_injects(self, tmp_path, monkeypatch):
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root)
        fake_sbx = FakeSbx(ls_output=_empty_ls_output("my-agent"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            mock_client = _make_mock_client(
                _register_response(agent_id="agent-42", api_key="minted-key")
            )
            MockClient.return_value = mock_client

            agent_id = await run(args)

        assert agent_id == "agent-42"
        assert read_agent_id(workspace.root) == "agent-42"
        set_custom_calls = [
            c for c in fake_sbx.calls if c["argv"][1:3] == ["secret", "set-custom"]
        ]
        assert len(set_custom_calls) == 1
        assert set_custom_calls[0]["input"] == "minted-key"
        # --no-create by default in _make_args: sbx create must not run.
        assert not any(c["argv"][1] == "create" for c in fake_sbx.calls)

    @pytest.mark.asyncio
    async def test_create_flag_runs_sbx_create(self, tmp_path, monkeypatch):
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root, create=True, kit="oci://example/kit:latest")
        fake_sbx = FakeSbx(ls_output=_empty_ls_output("my-agent"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            MockClient.return_value = _make_mock_client()
            await run(args)

        create_calls = [c for c in fake_sbx.calls if c["argv"][1] == "create"]
        assert len(create_calls) == 1
        assert "oci://example/kit:latest" in create_calls[0]["argv"]

    @pytest.mark.asyncio
    async def test_registration_uses_zero_retries(self, tmp_path, monkeypatch):
        """Registration mints a key shown exactly once and has no idempotency
        key -- a transport-level retry after the platform already committed
        the write would hit the duplicate-name path with the original
        response (and its key) already gone. Must never retry."""
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root)
        fake_sbx = FakeSbx(ls_output=_empty_ls_output("my-agent"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            mock_client = _make_mock_client(_register_response())
            MockClient.return_value = mock_client
            await run(args)

        call_kwargs = mock_client.human_api_agents.register_my_agent.call_args.kwargs
        assert call_kwargs["request_options"] == NO_RETRY_REQUEST_OPTIONS

    @pytest.mark.asyncio
    async def test_create_after_already_registered_creates_missing_sandbox(
        self, tmp_path, monkeypatch
    ):
        """Registration and secret injection succeeded on a prior run, but
        `sbx create` itself failed transiently. Re-running with --create must
        still create the sandbox: a saved agent.id + secret only proves the
        agent was registered, not that the sandbox exists."""
        workspace = make_workspace(tmp_path)
        config = default_config(workspace)
        config["agent"]["id"] = "already-registered-agent"
        write_config(workspace, config)

        args = _make_args(
            workspace.root, name="my-agent", create=True, kit="oci://example/kit:latest"
        )
        fake_sbx = FakeSbx(
            ls_output=_ls_table(scope="my-agent", host="**.band.ai"),
            sandbox_ls_json='{"sandboxes": []}',
        )
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            agent_id = await run(args)
            MockClient.assert_not_called()

        assert agent_id == "already-registered-agent"
        create_calls = [c for c in fake_sbx.calls if c["argv"][1] == "create"]
        assert len(create_calls) == 1

    @pytest.mark.asyncio
    async def test_create_after_already_registered_skips_when_sandbox_exists(
        self, tmp_path, monkeypatch
    ):
        workspace = make_workspace(tmp_path)
        config = default_config(workspace)
        config["agent"]["id"] = "already-registered-agent"
        write_config(workspace, config)

        args = _make_args(
            workspace.root, name="my-agent", create=True, kit="oci://example/kit:latest"
        )
        fake_sbx = FakeSbx(
            ls_output=_ls_table(scope="my-agent", host="**.band.ai"),
            sandbox_ls_json='{"sandboxes": [{"name": "my-agent"}]}',
        )
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            agent_id = await run(args)
            MockClient.assert_not_called()

        assert agent_id == "already-registered-agent"
        assert not any(c["argv"][1] == "create" for c in fake_sbx.calls)

    @pytest.mark.asyncio
    async def test_refuses_when_name_collides_with_unclaimed_secret(
        self, tmp_path, monkeypatch
    ):
        """`sbx secret set-custom` is create-or-update: a secret already
        present under --name with no matching local agent.id means --name
        collides with an unrelated sandbox. Registering here would silently
        overwrite that sandbox's live Band credential -- must refuse instead."""
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root, name="someone-elses-sandbox")
        fake_sbx = FakeSbx(
            ls_output=_ls_table(scope="someone-elses-sandbox", host="**.band.ai")
        )
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            with pytest.raises(RuntimeError, match="collides|overwrite"):
                await run(args)
            MockClient.assert_not_called()

        # Refused before any mutation: no registration, no secret write.
        assert not any(
            c["argv"][1:3] == ["secret", "set-custom"] for c in fake_sbx.calls
        )
        assert read_agent_id(workspace.root) == PLACEHOLDER_AGENT_ID

    @pytest.mark.asyncio
    async def test_idempotent_skip_when_already_provisioned(
        self, tmp_path, monkeypatch
    ):
        workspace = make_workspace(tmp_path)
        config = default_config(workspace)
        config["agent"]["id"] = "already-registered-agent"
        write_config(workspace, config)

        args = _make_args(workspace.root, name="my-agent")
        fake_sbx = FakeSbx(_ls_table(scope="my-agent", host="**.band.ai"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            agent_id = await run(args)
            MockClient.assert_not_called()

        assert agent_id == "already-registered-agent"
        # No secret/create calls either -- only the read-only `secret ls` probe.
        assert all(c["argv"][1:3] == ["secret", "ls"] for c in fake_sbx.calls)

    @pytest.mark.asyncio
    async def test_reregisters_when_secret_missing_despite_saved_id(
        self, tmp_path, monkeypatch
    ):
        workspace = make_workspace(tmp_path)
        config = default_config(workspace)
        config["agent"]["id"] = "stale-agent-id"
        write_config(workspace, config)

        args = _make_args(workspace.root, name="my-agent")
        fake_sbx = FakeSbx(ls_output=_empty_ls_output("my-agent"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            mock_client = _make_mock_client(
                _register_response(agent_id="fresh-agent-id")
            )
            MockClient.return_value = mock_client
            agent_id = await run(args)

        assert agent_id == "fresh-agent-id"
        mock_client.human_api_agents.register_my_agent.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status_code, expected_match",
        [(403, "cap reached"), (422, "not decided yet")],
        ids=["403-plan-cap", "422-duplicate-name"],
    )
    async def test_register_error_writes_nothing(
        self, tmp_path, monkeypatch, status_code, expected_match
    ):
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root)
        fake_sbx = FakeSbx(ls_output=_empty_ls_output("my-agent"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            MockClient.return_value = _make_mock_client(
                register_side_effect=ApiError(status_code=status_code, body=None)
            )
            with pytest.raises(RuntimeError, match=expected_match):
                await run(args)

        assert read_agent_id(workspace.root) == PLACEHOLDER_AGENT_ID
        assert not any(
            c["argv"][1:3] == ["secret", "set-custom"] for c in fake_sbx.calls
        )

    @pytest.mark.asyncio
    async def test_timeout_is_disambiguated_end_to_end(self, tmp_path, monkeypatch):
        """Wiring proof: run() actually routes a registration timeout through
        _check_registration_after_timeout + _describe_registration_timeout.
        Each piece's own behavior (the three possible outcomes, and the
        message each produces) is covered directly and cheaply by
        TestCheckRegistrationAfterTimeout / TestDescribeRegistrationTimeout
        below -- this is the one test proving they're actually connected."""
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root, timeout=0.01)
        fake_sbx = FakeSbx(ls_output=_empty_ls_output("my-agent"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        async def _slow_register_agent(**kwargs):
            await asyncio.sleep(10)

        monkeypatch.setattr(
            "band.docker.provision.register_agent", _slow_register_agent
        )

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            mock_client = AsyncMock()
            mock_client._client_wrapper.httpx_client.httpx_client.aclose = AsyncMock()
            mock_client.human_api_agents.list_my_agents.return_value = (
                _agents_response()
            )
            MockClient.return_value = mock_client

            with pytest.raises(RuntimeError, match="safe to retry"):
                await run(args)

        assert (
            mock_client.human_api_agents.list_my_agents.call_args.kwargs["name"]
            == "my-agent"
        )

    @pytest.mark.asyncio
    async def test_key_never_appears_in_logs(self, tmp_path, monkeypatch, caplog):
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root)
        fake_sbx = FakeSbx(ls_output=_empty_ls_output("my-agent"))
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with (
            caplog.at_level("DEBUG"),
            patch("band.docker.provision.AsyncRestClient") as MockClient,
        ):
            MockClient.return_value = _make_mock_client(
                _register_response(agent_id="agent-1", api_key="do-not-log-me")
            )
            await run(args)

        assert "do-not-log-me" not in caplog.text

    @pytest.mark.asyncio
    async def test_key_persist_failure_prints_recovery_banner(
        self, tmp_path, monkeypatch, capsys
    ):
        workspace = _placeholder_workspace(tmp_path)
        args = _make_args(workspace.root)
        fake_sbx = FakeSbx(
            ls_output=_empty_ls_output("my-agent"),
            set_custom_returncode=1,
            set_custom_stderr="daemon unreachable",
        )
        monkeypatch.setattr("band.docker.provision.subprocess.run", fake_sbx)

        with patch("band.docker.provision.AsyncRestClient") as MockClient:
            MockClient.return_value = _make_mock_client(
                _register_response(agent_id="agent-1", api_key="unrecoverable-key")
            )
            with pytest.raises(RuntimeError, match="daemon unreachable"):
                await run(args)

        assert "unrecoverable-key" in capsys.readouterr().err


# --- main() ---


_MINIMAL_PROVISION_ARGV = [
    "band-kit",
    "provision",
    "--name",
    "a",
    "--description",
    "d",
    "--workspace",
    ".",
    "--no-create",
]


class TestMain:
    def test_exits_0_on_success(self, monkeypatch):
        monkeypatch.setattr("sys.argv", _MINIMAL_PROVISION_ARGV)
        with (
            patch(
                "band.docker.provision.asyncio.run",
                side_effect=_fake_asyncio_run(return_value="agent-ok"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0

    def test_writes_agent_id_to_stdout_only(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", _MINIMAL_PROVISION_ARGV)
        with (
            patch(
                "band.docker.provision.asyncio.run",
                side_effect=_fake_asyncio_run(return_value="agent-xyz"),
            ),
            pytest.raises(SystemExit),
        ):
            main()
        assert capsys.readouterr().out.strip() == "agent-xyz"

    def test_exits_1_on_value_error(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", _MINIMAL_PROVISION_ARGV)
        with (
            patch(
                "band.docker.provision.asyncio.run",
                side_effect=_fake_asyncio_run(
                    side_effect=ValueError("register_only-scoped user API key")
                ),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 1
        assert "register_only-scoped" in capsys.readouterr().err
