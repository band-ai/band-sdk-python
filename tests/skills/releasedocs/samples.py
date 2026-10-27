"""Sample release values shared by the release-docs tests."""

from __future__ import annotations

RELEASE_TAG = "band-sdk-v6.1.0"
RELEASE_URL = f"https://github.com/band-ai/band-sdk-python/releases/tag/{RELEASE_TAG}"
RELEASE_DATE = "2026-10-20"
PREVIOUS_TAG = "band-sdk-v6.0.1"
PRODUCT = {"name": "Band Python SDK", "label": "Python SDK", "tag": "python"}
# A published release as ``gh release view --json`` returns it.
PUBLISHED_RELEASE = {
    "url": RELEASE_URL,
    "publishedAt": f"{RELEASE_DATE}T10:00:00Z",
    "isPrerelease": False,
    "isDraft": False,
    "assets": [],
}
# Report items shared by the renderer and announcer tests: category, label, sentence.
SEND_RETRIES = ("Added", "Send Retries", "Sends retry with backoff.")
RECONNECT = ("Fixed", "Reconnect", "Rooms rejoin after a drop.")
TOKEN_REDACTION = ("Security", "Token Redaction", "Keys never reach logs.")
LEGACY_TOOLS = ("Removed", "Legacy Tools", "The deprecated tool names are gone.")
TOOL_PREFIX = ("Changed", "Tool Prefix", "Tool names start with a prefix.")
# A Fern docs page and the held-items ledger, as the Fern repository holds them.
DOCS_PAGE = "fern/docs/pages/integrations/sdks/tutorials/agents.mdx"
DOCS_PAGE_TEXT = """---
title: Agents
---

import { Tabs } from "@components";

<Tabs>
Create an agent with `Agent.create()`.
</Tabs>

See the [reference](https://docs.band.ai/reference).
"""
LEDGER = """held_items:
  - id: sdk-901-room-tasks
    source: band-sdk-python#901
    title: Room tasks
    status: held
    reason: Not public yet.
    surfaces: [sdks, platform]
    released_surfaces: []
  - id: platform-77-billing
    source: thenvoi-platform#77
    title: Billing
    status: held
    reason: Platform only.
    surfaces: [platform]
    released_surfaces: []
"""
# The PR the ledger's sdks entry cites.
HELD_PR = "901"
