"""Never-in-VM proof: proxy-managed custody keeps the real Band key off the sandbox.

Under proxy-managed custody (`sbx create WORKLOAD WORKSPACE` + host-side
`sbx secret set-custom`), with only the sentinel in the VM:

1. **injection path** — the Band host's cert, through the sandbox proxy, is the
   Docker Sandboxes MITM cert. This proves the path is active, not that the key
   authenticates; ``test_owned_session.py`` proves that with completed turns.
2. **declared posture** — VM exec runs as ``agent`` and only declared hosts pass.
3. **never in VM** — the exact key the fixture stored is absent from the VM's
   env, files, and the checked ``/proc`` entries.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit.docker_cli import BUILD_TIMEOUT_S
from tests.docker.toolkit.sandbox_agent import workload_posture
from tests.docker.toolkit.sbx_cli import CREATE_TIMEOUT_S, PROXY_CA_MARKER, Sandbox
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.requires import Dep, requires
from tests.e2e.baseline.settings import BaselineSettings


@lane(Lane.KIT)
@requires(Dep.DOCKER, Dep.SBX)
@pytest.mark.timeout(extra=BUILD_TIMEOUT_S + CREATE_TIMEOUT_S)
# The session WebSocket observer lives on the session loop.
@pytest.mark.asyncio(loop_scope="session")
class TestProxyManagedCustody:
    async def test_band_host_rides_the_injection_path(
        self,
        custody_sandbox: tuple[Sandbox, str],
        baseline_settings: BaselineSettings,
    ) -> None:
        sandbox, _ = custody_sandbox
        band_host = urlsplit(baseline_settings.endpoints.rest_url).hostname
        assert band_host is not None

        cert = sandbox.band_host_cert(band_host)

        # The probe produced a cert, so a missing marker means "wrong issuer".
        assert "cert-probe-error" not in cert, cert
        assert "issuer:" in cert, cert
        assert PROXY_CA_MARKER in cert, cert

    @pytest.mark.parametrize("custody_sandbox", [False], indirect=True)
    async def test_workload_identity_and_network_policy(
        self, custody_sandbox: tuple[Sandbox, str]
    ) -> None:
        """Before foreground startup, VM exec uses agent and enforces declared hosts."""
        sandbox, _ = custody_sandbox

        assert workload_posture(sandbox) == ("1000", "200", "403")

    async def test_real_band_key_never_enters_the_vm(
        self, custody_sandbox: tuple[Sandbox, str]
    ) -> None:
        sandbox, real_key = custody_sandbox
        environ = sandbox.all_process_environ()

        # Positive control first: the agent's live environment was captured, so
        # the absence checks below cannot pass over empty output.
        assert f"BAND_API_KEY={PROXY_MANAGED_API_KEY}" in environ, (
            "agent env not observed"
        )
        assert real_key not in environ
        assert real_key not in sandbox.shell_env()
        assert (
            sandbox.files_containing(
                real_key, search_paths=["/home/agent", "/etc", "/tmp", "/workspace"]
            )
            == ""
        )
