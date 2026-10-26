"""Sample release values shared by the release-docs tests."""

from __future__ import annotations

RELEASE_TAG = "band-sdk-v6.1.0"
RELEASE_URL = f"https://github.com/band-ai/band-sdk-python/releases/tag/{RELEASE_TAG}"
RELEASE_DATE = "2026-10-20"
# Report items shared by the renderer and announcer tests: category, label, sentence.
SEND_RETRIES = ("Added", "Send Retries", "Sends retry with backoff.")
RECONNECT = ("Fixed", "Reconnect", "Rooms rejoin after a drop.")
TOKEN_REDACTION = ("Security", "Token Redaction", "Keys never reach logs.")
LEGACY_TOOLS = ("Removed", "Legacy Tools", "The deprecated tool names are gone.")
TOOL_PREFIX = ("Changed", "Tool Prefix", "Tool names start with a prefix.")
