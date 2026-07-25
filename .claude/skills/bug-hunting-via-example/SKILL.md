---
name: bug-hunting-via-example
description: Ask the user which real runnable example or examples to use, then validate them individually, concurrently, and across adapters; use them as executable specifications and differential probes to reproduce failures, instrument complete flows, isolate the failing boundary, find root causes in application code, adapters, dependencies, configuration, orchestration, infrastructure, or external services, implement invariant-level fixes, and add durable regression coverage. Use for example certification, live E2E testing, multi-agent or multi-room failures, adapter integration debugging, concurrency and lifecycle bugs, tool-routing issues, stuck turns, and investigations where assumptions or a narrow component-only review risk a bolt-on patch.
---

# Bug Hunting via Example

Use actual examples to discover integration behavior, test realistic topologies, and trace failures to their authoritative cause. Allow examples to come first; convert learned invariants into durable tests after behavior is understood.

## Principles

- Run the exact user-facing example, not an equivalent hand-built adapter.
- Treat startup as readiness only, never as proof of behavior.
- Use fresh identities and rooms for controlled tests; preserve the original failing topology separately.
- Run multiple examples together, including same-adapter and cross-adapter groups.
- Assume nothing is correct merely because it is existing, shared, generated, documented locally, or outside the adapter. Treat example code, SDK code, adapter code, dependency behavior, settings, credentials, launch commands, process orchestration, shared services, network transport, platform behavior, and the test itself as possible fault domains.
- During investigation, follow evidence across every relevant layer. Do not declare a layer out of scope because it is outside the original ticket.
- During implementation, change the smallest authoritative boundary that restores the invariant. Remove obsolete workarounds and avoid adapter-specific patches around a shared defect.
- Consult current online primary documentation early whenever behavior crosses a framework, protocol, API, CLI, generated client, or external service. Match documentation to the installed version, then confirm consequential details in dependency source or a minimal live probe. Treat names and old knowledge as hypotheses, not contracts.
- Prefer event-driven barriers and correlated traces over sleeps, silence windows, or larger timeouts.
- Use test credentials and scoped resources. Never print tokens or stop unrelated processes.

## 0. Select the Examples

Before launching, modifying, or provisioning anything, establish the exact example set.

1. Discover the repository's available runnable examples and their documented purpose so the question is grounded in real choices.
2. If the user has not already named an exact example set, ask: **Which example or examples should I use?** Present a concise list or relevant subset using repository paths or stable IDs.
3. For more than one selected example, also ask whether to run them independently, together, or both, unless the requested topology already makes that clear.
4. Repeat the chosen paths/IDs and topology before starting so the scope is auditable.

Do not silently select a familiar pair, default example, adapter, or “all examples.” A user request that already identifies exact examples, a directory-wide set, or an explicit selection rule counts as the answer; do not ask again.

### Bundled discovery tool

Use `scripts/discover.py` to build the choice list from executable repository facts rather than memory:

```bash
uv run python .claude/skills/bug-hunting-via-example/scripts/discover.py
uv run python .claude/skills/bug-hunting-via-example/scripts/discover.py --family copilot_sdk --json
```

It reports PEP 723 runnable scripts, config keys, environment inputs, dependencies, and documented commands. Review its output against example documentation; discovery is an inventory aid, not proof that a capability works.

## 1. Learn the Repository Contract

Before running anything:

1. Read repository instructions and example documentation.
2. Find the existing E2E toolkit, registry, fixtures, and cleanup policy. Reuse them rather than creating parallel provisioning or observation code.
3. Inspect the exact example commands, settings, required services, credentials, and promised capabilities.
4. Check the working tree and preserve unrelated changes.
5. Consult current official online documentation for every consequential external boundary, including startup flags, API semantics, namespaces, lifecycle operations, event formats, timeout behavior, and supported concurrency. Match it to the installed version and inspect dependency source when docs are ambiguous.
6. Verify important claims with a minimal probe. Documentation guides the investigation; observed version-specific behavior closes it.

Create a concise inventory for each selected example:

- stable example ID and exact command
- adapter and backend/service requirements
- credential gates
- agent identity/config source
- expected capabilities and observable outcome
- whether its backend may or must be shared
- readiness and shutdown behavior

Do not hardcode adapter branches in orchestration. Keep adapter-specific launch knowledge in an existing registry or a small declarative example/backend specification.

## 2. Choose Topologies

Start with the smallest scenario that can answer the question, then expand. Cover applicable rows:

| Topology | What it detects |
|---|---|
| One example, one fresh room | Basic end-to-end correctness |
| One example, several rooms | Room and session isolation |
| Several copies of one example | Instance-global state |
| Several examples of one adapter | Shared-backend registration and lifecycle bugs |
| Examples from different adapters | Platform interoperability and differential diagnosis |
| Several agents in one room | Mentions, collaboration, delegation, and loop suppression |
| Interleaved in-flight turns | Races and accidental global state |
| Stop one while others run | Resource ownership and cleanup isolation |
| Restart one identity | Rehydration and stale-session handling |

Do not special-case a mascot pair or known example. The same orchestration must accept any selected set of examples.

## 3. Spin Everything Up

Use one adapter-agnostic lifecycle:

```text
discover selected examples
-> validate dependencies and credentials
-> allocate ports and start shared services
-> provision fresh agent identities
-> generate per-run configuration
-> launch the exact example processes
-> establish platform readiness
-> provision rooms and participants
-> drive scenarios with the live E2E user identity
-> observe and assert
-> stop example processes
-> stop unreferenced shared services
-> reap rooms and agents
-> verify no scoped leaks remain
```

Determine readiness through the real system boundary where possible: online presence, subscriptions, and a deterministic probe turn. Log matching alone is insufficient.

Deduplicate shared services by logical service identity and reference-count their users. Stopping one example must not disable a backend or registration still owned by another active example.

If the repository has no general runner yet, first run the documented commands directly and record the missing reusable seam. Do not invent a large framework before the first concrete use proves what must be generalized.

### Bundled live runner

For this repository, use `scripts/runner.py` when the scenario fits its proven seam. It reuses the baseline toolkit for fresh provisioning, user-token REST/WebSocket driving, event barriers, and scoped cleanup. It launches each selected example from an isolated temporary working directory with its own generated `agent_config.yaml`, so examples that share a config key cannot accidentally share an identity.

Create a temporary declarative plan; do not add adapter branches to the runner:

```yaml
version: 1
topologies: [independent, together]
examples:
  - id: first
    path: examples/framework/01-first.py
    config_key: first_agent
    # Optional override; placeholders: {repo}, {path}, {workdir}
    command: ["{repo}/.venv/bin/python", "{path}"]
    unset_env: [GITHUB_TOKEN]
    steps:
      - prompt: "Reply with the exact marker {marker}."
        barrier: reply
        contains_any: ["{marker}"]
  - id: second
    path: examples/another/02-second.py
    config_key: second_agent
collaborations:
  - source: first
    target: second
    prompt: "Send {marker} to @{target_name} using your Band messaging tool."
    contains_any: ["{marker}"]
```

Validate the manifest before live writes, then run it:

```bash
uv run python .claude/skills/bug-hunting-via-example/scripts/runner.py /tmp/example-plan.yaml --dry-run
uv run python .claude/skills/bug-hunting-via-example/scripts/runner.py /tmp/example-plan.yaml --json-out /tmp/example-scorecard.json
```

The default command is the current Python interpreter plus the exact example path. Override it only when the documented launch command has meaningful semantics. `reply` waits for both processed delivery and the selected agent's reply; `processed` is for turns where a reply is optional. `contains_any` is case-insensitive and intentionally semantic-tolerant. Together mode covers separate concurrent rooms and one shared room for every selected example; `collaborations` adds directed agent-to-agent probes without assuming particular names or adapters.

The runner requires `E2E_TESTS_ENABLED=true` and `BAND_API_KEY_USER`, loaded through the baseline settings and `.env.test`. It never prints credentials. Cleanup is the default; use `--keep` only when the user explicitly wants live resources preserved for investigation, and reap them afterward. If a selected example needs a shared server, port allocation, a non-reply capability assertion, or other behavior the runner does not model, use the baseline toolkit directly first. Generalize that seam only after the concrete run proves it is reusable.

## 4. Validate Behavior

For each example:

1. Send a deterministic message through the real user API/token used by live E2E tests.
2. Wait for the correct event-driven barrier: reply for reply assertions; processed/durable completion for tool, event, usage, or memory assertions.
3. Assert the capability promised by that example, not exact prose.
4. Exercise at least one meaningful interaction; merely keeping the process alive does not pass.

Then run grouped scenarios:

- independent turns for every participant
- interleaved turns in separate rooms
- same-room collaboration in both directions
- tool execution and reply delivery
- partial stop and restart while peers remain active

Use structural assertions first. Use an LLM judge only when semantics cannot be expressed deterministically.

## 5. Instrument the Complete Flow

Record correlation identifiers without secrets:

- run and example ID
- immutable agent ID
- room ID
- triggering message ID
- adapter and backend session IDs
- shared registration or tool namespace
- tool-call, approval, or question request ID

Trace phase transitions at boundaries:

```text
process started
-> platform connected
-> room subscribed
-> message received
-> backend prompt accepted
-> tool or approval requested
-> backend terminal event received
-> reply posted
-> platform delivery processed
```

Classify the failure by the last confirmed phase before changing code. Add concise phase diagnostics when current logs cannot distinguish boundaries. Do not use logging as the fix.

## 6. Hunt the Root Cause Differentially

Change one axis per experiment:

- original room vs fresh room
- original identity vs fresh identity
- one agent vs several
- shared backend vs separate backends
- sequential vs interleaved turns
- tools enabled vs disabled
- continuous run vs cleanup/restart
- one adapter vs a known-good adapter in the same role
- direction A -> B vs B -> A

Interpret cross-adapter controls:

| Observation | Strong lead |
|---|---|
| Every adapter fails | Platform, driver, room, or shared API |
| Only one adapter fails | Adapter or framework integration |
| Only one backend family fails | Backend protocol/service behavior |
| Single instance works; same-adapter pair fails | Shared global state or namespace collision |
| Cross-adapter pair works | Platform collaboration is probably healthy |
| Every pair involving one adapter fails | Its mentions, tools, event flow, or lifecycle |
| Failure follows a room | Room/session persistence |
| Failure follows an identity | Registration or durable agent state |
| Failure follows concurrency | Race, shared ownership, capacity, or rate limit |

Inspect state on both sides of every suspected boundary: platform participants and delivery state, adapter maps and tasks, backend sessions and registrations, and network/event-stream behavior.

Keep every fault domain open until evidence closes it:

| Fault domain | Validate directly |
|---|---|
| Example code | Exact command, imports, settings, defaults, and promised interaction |
| Core/SDK code | State ownership, concurrency, lifecycle, serialization, and error propagation |
| Adapter code | Session mapping, tool visibility/routing, event handling, and cleanup |
| Dependencies | Installed version behavior, source, protocol support, and known constraints |
| Configuration | Effective values, precedence, URLs, identities, models, and credentials |
| Spin-up/orchestration | Process ownership, port allocation, readiness, shared-service reuse, and shutdown |
| Infrastructure/platform | API responses, delivery state, presence, rate limits, reconnects, and persistence |
| Test harness | Driver identity, barriers, capture ordering, assertions, retries, and cleanup |

Do not infer that a timeout is a model problem, that a 404 is harmless, that an online process is ready, or that a passing single-agent run proves isolation. Validate each claim at its boundary.

A root cause requires all of the following:

1. A reliable reproducer or captured causal trace.
2. A concrete violated invariant.
3. An explanation of every important symptom, including why simpler cases passed.
4. Evidence that changing the suspected variable changes the outcome.
5. A regression test that fails for the mechanism, not merely its timeout symptom.

## 7. Fix the Owning Invariant

Locate the single source of truth for identity, ownership, visibility, lifecycle, timeout, or protocol semantics. Fix that boundary directly.

Review the proposed fix for warning signs:

- adapter-name conditionals in generic code
- duplicated policy or magic strings
- random IDs where stable identity is required
- retries or wider timeouts masking lost events
- cleanup callbacks controlling resources they do not exclusively own
- process-global state used for rooms or agent instances
- mocks that never suspend across a real async race
- preserved workaround code that the invariant-level fix makes obsolete

Keep exploration broad and the final change cohesive and small.

## 8. Lock In the Learning

Add coverage at the lowest meaningful boundaries:

1. Unit test the invariant and edge cases.
2. Use a suspending fake or local peer for real async ordering and protocol behavior.
3. Add lifecycle/concurrency coverage for multiple rooms or instances.
4. Extend the shared baseline matrix rather than adding an adapter-only duplicate.
5. Re-run the exact example topology that exposed the issue.
6. Run a cross-adapter control to ensure interoperability remains intact.

Examples remain user-facing smoke tests and discovery tools. Regression tests carry the durable invariant once learned.

## 9. Report and Clean Up

Report:

- exact examples and topologies exercised
- pass/fail per promised capability
- last confirmed phase for failures
- root cause and violated invariant
- alternatives ruled out
- fix location and why it owns the policy
- regression tests added
- residual infrastructure flakiness separated from product defects

Always stop only scoped example processes and services, reap provisioned resources according to repository policy, and verify ports, registrations, sessions, and tasks are clean. Identify unrelated processes explicitly and leave them running.
