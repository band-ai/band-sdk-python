from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest


class TestArguments:
    def test_claude_check_needs_no_band_identity(
        self, bridge: ModuleType, tmp_path: Path
    ) -> None:
        args = bridge.arguments(["--check-claude", "--workspace", str(tmp_path)])
        assert args.agent_id is None
        assert args.workspace == tmp_path.resolve()

    def test_connect_requires_an_explicit_identity(self, bridge: ModuleType) -> None:
        with pytest.raises(SystemExit) as error:
            bridge.arguments([])
        assert error.value.code == 2

    def test_refuses_a_file_as_the_workspace(
        self, bridge: ModuleType, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "file"
        workspace.write_text("not a directory", encoding="utf-8")
        with pytest.raises(SystemExit) as error:
            bridge.arguments(["--check-claude", "--workspace", str(workspace)])
        assert error.value.code == 2
