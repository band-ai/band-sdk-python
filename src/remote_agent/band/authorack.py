"""Recognize receipt acknowledgements in native author job projections."""

from __future__ import annotations


def acknowledges_completion(response: object, identifier: str, attempt: int) -> bool:
    """Accept a saved receipt even after a later compiler attempt is claimed."""
    if not isinstance(response, dict) or response.get("id") != identifier:
        return False
    status = response.get("status")
    if status in ("ready", "needs_input", "queued", "failed", "held"):
        return True
    current = response.get("attempt")
    return status == "running" and type(current) is int and attempt < current <= 2
