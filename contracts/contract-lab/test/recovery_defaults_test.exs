defmodule ContractLab.RecoveryDefaultsTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  @body """
  type question
  type answer
    question: ref(question)
  state new
    requester -> assistant: question() awaits answering
      => answer() & complete
       | @5m & stalled
  end complete
  end stalled
  """

  defp compile(extra \\ "", body \\ nil) do
    body = body || @body

    bytes =
      """
      ---
      name: recovery-defaults
      description: One agent asks another agent for an answer.
      engine: 0.2.0-lab.1
      roles: [requester, assistant]
      #{extra}---
      """ <> body

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp new(c, max_messages \\ 2),
    do:
      Engine.new(c,
        session_id: "s",
        bindings: %{"requester" => "r", "assistant" => "a"},
        now: 0,
        max_messages: max_messages,
        room_policy: :unchecked,
        capabilities: c.card.extensions
      )

  defp question(s, now),
    do:
      Engine.step(s, %{"id" => "q", "session" => "s", "kind" => "question", "payload" => %{}},
        actor: "r",
        now: now
      )

  test "the default card has no arbitrary lifetime, idle timeout or mention cap" do
    assert {:ok, c} = compile()
    assert c.card.lifetime == :infinite
    assert c.card.inactivity_timeout == :infinite
    refute Map.has_key?(c.card, :mentions)
    refute Map.has_key?(c, :mentions)
    assert {:ok, s} = new(c)
    assert {:ok, quiet} = Engine.tick(s, 86_400_000)
    assert quiet.status == :active

    assert {:ok, _} =
             StateCodec.restore(c, StateCodec.export(quiet), 86_400_001,
               capabilities: c.card.extensions
             )

    assert {%{status: :accepted}, working} = question(quiet, 86_400_001)
    assert {:ok, %{outcome: "stalled"}} = Engine.tick(working, 86_700_001)
  end

  test "new work is refused when its required answer cannot fit the remaining capacity" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c, 1)
    assert {%{status: :rejected, reason: :completion_capacity}, after_attempt} = question(s, 0)
    assert after_attempt.facts == %{}
    assert after_attempt.obligations == []
    assert after_attempt.wait == nil
    assert after_attempt.state == "new"
  end

  test "a maximum duration cannot silently shorten a newly accepted duty" do
    assert {:ok, c} = compile("", @body <> "contract @30m => expired\n")
    assert {:ok, s} = new(c)
    assert {%{status: :rejected, reason: :insufficient_time}, refused} = question(s, 1_790_000)
    assert refused.state == "new"
    assert {%{status: :accepted}, _} = question(s, 1_500_000)
  end

  test "the specific duty wins a tie with the overall cap" do
    assert {:ok, c} = compile("", @body <> "contract @5m => expired\n")
    assert {:ok, s} = new(c)
    assert {%{status: :accepted}, s} = question(s, 0)
    assert {:ok, %{outcome: "stalled"}} = Engine.tick(s, 300_000)
  end

  test "the new profile has no idle timer field" do
    for value <- ["5m", "infinite"] do
      assert {:error, %{code: :unknown_field, line: 6, detail: "inactivity-timeout"}} =
               compile("inactivity-timeout: #{value}\n")
    end
  end

  test "plus declarations were removed; ordinary declarations are the only spelling" do
    plain =
      "type question\nstate new\n requester -> assistant: question() => complete\nend complete\n"

    native =
      "+TYPE question\n+STATE new\n requester -> assistant: question() => complete\n+END complete\n"

    assert {:ok, _} = compile("", plain)
    assert {:error, %{code: :syntax, line: 1, detail: detail}} = compile("", native)
    assert detail =~ "Plus-prefixed declarations were removed"
  end

  test "unrelated progress cannot spend the last credit reserved for an answer" do
    body = """
    type question
    type answer
      question: ref(question)
    type note
    state new
      requester -> assistant: question() awaits answering
        => answer() & complete
         | @5m & stalled
    state answering
      event requester -> assistant: note()
    end complete
    end stalled
    """

    assert {:ok, c} = compile("", body)
    assert {:ok, s} = new(c)
    assert {%{status: :accepted}, s} = question(s, 0)
    note = %{"id" => "n", "session" => "s", "kind" => "note", "payload" => %{}}

    assert {%{status: :rejected, reason: :completion_capacity}, ^s} =
             Engine.step(s, note, actor: "r", now: 0)

    answer = %{
      "id" => "a",
      "session" => "s",
      "kind" => "answer",
      "payload" => %{"question" => "q"}
    }

    assert {%{status: :accepted, outcome: "complete"}, _} =
             Engine.step(s, answer, actor: "a", now: 1)
  end
end
