defmodule ContractLab.HostSignalsTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  defp contract(transform \\ &Function.identity/1) do
    bytes = """
    ---
    name: host-signals
    description: Agent work reacts to trusted room conditions.
    engine: 0.2.0-lab.1
    roles: [requester, assistant]
    extensions: [host.presence/1, host.membership/1]
    ---
    watch supervisor
    type question
    type ack
      question: ref(question)
    type answer
    state new
      requester -> assistant: question() awaits accepting
        => ack() & working
         | @10m & stalled
    state working
      on assistant offline for 5m => paused
      on watch.supervisor offline for 5m => paused
      assistant -> requester: answer() @10m => complete | stalled
    state paused max_entries 3 => stalled
      on assistant online => working
      assistant -> requester: answer() => complete
    anystate
      on assistant removed => left
    end complete
    end stalled
    end left
    """

    content = ContractLab.TestClock.ensure(transform.(bytes))

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => content,
      "sha256" => Canonical.hash(content)
    })
  end

  defp working(limit \\ 32) do
    assert {:ok, c} = contract()

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               bindings: %{"requester" => "r", "assistant" => "a"},
               now: 0,
               max_messages: 3,
               capabilities: c.card.extensions,
               max_controls: limit,
               watch_bindings: %{"supervisor" => "human-42"},
               room_policy: :unchecked
             )

    assert {%{status: :accepted}, s} =
             Engine.step(
               s,
               %{"id" => "q", "session" => "s", "kind" => "question", "payload" => %{}},
               actor: "r",
               now: 0
             )

    assert {%{status: :accepted, state: "working"}, s} =
             Engine.step(
               s,
               %{
                 "id" => "k",
                 "session" => "s",
                 "kind" => "ack",
                 "payload" => %{"question" => "q"}
               },
               actor: "a",
               now: 0
             )

    {c, s}
  end

  defp event(sequence, value, at, subject \\ "assistant", principal \\ "a") do
    %{
      "id" => "host-#{sequence}",
      "sequence" => sequence,
      "subject" => subject,
      "principal" => principal,
      "kind" => if(value == "removed", do: "membership", else: "presence"),
      "value" => value,
      "at" => at
    }
  end

  defp signal(s, e), do: Engine.signal(s, e, authority: :host, now: e["at"])

  test "offline dwell survives restart and a nonterminal transition retains the original duty" do
    {c, s} = working()
    assert {%{status: :host_accepted}, s} = signal(s, event(1, "offline", 1000))

    assert {:ok, s} =
             StateCodec.restore(c, StateCodec.export(s), 200_000, capabilities: c.card.extensions)

    assert {:ok, %{state: "working"}} = Engine.tick(s, 300_999)
    assert {:ok, %{state: "paused"} = paused} = Engine.tick(s, 301_000)
    assert [%{due_at: 600_000}] = paused.obligations

    assert {:ok, restored} =
             StateCodec.restore(c, StateCodec.export(paused), 301_001,
               capabilities: c.card.extensions
             )

    assert {:ok, %{outcome: "stalled"}} = Engine.tick(restored, 600_000)
  end

  test "reconnection before the dwell deadline cancels the pending trigger" do
    {_, s} = working()
    assert {_, s} = signal(s, event(1, "offline", 1000))
    assert {_, s} = signal(s, event(2, "online", 300_999))
    assert {:ok, %{state: "working"}} = Engine.tick(s, 301_000)
  end

  test "duplicate observations and repeated offline status do not renew the clock" do
    {_, s} = working()
    e = event(1, "offline", 1000)
    assert {_, s} = signal(s, e)
    assert {%{status: :duplicate_host_event}, ^s} = signal(s, e)
    assert {_, s} = signal(s, event(2, "offline", 200_000))

    assert {%{status: :rejected, reason: :stale_host_event}, ^s} =
             signal(s, %{e | "id" => "stale", "at" => 200_000})

    assert {:ok, %{state: "paused"}} = Engine.tick(s, 301_000)
  end

  test "an equal sequence under a new id is stale; sequences strictly increase" do
    {_, s} = working()
    assert {%{status: :host_accepted}, s} = signal(s, event(1, "offline", 1000))

    assert {%{status: :rejected, reason: :stale_host_event}, ^s} =
             signal(s, %{event(1, "online", 2000) | "id" => "host-1b"})

    assert {%{status: :host_accepted}, _} = signal(s, event(2, "online", 2000))
  end

  test "membership removal is immediate and does not spend business-message capacity" do
    {c, s} = working()

    assert {%{status: :host_accepted}, %{outcome: "left"} = done} =
             signal(s, event(1, "removed", 1000))

    # The question and its ack, and nothing for the removal.
    assert map_size(done.facts) == 2
    assert done.mentions_used == 2

    assert {:ok, _} =
             StateCodec.restore(c, StateCodec.export(done), 1001, capabilities: c.card.extensions)
  end

  test "ordinary messages cannot forge host authority or watched identities" do
    {_, s} = working()
    e = event(1, "offline", 1000)

    assert {%{status: :rejected, reason: :host_authority_required}, ^s} =
             Engine.signal(s, e, now: 1000)

    assert {%{status: :rejected, reason: :subject_binding}, ^s} =
             signal(s, %{e | "principal" => "impostor"})

    forged = %{
      "id" => "fake",
      "session" => "s",
      "kind" => "presence",
      "payload" => %{"value" => "offline"}
    }

    assert {%{status: :rejected, reason: :illegal_message}, _} =
             Engine.step(s, forged, actor: "a", now: 1000)
  end

  test "a watched human remains outside the session's agent roles" do
    {c, s} = working()
    refute "supervisor" in c.roles
    refute "human-42" in Map.values(s.bindings)
    assert {_, s} = signal(s, event(1, "offline", 1000, "watch.supervisor", "human-42"))
    assert {:ok, %{state: "paused"}} = Engine.tick(s, 301_000)
  end

  test "control exhaustion has a reserved terminal record" do
    {c, s} = working(3)
    assert {_, s} = signal(s, event(1, "offline", 1))
    assert {_, s} = signal(s, event(2, "online", 2))

    assert {%{status: :rejected, reason: :control_capacity}, done} =
             signal(s, event(3, "offline", 3))

    assert done.outcome == "control_capacity_exhausted"
    assert length(done.control_log) == 3

    assert {:ok, _} =
             StateCodec.restore(c, StateCodec.export(done), 4, capabilities: c.card.extensions)
  end

  test "removal can use the final reserved control slot" do
    {_, s} = working(3)
    assert {_, s} = signal(s, event(1, "offline", 1))
    assert {_, s} = signal(s, event(2, "online", 2))

    assert {%{status: :host_accepted}, %{outcome: "left"} = done} =
             signal(s, event(3, "removed", 3))

    assert length(done.control_log) == 3
  end

  test "unknown presence interrupts continuous offline evidence" do
    {_, s} = working()
    assert {_, s} = signal(s, event(1, "offline", 1000))
    assert {_, s} = signal(s, event(2, "unknown", 200_000))
    assert {_, s} = signal(s, event(3, "offline", 250_000))
    assert {:ok, %{state: "working"}} = Engine.tick(s, 301_000)
    assert {:ok, %{state: "paused"}} = Engine.tick(s, 550_000)
  end

  test "the original work deadline wins a tie with presence dwell" do
    {_, s} = working()
    assert {_, s} = signal(s, event(1, "offline", 300_000))
    assert {:ok, %{outcome: "stalled"}} = Engine.tick(s, 600_000)
  end

  test "host transitions cannot promise a new duty past the hard cap" do
    {c, s} = working()

    modified =
      put_in(c.states["paused"].deadlines, [
        hd(c.states["working"].deadlines) |> Map.put(:id, "extra")
      ])

    s = %{s | contract: modified, max_at: 700_000}
    next = Engine.host_transition(s, "paused", 300_000)
    assert next.outcome == "insufficient_time"
  end

  test "host transitions preserve capacity for carried and newly created work" do
    {c, s} = working()

    extra =
      hd(c.states["working"].deadlines)
      |> Map.merge(%{id: "extra", kind: "question", participants: ["requester"]})

    reply = hd(c.states["new"].rules)

    modified =
      c
      |> put_in([:states, "paused", :deadlines], [extra])
      |> update_in([:states, "paused", :rules], &(&1 ++ [reply]))

    next = Engine.host_transition(%{s | contract: modified}, "paused", 1000)
    assert next.outcome == "completion_capacity"
  end

  test "immediate triggers and exact-boundary reconnect preserve the original work deadline" do
    assert {:ok, c} = contract(&String.replace(&1, "offline for 5m", "offline"))
    {_, s} = working()
    assert {_, paused} = signal(%{s | contract: c}, event(1, "offline", 1000))
    assert paused.state == "paused"
    assert {_, resumed} = signal(paused, event(2, "online", 1001))
    assert resumed.state == "working"
    assert [%{due_at: 600_000}] = resumed.obligations
    {_, s} = working()
    assert {_, s} = signal(s, event(1, "offline", 1000))
    assert {_, resumed} = signal(s, event(2, "online", 301_000))
    assert resumed.state == "working"
    assert [%{due_at: 600_000}] = resumed.obligations
  end

  test "membership removal of an agent cannot leave an active session" do
    assert {:error, %{code: :membership_outcome}} =
             contract(&String.replace(&1, "removed => left", "removed => paused"))

    # A trigger into new also puts the question's wait on a cycle, so the vector bounds the
    # wait to leave only the initial-state error.
    assert {:error, %{code: :initial_state_target}} =
             contract(
               &(&1
                 |> String.replace("online => working", "online => new")
                 |> String.replace(
                   "state working\n",
                   "state accepting max_entries 3 => stalled\nstate working\n"
                 ))
             )
  end
end
