defmodule ContractLab.ScenarioObserveTest do
  use ExUnit.Case, async: true
  alias ContractLab.{CLI, Scenario}
  @contract Path.expand("../contracts/all-participants.band", __DIR__)

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
            "message" => %{"id" => actor, "session" => "s", "kind" => "ack", "payload" => %{}}
          }
        end)
    }
  end

  defp load, do: ContractLab.load(@contract)

  test "observation executes every step without assertions" do
    {:ok, contract} = load()
    assert {:ok, result} = Scenario.run(contract, scenario(), observe: true)
    assert result["outcome"] == "complete"
    assert Enum.map(result["steps"], & &1["status"]) == ~w(accepted accepted accepted)
    assert Enum.map(result["steps"], & &1["version"]) == [1, 2, 3]
    assert Enum.all?(result["steps"], &(&1["decision"]["policy"] == "unchecked"))
  end

  test "observation ignores mismatched assertions and continues after rejected messages" do
    {:ok, contract} = load()
    [first, second, third] = scenario()["steps"]
    first = Map.put(first, "expect", %{"state" => "complete"})
    rejected = put_in(second, ["message", "kind"], "unknown")
    steps = [first, rejected, second, third, %{"tick" => 0, "expect" => %{"state" => "new"}}]

    assert {:ok, result} =
             Scenario.run(contract, Map.put(scenario(), "steps", steps), observe: true)

    assert Enum.map(result["steps"], & &1["status"]) ==
             ~w(accepted rejected accepted accepted ticked)

    assert Enum.map(result["steps"], & &1["version"]) == [1, 1, 2, 3, 3]
    assert result["outcome"] == "complete"
  end

  test "asserted mode still requires expectations and stops on the first mismatch" do
    {:ok, contract} = load()

    assert {:error, %{"error" => "expectation_required", "step" => 1}} =
             Scenario.run(contract, scenario())

    [first | rest] = scenario()["steps"]
    first = Map.put(first, "expect", %{"state" => "complete"})

    assert {:error, %{"error" => "expectation_mismatch", "step" => 1}} =
             Scenario.run(contract, Map.put(scenario(), "steps", [first | rest]))
  end

  test "observation does not disable step-shape validation" do
    {:ok, contract} = load()

    assert {:error, %{"error" => "step_fields", "step" => 1}} =
             Scenario.run(
               contract,
               Map.put(scenario(), "steps", [%{"tick" => 0, "extra" => true}]),
               observe: true
             )
  end

  test "CLI --observe returns every decision and retains the unchecked warning" do
    path = Path.join(System.tmp_dir!(), "observe-#{System.unique_integer([:positive])}.json")
    on_exit(fn -> File.rm(path) end)
    File.write!(path, JSON.encode!(scenario()))

    warning =
      ExUnit.CaptureIO.capture_io(:stderr, fn ->
        assert {:ok, %{"outcome" => "complete", "steps" => [_, _, _]}} =
                 CLI.run(["replay", "--observe", @contract, path])
      end)

    assert warning =~ "unchecked"
    assert {:error, %{"error" => "expectation_required"}} = CLI.run(["replay", @contract, path])
  end

  test "--observe is not silently accepted by check or query" do
    assert {:error, :conflicting_options} = CLI.run(["check", "--observe", @contract])
    assert {:error, :conflicting_options} = CLI.run(["query", "--observe", @contract])
  end
end
