# Fern Changelog Entry Format — canonical spec

Single source of truth for entries written into a Fern docs repo's
`fern/changelog/<surface>/` directory. `scripts/validateentry.py` enforces
every rule below mechanically; prose here explains intent, the script decides
pass/fail. When they disagree, the script is a bug — fix it, don't hand-wave the
entry through.

Derived from the 74 published entries in `thenvoi/fern` (`fern/changelog/**`),
not from taste. Counts below are from that corpus.

## Surfaces

| Surface | Directory | Content | Frontmatter |
|---|---|---|---|
| `platform` | `fern/changelog/platform/` | product and UI changes | none |
| `rest-api` | `fern/changelog/rest-api/` | endpoints, schemas, auth | none |
| `websocket` | `fern/changelog/websocket/` | real-time events, message formats | none |
| `sdks` | `fern/changelog/sdks/` | SDK and client-library releases | `tags` required |

66 of 74 entries carry no frontmatter; all 8 that do are `sdks` entries, every
one of them since 2026-05-26. Do not add frontmatter to the other surfaces.

**Filename**: `YYYY-MM-DD.md` — the release date, never today's date. If the
file already exists (another release the same day), append to it; never
overwrite.

## Structure

```md
## Added
- **Python SDK:**
  - **Strands Agents Adapter** — Connect an AWS Strands Agents-based agent to Band chat rooms.
  - **Docker Three-Agent Demo** — New example showing a three-agent design-meeting workflow.

## Changed
- **Python SDK:**
  - **OpenCode Adapter Reaches Full Conformance** — The OpenCode adapter now meets the platform's L0-L4 bar.

## Fixed
- **Python SDK:**
  - **Parlant Adapter Port Collisions** — Two Parlant agents on one machine no longer crash on port binding.
```

Rules:

1. **Categories are `##`** — [Keep a Changelog](https://keepachangelog.com/en/1.1.0/):
   `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`, in that
   order, each at most once. The corpus uses exactly these four in practice:
   Added (49), Fixed (39), Changed (36), Security (11).
2. **No entry title.** A `##` heading that isn't a category is only allowed in a
   breaking-change entry (see below).
3. **No blank line between a category heading and its first bullet** — 135
   occurrences tight, 0 with a blank line.
4. **Bullets are `- **Label** — sentence.`** with an em dash (236 em dash vs 12
   hyphen). The label is a noun phrase naming the thing that changed; the
   sentence states the user-facing effect. Plain sentence bullets with no bold
   label are allowed for one-liners.
5. **Group by product with a bullet, never a heading.** On `sdks`, every
   top-level bullet under a category is `- **<Product Label>:**` with the entry
   bullets nested two spaces under it — all 8 frontmatter-era entries do this,
   and none contains a single `###`. `### TypeScript SDK` is the drift this spec
   exists to stop.
6. **No `<Badge>` components.** Removed from the changelog in
   [thenvoi/fern#325](https://github.com/thenvoi/fern/pull/325); zero of the 74
   entries contain one. The `tags` frontmatter is what drives badge rendering.
7. **`Action Required` and `Migration` are `###`**, never `##` — all 5
   occurrences in the corpus are `###`.

## Tags (`sdks` only)

```yaml
---
tags: ["feature", "improvement", "fix", "python"]
---
```

- Vocabulary: `feature`, `improvement`, `breaking-change`, `deprecation`,
  `security`, `documentation`, `fix`, plus one product tag (`python`,
  `typescript`, ...).
- Fixed order: the category tags above in that order, product tag last. All 8
  corpus entries follow it.
- Every section present requires its tag: `Added` ⇒ `feature`, `Fixed` ⇒
  `fix`, `Security` ⇒ `security`, `Removed` ⇒ `breaking-change`, `Changed` ⇒
  `improvement` **or** `breaking-change`, `Deprecated` ⇒ `deprecation` or
  `breaking-change`.
- The reverse does not hold: an entry may carry `improvement` or
  `documentation` with no `## Changed` section, because those changes are often
  listed under `## Added` (see `sdks/2026-07-08.md`).

## Breaking-change entries

Only these carry a `##` entry title, and it names the product:
`## Python SDK: Platform Tool Names Now Prefixed`. Four parts, so a developer
knows exactly what to change without guessing:

1. One-line summary of what changed.
2. An exhaustive before/after table — every field name, endpoint path, tool
   name, parameter value.
3. `### What This Means`, segmented by user type (new users, existing users,
   edge cases such as MCP or SDK variants).
4. `### Migration` with concrete next steps: deprecation warnings, migration
   scripts, compatibility windows.

Tag the file `breaking-change` and pass `--breaking` to the validator.

## Writing style

Describe the user-facing effect only, never the implementation.

- Good: "Display names in @mentions can no longer be spoofed."
- Bad: "Mention names are now resolved server-side."

Every bullet must trace to a merged PR, commit, or Linear issue in the release
range. If a change can't be inspected — a private sub-PR — omit it rather than
publishing a vague claim like "Bug fixes."

## Validation

```sh
python <this-skill>/scripts/validateentry.py <entry file> \
  --surface sdks --product-label "Python SDK" --product-tag python \
  --release-date <YYYY-MM-DD>
```

Run it before `fern check`, and never open the PR while either one fails.
