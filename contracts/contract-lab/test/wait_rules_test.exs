defmodule ContractLab.WaitRulesTest do
  # RULE.md §A and §A.2, engine side, against Sofia's SECURITY-OLDFORM.md witnesses
  # (ubf-replies/old-form/sofia-probes). The compiler does not emit these shapes yet, so each
  # contract is compiled in today's syntax and rearranged the way RULE.md describes.
  #
  # - A1-1: a rule in W that always transitions may rebind W's name; an event, an `each` rule
  #   or a collection reply still may not.
  # - A1b-2: a head in `anystate` binds, admits its reply and restores like a state head.
  # - A1b-3: an `anystate` head never applies inside its own wait.
  # - T1-1: a renewal must leave room for the wait of the state the renewed rule enters.
  # - B1: W is entered only by its heads: no other rule, timer, close or trigger targets it.
  # - B4: an alternative may not bind the head's name.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Checkpoint, Engine, ReplyWait}

  @m 60_000

  defp compile(roles, body) do
    b =
      "---\nname: probe\ndescription: probe.\nengine: 0.2.0-lab.1\nroles: [#{roles}]\n---\n" <>
        body

    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => b,
               "sha256" => Canonical.hash(b)
             })

    c
  end

  defp new(c, bindings),
    do:
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 60,
        bindings: bindings,
        room_policy: :unchecked,
        capabilities: c.card.extensions
      )

  defp step(s, id, actor, kind, now, payload \\ %{}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )

  defp move(c, from, kind, to, fun \\ & &1) do
    rule = Enum.find(c.states[from].rules, &(&1.kind == kind))

    c
    |> update_in([:states, from, :rules], &List.delete(&1, rule))
    |> then(fn c ->
      case to do
        :anystate -> Map.update!(c, :any_rules, &(&1 ++ [fun.(rule)]))
        state -> update_in(c, [:states, state, :rules], &(&1 ++ [fun.(rule)]))
      end
    end)
  end

  defp restore(s) do
    assert {:ok, packet} = Checkpoint.export(s)
    packet = packet |> Canonical.encode() |> JSON.decode!()

    Checkpoint.restore(s.contract, packet, s.now, :local_reference,
      capabilities: s.contract.card.extensions
    )
  end

  # room-work under A1 + A4 (selfloop.exs): `revise() as req => assigned` inside `assigned`.
  defp selfloop do
    c =
      compile("lead, member", """
      contract @2h => expired
      type assign
      type revise
      type ack
        request: ref(assign|revise)
      state new
        lead -> member: assign() as req awaits assigned
          => ack() & done
           | @15m & unacknowledged
      state assigned max_entries 6 => revision_limit
      state hold
        lead -> member: assign() as req => assigned
      end done
      end unacknowledged
      end revision_limit
      end expired
      """)

    move(c, "hold", "assign", "assigned", &%{&1 | kind: "revise"})
  end

  describe "A1-1: a transitioning rule in W may rebind W's name" do
    test "a requester re-send in W re-enters it; the late reply to the old request is refused" do
      c = selfloop()
      assert {:ok, s} = new(c, %{"lead" => "l", "member" => "m"})
      assert {%{status: :accepted}, s} = step(s, "m1", "l", "assign", 1 * @m)
      assert %{head: "m1", due_at: due} = s.wait
      assert due == 16 * @m
      assert {%{status: :accepted}, s} = step(s, "m2", "l", "revise", 14 * @m)
      assert %{state: "assigned", variables: %{"req" => "m2"}} = s
      assert s.entries["assigned"] == 2
      assert %{head: "m2", due_at: due} = s.wait
      assert due == 29 * @m

      assert {%{status: :rejected, reason: :reference}, s} =
               step(s, "a1", "m", "ack", 15 * @m, %{"request" => "m1"})

      assert {%{status: :accepted}, s} = step(s, "a2", "m", "ack", 20 * @m, %{"request" => "m2"})
      assert s.state == "done"
    end

    test "the rebinding re-send restores" do
      c = selfloop()
      assert {:ok, s} = new(c, %{"lead" => "l", "member" => "m"})
      assert {%{status: :accepted}, s} = step(s, "m1", "l", "assign", 1 * @m)
      assert {%{status: :accepted}, s} = step(s, "m2", "l", "revise", 14 * @m)
      assert {:ok, _} = restore(s)
    end

    test "an event, an `each` rule or a collection reply in W still may not rebind it" do
      c = selfloop()
      revise = Enum.find(c.states["assigned"].rules, &(&1.kind == "revise"))

      for {label, shape} <- [
            event: %{revise | event: true, target: nil},
            each: %{revise | each: true},
            collection: Map.put(revise, :collection, "acks")
          ] do
        bad = update_in(c, [:states, "assigned", :rules], &[shape | List.delete(&1, revise)])

        assert new(bad, %{"lead" => "l", "member" => "m"}) == {:error, :reply_correlation},
               "#{label}"
      end

      # The same holds for an anystate rule, which applies in W too.
      event = %{revise | event: true, target: nil}

      bad =
        c
        |> update_in([:states, "assigned", :rules], &List.delete(&1, revise))
        |> Map.update!(:any_rules, &(&1 ++ [event]))

      assert new(bad, %{"lead" => "l", "member" => "m"}) == {:error, :reply_correlation}
    end
  end

  # S2-validation's cancel under A1b (a1b.exs): the head lives in `anystate`.
  defp anystate_head do
    c =
      compile("lead, executor", """
      contract @2h => expired
      type go
      type cancel
      type cleanup_report
        cancel: ref(cancel)
      state new
        lead -> executor: go() => working
      state working @1h => stalled
      state holder
        lead -> executor: cancel() awaits cleaning
          => cleanup_report() & cancelled
           | @15m & cleanup_unreported
      state cleaning max_entries 5 => cleanup_unreported
      end cancelled
      end cleanup_unreported
      end stalled
      end expired
      """)

    move(c, "holder", "cancel", :anystate)
  end

  defp cleaning do
    c = anystate_head()
    assert {:ok, s} = new(c, %{"lead" => "l", "executor" => "e"})
    assert {%{status: :accepted}, s} = step(s, "g", "l", "go", 1 * @m)
    assert {%{status: :accepted}, s} = step(s, "c1", "l", "cancel", 2 * @m)
    assert %{state: "cleaning", variables: %{"cleaning" => "c1"}} = s
    assert %{head: "c1"} = s.wait
    s
  end

  describe "A1b-2: an anystate head binds, admits its reply and restores" do
    test "Engine.new accepts it and the correct reply is accepted" do
      s = cleaning()

      assert {%{status: :accepted}, s} =
               step(s, "r0", "e", "cleanup_report", 3 * @m, %{"cancel" => "c1"})

      assert s.state == "cancelled"
    end

    test "a reply naming another cancel is refused" do
      s = cleaning()

      assert {%{status: :rejected, reason: :reference}, _} =
               step(s, "r0", "e", "cleanup_report", 3 * @m, %{"cancel" => "g"})
    end

    test "the open wait restores, and the restored session admits the reply" do
      s = cleaning()
      assert {:ok, restored} = restore(s)

      assert {%{status: :accepted}, restored} =
               step(restored, "r0", "e", "cleanup_report", 3 * @m, %{"cancel" => "c1"})

      assert restored.state == "cancelled"
    end
  end

  describe "A1b-3: an anystate head never applies inside its own wait" do
    test "a second cancel while cleaning is illegal; the clock and the reply stand" do
      s = cleaning()
      due = s.wait.due_at

      assert {%{status: :rejected, reason: :illegal_message}, s} =
               step(s, "c2", "l", "cancel", 16 * @m)

      assert %{head: "c1", due_at: ^due} = s.wait
      assert s.entries["cleaning"] == 1

      assert {%{status: :accepted}, s} =
               step(s, "r1", "e", "cleanup_report", 16 * @m + 30_000, %{"cancel" => "c1"})

      assert s.state == "cancelled"
    end

    test "the wait's exits do not list its own anystate head" do
      c = anystate_head()
      [cancel] = c.any_rules
      refute Enum.any?(ReplyWait.exits(c, "cleaning"), &(&1.rule == cancel.id))
    end
  end

  # bypass.exs (Sofia's re-check of 19a3318): shapes the compiler refuses, given to Engine.new.
  defp bypass do
    compile("lead, member", """
    contract @2h => expired
    type assign
    type nudge
    type ack
      request: ref(assign)
    state new
      lead -> member: assign() as req awaits assigned
        => ack() & done
         | @15m & unacknowledged
    state assigned max_entries 6 => unacknowledged
    state hold @1h => stalled
      lead -> member: assign() as req => assigned
      lead -> member: nudge() => parked
    state parked
    end done
    end unacknowledged
    end stalled
    end expired
    """)
  end

  defp retarget(c, state, kind, fun) do
    update_in(c, [:states, state, :rules], fn rules ->
      Enum.map(rules, &if(&1.kind == kind, do: fun.(&1), else: &1))
    end)
  end

  describe "B1: W is entered only by its heads" do
    test "the bypass contract itself starts" do
      assert {:ok, _} = new(bypass(), %{"lead" => "l", "member" => "m"})
    end

    test "a rule in another state that enters W without being a head is refused (B1a)" do
      c = retarget(bypass(), "hold", "nudge", &%{&1 | target: "assigned"})
      assert new(c, %{"lead" => "l", "member" => "m"}) == {:error, :reply_correlation}
    end

    test "a non-head rule in W that re-enters W and rebinds its name is refused (B1b)" do
      c = bypass()
      nudge = Enum.find(c.states["hold"].rules, &(&1.kind == "nudge"))

      c =
        c
        |> update_in([:states, "hold", :rules], &List.delete(&1, nudge))
        |> update_in(
          [:states, "assigned", :rules],
          &(&1 ++ [%{nudge | target: "assigned", bind: "req"}])
        )

      assert new(c, %{"lead" => "l", "member" => "m"}) == {:error, :reply_correlation}
    end

    test "an anystate rule into W that is not a head is refused" do
      c = bypass()
      nudge = Enum.find(c.states["hold"].rules, &(&1.kind == "nudge"))

      c =
        c
        |> update_in([:states, "hold", :rules], &List.delete(&1, nudge))
        |> Map.update!(:any_rules, &(&1 ++ [%{nudge | target: "assigned"}]))

      assert new(c, %{"lead" => "l", "member" => "m"}) == {:error, :reply_correlation}
    end

    test "a timer into W is refused: it would enter W with no wait" do
      # The compiler sends a state timeout only to a terminal; a forged artifact need not.
      c = bypass()
      assert %{outcome: "stalled"} = c.states["hold"].timeout
      c = put_in(c, [:states, "hold", :timeout, :outcome], "assigned")
      assert new(c, %{"lead" => "l", "member" => "m"}) == {:error, :reply_correlation}
    end
  end

  describe "B4: an alternative may not bind the head's name" do
    test "Engine.new refuses it" do
      c = retarget(bypass(), "assigned", "ack", &%{&1 | bind: "req"})
      assert new(c, %{"lead" => "l", "member" => "m"}) == {:error, :reply_correlation}
    end
  end

  # Priya (priya/old-form 8ad2317): Totals.short skipped the reply wait on entry, so a re-send
  # under a `total` opened a fresh wait the budget could not hold instead of ending the run.
  # The compiler refuses a total shorter than the wait, so the dwell builds up over entries.
  defp budgeted(:resend) do
    compile("lead, member", """
    contract @12h => expired
    type request
    type deliverable
      request: ref(request)
    type partial
      request: ref(request)
    state new
      lead -> member: request() as req awaits working
        => deliverable() & delivered
         | partial() & reviewing
         | @1h & late
    state working total @2h => over_budget max_entries 3 => late
    state reviewing
      lead -> member: request() as req => working
    end delivered
    end late
    end over_budget
    """)
  end

  defp budgeted(:head) do
    compile("lead, member", """
    contract @12h => expired
    type go
    type request
    type deliverable
      request: ref(request)
    type partial
      request: ref(request)
    state new
      lead -> member: go() => reviewing
    state reviewing
      lead -> member: request() as req awaits working
        => deliverable() & delivered
         | partial() & reviewing
         | @1h & late
    state working total @2h => over_budget max_entries 3 => late
    end delivered
    end late
    end over_budget
    """)
  end

  # Two cycles through `working` (40m + 35m of its 2h total), then a third request at 85m:
  # 45 minutes are left and the wait needs 1h.
  defp third_request(path) do
    assert {:ok, s} = new(budgeted(path), %{"lead" => "l", "member" => "m"})

    s =
      if path == :head,
        do: elem(step(s, "g", "l", "go", 0), 1),
        else: s

    assert {%{status: :accepted, state: "working"}, s} = step(s, "r1", "l", "request", 0)

    assert {%{status: :accepted, state: "reviewing"}, s} =
             step(s, "p1", "m", "partial", 40 * @m, %{"request" => "r1"})

    assert {%{status: :accepted, state: "working"}, s} = step(s, "r2", "l", "request", 45 * @m)
    assert %{head: "r2", due_at: due} = s.wait
    assert due == 105 * @m

    assert {%{status: :accepted, state: "reviewing"}, s} =
             step(s, "p2", "m", "partial", 80 * @m, %{"request" => "r2"})

    step(s, "r3", "l", "request", 85 * @m)
  end

  describe "a reply wait counts against the active total on entry" do
    test "re-send path: the dwell left cannot hold a fresh wait, so the entry ends the run" do
      assert {%{status: :accepted, state: "over_budget", outcome: "over_budget"}, s} =
               third_request(:resend)

      assert s.wait == nil
    end

    test "head path: the dwell left cannot hold a fresh wait, so the entry ends the run" do
      assert {%{status: :accepted, state: "over_budget", outcome: "over_budget"}, s} =
               third_request(:head)

      assert s.wait == nil
    end
  end

  describe "T1-1: a renewal leaves room for the wait the renewed rule opens" do
    # t1_renew.exs: the owed `deploy` re-send keeps a 10m duty in `prep` and opens a 1h wait.
    defp timed_head do
      c0 =
        compile("lead, deployer", """
        contract @2h => expired
        type deploy
        type go
        type hb
        type never
        type ok
          request: ref(deploy)
        state new
          lead -> deployer: deploy() awaits deploying
            => ok() & deployed
             | @1h & stalled
          lead -> deployer: go() => prep
        state prep
          lead -> deployer: deploy() => deploying
          event lead -> deployer: hb() => @self
          lead -> deployer: never() @10m => lead_silent | lead_silent
        end deployed
        end stalled
        end lead_silent
        end expired
        """)

      prep = c0.states["prep"]
      resend = Enum.find(prep.rules, &(&1.kind == "deploy"))
      [duty] = prep.deadlines

      rules =
        prep.rules
        |> Enum.reject(&(&1.kind == "never"))
        |> Enum.map(
          &if(&1.kind == "hb", do: %{&1 | reset: %{&1.reset | rule: resend.id}}, else: &1)
        )

      put_in(c0, [:states, "prep"], %{prep | deadlines: [%{duty | kind: "deploy"}], rules: rules})
    end

    test "an @self renewal whose continuation's wait cannot fit is insufficient_time" do
      c = timed_head()
      assert {:ok, s} = new(c, %{"lead" => "l", "deployer" => "d"})
      assert {%{status: :accepted}, s} = step(s, "g1", "l", "go", 70 * @m)
      assert [%{due_at: due}] = s.obligations
      assert due == 80 * @m
      # 75m + 10m renewed, then a 1h wait: 145m is past the 2h contract clock.
      assert {%{status: :rejected, reason: :insufficient_time}, s} =
               step(s, "h1", "l", "hb", 75 * @m)

      assert [%{due_at: ^due}] = s.obligations
    end

    test "a renewal that leaves the wait its whole window is accepted" do
      c = timed_head()
      assert {:ok, s} = new(c, %{"lead" => "l", "deployer" => "d"})
      assert {%{status: :accepted}, s} = step(s, "g1", "l", "go", 10 * @m)
      assert {%{status: :accepted}, s} = step(s, "h1", "l", "hb", 15 * @m)
      assert [%{due_at: due}] = s.obligations
      assert due == 25 * @m
    end
  end

  # S2 scenario 14 (Darius, ada-s2-latestop): a stop is always admitted. An anystate head's
  # wait may run past the contract cap; the cap then ends the run with its own outcome.
  # A head in a state still needs its wait to fit (insufficient_time).
  defp stoppable do
    compile("lead, executor", """
    contract @2h => expired
    type go
    type stop
    type cancel
    type cleanup_report
      cancel: ref(cancel)
    type stop_ack
      stop: ref(stop)
    state new
      lead -> executor: go() => working
    state working
      lead -> executor: stop() awaits stopping
        => stop_ack() & stopped
         | @15m & stop_unacknowledged
    state stopping
    anystate
      lead -> executor: cancel() awaits cleaning
        => cleanup_report() & cancelled
         | @15m & cleanup_unreported
    state cleaning
    end stopped
    end stop_unacknowledged
    end cancelled
    end cleanup_unreported
    end expired
    """)
  end

  defp working do
    assert {:ok, s} = new(stoppable(), %{"lead" => "l", "executor" => "e"})
    assert {%{status: :accepted, state: "working"}, s} = step(s, "g", "l", "go", 0)
    s
  end

  describe "an anystate head is always admitted; the cap ends its wait" do
    test "a cancel at 106m of 2h opens a wait due past the cap; the cap fires with expired" do
      s = working()

      assert {%{status: :accepted, state: "cleaning"}, s} =
               step(s, "x1", "l", "cancel", 106 * @m)

      assert %{head: "x1", due_at: due} = s.wait
      assert due == 121 * @m
      assert {:ok, s} = Engine.tick(s, 120 * @m)
      assert %{status: :terminal, outcome: "expired"} = s
      assert s.wait == nil
    end

    test "the cleanup report before the cap is still accepted" do
      s = working()
      assert {%{status: :accepted}, s} = step(s, "x1", "l", "cancel", 106 * @m)

      assert {%{status: :accepted}, s} =
               step(s, "k1", "e", "cleanup_report", 110 * @m, %{"cancel" => "x1"})

      assert s.outcome == "cancelled"
    end

    test "the wait due past the cap restores, and the cap still ends it" do
      s = working()
      assert {%{status: :accepted}, s} = step(s, "x1", "l", "cancel", 106 * @m)
      assert {:ok, restored} = restore(s)
      assert %{head: "x1", due_at: due} = restored.wait
      assert due == 121 * @m
      assert {:ok, restored} = Engine.tick(restored, 120 * @m)
      assert restored.outcome == "expired"
    end

    test "a head in a state keeps insufficient_time when its wait cannot fit" do
      s = working()

      assert {%{status: :rejected, reason: :insufficient_time}, s} =
               step(s, "s1", "l", "stop", 106 * @m)

      assert s.state == "working"

      assert {%{status: :accepted, state: "stopping"}, _} =
               step(working(), "s2", "l", "stop", 105 * @m)
    end

    test "check reports the state head's last admissible time, and when the cap cuts a stop's wait" do
      c = stoppable()
      report = ContractLab.AdmissionLimits.report(c)
      [cancel] = c.any_rules

      # A stop is admissible until the cap; from cap minus its wait, the cap cuts the wait.
      assert [
               %{
                 "kind" => "cancel",
                 "state" => "anystate",
                 "stop" => true,
                 "window_ms" => 0,
                 "last_admissible_ms" => 7_199_999,
                 "wait_cut_ms" => 6_300_000,
                 "wait_cut" => "105m"
               }
             ] = Enum.filter(report, &(&1["rule"] == cancel.id))

      assert [%{"kind" => "stop", "window_ms" => 900_000, "last_admissible" => "105m"} = head] =
               Enum.filter(report, &(&1["kind"] == "stop"))

      refute Map.has_key?(head, "stop")
    end
  end

  # Sofia 1a (SECURITY-ANYSTATE-STOP.md, sofia-probes/df28): only a stop is exempt. An anystate
  # head whose alternatives or timeout lead anywhere but an `end` terminal keeps the cap check,
  # so its sender cannot escape its own owed duty in the last window before the cap.
  defp escalation do
    compile("lead, executor", """
    contract @2h => expired
    type go
    type escalate
    type guidance
      escalate: ref(escalate)
    type cancel
    type cleanup_report
      cancel: ref(cancel)
    type work
    state new
      executor -> lead: go() => executing
    state executing
      executor -> lead: work() @30m => worked | silent
    state worked
    anystate
      lead -> executor: cancel() awaits cleaning
        => cleanup_report() & cancelled
         | @15m & cleanup_unreported
      executor -> lead: escalate() awaits escalated
        => guidance() & executing
         | @30m & unguided
    state escalated max_entries 3 => unguided
    state cleaning max_entries 3 => cleanup_unreported
    end silent
    end cancelled
    end cleanup_unreported
    end unguided
    end expired
    """)
  end

  defp executing do
    assert {:ok, s} = new(escalation(), %{"lead" => "l", "executor" => "e"})
    assert {%{status: :accepted, state: "executing"}, s} = step(s, "g", "e", "go", 80 * @m)
    s
  end

  describe "only a stop is exempt from the cap check (Sofia 1a)" do
    test "a non-stop anystate head at 109m is insufficient_time; the owed work ends the run silent" do
      assert {%{status: :rejected, reason: :insufficient_time}, s} =
               step(executing(), "x1", "e", "escalate", 109 * @m)

      assert {:ok, s} = Engine.tick(s, 120 * @m)
      assert s.outcome == "silent"
    end

    test "the cancel is no stop here: escalate can leave its wait for a live state (1a-2)" do
      assert {%{status: :rejected, reason: :insufficient_time}, s} =
               step(executing(), "c1", "l", "cancel", 109 * @m)

      assert {:ok, s} = Engine.tick(s, 120 * @m)
      assert s.outcome == "silent"
    end

    test "check reports the non-stop head's last admissible time like any head's" do
      c = escalation()
      escalate = Enum.find(c.any_rules, &(&1.kind == "escalate"))

      assert [%{"window_ms" => 1_800_000, "last_admissible" => "90m"} = entry] =
               Enum.filter(ContractLab.AdmissionLimits.report(c), &(&1["rule"] == escalate.id))

      refute Map.has_key?(entry, "stop")
    end
  end

  # Sofia 1a-2 (pivot.exs): a stop's wait state must not be left for a live state. Here
  # `cleaning` holds the lead's `resume => idle`, so the lead, who owes a receipt by 118m,
  # cancels at 117m, resumes and finishes `done` before the cap. Every exit of W, every host
  # trigger that applies in W and W's own clocks must end the run for the head to be a stop.
  defp pivot do
    compile("lead, executor", """
    contract @2h => expired
    type go
    type result
    type receipt
      report: ref(result)
    type cancel
    type cleanup_report
      cancel: ref(cancel)
    type resume
    type finish
    state new
      executor -> lead: go() => executing
    state executing
      executor -> lead: result() awaits reported
        => receipt() & handed_off
         | @10m & handoff_unacknowledged
    state reported
    state idle
      lead -> executor: finish() => done
    anystate
      lead -> executor: cancel() awaits cleaning
        => cleanup_report() & cancelled
         | @15m & cleanup_unreported
    state cleaning max_entries 3 => cleanup_unreported
      lead -> executor: resume() => idle
    end handed_off
    end handoff_unacknowledged
    end cancelled
    end cleanup_unreported
    end done
    end expired
    """)
  end

  describe "a stop's wait state ends the run on every path (Sofia 1a-2)" do
    test "pivot: the cancel at 117m is insufficient_time; the owed receipt ends the run" do
      assert {:ok, s} = new(pivot(), %{"lead" => "l", "executor" => "e"})
      assert {%{status: :accepted}, s} = step(s, "g", "e", "go", 0)
      assert {%{status: :accepted, state: "reported"}, s} = step(s, "r1", "e", "result", 108 * @m)

      assert {%{status: :rejected, reason: :insufficient_time}, s} =
               step(s, "c1", "l", "cancel", 117 * @m)

      assert {:ok, s} = Engine.tick(s, 118 * @m)
      assert s.outcome == "handoff_unacknowledged"
    end

    test "a host trigger or a clock of W that enters a live state makes it no stop" do
      c = stoppable()
      [cancel] = c.any_rules
      assert ReplyWait.stop?(c, cancel, "cleaning")

      trigger = %{
        id: "on-1",
        kind: "presence",
        subject: "executor",
        value: "offline",
        state: "cleaning",
        after_ms: 60_000,
        target: "working"
      }

      for {label, forged} <- [
            trigger: put_in(c, [:extensions, :triggers], [trigger]),
            any_trigger: put_in(c, [:extensions, :triggers], [%{trigger | state: "*"}]),
            timeout:
              put_in(c, [:states, "cleaning", :timeout], %{after_ms: 60_000, outcome: "working"}),
            total:
              put_in(c, [:states, "cleaning", :total], %{after_ms: 60_000, outcome: "working"}),
            max_entries:
              put_in(c, [:states, "cleaning", :max_entries], %{limit: 2, outcome: "working"})
          ] do
        refute ReplyWait.stop?(forged, cancel, "cleaning"), "#{label}"
      end

      ended = put_in(c, [:extensions, :triggers], [%{trigger | target: "cancelled"}])
      assert ReplyWait.stop?(ended, cancel, "cleaning")
      other = put_in(c, [:extensions, :triggers], [%{trigger | state: "working"}])
      assert ReplyWait.stop?(other, cancel, "cleaning")
    end

    test "a stop is admissible strictly before the cap, as check reports" do
      assert {%{status: :accepted, state: "cleaning"}, _} =
               step(working(), "c8", "l", "cancel", 120 * @m - 1)

      assert {%{status: :rejected}, _} = step(working(), "c9", "l", "cancel", 120 * @m)
    end
  end
end
