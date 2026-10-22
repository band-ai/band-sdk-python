defmodule ContractLab.StateTotalTest do
  @moduledoc "DF-21: a state's `total` budget caps its dwell over every entry."
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  @h 3_600_000

  # The request's reply is the member's ack, so `working` stays an ordinary state that the
  # member may leave with a question of its own: a wait state cannot hold the replier's question.
  @body """
  contract @12h => expired
  type request
  type ack
    request: ref(request)
  type question
  type answer
    question: ref(question)
  type deliverable
  state new
    lead -> member: request() awaits requested
      => ack() & working
       | @15m & stalled
  ~s|Work for at most four hours per entry and six hours in all.|
  state working @4h => stalled total @6h => over_budget
    member -> lead: deliverable() => delivered
    member -> lead: question() awaits asking
      => answer() & working
       | @2h & stalled
  state asking max_entries 4 => stalled
  end delivered
  end stalled
  end over_budget
  """

  defp compile(body \\ @body, extra \\ "") do
    bytes =
      """
      ---
      name: state-total
      description: Cap the work in one state across its entries.
      engine: 0.2.0-lab.1
      roles: [lead, member]
      #{extra}---
      """ <> body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp new(c),
    do:
      Engine.new(c,
        session_id: "s",
        bindings: %{"lead" => "l", "member" => "m"},
        now: 0,
        max_messages: 40,
        room_policy: :unchecked,
        capabilities: c.card.extensions
      )

  defp send_message(s, id, kind, actor, now, payload \\ %{}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )

  # The member takes the request at once: working is entered at 0.
  defp started(c) do
    assert {:ok, s} = new(c)
    assert {%{status: :accepted, state: "requested"}, s} = send_message(s, "r", "request", "l", 0)

    assert {%{status: :accepted, state: "working"}, s} =
             send_message(s, "k", "ack", "m", 0, %{"request" => "r"})

    s
  end

  defp answer(s, id, question, now),
    do: send_message(s, id, "answer", "l", now, %{"question" => question})

  defp restore(c, s, now),
    do: StateCodec.restore(c, StateCodec.export(s), now, capabilities: c.card.extensions)

  # Enter working at 0, leave for asking at 3.5 h, come back at 5 h.
  defp round_trip(c) do
    s = started(c)
    assert {%{status: :accepted}, s} = send_message(s, "q", "question", "m", 12_600_000)
    assert {%{status: :accepted, state: "working"}, s} = answer(s, "a", "q", 5 * @h)
    s
  end

  test "total is versioned, documented, visible to every role and absent when undeclared" do
    assert {:ok, c} = compile()

    assert %{profile: "band-contract/total/1", after_ms: 21_600_000, outcome: "over_budget"} =
             c.states["working"].total

    assert c.states["working"].timeout.after_ms == 4 * @h
    assert {:ok, role} = ContractLab.for_role(c, "member")
    assert role.states["working"].total == c.states["working"].total
    refute Map.has_key?(c.states["asking"], :total)

    plain = String.replace(@body, " total @6h => over_budget", "")
    assert {:ok, without} = compile(plain)
    refute Map.has_key?(without.states["working"], :total)
    assert {:ok, s} = new(without)
    refute Map.has_key?(s, :dwell)
  end

  test "dwell accumulates across entries; time in other states does not count" do
    assert {:ok, c} = compile()
    s = round_trip(c)
    assert s.dwell == %{"working" => 12_600_000}
    # 3.5 h used, 2.5 h left from re-entry at 5 h: the budget ends at 7.5 h,
    # before the per-entry clock (9 h).
    assert {:ok, %{status: :active}} = Engine.tick(s, 27_000_000 - 1)

    assert {:ok, %{outcome: "over_budget", expired_at: expired_at, expiry: expiry}} =
             Engine.tick(s, 27_000_000)

    assert expired_at == 27_000_000
    assert %{reason: :state_total, id: "total-working"} = expiry
  end

  test "without total the same round trip only meets the per-entry clock" do
    plain = String.replace(@body, " total @6h => over_budget", "")
    assert {:ok, c} = compile(plain)
    s = round_trip(c)
    assert {:ok, %{status: :active}} = Engine.tick(s, 27_000_000)
    assert {:ok, %{outcome: "stalled", expired_at: 32_400_000}} = Engine.tick(s, 9 * @h)
  end

  test "restore keeps the dwell and never credits downtime" do
    assert {:ok, c} = compile()
    s = round_trip(c)
    assert {:ok, restored} = restore(c, s, 6 * @h)
    assert restored.dwell == s.dwell

    assert {:ok, %{outcome: "over_budget", expired_at: 27_000_000}} = restore(c, s, 8 * @h)
  end

  test "an entry whose declared window cannot fit the remaining budget takes the total outcome" do
    body = """
    contract @12h => expired
    type request
    type ack
      request: ref(request)
    type question
    type answer
      question: ref(question)
    type deliverable
    state new
      lead -> member: request() awaits requested
        => ack() & working
         | @15m & stalled
    state working total @2h => over_budget
      member -> lead: deliverable() @1h => delivered | late
      member -> lead: question() awaits asking
        => answer() & working
         | @2h & stalled
    state asking max_entries 4 => stalled
    end delivered
    end late
    end over_budget
    end stalled
    """

    assert {:ok, c} = compile(body)
    s = started(c)
    assert {%{status: :accepted}, s} = send_message(s, "q", "question", "m", 30 * 60_000)
    # 1.5 h left holds the one-hour window.
    assert {%{status: :accepted, state: "working"}, s} = answer(s, "a", "q", @h)

    assert {%{status: :accepted}, s} = send_message(s, "q2", "question", "m", 119 * 60_000)
    # 31 minutes left cannot hold it: the entry ends the run instead of cutting the window.
    assert {%{status: :accepted, outcome: "over_budget"}, s} = answer(s, "a2", "q2", 3 * @h)

    assert s.outcome == "over_budget"
  end

  test "a renewal cannot reach past the budget" do
    body = """
    contract @12h => expired
    type request
    type deliverable
      request: ref(request)
    type status
    state new
      lead -> member: request() awaits working
        => deliverable() & delivered
         | @1h & late
    state working total @2h => over_budget
      event member -> lead: status() => @self
    end delivered
    end late
    end over_budget
    """

    assert {:ok, c} = compile(body)
    assert {:ok, s} = new(c)
    assert {%{status: :accepted}, s} = send_message(s, "r", "request", "l", 0)
    assert {%{status: :accepted}, s} = send_message(s, "p1", "status", "m", 50 * 60_000)

    assert {%{status: :rejected, reason: :insufficient_time}, _} =
             send_message(s, "p2", "status", "m", 70 * 60_000)
  end

  test "static validation of total" do
    bad = fn from, to -> compile(String.replace(@body, from, to)) end

    assert {:error, %{code: :unknown_state}} =
             bad.("total @6h => over_budget", "total @6h => asking")

    assert {:error, %{code: :duration}} = bad.("total @6h", "total @3h")

    assert {:error, %{code: :syntax}} = bad.("total @6h", "TOTAL @6h")

    short = """
    contract @12h => expired
    type request
    type deliverable
      request: ref(request)
    state new
      lead -> member: request() awaits working
        => deliverable() & delivered
         | @1h & late
    state working total @30m => over_budget
    end delivered
    end late
    end over_budget
    """

    assert {:error, %{code: :duration}} = compile(short)
  end
end
