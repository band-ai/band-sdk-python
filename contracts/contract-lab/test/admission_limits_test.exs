defmodule ContractLab.AdmissionLimitsTest do
  # DF-28: a duty created on entering a state must fit before the contract cap, so a rule into
  # that state is refused `insufficient_time` after `cap - window`. `mix contract.check` reports
  # that last admissible time per rule, so the author sees it at compile time, not in a replay.
  # Both sends are the lead's, so the contract is one-way (reply_required has nothing to flag).
  use ExUnit.Case, async: true
  alias ContractLab.{CLI, Scenario}

  @header "---\nname: probe\ndescription: DF-28 probe.\nengine: 0.2.0-lab.1\nroles: [lead, executor]\n---\n"

  defp source(clock) do
    @header <>
      "contract #{clock} => expired\ntype request\n text: text(100)\ntype result\n text: text(100)\n" <>
      "type receipt\n text: text(100)\nstate new\n lead -> executor: request() => working\n" <>
      "state working\n lead -> executor: result() => reported\nstate reported\n" <>
      " lead -> executor: receipt() @1h => done | unacknowledged\nend done\nend unacknowledged\n"
  end

  defp contract(clock) do
    dir = Path.join(System.tmp_dir!(), "df28-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    path = Path.join(dir, "probe.band")
    File.write!(path, source(clock))
    path
  end

  test "check reports each rule's last admissible time before the contract cap" do
    assert {:ok, [result]} = CLI.run(["check", contract("@2h")])

    assert result["admission_limits"] == [
             %{
               "rule" => "rule-1",
               "line" => 11,
               "state" => "working",
               "kind" => "result",
               "target" => "reported",
               "window_ms" => 3_600_000,
               "last_admissible_ms" => 3_600_000,
               "last_admissible" => "1h"
             }
           ]
  end

  test "an unbounded contract has no admission limits" do
    assert {:ok, [result]} = CLI.run(["check", contract("@infinity")])
    assert result["admission_limits"] == []
  end

  test "the reported time is the engine's boundary: admitted at it, refused just after" do
    {:ok, compiled} = ContractLab.load(contract("@2h"))

    run = fn at ->
      Scenario.run(compiled, %{
        "bindings" => %{"lead" => "l", "executor" => "e"},
        "session" => "s",
        "now" => 0,
        "inactivity" => "accepted",
        "max_messages" => 10,
        "steps" => [
          step("l", 0, "r1", "request", 0, %{"status" => "accepted"}),
          step("l", at, "r2", "result", 1, %{"status" => "accepted", "state" => "reported"})
        ]
      })
    end

    assert {:ok, _} = run.(3_600_000)
    assert {:error, %{"actual" => %{"reason" => "insufficient_time"}}} = run.(3_600_001)
  end

  defp step(actor, now, id, kind, version, expect) do
    %{
      "actor" => actor,
      "now" => now,
      "message" => %{
        "id" => id,
        "session" => "s",
        "kind" => kind,
        "payload" => %{"text" => "x"},
        "expected_version" => version
      },
      "expect" => expect
    }
  end
end
