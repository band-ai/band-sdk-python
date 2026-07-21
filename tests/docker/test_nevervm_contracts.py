"""CI-run guards for the contracts the (gated) never-in-VM proof depends on.

The proof in ``test_kit_proxy_managed_live.py`` runs only on sbx + staging, so
the deterministic pieces it relies on are guarded here, where CI executes them —
a broken agent-name derivation, cert probe, or settings path fails fast here
instead of lying dormant until a rare live run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.docker.markers import requires_posix_shell
from tests.docker.toolkit.sbx_cli import (
    files_containing_command,
    kit_agent_name,
    kit_baseline_hosts,
    remove_custom_secret_command,
    set_custom_secret_command,
)
from tests.e2e.baseline.settings import BaselineSettings
from tests.paths import KIT_DIR

# The cert probe's behavior (full CONNECT read, non-2xx rejection, and thereby
# its syntactic validity) is covered by tests/docker/test_sbx_cli.py.


def test_kit_agent_name_matches_the_shipped_spec() -> None:
    # A sandbox kit is created as `sbx create --kit <kit> <name>`; the name is
    # the kit's own (a plain `shell` agent is rejected). Guards the derivation
    # reads spec.yaml's `name`, and that the shipped kit declares it.
    assert kit_agent_name(KIT_DIR) == "band-python-kit"


def test_custom_secret_injection_is_scoped_to_the_sandbox() -> None:
    # The Band key is injected scoped to the proof's own sandbox, never globally
    # (`-g`): the kit README registers the Band key globally for real use, so a
    # global test secret would overwrite and then delete the operator's own
    # credential. Guards both the set and the removal against a `-g` regression.
    name = "band-nevervm-abc123"
    set_command = set_custom_secret_command(
        sandbox=name,
        host="**.band.ai",
        env="BAND_API_KEY",
        value="real-key",
        placeholder="proxy-managed",
    )
    remove_command = remove_custom_secret_command(sandbox=name, host="**.band.ai")

    assert "-g" not in set_command and name in set_command
    assert "-g" not in remove_command and name in remove_command


def test_kit_baseline_reaches_only_prod_band() -> None:
    # The proof grants a Band host only when the kit's baseline doesn't already
    # reach it. Prod (`app.band.ai`) is the *only* Band host the shipped spec
    # reaches, so it is never granted globally, while any non-prod Band
    # deployment is absent and gets the per-run `allow_network`. Asserting prod
    # is the sole baseline Band host fails for a real reason — a second `band.ai`
    # host added to the spec — rather than restating that one arbitrary staging
    # host happens not to be listed.
    baseline = kit_baseline_hosts(KIT_DIR)
    band_hosts = {host for host in baseline if host.endswith(".band.ai")}
    assert band_hosts == {"app.band.ai"}


def test_baseline_settings_expose_the_paths_the_proof_reads() -> None:
    # The fixtures read settings.endpoints.rest_url and
    # settings.credentials.api_key_user; the flat .rest_url / .api_key_user bug
    # this guards is an AttributeError only reachable on a live run.
    settings = BaselineSettings()
    assert isinstance(settings.endpoints.rest_url, str)
    assert isinstance(settings.credentials.api_key_user, str)


@requires_posix_shell
def test_absence_search_scans_binary_files_too(tmp_path: Path) -> None:
    # The never-in-VM proof asserts the real key is in *no* searched file. grep's
    # default binary heuristic can skip a file with NUL bytes (a cache/DB), so a
    # key leaked into one would pass the absence check unseen — the command uses
    # `-a` to search every file as text. Builds the real command and runs it via
    # the system grep the sandbox uses, so a flag regression fails here in CI
    # rather than lying dormant until a live sbx run. The secret's metacharacters
    # also guard that `-F` keeps the match literal.
    secret = "band-sk-neVerVm.$*SEKRET"
    (tmp_path / "plain.txt").write_text(f"prefix {secret} suffix\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01" + secret.encode() + b"\x00\xff")

    command = files_containing_command(secret, [str(tmp_path)])
    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert set(result.stdout.splitlines()) == {
        str(tmp_path / "plain.txt"),
        str(tmp_path / "blob.bin"),
    }
