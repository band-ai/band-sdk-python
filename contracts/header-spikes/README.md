# Multi-agent skill header parsers

These are **Elixir discovery parsers**, not a production contract engine. The contract lab uses them to read a contract's header before its body. Contracts are written with YAML frontmatter (B); a standard skill package (C) carries the same fields as metadata. The caller selects the format: the retired native header fails with `frontmatter_required`; any other format is refused (`invalid_format` in the Elixir API, `input` on the portable source envelope).

| Option | Actual example | Shape |
|---|---|---|
| B | [YAML frontmatter](examples/frontmatter/room-work.md) | One file, bounded frontmatter and declarative body |
| C | [Standard SKILL.md](examples/standard/room-work/SKILL.md) | Standard metadata wrapper and a digest-pinned referenced body |

Both examples declare engine `0.2.0-lab.1`, normalize to one discovery card and load exactly the same 1,774-byte body. That body is a current contract: the contract lab compiles both packages to the same definition digest.

## Run

From this directory, with Elixir 1.18 or later:

```sh
mix deps.get
mix test --seed 0
mix format --check-formatted
mix run scripts/compare.exs
```

Dependencies are pinned in `mix.lock`: `yaml_elixir` 2.12.2 and `yamerl` 0.10.0.

## What the spike establishes

- Discovery reads at most 4096 application bytes, including delimiters, through a raw file descriptor without application read-ahead. It stops before the first body byte. Operating-system caching is outside this metric.
- Each delimiter occupies its own newline-terminated line. Both LF and CRLF are handled. Missing delimiters fail within the byte limit.
- A missing engine fails. A caller that lists the engines it implements has any other engine refused with `unsupported_engine` before other header fields are checked.
- The header carries no clocks or mention caps. Those are body declarations (`contract @30m => expired`, `mentions N`), so such a header field fails as unknown.
- Duplicate declarations and unknown Band fields fail. YAML uses a real parser, with anchors, aliases, and explicit tags excluded from this discovery profile.
- The C wrapper keeps metadata values as strings. Discovery does not open the referenced file. Full loading checks its SHA-256 digest.
- Reference traversal and existing symlinks inside the package fail. Changing a header after discovery fails when loading.

## Limits

The examples and parser form a narrow experimental profile. This is not a full Agent Skills validator, YAML interoperability certification, installed-engine resolver, or workflow validator. It does not generate production libraries, validate body semantics, run sessions, enforce deadlines, or authenticate transports.

Package files are assumed to be trusted and stable during an individual read. Each file open runs in a helper and must return within `open_ms` (5000 ms by default; set the `:header_spikes, :open_ms` application setting, or `open_ms:` on `HeaderSpikes.FileSnapshot` calls, where storage can stall longer); otherwise the read fails closed with `unsafe_contract_path`. That covers a FIFO renamed over the path, whose open would block; the stuck helper is then released, and no late reply reaches the caller. Path checks do not prevent a hostile concurrent filesystem replacement. Body loading is capped at 262,144 bytes. A production loader needs immutable, size-limited package snapshots, including an approved header and body identity.

These parsers validate header declarations only; the contract lab compiles bodies and runs sessions. The discovery cap is a candidate policy, not an established optimum. The measurements do not establish model token savings, ranking quality, or parser throughput. Both formats (B and C) generate the same small catalog entry, so authoring format need not determine the model's initial context size.

## Evidence

On 15 October 2026, `mix test --seed 0` passed 51 tests, `mix format --check-formatted` passed, and `mix run scripts/compare.exs` reported equal cards for B and C: 204 and 353 header bytes, no contract bytes read at discovery, and a 1,774-byte body after loading. The RED/GREEN logs under [evidence](../evidence) record the original spike and predate the current profile.

This is an isolated local Git repository with no configured remote. The researched Python prototype was read separately and remains unchanged.
