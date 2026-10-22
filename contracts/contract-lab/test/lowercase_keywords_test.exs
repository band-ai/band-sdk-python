defmodule ContractLab.LowercaseKeywordsTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  defp compile(body, roles \\ "lead, a, b", extra \\ "") do
    bytes = """
    ---
    name: lowercase
    description: Readable contract keywords.
    engine: 0.2.0-lab.1
    roles: [#{roles}]
    #{extra}---
    #{body}
    """

    source(bytes)
  end

  defp source(bytes) do
    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  # D-6: keywords and options have one spelling. Each uppercase swap fails.
  defp lowercase_only(body, swaps, roles \\ "lead, a, b", extra \\ "") do
    assert {:ok, c} = compile(body, roles, extra)

    for {lower, upper} <- swaps do
      assert {:error, %{code: :syntax, detail: detail}} =
               compile(String.replace(body, lower, upper), roles, extra),
             upper

      assert detail =~ "lowercase", upper
    end

    c
  end

  test "as and event are lowercase only; activity was removed" do
    body = """
    type request
    type review
      source: ref(request)
    type progress
    state new
      lead -> a: request() as task awaits reviewing
        => review(source=$task) & collected
         | @5m & stalled
    state reviewing
      ~s|AS EACH EVENT stay literal in documentation.|
      event a -> lead: progress()
    end collected
    end stalled
    """

    c = lowercase_only(body, [{" as task", " AS task"}, {"event a", "EVENT a"}])

    assert Enum.find(c.states["reviewing"].rules, & &1.event).meta.doc ==
             "AS EACH EVENT stay literal in documentation."

    assert hd(c.states["new"].rules).bind == "task"

    for activity <- [" activity", " ACTIVITY"] do
      assert {:error, %{code: :syntax, line: 11}} =
               compile(String.replace(body, "progress()\n", "progress()" <> activity <> "\n"))
    end
  end

  test "each with an inline deadline waits for distinct contributors" do
    body = """
    type request
    type review
    state new
      lead -> {a,b}: request() => reviewing
    state reviewing
      {a,b} -> lead: review() each @5m => collected | stalled
    end collected
    end stalled
    """

    c = lowercase_only(body, [{" each ", " EACH "}])

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: 3,
               bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
               room_policy: :unchecked
             )

    send = fn s, id, actor, kind, now ->
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => %{}},
        actor: actor,
        now: now
      )
    end

    assert {%{status: :accepted}, s} = send.(s, "q", "l", "request", 0)
    assert {%{status: :accepted, state: "reviewing"}, s} = send.(s, "a1", "a", "review", 1)
    assert {%{status: :duplicate}, s} = send.(s, "a1", "a", "review", 2)
    assert {%{status: :accepted, outcome: "collected"}, _} = send.(s, "b1", "b", "review", 3)
  end

  test "due was removed; a timed event keeps the single, all and any cases" do
    body = &"type review\nstate new\n  #{&1}\nend stalled\n"

    for {due, event, quantifier, participants} <- [
          {"due a with review in 5m => stalled", "event a -> lead: review() @5m => stalled", :all,
           ["a"]},
          {"due all of {a,b} with review in 5m => stalled",
           "event {a,b} -> lead: review() each @5m => stalled", :all, ["a", "b"]},
          {"due any of ~w[a b] with review in 5m => stalled",
           "event ~w[a b] -> lead: review() @5m => stalled", :any, ["a", "b"]}
        ] do
      assert {:error, %{code: :syntax, line: 3, detail: detail}} = compile(body.(due))
      assert detail =~ "@duration"
      assert {:error, %{code: :syntax, line: 3}} = compile(body.(String.upcase(due)))
      assert {:ok, c} = compile(body.(event))

      assert [
               %{
                 quantifier: ^quantifier,
                 participants: ^participants,
                 kind: "review",
                 after_ms: 300_000,
                 outcome: "stalled"
               }
             ] = c.states["new"].deadlines
    end
  end

  test "max_entries works with state timeouts" do
    body = """
    type request
    state new
      lead -> a: request() => working
    state working @5m => stalled max_entries 3 => exhausted
      lead -> a: request() => working
    end stalled
    end exhausted
    """

    lowercase_only(body, [{"max_entries", "MAX_ENTRIES"}])
  end

  test "host observation keywords are lowercase with unchanged normalized triggers" do
    body = """
    watch supervisor
    type request
    state new
      lead -> a: request() => working
    state working
      on a offline for 5m => paused
      on watch.supervisor offline => paused
    state paused
      on a online => working
    anystate
      on a removed => left
    end left
    """

    c =
      lowercase_only(
        body,
        [
          {"on ", "ON "},
          {" offline", " OFFLINE"},
          {" online", " ONLINE"},
          {" removed", " REMOVED"},
          {" for ", " FOR "}
        ],
        "lead, a, b",
        "extensions: [host.presence/1, host.membership/1]\n"
      )

    assert Enum.map(c.extensions.triggers, & &1.value) == [
             "offline",
             "offline",
             "online",
             "removed"
           ]
  end

  test "keyword-shaped roles, fields, bindings and literal values are not rewritten" do
    body = """
    type request
      label: enum(as|each|event|due|on)
      code: text(20, regex: ~r/AS|EACH/)
    type review
      each: ref(request)
    state new
      event -> due: request() as each awaits working
        => review(each=$each) & collected
         | @5m & stalled
    end collected
    end stalled
    """

    c = lowercase_only(body, [{" as each ", " AS each "}], "event, due")
    assert c.types["request"].fields["label"].values == ~w(as each event due on)
    assert hd(c.states["new"].rules).from == ["event"]
    assert hd(c.states["working"].rules).matches == %{"each" => "each"}
  end

  test "duplicate and mixed-case modifiers remain errors" do
    for options <- ["each EACH", "as task AS other", "Each", "As task"] do
      assert {:error, %{code: :syntax}} =
               compile("""
               type review
               state new
                 a -> lead: review() #{options} => complete
               end complete
               """)
    end
  end
end
