defmodule ContractLab.ReplyTimedHeadsTest do
  # ubf-replies/old-form/RULE.md §A.2 and SECURITY-OLDFORM.md (op-usn3mvm3).
  # T1: a head may carry its sender's own duty, `a -> b: k() as x @d awaits W | T`. IDs: head
  # rule-N, its duty due-(N+1), alternatives rule-(N+2)…, the wait timeout last; the head's
  # state keeps the duty row, W gets the wait. T1-2: a T1 head never awaits its own state
  # (reply_route). A4: W may be entered by a re-send of another kind with the head's route and
  # binding when every alternative's correlated ref lists that kind (else reply_correlation).
  # (A1-1's `each` and collection rebinding cannot be written: `each` takes no `as`.)
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @header "---\nname: s2-t1\ndescription: S2 under T1 and A4.\nengine: 0.2.0-lab.1\nroles: [lead, executor]\n---\n"

  @types """
  contract @1d => expired
  type request
  type result
  type pass
  type blocked
  type heartbeat
  type receipt
    report: ref(result|pass|blocked)
  type plan
    request: ref(request)
  """

  @ends """
  end unstarted
  end silent
  end handed_off
  end handoff_unacknowledged
  """

  @s2 """
  state new
    lead -> executor: request() as req awaits planning
      => plan() & executing
       | @15m & unstarted
  state executing
    event executor -> lead: heartbeat() => @self
    executor -> lead: result() as res @20m awaits reported | silent
      => receipt() & handed_off
       | @10m & handoff_unacknowledged
    executor -> lead: pass() as res => reported
    executor -> lead: blocked() as res => reported
  """

  defp compile(states, header \\ @header, types \\ @types) do
    bytes = header <> types <> states <> @ends

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp start(c) do
    Engine.new(c,
      session_id: "s",
      now: 0,
      max_messages: 40,
      bindings: %{"lead" => "l", "executor" => "x"},
      room_policy: :unchecked,
      capabilities: c.card.extensions
    )
  end

  defp send(s, id, actor, kind, now, payload \\ %{}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )

  defp executing do
    assert {:ok, c} = compile(@s2)
    assert {:ok, s} = start(c)
    assert {%{status: :accepted, state: "planning"}, s} = send(s, "r1", "l", "request", 1)

    assert {%{status: :accepted, state: "executing"}, s} =
             send(s, "p1", "x", "plan", 2, %{"request" => "r1"})

    {c, s}
  end

  describe "T1 timed heads" do
    test "the head is rule-N, its duty due-(N+1), then the alternatives and the wait timeout" do
      assert {:ok, c} = compile(@s2)
      head = Enum.find(c.states["executing"].rules, &(&1.kind == "result"))
      "rule-" <> n = head.id
      n = String.to_integer(n)

      assert head.target == "reported" and head.bind == "res"

      assert [%{id: duty, outcome: "silent", after_ms: 1_200_000}] =
               c.states["executing"].deadlines

      assert duty == "due-#{n + 1}"

      wait = c.states["reported"].wait
      assert wait.alternatives == ["rule-#{n + 2}"]
      assert wait.id == "due-#{n + 3}"
      assert head.id in wait.heads
      assert "rule-#{n + 4}" in Enum.map(c.states["executing"].rules, & &1.id)
    end

    test "the head's duty fires its outcome; accepting the head opens the wait" do
      {_c, s} = executing()
      assert {:ok, late} = Engine.tick(s, 2 + 1_200_000)
      assert %{status: :terminal, state: "silent"} = late

      assert {%{status: :accepted, state: "reported"}, s} = send(s, "x1", "x", "result", 10)
      assert %{head: "x1", due_at: due} = s.wait
      assert due == 10 + 600_000

      assert {%{status: :accepted, state: "handed_off"}, _} =
               send(s, "k1", "l", "receipt", 11, %{"report" => "x1"})
    end

    test "T1-2: a timed head that awaits its own state is reply_route" do
      body =
        String.replace(
          @s2,
          "executor -> lead: result() as res @20m awaits reported | silent",
          "executor -> lead: result() as res @20m awaits executing | silent"
        )

      assert {:error, %{code: :reply_route}} = compile(body)
    end

    test "a timed rule with a target followed by alternatives stays syntax" do
      body =
        String.replace(
          @s2,
          "executor -> lead: result() as res @20m awaits reported | silent",
          "executor -> lead: result() as res @20m => reported | silent"
        )

      assert {:error, %{code: :syntax}} = compile(body)
    end
  end

  describe "A4 one wait for several request kinds" do
    test "a re-send of another kind with the head's route and binding enters W" do
      {_c, s} = executing()
      assert {%{status: :accepted, state: "reported"}, s} = send(s, "b1", "x", "blocked", 10)
      assert s.wait.head == "b1"

      assert {%{status: :accepted, state: "handed_off"}, _} =
               send(s, "k1", "l", "receipt", 11, %{"report" => "b1"})
    end

    test "an entering kind missing from the alternative's ref is reply_correlation" do
      types = String.replace(@types, "ref(result|pass|blocked)", "ref(result|pass)")
      assert {:error, %{code: :reply_correlation, line: line}} = compile(@s2, @header, types)
      assert line == 21
    end

    test "a re-send of another kind on another route stays reply_route" do
      body =
        String.replace(
          @s2,
          "executor -> lead: pass() as res => reported",
          "lead -> executor: pass() as res => reported"
        )

      assert {:error, %{code: :reply_route}} = compile(body)
    end

    test "a re-send of another kind with another binding is reply_correlation" do
      body =
        String.replace(
          @s2,
          "executor -> lead: pass() as res => reported",
          "executor -> lead: pass() as other => reported"
        )

      assert {:error, %{code: :reply_correlation}} = compile(body)
    end
  end
end
