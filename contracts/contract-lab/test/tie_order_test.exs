defmodule ContractLab.TieOrderTest do
  # D-12 (H5): timers due at the same instant fire in one normative order:
  # work duty, collection deadline, offline grace, state timeout, host trigger,
  # whole-run cap. IDs only break ties inside a category.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @min 60_000

  defp compile(body, extensions) do
    bytes =
      "---\nname: tie-order\ndescription: Equal-time timers.\nengine: 0.2.0-lab.1\nroles: [lead, reviewers]\nextensions: [#{Enum.join(extensions, ", ")}]\n---\n" <>
        body

    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

    c
  end

  defp policy do
    principals =
      Map.new(~w(l a b), &{&1, %{kind: :agent, active: true, permission: "participant"}})

    %{revision: "room-1", fixed_roles: %{}, principals: principals}
  end

  defp start(body, extensions \\ ["room.contributions/1"], review? \\ true) do
    c = compile(body, extensions)

    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 20,
        capabilities: extensions,
        max_controls: 32,
        bindings: %{
          "lead" => "l",
          "reviewers" => Enum.map(~w(a b), &%{"name" => &1, "actor" => &1})
        },
        room_policy: policy()
      )

    {%{status: :accepted}, s} = send(s, "l", "q", "request", 0)

    if review? do
      {%{status: :accepted}, s} = send(s, "a", "r1", "review", @min)
      s
    else
      s
    end
  end

  defp send(s, actor, id, kind, at) do
    Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => %{}},
      actor: actor,
      now: at,
      room_policy: policy()
    )
  end

  @shape """
  type request
  type review
  type status
  type summary
  state new
    lead -> reviewers: request() => reviewing
  state reviewing
  """

  test "a work duty fires before an equal collection deadline and its outcome wins" do
    s =
      start("""
      contract @1h => expired
      type reviewer: agent
      reviewers: list(reviewer)
      #{@shape}  lead -> reviewers: status() @5m => reviewing | stalled
        collect reports from reviewers -> lead @5m => summarizing
          reply review()
      state summarizing
        lead -> reviewers: summary() => complete
      end complete
      end stalled
      """)

    assert {:ok, t} = Engine.tick(s, 5 * @min)
    assert {t.state, t.status, t.outcome} == {"stalled", :terminal, "stalled"}
  end

  test "a collection deadline fires before an equal state timeout" do
    s =
      start("""
      contract @1h => expired
      type reviewer: agent
      reviewers: list(reviewer)
      #{String.replace(@shape, "state reviewing", "state reviewing @5m => stalled")}  collect reports from reviewers -> lead @5m => summarizing
          reply review()
      state summarizing
        lead -> reviewers: summary() => complete
      end complete
      end stalled
      """)

    assert {:ok, t} = Engine.tick(s, 5 * @min)
    assert {t.state, t.status} == {"summarizing", :active}
  end

  test "a collection closes before an equal whole-run cap, then the cap expires the run" do
    s =
      start("""
      contract @5m => expired
      type reviewer: agent
      reviewers: list(reviewer)
      #{@shape}  collect reports from reviewers -> lead @5m => summarizing
          reply review()
      state summarizing
        lead -> reviewers: summary() => complete
      end complete
      """)

    assert {:ok, t} = Engine.tick(s, 5 * @min)
    assert {t.status, t.outcome} == {:terminal, "expired"}
    assert [%{reason: "deadline"}] = Enum.map(t.collection_results, &Map.take(&1, [:reason]))
  end

  defp offline(s, subject, principal, at, seq \\ 1) do
    {%{status: :host_accepted}, s} =
      Engine.signal(
        s,
        %{
          "id" => "p-#{seq}",
          "sequence" => seq,
          "subject" => subject,
          "principal" => principal,
          "kind" => "presence",
          "value" => "offline",
          "at" => at
        },
        authority: :host,
        now: at
      )

    s
  end

  # L21: the offline-grace pairs. Reviewer b goes offline at 4m with 1m grace, so its grace
  # ends at 5m, exactly when the collection deadline or the state cap is due.
  test "a collection deadline fires before an equal offline grace" do
    s =
      start(
        """
        contract @1h => expired
        type reviewer: agent
        reviewers: list(reviewer)
        #{@shape}  collect reports from reviewers -> lead @5m => summarizing
            reply review()
            offline @1m
        state summarizing
          lead -> reviewers: summary() => complete
        end complete
        """,
        ["host.presence/1"]
      )

    s = offline(s, "reviewers/b", "b", 4 * @min)
    assert {:ok, t} = Engine.tick(s, 5 * @min)
    assert [result] = t.collection_results
    assert {t.state, result.reason} == {"summarizing", "deadline"}
    refute result.members["reviewers/b"].status == "offline"
  end

  # Offline grace against a state timeout cannot be isolated: a collection deadline may not
  # outlive its state's cap, so a grace ending at the cap ties with that deadline, which fires
  # first (pinned above). The next category after grace that can tie alone is a host trigger.
  test "an offline grace fires before an equal host trigger" do
    s =
      start(
        """
        contract @1h => expired
        type reviewer: agent
        reviewers: list(reviewer)
        #{@shape}  collect reports from reviewers -> lead @10m => summarizing
            reply review()
            offline @1m
          on lead offline for 4m => paused
        state summarizing
          lead -> reviewers: summary() => complete
        state paused
          lead -> reviewers: summary() => complete
        end complete
        """,
        ["host.presence/1"]
      )

    s = offline(s, "lead", "l", @min, 1)
    s = offline(s, "reviewers/b", "b", 4 * @min, 2)
    assert {:ok, t} = Engine.tick(s, 5 * @min)
    assert {t.state, t.status} == {"summarizing", :active}
    assert [%{reason: "settled"} = result] = t.collection_results
    assert result.members["reviewers/b"].status == "offline"
  end

  test "a host trigger fires before an equal whole-run cap, then the cap expires the run" do
    s =
      start(
        """
        contract @5m => expired
        type reviewer: agent
        reviewers: list(reviewer)
        #{@shape}  on lead offline for 4m => paused
        state paused
          lead -> reviewers: summary() => complete
        end complete
        """,
        ["host.presence/1"],
        false
      )

    s = offline(s, "lead", "l", @min)
    assert {:ok, t} = Engine.tick(s, 5 * @min)
    # The trigger entered paused first; the cap then ended the run.
    assert {t.entries["paused"], t.status, t.outcome} == {1, :terminal, "expired"}
  end

  test "a state timeout fires before an equal host trigger" do
    s =
      start(
        """
        contract @1h => expired
        type reviewer: agent
        reviewers: list(reviewer)
        #{String.replace(@shape, "state reviewing", "state reviewing @5m => stalled")}  on lead offline for 4m => paused
        state paused
          lead -> reviewers: summary() => complete
        end complete
        end stalled
        """,
        ["host.presence/1"],
        false
      )

    {%{status: :host_accepted}, s} =
      Engine.signal(
        s,
        %{
          "id" => "p-1",
          "sequence" => 1,
          "subject" => "lead",
          "principal" => "l",
          "kind" => "presence",
          "value" => "offline",
          "at" => @min
        },
        authority: :host,
        now: @min
      )

    assert {:ok, t} = Engine.tick(s, 5 * @min)
    assert {t.state, t.outcome} == {"stalled", "stalled"}
  end
end
