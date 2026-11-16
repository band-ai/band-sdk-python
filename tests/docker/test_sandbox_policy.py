"""The sandbox policy the self-registration demo relies on, guarded where CI runs it."""

from __future__ import annotations

from band.credentials import PROXY_MANAGED_API_KEY
from tests.docker.toolkit.sbx_cli import (
    SBX,
    kit_baseline_hosts,
    remove_custom_secret_command,
)
from tests.paths import KIT_DIR


class TestSandboxPolicyContracts:
    def test_secret_removal_is_scoped_to_the_sandbox(self) -> None:
        # A global (`-g`) removal could delete an operator's credential.
        name = "band-nevervm-abc123"
        assert remove_custom_secret_command(sandbox=name) == [
            SBX, "secret", "rm", "--placeholder", PROXY_MANAGED_API_KEY,
            "--sandbox", name, "-f",
        ]  # fmt: skip

    def test_kit_baseline_reaches_only_prod_band(self) -> None:
        # A Band host is granted only when the kit's baseline doesn't already
        # reach it. Prod (`app.band.ai`) is the *only* Band host the shipped spec
        # reaches, so it is never granted globally, while any non-prod Band
        # deployment is absent and gets the per-run `allow_network`.
        baseline = kit_baseline_hosts(KIT_DIR)
        band_hosts = {host for host in baseline if host.endswith(".band.ai")}
        assert band_hosts == {"app.band.ai"}
