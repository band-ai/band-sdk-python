# Reply alternatives: U + sugar adversarial conformance

## Landing correction (2026-10-15)

Ada's `op-6nsokny2`, session `pmxxoweaog4wk`, requests one landing commit,
separate from the scaling suite. Consumers: Lucas (combined conformance gate),
Nadia (site leak gate), and Ada (landing handoff).

| Requested item | Acceptance criterion | Disposition |
|---|---|---|
| Portable evidence | Given historical command logs, when normalized, then checkout paths are repo-relative and other home paths use `~/`; results, timestamps, hashes, and exits stay unchanged. | As expected: the three validation JSONs regenerated with path-only substitutions, checked against the parent objects. |
| Generator prevention | Given nested evidence or a harness error, when serialized by the runner, then paths use the same portable representation. | As expected: shared evidence serializer and `--normalize-evidence`; three regression checks observed RED then GREEN. |
| Both multi-ref positive controls | Given each ref field is explicitly matched except the one eligible for inference, when compiled, then compilation succeeds without weakening `unmatched_reference`. | As expected: both `previous` fields bound; native and portable sources regenerated. Only these two of 48 vectors changed. |
| Requested d3d8df5 run | Given Kenji's pinned parser and the gate's Omar runtime, when all checks run, then all 53 pass, including the old-reader checks. | As expected in the isolated integration below: 51/53 RED → 53/53 GREEN, actual exits 1 → 0. The parser-only head instead fails at its missing wait-renewal runtime; that failure is retained separately. |
| Site leak gate | Given the generated site, when Nadia's unchanged reviewer gate runs, then no blocking or secret pattern is found and all internal links resolve. | As expected: reviewer build and gate pass without credential access. Ada explicitly accepted reviewer mode; Nadia owns the final spec-sync stage at landing. The existing detector regex is unchanged. |
| Delivery boundary | Given this fix, then it remains separate from scaling work and has no publication side effects. | As expected: one local landing-fix commit; no merge, deploy, site publication, or complete combined-gate acceptance claimed. |

Validation integration `9a7ed99acc915b034cca98728ff99b149a56d5f2` is Lucas's
`70e41f56b035dc6c01f9e1d3a894eca50abafee1` gate trial (including Omar `8db902b`)
merged with immutable Kenji `d3d8df589deb0904439a52dbc2225dda44b8b631`.
The latter's compiler and reply-checker sources were checked byte-for-byte.
Existing implementation worktrees were not edited.

Evidence: [before](evidence/landing-before.json),
[after](evidence/landing-after.json),
[parser-only harness failure](evidence/landing-parser-only.json),
[two-vector parser RED](evidence/landing-vector-red.json), and
[validation summary](evidence/landing-validation.json).
The original B/U delivery descriptions and RED evidence below are historical;
the 53/53 local suite result supersedes their pending-feature status only for
the integration named above, not for the entire combined gate.

```bash
# Path-only maintenance: no old commands rerun and no historical results replaced.
python3 -m contracts.spec.tools.repliesadversarial --normalize-evidence
```

## Original delivery and historical evidence

Owner: Felix. Consumers: Lucas (conformance and combined gate), Kenji (compiler).
Ada's U migration is `op-cgb7ffgk`, session `5ecoid32wkr2p`, on branch
`felix/replies-adversarial-20261015` at `26aaf85`. It follows Eric decision 4
in `ubf-replies/COVERAGE.md` and the Option U / Implicit correlation sugar
sections of `ubf-replies/SYNTAX-OPTIONS.md`. The original suite was delivered
under `op-5jkmbagl`, session `satkyaqkzlvlp`.

Lead decision 7 follow-up, on signed U-suite commit `1ca5735`, keeps the same
operation/session. It corrects the two-block classification and adds the three
malformed-alternative cases requested by Ada.

The configured local origin and Kenji's relevant compiler branch were fetched
before this change. The immutable baseline, refreshed to Kenji's committed head
at 07:38Z, is `4eb674ca2f10fe53f7df206e5fea78b779d0555d` from
`kenji-replies/compiler` (intake head was `5c00d5e`). It still implements B,
not U in the parser; its new U formatter support does not implement the parser.
An owned detached worktree was force-built for this
baseline; implementation worktrees were not changed or used dirty.

## Format and runner

All 48 vector sources use U, including deliberately malformed U (no B
reply/timeout lines remain), embedded in exact
`band-contract/file-source/1` envelopes in
[suite.json](suite.json). The embedded scenarios use the existing
`ContractLab.Scenario.run/2` compile/replay format with independent `expect`
objects. No tracked `.band` files or `test_*.py` discovery entries are added
before the compiler lands. The runner prints one JSON report and exits **1**
for either a failed vector or a harness failure; **0** requires every check.

```bash
# Build both selected checkouts first, using the repo's Elixir/OTP toolchain.
python3 -m contracts.spec.tools.repliesadversarial \
  --repo /absolute/path/to/built/combined-checkout \
  --old-repo /absolute/path/to/built/immutable-95c9b25-checkout \
  --report /absolute/path/to/owned/report.json
```

`--old-repo` is REQUIRED. It must have the exact pinned HEAD and no tracked
changes. Both VMs have a 30-second deadline and 16-MiB I/O bound via the existing
adapter driver. Elixir must be on PATH. The script does not fetch, build, merge,
or change the selected code checkouts. `--write` deterministically regenerates
the JSON inputs and [expected-code/trace table](EXPECTED.md), not observed outputs.

[semantic-inputs.json](semantic-inputs.json) additionally uses the existing
portable `cases` / `requests` / RFC-6901 `assertions` input shape. It deliberately
has no implementation-blessed `expected` outputs, manifest pins, or assigned
requirement IDs yet. Lucas owns those integration decisions after the heads
land; this branch does not modify the default gate or claim qualification.
The native runner exercises real Elixir modules, not a model of their behavior.
Portable inputs are provided for future adapter comparison; no second compiler
or Python runtime was executed for these vectors.

## Acceptance criteria and expectation ledger

All listed vectors/probes are implemented locally. Unless stated otherwise,
execution against Kenji's pinned head is observed RED at the missing U syntax;
GREEN on U-enabled Kenji + Omar is **not yet**. Every check is automated.
This delivery is not a combined-gate acceptance, review, merge, or deployment.

| Requested boundary | Given / When / Then | Disposition |
|---|---|---|
| Overlap witnesses | Given `x=true`, `y=true`, `(false,false)`, when compiled, then reject the later intersecting row with `reply_overlap`, even though every row is useful and the union is total. Bare fallback also overlaps. | As expected: implemented, baseline RED. |
| S4 16×4 coverage blow-up | Given 16 rows on 64 staggered booleans, then reject overlap without enumerating 2^64 payloads. Given disjoint tag-separated rows over 2^52 or 2^196 assignments, then report `reply_not_exhaustive`; all 16 combinations on four shared booleans compile. | As expected: implemented, baseline RED. Limits 17 rows, 5 literals, 272 total alternatives are also pinned. |
| Unknown literals | Given an undeclared enum/boolean literal, when compiled, then `reply_match`; 128 distinct previously absent enum-literal names must still not be existing VM atoms afterwards. | As expected: implemented. Baseline has zero newly interned probe names but wrong `syntax` code, so the complete check remains RED. |
| C1 re-entry | Given request 1 → revise → request 2, when a first-arriving late reply references request 1 using the current version, then `reference`, version unchanged, wait still open; request-2 reply then succeeds. Before re-entry the late kind is `illegal_message`. | As expected: implemented, baseline RED. |
| Revised acyclic witness | Given W1 → requester cancel → W2 sharing role/kind, when a new late proposal references W1's request, then `reference`, not acceptance for W2. The current W2 reply succeeds. | Changed per Ada's Lead decision 2: implemented, baseline RED. |
| Rebinding/correlation | Given no eligible ref, several eligible refs without explicit `=$`, a wrong `=$`, or local/anystate rebinding, then `reply_correlation`. Given one eligible ref, missing `as`/`=$` is legal. Cycle-without-max-entries stays `reply_cycle`; ordinary rule/event acceptance of a reply-only kind still fails (SR3). | Changed per Eric decision 4 and Ada's clarification: implemented, baseline RED. |
| U source migration | Given the previous 31 sources, when rewritten to U, then all 28 unaffected source bodies, compile expectations and existing runtime scenarios retain their semantics exactly. | As expected: checked; three former missing-binding/reference cases are explicitly replaced below. |
| Stale reply, implicit binding | Given W1 → cancel → W2 with inferred `w1`/`w2` bindings, when a new late reply references W1 at current version 3, then `reference`, with W2, its binding, wait, facts, receipts and version unchanged; the current reply succeeds. | As expected: implemented in native and portable vectors, baseline RED. |
| Several ref fields | Given two refs eligible for the head kind and no `=$`, then `reply_correlation` at the alternative. Explicit selection is legal; multiple refs with only one eligible field are also legal. No eligible field, including a ref to an unrelated kind, is rejected. | As expected: negatives and positive controls implemented, baseline RED. |
| Generated wait-name collision | Given targetless `request()` with U alternatives, then infer `request_wait`. An explicit state with that name produces `duplicate`. A second head declaring a block for the same implied W produces `reply_route` at that later head (body line 12). | Changed per Lead decision 7: classification corrected; state-name duplicate and replay control retained, baseline RED. |
| Two positions of `|` | Given line-start U alternatives followed by an inline timed rule, then both reply rows reach their own targets, the timed success target remains `done`, and its inline timeout target remains `stalled`. | As expected: both row selections and timed-rule timeout implemented, baseline RED. |
| Malformed `|` / `=>` shapes | Given `|` without a `=>` opener or two `=>` openers, then `reply_timeout`. Given two alternatives on one physical line, then `syntax` at the offending alternative (body line 9), not an old parser's error at the head. | As expected per Lead decision 7: three automated negatives added, baseline RED. |
| V4 multi-target renewal | Given 30-minute run, 10-minute wait and terminal/20-minute continuations, when heartbeat arrives at minute 5, then `insufficient_time` in both row orders; original minute-10 timeout still fires. With 15-minute continuation, minute 5 fits exactly and +1 ms fails. | As expected: implemented, baseline RED. |
| Reserve 0/3 → 4 | Given same-kind reply alternatives with follow-on reserves 0 and 3, when the head enters W, then reserve 4 (not 2 or the first branch's 1). Total budget 4 refuses the head; budget 5 permits both cheap and full three-follow-on paths. | As expected: implemented, baseline RED. |
| Old-reader fail closed | Given real 95c9b25 modules, then new source fails compilation, a synthetic compiled unknown-extension marker yields `unsupported_extension`, and a valid old saved state with an added `wait` key yields `invalid_checkpoint`. | As expected: all three observed PASS. Always required, never silently skipped. |
| Runner correctness | Given a legacy real replay, then the correct outcome succeeds and a deliberately false outcome produces `expectation_mismatch`. Missing old-reader input fails the runner. | As expected: observed PASS. |

## Evidence and boundaries

Current decision-7 baseline:
[u-decision7-baseline-red.json](evidence/u-decision7-baseline-red.json),
**4 pass / 49 fail, actual runner exit 1**, against the same immutable Kenji
head above (re-fetched for this revision). All 48 U compile/replay vectors and
the unknown-literal compound probe remain RED; no harness failure occurred.
The one-line-alternatives case pins line 9 specifically so the old parser's
`syntax` at head line 8 cannot falsely pass it. Replay control and all three
old-reader probes pass. [u-decision7-validation.json](evidence/u-decision7-validation.json)
records the observed classification RED → GREEN, exact retention of the other
44 vectors, source identities, deterministic regeneration and actual exits.
The full U-enabled feature gate remains not yet.

Initial U baseline (historical): [u-baseline-red.json](evidence/u-baseline-red.json),
**4 pass / 46 fail, actual runner exit 1**, against the force-built immutable
Kenji head above. All 45 U compile/replay vectors and the unknown-literal
compound probe fail at unsupported U syntax. The real replay mutation control
and all three required 95c9b25 old-reader probes pass. This is expected feature
RED, not a harness failure. [u-validation.json](evidence/u-validation.json)
records source equivalence, generator determinism, actual exits, source/build
identities, and the schema-only gate. No U-enabled feature GREEN is claimed.

Decision-4 replacements (not silent expectation weakening):

- `c1-cycle-without-binding` → `c1-cycle-no-eligible-ref`: still
  `reply_correlation`, because the reply's field is now explicitly non-ref.
- `c1-acyclic-missing-binding` → `c1-acyclic-implicit-binding`: now compile OK.
- `c1-acyclic-missing-reference` → `c1-acyclic-implicit-reference`: now compile OK.

Historical B-suite evidence is preserved unchanged:
[baseline-red.json](evidence/baseline-red.json) records the original run:
**4 pass / 32 fail, actual runner exit 1**. All 31 new-feature compile/replay
vectors plus the unknown-literal compound probe are RED because old modules
report `syntax`. Four passing results are the replay mutation control and the
three old-reader checks. [missing-old-repo.json](evidence/missing-old-repo.json)
records fail-closed runner admission without the mandatory old checkout.
[validation.json](evidence/validation.json) records its actual subprocess exits,
deterministic generator hashes, compiled-module hashes and the pinned
Elixir 1.18.4 / OTP 27.3.4.17 build. The existing schema-only gate passed
(164 cases, 369 assertions, exit 0; adapter execution false). This is structure
preservation, not new-feature conformance GREEN.

The old-engine probe deliberately uses a synthetic extension-marked legacy
compiled contract, and the old-codec probe adds the new key to a valid legacy
saved state. These establish the actual old gates, **not** a full cross-version
restore of a future compiled artifact. Candidate auto-marker assertions and
Lucas's normalized/restore/digest coverage remain required at integration.
Timing fields and the bounded VM are guards against unbounded enumeration, not
a cross-machine benchmark or proof of the chosen algorithm.

No implementation fix, merge, deployment, site publication, combined gate,
old-corpus digest qualification, or security approval is claimed. Candidate
runtime traces and rejection invariants cannot turn GREEN until both heads
land. At that point, reference/renewal/capacity rejection checks additionally
compare wait, binding, fact, receipt and version state before and after rejection.

### Input identity (2026-10-15)

The current Lead decision 7 `COVERAGE.md` hash is in
[u-decision7-validation.json](evidence/u-decision7-validation.json). Initial U
migration inputs (`SYNTAX-OPTIONS.md`, `COVERAGE.md`) have their exact hashes in
[u-validation.json](evidence/u-validation.json). The following table is the
historical initial-suite input identity, retained for provenance.

Read from Ada's `ubf-replies/` design bundle, plus Victor's acyclic
`RECHECK-CHECKS.json`; COVERAGE's core-syntax, always-correlated and SR3 decisions
override the older design paragraphs.

| Input | SHA-256 |
|---|---|
| DESIGN.md v2 | `5c4dac7570aff1a567f1d443c79d1cadb96bc2829c9eb62d2befa4d155ec1092` |
| REVIEW.md | `622ffa178d4086473df37086e51eb801bb1165492b8b4ac92a7a940aa5f90554` |
| SECURITY.md | `fa474a57023bd6701ca7a4a6162224fe679681451a3a69406612053af8fe652e` |
| COVERAGE.md | `7407285166cbab9c7b88a3552837a4733732c2e0b558cdb9b0434c001b46eb82` |
