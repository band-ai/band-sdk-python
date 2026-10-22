# Contract API examples

These examples use the public `band.contracts` API from this SDK checkout. The
PEP 723 metadata names the canonical package; `--with-editable .` selects the
local implementation while the feature is under development. Run commands from
the repository root. The examples are source compositions; installed-wheel and
target-host behavior require the release smoke and qualification checks.

For [01-read-room.py](01-read-room.py), configure an enrolled agent under
`contract_agent` in the root `agent_config.yaml`, or select another key with
`AGENT_KEY`. Keep `BAND_REST_URL`, `CONTRACT_ROOM`, and `CONTRACT_SESSION` in the
root `.env.test`, aligned with that agent's platform. The agent must already
belong to the room and be bound in the selected session.

```sh
uv run --with-editable . examples/contracts/01-read-room.py
```

This example reads identity, view, and the first inbox page. It logs only event
count and cursor and does not save progress. A durable consumer must persist its
cursor after handling a page and retain exact proposal bytes before dispatch.
The [API guide](../../docs/contracts.md) explains retries and room authority.

[02-managed-service.py](02-managed-service.py) requires a qualified Linux Python
3.12 environment with `band-sdk[contracts]` installed and an existing private
service configuration. Provision the matching engine and native prerequisites
using the [deployment guide](../../contracts/deploy/README.md). Select
`contract`, `author`, or `programs` to match that configuration:

```sh
uv run --with-editable . examples/contracts/02-managed-service.py \
  --python /absolute/runtime/bin/python \
  --config /absolute/private/service.json --service contract
```

The example waits for the service's readiness event, then owns it until it exits
or you interrupt the example. Context-manager exit awaits cleanup. Provider
credentials are not inherited from the invoking shell; use the credential
configuration supported by the selected service. Reuse retained state for a
restart, and construct a new service owner.

The retained [hybrid demo](../../contracts/agent-runtime/examples/hybrid_demo.py)
also exercises scripted delegation, repair, journal recovery, and separate
engine processes. It does not establish live model quality or deployment
qualification.
