# Derivation from the chat fixture

Input: [review-chat.md](review-chat.md). Output: [history-review.band](../contracts/history-review.band). This example was authored from the transcript and checked with the same compiler and runtime used by the other contracts.

| Source | Contract consequence | Verification |
|---|---|---|
| H01–H02 | Offer, exact-reference acceptance/refusal, two-minute acceptance duty | Success and unclaimed scenarios |
| H02–H03 | Submission refers to the offer; independent reviewer approves or requests changes | Success and rejected-message scenarios |
| H04 | Lead accepts the exact reviewed submission; named stalled and needs_revision outcomes | Success, rejected-message and changes scenarios |
| H05–H06 | Self-approval is rejected; repeats do not create another contribution | Rejected-message scenario |
| H07 | Both header expiry policies, mention quotas; host requires transition inactivity and twenty-message bound | All scenarios; idle-boundary scenario |
| H08 | Documentation distinguishes artifact claims from checked acceptance | Type and role queries retain the text |
| H09 | Lead cancellation ends this effect-free example; whole-session expiry is separate | Cancel scenario and existing engine timer tests |

Task/artifact/reason bounds of 600/600/300 graphemes are proposed local schema choices because the chat gives no lengths. They are documented as such. Distinct role-to-actor bindings are required by this runtime. No actor identity is taken from a message body.

The five-minute inactivity timer can expire before the fifteen-minute submission deadline when no state transition occurs. This follows H07 literally; it is visible in the idle-boundary scenario. An author should flag this consequence for the user rather than silently extending inactivity or making progress messages reset it.

No unresolved inference is presented as agreement. Changing the inactivity policy, allowing self-review, or adding automatic external publication requires a changed contract or host policy with evidence and appropriate authorization.
