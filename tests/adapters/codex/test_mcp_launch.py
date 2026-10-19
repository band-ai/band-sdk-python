"""Consumer-visible snapshot, launch and diagnostic safety for Codex MCP."""

from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import tomllib
import traceback
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from pydantic import TypeAdapter

from band.integrations.codex import CodexStdioClient
from band.integrations.codex import mcp as codex_mcp
from band.integrations.codex.mcp import (
    CodexMcpHttpServer,
    CodexMcpLaunch,
    CodexMcpRedactor,
    CodexMcpServer,
    CodexMcpStartupError,
    CodexMcpStdioServer,
    freeze_mcp_servers,
)


def launched_servers(
    launch: CodexMcpLaunch, original_command: Sequence[str]
) -> dict[str, Any]:
    tail = launch.command[len(original_command) :]
    assert len(tail) % 2 == 0
    assert all(flag == "-c" for flag in tail[::2])
    parsed = tomllib.loads("\n".join(tail[1::2]))
    return parsed.get("mcp_servers", {})


def run_stdio_entry(entry: dict[str, Any], launch: CodexMcpLaunch) -> dict[str, Any]:
    assert launch.env is not None
    inherited_names = {"SYSTEMROOT", "WINDIR", "PATH", "HOME", "TEMP", "TMP", "TMPDIR"}
    child_env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in inherited_names
    }
    child_env.update({name: launch.env[name] for name in entry["env_vars"]})
    completed = subprocess.run(
        [entry["command"], *entry["args"]],
        cwd=entry["cwd"],
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


class TestCodexMcpLaunch:
    def test_caller_mutation_cannot_change_snapshot_or_launch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            codex_mcp, "os", SimpleNamespace(name="posix", environ=os.environ)
        )
        args = ["--mode", "original"]
        env = {"TOKEN": "stdio-original-grant"}
        headers = {"X-Grant": "http-original-grant"}
        stdio = CodexMcpStdioServer("relative-runner", args=args, env=env)
        http = CodexMcpHttpServer("http://localhost/mcp", headers=headers)
        servers: dict[str, CodexMcpServer] = {"stdio": stdio, "http": http}
        snapshot = freeze_mcp_servers(servers)
        command = ["codex", "app-server", "--listen", "stdio://"]
        base_env = {"EXISTING": "existing-value"}
        launch = snapshot.launch(command, base_env)

        args.append("mutated")
        env["TOKEN"] = "stdio-replacement-grant"
        headers["X-Grant"] = "http-replacement-grant"
        servers.clear()
        command.append("--changed")
        base_env["EXISTING"] = "changed"

        assert snapshot.expected_names == frozenset({"stdio", "http"})
        assert snapshot.servers["stdio"].args == ("--mode", "original")
        entries = launched_servers(launch, command[:-1])
        assert "mutated" not in entries["stdio"]["args"]
        assert launch.env is not None
        assert launch.env["EXISTING"] == "existing-value"
        assert launch.env[entries["http"]["env_http_headers"]["X-Grant"]] == (
            "http-original-grant"
        )
        assert "stdio-original-grant" in launch.env.values()
        assert "stdio-replacement-grant" not in launch.env.values()
        assert launch.command[:4] == ("codex", "app-server", "--listen", "stdio://")
        assert snapshot.redactor.text("stdio-original-grant") == "[REDACTED]"

        with pytest.raises(TypeError):
            snapshot.servers["new"] = http
        with pytest.raises(TypeError):
            stdio.env["TOKEN"] = "changed"
        with pytest.raises(TypeError):
            http.headers["X-Grant"] = "changed"
        with pytest.raises(FrozenInstanceError):
            stdio.command = "changed"
        with pytest.raises(FrozenInstanceError):
            http.url = "changed"
        with pytest.raises(TypeError):
            launch.env["EXISTING"] = "changed"

    def test_empty_snapshot_does_not_add_overrides_or_child_env(self) -> None:
        command = ["codex", "app-server", "--listen", "stdio://"]
        snapshot = freeze_mcp_servers({})
        launch = snapshot.launch(command, None)
        assert launch.command == tuple(command)
        assert launch.env is None
        assert snapshot.expected_names == frozenset()
        assert snapshot.redactor.text("unregistered value") == "unregistered value"

        base_env = {"BASE": "base-secret"}
        launch_with_env = snapshot.launch(command, base_env)
        base_env.clear()
        assert launch_with_env.env == {"BASE": "base-secret"}
        assert "base-secret" not in repr(launch_with_env)

    @pytest.mark.parametrize(
        "unsupported",
        [
            SimpleNamespace(transport="sse", url="secret-url"),
            {"url": "secret-url", "transport": "http"},
            type("secret-callback-type", (), {})(),
            type("HttpSubclass", (CodexMcpHttpServer,), {})("http://localhost/mcp"),
        ],
        ids=["sse", "untyped-mapping", "unknown-type", "descriptor-subclass"],
    )
    def test_unsupported_descriptors_have_named_secret_free_errors(
        self, unsupported: Any
    ) -> None:
        with pytest.raises(CodexMcpStartupError) as caught:
            freeze_mcp_servers({"tools": unsupported})
        assert caught.value.stage == "unsupported-transport"
        assert "unsupported transport" in str(caught.value).lower()
        assert "secret-url" not in str(caught.value)
        assert "secret-callback-type" not in repr(caught.value)

    def test_a_callback_type_cannot_spoof_supported_descriptor_identity(self) -> None:
        class PretendType(type):
            def __eq__(self, other: object) -> bool:
                return True

        class PretendDescriptor(metaclass=PretendType):
            pass

        with pytest.raises(CodexMcpStartupError) as caught:
            freeze_mcp_servers({"tools": PretendDescriptor()})
        assert caught.value.stage == "unsupported-transport"

    @pytest.mark.parametrize(
        "name", ["", "with.dot", "with space", "with/slash", "café", "secret-name\n", 7]
    )
    def test_invalid_server_names_have_safe_named_errors(self, name: Any) -> None:
        with pytest.raises(CodexMcpStartupError) as caught:
            freeze_mcp_servers({name: CodexMcpHttpServer("http://localhost/mcp")})
        assert caught.value.stage == "invalid-name"
        assert "invalid server name" in str(caught.value).lower()
        assert "secret-name" not in repr(caught.value)

    def test_native_host_owned_name_is_reserved_without_invented_names(self) -> None:
        server = CodexMcpHttpServer("http://localhost/mcp")
        with pytest.raises(CodexMcpStartupError) as caught:
            freeze_mcp_servers({"codex_apps": server})
        assert caught.value.stage == "reserved-name"
        snapshot = freeze_mcp_servers(
            {name: server for name in ("band", "codex", "Codex_apps", "tools-1_v2")}
        )
        launch = snapshot.launch(("codex", "app-server"), None)
        assert set(launched_servers(launch, ("codex", "app-server"))) == {
            "band",
            "codex",
            "Codex_apps",
            "tools-1_v2",
        }

    @pytest.mark.parametrize("value", [None, [], "private-nonmapping-data"])
    def test_nonmapping_resolver_results_have_safe_snapshot_errors(
        self, value: Any
    ) -> None:
        with pytest.raises(CodexMcpStartupError) as caught:
            freeze_mcp_servers(value)
        assert caught.value.stage == "snapshot"
        assert "private-nonmapping-data" not in repr(caught.value)

    @pytest.mark.parametrize(
        "make_server",
        [
            lambda: CodexMcpStdioServer("runner", args="private-invalid-args"),
            lambda: CodexMcpStdioServer("runner", env={"TOKEN": 7}),
            lambda: CodexMcpStdioServer(
                "runner", env={"BAD=NAME": "private-env-value"}
            ),
            lambda: CodexMcpHttpServer("http://localhost/mcp", bearer_token=object()),
            lambda: CodexMcpHttpServer("http://localhost/mcp", headers={"X-Grant": 7}),
        ],
        ids=[
            "string-args",
            "nonstring-env",
            "invalid-env-key",
            "nonstring-bearer",
            "nonstring-header",
        ],
    )
    def test_invalid_descriptor_data_is_rejected_without_input_diagnostics(
        self, make_server: Any
    ) -> None:
        with pytest.raises(CodexMcpStartupError) as caught:
            make_server()
        assert caught.value.stage == "invalid-descriptor"
        assert "private-invalid-args" not in repr(caught.value)
        assert "private-env-value" not in repr(caught.value)

    @pytest.mark.parametrize(
        "url",
        [
            "https://url-only-grant@mcp.example.test/mcp",
            "https://user:url-only-grant@mcp.example.test/mcp",
            "https://user%3Aurl-only-grant@mcp.example.test/mcp",
            "https://mcp.example.test/mcp?api_key=url-only-grant",
            "https://mcp.example.test/mcp?opaque=url-only-grant",
            "https://mcp.example.test/mcp?%61pi_key=url-only-grant",
            "https://mcp.example.test/mcp#url-only-grant",
            "https://@mcp.example.test/mcp",
            "https://mcp.example.test/mcp?",
            "https://mcp.example.test/mcp#",
            "https://url-only-grant／mcp.example.test/mcp",
            "https://[url-only-grant/mcp",
        ],
        ids=[
            "userinfo-token",
            "userinfo-password",
            "encoded-userinfo",
            "api-key-query",
            "unknown-query",
            "encoded-query-key",
            "fragment",
            "empty-userinfo",
            "empty-query",
            "empty-fragment",
            "parser-normalization-error",
            "parser-bracket-error",
        ],
    )
    @pytest.mark.parametrize(
        "entry_point", ["constructor", "pydantic-python", "pydantic-json"]
    )
    def test_credential_urls_are_refused_before_public_descriptors_exist(
        self, url: str, entry_point: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with pytest.raises(CodexMcpStartupError) as caught:
            match entry_point:
                case "constructor":
                    CodexMcpHttpServer(url)
                case "pydantic-python":
                    TypeAdapter(CodexMcpHttpServer).validate_python({"url": url})
                case "pydantic-json":
                    TypeAdapter(CodexMcpHttpServer).validate_json(
                        json.dumps({"url": url})
                    )
        assert caught.value.stage == "invalid-descriptor"
        diagnostics = (
            str(caught.value)
            + repr(caught.value)
            + "".join(traceback.format_exception(caught.value))
            + caplog.text
        )
        assert url not in diagnostics
        assert "url-only-grant" not in diagnostics

    @pytest.mark.parametrize(
        "url",
        [
            "https://url-only-grant@mcp.example.test/mcp",
            "https://mcp.example.test/mcp?api_key=url-only-grant",
            "https://mcp.example.test/mcp#url-only-grant",
        ],
        ids=["userinfo", "query", "fragment"],
    )
    def test_snapshot_revalidates_url_before_building_native_overrides(
        self, url: str
    ) -> None:
        server = CodexMcpHttpServer("https://mcp.example.test/mcp")
        object.__setattr__(server, "url", url)
        with pytest.raises(CodexMcpStartupError) as caught:
            freeze_mcp_servers({"remote": server}).launch(("codex", "app-server"))
        assert caught.value.stage == "invalid-descriptor"
        diagnostics = "".join(traceback.format_exception(caught.value))
        assert "url-only-grant" not in diagnostics

    @pytest.mark.parametrize(
        "name",
        [
            "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
            "node_repl_auth_token",
            "CODEX_GUARDIAN_DECISIONS_API_KEY",
            "OPENAI_FEDERATION_RULE_ID",
            "OPENAI_IDENTITY_TOKEN_FILE",
            "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
        ],
    )
    def test_stdio_alias_remapping_cannot_restore_native_noninheritable_env(
        self, name: str
    ) -> None:
        with pytest.raises(CodexMcpStartupError) as caught:
            CodexMcpStdioServer("runner", env={name: "private-native-launch-context"})
        assert caught.value.stage == "invalid-descriptor"
        assert "private-native-launch-context" not in repr(caught.value)

    def test_duplicate_mapping_entries_are_not_silently_overwritten(self) -> None:
        class RepeatedNames(Mapping[str, CodexMcpServer]):
            def __iter__(self) -> Iterator[str]:
                return iter(("repeated", "repeated"))

            def __len__(self) -> int:
                return 2

            def __getitem__(self, key: str) -> CodexMcpServer:
                return CodexMcpHttpServer("http://localhost/mcp")

        with pytest.raises(CodexMcpStartupError) as caught:
            freeze_mcp_servers(RepeatedNames())
        assert caught.value.stage == "duplicate-name"
        assert "duplicate server name" in str(caught.value).lower()

    @pytest.mark.parametrize("failure", ["iteration", "lookup", "malformed-items"])
    def test_caller_mapping_failures_are_normalized_before_validation(
        self, failure: str
    ) -> None:
        secret = "private-callback-error"

        class BrokenMapping(Mapping[str, CodexMcpServer]):
            def __iter__(self) -> Iterator[str]:
                if failure == "iteration":
                    raise RuntimeError(secret)
                return iter(("tools",))

            def __len__(self) -> int:
                return 1

            def __getitem__(self, key: str) -> CodexMcpServer:
                raise ValueError(secret)

            def items(self) -> Any:
                if failure == "malformed-items":
                    return [
                        ("tools", CodexMcpHttpServer("http://localhost/mcp"), secret)
                    ]
                return super().items()

        with pytest.raises(CodexMcpStartupError) as caught:
            freeze_mcp_servers(BrokenMapping())
        assert caught.value.stage == "snapshot"
        rendered = "".join(traceback.format_exception(caught.value))
        assert secret not in str(caught.value)
        assert secret not in repr(caught.value)
        assert secret not in rendered

    @pytest.mark.parametrize(
        "command",
        [
            ["codex", "app-server", "--listen", "stdio://", "-c", 'model="base"'],
            [
                "python",
                "-m",
                "band_agent",
                "codex-relay",
                "--skills-root",
                "skills",
                "--",
                "codex",
                "app-server",
                "--listen",
                "stdio://",
                "--extra-arg",
            ],
        ],
        ids=["default-command-extra-config", "relay-boundary-extra-args"],
    )
    def test_native_overrides_follow_existing_default_or_relay_argv(
        self, command: list[str]
    ) -> None:
        original = command.copy()
        snapshot = freeze_mcp_servers(
            {"tools": CodexMcpHttpServer("http://localhost/mcp")}
        )
        launch = snapshot.launch(command, None)
        assert command == original
        assert launch.command[: len(original)] == tuple(original)
        assert launched_servers(launch, original)["tools"] == {
            "url": "http://localhost/mcp",
            "env_http_headers": {},
            "enabled": True,
            "required": True,
            "startup_readiness": "connection",
        }

    def test_toml_preserves_nonsecret_strings_without_an_unneeded_trampoline(
        self,
    ) -> None:
        args = ('quote" and backslash\\', "line\n\tbreak", "emoji-🦉", "delete-\x7f")
        server = CodexMcpStdioServer("./relative-runner", args=args, cwd="relative dir")
        command = ("codex", "app-server")
        launch = freeze_mcp_servers({"tools": server}).launch(command, None)
        entry = launched_servers(launch, command)["tools"]
        assert entry["command"] == "./relative-runner"
        assert entry["args"] == list(args)
        assert entry["cwd"] == "relative dir"
        assert entry["env_vars"] == []
        assert entry["required"] is True
        assert entry["startup_readiness"] == "connection"
        assert launch.env is None

    def test_secrets_only_exist_in_the_private_child_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            codex_mcp, "os", SimpleNamespace(name="posix", environ=os.environ)
        )
        bearer = 'bearer-secret-"\\-🦉'
        header = "header-secret-with\nnewline"
        stdio_value = "stdio-secret-value"
        servers = {
            "remote": CodexMcpHttpServer(
                "http://localhost/mcp", bearer_token=bearer, headers={"X.Grant": header}
            ),
            "local": CodexMcpStdioServer("runner", env={"TOKEN": stdio_value}),
        }
        command = ["codex", "app-server"]
        base_env = {"BASE": "base-secret-value"}
        process_env_before = dict(os.environ)
        snapshot = freeze_mcp_servers(servers)
        launch = snapshot.launch(command, base_env)
        entries = launched_servers(launch, command)
        assert launch.env is not None
        remote = entries["remote"]
        assert launch.env[remote["bearer_token_env_var"]] == bearer
        assert launch.env[remote["env_http_headers"]["X.Grant"]] == header
        assert [launch.env[name] for name in entries["local"]["env_vars"]] == [
            stdio_value
        ]
        assert "TOKEN" not in launch.env
        assert "bearer_token" not in remote
        assert "http_headers" not in remote
        assert "env" not in entries["local"]
        assert dict(os.environ) == process_env_before
        assert base_env == {"BASE": "base-secret-value"}

        diagnostic = repr((snapshot, snapshot.redactor, launch, *servers.values()))
        for secret in (bearer, header, stdio_value, "base-secret-value"):
            assert secret not in " ".join(launch.command)
            assert secret not in diagnostic
            assert snapshot.redactor.text(secret) == "[REDACTED]"

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost/mcp",
            "http://127.0.0.1:8765/mcp",
            "http://[::1]:8765/mcp",
            "https://mcp.example.test",
            "HTTPS://mcp.example.test/public%20endpoint",
            "https://mcp.example.test/public@path",
        ],
    )
    def test_public_endpoints_keep_protected_auth_out_of_real_child_argv(
        self, url: str
    ) -> None:
        bearer = "protected-bearer-grant"
        header = "protected-header-grant"
        server = CodexMcpHttpServer(
            url, bearer_token=bearer, headers={"X-Grant": header}
        )
        snapshot = freeze_mcp_servers({"remote": server})
        probe = (
            "import json,os,sys,tomllib; "
            "entry=tomllib.loads('\\n'.join(sys.argv[2::2]))['mcp_servers']['remote']; "
            "refs=[entry['bearer_token_env_var'],*entry['env_http_headers'].values()]; "
            "print(json.dumps({'argv':sys.argv[1:],"
            "'grants':[os.environ[name] for name in refs]}))"
        )
        command = (sys.executable, "-I", "-c", probe)
        launch = snapshot.launch(command)
        assert launch.env is not None
        completed = subprocess.run(
            launch.command,
            env={**os.environ, **launch.env},
            capture_output=True,
            text=True,
            check=True,
        )
        observed = json.loads(completed.stdout)
        assert observed["argv"] == list(launch.command[len(command) :])
        assert observed["grants"] == [bearer, header]
        assert launched_servers(launch, command)["remote"]["url"] == url
        assert TypeAdapter(CodexMcpHttpServer).dump_python(server) == {"url": url}
        diagnostics = (
            repr((server, snapshot, launch))
            + TypeAdapter(CodexMcpHttpServer).dump_json(server).decode()
            + TypeAdapter(CodexMcpLaunch).dump_json(launch).decode()
            + snapshot.redactor.text(f"failed {bearer} {header}")
        )
        for secret in (bearer, header):
            assert secret not in " ".join(observed["argv"])
            assert secret not in diagnostics

    def test_descriptor_equality_and_serialization_do_not_use_secret_fields(
        self,
    ) -> None:
        left = CodexMcpHttpServer(
            "http://localhost/mcp",
            bearer_token="first-secret",
            headers={"X-Grant": "one"},
        )
        right = CodexMcpHttpServer(
            "http://localhost/mcp",
            bearer_token="second-secret",
            headers={"X-Grant": "two"},
        )
        stdio = CodexMcpStdioServer("runner", env={"TOKEN": "stdio-secret"})
        assert left == right
        assert hash(left) == hash(right)
        assert stdio == CodexMcpStdioServer("runner", env={"TOKEN": "other-secret"})
        assert TypeAdapter(CodexMcpHttpServer).dump_python(left) == {
            "url": "http://localhost/mcp"
        }
        assert TypeAdapter(CodexMcpStdioServer).dump_python(stdio) == {
            "command": "runner",
            "args": (),
            "cwd": None,
        }
        with pytest.raises(TypeError):
            asdict(left)
        with pytest.raises(TypeError):
            asdict(stdio)
        launch = freeze_mcp_servers({"remote": left}).launch(
            ("codex", "app-server"), None
        )
        assert (
            "first-secret" not in TypeAdapter(CodexMcpLaunch).dump_json(launch).decode()
        )
        for runtime in (left, stdio, launch, CodexMcpRedactor(("first-secret",))):
            with pytest.raises(TypeError) as caught:
                pickle.dumps(runtime)
            assert "first-secret" not in repr(caught.value)

    def test_windows_allows_env_free_stdio_and_secret_http(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            codex_mcp, "os", SimpleNamespace(name="nt", environ=os.environ)
        )
        probe = (
            "import json,os,sys; "
            "print(json.dumps({'argv':sys.argv[1:], 'env_names':list(os.environ)}))"
        )
        command = ("codex", "app-server")
        snapshot = freeze_mcp_servers(
            {
                "local": CodexMcpStdioServer(
                    sys.executable,
                    args=("-I", "-c", probe, "literal argument"),
                    cwd=str(tmp_path),
                ),
                "remote": CodexMcpHttpServer(
                    "http://localhost/mcp",
                    bearer_token="private-http-platform-grant",
                    headers={"X-Grant": "private-http-header-grant"},
                ),
            }
        )
        launch = snapshot.launch(command, None)
        entries = launched_servers(launch, command)
        assert set(entries) == {"local", "remote"}
        observed = run_stdio_entry(entries["local"], launch)
        assert observed["argv"] == ["literal argument"]
        assert "TOKEN" not in observed["env_names"]
        assert not any(
            name.startswith("BAND_CODEX_MCP_") for name in observed["env_names"]
        )

    @pytest.mark.skipif(
        os.name != "posix",
        reason="secret-bearing stdio alias ownership requires POSIX exec",
    )
    def test_stdio_env_aliases_deliver_distinct_same_named_grants_to_relative_targets(
        self,
    ) -> None:
        target = str(Path(".") / Path(sys.executable).name)
        if not target.startswith("."):
            target = os.path.join(".", target)
        cwd = str(Path(sys.executable).parent)
        probe = (
            "import json,os,sys; "
            "print(json.dumps({'token':os.environ['TOKEN'], 'argv':sys.argv[1:], "
            "'cwd':os.getcwd(), 'env_names':list(os.environ)}))"
        )
        command = ("codex", "app-server")
        snapshot = freeze_mcp_servers(
            {
                "first": CodexMcpStdioServer(
                    target,
                    args=("-I", "-c", probe, "literal argument"),
                    env={"TOKEN": "first-server-grant"},
                    cwd=cwd,
                ),
                "second": CodexMcpStdioServer(
                    target,
                    args=("-I", "-c", probe, "literal argument"),
                    env={"TOKEN": "second-server-grant"},
                    cwd=cwd,
                ),
            }
        )
        launch = snapshot.launch(command, None)
        entries = launched_servers(launch, command)
        assert set(entries["first"]["env_vars"]).isdisjoint(
            entries["second"]["env_vars"]
        )
        for name, expected in (
            ("first", "first-server-grant"),
            ("second", "second-server-grant"),
        ):
            observed = run_stdio_entry(entries[name], launch)
            assert observed["token"] == expected
            assert observed["argv"] == ["literal argument"]
            assert Path(observed["cwd"]).samefile(cwd)
            assert set(observed["env_names"]).isdisjoint(entries[name]["env_vars"])

    def test_fresh_launches_do_not_reuse_generated_secret_names(self) -> None:
        snapshot = freeze_mcp_servers(
            {"tools": CodexMcpHttpServer("http://localhost/mcp", bearer_token="grant")}
        )
        command = ("codex", "app-server")
        first = snapshot.launch(command, None)
        second = snapshot.launch(command, None)
        first_ref = launched_servers(first, command)["tools"]["bearer_token_env_var"]
        second_ref = launched_servers(second, command)["tools"]["bearer_token_env_var"]
        assert first_ref != second_ref
        assert first.env[first_ref] == second.env[second_ref] == "grant"

    @pytest.mark.parametrize(
        "supplied_env", [False, True], ids=["inherited", "overrides"]
    )
    def test_secret_aliases_reserve_effective_stdio_child_environment(
        self, monkeypatch: pytest.MonkeyPatch, supplied_env: bool
    ) -> None:
        namespace = UUID("00000000-0000-0000-0000-000000000005")
        inherited_name = "BAND_CODEX_MCP_00000000000000000000000000000005_0"
        override_name = "BAND_CODEX_MCP_00000000000000000000000000000005_1"
        command = ("codex", "app-server")
        snapshot = freeze_mcp_servers(
            {
                "tools": CodexMcpHttpServer(
                    "http://localhost/mcp", bearer_token="correct-server-grant"
                )
            }
        )
        with monkeypatch.context() as scoped:
            scoped.setattr("band.integrations.codex.mcp.uuid4", lambda: namespace)
            scoped.setenv(inherited_name, "inherited-value")
            scoped.setenv(override_name, "inherited-override-value")
            env = {override_name: "supplied-override-value"} if supplied_env else None
            launch = snapshot.launch(command, env)
            client = CodexStdioClient(
                command=launch.command, env=launch.env, redactor=snapshot.redactor
            )
        reference = launched_servers(launch, command)["tools"]["bearer_token_env_var"]
        assert client.env is not None
        assert client.env[inherited_name] == "inherited-value"
        assert client.env[override_name] == (
            "supplied-override-value" if supplied_env else "inherited-override-value"
        )
        assert reference not in {inherited_name, override_name}
        assert client.env[reference] == "correct-server-grant"

    @pytest.mark.parametrize(
        "stage",
        [
            "resolution",
            "snapshot",
            "launch",
            "readiness",
            "thread/start",
            "thread/resume",
        ],
    )
    def test_owned_startup_stages_have_fixed_safe_text(self, stage: str) -> None:
        error = CodexMcpStartupError(stage)
        assert error.stage == stage
        assert stage in str(error)
        assert str(error) == error.args[0]

    def test_untrusted_startup_stage_text_is_not_reflected(self) -> None:
        error = CodexMcpStartupError("secret-callback-message")
        assert error.stage == "startup"
        assert "secret-callback-message" not in str(error)
        assert "secret-callback-message" not in repr(error)

    @pytest.mark.parametrize(
        "render",
        [
            lambda value: value,
            lambda value: json.dumps(value, ensure_ascii=True)[1:-1],
            lambda value: json.dumps(value, ensure_ascii=False)[1:-1],
            lambda value: repr(value)[1:-1],
            lambda value: ascii(value)[1:-1],
            lambda value: value.encode("unicode_escape").decode(),
            lambda value: json.dumps(json.dumps(value))[1:-1],
        ],
        ids=[
            "raw",
            "json-ascii",
            "json-unicode",
            "repr",
            "ascii-repr",
            "unicode-escape",
            "nested-json",
        ],
    )
    def test_redaction_covers_raw_and_escaped_secret_forms(self, render: Any) -> None:
        secret = 'private-"quote\\slash\nline-é-🦉'
        snapshot = freeze_mcp_servers(
            {"tools": CodexMcpHttpServer("http://localhost/mcp", bearer_token=secret)}
        )
        message = f"before {render(secret)} after"
        assert snapshot.redactor.text(message) == "before [REDACTED] after"
        assert secret not in repr(snapshot.redactor)

    def test_redaction_keeps_distinct_fields_when_secret_keys_collide(self) -> None:
        redactor = CodexMcpRedactor(("first-grant", "second-grant"))
        assert redactor.value(
            {"first-grant": "first error", "second-grant": "second error"}
        ) == {
            "[REDACTED]": "first error",
            "[REDACTED]#2": "second error",
        }

    def test_redaction_preserves_nested_nonsecret_data_and_does_not_mutate_input(
        self,
    ) -> None:
        secret = "private-nested-grant"
        redactor = CodexMcpRedactor((secret,))
        payload = {
            "message": f"received {secret}",
            secret: [secret, {"nested": (secret, 42, True, None)}],
            "safe": "public explanation",
        }
        assert redactor.value(payload) == {
            "message": "received [REDACTED]",
            "[REDACTED]": ["[REDACTED]", {"nested": ("[REDACTED]", 42, True, None)}],
            "safe": "public explanation",
        }
        assert payload["message"] == f"received {secret}"
        assert payload[secret][0] == secret
        assert redactor.value(SimpleNamespace(secret=secret)) == "[REDACTED]"

    def test_redaction_handles_overlapping_values_without_rewriting_its_marker(
        self,
    ) -> None:
        redactor = CodexMcpRedactor(("grant", "grant-longer", "REDACTED"))
        assert redactor.text("grant-longer grant REDACTED") == (
            "[REDACTED] [REDACTED] [REDACTED]"
        )

    def test_redaction_scope_clears_only_its_own_secret_references(self) -> None:
        first = CodexMcpRedactor(("first-grant", ""))
        second = CodexMcpRedactor(("second-grant",))
        assert first.text("first-grant second-grant") == "[REDACTED] second-grant"
        assert second.text("first-grant second-grant") == "first-grant [REDACTED]"
        first.clear()
        first.clear()
        assert first.text("first-grant") == "first-grant"
        assert first.value({"detail": "first-grant"}) == {"detail": "first-grant"}
        assert second.text("second-grant") == "[REDACTED]"
