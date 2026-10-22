# Runnable carrier examples

For adapter and client implementers: carry the same contract message over Band,
HTTP paths or email headers without copying every envelope field into JSON.
The [engine](engine.md#messages-and-normalization) and
[transport specification](transport.md) own the rules. These examples exercise
local normalization and the reducer, not live services or authentication.

## Run the examples

Use the repository's [development prerequisites](../README.md#source-and-reproducible-checks),
with `mix` available on `PATH`. From the repository root:

```sh
python3.12 -m contracts.spec.tools.checkcarrierexamples --output /tmp/carrier-examples-check.json
```

The checker fetches locked Mix dependencies in this checkout, compiles with
warnings treated as errors, compiles the existing
[one-review contract](../contract-lab/contracts/v2/one-review.band) as frontmatter,
runs the [example](../contract-lab/examples/carrier_round_trip.exs), and checks
its format, map/document parity and local links. The JSON receipt records each
child command's actual exit, source hashes and the checked Git head; logs sit
beside it. A failed child stops the run. This is not the full conformance gate.

Once dependencies are installed, the core commands from `contract-lab/` are:

```sh
mix compile --warnings-as-errors
mix contract.check --format frontmatter contracts/v2/one-review.band
mix run examples/carrier_round_trip.exs
mix format --check-formatted examples/carrier_round_trip.exs
```

The contract uses current frontmatter and lowercase rules, a one-hour contract
clock, and a five-minute review duty. No separate carrier-specific contract is
needed. Each run generates one unpredictable proposal ID, then preserves that
exact envelope and `expected_version` across carriers and retries. A real client
must durably stage it before sending; the local example does not implement an outbox.

## Three maps, one normalized message

The following maps are the exact input in
[carriers.json](examples/carriers.json). Their names identify example binding
versions; `http-path/1` and `email/1` do not announce deployed endpoints.
The deployment pins the resolved `profile`, `name` and all four `fields` entries
and records them in binding session metadata at creation, outside wire input
and reducer state. Missing map entries resolve to `body`. Do not change a
deployment's map in place or choose another map from an incoming message.

### Band

```json
{"profile":"band-contract/carrier/1","name":"band-room/1","fields":{"id":"body","session":"path:id","kind":"body","expected_version":"body"}}
```

The [Band binding](band-transport.md#authentication-and-paths) supplies the
session from `/contracts/{id}`. JSON retains `id`, `kind`, `payload` and
`expected_version`. Proposal ID is not the ordinary chat-message ID.

### HTTP path

```json
{"profile":"band-contract/carrier/1","name":"http-path/1","fields":{"id":"body","session":"path:id","kind":"path:kind","expected_version":"header:X-Band-Expected-Version"}}
```

This illustrative edge supplies the session and kind from path components, and
the expected version from a declared header source. JSON retains `id` and
`payload`. The binding must parse and validate the header before supplying a
typed integer: `0`, not the string `"0"`. The example checks both forms. It does
not add routes or alter the standalone HTTP API.

### Email

```json
{"profile":"band-contract/carrier/1","name":"email/1","fields":{"id":"header:Message-ID","session":"header:X-Band-Session","kind":"body","expected_version":"body"}}
```

The JSON part retains `kind`, `payload` and `expected_version`; the declared
headers supply ID and session. The example uses a generated Message-ID ending
in `@example.invalid` and preserves the complete value across other carriers.
Header source-token spelling is exact. These are already-extracted typed values,
not an RFC email parser. A separate declared-kind variant demonstrates taking
`kind` from `header:X-Band-Kind`; the contract still rejects an undeclared kind.

This local `Message-ID` mapping is not safe for adoption when another session
member can observe or predict the header before admission, including via CC,
a mailing list or a relay copy. Such a member could pre-claim the proposal ID
and force `id_conflict` for its sender. An adopting binding MUST use a carrier
ID source that other members cannot observe or predict before admission;
otherwise retain a separate unpredictable proposal ID in the JSON body or
derive a distinct ID with a host-private key. The proposal ID itself must stay
unobservable to other members before admission and be durably staged for exact
retries. The example proves codec equivalence only, not that ordinary email
headers satisfy these [binding requirements](transport.md#carrier-map-profile).

## Use the agreed Wire interface

The executable loads each `carrier` map and passes only its declared non-body
source tokens in `transport`:

```elixir
opts = [carrier: carrier, transport: transport]
{:ok, bytes} = ContractLab.Wire.encode(message, opts)
{:ok, ^message} = ContractLab.Wire.decode(bytes, opts)
```

For email, `transport` has keys `"header:Message-ID"` and
`"header:X-Band-Session"`, not envelope keys `"id"` and `"session"`. There is
no separate `sources` option. Encoding omits mapped fields only when their
values agree; a mapped field stays in JSON when its source value was not
supplied. Decoding merges supplied values before admission. `payload` always stays
in JSON. A carrier map cannot supply `actor`, clock, role or payload fields.

## What the run proves

| Check | Expected result |
|---|---|
| Band, HTTP and email encode/decode | The same complete normalized message |
| Mapped fields omitted from JSON | Roundtrip succeeds; payload remains in JSON |
| Mapped fields duplicated with matching typed values | Succeeds |
| Each mapped field conflicts during encode or decode | `transport_conflict` |
| Map declares a source but no transport value is supplied | Body value stays intact |
| Missing required normalized ID or kind | `envelope` |
| Invalid profile, actor/payload map, undeclared source token | `transport_fields` |
| String supplied for an expected-version carrier | `envelope` |
| Band acceptance followed by identical email retry | Original acceptance returned as a duplicate; no state change |
| Body claims an actor | `envelope` |
| Same message with an unbound authenticated-context actor | `unauthorized` |
| Carrier supplies a kind absent from the contract | Reducer rejection |

Actor identity comes only from the binding's authentication. Neither email
`From`/`Message-ID` nor an HTTP path authenticates an actor. The example uses
explicit trusted test context and an explicit local membership policy; it
does not claim real email-sender authentication, TLS, Band authorization,
creation/restore pin persistence, durable receipt storage, publication or deployment.
The cross-carrier check compares already-normalized proposals to the same local
reducer; it does not switch a running deployment's pinned map.

Run the separate [conformance gate](conformance/README.md) for schema/vector and
adapter-output checks. Its success and a successful example are different
pieces of evidence, neither of which proves a live transport deployment.
