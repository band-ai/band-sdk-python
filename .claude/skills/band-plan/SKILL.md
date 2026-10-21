---
name: band-plan
description: Turns a Linear ticket (URL or identifier like INT-1234) into a finalized, reviewed implementation plan attached to the ticket. Designs for the root cause behind the ticket rather than the symptom it reports, after a system-level view decides between a local fix and an architectural adjustment. Verifies everything against fresh origin/main, sibling repos, and current docs, and researches best practices and well-maintained libraries when the problem is a general one. Works in plan mode, presents a short scenario-based review summary, and iterates until the user finalizes the plan. Never implements; the plan ends with a hand-off prompt for a separate agent. Trigger on "plan this ticket", "/band-plan INT-1234", "plan out/scope out INT-1234", "let's plan this out", or a Linear ticket ID/URL paired with build intent ("let's figure out how to build this", "what would it take to do INT-1234", "how should we implement this"). Not for implementing directly, and not for handling a code review (use band-here-is-a-review).
argument-hint: "[ticket-id-or-url]"
---

# Plan a Ticket

Scope: produce a finalized, attached plan for a Linear ticket. Never write or
edit product code, never implement, never call `ExitPlanMode`. The plan hands
off through its Implementation Prompt, for a separate agent to run. Plan
mode's no-edit rule covers the repos; the only writes outside the plan file
are Phase 8's Linear updates, made after the user finalizes and while still
in plan mode.

Requires the Linear MCP server for reading the ticket and attaching the plan.
Without it, ask the user to paste the ticket and its comments, and in Phase 8
hand over the plan file path instead of attaching it.

The plan solves the real problem behind the ticket, not the spot where it
surfaced. A ticket usually reports a symptom or proposes a fix; neither is the
root cause until Phase 3 shows it is.

The plan lives in plan mode's own plan file (the path plan mode gives) from
the first draft on. Every revision updates that file; the chat summary is
derived from it and Phase 8 publishes it. That keeps one copy of the plan,
and the file survives context compaction.

**The plan is NOT a history book.** It holds facts, decisions, and the
references they rest on — nothing else. No narration of how it was reached:
no "first I checked X, then found Y", no "after discussion we decided", no
"originally the plan was", no "updated per feedback", no changelog of rounds.
A revision rewrites the affected text in place, so the plan always reads as
if it had been written once, in its final form. Alternatives considered
states each rejected option and why it lost, never the story of rejecting
it. What changed between rounds belongs in the chat summary only.

Copy this checklist into your first reply and update it as phases finish:

```
- [ ] 0 Ticket, prior plan, size (light/full)
- [ ] 1 Ground truth
- [ ] 2 Survey what exists
- [ ] 3 Root cause and level
- [ ] 4 Outside research (or why it doesn't apply)
- [ ] 5 Draft in the plan file
- [ ] 6 Self-review
- [ ] 7 Review summary and discussion, until finalized
- [ ] 8 Publish
```

## Delegating to forks

When Phases 1 and 4 fan out, each wave of parallel `fork`s launches in a
single message.
A fork inherits this context, but a vague brief still leads to duplicated work
or gaps, so every brief states:

- **Objective:** the one question this fork answers.
- **Boundaries:** what belongs to other forks; read-only, no edits.
- **Sources:** where to look, primary sources first.
- **Output:** conclusions with citations (file path and line, doc section or
  URL, pinned version, date), at most about 800 words, no raw file, page, or
  diff dumps.

Fold every fork's findings in before moving on.

## Phase 0 — Enter plan mode, resolve and size the ticket

1. `EnterPlanMode` — first, before any research, so nothing that follows can
   slip in an edit.
2. Accept a Linear URL or a bare identifier. From a URL, extract the
   identifier (`[A-Z]+-\d+`) rather than guessing from the slug. If it doesn't
   resolve, ask before guessing.
3. `get_issue` for title, description, state, team, labels, and attachments;
   `list_comments` too — acceptance criteria often live in comments.
4. If a prior run's plan is attached (filename `plan-<identifier>.md` or title
   starting `Plan: <identifier>`),
   `get_attachment` and read it, tell the user what it covers, and ask with
   `AskUserQuestion`: update it (recommended) or start fresh. Either way note
   its attachment id; Phase 8 replaces it.
5. Size the ticket. **Light** when the change fits in one sentence, stays in
   one area, and touches no public API, config, or wire shape: run every phase
   inline without forks, skip Phase 4 with a one-line reason, and mark plan
   sections that don't apply "n/a". Otherwise **full**. When a later phase
   shows a light ticket isn't (Phase 3 finds a design cause, a public surface
   changes), switch to full and say so.

## Phase 1 — Ground truth before anything else

Never assume, always verify — the one rule everything else follows from.

1. In every repo the plan touches, `git fetch origin` and read from
   `origin/main`, not a possibly-stale checkout; say so if local `main` is
   behind.
2. Read the full ticket and comments — never plan from a title.
3. Verify anything outside this repo in its actual source, never from memory
   or a name:
   - Sibling repos: `band-ai/band-sdk-core`, `band-ai/band-sdk-typescript`,
     `thenvoi/thenvoi-platform` (platform backend), `thenvoi/fern` (source of
     the generated REST client). Use a local checkout when one
     exists (fetch there too), otherwise read them with `gh`; ask the user
     where a checkout lives rather than guessing.
   - Third-party libraries and APIs: current primary docs plus the source of
     the pinned version. A capability that only exists in a newer release, or
     only sounds plausible from its name, is not verified.
4. Read `AGENTS.md`, the linked `docs/*.md` for the area the ticket touches,
   and the guideline skills it lists. They are the project's single sources
   of truth: don't re-derive or contradict them.

If all of this lives in one place, do it inline. Once it spans more than one
independent area (a sibling repo, a library, a separate doc area), give each
area a fork, and have each fork also do Phase 2's survey for its area.

## Phase 2 — Survey what exists

1. Look for an existing mechanism that does this or nearly does — a helper,
   base class, schema builder, toolkit fixture — and plan to extend it. Build
   new only once nothing usable exists. Forks from Phase 1 already covered
   their areas; use their findings.
2. Find the single source of truth for every value, policy, or constant the
   plan touches. The plan extends it, never adds a parallel one. This is a
   synthesis across all areas, so do it here, not in a fork.

## Phase 3 — Find the root cause and the right level

Run this before drafting. Its output decides where the change lives. Do it in
this context, not a fork: it's a synthesis, and no single area shows the
whole flow.

1. **Name the underlying problem.** Keep asking "why does this happen?" or
   "why is this needed?" until the answer is a cause in the system, not the
   place the symptom shows up. State it in one sentence. If the ticket asks
   for a symptom-level fix, say so and name the cause it would leave in place.
2. **Take the high-level view.** Trace the flow end to end: which components
   produce, transform, and consume the thing at fault, which layer owns each
   rule, and which repo boundaries it crosses. Decide whether this is a
   one-off defect in sound design, or whether the design makes this class of
   bug likely. Signs it's the design:
   - the same fix would be needed in more than one place (several adapters,
     both SDKs, producer and consumer);
   - a rule is enforced in the wrong layer, or owned by a component that
     shouldn't know about it;
   - the natural fix is a special case, flag, guard, retry, or `try`/`except`
     next to the symptom;
   - earlier workarounds already sit around the same spot.
3. **Pick the level.** Sound design: a local fix at the cause. Design is the
   cause: the architectural adjustment — move the responsibility, unify the
   duplicated paths, or put the rule in the layer that owns it. Architectural
   doesn't mean large; choose the smallest change that removes the cause where
   it lives. A patch is acceptable only with a stated reason the cause can't be
   reached from here (another team's repo, an external bug).
4. **Size it against the ticket.** If the right-level fix is clearly larger
   than the ticket, neither expand scope silently nor downgrade to a patch
   silently: carry both options and their trade-offs to Phase 7 for the user.

## Phase 4 — Research best practices and third-party options

Search for Phase 3's root cause, not the ticket's symptom.

1. **Decide relevance.** Research when the problem has a general equivalent: a
   mechanism (retry, backoff, caching, rate limiting, reconnection, schema
   validation, concurrency control), a protocol or spec (A2A, ACP, MCP,
   Phoenix Channels, JSON-RPC), or a problem other agent SDKs also solved.
   Skip only when it's specific to this codebase, and give the one-line reason
   in the summary.
2. **Best practices and case studies:** how this is solved well elsewhere,
   from the spec, maintainers' guidance, comparable projects' source, and
   postmortems. If established practice puts the fix at a different level,
   re-decide Phase 3 step 3.
3. **Third-party options:** look for a well-maintained library before planning
   to hand-roll a nontrivial mechanism.
4. **Choose** between adopting a library, extending what Phase 2 found, and
   building new, with trade-offs stated. A close call is a question for the
   user in Phase 7.

Run it as forks in two waves, each using `WebSearch` and `WebFetch`. First
wave: one fork for best practices and case studies that also returns the
candidate libraries. Second wave: one fork per serious candidate, vetting it
with [research.md](research.md).

## Phase 5 — Draft the plan

Write the draft to the plan file. If Phase 0 kept a prior plan, start from it:
keep what Phase 1 still confirms, fix or drop what's stale, extend what it
missed, and drop its Implementation Prompt — Phase 8 writes a new one from
the final plan. Where a decision still waits on the user (Phases 3 and 4), the draft
uses the option you recommend and lists the other under Alternatives
considered; Phase 7 asks.

Sections, in order, each short; "n/a" is fine where one doesn't apply:

1. **Problem:** the root cause in one sentence, the chosen level (local fix or
   architectural adjustment), and why.
2. **Goals and non-goals:** what the change must achieve, and reasonable
   things it deliberately won't do, including deferred followups.
3. **Scenarios:** concrete "given / when / then" lines for the main flow, the
   edge and failure cases, and nearby behavior that must stay unchanged. Each
   names who or what triggers it (a user in a room, an adapter, the platform,
   a CLI call) and what they observe, not which function runs.
4. **Approach:** the design, the files it touches, and the outside option
   chosen in Phase 4 with the sources it rests on. A library capability that
   only running it can confirm is stated here as unconfirmed, with the call
   that confirms it.
5. **Alternatives considered:** doing nothing, the symptom patch the ticket
   implies, and one or two real alternatives, each with the reason it lost.
6. **Compatibility:** every public symbol, config field, default, and wire
   shape the change touches; whether each change breaks callers, the semver
   impact, and the deprecation path.
7. **Cross-cutting:** one line each for security, logging and observability,
   paid-LLM cost, concurrency and ordering, and Windows/Ubuntu behavior.
8. **Test plan:** at least one test per scenario, at the lowest level that
   really exercises it.
9. **Definition of done:** automated checks as exact commands (the targeted
   `uv run pytest ...` plus `AGENTS.md`'s Pre-Commit Checklist) and manual
   checks (live E2E, editor flows), each with its expected result.

Design rules:

- The smallest elegant solution that removes the root cause, at the level
  Phase 3 chose. No speculative abstraction, no designing for a hypothetical
  future ticket.
- DRY: one named constant, enum, or helper per concept; no magic values.
- Fits the surrounding code's idiom and the `band-python-conventions` skill,
  so the diff reads like it was always there.

Test rules:

- Test behavior that can actually break, not defaults or implementation
  trivia (see the `band-python-test-conventions` skill). A test that would
  still pass under a symptom-level patch doesn't cover the root cause.
- Where the area has an end-to-end flow, include one on the `tests/e2e/baseline`
  toolkit (see the `band-write-e2e-test` skill) instead of new plumbing.
- A missing helper, fixture, or local peer is its own step, before the step
  that uses it — never an empty mock in place of a real boundary.

Scope rules:

- Everything traces back to the ticket's problem, its acceptance criteria, or
  the root cause behind them.
- Out-of-scope but important: small (localized, no design or API change) →
  fold in and flag as an in-passing extra; large → a non-goal and a candidate
  followup for Phase 6.

## Phase 6 — Self-review the draft

Treat every claim and decision in the draft as a claim to verify, not a
conclusion — the same discipline `band-here-is-a-review` applies to someone
else's review.

1. **Independent review.** Give a fresh `general-purpose` subagent only the
   ticket and the plan file, not the research trail — an agent without the
   drafting history isn't anchored to what this session believes it checked.
   Tell it the plan describes `origin/main` of each repo, to be read with
   `git show origin/main:<path>`, not the working tree. Ask it to:
   - re-verify every factual claim (this exists here, this API behaves so,
     this library ships this in the pinned version) and list what it can't
     confirm or finds contradicted;
   - challenge the root cause and level: is this the real cause, or would
     someone looking at the whole system call it a patch?
   - run a pre-mortem: it's three months later and this change was reverted —
     give the three most plausible reasons;
   - flag only gaps that affect correctness, the root cause, compatibility, or
     the ticket's requirements. A reviewer asked for gaps always finds some;
     polish is not a finding.
2. **Sort its findings:**
   - already answered by the plan → make the plan clearer;
   - minor → decide it, and record it as your call for the summary;
   - the user's decision → a question for Phase 7.
   Each pre-mortem reason becomes a test, a plan change, or a stated risk.
3. **Scope gate:** re-apply Phase 5's scope rules. Large and out of scope
   goes to Phase 7 as a question (fold in, followup ticket, or drop) — never
   silently kept or dropped. File a followup ticket only after the user
   chooses one; Phase 8 files it.
4. **Consistency:** every scenario is handled by the approach and covered by a
   test; no design step or test exists without a scenario or the root cause
   behind it; nothing contradicts a goal or non-goal.
5. **No open questions** in the plan text: no "TBD", "open question", or
   "decide later". Every remaining decision is asked in Phase 7.
6. **No narration:** the plan states facts, decisions, and references only —
   no research or discussion history (see "The plan is NOT a history book").

## Phase 7 — Present and discuss (never offer to implement)

1. Never call `ExitPlanMode`, and never ask "should I implement this" or
   "ready to proceed" in any form.
2. Present a review summary the user can check in a couple of minutes, not
   the full plan. Its job is to catch a misread intent quickly. About one
   screen, in this order:
   1. **What I understood:** the goal, the root cause, and the chosen level,
      in one or two sentences.
   2. **Scenarios:** one line each. This is the main thing to review: if a
      scenario reads wrong, the design is wrong.
   3. **Where this might not match your intent:** places the plan interprets
      an ambiguous ticket, departs from what it literally asked, or makes a
      call the user might make differently — a different level than the
      ticket implied, a library over building, a behavior or compatibility
      change, a non-goal, an in-passing extra, and your calls from Phase 6. Riskiest first, each
      with the alternative it rejected. Never empty without saying why.
   4. **Approach:** the design in a few bullets, the files it touches, and the
      research's conclusion or the reason it didn't apply.
   5. **Decisions needed:** the questions from Phases 3, 4 and 6, asked
      directly so the user resolves them.
   Offer the full plan on request instead of pasting it.
3. Close by inviting feedback, not action — "what would you change?", not
   "want me to start?".
4. Treat everything the user says next as feedback. Update the plan file and
   re-apply Phases 3–6 to what the feedback changes: a new direction can move
   the root cause, the level, or the best outside option and gets a fresh
   independent review, while a wording fix needs neither. Present the same
   summary, opened with what changed since the last round.
5. Move to Phase 8 only on an explicit signal ("looks good", "finalize this",
   "attach it", "lock it in"), never on a quiet pause.

## Phase 8 — Publish the finalized plan

1. Re-read the plan file as a whole: the agreed version, not an earlier round;
   internally consistent; citing a source only where a decision rests on it.
   Strip every trace of narration or history (see "The plan is NOT a history
   book" above). Any open question or TBD left means it isn't final — go back
   to Phase 7.
2. Write the "## Implementation Prompt" section at the end, replacing any
   existing one: a self-contained prompt a
   separate agent can run verbatim to implement the plan exactly as agreed.
   It carries the ticket identifier and title, the root cause and level (so
   the implementer doesn't fall back to a symptom patch), the non-goals, the
   chosen library and pinned version if any, the scenarios, the files and
   tests to touch, and this repo's non-negotiables (root cause not a patch,
   DRY, no magic values, the test plan as written). It ends with the
   Definition of done, and requires the implementer to show each check's
   output as evidence rather than claim success. When Approach marks a
   capability unconfirmed, the prompt's first step is installing that
   version and exercising the confirming call.
3. File any followup ticket the user chose in Phase 7, and link it from the
   plan's non-goals.
4. Attach the plan file to the ticket and update the ticket, following
   [publish.md](publish.md).
5. Report the attachment URL.
