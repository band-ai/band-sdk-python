from __future__ import annotations

from band.runtime.formatters import (
    replace_uuid_mentions,
    strip_leading_mentions,
)


class TestReplaceUuidMentions:
    def test_replaces_single_uuid_mention(self):
        content = "Hey @[[550e8400-e29b-41d4-a716-446655440000]], check this"
        participants = [
            {"id": "550e8400-e29b-41d4-a716-446655440000", "handle": "john"}
        ]
        result = replace_uuid_mentions(content, participants)
        assert result == "Hey @john, check this"

    def test_replaces_multiple_uuid_mentions(self):
        content = "Hi @[[uuid1]] and @[[uuid2]]"
        participants = [
            {"id": "uuid1", "handle": "alice"},
            {"id": "uuid2", "handle": "bob"},
        ]
        result = replace_uuid_mentions(content, participants)
        assert result == "Hi @alice and @bob"

    def test_preserves_content_when_no_participants(self):
        content = "Hello @[[some-uuid]]"
        result = replace_uuid_mentions(content, [])
        assert result == "Hello @[[some-uuid]]"

    def test_preserves_unmatched_uuids(self):
        content = "@[[unknown-uuid]] hello"
        participants = [{"id": "different-uuid", "handle": "john"}]
        result = replace_uuid_mentions(content, participants)
        assert result == "@[[unknown-uuid]] hello"

    def test_falls_back_to_name_when_handle_missing(self):
        # ChatParticipant.handle is omitted when unavailable (e.g. a room's
        # implicit human owner) -- a bare @[[uuid]] left for the LLM is
        # unreadable and gets echoed back verbatim, so name is the fallback.
        content = "@[[uuid1]] hello"
        participants = [{"id": "uuid1", "name": "John"}]  # No handle
        result = replace_uuid_mentions(content, participants)
        assert result == "@John hello"

    def test_preserves_uuid_when_neither_handle_nor_name_available(self):
        content = "@[[uuid1]] hello"
        participants = [{"id": "uuid1"}]  # Neither handle nor name
        result = replace_uuid_mentions(content, participants)
        assert result == "@[[uuid1]] hello"  # Nothing to fall back to

    def test_collapses_whitespace_in_multi_word_name_fallback(self):
        # A display name fallback must stay whitespace-free like a real handle
        # -- strip_leading_mentions()'s @\S+ matching assumes no embedded space.
        content = "@[[uuid1]] approve REQ-1"
        participants = [{"id": "uuid1", "name": "Jane Doe"}]  # No handle
        result = replace_uuid_mentions(content, participants)
        assert result == "@Jane-Doe approve REQ-1"
        # The invariant this exists to protect: stripping the mention must
        # never eat into the command that follows it.
        assert strip_leading_mentions(result) == "approve REQ-1"

    def test_handles_empty_content(self):
        result = replace_uuid_mentions("", [{"id": "uuid1", "handle": "john"}])
        assert result == ""

    def test_handles_none_participants(self):
        # Verify behavior when participants is falsy
        content = "Hello @[[uuid1]]"
        result = replace_uuid_mentions(content, [])
        assert result == "Hello @[[uuid1]]"
