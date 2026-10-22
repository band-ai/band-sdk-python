defmodule ContractLab.ScenarioExtensionsTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Scenario}

  defp contract do
    source = """
    ---
    name: replay-extensions
    description: Replay host presence and contribution controls.
    engine: 0.2.0-lab.1
    roles: [lead, reviewers]
    extensions: [host.presence/1, room.contributions/1]
    ---
    contract @15m => expired
    type reviewer: agent
    reviewers: list(reviewer)
    type request
    type review
    state new
      lead -> reviewers: request() => collecting
    state collecting
      collect reports from reviewers -> lead @5m => done
        reply review()
        offline @10s
    end done
    end expired
    """

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => source,
      "sha256" => Canonical.hash(source)
    })
  end

  defp scenario(steps) do
    %{
      "bindings" => %{"lead" => "l", "reviewers" => [%{"name" => "sec", "actor" => "sec"}]},
      "session" => "s",
      "now" => 0,
      "max_messages" => 10,
      "steps" => [request() | steps]
    }
  end

  defp request do
    %{
      "actor" => "l",
      "now" => 0,
      "message" => %{"id" => "r", "session" => "s", "kind" => "request", "payload" => %{}},
      "expect" => %{"status" => "accepted", "state" => "collecting"}
    }
  end

  defp report do
    %{
      "id" => "failure",
      "session" => "s",
      "collection" => "reports",
      "entry" => 1,
      "member" => "reviewers/sec",
      "cause" => %{"type" => "provider/failed", "reference" => nil, "message" => "Stopped."}
    }
  end

  test "declared extensions are available for ordinary local replay without extra config" do
    {:ok, contract} = contract()
    assert {:ok, %{"state" => "collecting"}} = Scenario.run(contract, scenario([]))
  end

  test "trusted presence observations replay the exact offline grace boundary" do
    {:ok, contract} = contract()

    event = %{
      "id" => "p1",
      "sequence" => 1,
      "subject" => "reviewers/sec",
      "principal" => "sec",
      "kind" => "presence",
      "value" => "offline",
      "at" => 1
    }

    steps = [
      %{"host_signal" => event, "now" => 1, "expect" => %{"status" => "host_accepted"}},
      %{"tick" => 10_000, "expect" => %{"state" => "collecting"}},
      %{"tick" => 10_001, "expect" => %{"outcome" => "done"}}
    ]

    assert {:ok, result} = Scenario.run(contract, scenario(steps))
    assert result["outcome"] == "done"
    assert Enum.at(result["steps"], 1)["decision"]["policy"] == "unchecked"
  end

  test "member contribution controls retain ownership checks" do
    {:ok, contract} = contract()

    steps = [
      %{
        "actor" => "l",
        "now" => 1,
        "stop_contribution" => report(),
        "expect" => %{"status" => "rejected", "reason" => "unauthorized_contribution"}
      },
      %{
        "actor" => "sec",
        "now" => 2,
        "stop_contribution" => report(),
        "expect" => %{"status" => "contribution_stopped", "outcome" => "done"}
      }
    ]

    assert {:ok, %{"outcome" => "done"}} = Scenario.run(contract, scenario(steps))
  end

  test "host contribution failure is a separate trusted fixture operation" do
    {:ok, contract} = contract()

    step = %{
      "now" => 1,
      "fail_contribution" => report(),
      "expect" => %{"status" => "contribution_stopped", "outcome" => "done"}
    }

    assert {:ok, result} = Scenario.run(contract, scenario([step]))
    assert List.last(result["steps"])["decision"]["origin"] == "host"
    assert List.last(result["steps"])["decision"]["policy"] == "unchecked"
  end

  test "explicit capabilities and control budgets are still validated" do
    {:ok, contract} = contract()

    assert {:error, :unsupported_extension} =
             Scenario.run(contract, Map.put(scenario([]), "capabilities", []))

    assert {:error, :control_budget_required} =
             Scenario.run(contract, Map.put(scenario([]), "max_controls", 1))

    assert {:error, :scenario_fields} =
             Scenario.run(contract, Map.put(scenario([]), "max_control", 10))
  end

  test "initial presence is host configuration and obeys validation" do
    {:ok, contract} = contract()
    spec = scenario([]) |> Map.put("now", 20_000)
    spec = put_in(spec, ["steps", Access.at(0), "now"], 20_000)
    spec = put_in(spec, ["steps", Access.at(0), "expect", "state"], "done")
    presence = %{"reviewers/sec" => %{"value" => "offline", "since" => 0}}

    assert {:ok, %{"outcome" => "done"}} =
             Scenario.run(contract, Map.put(spec, "initial_presence", presence))

    assert {:error, :invalid_initial_presence} =
             Scenario.run(contract, Map.put(spec, "initial_presence", %{"reviewers/sec" => %{}}))
  end
end
