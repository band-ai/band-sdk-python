defmodule ContractLab.ContributionTest do
  use ExUnit.Case, async: true
  alias ContractLab.{AgentGate, Canonical, Engine, StateCodec}

  @body """
  contract @30m => expired
  type reviewer: agent
  reviewers: list(reviewer)
  type request
  type review
    text: text(1..1000)
  type progress
    text: text(1..1000)
  type retry
  state new
    lead -> reviewers: request() => reviewing
  state reviewing
    event reviewers -> lead: progress()
    collect reports from reviewers -> lead @5m => summarizing
      reply review()
  state summarizing
    lead -> reviewers: retry() => reviewing
  """

  defp compile(body \\ @body, extensions \\ "room.contributions/1") do
    bytes =
      "---\nname: contribution-controls\ndescription: Collect available results.\nengine: 0.2.0-lab.1\nroles: [lead, reviewers]\nextensions: [#{extensions}]\n---\n" <>
        body

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp start(opts \\ []) do
    assert {:ok, c} = compile()

    config = [
      session_id: "s",
      now: 0,
      max_messages: 30,
      capabilities: ["room.contributions/1"],
      bindings: %{
        "lead" => "l",
        "reviewers" => Enum.map(~w(sec web gen), &%{"name" => &1, "actor" => &1})
      },
      room_policy: :unchecked
    ]

    assert {:ok, s} = Engine.new(c, Keyword.merge(config, opts))

    assert {%{status: :accepted}, s} =
             send(s, "l", "start", "request", %{}, 0, opts[:room_policy])

    {c, s}
  end

  defp send(s, actor, id, kind, payload, at, policy \\ nil) do
    Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
      actor: actor,
      now: at,
      room_policy: policy
    )
  end

  defp report(member \\ "gen", id \\ "stop-1", entry \\ 1) do
    %{
      "id" => id,
      "session" => "s",
      "collection" => "reports",
      "entry" => entry,
      "member" => "reviewers/" <> member,
      "cause" => %{
        "type" => "provider/budget_exhausted",
        "reference" => "provider-event-7",
        "message" => "The assigned work has ended without a report."
      }
    }
  end

  defp fail(s, r, at), do: Engine.fail_contribution(s, r, authority: :host, now: at)
  defp own_stop(s, r, actor, at), do: Engine.stop_contribution(s, r, actor: actor, now: at)

  test "new collections use room contribution controls without a failure message type" do
    assert {:ok, c} = compile()
    assert c.contribution_profile == "band-contract/contribution/1"
    assert c.collections["reports"].profile == "band-contract/collection/2"
    refute Map.has_key?(c.types, "unable")
    assert {:ok, definition} = ContractLab.for_role(c, "reviewers")
    assert definition.contract.contribution_profile == c.contribution_profile

    old_form = String.replace(@body, "reply review()", "reply review()\n    failure review()")
    assert {:error, %{code: :syntax}} = compile(old_form)

    assert {:ok, legacy} = compile(@body, "")
    assert legacy.collections["reports"].profile == "band-contract/collection/1"
    refute Map.has_key?(legacy, :contribution_profile)
  end

  test "the host can report terminal failure even when the participant sends nothing" do
    {c, s} = start()
    assert {%{status: :accepted}, s} = send(s, "sec", "a", "review", %{"text" => "A"}, 1)
    assert {%{status: :accepted}, s} = send(s, "web", "b", "review", %{"text" => "B"}, 2)
    assert {%{status: :contribution_stopped, state: "summarizing"}, s} = fail(s, report(), 3)
    assert s.status == :active
    assert Map.keys(s.facts) |> Enum.sort() == ~w(a b start)
    result = List.last(s.collection_results)
    assert result.members["reviewers/gen"].status == "failed"
    assert result.members["reviewers/gen"].message == nil
    assert result.members["reviewers/gen"].failure.origin == "host"
    assert result.members["reviewers/gen"].failure.type == "provider/budget_exhausted"
    assert result.members["reviewers/gen"].failure.reference == "provider-event-7"
    assert StateCodec.export(s)["profile"] == "band-contract/state/5"

    assert {:ok, restored} =
             StateCodec.restore(c, StateCodec.export(s), 4, capabilities: c.card.extensions)

    assert restored.collection_results == s.collection_results
  end

  test "an agent can stop only its own contribution through an authenticated operation" do
    {_, s} = start()

    assert {%{status: :rejected, reason: :unauthorized_contribution}, ^s} =
             own_stop(s, report(), "sec", 1)

    assert {%{status: :rejected, reason: :unauthorized}, ^s} =
             own_stop(s, report(), "stranger", 1)

    assert {%{status: :rejected, reason: :host_authority_required}, ^s} =
             Engine.fail_contribution(s, report(), actor: "gen", now: 1)

    assert {%{status: :contribution_stopped}, s} = own_stop(s, report(), "gen", 1)
    assert s.collections["reports"].members["reviewers/gen"].failure.origin == "member"
    assert s.collections["reports"].members["reviewers/sec"].status == "pending"

    assert {%{status: :rejected, reason: :already_contributed}, _} =
             send(s, "gen", "too-late", "review", %{"text" => "Late reply"}, 2)
  end

  test "the member path rechecks current room authorization" do
    active =
      Map.new(~w(l sec web gen), &{&1, %{kind: :agent, active: true, permission: "participant"}})

    {_, s} = start(room_policy: %{revision: "1", principals: active, fixed_roles: %{}})

    policy = %{
      revision: "2",
      principals:
        Map.new(
          ~w(l sec web gen),
          &{&1, %{kind: :agent, active: &1 != "gen", permission: "participant"}}
        ),
      fixed_roles: %{}
    }

    assert {%{status: :rejected, reason: :room_access}, ^s} =
             Engine.stop_contribution(s, report(), actor: "gen", now: 1, room_policy: policy)
  end

  test "failures are scoped to the session, collection round and member" do
    {_, s} = start()

    for {field, value} <- [
          {"session", "other"},
          {"collection", "other"},
          {"entry", 2},
          {"member", "lead"}
        ] do
      assert {%{status: :rejected, reason: :contribution_scope}, next} =
               fail(s, Map.put(report(), field, value), 1)

      assert next.collections == s.collections
    end

    for malformed <- [
          Map.put(report(), "terminal", true),
          put_in(report(), ["cause", "type"], ""),
          put_in(report(), ["cause", "message"], String.duplicate("é", 1025)),
          put_in(report(), ["cause", "reference"], %{"url" => "somewhere"})
        ] do
      assert {%{status: :rejected, reason: :invalid_contribution}, ^s} = fail(s, malformed, 1)
    end
  end

  test "ordinary diagnostic messages and rejected messages leave contributions pending" do
    {_, s} = start()

    assert {%{status: :accepted}, s} =
             send(s, "gen", "retrying", "progress", %{"text" => "Provider error; retrying."}, 1)

    assert {%{status: :rejected, reason: :illegal_message}, s} =
             send(s, "gen", "raw-error", "provider_error", %{}, 2)

    assert s.collections["reports"].members["reviewers/gen"].status == "pending"
    assert s.contribution_receipts == %{}
  end

  test "exact retries retain receipts across closure and restart; changed retries conflict" do
    {c, s} = start()
    assert {%{status: :contribution_stopped} = decision, s} = fail(s, report(), 1)
    assert {%{status: :duplicate_contribution} = duplicate, s} = fail(s, report(), 2)
    assert Map.delete(duplicate, :status) == Map.delete(decision, :status)
    assert map_size(s.contribution_receipts) == 1

    assert {%{status: :rejected, reason: :id_conflict}, _} =
             fail(s, put_in(report(), ["cause", "type"], "process/exit"), 3)

    assert {:ok, s} = Engine.tick(s, 300_000)

    assert {:ok, s} =
             StateCodec.restore(c, StateCodec.export(s), 300_001, capabilities: c.card.extensions)

    assert {%{status: :duplicate_contribution}, s} = fail(s, report(), 300_002)
    assert length(s.collection_results) == 1
  end

  test "deadline and accepted reply win over a later failure for the same contribution" do
    {_, s} = start()
    assert {%{status: :accepted}, s} = send(s, "gen", "answer", "review", %{"text" => "Done"}, 1)
    assert {%{status: :rejected, reason: :already_contributed}, s} = fail(s, report(), 2)
    assert s.collections["reports"].members["reviewers/gen"].status == "ok"

    assert {%{status: :rejected, reason: :contribution_scope}, s} =
             fail(s, report("sec"), 300_000)

    assert s.state == "summarizing"
    assert List.last(s.collection_results).members["reviewers/sec"].status == "timed_out"
  end

  test "late callbacks cannot stop the next collection round" do
    {_, s} = start()
    assert {:ok, s} = Engine.tick(s, 300_000)
    assert {%{status: :accepted}, s} = send(s, "l", "retry", "retry", %{}, 300_001)
    assert s.collections["reports"].entry == 2
    assert {%{status: :rejected, reason: :contribution_scope}, s} = fail(s, report(), 300_002)
    assert s.collections["reports"].members["reviewers/gen"].status == "pending"
    assert {%{status: :contribution_stopped}, _} = fail(s, report("gen", "current", 2), 300_003)
  end

  test "failure evidence is visible to its member and receivers but redacted from other projections" do
    {c, s} = start()
    assert {{:ok, view}, _} = Engine.view(s, actor: "sec", now: 0)

    assert view.contribution_controls == [
             %{
               operation: "stop_contribution",
               session: "s",
               collection: "reports",
               entry: 1,
               member: "reviewers/sec"
             }
           ]

    r = put_in(report("sec"), ["cause", "message"], "private provider log")
    assert {%{status: :contribution_stopped}, s} = fail(s, r, 1)
    assert {:ok, receiver, _} = AgentGate.project(s, "l", 2)
    assert Canonical.encode(receiver) =~ "private provider log"
    assert {:ok, own, _} = AgentGate.project(s, "sec", 2)
    assert Canonical.encode(own) =~ "private provider log"
    assert own["view"]["contribution_controls"] == []
    assert {:ok, other, _} = AgentGate.project(s, "web", 2)
    refute Canonical.encode(other) =~ "private provider log"
    refute Canonical.encode(other) =~ "provider-event-7"

    message = %{
      "id" => "web-result",
      "session" => "s",
      "kind" => "review",
      "payload" => %{"text" => "B"},
      "expected_version" => other["view"]["version"]
    }

    assert AgentGate.check(c, other, message, "web", 2)["status"] == "accepted"
  end

  test "support must be negotiated and an unused contribution extension is harmless" do
    assert {:ok, c} = compile()

    assert {:error, :unsupported_extension} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: 4,
               capabilities: [],
               bindings: %{"lead" => "l", "reviewers" => [%{"name" => "gen", "actor" => "gen"}]}
             )

    body = "type request\nstate new\n  lead -> reviewers: request() => done\nend done"
    assert {:ok, c} = compile(body)

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: 4,
               capabilities: ["room.contributions/1"],
               bindings: %{"lead" => "l", "reviewers" => "gen"},
               room_policy: :unchecked
             )

    assert {%{status: :rejected, reason: :contribution_scope}, s} = fail(s, report(), 1)

    assert {:ok, _} =
             StateCodec.restore(c, StateCodec.export(s), 2, capabilities: c.card.extensions)

    assert {:ok, projection, _} = AgentGate.project(s, "gen", 2)
    assert projection["view"]["contribution_controls"] == []
  end

  test "recovery rejects a forged cause, a missing receipt and a mismatched round" do
    {c, s} = start()
    assert {%{status: :contribution_stopped}, s} = fail(s, report(), 1)
    data = StateCodec.export(s)
    path = ["state", "collections", "reports", "members", "reviewers/gen", "failure", "type"]

    assert {:error, :invalid_checkpoint} =
             StateCodec.restore(c, put_in(data, path, "forged"), 2,
               capabilities: c.card.extensions
             )

    assert {:error, :invalid_checkpoint} =
             StateCodec.restore(c, put_in(data, ["state", "contribution_receipts"], %{}), 2,
               capabilities: c.card.extensions
             )

    assert {:error, :invalid_checkpoint} =
             StateCodec.restore(
               c,
               put_in(data, ["state", "collections", "reports", "entry"], 2),
               2,
               capabilities: c.card.extensions
             )
  end

  test "control receipts have a finite per-session capacity" do
    {_, s} = start(max_messages: 6)

    s =
      Enum.reduce(1..2, s, fn round, s ->
        s =
          Enum.reduce(~w(sec web gen), s, fn member, s ->
            assert {%{status: :contribution_stopped}, s} =
                     fail(s, report(member, "#{round}-#{member}", round), round * 10)

            s
          end)

        assert {%{status: :accepted}, s} =
                 send(s, "l", "retry-#{round}", "retry", %{}, round * 10 + 1)

        s
      end)

    assert {%{status: :rejected, reason: :contribution_capacity}, s} =
             fail(s, report("gen", "over", 3), 30)

    assert map_size(s.contribution_receipts) == 6
    assert s.collections["reports"].members["reviewers/gen"].status == "pending"
  end
end
