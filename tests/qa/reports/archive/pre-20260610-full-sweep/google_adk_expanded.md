# QA Report: google_adk / google_adk_expanded

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** (multiple)
- **Startup:** OK

## Scenario Results

### E: Memory Tools
**Status:** PARTIAL
**Room:** `e6299e8b-6388-438f-bced-da66f5517f80`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I have stored the information as a memory. The memory ID i | PASS |
| 2 | List memories (same room) | Response includes Quinn + teal | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I don't have any memories stored specifically for you. The | PARTIAL |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I have stored the information as a memory with ID: a3ce6d8 | PARTIAL |
| 4 | Cross-room memory recall | Agent finds memory from other room (Quinn, teal) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I checked my memories, and I don't have any information st | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I have stored the following memory for you: - **Memory ID: | PARTIAL |
| 6 | Store updated memory (green) | New memory stored | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are the full details of the memory: - **Content:** My | PARTIAL |
| 7 | Archive superseded memory | Agent calls band_archive_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The memory about your favorite color has been superseded. | PARTIAL |
| 8 | Final memory list | Only green memory active; teal superseded/archived | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I have stored the new memory for you. The memory ID is c2a | PARTIAL |

## Chat Rooms
- **E: Memory Tools**: `e6299e8b-6388-438f-bced-da66f5517f80`
