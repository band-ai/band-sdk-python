defmodule ContractLab.ScenarioInactivityTest do
  use ExUnit.Case, async: true
  alias ContractLab.{CLI, Scenario}
  @contract Path.expand("../contracts/all-participants.band", __DIR__)

  defp scenario do
    %{
      "bindings" => %{"alice" => "a", "bob" => "b", "carol" => "c"},
      "session" => "s",
      "now" => 0,
      "max_messages" => 10,
      "steps" => [
        %{"tick" => 299_999, "expect" => %{"state" => "new", "outcome" => nil}},
        %{"tick" => 300_000, "expect" => %{"outcome" => "stalled"}}
      ]
    }
  end

  test "current-profile replay omits inactivity without inventing an idle timer" do
    {:ok, contract} = ContractLab.load(@contract)
    assert {:ok, result} = Scenario.run(contract, scenario())
    assert result["outcome"] == "stalled"

    for policy <- ~w(accepted transition marked) do
      assert {:ok, ^result} = Scenario.run(contract, Map.put(scenario(), "inactivity", policy))
    end
  end

  test "explicit invalid inactivity remains rejected, including null" do
    {:ok, contract} = ContractLab.load(@contract)

    for policy <- [nil, false, 0, "implicit"] do
      assert {:error, :inactivity_policy_required} =
               Scenario.run(contract, Map.put(scenario(), "inactivity", policy))
    end
  end

  test "the replay CLI accepts a scenario without inactivity" do
    path = Path.join(System.tmp_dir!(), "inactivity-#{System.unique_integer([:positive])}.json")
    on_exit(fn -> File.rm(path) end)
    File.write!(path, JSON.encode!(scenario()))
    assert {:ok, %{"outcome" => "stalled"}} = CLI.run(["replay", @contract, path])
  end
end
