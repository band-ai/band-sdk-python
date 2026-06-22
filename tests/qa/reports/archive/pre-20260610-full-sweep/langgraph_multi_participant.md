# QA Report: langgraph / langgraph_multi_participant

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** gpt-4o
- **Agent ID:** (multi)
- **Startup:** OK

## Scenario Results

### D: Multi-Participant
**Status:** PARTIAL
**Rooms:** `9861d67a-4f81-47d6-bdda-9f27852bc558`, `cd13464b-6fef-4d5f-8b00-0c6f0223d0d8`, `d9f44f0a-c79d-45df-9001-b676a49564ce`, `6aa393bd-6c1e-4c69-9180-315fa142d4dc`, `6c34db18-cee6-4d3f-8484-5154c1691cb1`
*Two agents holding a multi-directional, agent-to-agent conversation*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create room and add both agents | Room with agent A and agent B | room=9861d67a-4f81-47d6-bdda-9f27852bc558, A=nir.singhertest/qa-lg-simple-agent-1919, B=nir.singhert | PASS |
| 2 | Both agents answer a direct multi-mention | Agent A and Agent B both reply | A_replied=True, B_replied=False | PARTIAL |
| 3 | A->B->user (room cd13464b): asker delegates; helper answers the user directly | asker @mentions helper -> helper @mentions the user with the answer | asker_delegated=True, helper_answered_user=False | PARTIAL |
| 4 | B->A->user (room d9f44f0a): asker delegates; helper answers the user directly | asker @mentions helper -> helper @mentions the user with the answer | asker_delegated=False, helper_answered_user=False | FAIL |
| 5 | A->B->A->user (room 6aa393bd): asker consults helper, then relays the answer to the user | asker @mentions helper -> helper replies -> asker @mentions the user with the answer | asker_asked_helper=True, helper_replied=False, asker_relayed_to_user=False | PARTIAL |
| 6 | B->A->B->user (room 6c34db18): asker consults helper, then relays the answer to the user | asker @mentions helper -> helper replies -> asker @mentions the user with the answer | asker_asked_helper=False, helper_replied=False, asker_relayed_to_user=False | FAIL |

## Chat Rooms
- **D: Multi-Participant**: `9861d67a-4f81-47d6-bdda-9f27852bc558`, `cd13464b-6fef-4d5f-8b00-0c6f0223d0d8`, `d9f44f0a-c79d-45df-9001-b676a49564ce`, `6aa393bd-6c1e-4c69-9180-315fa142d4dc`, `6c34db18-cee6-4d3f-8484-5154c1691cb1`
