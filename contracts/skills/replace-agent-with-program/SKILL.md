---
name: replace-agent-with-program
description: Replace or partially replace a remote contract participant with a Python program, SQLite journal and snapshots, local contract checks, and an LLM that takes over turns or repairs code in the background.
---

# Replace an agent with a program

Treat the artifact as a **multi-agent skill**: retained guidance plus enforceable coordination. Document roles, message intent, state guidance and outcome meaning, not just schemas. Use ordinary `"..."` or multiline `"""` strings directly above the item they guide. The parser retains them as prompt metadata for the agent, including business rules the room cannot directly enforce. Use `~r` for regular expressions. Other sigils are for cases that need their special behavior; `/* ... */` author comments are ignored by role queries. For runtime rosters, partial collection, offline grace or renewable clocks, read [the current coordination guide](../../spec/multi-agent-skills.md) and its normative links. For runtime participants, use the selected reusable type form: `type reviewer: agent` with ordinary guidance and bounded attributes, then `reviewers: list(reviewer)`. Validate host bindings against that schema and preserve all guidance in agent views. Explain agreed designs directly; compare alternatives only for unresolved choices or when requested. Keep ordinary agents independent of optional client machinery. For partial collections, declare `room.contributions/1`, specify the successful reply, and use standard room contribution controls for terminal outcomes. A member may stop only its own contribution; the host may report terminal execution failure scoped to its session, collection entry and member. Retain the original cause type and evidence reference. Do not author `failure unable()` or infer failure from recoverable errors. Read [contribution outcomes](../../spec/contribution-outcomes.md).

This workflow is optional and experimental. Use it only when program replacement
or this recovery runtime is requested; do not insert it into ordinary contract
onboarding. Room enforcement and public discovery work with any compatible
authorized agent. A journal, SQLite, snapshots, local checker and repair are not
participation prerequisites. `llm_only` is a mode of this experiment, not a
mandatory wrapper around other agents. Read [the boundary](../../spec/room-clients-v2.md).

Read the [replacement reference](references/replacement.md), then the [Python runtime guide](../../agent-runtime/README.md) for the relevant APIs. Use the [remote example](../../agent-runtime/examples/hybrid_demo.py) for integration shape. The intermediary sits beside the remote agent, outside the room.

Pin the contract and participant role. Query its documented role view and inspect visible accepted histories. Classify replacement as `full`, `partial`, or `llm_only` before writing the program. Optimism means trying proven common cases with reliable delegation; it does not mean forcing language interpretation into fixed rules. Arbitrary English-to-JSON interpretation generally belongs with the LLM unless a precisely bounded subset has an independently testable meaning. A contract specifies legal interaction, not the correct business answer.

Implement the Python SDK's versioned JSON state, explicit coverage declaration, and pure callbacks. Return `Handled` or `Delegate` with per-turn or ongoing scope. Use `context.checkpoint(state)` for a snapshot in the controller's SQLite database; do not create a separate journal or snapshot file. The snapshot is committed with the accepted turn, not halfway through a callback. Derive semantic tests from the role's obligations and representative held-out inputs, including ambiguous near matches that must delegate.

Connect authenticated room attribution and deliver only accepted facts addressed to this role. Keep the local checker beside the program/LLM controller. Reject illegal program output locally and let the LLM handle the same input; the room still has final admission authority. Pin host tool and control grants separately from contract prose. Control endpoint attribution comes from authentication, not actor fields in request JSON, and activation is durably deduplicated.

Configure SQLite path, snapshot interval, callback deadlines, pending-input bound, model/repair/retry budgets, artifact directory, and control actors. Journal the accepted input, stage state and outgoing proposal before dispatch, then commit program state and snapshot after the room acknowledges acceptance. Retain immutable Python artifacts and the same pinned host configuration for restart.

Validate the initial program before connecting it. Configure the provider-neutral command adapter and a separate host-owned quality validator. Use the maintained [sidecar prompt](../../../src/remote_agent/sidecar_prompt.md) for response, wake, and repair behavior. Commands and credentials are host configuration, never chosen by room content. A scripted provider exercises the mechanism without proving live model quality or integration.

On a fault, continue the current conversation through the LLM while a separate bounded repair job inspects the journal, snapshot, failed input, and unresolved proposal. Produce a fresh artifact preserving state version and committed behavior. Candidate validation replays the full history, probes pending faults, and runs quality checks. It may delegate earlier LLM-owned turns; partial replacement remains valid. Catch up with conversation progress before installation. Never reset state, erase pending work, weaken quality checks, or override a newer explicit takeover.

Validate common cases with zero model calls, one-turn and ongoing delegation, illegal output, crash/timeout, authenticated wake, continued LLM replies during repair, failed candidates, restart, duplicates, and exhausted budgets. An unknown outgoing acknowledgement must be reconciled before generating another answer. A non-idempotent uncertain effect requires destination evidence or host intervention. Do not promise uninterrupted service if the room, model, storage, network, or remaining budget is unavailable.

Deliver the coverage classification, code, documented role, semantic scenarios, failure/recovery evidence, artifacts, and precise host integration limits. Report calls separately from tokens and cost. The earlier Elixir-only experiment remains documented in [its reference](references/elixir-replacement.md); use it when that host is specifically requested.
