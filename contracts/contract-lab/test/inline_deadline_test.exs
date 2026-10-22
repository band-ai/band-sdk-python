defmodule ContractLab.InlineDeadlineTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  defp compile(rule, extra \\ "", scope \\ "state reviewing") do
    bytes = """
    ---
    name: inline-review
    description: Collect a timed review.
    engine: 0.2.0-lab.1
    roles: [lead, a, b]
    #{extra}---
    type request
    type review
      text: text(1..100)
    type progress
    state new
      lead -> {a,b}: request() => reviewing
    #{scope}
      #{rule}
      event lead -> {a,b}: progress()
    end collected
    end stalled
    """

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp waiting(c, capacity \\ 10) do
    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
        now: 0,
        max_messages: capacity,
        room_policy: :unchecked
      )

    assert {%{status: :accepted}, s} = send_message(s, "request", "l", "request", 1000, %{})
    s
  end

  defp send_message(s, id, actor, kind, now, payload \\ %{"text" => "ok"}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )

  test "inline syntax compiles to the transition followed by its duty" do
    assert {:ok, compact} = compile("a -> lead: review() @5m => collected | stalled")
    assert {:ok, plain} = compile("a -> lead: review() => collected")
    assert hd(compact.states["reviewing"].rules) == hd(plain.states["reviewing"].rules)

    assert [
             %{
               participants: ["a"],
               quantifier: :all,
               kind: "review",
               after_ms: 300_000,
               outcome: "stalled"
             }
           ] = compact.states["reviewing"].deadlines

    assert {:ok, role} = ContractLab.for_role(compact, "a")
    assert role.states["reviewing"].deadlines == compact.states["reviewing"].deadlines
  end

  test "only an accepted qualifying review completes before the absolute boundary" do
    assert {:ok, c} = compile("a -> lead: review() @5m => collected | stalled")
    s = waiting(c)

    assert {%{status: :rejected, reason: :payload}, s} =
             send_message(s, "bad", "a", "review", 2000, %{"text" => ""})

    assert {%{status: :rejected, reason: :illegal_message}, s} =
             send_message(s, "wrong", "b", "review", 3000)

    assert {%{status: :accepted}, s} = send_message(s, "p", "l", "progress", 4000, %{})
    assert {:ok, s} = StateCodec.restore(c, StateCodec.export(s), 200_000)

    assert {%{status: :accepted, outcome: "collected"}, _} =
             send_message(s, "r", "a", "review", 300_999)

    assert {%{status: :rejected, reason: :terminal}, %{outcome: "stalled"}} =
             send_message(s, "r", "a", "review", 301_000)
  end

  test "each owes both reviews on one clock; repeated contributions do not reset it" do
    assert {:ok, c} = compile("{a,b} -> lead: review() each @5m => collected | stalled")
    s = waiting(c)

    assert {%{status: :accepted, state: "reviewing"}, s} =
             send_message(s, "a1", "a", "review", 60_000)

    assert {%{status: :duplicate}, s} = send_message(s, "a1", "a", "review", 90_000)

    assert {%{status: :rejected, reason: :already_contributed}, s} =
             send_message(s, "a2", "a", "review", 100_000)

    assert {:ok, s} = StateCodec.restore(c, StateCodec.export(s), 120_000)

    assert {%{status: :accepted, outcome: "collected"}, _} =
             send_message(s, "b1", "b", "review", 300_999)

    assert {:ok, %{outcome: "stalled", expiry: %{missing: ["b"]}}} = Engine.tick(s, 301_000)
  end

  test "a group without each needs just one review" do
    assert {:ok, c} = compile("{a,b} -> lead: review() @5m => collected | stalled")
    assert [duty] = c.states["reviewing"].deadlines
    assert duty.quantifier == :any

    assert {%{status: :accepted, outcome: "collected"}, _} =
             send_message(waiting(c), "b1", "b", "review", 2000)
  end

  test "the shorthand keeps completion reservation and the full response window" do
    assert {:ok, c} =
             compile(
               "a -> lead: review() @5m => collected | stalled",
               "",
               "contract @5m => expired\nstate reviewing"
             )

    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
        now: 0,
        max_messages: 2,
        room_policy: :unchecked
      )

    assert {%{status: :rejected, reason: :insufficient_time}, _} =
             send_message(s, "q", "l", "request", 1, %{})

    assert {%{status: :accepted}, s} = send_message(s, "q", "l", "request", 0, %{})

    assert {%{status: :rejected, reason: :completion_capacity}, _} =
             send_message(s, "p", "l", "progress", 1, %{})

    assert {%{status: :accepted, outcome: "collected"}, _} =
             send_message(s, "r", "a", "review", 2)
  end

  test "invalid durations, missing branches, nonterminal outcomes and unsupported scopes fail" do
    for rule <- [
          "a -> lead: review() @0m => collected | stalled",
          "a -> lead: review() @infinite => collected | stalled",
          "a -> lead: review() @5m => collected",
          "a -> lead: review() => collected | stalled",
          "a -> lead: review() @5m => collected | reviewing",
          "a -> lead: review() @5m => collected | new",
          "a -> lead: review() @5m @2m => collected | stalled",
          "event a -> lead: review() @5m => collected | stalled"
        ] do
      assert {:error, _} = compile(rule)
    end

    assert {:error, _} = compile("a -> lead: review() @5m => collected | stalled", "", "anystate")
  end
end
