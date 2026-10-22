defmodule ContractLab.EntryStateTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  defp compile(body, engine \\ "0.2.0-lab.1")

  defp compile(body, "0.2.0-lab.1") do
    bytes =
      ContractLab.TestClock.ensure("""
      ---
      name: entry-only
      description: Start once, finish once.
      engine: 0.2.0-lab.1
      roles: [requester, assistant]
      ---
      type question
      type answer
        question: ref(question)
      #{body}
      end complete
      end stalled
      end expired
      """)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  test "new is forbidden as any transition target, including self and each" do
    for body <- [
          "state new\n requester -> assistant: question() => new",
          # A reply alternative into new; the cycle through new needs max_entries to reach the check.
          "state new\n requester -> assistant: question() awaits answering\n  => answer() & new\n   | @1m & stalled\nstate answering max_entries 1 => stalled",
          # A one-way anystate restart into new.
          "type restart\nstate new\n requester -> assistant: question() => answering\nstate answering\nanystate\n requester -> assistant: restart() => new",
          "state new\n {requester,assistant} -> requester: question() each => new"
        ] do
      assert {:error, %{code: :initial_state_target}} = compile(body), body
    end
  end

  test "deadline and entry-limit outcomes cannot reopen new" do
    for body <- [
          "state new\n requester -> assistant: question() @1m => complete | new",
          "state new max_entries 1 => new\n requester -> assistant: question() => complete"
        ] do
      assert {:error, %{code: :initial_state_target}} = compile(body), body
    end
  end

  test "one request and one answer finish; another request needs another session" do
    body =
      "state new\n requester -> assistant: question() awaits answering\n  => answer() & complete\n   | @5m & stalled"

    assert {:ok, c} = compile(body)

    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        bindings: %{"requester" => "r", "assistant" => "a"},
        now: 0,
        inactivity: :accepted,
        max_messages: 10,
        room_policy: :unchecked,
        capabilities: c.card.extensions
      )

    question = %{"id" => "q", "session" => "s", "kind" => "question", "payload" => %{}}

    answer = %{
      "id" => "a",
      "session" => "s",
      "kind" => "answer",
      "payload" => %{"question" => "q"}
    }

    assert {%{status: :accepted}, s} = Engine.step(s, question, actor: "r", now: 0)

    assert {%{status: :accepted, outcome: "complete"}, s} =
             Engine.step(s, answer, actor: "a", now: 1)

    assert {%{status: :rejected, reason: :terminal}, ^s} =
             Engine.step(s, %{question | "id" => "q2"}, actor: "r", now: 1)

    assert {:ok, %{state: "new"}} =
             Engine.new(c,
               session_id: "next",
               bindings: %{"requester" => "r", "assistant" => "a"},
               now: 2,
               inactivity: :accepted,
               max_messages: 10,
               room_policy: :unchecked,
               capabilities: c.card.extensions
             )
  end
end
