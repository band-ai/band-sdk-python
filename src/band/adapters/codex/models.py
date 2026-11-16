"""What Codex's ``model/list`` reports about each model."""

from __future__ import annotations

from typing import Any


def _model_entries(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def visible_model_ids(result: dict[str, Any]) -> list[str]:
    return [
        model_id
        for entry in _model_entries(result)
        if isinstance(model_id := entry.get("id"), str)
        and model_id
        and not entry.get("hidden", False)
    ]


def supported_efforts(result: dict[str, Any], model_id: str) -> list[str]:
    entry = next((e for e in _model_entries(result) if e.get("id") == model_id), None)
    efforts = entry.get("supportedReasoningEfforts") if entry else None
    if not isinstance(efforts, list):
        return []
    return [
        effort["reasoningEffort"]
        for effort in efforts
        if isinstance(effort, dict) and isinstance(effort.get("reasoningEffort"), str)
    ]
