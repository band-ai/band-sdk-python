"""Drift checks: the kit spec ships exactly the contract that was reviewed.

Identity, attached-entrypoint launch shape, network allowlist, and credential
injection (Band only; no baked secrets). Pure file/contract checks — no Docker
daemon and no sbx CLI needed, so these
run in the ordinary unit suite. `sbx kit validate` itself is a manual step
(recorded in the kit README) because the sandbox CLI only exists on
Docker-Sandbox-capable machines.

The echo-agent starter's contract lives in test_echo_agent.py; the release
artifact checks use the pinned TCK; the supply-chain quarantine
gate's tests are in test_lock_age.py.
"""

from __future__ import annotations

import importlib
import re
import shlex
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from band.credentials import PROXY_MANAGED_API_KEY
from band.integrations.uvicorn_server import ManagedUvicornServer
from tests.docker.toolkit.sbx_cli import kit_baseline_hosts
from tests.lifecycle import running
from tests.paths import KIT_DIR

# Hosts required by every fresh starter workspace (see the kit spec's
# comments). Provider, repository, and custom-service hosts are scoped per
# sandbox by the operator rather than baked into the kit.
DEFAULT_ALLOWLIST_HOSTS = {
    "app.band.ai",
    "pypi.org",
    "files.pythonhosted.org",
}

SECRET_RECIPE_DOCS = ("README.md", "echo-agent/README.md", "spec.yaml")


def load_spec() -> dict[str, Any]:
    return yaml.safe_load((KIT_DIR / "spec.yaml").read_text(encoding="utf-8"))


def test_spec_selects_workload_frontend_without_legacy_runtime_fields() -> None:
    spec = load_spec()
    assert spec["schemaVersion"] == "3"
    assert spec["kind"] == "workload"
    assert spec["dockerfile"] == "Dockerfile"
    assert not {"name", "sandbox", "permissions", "agentInstructions"} & spec.keys()
    assert (KIT_DIR / "Dockerfile").is_file()


def test_workload_declares_only_attached_agent_capabilities() -> None:
    entries = load_spec()["capabilities"]
    assert {entry["type"] for entry in entries} == {
        "com.docker.sandbox/sbx@1",
        "com.docker.sandbox/network-policy@1",
        "com.docker.sandbox/agent-context@1",
    }
    context = next(e for e in entries if e["type"].endswith("/agent-context@1"))
    assert "band.yaml" in context["config"]["content"]


def test_launcher_module_is_importable() -> None:
    importlib.import_module("band.docker.launcher")


def test_allowlist_matches_immutable_baseline() -> None:
    assert kit_baseline_hosts(KIT_DIR) == DEFAULT_ALLOWLIST_HOSTS


def validate_documented_secret_bindings(kit: Path) -> None:
    band_bindings = 0
    for relative in SECRET_RECIPE_DOCS:
        doc = kit / relative
        source = doc.read_text(encoding="utf-8").replace("\\\n", " ")
        bindings: set[str] = set()
        cleanups: set[str] = set()
        for action, command in re.findall(
            r"sbx secret (set-custom|rm)\s+([^\n]+)", source
        ):
            arguments = shlex.split(command)
            if "--placeholder" not in arguments:
                continue
            placeholder = arguments[arguments.index("--placeholder") + 1]
            if action == "rm":
                cleanups.add(placeholder)
                continue
            bindings.add(placeholder)
            if (
                "--env" in arguments
                and arguments[arguments.index("--env") + 1] == "BAND_API_KEY"
            ):
                band_bindings += 1
                assert placeholder == PROXY_MANAGED_API_KEY, (
                    f"Incorrect Band sentinel in {doc}"
                )
        assert bindings == cleanups, f"Secret binding/cleanup mismatch in {doc}"
    assert band_bindings, "No documented Band binding found"


def test_kit_docs_placeholder_matches_the_sdk_sentinel() -> None:
    validate_documented_secret_bindings(KIT_DIR)


@pytest.mark.parametrize(
    "original,replacement,error",
    [
        (
            "--env BAND_API_KEY --placeholder proxy-managed",
            "--env BAND_API_KEY --placeholder incorrect-band-sentinel",
            "Incorrect Band sentinel",
        ),
        (
            "secret rm --placeholder proxy-managed",
            "secret rm --placeholder incorrect-band-sentinel",
            "binding/cleanup mismatch",
        ),
    ],
)
def test_docs_drift_guard_rejects_broken_band_recipe(
    tmp_path: Path,
    original: str,
    replacement: str,
    error: str,
) -> None:
    for relative in SECRET_RECIPE_DOCS:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(KIT_DIR / relative, destination)
    guide = tmp_path / "echo-agent" / "README.md"
    guide.write_text(guide.read_text().replace(original, replacement))
    with pytest.raises(AssertionError, match=error):
        validate_documented_secret_bindings(tmp_path)


def test_extracted_starter_keeps_its_local_documentation_links(tmp_path: Path) -> None:
    workspace = tmp_path / "starter"
    shutil.copytree(KIT_DIR / "echo-agent", workspace)
    guide = workspace / "README.md"
    for target in re.findall(r"\]\(([^)]+)\)", guide.read_text()):
        path = target.split("#", 1)[0]
        if path and "://" not in path:
            assert (guide.parent / path).is_file(), f"Missing starter guide: {target}"


async def test_written_anthropic_conversion_authenticates_with_custom_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guide = KIT_DIR / "echo-agent" / "README.md"
    source = re.search(r"```python notest\n(.*?)\n```", guide.read_text(), re.DOTALL)
    assert source is not None
    namespace: dict[str, Any] = {"__name__": "documented_example"}
    # Execute the trusted repository example so the documented application is tested.
    exec(compile(source.group(1), str(guide), "exec"), namespace)  # noqa: S102
    sentinel = "sk-ant-custom-sentinel"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_PROXY_KEY", sentinel)
    for key, value in {
        "BAND_AGENT_ID": "documented-agent",
        "BAND_API_KEY": PROXY_MANAGED_API_KEY,
        "BAND_WS_URL": "wss://band.test/socket",
        "BAND_REST_URL": "https://band.test",
    }.items():
        monkeypatch.setenv(key, value)
    observed_keys: list[str] = []

    async def provider(request: Request) -> JSONResponse:
        observed_keys.append(request.headers["x-api-key"])
        return JSONResponse(
            {
                "id": "message-probe",
                "type": "message",
                "role": "assistant",
                "model": "probe-model",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    server = ManagedUvicornServer(
        Starlette(routes=[Route("/v1/messages", provider, methods=["POST"])]),
        host="127.0.0.1",
        port=0,
    )

    async def provider_probe(agent: Any) -> None:
        adapter = agent.adapter
        try:
            await adapter.client.messages.create(
                model=adapter.config.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "probe"}],
            )
        finally:
            await adapter.client.close()

    # Replace the live Band boundary; keep the documented adapter and real HTTP client.
    namespace["Agent"] = SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(**kwargs)
    )
    namespace["run_with_graceful_shutdown"] = provider_probe
    async with running(server):
        monkeypatch.setenv(
            "ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.bound_port}"
        )
        await namespace["main"]()
    assert observed_keys == [sentinel]
