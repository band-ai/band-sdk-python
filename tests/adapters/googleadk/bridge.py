"""Resolve the optional ADK adapter and its bridge for focused tests."""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("google.adk", reason="google-adk not installed")

_google_adk_mod = importlib.import_module("band.adapters.google_adk")
GoogleADKAdapter = _google_adk_mod.GoogleADKAdapter
GoogleADKAdapterConfig = _google_adk_mod.GoogleADKAdapterConfig
_get_tool_bridge_class = _google_adk_mod._get_tool_bridge_class
_BandToolBridge = _get_tool_bridge_class()
_sanitize_adk_agent_name = _google_adk_mod._sanitize_adk_agent_name
_google_llm = importlib.import_module("google.adk.models.google_llm")
