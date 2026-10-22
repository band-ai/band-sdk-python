defmodule ContractLab.ContractTimeoutTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}
  @safe 9_007_199_254_740_991

  defp compile(declaration \\ "contract @30m => expired", extra \\ "", body \\ nil) do
    body =
      body ||
        """
        type request
        type review
          request: ref(request)
        type revise
        state new
          lead -> reviewer: request() awaits reviewing
            => review() & collected
             | @5m & stalled
        state reviewing max_entries 3 => stalled
          lead -> reviewer: revise() => revising
        state revising
          lead -> reviewer: request() => reviewing
        end collected
        end stalled
        end timed_out
        """

    bytes =
      """
      ---
      name: contract-timeout
      description: Bound a whole run.
      engine: 0.2.0-lab.1
      roles: [lead, reviewer]
      #{extra}---
      ~s|One fixed clock for this entire run.|
      #{declaration}
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
        max_messages: 10,
        room_policy: :unchecked,
        capabilities: c.card.extensions
      )

  defp send_message(s, id, kind, actor, now, payload \\ %{}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )

  test "contract clock is explicit versioned data and appears in role queries" do
    assert {:ok, c} = compile()
    assert c.card.lifetime == {:seconds, 1800}

    assert %{profile: "band-contract/timeout/1", after_ms: 1_800_000, outcome: "expired"} =
             c.timeout

    assert c.meta.doc == "One fixed clock for this entire run."
    assert {:ok, role} = ContractLab.for_role(c, "reviewer")
    assert role.contract.timeout == c.timeout
    assert {:ok, s} = new(c, 1000)
    assert s.max_at == 1_801_000
    assert {:ok, %{status: :active}} = Engine.tick(s, 1_800_999)
    assert {:ok, %{outcome: "expired", expired_at: 1_801_000}} = Engine.tick(s, 1_801_000)
  end

  test "custom terminal outcome survives transitions, re-entry, retries and restore" do
    assert {:ok, c} = compile("contract @30m => timed_out")
    assert {:ok, s} = new(c)

    assert {%{status: :accepted, state: "reviewing"}, s} =
             send_message(s, "q", "request", "l", 1000)

    assert {%{status: :accepted, state: "revising"}, s} =
             send_message(s, "v", "revise", "l", 2000)

    # The re-send into the reply wait must fit its whole 5m wait before the 30m cap, so the
    # latest re-entry is 1_500_000; the run then leaves the wait and sits in `revising`.
    assert {%{status: :accepted, state: "reviewing"}, s} =
             send_message(s, "q2", "request", "l", 1_500_000)

    assert {%{status: :duplicate}, s} = send_message(s, "q2", "request", "l", 1_500_500)

    assert {%{status: :accepted, state: "revising"}, s} =
             send_message(s, "v2", "revise", "l", 1_799_000)

    assert {:ok, s} =
             StateCodec.restore(c, StateCodec.export(s), 1_799_900,
               capabilities: c.card.extensions
             )

    assert s.max_at == 1_800_000
    assert %{status: :active, state: "revising"} = s

    # At the cap the run is already terminal: even the re-send `revising` allows is refused
    # as terminal, not as insufficient_time.
    assert {%{status: :rejected, reason: :terminal}, s} =
             send_message(s, "q3", "request", "l", 1_800_000)

    assert s.outcome == "timed_out"
    assert s.expiry.reason == :lifetime

    assert {:ok, %{outcome: "timed_out"}} =
             StateCodec.restore(c, StateCodec.export(s), 2_000_000,
               capabilities: c.card.extensions
             )
  end

  test "successful completion cancels all pending clocks" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c)
    assert {%{status: :accepted}, s} = send_message(s, "q", "request", "l", 0)

    assert {%{status: :accepted, outcome: "collected"}, s} =
             send_message(s, "r", "review", "r", 1, %{"request" => "q"})

    assert {:ok, %{outcome: "collected", expiry: nil}} = Engine.tick(s, 2_000_000)
  end

  test "the body clock is the only clock, and a second contract clock fails" do
    assert {:ok, body} = compile()
    assert {:ok, a} = new(body)
    assert {:ok, %{outcome: "expired"}} = Engine.tick(a, a.max_at)

    assert {:error, %{code: :unknown_field, line: 6, detail: "max-duration"}} =
             compile("contract @30m => expired", "max-duration: 30m\n")

    assert {:error, %{code: :duplicate}} =
             compile("contract @30m => expired\ncontract @1h => expired")
  end

  test "new work still needs its entire declared window before the whole-run cap" do
    body = """
    type request
    type review
      request: ref(request)
    state new
      lead -> reviewer: request() awaits reviewing
        => review() & collected
         | @5m & stalled
    end collected
    end stalled
    """

    assert {:ok, c} = compile("contract @30m => expired", "", body)
    assert {:ok, s} = new(c)

    assert {%{status: :rejected, reason: :insufficient_time}, _} =
             send_message(s, "q", "request", "l", 1_500_001)

    assert {%{status: :accepted}, s} = send_message(s, "q", "request", "l", 1_500_000)
    assert {:ok, %{outcome: "stalled"}} = Engine.tick(s, 1_800_000)

    assert {:error, %{code: :duration}} =
             compile(
               "contract @2m => expired",
               "",
               "type review\nstate new\n reviewer -> lead: review() @5m => collected | stalled\nend collected\nend stalled\n"
             )
  end

  test "the explicit infinity opt-out keeps the unbounded default and state shape" do
    assert {:ok, c} = compile("contract @infinity => expired", "", "state new\n")
    refute Map.has_key?(c, :timeout)
    assert {:ok, s} = new(c)
    assert s.max_at == nil
    assert {:ok, %{status: :active}} = Engine.tick(s, 100_000_000)
  end

  test "scope clocks require finite safe durations and terminal destinations" do
    for declaration <- [
          "contract @0m => expired",
          "contract @infinite => expired",
          "contract @5m => new",
          "contract @5m => reviewing",
          "contract @5m => unknown",
          "contract @5m",
          "contract @5m => expired | stalled"
        ] do
      assert {:error, _} = compile(declaration)
    end

    assert {:ok, c} = compile()
    assert {:error, :invalid_time_range} = new(c, @safe - 1_799_999)
  end
end
