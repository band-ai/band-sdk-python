defmodule ContractLab.MentionModelTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  defp compile(body, extra \\ "", roles \\ "lead, a, b, c") do
    bytes =
      "---\nname: mention-model\ndescription: Per-message mention caps.\nengine: 0.2.0-lab.1\nroles: [#{roles}]\n#{extra}---\n" <>
        body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp fanout(contract, option),
    do:
      "#{contract}\ntype request\nstate new\n  lead -> {a,b,c}: request()#{option} => done\nend done\n"

  defp start(c, bindings),
    do:
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 10,
        bindings: bindings,
        room_policy: :unchecked
      )

  defp send(s, actor, kind),
    do:
      Engine.step(
        s,
        %{"id" => actor <> kind, "session" => "s", "kind" => kind, "payload" => %{}},
        actor: actor,
        now: 0
      )

  @everyone %{"lead" => "l", "a" => "a", "b" => "b", "c" => "c"}

  test "the header mention fields and their defaults are removed" do
    for field <- ~w(mentions-per-message mentions-per-session) do
      assert {:error, %{code: :unknown_field, line: 6, detail: ^field}} =
               compile(fanout("contract @infinity => expired", ""), "#{field}: 2\n")
    end

    assert {:ok, c} = compile(fanout("contract @infinity => expired", ""))
    refute Map.has_key?(c.card, :mentions)
    refute Map.has_key?(c, :mentions)
  end

  test "with no cap a rule may mention every principal it names" do
    assert {:ok, c} = compile(fanout("contract @infinity => expired", ""))
    assert {:ok, s} = start(c, @everyone)
    assert {%{status: :accepted, recipients: recipients}, s} = send(s, "l", "request")
    assert length(recipients) == 3
    assert s.mentions_used == 3
  end

  test "the contract line sets the default cap and a rule overrides it" do
    assert {:error, %{code: :mention_limit, line: 4}} =
             compile(fanout("contract @30m mentions 2 => expired", ""))

    assert {:ok, c} = compile(fanout("contract @30m mention 2 => expired", " mentions 3"))
    assert c.mentions == %{max: 2}
    assert [%{mention_cap: 3}] = c.states["new"].rules

    assert {:ok, c} = compile(fanout("contract @30m mentions 2 => expired", " mentions infinity"))
    assert [%{mention_cap: nil}] = c.states["new"].rules
    assert {:ok, s} = start(c, @everyone)
    assert {%{status: :accepted}, _} = send(s, "l", "request")

    assert {:error, %{code: :mention_limit, line: 4}} =
             compile(fanout("contract @infinity => expired", " mention 1"))

    assert {:ok, c} = compile(fanout("contract @infinity mentions 3 => expired", ""))
    refute Map.has_key?(c, :timeout)
    assert c.mentions == %{max: 3}

    assert {:ok, c} = compile(fanout("contract @80m max @4h mentions 3 => expired", ""))
    assert c.timeout.renewable
    assert c.mentions == %{max: 3}
  end

  test "a cap is a positive safe integer or infinity, given at most once" do
    for option <- [
          " mentions 0",
          " mentions 01",
          " mentions -1",
          " mentions many",
          " mentions 9007199254740992",
          " mentions 2 mentions 3",
          " mentions"
        ] do
      assert {:error, %{code: :syntax, line: 4}} =
               compile(fanout("contract @infinity => expired", option))
    end

    assert {:ok, c} =
             compile(
               "contract @infinity => expired\ntype request\nstate new\n  lead -> {a,b,c}: request() mentions 3 as r => done\nend done\n"
             )

    assert [%{mention_cap: 3, bind: "r"}] = c.states["new"].rules
  end

  test "the sender is never counted, including for the all-roles selector" do
    body = fn cap ->
      "contract @infinity mentions #{cap} => expired\ntype note\ntype stop\nstate new\n  event * -> *: note()\n  lead -> a: stop() => done\nend done\n"
    end

    assert {:ok, _} = compile(body.(3))
    assert {:error, %{code: :mention_limit, line: 5}} = compile(body.(2))
  end

  @roster """
  contract @infinity => expired
  type reviewer: agent
    specialty: enum(devops|security|general)
  reviewers: list(reviewer)
  type request
  state new
    lead -> reviewers: request() mentions 2 => done
  end done
  """

  defp member(name, actor, specialty \\ "general"),
    do: %{"name" => name, "actor" => actor, "attributes" => %{"specialty" => specialty}}

  defp roster(n),
    do: %{"lead" => "l", "reviewers" => Enum.map(1..n, &member("r#{&1}", "a#{&1}"))}

  test "a roster that pushes a rule over its cap is refused at session start" do
    assert {:ok, c} = compile(@roster, "", "lead, reviewers")
    assert {:error, :mention_limit} = start(c, roster(3))
    assert {:ok, s} = start(c, roster(2))
    assert {%{status: :accepted, recipients: recipients}, _} = send(s, "l", "request")
    assert length(recipients) == 2
  end

  test "D1: the deck's minimal header accepts lead -> reviewers with three members" do
    body = ~S'''
    contract @infinity => expired
    """
    Review independently. Cite evidence and state uncertainty.
    """
    type reviewer: agent
      "The assigned perspective, not a permission."
      specialty: enum(devops|security|frontend|general)
    "Coordinate the independent reviewers for this run."
    reviewers: list(reviewer)
    type summary
    state new
      lead -> reviewers: summary() => complete
    end complete
    '''

    bindings = %{
      "lead" => "lead-agent",
      "reviewers" => [
        member("devops", "agent-a", "devops"),
        member("security", "agent-b", "security"),
        member("general_model_b", "agent-c")
      ]
    }

    assert {:ok, c} = compile(body, "", "lead, reviewers")
    assert {:ok, s} = start(c, bindings)
    assert {%{status: :accepted, recipients: recipients}, s} = send(s, "lead-agent", "summary")
    assert length(recipients) == 3
    assert s.mentions_used == 3
  end
end
