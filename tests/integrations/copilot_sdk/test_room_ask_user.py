"""Rendering of ask_user questions as room messages."""

from __future__ import annotations

from band.integrations.copilot_sdk import render_room_question


class TestRenderRoomQuestion:
    def test_choices_render_numbered_with_freeform_hint(self):
        text = render_room_question(
            {
                "question": "Which channel should I deploy to?",
                "choices": ["stable", "beta", "canary"],
                "allowFreeform": True,
            }
        )
        assert text == (
            "Which channel should I deploy to?\n"
            "\n"
            "1. stable\n"
            "2. beta\n"
            "3. canary\n"
            "\n"
            "Reply with a number or your own answer."
        )

    def test_freeform_forbidden_hint_asks_for_a_number(self):
        text = render_room_question(
            {
                "question": "Proceed?",
                "choices": ["yes", "no"],
                "allowFreeform": False,
            }
        )
        assert text.endswith("Reply with the number of one of the options.")

    def test_no_choices_renders_bare_question(self):
        text = render_room_question({"question": "  What codename?  "})
        assert text == "What codename?"

    def test_missing_fields_default_sanely(self):
        assert render_room_question({}) == ""
        assert render_room_question({"question": "Q", "choices": None}) == "Q"
