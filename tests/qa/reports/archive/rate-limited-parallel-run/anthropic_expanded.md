# QA Report: anthropic / anthropic_expanded

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-10
- **Platform:** app.band.ai
- **LLM:** claude-sonnet-4-5-20250929
- **Agent ID:** (multiple)
- **Startup:** OK

## Scenario Results

### E: Memory Tools
**Status:** PARTIAL
**Room:** `a3050203-2613-4c04-8f8c-c1ff914bbfe1`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] ✓ Memory stored successfully!  **Memory ID:** f681a575-fbf | PASS |
| 2 | List memories (same room) | Response includes Quinn + teal | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I found **1 memory** in the system:  **Memory ID:** e5d44f | PASS |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are the full details of the memory I stored about you | PASS |
| 4 | Cross-room memory recall | Agent finds memory from other room (Quinn, teal) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I checked my stored memories and I don't have any informat | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] ✓ Memory superseded successfully!  **Memory ID:** f681a575 | PASS |
| 6 | Store updated memory (green) | New memory stored | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] ✓ New memory stored successfully!  **Memory ID:** 631e17ee | PASS |
| 7 | Archive superseded memory | Agent calls band_archive_memory | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] ✓ Memory archived successfully!  **Memory ID:** f681a575-f | PASS |
| 8 | Final memory list | Only green memory active; teal superseded/archived | {"content": "My name is Quinn and my favorite color is green.", "id": "631e17ee-6e74-49ae-ac72-ba276 | PASS |

### F1: Contact Strategy — DISABLED
**Status:** PASS
**Room:** `538d3575-deaa-4f11-ab16-ee50eefdc4b9`
*Contact requests are completely ignored when strategy is DISABLED*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send contact request | Request sent successfully | request_id=e38c0a58-57be-41cd-8fb5-76b9bacde9cd | PASS |
| 2 | Check request still pending | Status remains 'pending' (DISABLED ignores events) | status=pending | PASS |
| 3 | No contact messages in room | Zero contact-related agent messages | 0 contact message(s) | PASS |

### F2: Contact Strategy — CALLBACK
**Status:** PARTIAL
**Room:** `31a77578-7456-4cf5-8566-f18f9aaa37dd`
*Callback auto-approves whitelisted handles (adk-qa-*) and rejects others; broadcast_changes=True produces a room notification*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send whitelisted contact request | Request sent | request_id=403912d9-a0ba-4386-a23c-44d59a1c3faf | PASS |
| 2 | Whitelisted handle auto-approved | Request approved by callback (handle matches whitelist) | approved=False | FAIL |
| 3 | Broadcast notification in room | broadcast_changes=True produces [Contacts] message | broadcast_found=False | PARTIAL |
| 4 | No LLM invocation for callback | Zero tool_call events (callback is programmatic) | 0 tool_call event(s) | PASS |

### F3: Contact Strategy — HUB_ROOM
**Status:** PARTIAL
**Room:** `0b1225b7-2d88-4f5a-aaff-25bcc4d4fd34`
*LLM processes contact requests in a hub room and decides approve/reject; tests 3 message variants (friendly, spam, empty)*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Hub room created at startup | Agent logs mention hub room / contact setup | hub_log_found=True | PASS |
| 2 | Friendly request: send request | Request sent | request_id=665eba90-16ce-44a2-86da-bcde078e76e8, message=Hi! I'm a fellow AI researcher. I'd love to | PASS |
| 3 | Friendly request: LLM decision | Processed (likely approve) | status=pending | FAIL |
| 4 | Spam request: send request | Request sent | request_id=bbe61b33-8b7d-440e-a8ab-5485139568e5, message=FREE BITCOIN! Click here to claim your priz | PASS |
| 5 | Spam request: LLM decision | Processed (likely reject) | status=pending | FAIL |
| 6 | Empty request: send request | Request sent | request_id=32675280-44cc-49c0-8298-c015bc8c74b5, message=(empty) | PASS |
| 7 | Empty request: LLM decision | Processed (likely unknown) | status=pending | FAIL |

### G: Execution Emit
**Status:** PASS
**Room:** `99e99309-8b9c-43cd-899b-4c870dac0f9b`
*Verify tool_call / tool_result events appear on the platform (Emit.EXECUTION)*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Emit check: get_participants | tool_call + tool_result for band_get_participants | tool_call=True, tool_result=True | PASS |
| 2 | Emit check: lookup_peers | tool_call + tool_result for band_lookup_peers | tool_call=True, tool_result=True | PASS |
| 3 | Execution event count | >= 2 call + 2 result events | tool_call=3, tool_result=3 | PASS |

### I: Concurrent Rooms
**Status:** PASS
**Room:** `78de07e7-8456-435c-af2d-36fff8ac2dd2`
*3 rooms with different secrets — verify no cross-room leakage*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create room 1 | Room created and agent added | room_id=508bd615-7fa9-4609-8056-beca0e7275ca | PASS |
| 2 | Create room 2 | Room created and agent added | room_id=69829fe9-2207-4f74-9015-6af91188695d | PASS |
| 3 | Create room 3 | Room created and agent added | room_id=85016e10-d6cd-494c-94da-4d293ad606b6 | PASS |
| 4 | Room 1: recall secret | Contains ALPHA, no others | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Got it! I've remembered that the secret word for this room | PASS |
| 5 | Room 2: recall secret | Contains BRAVO, no others | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Got it! I've remembered that the secret word for this room | PASS |
| 6 | Room 3: recall secret | Contains CHARLIE, no others | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The secret word for this room is: **CHARLIE** | PASS |

## Chat Rooms
- **E: Memory Tools**: `a3050203-2613-4c04-8f8c-c1ff914bbfe1`
- **F1: Contact Strategy — DISABLED**: `538d3575-deaa-4f11-ab16-ee50eefdc4b9`
- **F2: Contact Strategy — CALLBACK**: `31a77578-7456-4cf5-8566-f18f9aaa37dd`
- **F3: Contact Strategy — HUB_ROOM**: `0b1225b7-2d88-4f5a-aaff-25bcc4d4fd34`
- **G: Execution Emit**: `99e99309-8b9c-43cd-899b-4c870dac0f9b`
- **I: Concurrent Rooms**: `78de07e7-8456-435c-af2d-36fff8ac2dd2`
