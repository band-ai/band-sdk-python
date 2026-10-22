defmodule ContractLab.FollowOnReserveTest do
  # DF-56: an owed reply reserves itself plus the reserve its own admission will need, so
  # the engine never accepts a duty whose reply it would then refuse.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @room_work File.read!(Path.join(__DIR__, "fixtures/room-work-v030.band"))

  defp compile(source) do
    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => source,
               "sha256" => Canonical.hash(source)
             })

    c
  end

  defp start(source, bindings, max_messages) do
    c = compile(source)

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: max_messages,
               bindings: bindings,
               inactivity: :accepted,
               room_policy: :unchecked,
               capabilities: c.card.extensions
             )

    s
  end

  defp send(s, actor, id, kind, payload \\ %{}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: s.now + 1_000
      )

  # room-work v0.3.0 (scenario 20): assign and ack, then status notes until the reserve
  # refuses one. The notes stop at 98 facts: deliverable 1 + closing path 1 stay reserved.
  defp filled_room do
    s = start(@room_work, %{"lead" => "ada", "member" => "omar"}, 100)

    {%{status: :accepted}, s} =
      send(s, "ada", "a1", "assign", %{
        "op" => "op-df56",
        "attempt" => 1,
        "task" => "t",
        "deliverable" => "d",
        "input" => ""
      })

    {%{status: :accepted}, s} =
      send(s, "omar", "k1", "ack", %{"request" => "a1", "running" => ""})

    s =
      Enum.reduce(1..96, s, fn n, s ->
        assert {%{status: :accepted}, s} =
                 send(s, "omar", "s#{n}", "status", %{"request" => "a1", "note" => "n"})

        s
      end)

    assert {%{status: :rejected, reason: :completion_capacity}, s} =
             send(s, "omar", "s97", "status", %{"request" => "a1", "note" => "n"})

    assert map_size(s.facts) == 98 and s.state == "working"
    s
  end

  test "DF-51: a human question whose answer could not fit is refused, not stranded" do
    s = filled_room()

    # Today it is accepted at 99 facts, and then the owed human answer (and a cancel) are
    # refused completion_capacity: working would owe a delivery that no longer fits.
    assert {%{status: :rejected, reason: :completion_capacity}, s} =
             send(s, "omar", "h1", "human_question", %{"request" => "a1", "text" => "Push?"})

    assert {%{status: :rejected, reason: :completion_capacity}, s} =
             send(s, "omar", "q1", "question", %{"request" => "a1", "text" => "Push?"})

    # The member can still deliver and the lead can still accept.
    assert {%{status: :accepted}, s} =
             send(s, "omar", "d1", "deliverable", %{
               "request" => "a1",
               "summary" => "done",
               "effects" => ""
             })

    assert {%{status: :accepted}, s} = send(s, "ada", "ok", "accept", %{"deliverable" => "d1"})
    assert s.outcome == "accepted"
  end

  test "DF-51: a revise that would owe an ack and then a delivery is refused near the cap" do
    s = filled_room()

    assert {%{status: :rejected, reason: :completion_capacity}, s} =
             send(s, "ada", "r1", "revise", %{"task" => "t2", "input" => ""})

    # The lead's cancel still fits: its report ends the session.
    assert {%{status: :accepted}, s} = send(s, "ada", "c1", "cancel", %{"reason" => "withdrawn"})

    assert {%{status: :accepted}, s} =
             send(s, "omar", "x1", "cancel_ack", %{
               "cancel" => "c1",
               "done" => "none",
               "effects" => ""
             })

    assert s.outcome == "cancelled"
  end

  test "one note earlier, the human question fits and so do its answer and a cancel" do
    # max_messages 99 with 94 notes: 96 facts. The question needs itself (97), the answer
    # (98) and the delivery after it (99); a cancel and its report fit the same way.
    s = start(@room_work, %{"lead" => "ada", "member" => "omar"}, 99)

    {%{status: :accepted}, s} =
      send(s, "ada", "a1", "assign", %{
        "op" => "op-df56",
        "attempt" => 1,
        "task" => "t",
        "deliverable" => "d",
        "input" => ""
      })

    {%{status: :accepted}, s} =
      send(s, "omar", "k1", "ack", %{"request" => "a1", "running" => ""})

    s =
      Enum.reduce(1..94, s, fn n, s ->
        {%{status: :accepted}, s} =
          send(s, "omar", "s#{n}", "status", %{"request" => "a1", "note" => "n"})

        s
      end)

    assert map_size(s.facts) == 96

    assert {%{status: :accepted}, asking} =
             send(s, "omar", "h1", "human_question", %{"request" => "a1", "text" => "Push?"})

    assert {%{status: :accepted}, answered} =
             send(asking, "ada", "e1", "human_answer", %{
               "question" => "h1",
               "who" => "eric",
               "text" => "No."
             })

    assert {%{status: :accepted}, _} =
             send(answered, "omar", "d1", "deliverable", %{
               "request" => "a1",
               "summary" => "done",
               "effects" => ""
             })

    assert {%{status: :accepted}, cancelling} =
             send(asking, "ada", "c1", "cancel", %{"reason" => "withdrawn"})

    assert {%{status: :accepted}, _} =
             send(cancelling, "omar", "x1", "cancel_ack", %{
               "cancel" => "c1",
               "done" => "none",
               "effects" => ""
             })
  end

  @chain """
  ---
  name: follow-on-chain
  description: An answer that opens three owed reviews.
  engine: 0.2.0-lab.1
  roles: [lead, a, x, y, z]
  ---
  contract @30m => expired
  type ask
  type answer
    ask: ref(ask)
  type review
  type abandon
  state new
    lead -> a: ask() awaits asking
      => answer() & reviewing
       | @5m & stalled
  state reviewing
    {x,y,z} -> lead: review() each @5m => done | stalled
  anystate
    lead -> a: abandon() => abandoned
  end done
  end stalled
  end abandoned
  """

  @chain_bindings %{"lead" => "l", "a" => "a", "x" => "x", "y" => "y", "z" => "z"}

  test "the reserve follows owed replies, not the cheapest closing path" do
    # The answer needs itself plus three reviews (5 with the ask). Reserving the answer and
    # the one-message abandon would admit the ask at 4 and strand the answer.
    s = start(@chain, @chain_bindings, 4)
    assert {%{status: :rejected, reason: :completion_capacity}, _} = send(s, "l", "q", "ask")

    s = start(@chain, @chain_bindings, 5)
    assert {%{status: :accepted}, s} = send(s, "l", "q", "ask")
    assert {%{status: :accepted}, s} = send(s, "a", "ans", "answer", %{"ask" => "q"})

    s =
      Enum.reduce(~w(x y z), s, fn r, s ->
        assert {%{status: :accepted}, s} = send(s, r, "r-#{r}", "review")
        s
      end)

    assert s.outcome == "done"
  end

  @timed_event """
  ---
  name: timed-event-reserve
  description: A timed event reply keeps the closing move that follows it.
  engine: 0.2.0-lab.1
  roles: [lead, a]
  ---
  contract @30m => expired
  type request
  type answer
  type done
  state new
    lead -> a: request() => open
  state open
    event a -> lead: answer() @5m => stalled
    lead -> a: done() => complete
  end complete
  end stalled
  """

  test "a timed event duty also reserves its state's closing path" do
    # At 2, the request would leave room for the answer only; the answer, an event, then
    # needs the closing move too and would be refused.
    s = start(@timed_event, %{"lead" => "l", "a" => "a"}, 2)

    assert {%{status: :rejected, reason: :completion_capacity}, _} =
             send(s, "l", "r", "request")

    s = start(@timed_event, %{"lead" => "l", "a" => "a"}, 3)
    assert {%{status: :accepted}, s} = send(s, "l", "r", "request")
    assert {%{status: :accepted}, s} = send(s, "a", "ans", "answer")
    assert {%{status: :accepted}, s} = send(s, "l", "fin", "done")
    assert s.outcome == "complete"
  end

  @turns """
  ---
  name: turn-taking
  description: Each move owes the other side's next move.
  engine: 0.2.0-lab.1
  roles: [white, black]
  ---
  contract @30m => expired
  type start
  type move
  type reply
    move: ref(move)
  state new
    white -> black: start() => white_turn
  state white_turn
    white -> black: move() as m @5m awaits black_turn | white_timeout
      => reply() & white_turn
       | @5m & black_timeout
  state black_turn max_entries 10 => exhausted
  end white_timeout
  end black_timeout
  end exhausted
  """

  test "a cycle of owed replies keeps today's reserve (declared limit)" do
    # Each move owes the other side's move. A follow-on that leads back into the same cycle
    # counts 0, so each turn reserves only the next move, as before DF-56: near the cap the
    # last owed move of a cycle can still be refused.
    s = start(@turns, %{"white" => "w", "black" => "b"}, 3)
    assert {%{status: :accepted}, s} = send(s, "w", "go", "start")
    assert {%{status: :accepted}, s} = send(s, "w", "m1", "move")
    assert s.state == "black_turn"

    assert {%{status: :rejected, reason: :completion_capacity}, _} =
             send(s, "b", "m2", "reply", %{"move" => "m1"})
  end

  @into_cycle """
  ---
  name: into-cycle
  description: An owed plan that opens a draft and review cycle.
  engine: 0.2.0-lab.1
  roles: [lead, a]
  ---
  contract @30m => expired
  type start
  type plan
    start: ref(start)
  type draft
  type redo
    draft: ref(draft)
  state new
    lead -> a: start() awaits kickoff
      => plan() & drafting
       | @5m & stalled
  state drafting
    a -> lead: draft() as d @5m awaits judging | stalled
      => redo() & drafting
       | @5m & stalled
  state judging max_entries 10 => stalled
  end stalled
  """

  test "a follow-on into a cycle counts the cycle state's own replies" do
    # kickoff reserves the plan and the first draft it opens (2); inside the cycle each turn
    # reserves only its next reply.
    s = start(@into_cycle, %{"lead" => "l", "a" => "a"}, 2)
    assert {%{status: :rejected, reason: :completion_capacity}, _} = send(s, "l", "go", "start")

    s = start(@into_cycle, %{"lead" => "l", "a" => "a"}, 3)
    assert {%{status: :accepted}, s} = send(s, "l", "go", "start")
    assert {%{status: :accepted}, s} = send(s, "a", "p", "plan", %{"start" => "go"})
    assert s.state == "drafting"
  end

  # DF-25 x DF-56: a collect with a quorum closes into either target, so the reserve and the
  # closing path must cover the worse one. sec is a required lane that is offline from the
  # start, so the quorum fails and the last report enters the quorum target `rescue`.
  @quorum_start 200_000_000

  defp quorum_source(fallback) do
    ContractLab.TestClock.ensure("""
    ---
    name: quorum-reserve
    description: A failed quorum enters a state with its own path to the end.
    engine: 0.2.0-lab.1
    roles: [lead, lanes]
    extensions: [host.presence/1]
    ---
    contract @30m => expired
    type lane: agent
      required: boolean
    lanes: list(lane)
    type request
    type note
    type lane_report
    type compacted
    type rescue_report
    type final
    state new
      lead -> lanes: request() => reviewing
    state reviewing
      event lead -> lanes: note()
      collect reports from lanes -> lead @5m => verifying
        reply lane_report()
        offline @30s
        quorum required => rescue
    state verifying
      lead -> lanes: compacted() => done
    #{fallback}
    end done
    end stalled
    """)
  end

  defp quorum_room(fallback) do
    lanes =
      for {name, required} <- [{"sec", true}, {"web", true}, {"gen", false}],
          do: %{"name" => name, "actor" => name, "attributes" => %{"required" => required}}

    assert {:ok, s} =
             Engine.new(compile(quorum_source(fallback)),
               session_id: "s",
               now: @quorum_start,
               bindings: %{"lead" => "l", "lanes" => lanes},
               max_messages: 12,
               capabilities: ["host.presence/1"],
               max_controls: 32,
               initial_presence: %{
                 "lanes/sec" => %{"value" => "offline", "since" => @quorum_start - 3_600_000}
               },
               room_policy: :unchecked
             )

    assert {%{status: :accepted}, s} = send(s, "l", "go", "request")

    # The lead's notes run until the reserve refuses one.
    Enum.reduce_while(1..12, s, fn n, s ->
      case send(s, "l", "n#{n}", "note") do
        {%{status: :accepted}, s} -> {:cont, s}
        {%{status: :rejected, reason: :completion_capacity}, s} -> {:halt, s}
      end
    end)
  end

  defp report(s, lane), do: send(s, lane, "r-#{lane}", "lane_report")

  test "the last report into a quorum target that owes a chain still fits" do
    s =
      quorum_room("""
      state rescue
        lead -> lanes: rescue_report() @5m => fixing | stalled
      state fixing
        lead -> lanes: final() @5m => done | stalled
      """)

    assert {%{status: :accepted}, s} = report(s, "web")
    # gen settles the last member; sec is offline, so the close enters rescue.
    assert {%{status: :accepted}, s} = report(s, "gen")
    assert s.state == "rescue"
    assert {%{status: :accepted}, s} = send(s, "l", "rr", "rescue_report")
    assert {%{status: :accepted}, s} = send(s, "l", "fin", "final")
    assert s.outcome == "done"
  end

  test "the closing reserve counts the worse of a collect's two targets" do
    s =
      quorum_room("""
      state rescue
        lead -> lanes: rescue_report() => fixing
      state fixing
        lead -> lanes: final() => done
      """)

    assert {%{status: :accepted}, s} = report(s, "web")
    assert {%{status: :accepted}, s} = report(s, "gen")
    assert s.state == "rescue"
    # Two unowed moves end the session from rescue; the notes must have left both.
    assert {%{status: :accepted}, s} = send(s, "l", "rr", "rescue_report")
    assert {%{status: :accepted}, s} = send(s, "l", "fin", "final")
    assert s.outcome == "done"
  end
end
