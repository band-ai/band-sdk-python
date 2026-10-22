# Earlier Elixir replacement experiment

This reference describes the earlier in-process Elixir experiment. For the remote Python program/LLM arrangement, read [the current replacement reference](replacement.md).

Use [the lab guide](../../../contract-lab/README.md) for API details and [the executable example](../../../contract-lab/examples/room_recovery.exs) for actual wiring. The pieces have separate responsibilities:

| Piece | Responsibility |
|---|---|
| Transport adapter | Trusted sender attribution, wire field mapping, notification delivery |
| Contract authority | Current session state, legal inputs and outputs, deadlines, mention/message budgets |
| Replacement controller | Journal, program supervision, snapshots, replay, bounded sidecar activation, effect outbox |
| Program | Deterministic JSON state changes and declared effect intents |
| Sidecar | Inspect failure evidence, produce and validate a compatible fresh program artifact |

The program interface is:

```elixir
@behaviour ContractLab.Program
def state_version, do: "1"
def init(_role_view), do: {:ok, %{"count" => 0}}
def handle(%{"kind" => "increment", "payload" => %{"by" => by}}, state) do
  value = state["count"] + by
  {:ok, %{"count" => value},
   [%{"type" => "room", "kind" => "total", "payload" => %{"value" => value}}]}
end
def handle(_, _), do: {:unhandled, "Unsupported input"}
```

This snippet illustrates the interface; use a matching contract and validate numeric bounds. The state schema is independent of the contract's conversation state. A program receives accepted event facts, not untrusted text to interpret as instructions. The controller derives stable effect keys from the session, role, input identity, and output index.

`CommandSidecar.new` requires a workspace, generator executable/argv, and validator executable/argv. `{bundle}`, `{output}`, and `{candidate}` are direct-argv placeholders; there is no shell interpolation. The generator writes the fresh `candidate_module` named in the JSON bundle. Preserve source, validation logs, BEAM bytes, and manifest. On restart call `CommandSidecar.load_programs(workspace)` and provide the returned registry along with the same initial program and pinned controller configuration.

The full journal remains the recovery source of truth. A snapshot is a verified acceleration point, not permission to skip validation of candidate behavior. Repair validates the entire committed input history before installing a candidate. Restart replays the tail after a valid snapshot and suppresses historical output dispatch. Keep code artifacts available as long as their journals may be reopened.

Observe external effects through destination receipts. Declare an adapter idempotent only when the destination actually deduplicates stable keys. An uncertain non-idempotent call stays held for reconciliation. A different adapter name or idempotency claim cannot retroactively make a prior uncertain effect safe.

The current lab bounds journals and snapshots and stops when capacity is exhausted; it does not compact them. File ownership is enforced within one BEAM instance. Program callbacks are trusted code running in supervised processes, not an OS sandbox. The room example restarts a participant controller while its in-memory contract authority and room stay alive. Production host persistence, distributed ownership, and a live model provider need their own integrations and evidence.
