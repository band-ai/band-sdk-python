# Lightweight room simulator

`RoomSim` is an Elixir test library with one in-memory GenServer per room, a virtual clock, and an explicit notification queue. It has **no Hex dependencies**, database, sockets, hosted services, or model calls. Use it to develop and test participants and engine adapters without starting Band.

The simulator implements room transport behavior. The actual contract engine remains responsible for message validation, session state, accepted outcomes, budgets, and expiry. A post appearing in room history does not mean the engine accepted it.

## Run it

From this directory, with Elixir 1.18 or later:

```sh
mix test --seed 0
mix run examples/review_and_recover.exs
```

The [example](examples/review_and_recover.exs) advances a minute instantly, drops an approval notification, recovers it from permitted history, and duplicates its delivery. It also demonstrates why an unmentioned builder cannot fetch review evidence.

To use it in another Mix project's tests, add this local dependency:

```elixir
{:room_sim,
 path: "path/to/this-repository/room-sim",
 only: :test}
```

An ExUnit test can start a room and connect participants:

```elixir
room = start_supervised!({RoomSim, id: "review-test", now_ms: 0})
{:ok, lead} = RoomSim.join(room, "lead", :agent)
{:ok, reviewer} = RoomSim.join(room, "reviewer", :agent)
:ok = RoomSim.connect(reviewer) # deliver to this test process

{:ok, message} = RoomSim.post(lead, "review plan-7", mentions: ["reviewer"])
[delivery] = RoomSim.pending(room)
:ok = RoomSim.delay(room, delivery.id, 60_000)
[] = RoomSim.flush(room)
{:ok, 60_000} = RoomSim.advance(room, 60_000)
[{_, :delivered}] = RoomSim.flush(room)

assert_receive {:room_sim, "review-test", "reviewer", :message_created, ^message}
```

`connect(client, pid)` can target a separate participant process. A [conversation test](test/room_conversation_test.exs) runs a lead, reviewer, and builder with fixed responses, including recovery from a lost notification. Those scripts demonstrate the transport; they do not stand in for an implemented contract engine.

## Room and delivery controls

| API | Behavior |
|---|---|
| `join(room, id, :agent or :user)` | The test host registers an identity and receives a room-bound client. A repeated active identity is rejected. |
| `connect(client, pid)` / `disconnect(client)` | Connect or disconnect its notification recipient. A replacement invalidates the old connection's queued pushes. |
| `leave(client)` | Revoke the client and queued pushes for that membership. Rejoining issues a fresh client. |
| `post(client, content, mentions: [...])` | Store opaque text with simulator-assigned sender, room, ID, sequence, and virtual timestamp. |
| `history(client, after_sequence: n)` | Return eligible stored messages after the cursor. Agent history models the text/direct-mention selection with delivery status `all`. |
| `fetch(client, message_id)` | Test convenience with the same visibility check. It does not imply Band exposes this endpoint. |
| `pending(room)` | Inspect notification IDs, recipients, message IDs, event types, and due times. |
| `deliver(room, notification_id)` | Deliver one due notification. Choosing a later one first simulates reordering. |
| `flush(room)` | Attempt every currently due notification in queue order. Return `{notification_id, status}` pairs. |
| `delay(room, notification_id, milliseconds)` | Add delay from the later of its due time and the current clock. |
| `duplicate(room, notification_id)` | Add another notification of the same immutable message. |
| `drop(room, notification_id)` | Lose only that notification. The stored message remains. |
| `replay(client, after_sequence: n)` | Explicitly queue eligible history for the current connection, keeping the original message IDs. |
| `now(room)` / `advance(room, milliseconds)` | Inspect or advance monotonic virtual time. No sleeping, delivery, or engine timer execution is implicit. |
| `transcript(room)` / `trace(room)` | Host-only complete posts and deterministic transport decisions for assertions and debugging. |

Mentions use resolved participant IDs. A string means a direct mention; `%{id: "builder", kind: :reference}` records a reference without notifying that agent. Supported stored message types are `:text`, `:thought`, `:tool_call`, `:tool_result`, `:error`, `:task`, and `:attention`. Event messages produce `:event_created` for users. Agent notifications contain only `:message_created` text addressed to that agent, with no self echo.

A client is the test adapter's trusted identity binding. Sender claims inside content cannot change it. Host APIs deliberately have broader inspection rights; never hand the complete transcript to an agent as if it came from a restricted participant API.

## What comes from Band

The model was checked against the Band platform's `dev` branch source, fetched on October 10, 2026; inspection used immutable Git objects. The platform source is private and is not reproduced in this repository. This is source-based modeling, not live platform qualification.

| Band behavior | Simulator coverage |
|---|---|
| Acting identity comes from authenticated connection context. | Room-bound clients supply sender attribution; message content is never an identity source. |
| Agent channel pushes only directly mentioned text and excludes the agent's own posts. | Covered by routing tests, including reference-only and self-mentioned stored fixtures. |
| Human participants receive the broader room stream. | Explicit user members receive text and the supported event types. |
| Agent message listing uses `message_type: :text` and `direct_only: true`. | `history` applies those visibility rules with no processed-status filtering. |
| References do not count as delivery mentions. | Checked in both history and push selection, with a mutation test proving the guard matters. |

The agent endpoint's current direct-only filter is narrower than the generic `Chat.list_messages_for_participant` helper's default behavior. Using the endpoint policy avoids accidentally granting test agents a complete room transcript or automatic access to their unmentioned outbound messages. A sender receives its own post from `post` and can retain that result.

## Deliberate simplifications

- This models normalized stored messages, not REST request validation. Empty mention lists and self-mentioned stored fixtures are allowed for delivery tests even though the public posting API imposes additional restrictions. IDs need not be real participant UUIDs, and handles are not resolved.
- Removal invalidates all queued deliveries in this model. The reviewed Band channel documents a gap for some text pushes after removal. This stricter simulator rule must not be used to claim that the platform gap is fixed.
- Membership must be explicit. Agent ownership, delegation, tenant policy, billing, moderation, owner-multiplexed sockets, threads, edits/deletes, platform-authored membership events, presence heartbeats, and database/listener behavior are not modeled.
- The notification queue, virtual clock, sequence cursor, deterministic UUID-shaped IDs, and manual replay are test controls. They are not promises about production ordering, exactly-once delivery, cursor encoding, or automatic reconnect recovery. Controlled call order yields reproducible traces; arbitrary concurrent participant scheduling is still ordinary BEAM scheduling.
- Everything is in memory. A room process restart loses its state. Delivery status/claim APIs, durable outboxes, effects, and engine acceptance journals are outside this helper. `:delivered` means the notification was sent to a process, not that its engine accepted or completed work.
- `advance` supplies time to the harness. Inject `now(room)` into the real engine and explicitly run its timer step when testing deadlines. This helper does not select the still-open inactivity-reset policy or implement either session expiry timer.

These boundaries keep the simulator useful for fast tests without making it an alternate contract engine or a platform compatibility certification.

## Verification

The unimplemented API first produced **23 failing tests**, exit 2. The implemented simulator plus conversation tests pass **25 tests**, exit 0. Recorded local runs took 0.3–0.7 seconds inside ExUnit and 0.97–2.24 seconds including Mix startup; those are local test timings, not a comparison with Band or a throughput benchmark.

Compilation with warnings treated as errors and formatting checks pass. A separate minimal Mix project installed this library as a local test dependency and ran the exact ExUnit example above: **1 test passed**, exit 0.

Two temporary mutations each produced an observed failure: treating references as mentions leaked a notification to the builder, and bypassing the due-time guard delivered a delayed message early. The source was restored byte-for-byte before final validation.

- [Initial RED](evidence/red.log)
- [GREEN](evidence/green.log)
- [Final restored-source checks](evidence/final-checks.json)
- [Separate consumer project check](evidence/consumer.log)
- [Mutation receipts](evidence/mutation-checks.json)
- [Runnable example output](evidence/demo.log)

Implemented and tested locally. No Band product changes, production-engine integration, publication, or deployment is claimed. The enclosing experimental repository has no configured remote.
