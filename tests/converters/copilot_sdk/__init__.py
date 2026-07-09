"""Tests for CopilotSDKHistoryConverter, one file per test class.

Tests for shared converter behavior (user messages, agent filtering, empty
history, edge cases, output shape) live in
tests/framework_conformance/test_converter_conformance.py.
This package contains CopilotSDK-specific multi-message joining, tool event
handling, session ID extraction, and mixed history integration tests.
"""
