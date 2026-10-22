defmodule ContractLab.SingleDueTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  # D-4 removed `due assistant with answer in 5m`; a single owed answer is
  # written as the reply to the question that asks for it.
  @owed "  => answer() & complete\n     | @5m & stalled"

  defp compile(reply, rule \\ "") do
    bytes = """
    ---
    name: single-due
    description: One answer is owed.
    engine: 0.2.0-lab.1
    roles: [requester, assistant]
    ---
    type question
    type answer
      question: ref(question)
    state new
      requester -> assistant: question() awaits answering
    #{reply}
    state answering
      #{rule}
    end complete
    end stalled
    end expired
    """

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp waiting(c) do
    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        bindings: %{"requester" => "r", "assistant" => "a"},
        now: 0,
        inactivity: :accepted,
        max_messages: 2,
        room_policy: :unchecked,
        capabilities: c.card.extensions
      )

    assert {%{status: :accepted}, s} =
             Engine.step(
               s,
               %{"id" => "q", "session" => "s", "kind" => "question", "payload" => %{}},
               actor: "r",
               now: 1000
             )

    s
  end

  test "one participant owes the answer on its reply; the due form names it" do
    assert {:ok, simple} = compile(@owed)

    assert %{
             from: ["assistant"],
             to: ["requester"],
             kinds: ["answer"],
             after_ms: 300_000,
             outcome: "stalled"
           } = simple.states["answering"].wait

    assert simple.states["answering"].deadlines == []
    assert {:ok, role} = ContractLab.for_role(simple, "assistant")
    assert Map.delete(role.states["answering"].wait, :exits) == simple.states["answering"].wait

    for due <- [
          "due assistant with answer in 5m => stalled",
          "due all of assistant with answer in 5m => stalled"
        ] do
      assert {:error, %{code: :syntax, line: 9, detail: detail}} = compile(@owed, due)
      assert detail =~ "@duration"
    end
  end

  test "an accepted answer before the deadline completes the session" do
    assert {:ok, c} = compile(@owed)
    s = waiting(c)

    answer = %{
      "id" => "a",
      "session" => "s",
      "kind" => "answer",
      "payload" => %{"question" => "q"}
    }

    assert {%{status: :accepted, outcome: "complete"}, _} =
             Engine.step(s, answer, actor: "a", now: 300_999)

    assert {%{status: :rejected, reason: :terminal}, %{outcome: "stalled"}} =
             Engine.step(s, answer, actor: "a", now: 301_000)
  end

  test "restart retains the original deadline and missed work stalls without heartbeats" do
    assert {:ok, c} = compile(@owed)
    s = waiting(c)

    assert {:ok, restored} =
             StateCodec.restore(c, StateCodec.export(s), 200_000, capabilities: c.card.extensions)

    assert {:ok, %{outcome: "stalled"}} = Engine.tick(restored, 301_000)
  end

  test "the owing sender must be a declared role" do
    assert {:error, %{code: :unknown_role}} =
             compile(@owed, "missing -> requester: answer() @5m => complete | stalled")
  end
end
