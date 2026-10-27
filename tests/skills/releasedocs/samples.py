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
# A docs review's report on that page.
DOCS_REPORT = {
    "summary": "Agent creation now uses `Agent.start()`.",
    "pages_reviewed": [{"page": DOCS_PAGE, "reason": "Renamed", "sources": [812]}],
    "claims": [],
    "also_stale": [],
    "pages_missing": [],
}
# The PR the ledger's sdks entry cites, and that entry's fields.
HELD_PR = "901"
HELD_SOURCE = f"band-sdk-python#{HELD_PR}"
LEDGER_ID = "sdk-901-room-tasks"
LEDGER_REASON = "Not public yet."
# The source of the ledger's platform-only entry.
PLATFORM_SOURCE = "thenvoi-platform#77"
LEDGER = f"""held_items:
  - id: {LEDGER_ID}
    source: {HELD_SOURCE}
    title: Room tasks
    status: held
    reason: {LEDGER_REASON}
    surfaces: [sdks, platform]
    released_surfaces: []
  - id: platform-77-billing
    source: {PLATFORM_SOURCE}
    title: Billing
    status: held
    reason: Platform only.
    surfaces: [platform]
    released_surfaces: []
"""
# The same ledger once its sdks entry shipped: nothing is held from the SDK.
RELEASED_LEDGER = LEDGER.replace(
    "released_surfaces: []", "released_surfaces: [sdks]", 1
)
