# Final scenario validation

Use this gate on the agreed design before publishing the plan. Scenario lists
and a pre-mortem identify what to check; this pass works through whether the
proposed design handles it. Scale the depth to the change, including light plans.

## Exercise the design

1. Derive expected outcomes from the ticket, agreed goals and verified
   contracts, independently of the proposed approach. Reuse the plan's scenarios
   and add material omissions. Cover happy flows, edge/boundary cases and
   failure, crash and recovery paths; explain categories that do not apply.
   Consider partial completion, cancellation, retries, duplicate work, restart
   and concurrent ordering where the changed behavior makes them relevant.
2. For each scenario, specify the starting state, trigger or failure point,
   and observable outcome or invariant. Trace the actual sequence through the
   proposed components and owning layers, including state, effects and cleanup.
   For relevant crash paths, check interruption before and after an effect:
   what survives, what is reported, and how retry or restart recovers. Do not
   assume cleanup runs after process death or that a retry is safe.
3. Ground each trace in current source and verified dependency contracts;
   distinguish existing behavior from proposed steps. Use existing read-only
   checks or probes when available and permitted. Stay within the planning-only
   boundary: this gate does not authorize implementation, new test code, or
   live side effects. Label reasoning as a design walkthrough and record any
   executed check with its command and result. A future test is not evidence
   that the design already works, and a walkthrough is not a runtime pass.
4. Keep a compact **Design validation** matrix in the plan. For each named
   scenario, record its expected outcome, design trace with sources, verdict
   (supported, deficiency or unconfirmed), and the implementation test/check
   still needed. State execution limits and remaining assumptions explicitly.

## Repair and repeat

- Fix in-scope deficiencies in the approach at the owning layer, then update
  affected scenarios, compatibility, test plan and definition of done together.
  Do not weaken an expected outcome merely to make the design pass.
- Re-run affected and interacting scenarios after each repair, and check the
  complete matrix for contradictions or coverage gaps. Keep final results in
  place rather than accumulating a review history.
- If a finding needs a user decision or materially changes agreed behavior,
  scope, compatibility or design direction, return to Phase 7 with the finding
  and options. Re-apply the relevant research and review phases. Earlier
  finalization does not approve a changed design; obtain a new explicit signal
  before continuing. Proposed out-of-scope work follows the existing scope gate.
- Publish only when required scenarios are supported and no unresolved
  assumption undermines correctness. A blocked check is not a pass: resolve it
  or return to discussion about a verifiable design or a revised scope. Record
  non-blocking risks with the specific implementation checks that address them.

Carry the validated scenarios, remaining checks and non-blocking limitations
into the Implementation Prompt. Plan validation supports the design decision;
the implementer must still prove the resulting code at the required boundaries.
