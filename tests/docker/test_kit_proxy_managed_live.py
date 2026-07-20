"""Never-in-VM proof: proxy-managed custody keeps the real Band key off the sandbox.

Under proxy-managed custody (`sbx create --kit` + host-side `sbx secret
set-custom`), with only the sentinel in the VM, this checks:

1. **injection path** — the Band host's cert, *through the sandbox proxy*, is the
   Docker Sandboxes MITM cert (the request rides the injection path, not a plain
   tunnel). Note: this proves the path is active, **not** that the correct key
   authenticates — that is (3).
2. **never in VM** — the exact key the fixture provisioned into `set-custom` is
   absent from the VM's env, files, and the checked `/proc` entries.
3. **correct injection (auth)** — the agent connects (REST + WS) and round-trips
   a room message, i.e. the proxy replaced the sentinel with a *working* key.
   This is the only check that proves authentication; it needs live agent
   provisioning and is the first-live-run item below.

Runs only on a Docker-Sandbox-capable host (nested virtualization) with the
`sbx` CLI, against the Band deployment `.env.test` points at. Gated behind BOTH
``sandbox`` (SANDBOX_TESTS_ENABLED=true) and ``e2e`` (E2E_TESTS_ENABLED=true);
skipped on every ordinary/CI run.

Status: the injection-path and never-in-VM checks are wired against a
self-provisioned secret; the round-trip (auth) check is the live TODO.
"""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit.sbx_cli import (
    PROXY_CA_MARKER,
    Sandbox,
    custom_secret,
    sbx_available,
)
from tests.paths import KIT_DIR


pytestmark = [pytest.mark.sandbox, pytest.mark.e2e]


@pytest.fixture
def band_host() -> str:
    """The Band host the agent is pointed at (from settings) — the injection and
    absence checks both target exactly this host."""
    from tests.e2e.baseline.settings import BaselineSettings

    return urlsplit(BaselineSettings().endpoints.rest_url).hostname or "app.band.ai"


@pytest.fixture
def provisioned_sandbox(band_host: str) -> Iterator[tuple[Sandbox, str]]:
    """A sandbox under proxy-managed custody, plus the real key it was given.

    The fixture provisions the `set-custom` secret **itself** with the real Band
    key, so the value the absence check asserts-absent is exactly the one the
    proxy would inject — no guessing which key is stored. The kit bakes no
    `credentials` block (the kit-declared form crashes `sbx create` 0.35.0);
    injection is host-side `set-custom`, verified live to flip the Band host's
    TLS to the Docker Sandboxes MITM cert. Yields ``(sandbox, real_key)``.
    """
    if not sbx_available():
        pytest.skip("sbx CLI not on PATH")
    from tests.e2e.baseline.settings import BaselineSettings

    real_key = BaselineSettings().credentials.api_key_user
    if not real_key or real_key == PROXY_MANAGED_API_KEY:
        pytest.skip("a real Band key (BAND_API_KEY_USER) is required")

    workspace = KIT_DIR / "echo-agent"  # defaults to proxy-managed custody
    with custom_secret(
        host="**.band.ai",
        env="BAND_API_KEY",
        value=real_key,
        placeholder=PROXY_MANAGED_API_KEY,
    ):
        with Sandbox.create(kit=KIT_DIR, workspace=workspace) as sandbox:
            yield sandbox, real_key


def test_band_host_rides_the_injection_path(
    provisioned_sandbox: tuple[Sandbox, str], band_host: str
) -> None:
    """The Band host's cert (through the proxy) is the injection MITM cert.

    Proves the injection *path* is active for this host — not, on its own, that
    the injected key authenticates (see the round-trip test)."""
    sandbox, _ = provisioned_sandbox
    cert = sandbox.band_host_cert(band_host)
    assert PROXY_CA_MARKER in cert, cert


def test_real_band_key_never_enters_the_vm(
    provisioned_sandbox: tuple[Sandbox, str],
) -> None:
    """The provisioned real key is absent from the VM; only the sentinel is present.

    The asserted-absent value is the one the fixture stored in `set-custom`, so a
    pass means *that* key never reached the VM. The scan is targeted (env,
    ``/proc``, common writable paths), not an exhaustive filesystem sweep."""
    sandbox, real_key = provisioned_sandbox
    assert sandbox.real_secret_absent(
        real_key, search_paths=["/home/agent", "/etc", "/tmp", "/workspace"]
    )
    # The agent's BAND_API_KEY is exactly the sentinel — not just that the string
    # appears somewhere. `all_process_environ` emits one VAR=value per line.
    assert f"BAND_API_KEY={PROXY_MANAGED_API_KEY}" in sandbox.all_process_environ()


@pytest.mark.skip(
    reason="first live run: provision the agent in-sandbox + reply capture"
)
def test_agent_connects_and_round_trips_under_injection() -> None:
    """The agent connects over REST + WS and round-trips a room message — the only
    check that proves the proxy injected a *working* key (authentication), not
    just that the injection path is present.

    Reuse the baseline toolkit (`resource_manager` to provision the agent,
    `user_ops` to send, `reply_capture` to await the reply) as
    ``test_band_python_kit_live.py`` does, with the agent running inside the `sbx`
    sandbox (proxy-managed) rather than a bare `docker run`.
    """
