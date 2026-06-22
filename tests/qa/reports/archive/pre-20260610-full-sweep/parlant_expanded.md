# QA Report: parlant / parlant_expanded

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-04
- **Platform:** app.band.ai
- **LLM:** openai (via Parlant NLP)
- **Agent ID:** (multiple)
- **Startup:** OK

## Scenario Results

### E: Memory Tools
**Status:** PARTIAL
**Room:** `00122e23-3889-4ab8-9f0a-3914c4de1fcb`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The information has been stored in long_term memory (seman | PASS |
| 2 | List memories (same room) | Response includes Quinn + teal | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here is what I have stored: Memory ID d1569971-a83a-4e8d-a | PASS |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are the full details of the stored memory (ID: d15699 | PASS |
| 4 | Cross-room memory recall | Agent finds memory from other room (Quinn, teal) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I don't have any stored memories about your name or favori | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] To supersede the old memory about your favorite color, ple | PASS |
| 6 | Store updated memory (green) | New memory stored | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The new memory has been stored in long_term memory (semant | PASS |
| 7 | Archive superseded memory | Agent calls band_archive_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The old memory about your favorite color being teal has al | PARTIAL |
| 8 | Final memory list | Only green memory active; teal superseded/archived | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You currently have one active memory stored: Memory ID d15 | PASS |

## Chat Rooms
- **E: Memory Tools**: `00122e23-3889-4ab8-9f0a-3914c4de1fcb`
