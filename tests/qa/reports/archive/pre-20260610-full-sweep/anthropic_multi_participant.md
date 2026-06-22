# QA Report: anthropic / anthropic_multi_participant

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-04
- **Platform:** app.band.ai
- **LLM:** claude-sonnet-4-5-20250929
- **Agent ID:** (multi)
- **Startup:** OK

## Scenario Results

### D: Multi-Participant
**Status:** PARTIAL
**Rooms:** `5be1b930-ba2d-4897-98b1-ca341b661da1`, `b064b777-d247-40dc-8389-7a78f68e20fe`, `6dcfce45-9974-4155-9ae4-e007486e8f8b`, `80672959-9f06-4433-ac1e-4f09936072ab`, `88e98b63-7db2-4f5a-ab6f-e92a349c7308`
*Two agents holding a multi-directional, agent-to-agent conversation*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create room and add both agents | Room with agent A and agent B | room=5be1b930-ba2d-4897-98b1-ca341b661da1, A=nir.singhertest/qa-anth-anthropic-agen-2, B=nir.singher | PASS |
| 2 | Both agents answer a direct multi-mention | Agent A and Agent B both reply | A_replied=True, B_replied=True | PASS |
| 3 | A->B->user (room b064b777): asker delegates; helper answers the user directly | asker @mentions helper -> helper @mentions the user with the answer | asker_delegated=True, helper_answered_user=True | "@[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Fun fac | PASS |
| 4 | B->A->user (room 6dcfce45): asker delegates; helper answers the user directly | asker @mentions helper -> helper @mentions the user with the answer | asker_delegated=False, helper_answered_user=False | FAIL |
| 5 | A->B->A->user (room 80672959): asker consults helper, then relays the answer to the user | asker @mentions helper -> helper replies -> asker @mentions the user with the answer | asker_asked_helper=True, helper_replied=True, asker_relayed_to_user=True | "@[[6d8e9293-5939-45b9-9d | PASS |
| 6 | B->A->B->user (room 88e98b63): asker consults helper, then relays the answer to the user | asker @mentions helper -> helper replies -> asker @mentions the user with the answer | asker_asked_helper=True, helper_replied=True, asker_relayed_to_user=True | "@[[6d8e9293-5939-45b9-9d | PASS |

## Chat Rooms
- **D: Multi-Participant**: `5be1b930-ba2d-4897-98b1-ca341b661da1`, `b064b777-d247-40dc-8389-7a78f68e20fe`, `6dcfce45-9974-4155-9ae4-e007486e8f8b`, `80672959-9f06-4433-ac1e-4f09936072ab`, `88e98b63-7db2-4f5a-ab6f-e92a349c7308`
