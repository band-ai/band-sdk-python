# Run: mix run examples/direct_agent.exs
# The first block stands in for trusted room setup. The client flow uses only
# public messages and role views; no local checker, program runner or journal.
alias ContractLab.{Canonical, Engine}

{:ok, contract} = ContractLab.load("contracts/v2/one-review.band", :frontmatter)

{:ok, room} =
  Engine.new(contract,
    session_id: "review-1",
    now: 0,
    max_messages: 4,
    bindings: %{"lead" => "lead-agent", "reviewer" => "reviewer-agent"},
    # A local demo without a room host: explicitly unchecked, never evidence.
    room_policy: :unchecked
  )

proposal = fn id, kind, payload, version ->
  %{
    "id" => id,
    "session" => "review-1",
    "kind" => kind,
    "payload" => payload,
    "expected_version" => version
  }
end

# An ordinary client learns which moves are available from the room view.
{{:ok, lead_view}, room} = Engine.view(room, actor: "lead-agent", now: 0)
[request_rule] = lead_view.legal
"request" = request_rule.kind

{accepted, room} =
  Engine.step(
    room,
    proposal.("q1", request_rule.kind, %{"text" => "Review this change."}, lead_view.version),
    actor: "lead-agent",
    now: 1
  )

:accepted = accepted.status

# A bad payload is rejected. The host retains authority even with no preflight.
{rejected, room} =
  Engine.step(room, proposal.("bad-review", "review", %{}, room.version),
    actor: "reviewer-agent",
    now: 2
  )

:rejected = rejected.status
:payload = rejected.reason

{{:ok, view}, room} = Engine.view(room, actor: "reviewer-agent", now: 3)
"reviewing" = view.state
"reviewer" = view.definition.role.name
%{kind: :text, bytes: %{min: 1, max: 4000}} = view.definition.types["review"].fields["text"]
[review_rule] = view.legal
"review" = review_rule.kind
true = Map.has_key?(view.facts, "q1")

# A definitive rejection permits a corrected proposal under a new identity.
corrected =
  proposal.(
    "r1",
    review_rule.kind,
    %{"text" => "The change needs a regression check."},
    view.version
  )

{accepted, room} = Engine.step(room, corrected, actor: "reviewer-agent", now: 4)
:accepted = accepted.status
"collected" = accepted.outcome

# Keeping an exact proposal for retry does not prescribe a storage technology.
{duplicate, room} = Engine.step(room, corrected, actor: "reviewer-agent", now: 5)
:duplicate = duplicate.status
true = duplicate.original == accepted
{{:error, :unauthorized}, _} = Engine.view(room, actor: "unbound-agent", now: 6)

IO.puts(
  Canonical.encode(%{
    passed: true,
    checks: [
      "rule discovery",
      "invalid payload rejected",
      "documented field constraints",
      "corrected proposal accepted",
      "exact retry returns original receipt",
      "unauthorized view denied"
    ],
    client_runtime: "none",
    scope: "in-process host interfaces; no network or Band discovery claim"
  })
)
