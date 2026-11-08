from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from bandpap.bridge import (
    GatewayReplies,
    PAPGatewayAdapter,
    PAPGatewayAdapterConfig,
    ReplyBinding,
)
from bandpap.storage import StateUnavailable

from band.core.types import PlatformConnection
from tests.pap.adapterkit import incoming, turn
from tests.pap.gatewaykit import GatewayHarness, message


class TestGatewayBridge:
    @pytest.mark.parametrize(
        "attack", ["missing", "wrong_source", "wrong_room", "wrong_proxy", "revoked"]
    )
    async def test_forged_or_revoked_binding_cannot_publish_to_external_thread(
        self, tmp_path: Path, attack: Any
    ) -> None:
        kit = GatewayHarness(tmp_path / "state")
        access = kit.access()
        created = await kit.request(
            access, "POST", "/poppy/conversations", value=message()
        )
        conversation = created.json()["conversation_id"]
        replies = GatewayReplies(kit.store)
        adapter = PAPGatewayAdapter(
            PAPGatewayAdapterConfig(proxy_id="proxy-alice"),
            gateway=kit.gateway,
            replies=replies,
        )
        binding = ReplyBinding(
            access=access,
            conversation_id=conversation,
            room="one-room",
            proxy="proxy-alice",
            source="company",
            message_id="reply",
        )
        if attack != "missing":
            if attack == "wrong_source":
                binding = binding.model_copy(update={"source": "attacker"})
            if attack == "wrong_room":
                binding = binding.model_copy(update={"room": "other"})
            if attack == "wrong_proxy":
                binding = binding.model_copy(update={"proxy": "other"})
            replies.stage(binding)
        if attack == "revoked":
            kit.band.revoked.add(access.session.session_id)
        msg = incoming(
            id="reply",
            room_id="one-room",
            sender_id="company",
            metadata={"mentions": [{"id": "proxy-alice", "kind": "mention"}]},
        )
        tools = turn()
        with pytest.raises((StateUnavailable, PermissionError)):
            await adapter.on_message(
                msg,
                tools,
                None,
                None,
                None,
                is_session_bootstrap=False,
                room_id="one-room",
            )
        tools.turn.settle.assert_not_called()
        kit.band.revoked.clear()
        result = await kit.request(
            access, "GET", f"/poppy/conversations/{conversation}/events"
        )
        assert not any(
            event.get("message", {}).get("role") == "company"
            for event in result.json()["events"]
        )
        await kit.close()

    async def test_reference_mention_cannot_publish_even_with_valid_binding(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "state")
        access = kit.access()
        created = await kit.request(
            access, "POST", "/poppy/conversations", value=message()
        )
        replies = GatewayReplies(kit.store)
        replies.stage(
            ReplyBinding(
                access=access,
                conversation_id=created.json()["conversation_id"],
                room="one-room",
                proxy="proxy-alice",
                source="company",
                message_id="reply",
            )
        )
        adapter = PAPGatewayAdapter(
            PAPGatewayAdapterConfig(proxy_id="proxy-alice"),
            gateway=kit.gateway,
            replies=replies,
        )
        msg = incoming(
            id="reply",
            room_id="one-room",
            sender_id="company",
            metadata={"mentions": [{"id": "proxy-alice", "kind": "reference"}]},
        )
        await adapter.on_message(
            msg,
            turn(),
            None,
            None,
            None,
            is_session_bootstrap=False,
            room_id="one-room",
        )
        result = await kit.request(
            access,
            "GET",
            f"/poppy/conversations/{created.json()['conversation_id']}/events",
        )
        assert not any(
            event.get("message", {}).get("role") == "company"
            for event in result.json()["events"]
        )
        await kit.close()

    async def test_adapter_checks_its_independent_platform_identity(
        self, tmp_path: Path
    ) -> None:
        kit = GatewayHarness(tmp_path / "state")
        adapter = PAPGatewayAdapter(
            PAPGatewayAdapterConfig(proxy_id="proxy-alice"),
            gateway=kit.gateway,
            replies=GatewayReplies(kit.store),
        )
        adapter.platform = PlatformConnection(
            agent_id="other",
            api_key="private",
            rest_url="https://band.example",
            ws_url="wss://band.example/socket",
        )
        with pytest.raises(ValueError, match="own Band credential"):
            await adapter.on_started("proxy", "description")
        adapter.platform = replace(adapter.platform, agent_id="proxy-alice")
        await adapter.on_started("proxy", "description")
        assert adapter.agent_name == "proxy"
        await kit.close()
