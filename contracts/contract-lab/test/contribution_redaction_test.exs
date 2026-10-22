defmodule ContractLab.ContributionRedactionTest do
  # M10: another contributor's private stop-report ID never reaches a projection that may
  # not see that report (coordination.md: collection views do not expose it).
  use ExUnit.Case, async: true
  alias ContractLab.{AgentGate, Canonical, Engine}

  @private "sec-private-report-jira-SEC-4711"

  defp start do
    bytes =
      ContractLab.TestClock.ensure("""
      ---
      name: contribution-redaction
      description: Collect available results.
      engine: 0.2.0-lab.1
      roles: [lead, reviewers]
      extensions: [room.contributions/1]
      ---
      type reviewer: agent
      reviewers: list(reviewer)
      type request
      type review
        text: text(1..1000)
      state new
        lead -> reviewers: request() => reviewing
      state reviewing
        collect reports from reviewers -> lead @5m => summarizing
          reply review()
      state summarizing
      """)

    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 30,
        capabilities: ["room.contributions/1"],
        bindings: %{
          "lead" => "l",
          "reviewers" => Enum.map(~w(sec web gen), &%{"name" => &1, "actor" => &1})
        },
        room_policy: :unchecked
      )

    {%{status: :accepted}, s} =
      Engine.step(s, %{"id" => "start", "session" => "s", "kind" => "request", "payload" => %{}},
        actor: "l",
        now: 0
      )

    report = %{
      "id" => @private,
      "session" => "s",
      "collection" => "reports",
      "entry" => 1,
      "member" => "reviewers/sec",
      "cause" => %{"type" => "agent/stopped", "reference" => nil, "message" => "Stopping."}
    }

    {%{status: :contribution_stopped}, s} =
      Engine.stop_contribution(s, report, actor: "sec", now: 1)

    s
  end

  test "another contributor's projection carries no trace of the private report ID" do
    s = start()
    assert {:ok, other, _} = AgentGate.project(s, "web", 2)
    refute Canonical.encode(other) =~ @private
    refute Canonical.encode(other) =~ "jira-SEC-4711"
  end

  test "the stopping member and the receiver still see the report ID" do
    s = start()
    assert {:ok, own, _} = AgentGate.project(s, "sec", 2)
    assert Canonical.encode(own) =~ @private
    assert {:ok, receiver, _} = AgentGate.project(s, "l", 2)
    assert Canonical.encode(receiver) =~ @private
  end

  test "the redacted projection is still a checkpoint the agent gate accepts" do
    s = start()
    assert {:ok, projection, _} = AgentGate.project(s, "web", 2)

    review = %{
      "id" => "web-review",
      "session" => "s",
      "kind" => "review",
      "payload" => %{"text" => "Looks good."},
      "expected_version" => projection["view"]["version"]
    }

    assert %{"status" => "accepted"} = AgentGate.check(s.contract, projection, review, "web", 2)
  end

  test "admission refuses a report ID in the reserved redacted: namespace" do
    s = start()

    placeholder =
      "redacted:" <> binary_part(Canonical.digest(["reports", 1, "reviewers/sec"]), 0, 32)

    # Sofia's probe: web learns sec's placeholder from its own projection.
    assert {:ok, other, _} = AgentGate.project(s, "web", 2)
    assert Canonical.encode(other) =~ placeholder

    for id <- [placeholder, "redacted:", "redacted:web-1"] do
      report = %{
        "id" => id,
        "session" => "s",
        "collection" => "reports",
        "entry" => 1,
        "member" => "reviewers/web",
        "cause" => %{"type" => "agent/stopped", "reference" => nil, "message" => "Stopping."}
      }

      assert {%{status: :rejected, reason: :invalid_contribution}, ^s} =
               Engine.stop_contribution(s, report, actor: "web", now: 2)

      assert {%{status: :rejected, reason: :invalid_contribution}, ^s} =
               Engine.fail_contribution(s, report, authority: :host, now: 2)
    end

    assert {%{status: :contribution_stopped}, _} =
             Engine.stop_contribution(
               s,
               %{
                 "id" => "web-redacted:1",
                 "session" => "s",
                 "collection" => "reports",
                 "entry" => 1,
                 "member" => "reviewers/web",
                 "cause" => %{
                   "type" => "agent/stopped",
                   "reference" => nil,
                   "message" => "Stopping."
                 }
               },
               actor: "web",
               now: 2
             )
  end

  test "the placeholder is deterministic across projections" do
    s = start()
    assert {:ok, a, _} = AgentGate.project(s, "web", 2)
    assert {:ok, b, _} = AgentGate.project(s, "web", 2)
    assert Canonical.encode(a) == Canonical.encode(b)
  end
end
