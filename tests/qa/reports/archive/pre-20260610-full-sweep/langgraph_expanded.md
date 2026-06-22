# QA Report: langgraph / langgraph_expanded

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** gpt-4o
- **Agent ID:** (multiple)
- **Startup:** OK

## Scenario Results

### E: Memory Tools
**Status:** PARTIAL
**Room:** `157f2a12-f3af-4511-830e-93f167d19def`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | content='{\'content\': "Nir Singher Test\'s name is Quinn and their favorite color is teal.", \'id\' | PASS |
| 2 | List memories (same room) | Response includes Quinn + teal | content='{\'data\': [{\'content\': "Nir Singher Test\'s name is Quinn and their favorite color is te | PASS |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | content='{\'content\': "Nir Singher Test\'s name is Quinn and their favorite color is teal.", \'id\' | PASS |
| 4 | Cross-room memory recall | Agent finds memory from other room (Quinn, teal) | content='{"data": [], "meta": {"page_size": 50, "total_count": 0}}' name='band_list_memories' id='5f | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | content="{'content': 'My name is Quinn and my favorite color is green.', 'id': 'c06716ed-84a5-4624-8 | PASS |
| 6 | Store updated memory (green) | New memory stored | content='{\'content\': "Nir Singher Test\'s name is Quinn and their favorite color is green.", \'id\ | PARTIAL |
| 7 | Archive superseded memory | Agent calls band_archive_memory | content='{\'content\': "Nir Singher Test\'s name is Quinn and their favorite color is teal.", \'id\' | PASS |
| 8 | Final memory list | Only green memory active; teal superseded/archived | content='{\'data\': [{\'content\': "Nir Singher Test\'s name is Quinn and their favorite color is gr | PASS |

## Chat Rooms
- **E: Memory Tools**: `157f2a12-f3af-4511-830e-93f167d19def`
