"""OneShotInvoker: draining queued messages after the forwarded one."""

from __future__ import annotations

from unittest.mock import AsyncMock

from tests.runtime.conftest import ctx_item, make_link_mock, platform_msg
from tests.runtime.oneshot.kit import make_adapter_mock, make_invoker, msg_body


class TestDrain:
    async def test_drains_messages_seen_by_llm(self) -> None:
        """The case drain is for: the LLM saw msg-2 and msg-3 in its history
        snapshot. Drain marks them processed without re-invoking the LLM.
        """
        link = make_link_mock(
            history_items=[ctx_item("msg-2"), ctx_item("msg-3")],
            next_messages=[
                platform_msg("msg-1"),  # claim check
                platform_msg("msg-2"),  # drain (in snapshot)
                platform_msg("msg-3"),  # drain (in snapshot)
                None,
            ],
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(msg_body(msg_id="msg-1"))

        assert result["status"] == "done"
        assert result["drained"] == ["msg-2", "msg-3"]
        adapter.on_event.assert_awaited_once()  # LLM ran exactly once
        processed = [c.args for c in link.mark_processed.await_args_list]
        assert processed == [
            ("room-1", "msg-1"),
            ("room-1", "msg-2"),
            ("room-1", "msg-3"),
        ]

    async def test_drain_leaves_messages_not_in_snapshot_open(self) -> None:
        """Drain race fix: msg-2 arrived after the history snapshot (it's not
        in seen_ids). Drain must stop and leave it open for the next
        invocation rather than swallowing it without an LLM call.
        """
        link = make_link_mock(
            history_items=[ctx_item("msg-1")],  # snapshot = msg-1 only
            next_messages=[
                platform_msg("msg-1"),  # claim check
                platform_msg("msg-2"),  # arrived after snapshot → leave open
                None,
            ],
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(msg_body(msg_id="msg-1"))

        processed_ids = [c.args[1] for c in link.mark_processed.await_args_list]
        assert processed_ids == ["msg-1"], (
            f"drain must not swallow msg-2; got mark_processed={processed_ids}"
        )
        assert "msg-2" not in result.get("drained", [])

    async def test_drain_skips_self_messages_defensively(self) -> None:
        """If the platform returns one of our own messages from
        get_next_message during drain, skip it without marking — parity with
        the SDK's ExecutionContext self-message guard.
        """
        self_msg = platform_msg("msg-self", sender_type="Agent", sender_id="agent-1")
        link = make_link_mock(
            history_items=[ctx_item("msg-1"), ctx_item("msg-self")],
            next_messages=[
                platform_msg("msg-1"),  # claim check
                self_msg,  # our own message — skip
                None,
            ],
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(msg_body(msg_id="msg-1"))

        processed_ids = [c.args[1] for c in link.mark_processed.await_args_list]
        assert "msg-self" not in processed_ids, (
            f"drain must skip self-messages; got mark_processed={processed_ids}"
        )
        assert "msg-self" not in result.get("drained", [])

    async def test_drain_truncated_surfaced(self) -> None:
        """When the drain cap fires, the response carries drain_truncated so
        the bridge gets a signal.
        """
        # Always return an in-snapshot message so drain never naturally stops;
        # the cap is the only exit.
        always_stale = platform_msg("msg-x")
        link = make_link_mock(history_items=[ctx_item("msg-x")])
        link.get_next_message = AsyncMock(
            side_effect=[platform_msg("msg-1")]  # claim check
            + [always_stale] * 10  # drain keeps finding msg-x
        )
        invoker = await make_invoker(link, make_adapter_mock(), drain_cap=3)

        result = await invoker.handle_event(msg_body(msg_id="msg-1"))

        assert result["status"] == "done"
        assert result.get("drain_truncated") is True
