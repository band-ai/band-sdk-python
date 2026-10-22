defmodule ContractLab.RenewalTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  defp compile(body) do
    bytes =
      "---\nname: renewable-work\ndescription: Explicit renewable clocks.\nengine: 0.2.0-lab.1\nroles: [lead, a, b]\n---\n" <>
        body

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp body(contract, state, rules) do
    """
    #{contract}
    type start
    type result
    type heartbeat
    type other
    state new
      lead -> {a,b}: start() => working
    state working #{state}
      #{rules}
    end done
    end stalled
    """
  end

  defp start(source) do
    assert {:ok, c} = compile(source)

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: 30,
               bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
               room_policy: :unchecked
             )

    assert {%{status: :accepted}, s} = send(s, "start", "l", "start", 0)
    {c, s}
  end

  defp send(s, id, actor, kind, now, payload \\ %{}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )

  test "state renewal preserves entry identity and leaves the contract cap fixed" do
    {c, s} =
      start(
        body(
          "contract @30m => expired",
          "@5m => stalled",
          "event a -> lead: heartbeat() => @state\na -> lead: result() => done"
        )
      )

    assert {%{status: :accepted}, s} = send(s, "hb", "a", "heartbeat", 240_000)
    assert s.entered_at == 0
    assert s.entries["working"] == 1
    assert s.max_at == 1_800_000
    assert s.clocks.phase_at == 540_000
    assert {%{status: :duplicate}, retry} = send(s, "hb", "a", "heartbeat", 300_000)
    assert retry.clocks == s.clocks

    assert {%{status: :rejected, reason: :payload}, rejected} =
             send(retry, "bad", "a", "heartbeat", 400_000, %{"x" => 1})

    assert rejected.clocks == s.clocks
    data = StateCodec.export(rejected)
    assert data["profile"] == "band-contract/state/3"
    assert {:ok, recovered} = StateCodec.restore(c, data, 539_999)
    assert recovered.clocks == s.clocks

    assert {%{status: :rejected, reason: :terminal}, expired} =
             send(recovered, "late", "a", "heartbeat", 540_000)

    assert expired.outcome == "stalled"
  end

  test "self resolves by adjacency, not type, and keeps already accepted contributions" do
    {c, s} =
      start(
        body(
          "contract @30m => expired",
          "",
          "event {a,b} -> lead: heartbeat() => @self\n{a,b} -> lead: result() each @5m => done | stalled"
        )
      )

    [heartbeat, result] = c.states["working"].rules
    assert heartbeat.reset.rule == result.id
    assert {%{status: :accepted}, s} = send(s, "a-result", "a", "result", 60_000)
    assert {%{status: :accepted}, s} = send(s, "hb", "b", "heartbeat", 240_000)
    assert hd(s.obligations).remaining == ["b"]
    assert hd(s.obligations).due_at == 540_000
    assert Map.values(s.contributions) == [["a"]]
    assert {:ok, s} = StateCodec.restore(c, StateCodec.export(s), 300_000)

    assert {%{status: :accepted, outcome: "done"}, _} =
             send(s, "b-result", "b", "result", 500_000)
  end

  test "same message type on a different rule never selects its timer" do
    {c, _} =
      start(
        body(
          "",
          "",
          "event a -> lead: heartbeat() => @self\na -> lead: result() @5m => done | stalled\nb -> lead: result() @2m => done | stalled"
        )
      )

    [event, first, second] = c.states["working"].rules
    assert event.reset.rule == first.id
    refute event.reset.rule == second.id
  end

  test "renewable contract has a creation-anchored maximum and late input cannot revive it" do
    {c, s} =
      start(
        body(
          "contract @80m max @4h => expired",
          "",
          "event a -> lead: heartbeat() => @contract\na -> lead: result() => done"
        )
      )

    for_at = fn s, at ->
      assert {%{status: :accepted}, next} = send(s, "hb-#{at}", "a", "heartbeat", at)
      next
    end

    s = Enum.reduce([4_000_000, 8_000_000, 12_000_000], s, &for_at.(&2, &1))
    assert s.clocks.hard_at == 14_400_000
    assert s.max_at == 14_400_000
    assert {:ok, s} = StateCodec.restore(c, StateCodec.export(s), 14_399_999)

    assert {%{status: :rejected, reason: :terminal}, s} =
             send(s, "late", "a", "heartbeat", 14_400_000)

    assert s.outcome == "expired"
  end

  test "explicit infinity removes the maximum but keeps the renewable silence window" do
    {_, s} =
      start(
        body(
          "contract @30m max infinity => expired",
          "",
          "event a -> lead: heartbeat() => @contract\na -> lead: result() => done"
        )
      )

    assert is_nil(s.clocks.hard_at)
    assert {%{status: :accepted}, s} = send(s, "hb", "a", "heartbeat", 1_700_000)
    assert s.max_at == 3_500_000
    assert {:ok, %{outcome: "expired"}} = Engine.tick(s, 3_500_000)

    {_, unlimited} =
      start(body("contract @infinity => expired", "", "a -> lead: result() => done"))

    assert is_nil(unlimited.max_at)
  end

  test "a finite maximum cannot bound an infinite initial window" do
    for maximum <- ["@4h", "4h", "@1ms"] do
      assert {:error, %{code: :duration, line: 1}} =
               compile(
                 body(
                   "contract @infinity max #{maximum} => expired",
                   "",
                   "a -> lead: result() => done"
                 )
               )
    end

    {_, bounded} = start(body("contract @4h => expired", "", "a -> lead: result() => done"))
    assert bounded.max_at == 14_400_000
    assert {:ok, %{outcome: nil}} = Engine.tick(bounded, 14_399_999)
    assert {:ok, %{outcome: "expired"}} = Engine.tick(bounded, 14_400_000)

    assert {:ok, unlimited} =
             compile(
               body(
                 "contract @infinity max infinity => expired",
                 "",
                 "a -> lead: result() => done"
               )
             )

    assert unlimited.timeout.after_ms == nil
    assert unlimited.timeout.maximum_ms == nil
  end

  test "unsafe or ambiguous reset targets fail compilation" do
    for {contract, state, rules} <- [
          {"", "", "event a -> lead: heartbeat() => @state\na -> lead: result() => done"},
          {"contract @30m => expired", "",
           "event a -> lead: heartbeat() => @contract\na -> lead: result() => done"},
          {"contract @infinity max infinity => expired", "",
           "event a -> lead: heartbeat() => @contract\na -> lead: result() => done"},
          {"", "",
           "event a -> lead: heartbeat() => @result\na -> lead: result() @5m => done | stalled"},
          {"", "", "event a -> lead: heartbeat() => @self\na -> lead: result() => done"},
          {"", "",
           "a -> lead: result() @5m => done | stalled\nevent a -> lead: heartbeat() => @self"}
        ] do
      assert {:error, %{code: :timer_target}} = compile(body(contract, state, rules))
    end

    assert {:error, %{code: :duration}} =
             compile(body("contract @80m max @30m => expired", "", "a -> lead: result() => done"))
  end

  test "self renewal cannot shorten its promised window under a parent cap" do
    {_, s} =
      start(
        body(
          "contract @6m => expired",
          "",
          "event a -> lead: heartbeat() => @self\na -> lead: result() @5m => done | stalled"
        )
      )

    assert {%{status: :rejected, reason: :insufficient_time}, next} =
             send(s, "hb", "a", "heartbeat", 120_000)

    assert next.obligations == s.obligations
    assert next.version == s.version
  end

  test "a fixed contract maximum cannot shorten an initial owed response window" do
    assert {:error, %{code: :duration}} =
             compile("""
             contract @1m => expired
             type result
             state new
               event a -> lead: result() @5m => stalled
             end stalled
             """)

    assert {:error, %{code: :duration}} =
             compile("""
             contract @1m => expired
             type result
             state new
               collect results from a -> lead @5m => done
                 reply result()
             end done
             """)
  end

  defp continuation(
         extensions,
         working,
         reporting \\ "  lead -> {a,b}: summary() @15m => done | unsummarized",
         summary \\ "type summary"
       ) do
    bytes = """
    ---
    name: renewal-continuation
    description: A renewal must leave the continuation's declared windows.
    engine: 0.2.0-lab.1
    roles: [lead, a, b]
    extensions: [#{extensions}]
    ---
    contract @30m max @4h => expired
    type start
    type result
    type heartbeat
    #{summary}
    state new
      lead -> {a,b}: start() => working
    state working
      event {a,b} -> lead: heartbeat() => @self
    #{working}
    state reporting
    #{reporting}
    end done
    end stalled
    end unsummarized
    """

    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => bytes,
               "sha256" => Canonical.hash(bytes)
             })

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: 30,
               bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
               capabilities: c.card.extensions,
               room_policy: :unchecked
             )

    assert {%{status: :accepted}, s} = send(s, "start", "l", "start", 0)
    s
  end

  test "a rule or collection renewal must leave the continuation's declared windows before max_at" do
    # a's result is a T1 head: its own 10-minute duty, then the lead's 15-minute summary.
    rule =
      continuation(
        "",
        "  a -> lead: result() as res @10m awaits reporting | stalled\n" <>
          "    => summary() & done\n     | @15m & unsummarized",
        "",
        "type summary\n  result: ref(result)"
      )

    # The lead's summary is the head's reply; its 15-minute window is the wait's.
    assert %{kinds: ["summary"], after_ms: 900_000, outcome: "unsummarized"} =
             rule.contract.states["reporting"].wait

    collection =
      continuation(
        "room.contributions/1",
        "  collect reports from {a,b} -> lead @10m => reporting\n    reply result()"
      )

    for s <- [rule, collection] do
      # Renewed at minute 5 the reply may arrive at minute 15; reporting's 15-minute duty still fits by minute 30.
      assert {%{status: :accepted}, _} = send(s, "hb", "a", "heartbeat", 300_000)
      # Renewed at minute 9 it would end at minute 19, leaving reporting four minutes short.
      assert {%{status: :rejected, reason: :insufficient_time}, _} =
               send(s, "hb", "a", "heartbeat", 540_000)
    end
  end

  @untimed "  lead -> {a,b}: summary() => done"

  test "a renewal leaves an untimed continuation the renewed window before max_at" do
    rule = continuation("", "  a -> lead: result() @10m => reporting | stalled", @untimed)

    collection =
      continuation(
        "room.contributions/1",
        "  collect reports from {a,b} -> lead @10m => reporting\n    reply result()",
        @untimed
      )

    for s <- [rule, collection] do
      # Renewed at minute 9 the close may come at minute 19; the summary keeps ten minutes.
      assert {%{status: :accepted}, s} = send(s, "hb", "a", "heartbeat", 540_000)
      # Renewed again at minute 11 the close could reach minute 21, leaving the summary nine.
      assert {%{status: :rejected, reason: :insufficient_time}, _} =
               send(s, "hb2", "a", "heartbeat", 660_000)
    end
  end

  test "a member that has settled cannot renew the wait for the others" do
    rule = continuation("", "  {a,b} -> lead: result() each @10m => reporting | stalled")

    collection =
      continuation(
        "room.contributions/1",
        "  collect reports from {a,b} -> lead @10m => reporting\n    reply result()"
      )

    for s <- [rule, collection] do
      assert {%{status: :accepted}, s} = send(s, "ra", "a", "result", 60_000)

      assert {%{status: :rejected, reason: :already_contributed}, _} =
               send(s, "hb", "a", "heartbeat", 120_000)

      # The member still owing its reply may renew.
      assert {%{status: :accepted}, _} = send(s, "hb", "b", "heartbeat", 120_000)
    end
  end
end
