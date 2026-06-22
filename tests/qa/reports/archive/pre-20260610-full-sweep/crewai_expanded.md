# QA Report: crewai / crewai_expanded

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** gpt-5.4-mini
- **Agent ID:** (multiple)
- **Startup:** OK

## Scenario Results

### E: Memory Tools
**Status:** PARTIAL
**Room:** `f2fba5e2-ae23-4a44-aeb1-13a2f2e46e49`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Stored as requested. Memory ID: `77690621-f508-40ac-b9d3-3 | PASS |
| 2 | List memories (same room) | Response includes Quinn + teal | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I checked your memories and don’t currently have any store | PARTIAL |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I tried to retrieve memory ID `45aab33a-2ce1-4b18-b475-bc9 | PARTIAL |
| 4 | Cross-room memory recall | Agent finds memory from other room (Quinn, teal) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I checked the memories available to me, but I don’t have a | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I found the old active color memory and superseded it succ | PASS |
| 6 | Store updated memory (green) | New memory stored | NO RESPONSE | FAIL |
| 7 | Archive superseded memory | Agent calls band_archive_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I couldn’t find any memory about “teal” for that subject,  | PASS |
| 8 | Final memory list | Only green memory active; teal superseded/archived | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are your active memories: 1) "User's name is Quinn an | PARTIAL |

## Chat Rooms
- **E: Memory Tools**: `f2fba5e2-ae23-4a44-aeb1-13a2f2e46e49`
