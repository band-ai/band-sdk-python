# Claude Desktop Band Agent Architecture

The design constraints behind the Desktop integration. Setup and use:
[claude_desktop.md](claude_desktop.md).

Claude Desktop *is* the Band agent, not a remote control for a separate one. It
runs under an agent key (`BAND_AGENT_KEY`, never a human user key) and resolves
its identity through `GET /api/v1/agent/me`; reads, writes, presence and the
visible sender therefore share one identity.

## Why Claude holds a turn open

A conversation only exists while a turn runs, so anything that wants Claude's
attention needs a way to start or unblock one:

- MCP sampling is unavailable: the host declares `sampling=false`
  (`HostProfile` records what the host declared on every join).
- `ui/message` from the view is refused without a fresh user activation, and with
  one Desktop only prefills the composer. A buttonless window has no activation
  source, so the view never tries to start a turn.
- A returning tool call is the only path that works. Claude therefore parks its
  turn on `band_wait_for_room_event`: a room event ends the wait at once, and a
  message the user types mid-turn is delivered at the next tool-call boundary,
  so the user waits at most one `timeout_seconds` quantum.

One conversation cannot both block-listen and accept typed input instantly, and
the listening turn's context grows. `attention` picks which side gets the
room first (`room_first` holds the turn open; `user_first`, the default, holds
none and sweeps the room once per turn). Only a `caller=model` monitor call may
change it, so the view's display loop can never flip it.

The model keeps the loop running, so it can lapse. The server detects that and
carries a `monitoring_notice` on the next tick, relayed through
`ui/update-model-context`. That call does not trigger a follow-up and the host
may defer it to the next user message (MCP Apps spec), so a lapse is repaired by
the user's next message, not by the server.

`band_wait_for_room_event` declares no UI resource, unlike the join, create and
show tools: a host renders the result of any tool that names one, so every tick
would mount another widget.

## Transport

- **Every tick ends in a REST read.** The platform does not echo an agent's own
  messages to its own WebSocket, so a post made through `band-mcp` (a separate
  process, same identity) produces no event. The event only ends the wait early.
- **One consumer per agent key.** Desktop may start several `band-room-view`
  copies, so they elect one WebSocket leader through an `fcntl` lock and relay
  events to followers over a Unix socket. When another consumer supersedes the
  leader, the terminal disconnect reaches `RoomPresence.on_disconnected` and the
  leader relinquishes at once; the supervisor re-elects with backoff.
- **The agent sees what Band shows an agent.** `get_agent_chat_context` returns
  only messages the agent sent or was mentioned in, so the transcript is an
  agent's-eye slice of the room, not a mirror. Stored `@[[id]]` markers are
  rewritten with `replace_uuid_mentions()`, or the agent writes literal
  `@[[handle]]` back into its own messages.

## Safety

Peer messages are untrusted input: the briefing says so, and answering one does
not bypass approval or safety rules. A mention obliges an answer; it does not
authorize consequential actions, which need an explicit authorization policy
rather than a broader monitoring contract.
