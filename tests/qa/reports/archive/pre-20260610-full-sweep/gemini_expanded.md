# QA Report: gemini / gemini_expanded

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
**Room:** `d0708f83-6828-446d-a789-0347a6eb8799`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I have stored the memory. The memory ID is a9d55901-6a07-4 | PASS |
| 2 | List memories (same room) | Response includes Quinn + teal | {"data": [], "meta": {"page_size": 50, "total_count": 0}} | PARTIAL |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I couldn't find any memories associated with you directly. | PARTIAL |
| 4 | Cross-room memory recall | Agent finds memory from other room (Quinn, teal) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I don't have any memories stored about your name or favori | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] As I mentioned previously, I was unable to find the memory | PARTIAL |
| 6 | Store updated memory (green) | New memory stored | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I understand your favorite color has changed. However, as  | PASS |
| 7 | Archive superseded memory | Agent calls band_archive_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I have stored the new memory: "My name is Quinn and my fav | PARTIAL |
| 8 | Final memory list | Only green memory active; teal superseded/archived | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I understand you'd like to archive the old memory about yo | PARTIAL |

## Chat Rooms
- **E: Memory Tools**: `d0708f83-6828-446d-a789-0347a6eb8799`
