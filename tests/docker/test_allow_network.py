"""A run's network grant reads sbx's policy decision and revokes only what it added."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.docker.markers import requires_posix_shell
from tests.docker.toolkit.sbx_cli import allow_network

HOST = "staging.band.test"
CHECK = ["policy", "check", "network", HOST, "--json"]


def sbx_calls(calls: Path) -> list[list[str]]:
    return [json.loads(line) for line in calls.read_text().splitlines()]


@requires_posix_shell
class TestAllowNetwork:
    @pytest.mark.parametrize(
        ("allowed", "expected"),
        [
            pytest.param(True, [CHECK], id="already-allowed-left-alone"),
            pytest.param(
                False,
                [
                    CHECK,
                    ["policy", "allow", "network", HOST],
                    ["policy", "rm", "network", "--resource", HOST],
                ],
                id="denied-granted-then-revoked",
            ),
        ],
    )
    def test_decision_trailed_by_the_update_banner_drives_the_grant(
        self,
        sbx_policy_peer: Path,
        monkeypatch: pytest.MonkeyPatch,
        allowed: bool,
        expected: list[list[str]],
    ) -> None:
        monkeypatch.setenv("SBX_PEER_ALLOWED", str(allowed).lower())

        with allow_network(HOST):
            pass

        assert sbx_calls(sbx_policy_peer) == expected
