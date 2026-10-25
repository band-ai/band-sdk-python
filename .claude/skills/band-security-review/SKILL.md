---
name: band-security-review
description: Perform a requested security review or threat model of the Band Python SDK, especially tool execution, MCP/ACP permissions, room and agent authorization, credential handling, subprocesses, and workspace isolation. Use for "security review", "check these trust boundaries", or "threat model this adapter". Do not select for ordinary code review or routine debugging.
---

# Security Review

Read [the shared runtime guidance](../workflow-runtime.md). Review the PR,
paths, or behavior the user named; if none is specified, establish the review
scope before claiming comprehensive coverage. This skill supplements the
ordinary review angles, which do not cover security.

## Model the actual boundary

Read the relevant source, callers, tests, and current primary specifications
for external protocol behavior. Identify the actor, assets, entry point,
trusted authority, and attacker-controlled input for each flow. Distinguish a
developer running trusted configuration from an untrusted room participant or
a model following external content. Record deployment assumptions, such as
SaaS versus on-prem, and ask only when an unresolved assumption changes the
security conclusion.

Follow the boundaries relevant to the change:

| Boundary | What to establish | Starting points |
|---|---|---|
| Room and agent authorization | Which credential acts, which room/session it may affect, and where that authority is enforced | `src/band/runtime/tools/`, especially `src/band/runtime/tools/inputs/identifiers.py`, `src/band/integrations/mcp/engine.py`, relevant `tests/platform/` and `tests/runtime/` cases |
| Approval pairing | Requests and replies match the active agent, room, session, and offered decision; cancellation or a stale reply cannot authorize another operation | `src/band/integrations/acp/permissions.py`, adapter approval handlers, `tests/integrations/acp/` |
| Tool exposure | Negotiated capabilities and pinned context constrain execution, not merely advertised schemas | `src/band/runtime/capabilities.py`, MCP engine and tool dispatch |
| Credentials and external content | Provider/agent/user keys remain scoped; logs, subprocess environments, tool results, and errors do not expose broader authority | `src/band/client/streaming/`, subprocess launchers, example runner environment, `tests/websocket/` |
| Files and processes | Untrusted arguments, paths, symlinks, and archives cannot cross the intended boundary; cleanup affects only owned resources | `src/band/workspaces.py`, affected file tools and runtime launchers |

These are starting points, not proof of a control. Trace the actual caller
chain and platform enforcement; SDK-side filtering is not automatically an
authorization boundary. Default-off capabilities do not prove safety once
enabled, and ordinary developer-supplied executable configuration is not
automatically a remote-code-execution vulnerability.

## Validate findings

For each candidate, show a realistic actor, controlled input, reachable
sequence, violated boundary, and concrete impact. Check existing guards and
the relevant dependency's installed source/current primary documentation.
Prefer a local peer, controlled fixture, or isolated repro over a live attack.
Do not exploit a shared platform, read real secret values, send attack
messages, or change permissions merely to prove a hypothesis.

Separate confirmed vulnerabilities from unverified hypotheses and hardening
suggestions. Rank by impact, prerequisites, and existing controls; do not
assign high severity merely because a dangerous API is present. A static
proof may establish a clear reachable flaw, but state what was not exercised.

## Deliver and fix within scope

Return a concise list of findings with severity, `file:line`, actor and
preconditions, evidence, impact, and the smallest fix at the enforcing layer.
State reviewed boundaries and material coverage gaps. If nothing is found,
say so without asserting the system is secure.

A review request produces findings; fix confirmed issues when the user has
also requested fixes. Preserve existing authorization instead of asking
again. Regression tests should prove denial of the unauthorized operation
and continued success of the authorized flow. Validate according to
`AGENTS.md` "Pre-Commit Checklist"; do not loosen permission policy or bypass
checks to make a test pass. PR comments, ticket creation, pushes, and merges
need their own existing task authorization.
