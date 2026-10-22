# Exact file identity and session version selection

This extension defines the file identity boundary for `compiler/2`, engine
identifier `0.2.0-lab.1`.

## File source

A new-profile session MUST be created from a verified
`band-contract/file-source/1` source. A normalized card/body pair is insufficient
because it has already discarded the original header spelling. The source is
an object with exactly `profile`, `format`, `content`, and `sha256` for
`frontmatter`. Native directive headers are removed and rejected with
`frontmatter_required`; they are not another spelling of a current contract
([discovery rules](contract-dsl.md#package-and-discovery)). `content` contains the entire contract file as a valid UTF-8
string, and `sha256` is 64 lowercase hexadecimal digits.

The loader MUST re-encode the decoded content as UTF-8 without normalization or
newline conversion, compute SHA-256 over all those bytes, and require equality
with the supplied hash. This covers header, separators, body, documentation,
comments and final newline. JSON carrier escapes are not part of the file.
NFC/NFD and LF/CRLF spellings remain distinct byte identities. The loader MUST
reject invalid UTF-8, an incorrect hash, unknown fields and unknown formats.

Discovery remains bounded to 4,096 bytes including the terminating delimiter.
At most 262,144 bytes may follow it. The whole entry is therefore at most
266,240 bytes; a shorter header does not increase the body allowance. Existing
bounded parsing and non-executable documentation rules still apply.

For `standard`, the source additionally requires exactly `body` and
`package_name`. The primary hash covers the **whole `SKILL.md` entry**, including
prose after its frontmatter. That entry contains the required
`band-contract-sha256` of the referenced body; the loader MUST verify that body
as well, enforcing its separate 262,144-byte bound and UTF-8 validity.
`package_name` MUST match the declared name. Filesystem loading also preserves
the existing relative-path, regular-file and symlink checks. A hash of only a
referenced body or only the discovery prefix is not this package's identity.

## Identities returned to participants

For this engine, `source_digest` and the session identity's `source` equal the
exact file SHA-256. Session creation and durable state MUST record that hash,
the selected engine identifier, and the immutable selected source. Views and
restore checks MUST use the same selected contract. A participant can compare
the returned `source` against the file it intended to run.

The existing `digest`/`definition` remains a separate behavior fingerprint for
analysis and compatibility tooling. It does not substitute for the file hash.
An optional author `version` is a semantic-version label, represented by
`version` in frontmatter or `band-version` in standard metadata. The compiled card returns that label, or null when omitted.
The label is excluded from the behavior fingerprint and included in the exact
file hash. Reusing a version label never makes different files equivalent.

A file hash establishes exact content identity, not author identity, trust or
permission to run. Hosts still authenticate callers and authorize selected
packages. For a standard package, the entry hash commits to the checked body
through the body's digest stored inside the entry.

## Existing sessions do not upgrade

Selecting version `0.4.2` as the current catalog choice affects **new sessions**.
An existing session created from `0.4.1` continues using its original file hash,
engine semantics, roles, configuration, accepted facts and receipts until it
ends. Catalog lookup MUST happen when selecting the source for creation, not
on every message or restart. Restore with a different source hash MUST fail
with `contract_mismatch`, even if the behavior fingerprint or author version
happens to match.

The normal lifecycle has no in-place upgrade operation. If work must continue
under another protocol, create a separate, explicitly linked session after
reconciling unfinished work and uncertain effects. Do not alter old receipts
or present transferred data as if the new session originally accepted it.

## Evidence and implementation boundary

[File identity tests](../contract-lab/test/file_identity_test.exs) exercise
hash verification, header-byte changes, equivalent YAML header spellings,
standard-package body verification, filesystem/remote agreement and restore
mismatch rejection. They first failed against the old implementation
and pass after the extension. The compiler and reducer are local implementation
evidence; Band deployment and catalog routing require their own integration
checks. [Removed-form tests](../contract-lab/test/old_forms_test.exs) pin native
header rejection. The current [schema bundle](schemas/protocol-v2.schema.json)
describes source and compiled shapes; retained compatibility schemas are not
permission to author removed forms.
