# Synthetic iteration request

This continues the [synthetic chat fixture](review-chat.md); it is not a new instruction from the current user.

**H10 — lead:** The inactivity timeout should no longer cut off a writer who is still within the fifteen-minute submission window. Increase inactivity to twenty minutes, retain the thirty-minute fixed lifetime, and keep transition-only resets. Keep the original contract available for existing sessions.

Result: [history-review-v2.band](../contracts/history-review-v2.band). The existing contract remains unchanged. A session on the original contract expires after five inactive minutes; a new session on v2 remains in writing at five minutes and becomes stalled at the fifteen-minute submission deadline. This is an intentional changed requirement, not a bug in the old contract.

Both versions pass the successful conversation trace. The before/after timer test and v2 deadline scenario verify the new outcome, and query output preserves H01–H10 documentation. The new contract has a new name and digest; no running session is silently migrated.
