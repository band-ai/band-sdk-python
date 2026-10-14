"""OneShotInvoker: paging room history."""

from __future__ import annotations

from unittest.mock import AsyncMock

from band_rest import (
    GetAgentChatContextResponse,
    GetAgentChatContextResponseMetadata,
)

from tests.runtime.conftest import ctx_item, make_link_mock, platform_msg
from tests.runtime.oneshot.kit import make_adapter_mock, make_invoker, msg_body

# ---------------------------------------------------------------------------
# History pagination
#
# The real context endpoint paginates with a cursor (``next_cursor`` /
# ``has_more``); ``page``/``page_size`` are deprecated and scheduled for
# removal. A room whose history spans more than one page must not silently
# lose the earlier pages.
# ---------------------------------------------------------------------------


class TestHistoryPagination:
    async def test_long_running_room_keeps_facts_from_the_start_of_the_conversation(
        self,
    ) -> None:
        """A project channel active long enough to span more than one fetch
        holds facts the model still needs — the codename set on day one is as
        relevant as this morning's standup update. Neither should quietly
        fall out of context just because the room outgrew a single page.
        """
        link = make_link_mock(
            history_pages=[
                [ctx_item("hist-1", content="the project codename is NIGHTHAWK")],
                [ctx_item("hist-2", content="today's standup moved to 3pm")],
            ],
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(msg_body())

        history_content = [m["content"] for m in adapter.received_input.history.raw]
        assert "the project codename is NIGHTHAWK" in history_content
        assert "today's standup moved to 3pm" in history_content
        assert "history_truncated" not in result

    async def test_room_active_for_months_gets_a_prompt_reply_not_a_full_replay(
        self,
    ) -> None:
        """A room that's been running for months can carry far more history
        than the retention window holds. The agent still has to answer
        promptly, so it keeps only the trailing pages rather than replaying
        the entire backlog on every message — and says so honestly instead
        of presenting a partial conversation as the whole thing.

        Critically, "partial" must mean the *most recent* days, not the
        oldest ones — the endpoint is oldest-first with no way to request
        newest-first, so pagination has to walk to the true end and evict
        old pages, never stop early and keep the stale ones.
        """
        months_of_daily_updates = [
            [ctx_item(f"hist-{day}", content=f"day {day} update")] for day in range(5)
        ]
        link = make_link_mock(
            history_pages=months_of_daily_updates,
            next_messages=[platform_msg("msg-1"), None],
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter, history_page_cap=3)

        result = await invoker.handle_event(msg_body())

        get_context = link.rest.agent_api_context.get_agent_chat_context
        assert get_context.await_count == 5  # walked to the true end, not the cap

        history_content = [m["content"] for m in adapter.received_input.history.raw]
        assert history_content == [
            "day 2 update",
            "day 3 update",
            "day 4 update",
        ]
        assert result["history_truncated"] is True

    async def test_first_page_fetch_failure_is_reported_as_truncated(self) -> None:
        """A page-0 fetch failure is the worst case of "history incomplete" —
        the LLM runs with zero history instead of some. It must not be
        reported identically to a fully successful fetch.
        """
        link = make_link_mock(next_messages=[platform_msg("msg-1"), None])
        link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
            side_effect=RuntimeError("context endpoint unavailable")
        )
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter)

        result = await invoker.handle_event(msg_body())

        assert adapter.received_input.history.raw == []
        assert result["history_truncated"] is True

    async def test_has_more_without_a_next_cursor_stops_instead_of_looping(
        self,
    ) -> None:
        """``has_more=True`` with no ``next_cursor`` is a backend contract
        violation the response type doesn't rule out (``next_cursor`` is
        ``Optional``). Re-requesting with ``cursor=None`` would just refetch
        page 0 forever (until the page cap) and duplicate its items — this
        must instead stop after one page and say so honestly.
        """
        link = make_link_mock(next_messages=[platform_msg("msg-1"), None])
        malformed_response = GetAgentChatContextResponse(
            data=[ctx_item("hist-1", content="page with a broken cursor contract")],
            metadata=GetAgentChatContextResponseMetadata(
                has_more=True,
                limit=50,
                next_cursor=None,
            ),
        )
        get_context = AsyncMock(return_value=malformed_response)
        link.rest.agent_api_context.get_agent_chat_context = get_context
        adapter = make_adapter_mock()
        invoker = await make_invoker(link, adapter, history_page_cap=5)

        result = await invoker.handle_event(msg_body())

        get_context.assert_awaited_once()
        history_content = [m["content"] for m in adapter.received_input.history.raw]
        assert history_content.count("page with a broken cursor contract") == 1
        assert result["history_truncated"] is True
