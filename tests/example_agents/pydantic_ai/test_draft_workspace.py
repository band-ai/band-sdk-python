"""Both actual draft tools share a directory owned by the host's lifetime."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from tests.adaptercleanup import HostKind, host_for
from tests.example_agents.pydantic_ai.kit import EXAMPLE


class TestDraftWorkspace:
    @pytest.mark.parametrize("kind", list(HostKind))
    async def test_each_lifetime_has_a_fresh_workspace_removed_at_stop(
        self, kind: HostKind, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        paths = []
        for _ in range(2):
            resource = TemporaryDirectory(dir=tmp_path)
            with resource as workspace:
                draft_root = Path(workspace)
                paths.append(draft_root)
                adapter = EXAMPLE["build_adapter"](resource)
                save, read = EXAMPLE["shared_tools"](draft_root)
                async with host_for(adapter, kind) as host:
                    await host.start()
                    assert await read[1](read[0]()) == "No draft yet."
                    text = f"Draft {uuid4().hex}"
                    await save[1](save[0](text=text))
                    assert await read[1](read[0]()) == text
                    assert draft_root.is_dir()
                    await host.stop()
                    assert not draft_root.exists()
        assert paths[0] != paths[1]

    async def test_host_construction_failure_removes_the_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = tmp_path / f"{uuid4().hex}.yaml"
        config.write_text(
            "pydantic_agent:\n  agent_id: test-agent\n  api_key: test-key\n"
        )
        workspaces: list[Path] = []

        def create_workspace(*, prefix: str) -> TemporaryDirectory[str]:
            resource = TemporaryDirectory(prefix=prefix, dir=tmp_path)
            workspaces.append(Path(resource.name))
            return resource

        def refuse(*args: object, **kwargs: object) -> None:
            raise ValueError("Host construction failed")

        monkeypatch.setattr(EXAMPLE["Agent"], "from_config", refuse)
        monkeypatch.setitem(
            EXAMPLE["build_agent"].__globals__, "TemporaryDirectory", create_workspace
        )
        with pytest.raises(ValueError, match="Host construction failed"):
            await EXAMPLE["build_agent"](EXAMPLE["Settings"](DRAFT_CONFIG=config))
        assert len(workspaces) == 1
        assert not workspaces[0].exists()
