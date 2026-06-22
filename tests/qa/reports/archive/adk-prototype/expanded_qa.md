# QA Report: google_adk / expanded_adk_qa

## Summary
- **Status:** FAIL
- **Date:** 2026-05-25
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** (multiple)
- **Startup:** FAILED

## Scenario Results

### F1: Contact Strategy — DISABLED
**Status:** PASS
*Contact requests are completely ignored when strategy is DISABLED*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send contact request | Request sent successfully | request_id=a72d6148-8732-4d30-af1c-6a7f2b9936ef | PASS |
| 2 | Check request still pending | Status remains 'pending' (DISABLED ignores events) | status=pending | PASS |
| 3 | No contact messages in room | Zero contact-related agent messages | 0 contact message(s) | PASS |

### F2: Contact Strategy — CALLBACK
**Status:** PARTIAL
*Callback auto-approves whitelisted handles (adk-qa-*) and rejects others; broadcast_changes=True produces a room notification*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send whitelisted contact request | Request sent | request_id=1903ceb9-ac4a-4b02-9ebc-a12d1a6c4c66 | PASS |
| 2 | Whitelisted handle auto-approved | Request approved by callback (handle matches adk-qa-*) | approved=False | FAIL |
| 3 | Broadcast notification in room | broadcast_changes=True produces [Contacts] message | broadcast_found=False | PARTIAL |
| 4 | No LLM invocation for callback | Zero tool_call events (callback is programmatic) | 0 tool_call event(s) | PASS |

### F3: Contact Strategy — HUB_ROOM
**Status:** PARTIAL
*LLM processes contact requests in a hub room and decides approve/reject; tests 3 message variants (friendly, spam, empty)*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Hub room created at startup | Agent logs mention hub room / contact setup | hub_log_found=True | PASS |
| 2 | Friendly request: send request | Request sent | request_id=651e850e-f81d-4d3c-a85f-f037d1a7da8e, message=Hi! I'm a fellow AI researcher. I'd love to | PASS |
| 3 | Friendly request: LLM decision | Processed (likely approve) | status=approved | PASS |
| 4 | Spam request: send request | Request sent | request_id=c7df5458-c706-41f0-b6d5-f31486bc6798, message=FREE BITCOIN! Click here to claim your priz | PASS |
| 5 | Spam request: LLM decision | Processed (likely reject) | status=pending | FAIL |
| 6 | Empty request: send request | Request sent | request_id=ad03ee6d-8533-43e0-acb7-f4632ccb51d8, message=(empty) | PASS |
| 7 | Empty request: LLM decision | Processed (likely unknown) | status=pending | FAIL |
