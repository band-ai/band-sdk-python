"""band-kit provision — host-side self-registration for a Docker-sandboxed Band agent.

Lets a customer stand up a brand-new sandboxed Band agent from one user key and
one command, with no pre-provisioned identity, while keeping the real agent key
out of the sandbox VM (the custody model INT-981 established).

Registration mints the agent key in the HTTP response body, so the only way the
VM never sees it is to register outside the VM: this runs on the host, then
hands the minted key to `sbx secret set-custom` (the same proxy-managed
injection slot INT-981 wired up) and writes the non-secret agent id into the
workspace's `band.yaml`, which `resolve_agent_id` already reads
(`band.docker.launcher.config`). Idempotency is host-side: a sandbox that
already has both the injected secret and a real `agent.id` is left alone.

Exit codes:
    0 — success (prints the agent id to stdout; the agent key is never printed
        except as a last-resort recovery banner if persisting it fails)
    1 — failure (prints error message to stderr)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

from band_rest import AgentRegisterRequest, AsyncRestClient
from band_rest.core.api_error import ApiError
from band_rest.core.request_options import RequestOptions
from pydantic_settings import BaseSettings, SettingsConfigDict
from ruamel.yaml import YAML

from band import LogSettings, LogStream
from band.credentials import PROXY_MANAGED_API_KEY
from band.docker.launcher.config import load_workspace_config

logger = logging.getLogger(__name__)

DEFAULT_REST_URL = "https://app.band.ai/"
DEFAULT_BAND_HOST = "**.band.ai"
DEFAULT_REQUEST_OPTIONS: Final[RequestOptions] = {"max_retries": 3}
DEFAULT_TIMEOUT: Final[int] = 120
SBX_SECRET_TIMEOUT_S: Final[int] = 60
SBX_CREATE_TIMEOUT_S: Final[int] = 600

# The echo-agent starter's shipped-but-unset marker (docker/band_python_kit/
# echo-agent/band.yaml); a workspace still carrying it has never been
# provisioned even though `agent.id` is non-empty.
PLACEHOLDER_AGENT_ID = "replace-with-agent-id"

# The published kit's own name (docker/band_python_kit/spec.yaml `name`), not a
# per-workspace choice — every sandbox this CLI creates rides that one kit.
KIT_AGENT_NAME = "band-python-kit"

SBX = "sbx"

_YAML = YAML()
_YAML.preserve_quotes = True


class ProvisionSettings(BaseSettings):
    """Env-sourced defaults for `band-kit provision`; CLI flags take precedence."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    band_rest_url: str = ""
    band_api_key_user: str = ""


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="band-kit",
        description="Host-side helper for the Band Python Docker Sandboxes kit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    provision = subparsers.add_parser(
        "provision",
        help=(
            "Register a Band agent and wire its key into a Docker Sandbox "
            "so the real key never enters the VM."
        ),
    )
    provision.add_argument(
        "--name",
        required=True,
        help="Sandbox name; also scopes the injected secret and the idempotency check.",
    )
    provision.add_argument(
        "--agent-name",
        default=None,
        help="Band agent display name (default: --name).",
    )
    provision.add_argument(
        "--description",
        required=True,
        help="Band agent description (platform requires 10-500 characters).",
    )
    provision.add_argument(
        "--workspace",
        required=True,
        type=Path,
        help="Path to the workspace directory containing band.yaml.",
    )
    provision.add_argument(
        "--host",
        default=DEFAULT_BAND_HOST,
        help=f"Host pattern to inject the Band key on (default: {DEFAULT_BAND_HOST}).",
    )
    provision.add_argument(
        "--api-key",
        default=None,
        help="register_only-scoped user API key (env: BAND_API_KEY_USER).",
    )
    provision.add_argument(
        "--rest-url",
        default=None,
        help=f"Band REST API URL (env: BAND_REST_URL, default: {DEFAULT_REST_URL})",
    )
    create_group = provision.add_mutually_exclusive_group()
    create_group.add_argument(
        "--create",
        dest="create",
        action="store_true",
        default=True,
        help="Also run `sbx create` after provisioning (default).",
    )
    create_group.add_argument(
        "--no-create",
        dest="create",
        action="store_false",
        help="Only register and inject the secret; skip `sbx create`.",
    )
    provision.add_argument(
        "--kit",
        default=None,
        help="Kit reference for `sbx create --kit` (required unless --no-create).",
    )
    provision.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout in seconds for the registration call (default: {DEFAULT_TIMEOUT})",
    )
    provision.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose logging",
    )
    return parser


def _describe_register_error(err: ApiError) -> str:
    """A human-readable message for a failed registration call."""
    if err.status_code == 403:
        return "Plan agent cap reached — cannot register another external agent."
    if err.status_code == 422:
        return (
            "An agent with this name already exists. Re-registration semantics "
            "for a re-created sandbox (new identity vs. adopt-existing) are not "
            "decided yet — see PLT-1066 (stale-agent lifecycle). Pick a new "
            "--agent-name or --name to register a distinct agent."
        )
    body = getattr(err, "body", None)
    error_obj = getattr(body, "error", None)
    message = getattr(error_obj, "message", None)
    if message:
        return f"Failed to register agent: {message}"
    return f"Failed to register agent: HTTP {err.status_code}"


def _band_yaml_path(workspace: Path) -> Path:
    return workspace / "band.yaml"


def read_agent_id(workspace: Path) -> str:
    """The current `agent.id` in the workspace's `band.yaml` (`""` if unset)."""
    path = _band_yaml_path(workspace)
    if not path.is_file():
        raise ValueError(f"no band.yaml found in workspace: {path}")
    data = _YAML.load(path.read_text(encoding="utf-8")) or {}
    return str((data.get("agent") or {}).get("id") or "")


def write_agent_id(workspace: Path, agent_id: str) -> None:
    """Write `agent.id` into `band.yaml`, preserving every comment and the rest
    of the file (a round-trip load/dump, not a regenerate-from-scratch)."""
    path = _band_yaml_path(workspace)
    data = _YAML.load(path.read_text(encoding="utf-8"))
    data.setdefault("agent", {})["id"] = agent_id
    with path.open("w", encoding="utf-8") as fh:
        _YAML.dump(data, fh)
    # Reload through the launcher's own strict model: a malformed write (or an
    # already-invalid band.yaml) fails loudly here, not at sandbox boot.
    reloaded = load_workspace_config(path)
    if reloaded.agent.id != agent_id:
        raise RuntimeError(
            f"wrote agent.id to {path} but it did not round-trip; check the file"
        )


def _table_targets(ls_output: str) -> set[str]:
    """The TARGETS column of an `sbx secret ls` custom-secrets table.

    There is no `--json` output for `sbx secret ls` (verified against v0.35.0),
    so this parses the human-readable table; reading the header locates the
    column instead of hardcoding its position, so column reordering can't
    silently break the check.
    """
    lines = ls_output.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if line.split()[:1] == ["SCOPE"]), None
    )
    if header_idx is None:
        return set()
    header_cols = re.split(r"\s{2,}", lines[header_idx].strip())
    if "TARGETS" not in header_cols:
        return set()
    targets_idx = header_cols.index("TARGETS")
    targets = set()
    for line in lines[header_idx + 1 :]:
        cols = re.split(r"\s{2,}", line.strip())
        if len(cols) > targets_idx:
            targets.add(cols[targets_idx])
    return targets


def sandbox_has_band_secret(name: str, host: str) -> bool:
    """Whether `name`'s own scope already has a custom secret for `host`."""
    result = subprocess.run(
        [SBX, "secret", "ls", name],
        capture_output=True,
        text=True,
        check=False,
        timeout=SBX_SECRET_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sbx secret ls {name} failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return host in _table_targets(result.stdout)


def inject_agent_key(*, name: str, host: str, agent_key: str) -> None:
    """`sbx secret set-custom`, scoped to `name`, with the key on stdin only.

    The key never touches argv/history: `--value`/`--token` are omitted, so
    `set-custom` reads it from stdin (verified against v0.35.0). The
    placeholder is pinned to `band.credentials.PROXY_MANAGED_API_KEY`, the
    sentinel `require_sentinel_under_proxy_managed` requires in the VM.
    """
    argv = [
        SBX,
        "secret",
        "set-custom",
        name,
        "--host",
        host,
        "--env",
        "BAND_API_KEY",
        "--placeholder",
        PROXY_MANAGED_API_KEY,
    ]
    result = subprocess.run(
        argv,
        input=agent_key,
        capture_output=True,
        text=True,
        check=False,
        timeout=SBX_SECRET_TIMEOUT_S,
    )
    if result.returncode != 0:
        detail = (
            (result.stderr or result.stdout or "").replace(agent_key, "***").strip()
        )
        raise RuntimeError(
            f"sbx secret set-custom failed (exit {result.returncode}): {detail}"
        )


def create_sandbox(*, name: str, kit: str, workspace: Path) -> None:
    """`sbx create --name <name> --kit <kit> band-python-kit <workspace>`."""
    result = subprocess.run(
        [SBX, "create", "--name", name, "--kit", kit, KIT_AGENT_NAME, str(workspace)],
        capture_output=True,
        text=True,
        check=False,
        timeout=SBX_CREATE_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sbx create failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()}"
        )


async def register_agent(
    *, api_key: str, rest_url: str, name: str, description: str
) -> tuple[str, str]:
    """Register on the host with a user key. Returns (agent_id, agent_api_key)."""
    client = AsyncRestClient(api_key=api_key, base_url=rest_url.rstrip("/"))
    try:
        response = await client.human_api_agents.register_my_agent(
            agent=AgentRegisterRequest(name=name, description=description),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
    except ApiError as e:
        raise RuntimeError(_describe_register_error(e)) from e
    finally:
        await client._client_wrapper.httpx_client.httpx_client.aclose()
    return response.data.agent.id, response.data.credentials.api_key


async def run(args: argparse.Namespace) -> str:
    """Execute the provision flow. Returns the agent id on success."""
    settings = ProvisionSettings()
    api_key = args.api_key or settings.band_api_key_user
    if not api_key:
        raise ValueError(
            "A register_only-scoped user API key is required. "
            "Provide --api-key or set BAND_API_KEY_USER."
        )
    rest_url = args.rest_url or settings.band_rest_url or DEFAULT_REST_URL
    if args.create and not args.kit:
        raise ValueError("--kit is required unless --no-create is passed.")

    workspace = args.workspace
    agent_name = args.agent_name or args.name

    existing_id = read_agent_id(workspace)
    already_has_secret = sandbox_has_band_secret(args.name, args.host)
    if existing_id and existing_id != PLACEHOLDER_AGENT_ID and already_has_secret:
        logger.info(
            "Sandbox %s already provisioned (agent.id=%s, secret present) — skipping",
            args.name,
            existing_id,
        )
        return existing_id
    if existing_id and existing_id != PLACEHOLDER_AGENT_ID and not already_has_secret:
        logger.warning(
            "band.yaml already names agent %s but no injected secret was found "
            "for %s — registering a new agent rather than reusing it",
            existing_id,
            args.name,
        )

    logger.info("Registering agent %r...", agent_name)
    try:
        agent_id, agent_key = await asyncio.wait_for(
            register_agent(
                api_key=api_key,
                rest_url=rest_url,
                name=agent_name,
                description=args.description,
            ),
            timeout=args.timeout,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"registration timed out after {args.timeout}s") from None
    logger.info("Registered agent: %s", agent_id)

    try:
        write_agent_id(workspace, agent_id)
        inject_agent_key(name=args.name, host=args.host, agent_key=agent_key)
    except Exception:
        logger.error(
            "Registered agent %s but failed to persist its key locally", agent_id
        )
        sys.stderr.write(
            f"\nCRITICAL: agent {agent_id} was registered but its key could not "
            "be stored. The platform shows this key exactly once and it cannot "
            "be retrieved again — store it now or the agent is orphaned:\n\n"
            f"  {agent_key}\n\n"
        )
        raise

    if args.create:
        logger.info("Creating sandbox %s...", args.name)
        create_sandbox(name=args.name, kit=args.kit, workspace=workspace)
        logger.info("Sandbox created: %s", args.name)

    return agent_id


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    settings = (
        LogSettings(log_level="DEBUG", log_stream=LogStream.STDERR)
        if args.verbose
        else LogSettings(log_stream=LogStream.STDERR)
    )
    settings.configure()

    try:
        agent_id = asyncio.run(run(args))
    except (ValueError, RuntimeError) as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)

    # stdout carries the machine-readable agent id (intentional print for CLI output)
    sys.stdout.write(agent_id + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
