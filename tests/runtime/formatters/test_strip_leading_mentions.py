from __future__ import annotations

from band.runtime.formatters import (
    strip_leading_mentions,
)


class TestStripLeadingMentions:
    """A delivered room reply arrives with the platform's ``@handle`` mention
    block prepended; these pin that it is dropped so a terse command/answer
    parses from the text after it, without disturbing the rest."""

    def test_strips_the_platform_injected_leading_mention(self):
        # The exact shape a mentioned "approve <id>" reply reaches on_message as.
        assert (
            strip_leading_mentions("@alexander.zaikman/tom approve REQ-Aa1")
            == "approve REQ-Aa1"
        )

    def test_strips_a_run_of_leading_mentions(self):
        # Greedy (default) mode, used for command detection: a command never IS
        # an @token, so skipping the whole leading block only helps matching --
        # e.g. a human doubling the mention inline (metadata + typed token).
        assert strip_leading_mentions("@team/bot @team/bot reject") == "reject"

    def test_only_first_preserves_an_at_handle_answer(self):
        # Free-text answers use only_first: an answer that legitimately begins
        # with an @handle (naming a person) must survive -- greedy would eat it.
        assert strip_leading_mentions("@team/bot @alice", only_first=True) == "@alice"
        assert (
            strip_leading_mentions("@team/bot @alice review it", only_first=True)
            == "@alice review it"
        )

    def test_only_first_strips_the_single_delivery_mention(self):
        assert strip_leading_mentions("@team/bot ship it", only_first=True) == "ship it"

    def test_preserves_a_reply_with_no_leading_mention(self):
        # Bare replies (no delivery mention) must pass through untouched.
        assert strip_leading_mentions("approve req-1") == "approve req-1"

    def test_preserves_newlines_after_the_block(self):
        # A multi-question answer is one line per question -- the block strip
        # must not flatten the answer lines behind it.
        assert (
            strip_leading_mentions("@team/bot yes please\nno thanks")
            == "yes please\nno thanks"
        )

    def test_ignores_a_mention_that_is_not_at_the_start(self):
        assert strip_leading_mentions("ping @team/bot later") == "ping @team/bot later"

    def test_strips_a_mention_only_reply(self):
        assert strip_leading_mentions("@team/bot") == ""

    def test_strips_the_platforms_normalized_uuid_mention(self):
        # The platform prepends its mentions as @[[uuid]]. replace_uuid_mentions()
        # rewrites those to @handle first, but only for participants it can
        # resolve -- an unresolved one reaches the parsers in this raw form.
        assert (
            strip_leading_mentions(
                "@[[3029eb1d-d998-4567-bdf3-d82fc6b89a58]] /approve req-1"
            )
            == "/approve req-1"
        )

    def test_strips_the_platform_block_ahead_of_a_typed_handle(self):
        # Typing "@handle" is not the normalized form, so the platform still
        # prepends its own @[[uuid]] and the message carries both tokens. (It
        # skips the prepend only when the content already leads with the
        # @[[uuid]] itself -- the case the test above covers.)
        assert (
            strip_leading_mentions("@[[uuid-1]] @owner/support-b /approve req-1")
            == "/approve req-1"
        )

    def test_a_multi_word_display_name_never_reaches_the_content(self):
        # Handles are slugified and truncated, so an agent displayed as
        # "Support Bot Probe" is mentioned as one whitespace-free token. This is
        # what makes matching on @\S+ sufficient: no display name can survive
        # the block and be misread as the start of the message body.
        assert (
            strip_leading_mentions(
                "@alexander.zaikman/e2e-band-0d453-support-b approve"
            )
            == "approve"
        )
