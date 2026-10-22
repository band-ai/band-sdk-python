defmodule ContractLab.ParticipantTypeTest do
  use ExUnit.Case, async: true
  alias ContractLab.{AgentGate, Canonical, Engine, StateCodec}

  @body ~S'''
  "Review independently and state uncertainty."
  type reviewer: agent
    "The assigned perspective, not a permission."
    specialty: enum(security|frontend|general)
    label: text(1..8, graphemes: 1..4)
    experienced: boolean
    rounds: integer(1..3)
  "Coordinate this run's independent reviewers."
  reviewers: list(reviewer)
  type request
  type report
  state new
    lead -> reviewers: request() => reviewing
  state reviewing
    reviewers -> lead: report() each @5m => done | stalled
  end done
  end stalled
  '''

  defp compile(body \\ @body, roles \\ "lead, reviewers") do
    bytes =
      "---\nname: typed-participants\ndescription: Reusable agent schemas.\nengine: 0.2.0-lab.1\nroles: [#{roles}]\n---\n" <>
        body

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp member(name, actor),
    do: %{
      "name" => name,
      "actor" => actor,
      "guidance" => "Trace concrete evidence.",
      "attributes" => %{
        "specialty" => "security",
        "label" => "é",
        "experienced" => true,
        "rounds" => 2
      }
    }

  defp bindings,
    do: %{"lead" => "l", "reviewers" => [member("first", "a"), member("second", "b")]}

  defp new(c, config \\ bindings()),
    do:
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 5,
        bindings: config,
        room_policy: :unchecked
      )

  defp message(id, kind), do: %{"id" => id, "session" => "s", "kind" => kind, "payload" => %{}}

  defp send(s, actor, kind),
    do: Engine.step(s, message(actor <> kind, kind), actor: actor, now: 0)

  test "a reusable agent type is distinct from message types and reaches role queries" do
    assert {:ok, c} = compile()
    assert c.role_sets["reviewers"] == %{profile: "band-contract/roster/2", member: "reviewer"}
    assert {:ok, type} = ContractLab.query(c, {:type, "reviewer"})
    assert type.profile == "band-contract/participant/1"
    assert type.meta.doc == "Review independently and state uncertainty."
    assert type.fields["specialty"].meta.doc == "The assigned perspective, not a permission."
    refute Map.has_key?(c.types, "reviewer")
    assert {:ok, role} = ContractLab.for_role(c, "reviewers")
    assert role.contract.participant_types["reviewer"] == type

    reused = String.replace(@body, "type request", "auditors: list(reviewer)\ntype request")
    assert {:ok, both} = compile(reused, "lead, reviewers, auditors")
    assert both.role_sets["auditors"].member == "reviewer"
    assert {:ok, _} = new(both, Map.put(bindings(), "auditors", [member("third", "c")]))

    assert {:error, %{code: :syntax, line: 9, detail: detail}} =
             compile(
               String.replace(
                 @body,
                 "reviewers: list(reviewer)",
                 "role reviewers: list(reviewer)"
               )
             )

    assert detail =~ "without role"
  end

  test "validated assignments and all three guidance levels reach the participating agent" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c)
    assert {{:ok, view}, _} = Engine.view(s, actor: "a", now: 0)
    role = view.definition.role
    assert role.participant_type == "reviewer"
    assert role.attributes == hd(bindings()["reviewers"])["attributes"]
    assert role.assignment == "Trace concrete evidence."
    assert role.meta.doc == "Coordinate this run's independent reviewers."

    assert view.definition.contract.participant_types[role.participant_type].meta.doc =~
             "Review independently"

    assert {{:error, :unauthorized}, _} = Engine.view(s, actor: "unknown", now: 0)
  end

  test "typed members keep each contributions and recover through a portable checkpoint" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c)
    assert {%{status: :accepted}, s} = send(s, "l", "request")
    assert {%{status: :accepted, state: "reviewing"}, s} = send(s, "a", "report")
    exported = StateCodec.export(s)
    assert exported["profile"] == "band-contract/state/4"
    assert {:ok, restored} = StateCodec.restore(c, exported, 1)
    assert restored.roster_config == bindings()
    assert restored.contract.role_metadata == s.contract.role_metadata
    assert {:ok, projection, _} = AgentGate.project(restored, "b", 1)
    proposal = Map.put(message("breport", "report"), "expected_version", restored.version)

    assert %{"status" => "accepted", "outcome" => "done"} =
             AgentGate.check(c, projection, proposal, "b", 1)

    assert {%{status: :accepted, outcome: "done"}, _} =
             Engine.step(restored, proposal, actor: "b", now: 1)

    assert {:error, :invalid_checkpoint} =
             StateCodec.restore(c, Map.put(exported, "profile", "band-contract/state/3"), 1)

    invalid =
      put_in(exported, ["state", "roster_config", "reviewers"], [
        %{member("first", "a") | "attributes" => %{}}
      ])

    assert {:error, :invalid_checkpoint} = StateCodec.restore(c, invalid, 1)
  end

  test "creation rejects missing, extra, wrongly typed and over-bound attributes" do
    assert {:ok, c} = compile()
    attrs = hd(bindings()["reviewers"])["attributes"]

    for bad <- [
          nil,
          [],
          %{},
          Map.delete(attrs, "rounds"),
          Map.put(attrs, "authority", "owner"),
          %{attrs | "specialty" => "unknown"},
          %{attrs | "rounds" => 4},
          %{attrs | "experienced" => "true"},
          %{attrs | "label" => "abcde"},
          %{attrs | "label" => "🚀🚀🚀"}
        ] do
      assert {:error, :invalid_bindings} =
               new(c, %{
                 "lead" => "l",
                 "reviewers" => [Map.put(member("first", "a"), "attributes", bad)]
               })
    end

    assert {:error, :invalid_bindings} =
             new(c, %{
               "lead" => "l",
               "reviewers" => [Map.delete(member("first", "a"), "attributes")]
             })

    assert {:error, :invalid_bindings} =
             new(c, %{"lead" => "l", "reviewers" => [member("first", "l")]})
  end

  test "empty participant schemas allow omitted attributes without losing the type" do
    body =
      "type reviewer: agent\nreviewers: list(reviewer)\ntype request\nstate new\n  lead -> reviewers: request() => done\nend done\n"

    assert {:ok, c} = compile(body)

    assert {:ok, s} =
             new(c, %{"lead" => "l", "reviewers" => [%{"name" => "first", "actor" => "a"}]})

    assert s.contract.role_metadata["reviewers/first"].attributes == %{}
    assert s.contract.role_metadata["reviewers/first"].participant_type == "reviewer"
  end

  test "the encoded attribute budget applies in addition to individual field bounds" do
    assert {:ok, c} =
             compile(String.replace(@body, "text(1..8, graphemes: 1..4)", "text(1..20000)"))

    attrs = hd(bindings()["reviewers"])["attributes"]

    allowed =
      Map.put(member("first", "a"), "attributes", %{
        attrs
        | "label" => String.duplicate("a", 16000)
      })

    assert {:ok, _} = new(c, %{"lead" => "l", "reviewers" => [allowed]})
    oversized = put_in(allowed, ["attributes", "label"], String.duplicate("a", 16384))
    assert {:error, :invalid_bindings} = new(c, %{"lead" => "l", "reviewers" => [oversized]})
  end

  test "guidance is excluded from behavior identity while participant constraints are included" do
    assert {:ok, a} = compile()

    assert {:ok, b} =
             compile(
               String.replace(
                 @body,
                 "Review independently and state uncertainty.",
                 "Explain uncertainty carefully."
               )
             )

    assert a.digest == b.digest
    refute a.source_digest == b.source_digest
    assert {:ok, changed} = compile(String.replace(@body, "integer(1..3)", "integer(1..4)"))
    refute a.digest == changed.digest
  end

  test "unresolved types, message/participant confusion and unsupported schemas fail closed" do
    for source <- [
          String.replace(@body, "list(reviewer)", "list(missing)"),
          String.replace(@body, "list(reviewer)", "list(report)"),
          String.replace(@body, "report() each", "reviewer() each"),
          String.replace(@body, "type request", "type reviewer\ntype request"),
          String.replace(@body, "rounds: integer(1..3)", "rounds: ref(request)"),
          String.replace(@body, "type reviewer: agent", "type reviewer: human")
        ] do
      assert {:error, _} = compile(source)
    end

    assert {:error, _} =
             compile(
               String.replace(@body, "type request", "type request\n  author: ref(reviewer)")
             )
  end
end
