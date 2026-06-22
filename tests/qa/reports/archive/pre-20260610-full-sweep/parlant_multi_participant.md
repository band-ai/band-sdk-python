# QA Report: parlant / parlant_multi_participant

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** openai (via Parlant NLP)
- **Agent ID:** (multi)
- **Startup:** OK

## Scenario Results

### D: Multi-Participant
**Status:** PARTIAL
**Rooms:** `249d0dad-f6c9-472d-a9eb-161d4869e6d2`, `a310fed7-ac63-4276-a8c6-efdddfa54add`, `4cd34b86-e8bd-4261-95ef-af1edbd83747`, `ec191b46-e09d-445d-a519-fad431a4e841`, `e13ade35-33b4-4dc0-842d-49d4761421ff`
*Two agents holding a multi-directional, agent-to-agent conversation*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create room and add both agents | Room with agent A and agent B | room=249d0dad-f6c9-472d-a9eb-161d4869e6d2, A=nir.singhertest/qa-parlant-parlant-age-2, B=nir.singher | PASS |
| 2 | Both agents answer a direct multi-mention | Agent A and Agent B both reply | A_replied=True, B_replied=True | PASS |
| 3 | A->B->user (room a310fed7): asker delegates; helper answers the user directly | asker @mentions helper -> helper @mentions the user with the answer | asker_delegated=True, helper_answered_user=True | "@[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Fun fac | PASS |
| 4 | B->A->user (room 4cd34b86): asker delegates; helper answers the user directly | asker @mentions helper -> helper @mentions the user with the answer | asker_delegated=True, helper_answered_user=False | PARTIAL |
| 5 | A->B->A->user (room ec191b46): asker consults helper, then relays the answer to the user | asker @mentions helper -> helper replies -> asker @mentions the user with the answer | asker_asked_helper=True, helper_replied=True, asker_relayed_to_user=False | PARTIAL |
| 6 | B->A->B->user (room e13ade35): asker consults helper, then relays the answer to the user | asker @mentions helper -> helper replies -> asker @mentions the user with the answer | asker_asked_helper=True, helper_replied=True, asker_relayed_to_user=True | "@[[6d8e9293-5939-45b9-9d | PASS |

## Chat Rooms
- **D: Multi-Participant**: `249d0dad-f6c9-472d-a9eb-161d4869e6d2`, `a310fed7-ac63-4276-a8c6-efdddfa54add`, `4cd34b86-e8bd-4261-95ef-af1edbd83747`, `ec191b46-e09d-445d-a519-fad431a4e841`, `e13ade35-33b4-4dc0-842d-49d4761421ff`
