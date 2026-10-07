# REST Client

`band-client-rest` is the generated Fern client behind `link.rest`, pinned exactly in
`pyproject.toml`. The pin discipline is what the code cannot say for itself.

Before writing a workaround for a bug in the generated client:

1. **Check for a fixed release first.** `pip index versions band-client-rest`, then diff the
   relevant model or method between the pin and the newer version.
2. **If it is fixed upstream, bump the pin.** That is the default action. Only a cited
   blocker (failing CI, an unresolved conflict) justifies a workaround; "inconvenient" is not
   one.
3. **If the workaround is still needed, tie it to the pin:** comment the exact version at
   which it stops being reachable, so it cannot sit dead after a later bump.
4. **Make a test against the real dependency the tripwire** (not a stubbed exception), and
   confirm the CI result is real: a grouped Dependabot bump (`uv-minor-and-patch`) can fail at
   collection from an unrelated package first and hide it.

## Tool path identifiers

Tool inputs validate REST path identifiers before dispatch using the shared
[identifier definitions](../src/band/runtime/tools/inputs/identifiers.py). IDs
contain ASCII letters, digits, underscores and hyphens; empty values, whitespace
and routing delimiters are rejected with standard field-specific tool feedback.
Accepted strings retain their exact spelling. Task references also accept the
existing leading `#` shorthand, including board numbers and UUIDs. This lexical
check does not establish resource existence. Direct REST client calls remain
responsible for their own input validation.
