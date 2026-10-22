defmodule ContractLab.ReplyCounterRequestTest do
  # ubf-replies/old-form/RULE.md §A (op-ns6bjbpq part 2).
  # A1: in a wait W the replier leaves only by a head or re-send of another reply block (a
  # counter-request) or by a rule into an `end` terminal; a rule (not an event) in W may rebind
  # W's name, because any transition ends the wait; an `anystate` rule that applies in W obeys
  # the same rules. A1b: a head may sit in `anystate`; it applies everywhere except in its own
  # wait, so a second request there cannot restart the clock.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @header "---\nname: room-work-a1\ndescription: Room-work under A1.\nengine: 0.2.0-lab.1\nroles: [lead, member]\n---\n"

  @types """
  contract @1d => expired
  type assign
  type revise
  type cancel
  type abandon
  type ack
    request: ref(assign|revise)
  type question
    request: ref(assign|revise)
  type answer
    question: ref(question)
  type deliverable
    request: ref(assign|revise)
  type accept
    deliverable: ref(deliverable)
  type cancel_ack
    cancel: ref(cancel)
  """

  @ends """
  end unacknowledged
  end revision_limit
  end stalled
  end unanswered
  end question_limit
  end unreviewed
  end accepted
  end cancelled
  end cleanup_unconfirmed
  end abandoned
  """

  # RULE.md §A "room-work under A1", without the human question and the status event.
  @a1 """
  state new
    lead -> member: assign() as req awaits assigned
      => ack() & working
       | @15m & unacknowledged
  state assigned
    lead -> member: revise() as req => revised
    lead -> member: cancel() as cx => cancelling
  state revised max_entries 5 => revision_limit
    lead -> member: revise() as req => revised
    lead -> member: cancel() as cx => cancelling
  state working @4h => stalled
    member -> lead: deliverable(request=$req) as dlv @1h => delivered | stalled
    member -> lead: question(request=$req) as q awaits asking
      => answer() & working
       | @1h & unanswered
    lead -> member: revise() as req awaits revised
      => ack() & working
       | @15m & unacknowledged
    lead -> member: cancel() as cx awaits cancelling
      => cancel_ack() & cancelled
       | @15m & cleanup_unconfirmed
  state asking max_entries 4 => question_limit
    lead -> member: revise() as req => revised
    lead -> member: cancel() as cx => cancelling
  state delivered @2h => unreviewed
    lead -> member: accept(deliverable=$dlv) => accepted
    lead -> member: revise() as req => revised
    lead -> member: cancel() as cx => cancelling
  anystate
    lead -> member: abandon() => abandoned
  """

  # A1b: one anystate cancel head replaces the per-state cancel head and its re-sends.
  @a1b """
  state new
    lead -> member: assign() as req awaits assigned
      => ack() & working
       | @15m & unacknowledged
  state assigned
    lead -> member: revise() as req => revised
  state revised max_entries 5 => revision_limit
    lead -> member: revise() as req => revised
  state working @4h => stalled
    member -> lead: deliverable(request=$req) as dlv @1h => delivered | stalled
    lead -> member: revise() as req awaits revised
      => ack() & working
       | @15m & unacknowledged
  state delivered @2h => unreviewed
    lead -> member: accept(deliverable=$dlv) => accepted
  anystate
    lead -> member: cancel() as cx awaits cancelling
      => cancel_ack() & cancelled
       | @15m & cleanup_unconfirmed
  """

  defp compile(states) do
    bytes = @header <> @types <> states <> @ends

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
      bindings: %{"lead" => "l", "member" => "m"},
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

  describe "A1 counter-requests" do
    test "room-work under A1 compiles and the engine loads it" do
      assert {:ok, c} = compile(@a1)
      assert {:ok, _} = start(c)
    end

    test "in a wait, the replier may leave by a re-send of another block or into an end terminal" do
      # In `asking` the lead owes the answer; `revise` re-sends `revised`, `abandon` ends.
      assert {:ok, c} = compile(@a1)
      assert {:ok, s} = start(c)
      assert {%{status: :accepted, state: "assigned"}, s} = send(s, "a1", "l", "assign", 1)

      assert {%{status: :accepted, state: "working"}, s} =
               send(s, "k1", "m", "ack", 2, %{"request" => "a1"})

      assert {%{status: :accepted, state: "asking"}, s} =
               send(s, "q1", "m", "question", 3, %{"request" => "a1"})

      assert {%{status: :accepted, state: "revised"}, s} = send(s, "r1", "l", "revise", 4)
      assert s.wait.head == "r1"
    end

    test "any other move of the replier out of a wait stays reply_route" do
      body =
        String.replace(@a1, "state asking max_entries 4 => question_limit\n", """
        state asking max_entries 4 => question_limit
          lead -> member: abandon() => working
        """)

      assert {:error, %{code: :reply_route}} = compile(body)
    end

    test "an anystate rule that applies in a wait obeys the same rule (the loophole is closed)" do
      body =
        String.replace(@a1, "  lead -> member: abandon() => abandoned\n", """
          lead -> member: abandon() => abandoned
          lead -> member: accept() => working
        """)

      assert {:error, %{code: :reply_route}} = compile(body)
    end

    test "an event in a wait still may not rebind its name" do
      body =
        String.replace(@a1, "state assigned\n", """
        state assigned
          event lead -> member: revise() as req
        """)

      assert {:error, %{code: :reply_correlation}} = compile(body)
    end
  end

  describe "A1b anystate heads" do
    test "an anystate head compiles, and its own wait needs no max_entries" do
      assert {:ok, c} = compile(@a1b)
      assert c.states["cancelling"].wait.heads != []
      assert {:ok, _} = start(c)
    end

    test "the head opens its wait from any state but does not apply in its own wait" do
      assert {:ok, c} = compile(@a1b)
      assert {:ok, s} = start(c)
      assert {%{status: :accepted, state: "assigned"}, s} = send(s, "a1", "l", "assign", 1)
      assert {%{status: :accepted, state: "cancelling"}, s} = send(s, "c1", "l", "cancel", 2)
      assert s.wait.head == "c1"
      assert {%{status: :rejected}, s2} = send(s, "c2", "l", "cancel", 3)
      assert s2.state == "cancelling" and s2.wait.head == "c1"

      assert {%{status: :accepted, state: "cancelled"}, _} =
               send(s, "x1", "m", "cancel_ack", 4, %{"cancel" => "c1"})
    end

    test "another anystate rule into the wait is still reply_route" do
      body = @a1b <> "  lead -> member: abandon() => cancelling\n"
      assert {:error, %{code: :reply_route}} = compile(body)
    end
  end

  # RULE.md §A.3 A1-2: a counter-request goes to W's requester (or the replier exits into an
  # end terminal); A1b-4: an anystate head is untimed.
  describe "A1-2 and A1b-4" do
    @third "---\nname: room-work-a1\ndescription: Room-work under A1.\nengine: 0.2.0-lab.1\nroles: [lead, member, auditor]\n---\n"
    @escalate "type escalate\ntype verdict\n  escalate: ref(escalate)\n"

    defp compile_third(states) do
      bytes = @third <> @types <> @escalate <> states <> @ends

      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })
    end

    @escalated "state escalated max_entries 2 => question_limit\n"

    test "the replier's counter-request to a third role is reply_route" do
      body =
        String.replace(@a1, "state asking max_entries 4 => question_limit\n", """
        state asking max_entries 4 => question_limit
          lead -> auditor: escalate() as esc awaits escalated
            => verdict() & working
             | @1h & unanswered
        """) <> @escalated

      assert {:error, %{code: :reply_route, line: 43}} = compile_third(body)
    end

    test "an anystate counter-request to a third role is reply_route in the wait it applies in" do
      body =
        @a1 <>
          """
            lead -> auditor: escalate() as esc awaits escalated
              => verdict() & working
               | @1h & unanswered
          """ <> @escalated

      assert {:error, %{code: :reply_route, line: 51}} = compile_third(body)
    end

    test "the same counter-request to the requester compiles" do
      body =
        String.replace(@a1, "state asking max_entries 4 => question_limit\n", """
        state asking max_entries 4 => question_limit
          lead -> member: escalate() as esc awaits escalated
            => verdict() & working
             | @1h & unanswered
        """) <> @escalated

      assert {:ok, _} = compile_third(body)
    end

    test "A1b-4: a timed anystate head is reply_route" do
      body =
        String.replace(
          @a1b,
          "  lead -> member: cancel() as cx awaits cancelling\n",
          "  lead -> member: cancel() as cx @5m awaits cancelling | stalled\n"
        )

      assert {:error, %{code: :reply_route, line: 34}} = compile(body)
    end
  end
end
