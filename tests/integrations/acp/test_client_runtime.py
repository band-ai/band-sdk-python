"""Tests for ACP runtime and client profiles."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acp.client.connection import ClientSideConnection
from acp.exceptions import RequestError

from band.integrations.acp.client_profiles import (
    CursorACPClientProfile,
    NoopACPClientProfile,
)
from band.integrations.acp.client_runtime import (
    ACP_STDIO_LIMIT_BYTES,
    ACPCollectingClient,
    ACPRuntime,
    select_allow_option_id,
    tcp_spawn_process,
)
from band.integrations.acp.types import CollectedChunk


class TestSelectAllowOptionId:
    def test_prefers_allow_once_over_allow_always(self) -> None:
        options = [
            {"kind": "allow_always", "optionId": "always"},
            {"kind": "allow_once", "optionId": "once"},
        ]
        assert select_allow_option_id(options) == "once"

    def test_no_allow_option_returns_none(self) -> None:
        assert (
            select_allow_option_id([{"kind": "reject_once", "optionId": "no"}]) is None
        )

    def test_present_but_empty_option_id_is_not_dropped(self) -> None:
        """An explicit (if empty) optionId must not fall through to the snake_case
        alias and get dropped — coalesce on absence, not falsiness."""
        assert select_allow_option_id([{"kind": "allow_once", "optionId": ""}]) == ""


class TestACPCollectingClientProfiles:
    """Tests for ACP collecting client profile delegation."""

    @pytest.mark.asyncio
    async def test_noop_profile_ignores_extensions(self) -> None:
        client = ACPCollectingClient(profile=NoopACPClientProfile())

        method_result = await client.ext_method("unknown/method", {})
        await client.ext_notification("unknown/notification", {"sessionId": "sess-1"})

        assert method_result == {}
        assert client.get_collected_chunks("sess-1") == []

    @pytest.mark.asyncio
    async def test_cursor_profile_handles_methods_and_notifications(self) -> None:
        client = ACPCollectingClient(profile=CursorACPClientProfile())

        ask_result = await client.ext_method(
            "cursor/ask_question",
            {
                "options": [
                    {"optionId": "a", "name": "Option A"},
                    {"optionId": "b", "name": "Option B"},
                ]
            },
        )
        plan_result = await client.ext_method("cursor/create_plan", {"plan": "x"})
        await client.ext_notification(
            "cursor/update_todos",
            {
                "sessionId": "sess-1",
                "todos": [
                    {"content": "Read code", "completed": True},
                    {"content": "Write tests", "completed": False},
                ],
            },
        )
        await client.ext_notification(
            "cursor/task",
            {"sessionId": "sess-1", "result": "Refactored the module"},
        )

        chunks = client.get_collected_chunks("sess-1")
        assert ask_result == {"outcome": {"type": "selected", "optionId": "a"}}
        assert plan_result == {"outcome": {"type": "approved"}}
        assert [chunk.chunk_type for chunk in chunks] == ["plan", "text"]
        assert "[x] Read code" in chunks[0].content
        assert "Refactored the module" in chunks[1].content


class TestACPCollectingClientCoalescing:
    """Streamed text/thought deltas are coalesced into one chunk per run."""

    @staticmethod
    def _update(kind: str, text: str) -> MagicMock:
        u = MagicMock(session_update=kind)
        u.content = MagicMock(text=text)
        return u

    @pytest.mark.asyncio
    async def test_consecutive_text_deltas_merge_into_one_chunk(self) -> None:
        client = ACPCollectingClient()
        for part in ("The weather ", "is ", "sunny."):
            await client.session_update("s1", self._update("agent_message_chunk", part))
        await client.flush("s1")  # turn end finalizes the still-open run

        chunks = client.get_collected_chunks("s1")
        assert [c.chunk_type for c in chunks] == ["text"]  # one chunk, not three
        assert chunks[0].content == "The weather is sunny."
        assert client.get_collected_text("s1") == "The weather is sunny."

    @pytest.mark.asyncio
    async def test_a_tool_call_splits_text_runs(self) -> None:
        client = ACPCollectingClient()
        await client.session_update(
            "s1", self._update("agent_message_chunk", "before ")
        )
        await client.session_update("s1", MagicMock(session_update="tool_call"))
        await client.session_update("s1", self._update("agent_message_chunk", "after"))
        await client.flush("s1")  # turn end finalizes the trailing text run

        kinds = [c.chunk_type for c in client.get_collected_chunks("s1")]
        assert kinds == [
            "text",
            "tool_call",
            "text",
        ]  # runs on either side stay distinct

    @pytest.mark.asyncio
    async def test_tool_result_falls_back_to_content_blocks_when_raw_output_unset(
        self,
    ) -> None:
        """Some agents (Copilot) report output only via ``content``, not ``rawOutput``."""
        client = ACPCollectingClient()
        text_block = MagicMock(text="production, development, test")
        content_item = MagicMock(type="content", content=text_block)
        diff_item = MagicMock(type="diff", path="/tmp/x", new_text="ignored")
        tool_result = MagicMock(
            session_update="tool_call_update",
            tool_call_id="tc-env",
            raw_output=None,
            content=[content_item, diff_item],
            status="completed",
        )

        await client.session_update("s1", tool_result)

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].chunk_type == "tool_result"
        # Only the "content"-typed block contributes text; the diff entry,
        # despite being a MagicMock that would auto-vivify a `.content`
        # attribute, is excluded by its explicit type tag.
        assert chunks[0].content == "production, development, test"

    @pytest.mark.asyncio
    async def test_tool_result_prefers_structured_content_over_duplicated_text(
        self,
    ) -> None:
        """Copilot fronting a FastMCP primitive-typed tool forwards both the
        readable text *and* its structuredContent companion (MCP's own
        ``{"result": ...}`` primitive-output wrap) concatenated into one text
        block -- duplicating a JSON-serialized string across them. The clean,
        least-processed copy sits in ``rawOutput.structuredContent.result``;
        prefer it over the duplicated content block."""
        client = ACPCollectingClient()
        clean = '{\n  "id": "msg-1",\n  "recipients": [{"handle": "pat"}],\n  "success": true\n}'
        duplicated_text = clean + "\n\n" + json.dumps({"result": clean})
        text_block = MagicMock(text=duplicated_text)
        content_item = MagicMock(type="content", content=text_block)
        tool_result = MagicMock(
            session_update="tool_call_update",
            tool_call_id="tc-msg",
            raw_output={
                "content": duplicated_text,
                "detailedContent": duplicated_text,
                "contents": [{"type": "text", "text": clean}],
                "structuredContent": {"result": clean},
            },
            content=[content_item],
            status="completed",
        )

        await client.session_update("s1", tool_result)

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == clean

    @pytest.mark.asyncio
    async def test_tool_result_strips_dict_structured_echo(self) -> None:
        """A dict-returning MCP tool (e.g. the SDK's LocalMCPServer) surfaces as
        structuredContent = the result object itself, and the bridge appends its
        compact re-serialization after the readable rendering -- the second
        verified echo shape (captured live from Copilot + LocalMCPServer). Only
        the readable leading copy should reach the room."""
        client = ACPCollectingClient()
        payload = {"id": "0bf0c2d3", "message_type": "thought", "success": True}
        readable = json.dumps(payload, indent=2)
        duplicated = readable + "\n\n" + json.dumps(payload, separators=(",", ":"))
        text_block = MagicMock(text=duplicated)
        content_item = MagicMock(type="content", content=text_block)
        tool_result = MagicMock(
            session_update="tool_call_update",
            tool_call_id="tc-msg",
            raw_output={"structuredContent": payload},
            content=[content_item],
            status="completed",
        )

        await client.session_update("s1", tool_result)

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == readable

    @pytest.mark.asyncio
    async def test_tool_result_keeps_trailing_json_that_is_not_the_echo(self) -> None:
        """Trailing JSON that does not re-encode structuredContent is real data,
        not the bridge's echo -- content stays untouched."""
        client = ACPCollectingClient()
        payload = {"id": "0bf0c2d3", "success": True}
        content = json.dumps(payload, indent=2) + "\n\n" + '{"other": "data"}'
        text_block = MagicMock(text=content)
        content_item = MagicMock(type="content", content=text_block)
        tool_result = MagicMock(
            session_update="tool_call_update",
            tool_call_id="tc-msg",
            raw_output={"structuredContent": payload},
            content=[content_item],
            status="completed",
        )

        await client.session_update("s1", tool_result)

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == content

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content", "result"),
        [
            # Distinct human summary; the structured value appears nowhere in it.
            ("Created message successfully", '{"id": "msg-1"}'),
            # Prose that merely QUOTES the structured value -- containment is
            # not the echo signature (no trailing {"result": ...} wrapper).
            ('Created resource: {"id": 1}', '{"id": 1}'),
            # Primitive JSON as an accidental prefix of ordinary prose.
            ("true story: all checks passed", "true"),
            # An empty result string renders nothing -- every string "starts
            # with" it, so it proves no duplication (one copy, not two).
            ('{"result":""}', ""),
        ],
    )
    async def test_tool_result_leaves_distinct_human_content_untouched(
        self, content: str, result: str
    ) -> None:
        """A structuredContent shaped like the primitive-output wrap is not proof
        of duplication by itself, and neither is the value appearing inside the
        content. Only the full echo signature (the value followed by its own
        serialized {"result": ...} wrapper) may replace a bridge's human-facing
        content."""
        client = ACPCollectingClient()
        text_block = MagicMock(text=content)
        content_item = MagicMock(type="content", content=text_block)
        tool_result = MagicMock(
            session_update="tool_call_update",
            tool_call_id="tc-msg",
            raw_output={"structuredContent": {"result": result}},
            content=[content_item],
            status="completed",
        )

        await client.session_update("s1", tool_result)

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == content

    @pytest.mark.asyncio
    async def test_tool_result_keeps_content_on_coerced_type_mismatch(self) -> None:
        """JSON equality for the echo proof is type-strict: Python's
        ``True == 1`` coercion must not let two genuinely different payloads
        pass as duplicates."""
        client = ACPCollectingClient()
        content = '{"success":true}\n{"success":1}'
        text_block = MagicMock(text=content)
        content_item = MagicMock(type="content", content=text_block)
        tool_result = MagicMock(
            session_update="tool_call_update",
            tool_call_id="tc-msg",
            raw_output={"structuredContent": {"success": 1}},
            content=[content_item],
            status="completed",
        )

        await client.session_update("s1", tool_result)

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == content

    @pytest.mark.asyncio
    async def test_fold_only_recognizes_the_proven_echo_shape(self) -> None:
        """The re-report guard compares against the echo payload that was
        actually proven, never a guessed encoding. Frame 1 proved the wrap
        shape ({"result": <value>}); a later frame whose trailing JSON is the
        value itself re-encoded is NOT that duplicate -- it is new content and
        must win per the usual latest-readable rule."""
        client = ACPCollectingClient()
        clean = '{"id":1}'
        proven_echo = clean + "\n\n" + json.dumps({"result": clean})
        unproven_lookalike = clean + "\n" + clean

        def frame(text: str, raw_output: object, status: str) -> MagicMock:
            text_block = MagicMock(text=text)
            content_item = MagicMock(type="content", content=text_block)
            return MagicMock(
                session_update="tool_call_update",
                tool_call_id="tc-msg",
                raw_output=raw_output,
                content=[content_item],
                status=status,
            )

        await client.session_update(
            "s1",
            frame(proven_echo, {"structuredContent": {"result": clean}}, "in_progress"),
        )
        await client.session_update("s1", frame(unproven_lookalike, None, "completed"))

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == unproven_lookalike

    @pytest.mark.asyncio
    async def test_cleaned_tool_result_is_not_regressed_by_a_re_reported_duplicate(
        self,
    ) -> None:
        """A frame already cleaned by _unwrap_structured_result must not be
        overwritten by a later frame re-reporting the same proven duplicate
        without its structured proof (e.g. its rawOutput took a different
        shape). "Latest readable wins" is for genuine progress replacement (see
        test_latest_readable_tool_result_replaces_longer_progress), not for
        regressing an already-verified-clean value."""
        client = ACPCollectingClient()
        clean = '{"id": "msg-1"}'
        proven_echo = clean + "\n\n" + json.dumps({"result": clean})

        def frame(text: str, raw_output: object, status: str) -> MagicMock:
            text_block = MagicMock(text=text)
            content_item = MagicMock(type="content", content=text_block)
            return MagicMock(
                session_update="tool_call_update",
                tool_call_id="tc-msg",
                raw_output=raw_output,
                content=[content_item],
                status=status,
            )

        await client.session_update(
            "s1",
            frame(proven_echo, {"structuredContent": {"result": clean}}, "in_progress"),
        )
        await client.session_update(
            "s1", frame(proven_echo, {"other": "shape"}, "completed")
        )

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == clean

    @pytest.mark.asyncio
    async def test_later_final_result_replaces_cleaned_progress(self) -> None:
        """A cleaned progress frame is not sacred: a later frame carrying a
        genuinely new readable result (not a re-report of the same echo) must
        still replace it, per the usual latest-readable-wins rule. Only the
        same duplicate re-reported without its structured proof is held off
        (see test_cleaned_tool_result_is_not_regressed_by_a_re_reported_duplicate).
        """
        client = ACPCollectingClient()
        progress = '{"status": "creating"}'
        progress_echo = progress + "\n\n" + json.dumps({"result": progress})

        def frame(text: str, raw_output: object, status: str) -> MagicMock:
            text_block = MagicMock(text=text)
            content_item = MagicMock(type="content", content=text_block)
            return MagicMock(
                session_update="tool_call_update",
                tool_call_id="tc-msg",
                raw_output=raw_output,
                content=[content_item],
                status=status,
            )

        await client.session_update(
            "s1",
            frame(
                progress_echo,
                {"structuredContent": {"result": progress}},
                "in_progress",
            ),
        )
        await client.session_update(
            "s1", frame("Created msg-1 successfully.", None, "completed")
        )

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == "Created msg-1 successfully."

    @pytest.mark.asyncio
    async def test_tool_result_keeps_content_when_structured_content_is_richer(
        self,
    ) -> None:
        """A ``structuredContent`` that isn't the single-key primitive-output
        wrap (``{"result": <json string>}``) may be a genuinely distinct,
        richer payload -- leave the content blocks alone."""
        client = ACPCollectingClient()
        text_block = MagicMock(text="Found 3 matching rooms.")
        content_item = MagicMock(type="content", content=text_block)
        tool_result = MagicMock(
            session_update="tool_call_update",
            tool_call_id="tc-search",
            raw_output={
                "structuredContent": {"rooms": ["room-1", "room-2", "room-3"]},
            },
            content=[content_item],
            status="completed",
        )

        await client.session_update("s1", tool_result)

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == "Found 3 matching rooms."

    @pytest.mark.asyncio
    async def test_tool_result_is_blank_when_neither_raw_output_nor_content_present(
        self,
    ) -> None:
        """A terminal- or diff-only tool_call_update has no text to surface — this
        is the case the send_event blank-content guard exists to catch."""
        client = ACPCollectingClient()
        tool_result = MagicMock(
            session_update="tool_call_update",
            tool_call_id="tc-terminal",
            raw_output=None,
            content=None,
            status="completed",
        )

        await client.session_update("s1", tool_result)

        chunks = client.get_collected_chunks("s1")
        assert chunks[0].content == ""

    @pytest.mark.asyncio
    async def test_latest_readable_tool_result_replaces_longer_progress(self) -> None:
        """ACP content is replacement content, not an accumulating progress value."""
        client = ACPCollectingClient()

        def result(content: str, status: str) -> MagicMock:
            text_block = MagicMock(text=content)
            content_item = MagicMock(type="content", content=text_block)
            return MagicMock(
                session_update="tool_call_update",
                tool_call_id="tc-1",
                raw_output=None,
                content=[content_item],
                status=status,
            )

        await client.session_update(
            "s1", result("Command still running...", "in_progress")
        )
        await client.session_update("s1", result("OK", "completed"))

        chunks = client.get_collected_chunks("s1")
        assert len(chunks) == 1
        assert chunks[0].content == "OK"

    @pytest.mark.asyncio
    async def test_tool_result_posts_once_and_is_not_re_emitted_on_revision(
        self,
    ) -> None:
        """A post-terminal content revision folds into the buffer but never re-posts.

        The room event is posted once, at the first terminal frame (live, causal
        order); the events API is append-only, so a later frame that revises the
        content can neither edit that event nor be re-posted without duplicating the
        narration. This pins that trade-off — and guards against a well-meaning
        "re-emit on change" fix that would double-post the tool_result.
        """
        client = ACPCollectingClient()
        # Snapshot what the sink RECEIVES at call time (a string), mirroring
        # send_event(content=chunk.content, ...). Holding the chunk itself would
        # instead show the value a later fold mutates in — exactly what must not
        # reach the already-posted room event.
        posted: list[tuple[str, str]] = []

        async def record(chunk: CollectedChunk) -> None:
            posted.append((chunk.chunk_type, chunk.content))

        client.set_sink("s1", record)

        def result(raw_output: str) -> MagicMock:
            return MagicMock(
                session_update="tool_call_update",
                tool_call_id="tc-1",
                raw_output=raw_output,
                content=None,
                status="completed",
            )

        await client.session_update("s1", result("partial result"))
        # A later terminal frame for the same call carrying fuller content.
        await client.session_update("s1", result("partial result plus more"))
        await client.flush("s1")  # turn end must not re-emit the already-posted call

        # Posted to the room exactly once, with the first-terminal content.
        assert posted == [("tool_result", "partial result")]
        # The revision still folds into the buffer (history/tests see the newest).
        assert (
            client.get_collected_chunks("s1")[-1].content == "partial result plus more"
        )


class ConcurrencyProbe:
    """An async context manager that records the peak number of tasks inside it.

    Entering yields control once — as a real REST post would — so any
    concurrently dispatched task gets the chance to enter and be counted.
    A serialized code path keeps ``peak`` at 1.
    """

    def __init__(self) -> None:
        self._active = 0
        self.peak = 0

    async def __aenter__(self) -> None:
        self._active += 1
        self.peak = max(self.peak, self._active)
        await asyncio.sleep(0)

    async def __aexit__(self, *exc: object) -> None:
        self._active -= 1


class TestACPCollectingClientSerialization:
    """The acp transport runs each notification as its own task; the client must
    serialize the per-session ingest→sink path so room posts keep causal order,
    and must not lose a sink failure (the transport suppresses handler
    exceptions without a trace)."""

    @pytest.fixture
    def probe(self) -> ConcurrencyProbe:
        return ConcurrencyProbe()

    @staticmethod
    def _tool_call(title: str, call_id: str) -> MagicMock:
        return MagicMock(
            session_update="tool_call",
            title=title,
            tool_call_id=call_id,
            raw_input=None,
            status="in_progress",
        )

    @pytest.mark.asyncio
    async def test_concurrently_dispatched_updates_post_in_arrival_order(
        self, probe: ConcurrencyProbe
    ) -> None:
        """Notifications dispatched as concurrent tasks (the acp dispatcher's
        behavior) must post to the sink one at a time, in wire-arrival order —
        overlapping posts are concurrent REST calls that can commit out of order
        in the room."""
        client = ACPCollectingClient()
        posted: list[str] = []

        async def sink(chunk: CollectedChunk) -> None:
            async with probe:
                posted.append(chunk.content)

        client.set_sink("s1", sink)

        # Mimic DefaultMessageDispatcher._dispatch_notification: one task per
        # notification, created in wire-arrival order.
        await asyncio.gather(
            *(
                asyncio.create_task(
                    client.session_update("s1", self._tool_call(f"tool-{i}", f"tc-{i}"))
                )
                for i in range(3)
            )
        )

        assert probe.peak == 1  # posts never overlap
        assert posted == ["tool-0", "tool-1", "tool-2"]  # arrival order preserved

    @pytest.mark.asyncio
    async def test_permission_handling_never_interleaves_with_narration_posts(
        self, probe: ConcurrencyProbe
    ) -> None:
        """The permission handler may post a denied tool_call/tool_result pair to
        the room; it must not interleave with in-flight narration posts."""
        client = ACPCollectingClient()

        async def sink(chunk: CollectedChunk) -> None:
            async with probe:
                pass

        async def handler(**kwargs: object) -> dict[str, object]:
            async with probe:
                return {"outcome": {"outcome": "cancelled"}}

        client.set_sink("s1", sink)
        client.set_permission_handler("s1", handler)

        await asyncio.gather(
            client.session_update("s1", self._tool_call("tool-0", "tc-0")),
            client.request_permission(
                options=[], session_id="s1", tool_call=MagicMock()
            ),
        )

        assert probe.peak == 1

    @pytest.mark.asyncio
    async def test_sink_failure_is_logged_and_keeps_the_chunk_buffered(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failed room post must not vanish: the acp transport suppresses
        notification-handler exceptions silently, so the client logs the failure
        itself and keeps ingesting (the chunk stays buffered for turn-end
        collection)."""
        client = ACPCollectingClient()

        async def sink(chunk: CollectedChunk) -> None:
            raise RuntimeError("REST post failed")

        client.set_sink("s1", sink)

        with caplog.at_level(logging.ERROR):
            await client.session_update("s1", self._tool_call("tool-0", "tc-0"))

        chunks = client.get_collected_chunks("s1")
        assert [chunk.content for chunk in chunks] == ["tool-0"]
        assert "Failed to post" in caplog.text
        assert "REST post failed" in caplog.text


class TestACPRuntime:
    """Tests for ACP runtime subprocess orchestration."""

    @pytest.mark.asyncio
    async def test_start_initializes_connection_and_authenticates(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(
            return_value=MagicMock(
                agent_capabilities=MagicMock(
                    load_session=True,
                    mcp_capabilities=MagicMock(http=False, sse=True),
                )
            )
        )
        mock_conn.authenticate = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(mock_conn, MagicMock()))
        runtime = ACPRuntime(
            command=["codex"],
            auth_method="cursor_login",
            spawn_process=lambda *args, **kwargs: mock_ctx,
        )

        await runtime.start()

        assert runtime._conn is mock_conn
        assert runtime._agent_mcp_transport == "sse"
        assert runtime._agent_supports_session_load
        mock_conn.initialize.assert_awaited_once_with(protocol_version=1)
        mock_conn.authenticate.assert_awaited_once_with(method_id="cursor_login")

    @pytest.mark.asyncio
    async def test_create_session_and_prompt_use_active_connection(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.new_session = AsyncMock(return_value=MagicMock(session_id="sess-1"))
        mock_conn.prompt = AsyncMock()
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._client = ACPCollectingClient()
        runtime._client._session_chunks["sess-1"] = []

        session_id = await runtime.create_session(cwd="/tmp", mcp_servers=[])
        chunks = await runtime.prompt(session_id=session_id, prompt_text="hello")

        assert session_id == "sess-1"
        assert chunks == []
        mock_conn.new_session.assert_awaited_once_with(cwd="/tmp", mcp_servers=[])
        mock_conn.prompt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_load_session_uses_only_a_declared_capability(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(return_value=MagicMock())
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn

        assert not await runtime.load_session(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )
        mock_conn.load_session.assert_not_awaited()

        runtime._agent_supports_session_load = True
        assert await runtime.load_session(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )
        mock_conn.load_session.assert_awaited_once_with(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )

    @pytest.mark.asyncio
    async def test_load_session_handles_an_unavailable_persisted_session(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(
            side_effect=RequestError(-32002, "Session sess-1 not found")
        )
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._agent_supports_session_load = True

        assert not await runtime.load_session(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )

    @pytest.mark.asyncio
    async def test_load_session_timeout_is_treated_as_unavailable(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(side_effect=TimeoutError)
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._agent_supports_session_load = True

        with patch(
            "band.integrations.acp.client_runtime.ACP_SESSION_LOAD_TIMEOUT_SECONDS",
            0.01,
        ):
            assert not await runtime.load_session(
                cwd="/tmp", session_id="sess-1", mcp_servers=[]
            )

    @pytest.mark.asyncio
    async def test_load_session_treats_non_session_errors_as_miss(self) -> None:
        """Any protocol error on session/load is a recoverable load-miss: the
        caller falls back to a fresh session instead of the turn dying."""
        mock_conn = AsyncMock()
        mock_conn.load_session = AsyncMock(side_effect=RequestError.invalid_params())
        runtime = ACPRuntime(command=["codex"])
        runtime._conn = mock_conn
        runtime._agent_supports_session_load = True

        loaded = await runtime.load_session(
            cwd="/tmp", session_id="sess-1", mcp_servers=[]
        )

        assert loaded is False

    @pytest.mark.asyncio
    async def test_start_cleans_up_failed_initialize(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(side_effect=RuntimeError("boom"))
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(mock_conn, MagicMock()))
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        runtime = ACPRuntime(
            command=["codex"],
            spawn_process=lambda *args, **kwargs: mock_ctx,
        )

        with pytest.raises(RuntimeError, match="boom"):
            await runtime.start()

        assert runtime._conn is None
        assert runtime._ctx is None
        mock_ctx.__aexit__.assert_awaited_once_with(None, None, None)

    @pytest.mark.asyncio
    async def test_start_cleans_up_failed_authenticate(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(return_value=MagicMock())
        mock_conn.authenticate = AsyncMock(side_effect=RuntimeError("auth failed"))
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(mock_conn, MagicMock()))
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        runtime = ACPRuntime(
            command=["codex"],
            auth_method="cursor_login",
            spawn_process=lambda *args, **kwargs: mock_ctx,
        )

        with pytest.raises(RuntimeError, match="auth failed"):
            await runtime.start()

        assert runtime._conn is None
        assert runtime._ctx is None
        mock_ctx.__aexit__.assert_awaited_once_with(None, None, None)

    @pytest.mark.asyncio
    async def test_ensure_connection_respawns_when_allowed(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.initialize = AsyncMock(return_value=MagicMock())
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=(mock_conn, MagicMock()))
        runtime = ACPRuntime(
            command=["codex"],
            spawn_process=lambda *args, **kwargs: mock_ctx,
        )

        conn = await runtime.ensure_connection(can_respawn=True)

        assert conn is mock_conn
        mock_conn.initialize.assert_awaited_once_with(protocol_version=1)

    @pytest.mark.asyncio
    async def test_set_permission_handler_delegates_to_client(self) -> None:
        runtime = ACPRuntime(command=["codex"])
        runtime._client = ACPCollectingClient()
        handler = AsyncMock(
            return_value={"outcome": {"outcome": "selected", "optionId": "p-once"}}
        )

        runtime.set_permission_handler("sess-1", handler)
        runtime.reset_session("sess-2")

        assert runtime._client._permission_handlers["sess-1"] is handler
        assert "sess-2" not in runtime._client._permission_handlers

    @pytest.mark.asyncio
    async def test_stop_exits_context_and_clears_state(self) -> None:
        mock_ctx = AsyncMock()
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        runtime = ACPRuntime(command=["codex"])
        runtime._ctx = mock_ctx
        runtime._conn = AsyncMock()
        runtime._client = ACPCollectingClient()

        await runtime.stop()

        assert runtime._ctx is None
        assert runtime._conn is None
        assert runtime._client is None
        mock_ctx.__aexit__.assert_awaited_once_with(None, None, None)

    @pytest.mark.asyncio
    async def test_start_with_empty_command_forwards_no_positional_command(
        self, make_acp_transport
    ) -> None:
        """TCP transports pass command=[] — the runtime must forward no positional
        command args (host/port live in the injected spawn_process)."""
        transport = make_acp_transport()
        runtime = ACPRuntime(command=[], spawn_process=transport)

        await runtime.start()

        args, kwargs = transport.last_call
        assert args == ()  # no executable/args splatted for a connect-only transport
        assert kwargs["transport_kwargs"] == {"limit": ACP_STDIO_LIMIT_BYTES}


class TestTCPSpawnProcess:
    """Tests for the TCP connect-only spawn_process seam.

    Uses a real loopback server (no patching): the factory must open a socket,
    build a live ACP connection over it, yield ``(conn, writer)``, and close both
    on exit — ignoring the subprocess-shaped args the runtime forwards.
    """

    @pytest.mark.asyncio
    async def test_connects_and_cleans_up(self) -> None:
        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.read()  # hold the connection until the client closes
            finally:
                writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        async with server:
            spawn = tcp_spawn_process(host, port)
            client = ACPCollectingClient()

            # Forward subprocess-shaped args the runtime would pass; TCP ignores them.
            cm = spawn(
                client,
                "ignored-executable",
                "ignored-arg",
                env=None,
                transport_kwargs={"limit": 1024},
            )
            conn, writer = await cm.__aenter__()
            try:
                assert isinstance(writer, asyncio.StreamWriter)
                # A real ClientSideConnection built from the socket, not a mock.
                assert isinstance(conn, ClientSideConnection)
            finally:
                await cm.__aexit__(None, None, None)

            assert writer.is_closing()

    @pytest.mark.asyncio
    async def test_cleans_up_when_body_raises(self) -> None:
        """The connect CM must close the transport even if the caller raises
        mid-session (e.g. initialize fails) — the `finally` path, not just happy exit."""

        async def _handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await reader.read()
            finally:
                writer.close()

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]

        async with server:
            spawn = tcp_spawn_process(host, port)
            captured: dict[str, asyncio.StreamWriter] = {}

            with pytest.raises(RuntimeError, match="boom"):
                async with spawn(ACPCollectingClient()) as (_conn, writer):
                    captured["writer"] = writer
                    raise RuntimeError("boom")

            assert captured["writer"].is_closing()
