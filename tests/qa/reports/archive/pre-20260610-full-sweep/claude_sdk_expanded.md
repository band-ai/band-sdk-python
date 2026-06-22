# QA Report: claude_sdk / claude_sdk_expanded

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-04
- **Platform:** app.band.ai
- **LLM:** claude
- **Agent ID:** (multiple)
- **Startup:** OK

## Scenario Results

### E: Memory Tools
**Status:** PARTIAL
**Room:** `df3a3b28-93b3-43f7-8345-84833519ac1a`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Memory stored successfully! Here are the details:  - **Mem | PASS |
| 2 | List memories (same room) | Response includes Quinn + teal | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are all the memories I can see (total: 1 accessible + | PASS |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are the full details of the memory:  | Field | Value  | PASS |
| 4 | Cross-room memory recall | Agent finds memory from other room (Quinn, teal) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I checked all stored memories and found only one — an orga | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Done — memory `b3c958ba-8556-45c4-a6ae-a6476c5a6ee1` is no | PASS |
| 6 | Store updated memory (green) | New memory stored | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Stored! New memory details:  - **Memory ID:** `3b6c489d-22 | PASS |
| 7 | Archive superseded memory | Agent calls band_archive_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Done — memory `b3c958ba-8556-45c4-a6ae-a6476c5a6ee1` ("fav | PASS |
| 8 | Final memory list | Only green memory active; teal superseded/archived | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The list query returns **1 active memory** visible to me:  | PASS |

## Chat Rooms
- **E: Memory Tools**: `df3a3b28-93b3-43f7-8345-84833519ac1a`
