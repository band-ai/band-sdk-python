"""The ACP launcher resolves to a full path, so it spawns on Windows too."""

from __future__ import annotations

from unittest.mock import patch

from band.integrations.acp.client_adapter import (
    _resolve_launcher,
)


class TestResolveLauncher:
    """The launcher is resolved to a full path so the subprocess spawns on Windows,
    where an npm launcher (``npx``) is ``npx.cmd`` and bare-name exec lookup fails."""

    def test_resolves_launcher_and_preserves_args(self) -> None:
        """The launcher becomes its resolved path; the arguments are untouched."""
        with patch(
            "band.integrations.acp.client_adapter.shutil.which",
            return_value="/opt/node/bin/npx",
        ):
            assert _resolve_launcher(["npx", "@zed-industries/codex-acp"]) == [
                "/opt/node/bin/npx",
                "@zed-industries/codex-acp",
            ]

    def test_unresolved_name_is_left_as_is(self) -> None:
        """An unresolvable launcher is passed through so spawn fails loudly, not here."""
        with patch(
            "band.integrations.acp.client_adapter.shutil.which", return_value=None
        ):
            assert _resolve_launcher(["mystery-bin", "arg"]) == ["mystery-bin", "arg"]
