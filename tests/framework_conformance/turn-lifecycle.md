# Turn lifecycle coverage

The canonical traces are TL1–TL12 in SDK Core's
`crates/core/README.md#sdk-conformance-traces`. Python exercises them through its
existing native fixtures; Core owns the outcome and complete-round policies, while
`ExecutionContext`, `BackendTurns`, retained rooms and adapters own async work.

| Trace | Python boundary and evidence |
| --- | --- |
| TL1 | `test_turn_outcome.py` verifies reply/fallback once across the registry; `adapters/codex/test_detached_turn_outcome.py` and `adapters/opencode/test_detached_turns.py` verify detached judgment. |
| TL2 | `runtime/deferral/test_approval_beyond_first_page.py`, `adapters/cursorturns/test_control_handling.py`, and OpenCode detached tests verify control handback while ordinary input is refused. |
| TL3 | `runtime/deferral/test_refusal_readiness.py::TestRefusalReadiness::test_worker_completion_rechecks_the_remaining_backend_stop` checks both independent blockers before one accepted execution. |
| TL4 | `test_already_complete_blocker_wakes_once_without_retry_spin` in the same file proves one wake for a repeatedly returned completed future. |
| TL5 | `test_closed_execution_does_not_resume_when_its_blocker_finishes` closes the old execution, installs its replacement, and settles both blockers independently. |
| TL6 | `runtime/parking/test_unconfirmed_pacing.py::TestUnconfirmedPacing::test_admission_readiness_preserves_the_terminal_write_deadline` uses the real REST delivery boundary with retained failed writes. |
| TL7 | `core/test_backend_stop_confirmation.py` checks uncertainty, stable readiness, report-once and failed-report retry. `core/retention/test_backend_recovery.py` proves failed release, terminal closing without readiness, and stale record removal after confirmed disposal. Codex `test_cancelled_start_recovery.py` retains refused input through failed process close without spending its runtime failure budget. OpenCode `next_message_waits_for_the_abort` in `stopprobes/opencode.py` checks actual adapter refusal after abort request settlement, then independent terminal evidence. |
| TL8 | `adapters/codex/test_cancelled_start_recovery.py` proves process retirement before an unknown start answers; `test_process_loss_recovery.py` checks strict retained-thread restoration. Claude `test_session_seeding.py`, ACP `test_batch_replay.py`, and OpenCode `resume/test_offbootstrap_seed.py` prevent replacement input without reconstructable history. |
| TL9 | `integrations/googleadk/test_decline.py::TestDecline::test_successful_decline_waits_for_every_sibling_result` gates real ADK dispatch; LangGraph and PydanticAI decline suites run their installed framework loops. Anthropic/Gemini retain their SDK-owned complete batch boundaries. |
| TL10 | `integrations/googleadk/test_failures.py` checks failed siblings and thrown calls before model recovery; LangGraph/PydanticAI decline suites retain their failure-recovery checks. |
| TL11 | `integrations/googleadk/test_batch.py` checks complete rosters, extra/foreign results, consumed rounds and late results from the same invocation against a successor's captured round. Sequential SDK loops cannot receive results from a later active round because they await the entire prior batch. |
| TL12 | `runtime/deferral/test_released_turn_end.py` retains legacy bare-refusal wake; `test_oneshot_control.py` preserves one-shot refusal. The turn-outcome registry retains custom and unjudged adapter exemptions. |

`InputNotAccepted.ready` is optional. Observing it never cancels the provider's
future; settlement requests another admission check and preserves REST backoff.
Normal detached completion uses `detached_turn` inside reporting authority;
cancellation and provider errors retain their existing reporting paths.

Codex and Cursor recovery retire their existing retained process owner before
restoring/reseeding. Claude already owns generation recovery and release. Their
backend owner retains the original room/generation during stop recovery; a failed
release refuses unaccepted input, and later attempts retry that same owner.
OpenCode requires an independent session terminal event after uncertain abort;
creating another remote session alone does not isolate old native effects.
Generic ACP, Copilot ACP, OMP and unrelated `BackendTurns` callers retain their
existing stop policies. Process retirement does not promise revocation of
unrelated external effects already initiated by tools.

CrewAI and Strands keep their native early-stop behavior. Provider-owned loops,
caller-created graphs/runners and native subprocess tool loops are explicit
complete-round exemptions; their turn judgment still uses the existing ledger.
The SDK cannot stop a provider-owned loop after a decline, so it refuses every
later tool call at the point the call enters (`test_turn_termination.py`);
a call already in flight when the decline lands is served
(`mcp/test_decline_ordering.py`).
This coverage is deterministic/scripted-provider evidence, not a claim of live
provider reliability or cross-process exactly-once execution.
