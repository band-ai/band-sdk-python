"""EmptyCandidates behavior for Gemini."""

from __future__ import annotations

from unittest.mock import MagicMock

from band.adapters.gemini import GeminiAdapter


class TestEmptyCandidates:
    def test_extract_candidate_content_returns_none_for_empty_response(self):
        adapter = GeminiAdapter()
        response = MagicMock()
        response.candidates = []
        response.function_calls = []

        content = adapter._extract_candidate_content(response)
        assert content is None

    def test_extract_candidate_content_returns_none_when_no_candidates(self):
        adapter = GeminiAdapter()
        response = MagicMock()
        response.candidates = None
        response.function_calls = []

        content = adapter._extract_candidate_content(response)
        assert content is None
