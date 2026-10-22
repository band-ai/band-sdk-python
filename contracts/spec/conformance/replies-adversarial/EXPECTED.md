# Expected codes and traces

Generated from independently authored inputs, not observed implementation outputs. Trace entries show status / reason / state / outcome / version when pinned; terminal state names are not inferred. `None` means JSON null.

| Vector | Required result |
|---|---|
| `overlap-useful-exhaustive` | reply_overlap (body line 10) |
| `overlap-bare-fallback` | reply_overlap (body line 10) |
| `coverage-false-false-gap` | reply_not_exhaustive (body line 8) |
| `disjoint-total-control` | compile OK; auto contract.replies/1; accepted / waiting / 1 → accepted / done / 2 |
| `s4-16x4-staggered-booleans` | reply_overlap (body line 72) |
| `s4-disjoint-large-domain` | reply_not_exhaustive (body line 55) |
| `s4-disjoint-bigint-domain` | reply_not_exhaustive (body line 55) |
| `s4-16x4-total-boundary` | compile OK; auto contract.replies/1 |
| `limit-17-rows` | reply_limit |
| `limit-5-literals` | reply_limit |
| `limit-contract-272-rows` | reply_limit |
| `unknown-enum-literal` | reply_match |
| `unknown-boolean-literal` | reply_match |
| `c1-late-reply-reentered-wait` | compile OK; auto contract.replies/1; accepted / waiting / 1 → accepted / revising / 2 → rejected / illegal_message / revising / 2 → accepted / waiting / 3 → rejected / reference / waiting / 3 → accepted / done / 4 |
| `c1-cycle-no-eligible-ref` | reply_correlation (body line 8) |
| `c1-cycle-without-max-entries` | reply_cycle |
| `c1-rebind-state-event` | reply_correlation |
| `c1-rebind-anystate-event` | reply_correlation |
| `c1-acyclic-cross-wait` | compile OK; auto contract.replies/1; accepted / w1 / 1 → accepted / between / 2 → accepted / w2 / 3 → rejected / reference / w2 / 3 → accepted / second_done / 4 |
| `sugar-stale-implicit-binding` | compile OK; auto contract.replies/1; accepted / w1 / 1 → accepted / between / 2 → accepted / w2 / 3 → rejected / reference / w2 / 3 → accepted / second_done / 4 |
| `c1-acyclic-rebind` | reply_correlation |
| `c1-acyclic-implicit-binding` | compile OK; auto contract.replies/1 |
| `c1-acyclic-implicit-reference` | compile OK; auto contract.replies/1 |
| `c1-acyclic-wrong-reference` | reply_correlation |
| `c1-reply-kind-ordinary-rule` | reply_correlation |
| `c1-reply-kind-anystate-event` | reply_correlation |
| `sugar-no-eligible-ref-kind` | reply_correlation (body line 10) |
| `sugar-ambiguous-ref-fields` | reply_correlation (body line 8) |
| `sugar-ambiguous-refs-explicit-control` | compile OK; auto contract.replies/1 |
| `sugar-one-eligible-of-several-refs` | compile OK; auto contract.replies/1 |
| `sugar-implicit-wrong-reference` | reply_correlation (body line 9) |
| `sugar-rebind-wait-name-state` | reply_correlation (body line 14) |
| `sugar-rebind-wait-name-anystate` | reply_correlation (body line 14) |
| `sugar-auto-wait-positive-control` | compile OK; auto contract.replies/1; accepted / request_wait / 1 → accepted / done / 2 |
| `sugar-auto-wait-name-clash` | duplicate |
| `sugar-auto-wait-two-heads-clash` | reply_route (body line 12) |
| `u-pipe-no-opener` | reply_timeout |
| `u-double-opener` | reply_timeout |
| `u-two-alternatives-one-line` | syntax (body line 9) |
| `u-pipe-reply-vs-timed-rule` | compile OK; auto contract.replies/1; accepted / waiting / 1 → accepted / reporting / None / 2 → accepted / done / 3 |
| `u-pipe-reply-vs-timed-rule-second-alternative` | compile OK; auto contract.replies/1; accepted / waiting / 1 → accepted / done / 2 |
| `u-pipe-timed-rule-timeout` | compile OK; auto contract.replies/1; accepted / waiting / 1 → accepted / reporting / 2 → ticked / stalled |
| `v4-renew-all-targets-terminal-first` | compile OK; auto contract.replies/1; accepted / waiting / 1 → rejected / insufficient_time / waiting / 1 → ticked / stalled |
| `v4-renew-all-targets-long-first` | compile OK; auto contract.replies/1; accepted / waiting / 1 → rejected / insufficient_time / waiting / 1 → ticked / stalled |
| `v4-renew-exact-boundary` | compile OK; auto contract.replies/1; accepted / waiting / 1 → accepted / waiting / 2 → rejected / insufficient_time / 2 → ticked / waiting / None → ticked / stalled |
| `reserve-0-or-3-underflow` | compile OK; auto contract.replies/1; rejected / completion_capacity / new / 0 |
| `reserve-0-or-3-short` | compile OK; auto contract.replies/1; accepted / waiting / 1 → accepted / done / 2 |
| `reserve-0-or-3-long` | compile OK; auto contract.replies/1; accepted / waiting / 1 → accepted / f1 / None / 2 → accepted / f2 / 3 → accepted / f3 / 4 → accepted / done / 5 |
