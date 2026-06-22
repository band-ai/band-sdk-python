# QA Report: anthropic / anthropic_expanded

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** claude-sonnet-4-5-20250929
- **Agent ID:** (multiple)
- **Startup:** OK

## Scenario Results

### E: Memory Tools
**Status:** PARTIAL
**Room:** `b5d5f8e9-0354-4a1f-a0a7-356a3a2110a7`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Memory stored successfully!   **Memory ID:** 578f7252-2a97 | PASS |
| 2 | List memories (same room) | Response includes Quinn + teal | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I found 1 memory in the system:  **Memory ID:** e5d44f46-a | PASS |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are the full details of the memory I stored about you | PASS |
| 4 | Cross-room memory recall | Agent finds memory from other room (Quinn, teal) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I checked my stored memories and I don't have any informat | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Memory successfully superseded!  **Memory ID:** 578f7252-2 | PASS |
| 6 | Store updated memory (green) | New memory stored | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Memory stored successfully!  **Memory ID:** 4c9d17b0-986f- | PASS |
| 7 | Archive superseded memory | Agent calls band_archive_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Memory successfully archived!  **Memory ID:** 578f7252-2a9 | PASS |
| 8 | Final memory list | Only green memory active; teal superseded/archived | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Based on the active memories list, I'm seeing **1 active m | PASS |

## Chat Rooms
- **E: Memory Tools**: `b5d5f8e9-0354-4a1f-a0a7-356a3a2110a7`
