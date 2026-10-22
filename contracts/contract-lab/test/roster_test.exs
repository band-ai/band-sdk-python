defmodule ContractLab.RosterTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  defp compile(each \\ "each") do
    bytes = """
    ---
    name: runtime-reviewers
    description: Bind reviewer lanes at creation.
    engine: 0.2.0-lab.1
    roles: [lead, reviewers]
    ---
    type reviewer: agent
    ~s|Review independently; report evidence and uncertainty.|
    reviewers: list(reviewer)
    type request
    type report
    state new
      lead -> reviewers: request() => reviewing
    state reviewing
      reviewers -> lead: report() #{each} @5m => done | stalled
    end done
    end stalled
    """

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp bindings do
    %{
      "lead" => "l",
      "reviewers" => [
        %{"name" => "security", "actor" => "sec", "guidance" => "Trace authority checks."},
        %{"name" => "frontend", "actor" => "web"},
        %{"name" => "general", "actor" => "gen"}
      ]
    }
  end

  defp new(c, bindings),
    do:
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 5,
        bindings: bindings,
        room_policy: :unchecked
      )

  defp send(s, actor, kind),
    do:
      Engine.step(s, %{"id" => actor <> kind, "session" => "s", "kind" => kind, "payload" => %{}},
        actor: actor,
        now: 0
      )

  test "one template binds any nonempty bounded list with independent each contributions" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c, bindings())
    assert {%{status: :accepted, recipients: recipients}, s} = send(s, "l", "request")
    assert Enum.sort(recipients) == ["gen", "sec", "web"]
    assert {%{status: :accepted, state: "reviewing"}, s} = send(s, "sec", "report")
    assert {%{status: :accepted, state: "reviewing"}, s} = send(s, "web", "report")
    assert {%{status: :accepted, outcome: "done"}, _} = send(s, "gen", "report")
    assert {:ok, one} = new(c, put_in(bindings(), ["reviewers"], [hd(bindings()["reviewers"])]))
    assert map_size(one.bindings) == 2
    assert one.contract.digest == s.contract.digest
  end

  test "without each, any one member satisfies the generated duty" do
    assert {:ok, c} = compile("")
    assert {:ok, s} = new(c, bindings())
    assert {%{status: :accepted}, s} = send(s, "l", "request")
    assert hd(s.obligations).quantifier == :any
    assert {%{status: :accepted, outcome: "done"}, _} = send(s, "web", "report")
  end

  test "member views retain shared prompts and their host-supplied assignment" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c, bindings())
    assert {{:ok, view}, _} = Engine.view(s, actor: "sec", now: 0)
    assert view.definition.role.meta.doc =~ "Review independently"
    assert view.definition.role.assignment == "Trace authority checks."
    assert view.definition.role.collection == "reviewers"
    assert {{:error, :unauthorized}, _} = Engine.view(s, actor: "stranger", now: 0)
  end

  test "roster and lane guidance survive portable recovery without changing source pins" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c, bindings())
    assert {%{status: :accepted}, s} = send(s, "l", "request")
    assert {%{status: :accepted}, s} = send(s, "sec", "report")
    data = StateCodec.export(s)
    assert data["profile"] == "band-contract/state/4"
    assert {:ok, restored} = StateCodec.restore(c, data, 1)
    assert restored.bindings == s.bindings
    assert restored.roster_config == s.roster_config
    assert restored.contributions == s.contributions

    assert {:error, :invalid_checkpoint} =
             StateCodec.restore(
               c,
               put_in(data, ["state", "bindings", "reviewers/security"], "impostor"),
               1
             )
  end

  test "empty, overlapping, malformed and oversized rosters fail before participation" do
    assert {:ok, c} = compile()

    for members <- [
          [],
          [%{"name" => "x", "actor" => "l"}],
          [%{"name" => "x", "actor" => "s"}, %{"name" => "x", "actor" => "t"}],
          [%{"name" => "x", "actor" => "s"}, %{"name" => "y", "actor" => "s"}],
          [%{"name" => "x", "actor" => "s", "admin" => true}],
          Enum.map(1..256, &%{"name" => "m#{&1}", "actor" => "a#{&1}"})
        ] do
      assert {:error, :invalid_bindings} = new(c, put_in(bindings(), ["reviewers"], members))
    end
  end

  test "a roster's total canonical configuration fits a quarter of the redacted checkpoint" do
    assert {:ok, c} = compile()

    lane =
      &%{"name" => "m#{&1}", "actor" => "a#{&1}", "guidance" => String.duplicate("g", 16_384)}

    fits = put_in(bindings(), ["reviewers"], Enum.map(1..15, lane))
    over = put_in(bindings(), ["reviewers"], Enum.map(1..16, lane))
    assert byte_size(Canonical.encode(fits)) <= 262_144
    assert byte_size(Canonical.encode(over)) > 262_144
    assert {:ok, _} = new(c, fits)
    assert {:error, :invalid_bindings} = new(c, over)
  end
end
