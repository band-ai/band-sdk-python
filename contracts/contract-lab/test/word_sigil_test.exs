defmodule ContractLab.WordSigilTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @body ~S'''
  type request
    text: text(100)
  type revision
    text: text(100)
  type review
    work: ref(~w[request revision])
    verdict: enum(~w[approve revise reject])
  state new
    lead -> ~w[a b]: request() as work => reviewing
  state reviewing
    ~w[a b] -> lead: review(work=$work) each @5m => collected | stalled
  end collected
  end stalled
  '''

  test "name lists preserve enum and reference order and match existing behavior" do
    legacy =
      @body
      |> String.replace("~w[request revision]", "request|revision")
      |> String.replace("~w[approve revise reject]", "approve|revise|reject")
      |> String.replace("~w[a b]", "{a,b}")

    assert {:ok, c} = compile(@body)
    assert {:ok, old} = compile(legacy)
    assert c.digest == old.digest
    refute c.source_digest == old.source_digest
    assert c.types == old.types
    assert c.states == old.states
    assert c.types["review"].fields["verdict"].values == ["approve", "revise", "reject"]
    assert Engine.validate_payload(c.types["review"], %{"work" => "q", "verdict" => "approve"})
    refute Engine.validate_payload(c.types["review"], %{"work" => "q", "verdict" => "APPROVE"})
  end

  test "single-line delimiters and ASCII whitespace have the same name-list meaning" do
    for {open, close} <- [
          {"/", "/"},
          {"|", "|"},
          {"{", "}"},
          {"[", "]"},
          {"(", ")"},
          {"<", ">"},
          {"\"", "\""},
          {"'", "'"}
        ] do
      literal = "~w" <> open <> " a\t  b " <> close
      body = String.replace(@body, "~w[a b]", literal)
      assert {:ok, c} = compile(body), literal
      assert hd(c.states["new"].rules).to == ["a", "b"]
      assert hd(c.states["reviewing"].rules).from == ["a", "b"]
      assert hd(c.states["reviewing"].deadlines).participants == ["a", "b"]

      body =
        String.replace(
          @body,
          "~w[approve revise reject]",
          "~w" <> open <> " approve\t revise reject " <> close
        )

      assert {:ok, c} = compile(body), literal
      assert c.types["review"].fields["verdict"].values == ["approve", "revise", "reject"]
    end
  end

  test "group replies still require distinct reviewers and valid references" do
    assert {:ok, c} = compile(@body)
    s = start(c)
    assert {decision, s} = send(s, "lead", "q", "request", %{"text" => "Review"}, 0)
    assert decision.status == :accepted
    assert s.state == "reviewing"

    assert {bad, same} =
             send(s, "a", "bad", "review", %{"work" => "wrong", "verdict" => "approve"}, 1)

    assert bad.status == :rejected
    assert same == %{s | now: 1}

    assert {first, s} =
             send(s, "a", "a1", "review", %{"work" => "q", "verdict" => "approve"}, 60_000)

    assert first.status == :accepted
    assert s.state == "reviewing"

    assert {duplicate, s} =
             send(s, "a", "a1", "review", %{"work" => "q", "verdict" => "approve"}, 60_001)

    assert duplicate.status == :duplicate

    assert {second, s} =
             send(s, "b", "b1", "review", %{"work" => "q", "verdict" => "revise"}, 180_000)

    assert second.status == :accepted
    assert s.outcome == "collected"
  end

  test "word-list duties keep all with each, any without, and the exact deadline" do
    for {each, expected} <- [{" each", "stalled"}, {"", "collected"}] do
      assert {:ok, c} = compile(String.replace(@body, " each @5m", each <> " @5m"))
      {_, s} = send(start(c), "lead", "q", "request", %{"text" => "Review"}, 0)
      {_, s} = send(s, "a", "a1", "review", %{"work" => "q", "verdict" => "approve"}, 60_000)
      assert {:ok, s} = Engine.tick(s, 300_000)
      assert s.outcome == expected
    end

    assert {:ok, c} = compile(@body)
    {_, s} = send(start(c), "lead", "q", "request", %{"text" => "Review"}, 0)

    {decision, s} =
      send(s, "a", "a1", "review", %{"work" => "q", "verdict" => "approve"}, 300_000)

    assert decision.status == :rejected
    assert s.outcome == "stalled"
  end

  test "word lists also select event senders and recipients in optional scopes" do
    body =
      @body <>
        "anystate\n  event ~w[a b] -> ~w[lead]: request()\nroom\n  event ~w[a b] -> ~w[lead]: request()\n"

    assert {:ok, c} = compile(body)
    assert hd(c.any_rules).from == ["a", "b"]
    assert hd(c.room_rules).to == ["lead"]
    assert {:ok, view} = ContractLab.for_role(c, "a")
    assert hd(view.any_rules).from == ["a", "b"]
  end

  test "a word selector may spell any declared header role, including hyphens" do
    body =
      "type request\n  text: text(100)\nstate new\n  ~w[review-lead] -> ~w[reviewer]: request() => complete\nend complete\n"

    assert {:ok, c} = compile(body, "0.2.0-lab.1", "review-lead, reviewer")
    assert hd(c.states["new"].rules).from == ["review-lead"]
  end

  test "invalid names, empty lists, duplicates, modifiers and unknown types are rejected" do
    for literal <- [
          "~w[]",
          "~w[approve approve]",
          "~w[approve,reject]",
          "~w[approve|reject]",
          "~w[approve-reject]",
          "~w[Approve]",
          ~S(~w[#{call}]),
          "~w[approve]a",
          "~w[approve] trailing",
          "~w[approve",
          ~S(~w[approve\nreject]),
          "~w[approve\nreject]"
        ] do
      assert {:error, %{code: :type}} =
               compile(String.replace(@body, "~w[approve revise reject]", literal)),
             literal
    end

    assert {:error, %{code: :unknown_type}} =
             compile(String.replace(@body, "~w[request revision]", "~w[request missing]"))

    assert {:error, %{code: :type}} =
             compile(String.replace(@body, "~w[request revision]", "~w[request request]"))
  end

  test "selectors do not create roles or hide duplicates" do
    for {literal, code} <- [
          {"~w[]", :syntax},
          {"~w[a a]", :duplicate},
          {"~w[a stranger]", :unknown_role},
          {"~w[a b]a", :syntax},
          {"{a,b} extra", :syntax},
          {"a}", :syntax}
        ] do
      assert {:error, %{code: ^code}} =
               compile(String.replace(@body, "lead -> ~w[a b]", "lead -> " <> literal)),
             literal
    end
  end

  test "word names are strings and never interned atoms" do
    name = "untrusted_sigil_word_" <> Integer.to_string(System.unique_integer([:positive]))
    assert_raise ArgumentError, fn -> String.to_existing_atom(name) end

    assert {:ok, c} =
             compile(String.replace(@body, "~w[approve revise reject]", "~w[" <> name <> "]"))

    assert c.types["review"].fields["verdict"].values == [name]
    assert_raise ArgumentError, fn -> String.to_existing_atom(name) end
  end

  defp start(c) do
    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        bindings: %{"lead" => "lead", "a" => "a", "b" => "b"},
        now: 0,
        max_messages: 10,
        room_policy: :unchecked
      )

    s
  end

  defp send(s, actor, id, kind, payload, now),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )

  defp compile(body, engine \\ "0.2.0-lab.1", roles \\ "lead, a, b")

  defp compile(body, "0.2.0-lab.1", roles) do
    source =
      ContractLab.TestClock.ensure(
        "---\nname: word-lists\ndescription: Contract name lists.\nengine: 0.2.0-lab.1\nroles: [#{roles}]\n---\n" <>
          body
      )

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => source,
      "sha256" => Canonical.hash(source)
    })
  end
end
