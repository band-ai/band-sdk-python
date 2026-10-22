defmodule ContractLab.TimedRenewalEventTest do
  # DF-07 (D13): one timed event rule may carry a duty and an `@self` renewal:
  #   event adversary -> owner: progress() @15m => unacknowledged @self
  # Accepting the event discharges its own duty and renews the immediately following timed
  # rule, exactly as a separate `=> @self` event does. The form was a syntax error before.
  # The adversary opens the round, so its critique is an owed report and stays a timed rule: as the
  # reply to an owner's request it would be an alternative, and @self would renew the reply wait
  # instead (renewable-timeouts.md), leaving no following timed rule to renew.
  use ExUnit.Case, async: true
  alias ContractLab.Scenario

  @header "---\nname: probe\ndescription: DF-07 probe.\nengine: 0.2.0-lab.1\nroles: [owner, adversary]\n---\n"
  @min 60_000

  defp source(heartbeat) do
    @header <>
      """
      contract @12h => expired
      type round1
      type progress
      type critique
      type done
      state new
        adversary -> owner: round1() => round1
      state round1 @4h => stalled
        #{heartbeat}
        adversary -> owner: critique() @1h => folding | stalled
      state folding
        owner -> adversary: done() => complete
      end complete
      end unacknowledged
      end stalled
      """
  end

  defp load(heartbeat) do
    dir = Path.join(System.tmp_dir!(), "df07-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    path = Path.join(dir, "probe.band")
    File.write!(path, source(heartbeat))
    ContractLab.load(path)
  end

  @combined "event adversary -> owner: progress() @15m => unacknowledged @self"

  defp run(contract, steps) do
    Scenario.run(contract, %{
      "bindings" => %{"owner" => "o", "adversary" => "a"},
      "session" => "s",
      "now" => 0,
      "inactivity" => "accepted",
      "max_messages" => 20,
      "steps" => steps
    })
  end

  defp send(actor, minute, id, kind, version, expect) do
    %{
      "actor" => actor,
      "now" => minute * @min,
      "message" => %{
        "id" => id,
        "session" => "s",
        "kind" => kind,
        "payload" => %{},
        "expected_version" => version
      },
      "expect" => expect
    }
  end

  defp tick(minute, expect), do: %{"tick" => minute * @min, "expect" => expect}

  test "the combined form compiles to one rule with a duty and an @self renewal" do
    assert {:ok, c} = load(@combined)
    [heartbeat, critique] = c.states["round1"].rules
    assert heartbeat.event and heartbeat.kind == "progress"
    assert %{scope: "rule", rule: rule_id} = heartbeat.reset
    assert rule_id == critique.id

    assert [
             %{kind: "progress", after_ms: 900_000, outcome: "unacknowledged"},
             %{kind: "critique", after_ms: 3_600_000, outcome: "stalled"}
           ] =
             c.states["round1"].deadlines
  end

  test "one progress acknowledges and renews: 15m passes, the critique moves to 5m + 1h" do
    {:ok, c} = load(@combined)

    assert {:ok, _} =
             run(c, [
               send("a", 0, "r1", "round1", 0, %{"status" => "accepted", "state" => "round1"}),
               send("a", 5, "p1", "progress", 1, %{"status" => "accepted", "state" => "round1"}),
               tick(15, %{"state" => "round1", "outcome" => nil}),
               tick(61, %{"state" => "round1", "outcome" => nil}),
               tick(65, %{"outcome" => "stalled"})
             ])
  end

  test "every accepted progress renews; only the first discharges the duty" do
    {:ok, c} = load(@combined)

    assert {:ok, _} =
             run(c, [
               send("a", 0, "r1", "round1", 0, %{"status" => "accepted", "state" => "round1"}),
               send("a", 5, "p1", "progress", 1, %{"status" => "accepted"}),
               send("a", 30, "p2", "progress", 2, %{"status" => "accepted"}),
               tick(89, %{"state" => "round1", "outcome" => nil}),
               tick(90, %{"outcome" => "stalled"})
             ])
  end

  test "only @self combines with a timed event" do
    for target <- ["@state", "@contract"] do
      assert {:error, %{code: :syntax}} =
               load("event adversary -> owner: progress() @15m => unacknowledged #{target}")
    end
  end

  test "silence still ends unacknowledged at 15m" do
    {:ok, c} = load(@combined)

    assert {:ok, _} =
             run(c, [
               send("a", 0, "r1", "round1", 0, %{"status" => "accepted", "state" => "round1"}),
               tick(15, %{"outcome" => "unacknowledged"})
             ])
  end

  test "without the renewal the critique stays due at 1h (control)" do
    {:ok, c} = load("event adversary -> owner: progress() @15m => unacknowledged")

    assert {:ok, _} =
             run(c, [
               send("a", 0, "r1", "round1", 0, %{"status" => "accepted", "state" => "round1"}),
               send("a", 5, "p1", "progress", 1, %{"status" => "accepted", "state" => "round1"}),
               tick(60, %{"outcome" => "stalled"})
             ])
  end

  test "the combined form still needs a timed rule immediately after it" do
    text =
      String.replace(
        source(@combined),
        "  adversary -> owner: critique() @1h => folding | stalled",
        "  adversary -> owner: critique() => folding"
      )

    dir = Path.join(System.tmp_dir!(), "df07-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    path = Path.join(dir, "probe.band")
    File.write!(path, text)
    assert {:error, %{code: :timer_target}} = ContractLab.load(path)
  end
end
