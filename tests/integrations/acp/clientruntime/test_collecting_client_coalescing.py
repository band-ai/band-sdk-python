"""How the ACP collecting client coalesces and folds streamed updates."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from band.integrations.acp.client_runtime import ACPCollectingClient
from band.integrations.acp.types import CollectedChunk
from tests.integrations.acp.acp_toolkit.sink import NarrationSink


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

        client.set_sink("s1", NarrationSink(record))

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
