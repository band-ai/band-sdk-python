defmodule ContractLab.OldFormsTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  # Body line 1 is the contract clock, so the first body line below is line 2.
  defp compile(body, extra \\ "", roles \\ "lead, a, b", format \\ "frontmatter") do
    bytes =
      "---\nname: old-forms\ndescription: One spelling per construct.\nengine: 0.2.0-lab.1\nroles: [#{roles}]\n#{extra}---\ncontract @1h => expired\n" <>
        body

    source(bytes, format)
  end

  defp source(bytes, format),
    do:
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => format,
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

  # Lines 2-7; the line under test is line 8, inside `state reviewing`.
  @head """
  type request
  type review
  type progress
  state new
    lead -> {a,b}: request() => reviewing
  state reviewing
  """
  @tail "\n  {a,b} -> lead: review() => collected\nend collected\nend stalled\nend paused\n"

  test "each removed rule-level form fails with syntax at its line and names the new form" do
    for {line, hint} <- [
          {"  due all of {a,b} with review in 5m => stalled", "@5m"},
          {"  due any of {a,b} with review in 5m => stalled", "@5m"},
          {"  due a with review in 5m => stalled", "@5m"},
          {"  DUE all OF {a,b} WITH review IN 5m => stalled", "@5m"},
          {"  EVENT a -> lead: progress()", "lowercase"},
          {"  ON a OFFLINE FOR 5m => paused", "lowercase"},
          {"  event a -> lead: progress() activity", "idle timer"},
          {"  event a -> lead: progress() ACTIVITY", "idle timer"},
          {"  {a,b} -> lead: review() EACH => collected", "lowercase"},
          {"  {a,b} -> lead: review() AS r => collected", "lowercase"},
          {"  ~W[a b] -> lead: review() => collected", "~w"}
        ] do
      assert {:error, %{code: :syntax, line: 8, detail: detail}} =
               compile(@head <> line <> @tail),
             line

      assert detail =~ hint, line
    end
  end

  test "each removed declaration form fails with syntax and names the new form" do
    for {body, line, hint} <- [
          {"+TYPE request\n", 2, "type"},
          {"type request\n+STATE new\n", 3, "state"},
          {"type request\nstate new MAX_ENTRIES 2 => stalled\n", 3, "max_entries"},
          {"role reviewers: list(reviewer)\n", 2, "reviewers: list(reviewer)"},
          {"reviewers: list(agent)\n", 2, "type reviewer: agent"},
          {"~S|Raw guidance.|\ntype request\n", 2, "~s"},
          {"type request\n  code: text(8, regex: ~R/[A-Z]+/)\n", 3, "~r"},
          {"type request\n  code: text(8, regex: \"[A-Z]+\")\n", 3, "~r/"},
          {"type request\n  pick: enum(~W[x y])\n", 3, "~w"}
        ] do
      assert {:error, %{code: :syntax, line: ^line, detail: detail}} = compile(body), body
      assert detail =~ hint, body
    end
  end

  test "collect failure type() was removed" do
    body =
      @head <>
        "  collect reports from {a,b} -> lead @5m => collected\n    reply review()\n    failure review()" <>
        @tail

    assert {:error, %{code: :syntax, line: 8, detail: detail}} = compile(body)
    assert detail =~ "contribution"
  end

  test "the inactivity-timeout field and the native header are removed" do
    body = @head <> "  {a,b} -> lead: review() => collected\nend collected\n"

    for value <- ["infinite", "5m"] do
      assert {:error, %{code: :unknown_field, line: 6, detail: "inactivity-timeout"}} =
               compile(body, "inactivity-timeout: #{value}\n")
    end

    native =
      "+SKILL old-forms\n+SUMMARY One spelling per construct.\n+ENGINE 0.2.0-lab.1\n+ROLES lead, a, b\n+BODY\ncontract @1h => expired\n" <>
        body

    assert {:error, :frontmatter_required} = source(native, "native")
  end

  @timed """
  type request
  type review
  type progress
  state new
    lead -> {a,b}: request() => reviewing
  state reviewing
    event a -> lead: progress() @5m => stalled
    {a,b} -> lead: review() => collected
  end collected
  end stalled
  """

  defp start(c),
    do:
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 10,
        bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
        room_policy: :unchecked
      )

  defp send(s, actor, kind, now),
    do:
      Engine.step(
        s,
        %{"id" => actor <> kind <> "#{now}", "session" => "s", "kind" => kind, "payload" => %{}},
        actor: actor,
        now: now
      )

  test "an event rule may carry @duration: a duty the event discharges, with no transition" do
    assert {:ok, c} = compile(@timed)
    assert [rule, _] = c.states["reviewing"].rules
    assert %{event: true, target: nil, kind: "progress"} = rule

    assert [%{participants: ["a"], quantifier: :all, after_ms: 300_000, outcome: "stalled"}] =
             c.states["reviewing"].deadlines

    assert {:ok, s} = start(c)
    assert {%{status: :accepted}, s} = send(s, "l", "request", 0)
    assert {:ok, %{outcome: "stalled"}} = Engine.tick(s, 300_000)

    assert {%{status: :accepted, state: "reviewing"}, s} = send(s, "a", "progress", 1_000)
    assert {:ok, %{status: :active, state: "reviewing", outcome: nil}} = Engine.tick(s, 300_000)
  end

  test "a timed group event is owed by any one sender" do
    assert {:ok, c} =
             compile(
               String.replace(
                 @timed,
                 "event a -> lead: progress() @5m",
                 "event {a,b} -> lead: progress() @5m"
               )
             )

    assert [%{participants: ["a", "b"], quantifier: :any}] = c.states["reviewing"].deadlines
  end

  test "each on a timed group event owes it from every sender; the event may repeat" do
    each = "event {a,b} -> lead: progress() each @5m"

    assert {:ok, c} =
             compile(String.replace(@timed, "event a -> lead: progress() @5m", each))

    assert [%{participants: ["a", "b"], quantifier: :all}] = c.states["reviewing"].deadlines
    assert %{event: true, each: false} = hd(c.states["reviewing"].rules)

    assert {:ok, s} = start(c)
    assert {%{status: :accepted}, s} = send(s, "l", "request", 0)
    assert {%{status: :accepted}, s} = send(s, "a", "progress", 1_000)
    assert {%{status: :accepted}, s} = send(s, "a", "progress", 2_000)
    assert {:ok, %{outcome: "stalled"}} = Engine.tick(s, 300_000)
    assert {%{status: :accepted}, s} = send(s, "b", "progress", 3_000)
    assert {:ok, %{status: :active, outcome: nil}} = Engine.tick(s, 300_000)

    assert {:error, %{code: :syntax, line: 8}} =
             compile(
               String.replace(
                 @timed,
                 "event a -> lead: progress() @5m => stalled",
                 "event {a,b} -> lead: progress() each"
               )
             )
  end

  # Gate follow-ups (Priya N1–N3 on 85b2178).

  test "N1: uppercase sigil text inside a lowercase sigil is data, not an opener" do
    for field <- ["regex: ~r/~S[0-9]+/", "regex: ~r/a ~W[b]/", "regex: ~r/x~R|y/"] do
      body =
        "type request\n  code: text(8, #{field})\nstate new\n  lead -> a: request() => done\nend done\n"

      assert {:ok, _} = compile(body), field
    end

    # Anchors are not pattern syntax (matching is whole-string); the sigil check no longer fires.
    assert {:error, %{code: :pattern_syntax, line: 3}} =
             compile("type request\n  code: text(8, regex: ~r/^~S[0-9]+$/)\n")

    for {field, hint} <- [{"code: text(8, regex: ~R/x/)", "~r"}, {"pick: enum(~W[x y])", "~w"}] do
      assert {:error, %{code: :syntax, line: 3, detail: detail}} =
               compile("type request\n  #{field}\n"),
             field

      assert detail =~ hint, field
    end
  end

  test "N2: each may appear anywhere among a timed event's rule options" do
    for options <- ["each mention 1", "mention 1 each", "as p each", "each as p"] do
      line = "event {a,b} -> lead: progress() #{options} @5m => stalled"

      assert {:ok, c} =
               compile(String.replace(@timed, "event a -> lead: progress() @5m => stalled", line)),
             options

      assert [%{participants: ["a", "b"], quantifier: :all}] = c.states["reviewing"].deadlines
      assert %{event: true, each: false} = hd(c.states["reviewing"].rules)
    end

    for options <- ["each each", "each mention 1 each"] do
      line = "event {a,b} -> lead: progress() #{options} @5m => stalled"

      assert {:error, %{code: :syntax, line: 8}} =
               compile(String.replace(@timed, "event a -> lead: progress() @5m => stalled", line)),
             options
    end
  end

  test "N3: case variants of keywords and options name the lowercase form" do
    collect = "  collect reports from {a,b} -> lead @5m => collected\n"

    for {line, at, hint} <- [
          {collect <> "    REPLY review()", 9, "write reply"},
          {collect <> "    reply review()\n    OFFLINE @30s", 10, "write offline"},
          {collect <> "    reply review()\n    FAILURE review()", 10, "contribution"},
          {"  Due a with review in 5m => stalled", 8, "@5m"},
          {"  Event a -> lead: progress()", 8, "write event"},
          {"  On a offline for 5m => paused", 8, "write on"}
        ] do
      assert {:error, %{code: :syntax, line: ^at, detail: detail}} =
               compile(@head <> line <> @tail, "extensions: [host.presence/1]\n"),
             line

      assert detail =~ hint, line
    end

    for {body, at, hint} <- [
          {"TYPE request\n", 2, "write type"},
          {"Type request\n", 2, "write type"},
          {"type request\nSTATE new\n", 3, "write state"},
          {"type request\nstate new\n  lead -> a: request() => done\nEND done\n", 5, "write end"},
          {"type request\nANYSTATE\n", 3, "write anystate"},
          {"type request\nROOM\n", 3, "write room"},
          {"WATCH supervisor\n", 2, "write watch"}
        ] do
      assert {:error, %{code: :syntax, line: ^at, detail: detail}} = compile(body), body
      assert detail =~ hint, body
    end

    for {clock, hint} <- [
          {"CONTRACT @1h => expired", "write contract"},
          {"contract @80m MAX @4h => expired", "write max"},
          {"contract @1h MENTIONS 2 => expired", "write mentions"},
          {"contract @1h MENTION 1 => expired", "write mention"}
        ] do
      bytes =
        "---\nname: old-forms\ndescription: One spelling per construct.\nengine: 0.2.0-lab.1\nroles: [lead, a, b]\n---\n#{clock}\ntype request\nstate new\n  lead -> a: request() => done\nend done\n"

      assert {:error, %{code: :syntax, line: 1, detail: detail}} = source(bytes, "frontmatter"),
             clock

      assert detail =~ hint, clock
    end
  end

  test "on … offline for and max_entries are kept" do
    body = """
    watch supervisor
    type request
    type review
    state new
      lead -> {a,b}: request() => reviewing
    state reviewing max_entries 2 => stalled
      on a offline for 5m => stalled
      {a,b} -> lead: review() => collected
    end collected
    end stalled
    """

    assert {:ok, c} = compile(body, "extensions: [host.presence/1]\n")
    assert c.states["reviewing"].max_entries == %{limit: 2, outcome: "stalled"}
  end
end
