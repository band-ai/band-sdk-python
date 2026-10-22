defmodule ContractLab.AuthoringTest do
  use ExUnit.Case, async: true
  alias ContractLab.{CLI, Scenario}
  @assignment Path.expand("../contracts/assignment.band", __DIR__)
  @all Path.expand("../contracts/all-participants.band", __DIR__)

  for scenario <-
        Path.wildcard(Path.expand("../scenarios/current/history-review-*.json", __DIR__)),
      not String.contains?(Path.basename(scenario), "v2") do
    test "history-derived trace #{Path.basename(scenario)} matches its assertions" do
      contract = Path.expand("../contracts/history-review.band", __DIR__)
      assert {:ok, _result} = CLI.run(["replay", contract, unquote(scenario)])
    end
  end

  test "explicit rule deadlines preserve success and both inactivity configurations are inert" do
    original = Path.expand("../contracts/history-review.band", __DIR__)
    revised = Path.expand("../contracts/history-review-v2.band", __DIR__)
    idle = Path.expand("../scenarios/current/history-review-idle.json", __DIR__)
    deadline = Path.expand("../scenarios/current/history-review-v2-deadline.json", __DIR__)
    success = Path.expand("../scenarios/current/history-review-success.json", __DIR__)
    assert {:ok, %{"state" => "writing", "outcome" => nil}} = CLI.run(["replay", original, idle])
    assert {:ok, %{"outcome" => "stalled"}} = CLI.run(["replay", revised, deadline])
    assert {:ok, %{"outcome" => "complete"}} = CLI.run(["replay", original, success])
    assert {:ok, %{"outcome" => "complete"}} = CLI.run(["replay", revised, success])
    {:ok, first} = ContractLab.load(original)
    {:ok, second} = ContractLab.load(revised)
    refute first.digest == second.digest
    refute first.source_digest == second.source_digest
    assert second.meta.doc =~ "H10"
  end

  defp scenario do
    %{
      "bindings" => %{"alice" => "a", "bob" => "b", "carol" => "c"},
      "session" => "s",
      "now" => 0,
      "inactivity" => "accepted",
      "max_messages" => 10,
      "steps" =>
        Enum.map(["a", "b", "c"], fn actor ->
          %{
            "actor" => actor,
            "now" => 0,
            "message" => %{"id" => actor, "session" => "s", "kind" => "ack", "payload" => %{}},
            "expect" => %{
              "status" => "accepted",
              "state" => if(actor == "c", do: "complete", else: "new")
            }
          }
        end)
    }
  end

  test "scenario executes all participants and returns documented decisions" do
    {:ok, contract} = ContractLab.load(@all)
    assert {:ok, result} = Scenario.run(contract, scenario())
    assert result["outcome"] == "complete"
    assert result["mentions_used"] == 6
    assert length(result["steps"]) == 3
    assert Map.has_key?(hd(result["steps"])["decision"], "metadata")
  end

  test "scenario mismatch reports the exact step and observed decision" do
    {:ok, contract} = ContractLab.load(@all)

    spec =
      update_in(scenario(), ["steps"], fn [first | rest] ->
        [put_in(first, ["expect", "state"], "complete") | rest]
      end)

    assert {:error, %{"step" => 1, "actual" => %{"state" => "new"}}} =
             Scenario.run(contract, spec)
  end

  test "tick scenarios test the exact deadline boundary" do
    {:ok, contract} = ContractLab.load(@all)

    spec =
      Map.put(scenario(), "steps", [%{"tick" => 300_000, "expect" => %{"outcome" => "stalled"}}])

    assert {:ok, %{"outcome" => "stalled"}} = Scenario.run(contract, spec)
  end

  test "scenario refuses unknown policies, missing assertions and misspelled assertion keys" do
    {:ok, contract} = ContractLab.load(@all)
    assert {:error, _} = Scenario.run(contract, Map.put(scenario(), "inactivity", "implicit"))
    assert {:error, _} = Scenario.run(contract, Map.put(scenario(), "steps", [%{"tick" => 0}]))

    assert {:error, _} =
             Scenario.run(
               contract,
               Map.put(scenario(), "steps", [%{"tick" => 0, "expect" => %{"stats" => "active"}}])
             )
  end

  test "query CLI retains metadata in full, role and type results" do
    assert {:ok, full} = CLI.run(["query", @assignment])
    assert full["meta"]["doc"] =~ "Assign work"
    assert {:ok, role} = CLI.run(["query", @assignment, "--role", "worker"])
    assert role["role"]["meta"]["doc"] =~ "Accept or refuse"
    assert role["contract"]["meta"] == full["meta"]
    assert {:ok, type} = CLI.run(["query", @assignment, "--type", "offer"])
    assert type["fields"]["task"]["meta"]["doc"] =~ "self-contained"
  end

  test "CLI checks contracts and rejects conflicting or unknown query options" do
    assert {:ok, [a, b]} = CLI.run(["check", @assignment, @all])
    assert a["name"] == "assignment"
    assert b["name"] == "all-participants"
    assert {:error, _} = CLI.run(["query", @assignment, "--role", "worker", "--type", "offer"])
    assert {:error, _} = CLI.run(["query", @assignment, "--bogus"])
    assert {:error, _} = CLI.run(["check", @assignment, "--format", "imaginary"])
  end

  test "CLI replays a real JSON scenario file and propagates failed expectations" do
    path = Path.join(System.tmp_dir!(), "scenario-#{System.unique_integer([:positive])}.json")
    on_exit(fn -> File.rm(path) end)
    File.write!(path, JSON.encode!(scenario()))
    assert {:ok, %{"outcome" => "complete"}} = CLI.run(["replay", @all, path])

    File.write!(
      path,
      JSON.encode!(
        put_in(scenario(), ["steps"], [%{"tick" => 0, "expect" => %{"outcome" => "complete"}}])
      )
    )

    assert {:error, %{"step" => 1}} = CLI.run(["replay", @all, path])
  end
end
