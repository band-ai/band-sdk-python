defmodule ContractLab.ReplyWaitTest do
  # DESIGN v2 §3, §5, §6 (engine side): a send that declares a wait state W owes exactly one
  # reply; the reply's kind and literals pick the next state; the wait has its own due-M timer.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, RepliesFixture, StateCodec}
  import RepliesFixture, only: [send: 5, send: 6]

  @wait_ms 600_000

  defp deploying(opts \\ []) do
    c = RepliesFixture.deploy()
    assert {:ok, s} = RepliesFixture.start(c, opts)
    assert {%{status: :accepted, state: "deploying"}, s} = send(s, "d1", "l", "deploy", 10)
    {c, s}
  end

  defp error(id, request, retryable),
    do: {id, %{"request" => request, "retryable" => retryable}}

  describe "the wait lifecycle" do
    test "entering W through a head opens the wait: one reply owed by the recipient within d" do
      {_c, s} = deploying()
      assert s.wait == %{id: "due-4", head: "d1", due_at: 10 + @wait_ms, remaining: ["deployer"]}
      assert s.obligations == []
    end

    test "a contract without reply lines has no wait key at all" do
      c = RepliesFixture.base()
      assert {:ok, s} = RepliesFixture.start(c)
      refute Map.has_key?(s, :wait)
      assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)
      refute Map.has_key?(s, :wait)
    end

    test "the timeout fires as a work-duty timer with ID due-M into its terminal" do
      {_c, s} = deploying()
      assert {:ok, s} = Engine.tick(s, 10 + @wait_ms - 1)
      assert s.status == :active
      assert {:ok, s} = Engine.tick(s, 10 + @wait_ms)
      assert %{status: :terminal, state: "stalled", outcome: "stalled"} = s

      assert s.expiry == %{
               at: 10 + @wait_ms,
               id: "due-4",
               outcome: "stalled",
               reason: :deadline,
               missing: ["deployer"]
             }

      assert s.wait == nil
    end

    test "the wait keeps the duty tie order: at equal times it fires before a state clock" do
      # The state clock of `retrying` is 30m; a wait of exactly that length re-entered at the
      # same instant is not reachable, so tie against the contract clock (priority 5) instead.
      # The source writes whole seconds: a 3599s wait opened at 1s is due at the 1h clock.
      c = RepliesFixture.deploy(after_ms: 3_600_000 - 1_000)
      assert {:ok, s} = RepliesFixture.start(c)
      assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 1_000)
      assert {:ok, s} = Engine.tick(s, 3_600_000)
      assert %{id: "due-4", reason: :deadline} = s.expiry
    end

    test "leaving W by any other path ends the wait: an anystate exit" do
      {_c, s} = deploying()
      assert {%{status: :accepted, state: "failed"}, s} = send(s, "x1", "l", "cancel", 20)
      assert s.wait == nil
    end

    test "a host transition never carries the wait over, and never enters W" do
      {_c, s} = deploying()
      s = Engine.host_transition(s, "retrying", 20)
      assert s.state == "retrying" and s.wait == nil

      assert_raise ArgumentError, fn -> Engine.host_transition(s, "deploying", 30) end
    end

    test "max_entries on W redirects the fourth entry, which opens no wait" do
      {_c, s} = deploying()

      s =
        Enum.reduce(1..2, s, fn n, s ->
          {id, payload} = error("e#{n}", s.variables["job"], true)

          assert {%{status: :accepted, state: "retrying"}, s} =
                   send(s, id, "d", "error", 20 * n, payload)

          assert s.wait == nil

          assert {%{status: :accepted, state: "deploying"}, s} =
                   send(s, "d#{n + 1}", "l", "deploy", 20 * n + 5)

          assert s.wait.head == "d#{n + 1}"
          s
        end)

      {id, payload} = error("e3", s.variables["job"], true)

      assert {%{status: :accepted, state: "retrying"}, s} =
               send(s, id, "d", "error", 100, payload)

      assert {%{status: :accepted, state: "exhausted"}, s} = send(s, "d4", "l", "deploy", 105)
      assert s.wait == nil
    end
  end

  describe "admission inside W (steps 6, 7a, 7b, 7c)" do
    test "the reply's literals pick the state: ok, error retryable, error final" do
      for {kind, payload, target} <- [
            {"ok", %{"request" => "d1"}, "deployed"},
            {"error", %{"request" => "d1", "retryable" => true}, "retrying"},
            {"error", %{"request" => "d1", "retryable" => false}, "postmortem"}
          ] do
        {_c, s} = deploying()

        assert {%{status: :accepted, state: ^target, metadata: _}, next} =
                 send(s, "r1", "d", kind, 20, payload)

        assert next.wait == nil
        assert next.facts["r1"].rule in ["rule-1", "rule-2", "rule-3"]
      end
    end

    test "the selected alternative is recorded on the fact" do
      {_c, s} = deploying()
      {id, payload} = error("r1", "d1", false)
      assert {%{status: :accepted}, s} = send(s, id, "d", "error", 20, payload)
      assert s.facts["r1"].rule == "rule-3"
    end

    test "7a: the payload is checked against the kind's type before any literal is matched" do
      {_c, s} = deploying()

      assert {%{status: :rejected, reason: :payload, detail: %{field: "retryable"}}, _} =
               send(s, "r1", "d", "error", 20, %{"request" => "d1"})

      assert {%{status: :rejected, reason: :payload, detail: %{field: "retryable"}}, _} =
               send(s, "r1", "d", "error", 20, %{"request" => "d1", "retryable" => "yes"})
    end

    test "6: a kind with no candidate in W or anystate is illegal_message" do
      {_c, s} = deploying()

      assert {%{status: :rejected, reason: :illegal_message}, _} =
               send(s, "r1", "l", "ok", 20, %{"request" => "d1"})
    end

    test "7c: references are checked on the selected alternative" do
      {_c, s} = deploying()

      assert {%{status: :rejected, reason: :reference}, _} =
               send(s, "r1", "d", "error", 20, %{"request" => "zz", "retryable" => false})
    end

    test "a rejected reply leaves the wait open, and the replier may send again before d" do
      {_c, s} = deploying()
      assert {%{status: :rejected}, s} = send(s, "r1", "d", "error", 20, %{"request" => "d1"})
      assert s.state == "deploying"
      assert s.wait.due_at == 10 + @wait_ms

      assert {%{status: :accepted, state: "deployed"}, _} =
               send(s, "r2", "d", "ok", 30, %{"request" => "d1"})
    end

    test "C1: after a retry, a late reply to the earlier request fails the reference check" do
      {_c, s} = deploying()
      {id, payload} = error("e1", "d1", true)
      assert {%{status: :accepted, state: "retrying"}, s} = send(s, id, "d", "error", 20, payload)
      assert {%{status: :accepted, state: "deploying"}, s} = send(s, "d2", "l", "deploy", 30)
      assert s.wait.head == "d2"

      assert {%{status: :rejected, reason: :reference}, _} =
               send(s, "late", "d", "ok", 40, %{"request" => "d1"})

      assert {%{status: :accepted, state: "deployed"}, _} =
               send(s, "r2", "d", "ok", 40, %{"request" => "d2"})
    end

    test "no alternative matches (an incomplete compiled object): rejected, never the first rule" do
      c = RepliesFixture.deploy()
      [ok, retry, _final, progress] = c.states["deploying"].rules
      w = %{c.states["deploying"] | rules: [ok, retry, progress]}

      c = %{
        c
        | states:
            Map.put(
              c.states,
              "deploying",
              put_in(w, [:wait, :alternatives], ["rule-1", "rule-2"])
            )
      }

      assert {:ok, s} = RepliesFixture.start(c)
      assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)

      assert {%{status: :rejected, reason: :illegal_message}, _} =
               send(s, "r1", "d", "error", 20, %{"request" => "d1", "retryable" => false})
    end
  end

  describe "correlation is always required (Lead decision 2)" do
    defp uncorrelated(c, state, fun),
      do: RepliesFixture.redigest(update_in(c, [:states, state, :rules], &Enum.map(&1, fun)))

    test "W1 -> cancel -> W2 without a cycle: a late reply to W1's request fails reference" do
      c = RepliesFixture.relay()
      assert {:ok, s} = RepliesFixture.start(c)
      assert {%{status: :accepted, state: "first"}, s} = send(s, "d1", "l", "deploy", 10)
      assert s.wait.head == "d1"
      assert {%{status: :accepted, state: "between"}, s} = send(s, "x1", "l", "cancel", 20)
      assert s.wait == nil
      assert {%{status: :accepted, state: "second"}, s} = send(s, "d2", "l", "deploy", 30)
      assert s.wait == %{id: "due-6", head: "d2", due_at: 30 + @wait_ms, remaining: ["deployer"]}

      assert {%{status: :rejected, reason: :reference}, s} =
               send(s, "late", "d", "ok", 40, %{"request" => "d1"})

      assert s.state == "second" and s.wait.head == "d2"

      assert {%{status: :accepted, state: "done"}, _} =
               send(s, "r2", "d", "ok", 50, %{"request" => "d2"})
    end

    test "an alternative without =$ on the head's binding is refused at session start" do
      c = RepliesFixture.relay()

      c =
        uncorrelated(
          c,
          "second",
          &if(Map.has_key?(&1, :wait), do: %{&1 | matches: %{}}, else: &1)
        )

      assert {:error, :reply_correlation} = RepliesFixture.start(c)
    end

    test "a head without `as` is refused at session start" do
      c = RepliesFixture.relay()
      c = uncorrelated(c, "between", &%{&1 | bind: nil})
      assert {:error, :reply_correlation} = RepliesFixture.start(c)
    end

    test "a binding on a ref field that cannot hold the head's kind is refused" do
      c = RepliesFixture.relay()

      c =
        RepliesFixture.redigest(
          put_in(c, [:types, "ok", :fields, "request", :values], ["cancel"])
        )

      assert {:error, :reply_correlation} = RepliesFixture.start(c)
    end

    test "rebinding the wait's name in W or anystate is refused at session start, unless the rule always transitions (A1)" do
      c = RepliesFixture.deploy()

      in_w =
        uncorrelated(
          c,
          "deploying",
          &if(&1.kind == "progress", do: %{&1 | bind: "job"}, else: &1)
        )

      assert {:error, :reply_correlation} = RepliesFixture.start(in_w)

      # cancel and quit move into terminals, so they end the wait and may rebind its name.
      any = RepliesFixture.redigest(%{c | any_rules: Enum.map(c.any_rules, &%{&1 | bind: "job"})})
      assert {:ok, _} = RepliesFixture.start(any)

      any_each =
        RepliesFixture.redigest(%{
          c
          | any_rules: Enum.map(c.any_rules, &%{&1 | bind: "job", each: true})
        })

      assert {:error, :reply_correlation} = RepliesFixture.start(any_each)
    end

    test "SR3: a reply kind is reply-only for its sender role" do
      c = RepliesFixture.deploy()
      [cancel, quit] = c.any_rules
      c = RepliesFixture.redigest(%{c | any_rules: [cancel, %{quit | kind: "ok"}]})
      assert {:error, :reply_correlation} = RepliesFixture.start(c)
    end

    test "a reply must name the open wait's own request, even if a binding were stale" do
      {_c, s} = deploying()
      {id, payload} = error("e1", "d1", true)
      assert {%{status: :accepted}, s} = send(s, id, "d", "error", 20, payload)
      assert {%{status: :accepted, state: "deploying"}, s} = send(s, "d2", "l", "deploy", 30)
      # Defense in depth: even with the binding forced back, the reply answers wait d2 only.
      stale = %{s | variables: Map.put(s.variables, "job", "d1")}

      assert {%{status: :rejected, reason: :reference}, _} =
               send(stale, "late", "d", "ok", 40, %{"request" => "d1"})
    end

    test "restore refuses a wait whose head is not the binding's current value" do
      {c, s} = deploying()
      {id, payload} = error("e1", "d1", true)
      assert {%{status: :accepted}, s} = send(s, id, "d", "error", 20, payload)
      assert {%{status: :accepted, state: "deploying"}, s} = send(s, "d2", "l", "deploy", 30)
      data = StateCodec.export(s)
      assert {:ok, _} = StateCodec.restore(c, data, 40, capabilities: c.card.extensions)

      assert {:error, :invalid_checkpoint} =
               StateCodec.restore(c, put_in(data, ["state", "variables", "job"], "d1"), 40,
                 capabilities: c.card.extensions
               )
    end
  end

  describe "renewal: the wait reset scope" do
    test "@self in W renews the wait from now" do
      {_c, s} = deploying()
      assert {%{status: :accepted, state: "deploying"}, s} = send(s, "p1", "d", "progress", 100)
      assert s.wait.due_at == 100 + @wait_ms
      assert s.wait.head == "d1"
    end

    test "every alternative's target must still fit before max_at, not only one (V4)" do
      # retrying has a 30m state clock: a renewal that leaves it less than 30m before the
      # 1h contract window is refused even though deployed (terminal) and postmortem fit.
      # A 1_000_000 ms wait renewed at t ends at t + 1_000_000; retrying then needs 30m more.
      c = RepliesFixture.deploy(after_ms: 1_000_000)
      assert {:ok, s} = RepliesFixture.start(c)
      assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)
      late = 3_600_000 - 1_000_000 - 1_800_000 + 1
      assert late < s.wait.due_at

      assert {%{status: :rejected, reason: :insufficient_time}, _} =
               send(s, "p1", "d", "progress", late)

      assert {%{status: :accepted}, s} = send(s, "p1", "d", "progress", late - 1)
      assert s.wait.due_at == late - 1 + 1_000_000
    end

    test "renewal never moves the wait past the contract window" do
      c = RepliesFixture.deploy(after_ms: 3_000_000)
      assert {:ok, s} = RepliesFixture.start(c)
      assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)

      assert {%{status: :rejected, reason: :insufficient_time}, _} =
               send(s, "p1", "d", "progress", 700_000)
    end
  end

  describe "capacity (§6)" do
    test "the head reserves 1 + max R(target): postmortem's owed ack makes it 2" do
      c = RepliesFixture.deploy()
      assert {:ok, s} = RepliesFixture.start(c, max_messages: 2)

      assert {%{status: :rejected, reason: :completion_capacity}, _} =
               send(s, "d1", "l", "deploy", 10)

      assert {:ok, s} = RepliesFixture.start(c, max_messages: 3)
      assert {%{status: :accepted}, _} = send(s, "d1", "l", "deploy", 10)
    end

    test "completion_size checks every alternative's smallest envelope, literals included (S3)" do
      c = RepliesFixture.deploy()

      final =
        Canonical.encode(%{
          "id" => "a",
          "session" => "s",
          "kind" => "error",
          "payload" => %{"request" => "a", "retryable" => false}
        })

      assert {:ok, _} = RepliesFixture.start(c, max_bytes: byte_size(final))

      assert {:error, :completion_size} =
               RepliesFixture.start(c, max_bytes: byte_size(final) - 1)
    end
  end

  describe "saved state, restore and views" do
    test "the export carries the wait under a new state profile and restores round-trip" do
      {c, s} = deploying()
      data = StateCodec.export(s)
      assert data["profile"] == "band-contract/state/6"

      assert data["state"]["wait"] == %{
               "id" => "due-4",
               "head" => "d1",
               "due_at" => 10 + @wait_ms,
               "remaining" => ["deployer"]
             }

      assert {:ok, restored} = StateCodec.restore(c, data, 20, capabilities: c.card.extensions)
      assert restored.wait == s.wait
      assert StateCodec.export(%{restored | now: s.now}) == data
    end

    test "a restored wait still fires at due-M" do
      {c, s} = deploying()
      data = StateCodec.export(s)

      assert {:ok, restored} =
               StateCodec.restore(c, data, 10 + @wait_ms, capabilities: c.card.extensions)

      assert %{status: :terminal, outcome: "stalled", expiry: %{id: "due-4"}} = restored
    end

    test "a terminal or non-W state saves wait: null" do
      {c, s} = deploying()
      assert {%{status: :accepted}, s} = send(s, "r1", "d", "ok", 20, %{"request" => "d1"})
      data = StateCodec.export(s)
      assert data["state"]["wait"] == nil
      assert {:ok, _} = StateCodec.restore(c, data, 30, capabilities: c.card.extensions)
    end

    test "restore refuses a wait that does not belong where it is" do
      {c, s} = deploying()
      data = StateCodec.export(s)

      for broken <- [
            put_in(data, ["state", "wait", "id"], "due-9"),
            put_in(data, ["state", "wait", "head"], "nope"),
            put_in(data, ["state", "wait", "remaining"], ["lead"]),
            put_in(data, ["state", "wait", "due_at"], 10 + @wait_ms + 1_000),
            put_in(data, ["state", "wait", "extra"], 1)
            |> put_in(["state", "wait", "id"], "due-4"),
            put_in(data, ["state", "wait"], nil),
            Map.update!(data, "state", &Map.delete(&1, "wait"))
          ] do
        assert {:error, :invalid_checkpoint} =
                 StateCodec.restore(c, broken, 20, capabilities: c.card.extensions)
      end

      {_, done} = send(s, "r1", "d", "ok", 20, %{"request" => "d1"})
      data = StateCodec.export(done)

      broken =
        put_in(data, ["state", "wait"], %{
          "id" => "due-4",
          "head" => "d1",
          "due_at" => 10 + @wait_ms,
          "remaining" => ["deployer"]
        })

      assert {:error, :invalid_checkpoint} =
               StateCodec.restore(c, broken, 30, capabilities: c.card.extensions)
    end

    test "the replier's and requester's views show the wait, its alternatives and every exit" do
      {_c, s} = deploying()

      for actor <- ["d", "l"] do
        assert {{:ok, view}, _} = Engine.view(s, actor: actor, now: 20, room_policy: :unchecked)
        assert view.wait.id == "due-4" and view.wait.due_at == 10 + @wait_ms

        assert Enum.map(view.wait.alternatives, &{&1.rule, &1.kind, &1.literals, &1.target}) == [
                 {"rule-1", "ok", %{}, "deployed"},
                 {"rule-2", "error", %{"retryable" => true}, "retrying"},
                 {"rule-3", "error", %{"retryable" => false}, "postmortem"}
               ]

        # K-2: every rule that applies in W and moves the state, with its sender roles.
        assert view.wait.exits == [
                 %{rule: "rule-10", kind: "cancel", target: "failed", from: ["lead"]},
                 %{rule: "rule-11", kind: "quit", target: "failed", from: ["deployer"]}
               ]
      end
    end

    test "the role definition view lists every exit from W next to its wait" do
      c = RepliesFixture.deploy()
      assert {:ok, view} = ContractLab.for_role(c, "deployer")
      assert view.states["deploying"].wait.id == "due-4"

      assert view.states["deploying"].wait.exits == [
               %{rule: "rule-10", kind: "cancel", target: "failed", from: ["lead"]},
               %{rule: "rule-11", kind: "quit", target: "failed", from: ["deployer"]}
             ]
    end
  end

  describe "fail closed (S6)" do
    test "a host without the contract.replies/1 capability refuses the contract" do
      c = RepliesFixture.deploy()
      assert {:error, :unsupported_extension} = RepliesFixture.start(c, capabilities: [])
    end

    test "reply structures without the extension in the compiled object are refused" do
      c = RepliesFixture.deploy()
      c = %{c | card: %{c.card | extensions: []}}
      assert {:error, :unsupported_extension} = RepliesFixture.start(c, capabilities: [])
    end

    test "an extension this engine does not implement is refused even if the host lists it" do
      c = RepliesFixture.base()
      c = %{c | card: %{c.card | extensions: ["contract.replies/2"]}}

      assert {:error, :unsupported_extension} =
               RepliesFixture.start(c, capabilities: ["contract.replies/2"])
    end

    test "an old-profile checkpoint never gains a wait key, and a wait key is refused there" do
      c = RepliesFixture.base()
      assert {:ok, s} = RepliesFixture.start(c)
      assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)
      data = StateCodec.export(s)
      refute Map.has_key?(data["state"], "wait")
      assert data["profile"] != "band-contract/state/6"
      broken = put_in(data, ["state", "wait"], nil)

      assert {:error, :invalid_checkpoint} =
               StateCodec.restore(c, broken, 20, capabilities: c.card.extensions)
    end
  end
end
