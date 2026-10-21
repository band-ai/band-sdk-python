# Cycles

One cycle = one full-PR review with every group, its verification, and its
fix round. Nothing needs an extra cycle to get fixed, the last cycle's
findings included. Fresh cycles shift focus and surface missed issues, so
later cycles never narrow to the fix area; closed no-fix records are the
only history their agents see. "Resolve the target and scope" and "Simplify" run once, up front. Without `--fix`, exactly one
review runs, then the verdict-only close-out.

Convergence asks whether fixes are settling or each fix creates the next
problem. A fresh finding in code no fix touched is a missed issue, not a
signal: it's fixed like any other. Only fix-induced findings and regressions
can mark a run as not converging.

**Ledger.** Record the SHA at each cycle's start. For every item verified
CONFIRMED or PARTIALLY RIGHT (own findings and existing feedback alike) add a
line: cycle, id, severity, `file:symbol`, invariant or concern broken, fixing
commit (once known), origin. Existing feedback has no severity; the
coordinator assigns one by the Comment bar. The coordinator assigns origin:
- `regression of <id>` — matched a thread this run fixed.
- `fix-induced from <parent id>` — its lines were last written by the
  parent's fix commit (blame against commits since cycle 1's start SHA; the
  `simplify` commit doesn't count).
- `fresh` — anything else.

Unverified, rejected, deferred, and dropped findings never enter the ledger;
a claim the skeptic drops becomes a no-fix record instead.

**Decide** after each cycle's verification, checking the rows in this order
and taking the first that matches. Only `issue:` findings count, except
regressions, which count at any severity. Nits and suggestions never block
convergence and are fixed along the way.

1. **Not converging** — any of:
   - **No net progress** (never in cycle 1): the previous cycle's fixes
     induced at least one verified `issue:` finding, and at least as many as
     the `issue:` findings they fixed.
   - **Fix chain**: a fix-induced finding whose parent was fix-induced.
   - **Regression**: a finding tagged `regression of <id>` (any severity), or
     an earlier fix's regression test going red.
   → Halt: see "On non-convergence".
2. **Converged** — zero verified `issue:` findings this cycle (fresh,
   fix-induced, or from existing feedback; deferred ones don't count), and
   row 1 didn't match. Possible
   as early as cycle 1. → **Stop the run**: fix this cycle's nits and
   suggestions in the same round, then end. Start no further cycle and no
   re-review, even with cycles left; those last fixes are unreviewed, so
   say so in the report. The verification gate must still pass.
3. **Continue** — verified `issue:` findings exist and row 1 didn't match
   (fresh findings and a single fix-induced bug are normal corrections):
   fix, then run the next cycle.
4. **At the cap** (cycle N) rows 1–3 decide the same way, but row 3 fixes and
   then ends with no next cycle. The run is "capped": those fixes are
   unreviewed, and the report says so.

**On non-convergence, stop patching and bring the user into the loop.**
Another local fix would be a bolt-on. Take the verdict-only close-out for
the whole cycle (nits and suggestions stay unfixed too) so the verified
threads stay open as evidence, and start no further cycle. Then:

1. Design-review the hotspot: read its ledger lineage, name the shared root
   cause (unclear state ownership, a missing abstraction, an unmodeled
   lifecycle), and propose one or two design-level fixes with tradeoffs and
   blast radius.
2. Ask the user, naming the signal that fired and
   presenting it as "a real design issue, not patch material". Options: apply
   a proposed design fix, rethink it differently (their direction), or
   stop here with threads left open.
3. Wait. Apply no design change and open no ticket without their answer.
4. If they pick a design change, have the fix stage apply it as one root-cause
   change and run the gate; the coordinator commits and pushes, then resumes the
   loop. Their decision restarts the cycle cap and the ledger lineage for that
   hotspot, even if the run had reached the cap, and the open threads reach
   the next cycle as unresolved digest items. If they stop, go to the final
   report.
