# QA Report: gemini / gemini_expanded

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-10
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** (multiple)
- **Startup:** OK

## Scenario Results

### E: Memory Tools
**Status:** FAIL
**Room:** `aa1aa07d-9b2f-49be-b271-f6ec8cc41fe2`
*Memory lifecycle (store / list / get / supersede / archive) and cross-room persistence*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Store personal info as memory | Agent calls band_store_memory and confirms | NO RESPONSE | FAIL |
| 2 | List memories (same room) | Memory list returned | NO RESPONSE | FAIL |
| 3 | Get specific memory details | Agent returns memory content (Quinn) | NO RESPONSE | FAIL |
| 4 | Cross-room memory recall | Agent recalls personal info via memory tools | NO RESPONSE | FAIL |
| 5 | Supersede old memory | Agent calls band_supersede_memory | NO RESPONSE | FAIL |
| 6 | Store updated memory (green) | New memory stored | NO RESPONSE | FAIL |
| 7 | Archive superseded memory | Agent calls band_archive_memory | NO RESPONSE | FAIL |
| 8 | Final memory list | Updated memory list | NO RESPONSE | FAIL |

### F1: Contact Strategy — DISABLED
**Status:** PASS
**Room:** `921e3c4b-94a7-41dc-a9f2-b2dd444f56f0`
*Contact requests are completely ignored when strategy is DISABLED*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send contact request | Request sent successfully | request_id=a6852f8f-3f82-4238-9e95-3672e71cb00c | PASS |
| 2 | Check request still pending | Status remains 'pending' (DISABLED ignores events) | status=pending | PASS |
| 3 | No contact messages in room | Zero contact-related agent messages | 0 contact message(s) | PASS |

### F2: Contact Strategy — CALLBACK
**Status:** PARTIAL
**Room:** `a883c868-97d2-4c05-b363-7b39c56f88bc`
*Callback auto-approves whitelisted handles (adk-qa-*) and rejects others; broadcast_changes=True produces a room notification*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send whitelisted contact request | Request sent | request_id=c49f6252-89ff-4b19-a51b-85cd0bcc2c81 | PASS |
| 2 | Whitelisted handle auto-approved | Request approved by callback (handle matches whitelist) | approved=False | FAIL |
| 3 | Broadcast notification in room | broadcast_changes=True produces [Contacts] message | broadcast_found=False | PARTIAL |
| 4 | No LLM invocation for callback | Zero tool_call events (callback is programmatic) | 0 tool_call event(s) | PASS |

### F3: Contact Strategy — HUB_ROOM
**Status:** PARTIAL
**Room:** `a42ce221-2926-4688-b7a0-78fa62753865`
*LLM processes contact requests in a hub room and decides approve/reject; tests 3 message variants (friendly, spam, empty)*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Hub room created at startup | Agent logs mention hub room / contact setup | hub_log_found=True | PASS |
| 2 | Friendly request: send request | Request sent | request_id=b293f1c3-cca7-4f4d-ba1e-3f8b6b1b5249, message=Hi! I'm a fellow AI researcher. I'd love to | PASS |
| 3 | Friendly request: LLM decision | Processed (likely approve) | status=pending | FAIL |
| 4 | Spam request: send request | Request sent | request_id=7eb33648-d21c-41ab-81b7-303051ffd8b6, message=FREE BITCOIN! Click here to claim your priz | PASS |
| 5 | Spam request: LLM decision | Processed (likely reject) | status=pending | FAIL |
| 6 | Empty request: send request | Request sent | request_id=f4245d37-87fe-4e69-b7db-69b4ed30359a, message=(empty) | PASS |
| 7 | Empty request: LLM decision | Processed (likely unknown) | status=pending | FAIL |

### G: Execution Emit
**Status:** PARTIAL
**Room:** `8ff12c1c-77e3-4729-ad13-271989f59c16`
*Verify tool_call / tool_result events appear on the platform (Emit.EXECUTION)*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Emit check: get_participants | tool_call + tool_result for band_get_participants | tool_call=False, tool_result=False | FAIL |
| 2 | Emit check: lookup_peers | tool_call + tool_result for band_lookup_peers | tool_call=False, tool_result=False | FAIL |
| 3 | Execution event count | >= 2 call + 2 result events | tool_call=0, tool_result=0 | PARTIAL |

### I: Concurrent Rooms
**Status:** PARTIAL
**Room:** `c62b02c8-4f8c-493e-b5a3-e9ebb1d488cc`
*3 rooms with different secrets — verify no cross-room leakage*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create room 1 | Room created and agent added | room_id=ff1c7636-b2c7-4f7d-ae21-cf16411b0db0 | PASS |
| 2 | Create room 2 | Room created and agent added | room_id=c7b05adb-6f5b-4faa-a94e-a7c9d121bfb6 | PASS |
| 3 | Create room 3 | Room created and agent added | room_id=4cfdfa83-9108-4051-a2bb-bde1c94a9322 | PASS |
| 4 | Room 1: recall secret | Contains ALPHA | NO RESPONSE | FAIL |
| 5 | Room 2: recall secret | Contains BRAVO | NO RESPONSE | FAIL |
| 6 | Room 3: recall secret | Contains CHARLIE | NO RESPONSE | FAIL |

## Chat Rooms
- **E: Memory Tools**: `aa1aa07d-9b2f-49be-b271-f6ec8cc41fe2`
- **F1: Contact Strategy — DISABLED**: `921e3c4b-94a7-41dc-a9f2-b2dd444f56f0`
- **F2: Contact Strategy — CALLBACK**: `a883c868-97d2-4c05-b363-7b39c56f88bc`
- **F3: Contact Strategy — HUB_ROOM**: `a42ce221-2926-4688-b7a0-78fa62753865`
- **G: Execution Emit**: `8ff12c1c-77e3-4729-ad13-271989f59c16`
- **I: Concurrent Rooms**: `c62b02c8-4f8c-493e-b5a3-e9ebb1d488cc`
