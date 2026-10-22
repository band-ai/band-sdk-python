defmodule ContractLab.ReplyRequiredTest do
  # ubf-replies/old-form/RULE.md §1 (op-ns6bjbpq): the old send-then-answer form is the compile
  # error `reply_required`, at the request's line, with the answer's line and the reply head
  # to write instead. It runs after the reply_* checks and before contract_clock_required.
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @header "---\nname: old-form\ndescription: Old form probe.\nengine: 0.2.0-lab.1\nroles: [lead, reviewer, other]\n---\n"

  defp compile(body) do
    bytes = @header <> body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp contract(states, ends \\ "end complete\nend stalled\n") do
    "contract @1h => expired\ntype request\ntype review\ntype ack\ntype cancel\ntype answer\n  request: ref(request)\n" <>
      states <> ends
  end

  test "an untimed request answered by the recipient is reply_required at the request" do
    body =
      contract("""
      state new
        lead -> reviewer: request() => reviewing
      state reviewing
        reviewer -> lead: review() => complete
      """)

    assert {:error, %{code: :reply_required, line: 9, detail: detail}} = compile(body)
    assert detail =~ "line 11"

    assert detail =~
             "lead -> reviewer: request() awaits reviewing => review() & complete | @d & T"
  end

  test "a timed answer to a timed request is reply_required" do
    body =
      contract("""
      state new
        lead -> reviewer: request() @5m => reviewing | stalled
      state reviewing
        reviewer -> lead: review() @5m => complete | stalled
      """)

    assert {:error, %{code: :reply_required, line: 9}} = compile(body)
  end

  test "a timed request whose answers back are all untimed is an owed report (exempt)" do
    body =
      contract("""
      state new
        lead -> reviewer: request() => asked
      state asked
        reviewer -> lead: ack() @15m => working | stalled
      state working
        lead -> reviewer: cancel() => complete
      """)

    assert {:error, %{code: :reply_required, line: 9}} = compile(body)

    owed =
      contract("""
      state new
        reviewer -> lead: ack() @15m => working | stalled
      state working
        lead -> reviewer: cancel() => complete
      """)

    assert {:ok, _} = compile(owed)
  end

  test "a one-way send, a group send and an answer in anystate compile" do
    one_way =
      contract("""
      state new
        lead -> reviewer: request() => reviewing
      state reviewing
        lead -> reviewer: cancel() => complete
      """)

    group =
      contract("""
      state new
        lead -> {reviewer,other}: request() => reviewing
      state reviewing
        reviewer -> lead: review() => complete
      """)

    anystate =
      contract("""
      state new
        lead -> reviewer: request() => reviewing
      state reviewing
        lead -> reviewer: cancel() => complete
      anystate
        reviewer -> lead: review() => complete
      """)

    assert {:ok, _} = compile(one_way)
    assert {:ok, _} = compile(group)
    assert {:ok, _} = compile(anystate)
  end

  test "a roster recipient is a group send" do
    bytes =
      "---\nname: roster\ndescription: Roster probe.\nengine: 0.2.0-lab.1\nroles: [lead, reviewers]\n---\n" <>
        "contract @1h => expired\ntype reviewer: agent\nreviewers: list(reviewer)\ntype request\ntype review\n" <>
        "state new\n  lead -> reviewers: request() => reviewing\nstate reviewing\n  reviewers -> lead: review() => complete\nend complete\n"

    assert {:ok, _} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => bytes,
               "sha256" => Canonical.hash(bytes)
             })
  end

  test "an event is no answer, and a send into a terminal is no request" do
    body =
      contract("""
      state new
        lead -> reviewer: request() => reviewing
        lead -> reviewer: cancel() => complete
      state reviewing
        event reviewer -> lead: review() @5m => stalled
        lead -> reviewer: cancel() => complete
      state other_side
        reviewer -> lead: ack() => complete
      """)

    assert {:ok, _} = compile(body)
  end

  test "a request in anystate is caught" do
    body =
      contract("""
      state new
        lead -> reviewer: request() => complete
      anystate
        lead -> reviewer: cancel() => cancelling
      state cancelling
        reviewer -> lead: ack() => complete
      """)

    assert {:error, %{code: :reply_required, line: 11, detail: detail}} = compile(body)
    assert detail =~ "line 13"
  end

  test "the earliest request is reported when several qualify (ping-pong)" do
    body =
      contract("""
      state new
        lead -> reviewer: request() => answering
      state answering
        reviewer -> lead: review() @5m => answered | stalled
      state answered
        lead -> reviewer: request() => answering
        lead -> reviewer: cancel() => complete
      """)

    assert {:error, %{code: :reply_required, line: 9, detail: detail}} = compile(body)
    assert detail =~ "line 11"
  end

  test "the reply form with a re-send compiles" do
    body =
      contract("""
      state new
        lead -> reviewer: request() awaits reviewing
          => answer() & answered
           | @5m & stalled
      state answered
        lead -> reviewer: request() => reviewing
        lead -> reviewer: cancel() => complete
      state reviewing max_entries 3 => stalled
      """)

    assert {:ok, _} = compile(body)
  end

  test "reply_* errors come first and contract_clock_required comes after" do
    reply_route =
      contract("""
      state new
        lead -> reviewer: request() awaits waiting
          => answer() & complete
           | @5m & stalled
        lead -> reviewer: request() => reviewing
      state reviewing
        reviewer -> lead: review() => complete
      anystate
        reviewer -> lead: ack() => waiting
      """)

    assert {:error, %{code: :reply_route}} = compile(reply_route)

    no_clock =
      String.replace_prefix(
        contract("""
        state new
          lead -> reviewer: request() => reviewing
        state reviewing
          reviewer -> lead: review() => complete
        """),
        "contract @1h => expired\n",
        ""
      )

    assert {:error, %{code: :reply_required, line: 8}} = compile(no_clock)
  end
end
