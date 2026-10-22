defmodule ContractLab.ClosingReserveTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @source """
  ---
  name: closing-reserve
  description: Events must leave capacity for the cheapest closing path.
  engine: 0.2.0-lab.1
  roles: [lead, a, b]
  ---
  contract @30m => expired
  type request
  type review
  type progress
  type summary
  state new
    lead -> {a,b}: request() => reviewing
  state reviewing
    {a,b} -> lead: review() each @5m => summarizing | stalled
    event {a,b} -> lead: progress()
  state summarizing
    lead -> {a,b}: summary() => complete
  end complete
  end stalled
  """

  defp start(max_messages) do
    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => @source,
               "sha256" => Canonical.hash(@source)
             })

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: max_messages,
               bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
               room_policy: :unchecked
             )

    {%{status: :accepted}, s} = send(s, "l", "r", "request")
    s
  end

  defp send(s, actor, id, kind),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => %{}},
        actor: actor,
        now: s.now + 1
      )

  test "progress events cannot spend the untimed closing move after the reserved reviews" do
    # Five messages: the request, two reviews, the summary and one progress update.
    s = start(5)
    assert {%{status: :accepted}, s} = send(s, "a", "p1", "progress")

    assert {%{status: :rejected, reason: :completion_capacity}, same} =
             send(s, "b", "p2", "progress")

    assert same.version == s.version

    assert {%{status: :accepted}, s} = send(s, "a", "ra", "review")
    assert {%{status: :accepted}, s} = send(s, "b", "rb", "review")
    assert s.state == "summarizing"
    assert {%{status: :accepted}, s} = send(s, "l", "sum", "summary")
    assert s.outcome == "complete"
  end

  test "a closing move that is already a reserved duty is not reserved twice" do
    source =
      @source
      |> String.replace(
        "{a,b} -> lead: review() each @5m => summarizing | stalled",
        "{a,b} -> lead: review() each @5m => complete | stalled"
      )
      |> String.replace("state summarizing\n  lead -> {a,b}: summary() => complete\n", "")

    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => source,
               "sha256" => Canonical.hash(source)
             })

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: 4,
               bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
               room_policy: :unchecked
             )

    {%{status: :accepted}, s} = send(s, "l", "r", "request")
    # Request, progress and the two reviews fill the budget exactly.
    assert {%{status: :accepted}, s} = send(s, "a", "p1", "progress")
    assert {%{status: :accepted}, s} = send(s, "a", "ra", "review")
    assert {%{status: :accepted}, s} = send(s, "b", "rb", "review")
    assert s.outcome == "complete"
  end
end
