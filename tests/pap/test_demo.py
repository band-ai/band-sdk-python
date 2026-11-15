"""Acceptance checks for the standalone PAP collaboration example."""

from __future__ import annotations

import ast
import asyncio
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from bandpap.auth import AuthError
from bandpap.storage import StateUnavailable

from examples.pap.demo import run_demo
from examples.pap.host import DemoHost, build
from examples.pap.simulation import ROOM
from tests.paths import EXAMPLES_ROOT


class TestDemo:
    async def test_both_company_reply_paths_and_isolation_without_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse_network(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("Offline example attempted a socket connection")

        monkeypatch.setattr(socket.socket, "connect", refuse_network)
        result = await run_demo()
        assert set(result["peers"]) == {"company-a", "company-b"}
        for peer in result["peers"].values():
            assert peer["reply"]
            assert peer["reply_bound"] is True
            assert peer["structured_reply"] is True
        assert (
            result["peers"]["company-a"]["reply"]
            != result["peers"]["company-b"]["reply"]
        )
        assert result["denials"] == {
            "forged_identity": True,
            "cross_peer_record": True,
            "cross_peer_delivery": True,
            "unregistered_session": True,
        }
        routes = {
            (post["sender"], tuple(post["recipients"])) for post in result["transcript"]
        }
        for name in ("company-a", "company-b"):
            assert ("personal-" + name, (name,)) in routes
            assert (name, ("personal-" + name,)) in routes
        assert result["evidence"]["temporary_state_removed"] is True

    async def test_reply_records_reject_the_other_company(self) -> None:
        app = build()
        async with app.router.lifespan_context(app):
            host = app.state.demo
            report = await host.run()
            for name, other in (("company-a", "company-b"), ("company-b", "company-a")):
                reference = host.record_references[name]
                record = await host.outbox.record(
                    reference, room=ROOM, actor="personal-" + name
                )
                assert record["message"]["data"]["private_note"] not in json.dumps(
                    report
                )
                with pytest.raises(StateUnavailable):
                    await host.outbox.record(
                        reference, room=ROOM, actor="personal-" + other
                    )

    async def test_factory_lifespans_use_fresh_state_and_close_on_failure(self) -> None:
        app = build()
        paths = []
        for _ in range(2):
            with pytest.raises(RuntimeError, match="host interrupted"):
                async with app.router.lifespan_context(app):
                    host = app.state.demo
                    paths.append(host.path)
                    assert host.path.is_dir()
                    raise RuntimeError("host interrupted")
            assert host.closed
            assert not host.path.exists()
        assert paths[0] != paths[1]

    async def test_startup_failure_closes_allocated_resources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened = []
        original = DemoHost.open

        async def fail_after_open(host: DemoHost) -> None:
            opened.append(host)
            await original(host)
            raise RuntimeError("startup interrupted")

        monkeypatch.setattr(DemoHost, "open", fail_after_open)
        app = build()
        with pytest.raises(RuntimeError, match="startup interrupted"):
            async with app.router.lifespan_context(app):
                pytest.fail("Application yielded after failed startup")
        assert len(opened) == 1
        assert opened[0].closed
        assert not opened[0].path.exists()

    def test_documented_module_runs_with_only_the_example_tree(
        self, tmp_path: Path
    ) -> None:
        shutil.copytree(EXAMPLES_ROOT / "pap", tmp_path / "examples" / "pap")
        result = subprocess.run(
            [sys.executable, "-m", "examples.pap.demo"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert set(report["peers"]) == {"company-a", "company-b"}
        assert report["evidence"]["temporary_state_removed"] is True
        assert "[PAP record:" not in result.stdout
        assert "eyJ" not in result.stdout
        assert "PRIVATE KEY" not in result.stdout

    def test_example_has_no_test_framework_or_mock_dependencies(self) -> None:
        for path in (EXAMPLES_ROOT / "pap").glob("*.py"):
            tree = ast.parse(path.read_text())
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            assert not any(
                name.split(".")[0] in {"tests", "pytest", "unittest"}
                for name in imports
            ), path.name

    async def test_success_leaves_no_unsettled_room_deliveries(self) -> None:
        app = build()
        async with app.router.lifespan_context(app):
            host = app.state.demo
            await host.run()
            for proxy in host.identities:
                health = host.outbox.health(proxy=proxy)
                assert health["ready"] == 0, (proxy, health)
                assert health["uncertain"] == 0, (proxy, health)

    async def test_revoked_source_cannot_flush_an_accepted_handoff(self) -> None:
        app = build()
        async with app.router.lifespan_context(app):
            host = app.state.demo
            name = "company-a"
            access = host.accesses[name]
            await host.request(name)
            await host.gateway.flush(access)
            host.central.revoked.add(access.session.session_id)
            await host.outbox.flush(proxy=host.peers[name].personal)
            assert host.room.messages == []

    @pytest.mark.parametrize("owner", ["source", "company"])
    async def test_revoked_session_cannot_read_previously_delivered_record(
        self, owner: str
    ) -> None:
        app = build()
        async with app.router.lifespan_context(app):
            host = app.state.demo
            await host.run()
            name = "company-a"
            peer = host.peers[name]
            site, access = (
                (host.central, host.accesses[name])
                if owner == "source"
                else (peer.site, peer.access)
            )
            site.revoked.add(access.session.session_id)
            with pytest.raises((AuthError, PermissionError, StateUnavailable)):
                await host.outbox.record(
                    host.record_references[name], room=ROOM, actor=peer.personal
                )

    @pytest.mark.parametrize("phase", ["request", "exchange"])
    async def test_failed_peer_is_joined_before_resources_close(
        self, monkeypatch: pytest.MonkeyPatch, phase: str
    ) -> None:
        app = build()
        started = asyncio.Event()
        sibling_cleanup = []
        with pytest.raises(ExceptionGroup):
            async with app.router.lifespan_context(app):
                host = app.state.demo

                async def fail_or_wait(name: str) -> None:
                    if name == "company-a":
                        await started.wait()
                        raise RuntimeError("company interrupted")
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        sibling_cleanup.append(not host.closed and host.path.exists())

                monkeypatch.setattr(host, phase, fail_or_wait)
                await host.run()
        assert sibling_cleanup == [True]
        assert host.closed
        assert not host.path.exists()

    async def test_factory_serves_demo_and_keeps_protocol_routes_authenticated(
        self,
    ) -> None:
        app = build()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://collaboration.example.invalid",
            ) as client,
        ):
            denied = await client.post("/poppy/conversations", json={"message": {}})
            assert denied.status_code == 401
            response = await client.post("/demo/run")
            assert response.status_code == 200
            assert set(response.json()["peers"]) == {"company-a", "company-b"}
            second = await client.post("/demo/run")
            assert second.json() == response.json()

    async def test_one_personal_owner_keeps_company_sessions_and_threads_separate(
        self,
    ) -> None:
        app = build()
        async with app.router.lifespan_context(app):
            host = app.state.demo
            owners = {access.session.scope.user for access in host.accesses.values()}
            assert len(owners) == 1
            assert (
                len({access.session.session_id for access in host.accesses.values()})
                == 2
            )
            assert len({peer.owner.scope.user for peer in host.peers.values()}) == 2
            await host.run()
            with pytest.raises(PermissionError):
                await host.gateway.read(
                    host.accesses["company-a"], host.conversations["company-b"]
                )
