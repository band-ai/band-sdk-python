---
name: dependabot-triage
description: Triage this repo's open Dependabot PRs in one batch run — consolidate every bump onto one branch, prove it with the full test suite, approve the PRs that pass (so a maintainer can merge them quickly), and open Linear issues for what needs manual work. Keeps the per-ecosystem open-PR limit from silently starving new upgrades. Use when asked to review, clear, batch, or "handle the Dependabot PRs", or when a dependency upgrade never showed up as a PR.
---

# Dependabot PR Triage

Triage the whole Dependabot queue in a single, test-gated batch: consolidate all
the bumps onto one local branch, run the full suite, **approve** the PRs that
pass so a maintainer can merge them quickly, and file follow-ups for the rest.
By default the skill stops at approval — approval is its verdict and a human
merges; when you are *explicitly asked to drive the merges too*, follow the
"Merging the approved set" section below (it is a serial grind with real
gotchas, not a batch button). The goal is that after a run every mergeable bump
carries the skill's sign-off and every excluded bump has a paper trail — never a
silent close.

## Why this skill exists (the failure it prevents)

Dependabot has a per-`(ecosystem, directory)` **`open-pull-requests-limit`**
(see `.github/dependabot.yml`: the root `uv` config caps at **10**, the
echo-agent `uv` config at **5**, GitHub Actions is grouped into one weekly PR).
When that limit is hit, Dependabot **will not open another PR for that
ecosystem** — including for a genuinely important upgrade — until an existing one
is merged or closed. No error, no notification; the new upgrade is simply never
proposed.

That is how an important major upgrade got lost once: its PR was opened, then
**closed unmerged** (a breaking major nobody was ready for). Dependabot honored
the close ("I won't notify about *this release* again"), a newer version shipped
later, but by then the queue was full of green-but-unmerged PRs — the limit was
saturated, so the newer version was never re-proposed. A closed major PR plus a
saturated limit = a silently starved upgrade.

So a full queue is an alarm, not a steady state. Drain it, and give every
excluded bump a tracked reason so nothing is starved by neglect.

## Step 1 — Survey the queue

List every open Dependabot PR, split by ecosystem, with CI status, age, and bump
size. GitHub Actions PRs are detected by the `/` in the dependency name (their
`github-actions` label is not always applied).

```bash
gh pr list --state open --author "app/dependabot" --limit 100 \
  --json number,title,labels,createdAt,statusCheckRollup > /tmp/ddb-prs.json
python3 <<'PY'
import json, re, datetime as dt
prs = json.load(open("/tmp/ddb-prs.json"))
now = dt.datetime.now(dt.timezone.utc)
def dep(title):
    m = re.search(r"bump ([^ ]+) from", title)
    return m.group(1) if m else "?"
def bump(title):
    m = re.search(r"from (\d+)[.\d]* to (\d+)[.\d]*", title)
    return "?" if not m else ("MAJOR" if m.group(1) != m.group(2) else "minor/patch")
def eco(title, labels):
    if "github-actions" in {l["name"] for l in labels}: return "github-actions"
    return "github-actions" if "/" in dep(title) else "uv"
def ci(rollup):
    st = [c.get("conclusion") or c.get("state") for c in (rollup or [])]
    if any(s in ("FAILURE","ERROR","CANCELLED","TIMED_OUT") for s in st): return "RED"
    if any(s in ("PENDING","IN_PROGRESS","QUEUED",None) for s in st): return "pending"
    return "green" if st else "no-checks"
buckets = {}
for p in prs:
    age = (now - dt.datetime.fromisoformat(p["createdAt"].replace("Z","+00:00"))).days
    row = (p["number"], ci(p["statusCheckRollup"]), bump(p["title"]), dep(p["title"]), age, p["title"])
    buckets.setdefault(eco(p["title"], p["labels"]), []).append(row)
LIMITS = {"uv": 10, "github-actions": "grouped"}
for e, rows in sorted(buckets.items()):
    print(f"\n== {e}: {len(rows)} open (limit {LIMITS.get(e,'?')}) ==")
    for n, c, b, d, age, t in sorted(rows, key=lambda r: r[0]):
        print(f"  #{n:<5} {c:<8} {b:<11} {age:>3}d  {d}")
PY
```

If an ecosystem's open count meets or exceeds its limit, new upgrades for it are
being starved right now — draining is urgent.

**Handle the two ecosystems separately.** The consolidation below covers the
**`uv` (Python)** bumps, which all edit `uv.lock` and conflict with each other.
**GitHub Actions** bumps edit workflow YAML, never the lock, and Dependabot
already groups them — review and approve that grouped PR on its own once green;
don't fold it into the lock branch.

## Step 2 — Open a Linear tracking issue

Create one issue in the Integrations team for this triage run before touching
git, so the batch PR and any follow-ups link back to it. Record the surveyed
queue (the table from Step 1) in the description. Use the Linear MCP tools
(`create_issue` / `save_issue`). Keep the whole run under this issue.

Set the tracking issue (and each Step 8 sub-issue) to **Todo**, **assign it to
the triager**, and put it in the **current cycle** — resolve "current" via
`list_cycles(type="current")` for the Integrations team and pass that cycle
number, rather than assuming a name.

## Step 3 — Branch off `main`

Dependabot targets `main` (single-trunk GitHub flow). Consolidate there.

```bash
git fetch origin
git checkout -b chore/deps-batch-$(date +%Y%m%d) origin/main
```

## Step 4 — Apply all the `uv` bumps at once

Do **not** merge the individual branches (they all collide on `uv.lock`). Instead
reproduce their intent in one clean resolution: upgrade every Python package from
the survey in a single `uv lock`, pinning each to its PR's target version.

```bash
uv lock \
  -P claude-agent-sdk==0.2.123 \
  -P openai==2.45.0 \
  -P langgraph==1.2.9 \
  -P uvicorn==0.51.0 \
  -P pytest-rerunfailures==16.4 \
  -P crewai==1.15.4 \
  -P pydantic-ai-slim==2.13.0 \
  -P langchain-community==0.4.2 \
  -P pytest-asyncio==1.4.0 \
  -P starlette==1.3.1
# (fill -P flags from the survey; drop the version to just take latest-resolvable)
git add uv.lock
```

For the rare PR that **raises a constraint floor** in `pyproject.toml` (title
reads "update … requirement from >=X to >=Y", not "bump … from X to Y"), also
apply that `pyproject.toml` edit and `git add` it — a lock bump alone won't
reproduce it. Then commit:

```bash
git commit -m "chore(deps): batch Dependabot bumps"
```

**Exact-pinned deps can't be moved with `-P`.** `crewai` is pinned exactly
(`crewai==X`) in *two* places in `pyproject.toml` (the `crewai` and `dev-crewai`
extras). `uv lock -P crewai==<new>` fails as unsatisfiable ("depends on
crewai==X and crewai==Y"). Edit both pins to the target version and re-lock, and
update any nearby comment that cites the old version's transitive pins (the
`tool.uv.conflicts` rationale) so it stays accurate. Because crewai lives in its
own conflict fork, commit the crewai pin+lock change on its own once its
`dev-crewai` tests pass (below).

**Drop a known-breaking major from the lock up front.** A bump whose own PR is
already RED in the survey (typically a `1.x → 2.x` major like `pydantic-ai-slim`)
will usually make the whole `uv lock` fail to resolve, or fail the suite — so
omit its `-P` flag from Step 4 from the start rather than discovering it in
Step 6. It goes to Step 8 as an excluded bump.

## Step 5 — Run the full suite

Prove the consolidated set. The lint/type gate and unit tests are the floor;
integration and e2e run when their credentials are present (see CLAUDE.md's
Environment Variables — they need Band + LLM keys, and e2e needs
`E2E_TESTS_ENABLED=true`).

```bash
uv sync --extra dev
uv run pre-commit run --all-files                                     # ruff + pyrefly gate
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/  # unit
uv run pytest tests/integration/ -v -s --no-cov                       # needs BAND_API_KEY_USER
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v -s --no-cov  # needs live platform + LLM keys
```

**Actually run integration and e2e — don't pre-declare them "env-blocked."** If
they fail, isolate the cause (Step 6) rather than assuming; a real config bug
hides behind that assumption. The e2e baseline provisions its own agents (only
needs `BAND_API_KEY_USER`) and can take a while.

**Stream long runs to a log file, never pipe through `tail`/`head`.** A pipe
buffers the whole run until the process exits, so a hang looks identical to
progress. Redirect to a file and watch that instead:

```bash
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v --no-cov > /tmp/e2e.log 2>&1 &
# then tail -f /tmp/e2e.log (watch for PASSED|FAILED|ERROR|403|429|Traceback and the final tally)
```

**crewai must be tested in its own venv** — it conflicts with parlant/pydantic-ai
and is absent from the `dev` extra, so the run above never exercises the crewai
bump. Target the crewai test **files** explicitly: a bare `-k crewai` over
`tests/adapters/` still tries to *collect* every adapter module, and the
non-crewai ones fail to import in the crewai-only venv (collection error).

```bash
uv sync --extra dev-crewai
uv run pytest tests/adapters/test_crewai_adapter.py \
  tests/adapters/test_crewai_flow_adapter.py \
  tests/adapters/test_crewai_flow_phase3.py tests/adapters/test_crewai_flow_phase4.py \
  tests/adapters/test_crewai_flow_phase5.py tests/adapters/test_crewai_flow_state_source.py \
  tests/adapters/test_crewai_adapter_soak.py tests/converters/test_crewai.py -v
```

## Step 6 — Decide which bumps to keep (bisect on failure)

If everything is green, the whole set is good — keep it all.

If a check fails, the failing area usually names the culprit (a `pydantic_ai`
test failing ⇒ the `pydantic-ai-slim` bump; a crewai test ⇒ the crewai bump). A
bump whose **own** Dependabot PR was already RED in the survey (e.g. a breaking
major) is the first suspect. Drop the suspect from the upgrade set and re-lock
without it, then re-test:

```bash
# Re-run Step 4's uv lock omitting the suspect's -P flag (so it stays at its
# current version), then re-run Step 5. Repeat until green.
```

**Attribute each failure before dropping a bump — most integration/e2e failures
are environmental, not the bumps.** These run against a live platform and a
multi-lane matrix, so isolate with a minimal probe before blaming a package.
Signatures seen in practice that are **not** bump regressions:

- WebSocket `403` on connect **plus** a `422` "Expected :uuid" on a mention ⇒ a
  stale `TEST_AGENT_ID` / `BAND_AGENT_ID` in the test env that doesn't match the
  agent `BAND_API_KEY` owns. The SDK appends `&agent_id=` to the socket URL and
  the platform rejects an unauthorized pairing. Confirm by connecting the WS with
  vs. without `&agent_id=`; fix the env, don't drop a bump.
- WebSocket `429` "reconnect rate-limited after recent supersede" ⇒ too many
  connect/disconnect cycles on one agent from repeated runs — back off, not a bump.
- e2e errors for out-of-lane adapters (gemini/codex/opencode/letta reporting
  "not set" / "no server", crewai absent from the `dev` venv) and memory
  `403 plan_required` (Enterprise-plan-gated API) ⇒ lane / creds / plan, not bumps.
  A `copilot_acp` narration assertion is a known flaky out-of-process bridge.

Only a failure that survives this triage **and** points at a bumped package
justifies dropping that bump. Each dropped package is a bump you are **not**
approving — it goes to Step 8.

## Step 7 — Approve the passing PRs

Don't merge. The consolidation branch was only the test vehicle (keep it local,
don't push it). For **each** individual Dependabot PR whose bump was in the green
set, post an approving review with a comment recording that the skill validated
it — a maintainer then does the actual merge.

```bash
gh pr review <number> --approve --body "$(cat <<'MSG'
✅ Approved by the dependabot-triage skill.

Validated in a consolidated batch run: this bump was applied together with the
other passing Dependabot bumps on a local branch and the full suite (ruff +
pyrefly, unit, crewai in dev-crewai, and integration/e2e where credentials were
available) passed with all of them in place — so it's compatible with the rest
of the batch, not just green on its own. Safe to merge.
MSG
)"
```

Report the approved set to the maintainer so they can merge them. Merging is
what actually drains the queue and reopens the open-PR limit — the skill's
approval is the green light for that, not a substitute for it.

## Step 8 — File follow-ups for the excluded bumps

Every dropped/failing bump gets a Linear issue (sub-issue of Step 2's), stating
the package, the target version, and the failure it caused, so a breaking upgrade
is tracked work rather than something forgotten.

**Leave the excluded PRs open and unapproved.** Don't close them and don't add an
`ignore` entry — the open PR is the live reminder that the upgrade is pending, and
it carries Dependabot's own changelog/compatibility notes for whoever picks up the
migration. Once the maintainer merges the approved set, most slots reopen; the few
excluded PRs staying in place is intended.

## Step 9 — Recover an upgrade that was already starved or closed

If a dependency's upgrade never appeared (the incident above):

1. Confirm the ecosystem limit was saturated (Step 1). Approving (Steps 3–7) and
   getting the maintainer to merge drains it, which lets the next weekly run
   re-propose on schedule.
2. If the upgrade's PR was previously **closed unmerged**, Dependabot won't
   re-propose that exact version, only a newer one. To pull it now, just include
   it in the batch: add its `-P package==version` to Step 4 and let the test gate
   judge it like any other bump.

## Merging the approved set (only when explicitly asked to drive it)

Approval is the default verdict — a maintainer merges. If you *are* asked to
merge, know that this repo's setup makes it a **serial grind**, not a batch:

- **All root-`uv` PRs conflict on `uv.lock`.** Merge one and the rest instantly
  go stale; Dependabot must rebase each before it can merge. So: merge one → wait
  for the next to rebase + go green → merge it → repeat, one at a time. Poll
  `gh pr view <n> --json mergeStateStatus` for `CLEAN`. The echo-agent lock (own
  file) and the gh-actions group (YAML) are independent — they don't collide with
  the root set. Nudge a lagging rebase with a `@dependabot rebase` comment.
- **`main`'s ruleset dismisses stale approvals and requires branches up to date
  (strict).** Every Dependabot rebase/force-push wipes the approval, so
  **re-approve as the last step immediately before merging**, not before CI —
  otherwise the PR sits `BLOCKED` on "review required" despite green checks.
  Auto-merge is disabled repo-wide; do **not** `--admin`-bypass required checks.
- **The `uv lock --check` specifier-drift trap.** CI's lint job runs
  `uv lock --check`. Dependabot's version-only lock bump does *not* reconcile
  specifier metadata that has drifted on `main` (e.g. an `openai>=1.0.0 → >=2.0.0`
  floor raised across extras), so the PR's lint fails and **`@dependabot rebase`
  and `recreate` never fix it** (it loops). Fix it yourself: rebuild the lock on
  current `main` and force-push it to the Dependabot branch (needs push
  permission), then re-approve + merge:

  ```bash
  git fetch origin main
  git checkout -B lockfix origin/main
  uv lock -P <pkg>==<target>     # rebuilds the whole lock on current main
  uv lock --check                 # must pass now
  git commit -am "chore(deps): rebuild uv.lock for <pkg> on current main"
  git push --force origin "HEAD:$(gh pr view <n> --json headRefName -q .headRefName)"
  ```

- **Never merge a stale-based lock PR.** Its `uv.lock` predates the other merges
  and a squash-merge would silently *revert* them (a PR based on pre-openai-merge
  `main` drops openai back down). Always rebase/rebuild onto current `main` first.
- Merge each with `gh pr merge <n> --squash --delete-branch`.

## Guardrails

- **The test gate is the arbiter — never approve a bump that wasn't in a green
  batch**, and never drop a *passing* bump just to shrink the diff. The skill
  approves; it never merges.
- **Test crewai in `dev-crewai`.** A crewai bump validated only in the `dev` venv
  is untested — the default resolution excludes it.
- **Do not raise `open-pull-requests-limit` as the fix.** A higher ceiling only
  defers triage and hides the alarm; the queue still needs draining.
- **Every excluded bump gets a Linear issue and stays open as a PR** — never a
  silent close, and never an `ignore` entry. The open PR is the reminder that the
  upgrade is still pending.
- **Majors get human sign-off** even when green, especially framework/adapter
  dependencies where a major can change behavior the SDK relies on. Surface them
  in the report rather than approving them unremarked.
- **Run integration and e2e for real, and attribute every failure** (Step 6)
  before dropping a bump — live-platform/lane/plan/env failures are common and
  are not the bumps. Never call a suite "env-blocked" on assumption.
- **If you drive merges: re-approve last, never `--admin`-bypass CI, and never
  merge a stale-based lock PR** (it silently reverts already-merged bumps). See
  "Merging the approved set."
