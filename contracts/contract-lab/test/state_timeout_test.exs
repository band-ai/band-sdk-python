defmodule ContractLab.StateTimeoutTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}
  @safe 9_007_199_254_740_991

  # The request's reply is the reviewer's ack, so the clocked phase `reviewing` is an ordinary
  # state: a reply wait's own timeout would always fire before a state clock on the wait state.
  @body """
  type request
  type ack
    request: ref(request)
  type review
  type revise
  type progress
  state new
    lead -> reviewer: request() awaits requested
      => ack() & reviewing
       | @1m & stalled
  ~s|Leave this phase within five minutes.|
  state reviewing @5m => stalled max_entries 2 => exhausted
    reviewer -> lead: review() => collected
    lead -> reviewer: revise() => revising
    event reviewer -> lead: progress()
  state revising @2m => abandoned
    lead -> reviewer: request() => requested
  state requested max_entries 3 => exhausted
  end collected
  end stalled
  end abandoned
  end exhausted
  """

  defp compile(body \\ nil, extra \\ "") do
    body = body || @body

    bytes =
      """
      ---
      name: state-timeout
      description: Bound a phase across every exit path.
      engine: 0.2.0-lab.1
      roles: [lead, reviewer]
      #{extra}---
      """ <> body

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp new(c, now \\ 0),
    do:
      Engine.new(c,
        session_id: "s",
        bindings: %{"lead" => "l", "reviewer" => "r"},
        now: now,
        max_messages: 20,
        room_policy: :unchecked,
        capabilities: c.card.extensions
      )

  defp send_message(s, id, kind, actor, now, payload \\ %{}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )

  defp ack(s, id, request, now),
    do: send_message(s, id, "ack", "r", now, %{"request" => request})

  defp restore(c, s, now),
    do: StateCodec.restore(c, StateCodec.export(s), now, capabilities: c.card.extensions)

  defp waiting(c) do
    assert {:ok, s} = new(c)

    assert {%{status: :accepted, state: "requested"}, s} =
             send_message(s, "q", "request", "l", 1000)

    assert {%{status: :accepted, state: "reviewing"}, s} = ack(s, "k", "q", 1000)
    s
  end

  test "state timeout is versioned, documented and visible to every role" do
    assert {:ok, c} = compile()

    assert %{profile: "band-contract/timeout/1", after_ms: 300_000, outcome: "stalled"} =
             c.states["reviewing"].timeout

    assert {:ok, role} = ContractLab.for_role(c, "lead")
    assert role.states["reviewing"].timeout == c.states["reviewing"].timeout
    assert role.states["reviewing"].meta.doc == "Leave this phase within five minutes."
    assert c.states["reviewing"].deadlines == []
    refute Map.has_key?(c.states["new"], :timeout)
  end

  test "progress, retries and restore preserve the original phase deadline" do
    assert {:ok, c} = compile()
    s = waiting(c)
    assert {%{status: :accepted}, s} = send_message(s, "p", "progress", "r", 100_000)
    assert {%{status: :duplicate}, s} = send_message(s, "p", "progress", "r", 150_000)
    assert {:ok, s} = restore(c, s, 200_000)
    assert s.entered_at == 1000

    assert {%{status: :accepted, outcome: "collected"}, _} =
             send_message(s, "r1", "review", "r", 300_999)

    assert {%{status: :rejected, reason: :terminal}, expired} =
             send_message(s, "r1", "review", "r", 301_000)

    assert expired.outcome == "stalled"
    assert expired.expiry.reason == :state_timeout
    assert expired.expired_at == 301_000
    assert {:ok, restored} = restore(c, expired, 400_000)
    assert restored.expired_at == 301_000
  end

  test "leaving cancels the phase clock and bounded re-entry starts a fresh one" do
    assert {:ok, c} = compile()
    s = waiting(c)
    assert {%{status: :accepted}, s} = send_message(s, "revise", "revise", "l", 200_000)
    assert {:ok, %{status: :active} = s} = Engine.tick(s, 301_000)

    assert {%{status: :accepted, state: "requested"}, s} =
             send_message(s, "q2", "request", "l", 310_000)

    assert {%{status: :accepted, state: "reviewing"}, s} = ack(s, "k2", "q2", 310_000)
    assert s.entered_at == 310_000

    assert {:ok, %{outcome: "stalled", expired_at: 610_000, expiry: %{reason: :state_timeout}}} =
             Engine.tick(s, 610_000)

    assert {%{status: :accepted}, s} = send_message(s, "revise2", "revise", "l", 320_000)

    assert {%{status: :accepted, state: "requested"}, s} =
             send_message(s, "q3", "request", "l", 330_000)

    assert {%{status: :accepted, outcome: "exhausted"}, _} = ack(s, "k3", "q3", 330_000)
  end

  test "the earliest deadline wins, with duties before state before whole-run on ties" do
    body = """
    type request
    type review
      request: ref(request)
    state new
      lead -> reviewer: request() awaits reviewing
        => review() & collected
         | @5m & review_late
    state reviewing @5m => phase_late
    end collected
    end phase_late
    end review_late
    """

    # The reply wait times out as a work duty, ahead of the state clock and the whole run.
    assert {:ok, c} = compile(body <> "contract @5m => expired\n")
    assert {:ok, s} = new(c)
    assert {%{status: :accepted}, s} = send_message(s, "q", "request", "l", 0)

    assert {:ok, %{outcome: "review_late", expiry: %{reason: :deadline}}} =
             Engine.tick(s, 300_000)

    # A one-way request owes no reply: the state clock comes before the whole run.
    one_way =
      String.replace(
        body,
        "awaits reviewing\n    => review() & collected\n     | @5m & review_late",
        "=> reviewing"
      )

    assert {:ok, c} = compile(one_way <> "contract @5m => expired\n")

    assert {:ok, s} = new(c)
    assert {%{status: :accepted}, s} = send_message(s, "q", "request", "l", 0)
    assert {:ok, %{outcome: "phase_late"}} = Engine.tick(s, 300_000)

    assert {:ok, c} = compile(@body <> "contract @3m => expired\n")
    assert {:ok, %{outcome: "expired", expired_at: 180_000}} = Engine.tick(waiting(c), 400_000)
  end

  test "a shorter state cap cannot truncate the response window promised by a duty" do
    body = """
    type review
    state new @2m => stalled
      reviewer -> lead: review() @5m => collected | missing
    end stalled
    end missing
    end collected
    """

    assert {:error, %{code: :duration}} = compile(body)
  end

  test "an entry limit is a positive safe integer" do
    limit = fn n ->
      String.replace(@body, "max_entries 2 =>", "max_entries #{n} =>")
    end

    assert {:ok, c} = compile(limit.(@safe))
    assert c.states["reviewing"].max_entries.limit == @safe
    assert {:ok, _} = compile(limit.("0002"))
    assert {:error, %{code: :rounds, line: 12}} = compile(limit.(@safe + 1))
    assert {:error, %{code: :rounds, line: 12}} = compile(limit.(String.duplicate("9", 40)))
    assert {:error, %{code: :rounds, line: 12}} = compile(limit.(0))
  end

  test "clock arithmetic is bounded both on creation and on a transition" do
    assert {:ok, c} = compile("state new @2m => stalled\nend stalled\n")
    assert {:error, :invalid_time_range} = new(c, @safe - 119_999)
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c, @safe - 299_999)

    assert {%{status: :accepted, state: "requested"}, s} =
             send_message(s, "q", "request", "l", @safe - 299_999)

    assert {%{status: :rejected, reason: :invalid_time_range}, _} =
             ack(s, "k", "q", @safe - 299_999)
  end

  test "host state changes get the destination clock without extending owed work" do
    assert {:ok, c} = compile()
    s = Engine.host_transition(waiting(c), "revising", 200_000)
    assert {:ok, %{status: :active}} = Engine.tick(s, 301_000)
    assert {:ok, %{outcome: "abandoned", expired_at: 320_000}} = Engine.tick(s, 320_000)
  end

  test "bad durations and nonterminal timeout outcomes are rejected" do
    for header <- [
          "state new @0m => stalled",
          "state new @infinite => stalled",
          "state new @5m => new",
          "state new @5m => missing",
          "state new @5m"
        ] do
      assert {:error, _} = compile(header <> "\nend stalled\n")
    end
  end
end
