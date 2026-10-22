defmodule ContractLab.AgentGateTest do
  use ExUnit.Case, async: true
  alias ContractLab.{AgentGate, Engine, Canonical}

  defp start(name \\ "assignment", opts \\ []) do
    {:ok, c} = ContractLab.load(Path.expand("../contracts/#{name}.band", __DIR__))

    {:ok, state} =
      Engine.new(
        c,
        Keyword.merge(
          [
            session_id: "s",
            bindings: Map.new(c.roles, &{&1, "actor:" <> &1}),
            now: 0,
            inactivity: :accepted,
            max_messages: 100,
            room_policy: :unchecked,
            capabilities: c.card.extensions
          ],
          opts
        )
      )

    {c, state}
  end

  defp policy do
    principals =
      Map.new(
        ~w(lead worker reviewer),
        &{"actor:" <> &1, %{kind: :agent, active: true, permission: "participant"}}
      )

    %{revision: "room-1", fixed_roles: %{}, principals: principals}
  end

  defp message(id, kind, payload, version),
    do: %{
      "id" => id,
      "session" => "s",
      "kind" => kind,
      "payload" => payload,
      "expected_version" => version
    }

  defp normalize(value), do: value |> Canonical.encode() |> JSON.decode!()

  test "a remote projection checks output using the same engine without committing room state" do
    {c, s} = start("assignment", room_policy: policy())
    {:ok, projection, ^s} = AgentGate.project(s, "actor:lead", 0, policy())
    m = message("o", "offer", %{"task" => "Draft a note"}, 0)
    {expected, advanced} = Engine.step(s, m, actor: "actor:lead", now: 0, room_policy: policy())
    assert AgentGate.check(c, projection, m, "actor:lead", 0) == normalize(expected)
    assert advanced.version == 1
    assert s.version == 0
    assert projection["view"]["definition"]["contract"]["meta"]["doc"] =~ "Assign work"
  end

  test "a checkpoint is accepted in a fresh checker VM that has never run Engine" do
    {_, s} = start()
    {:ok, projection, _} = AgentGate.project(s, "actor:lead", 0)
    message = message("o", "offer", %{"task" => "Fresh VM"}, 0)
    input = Canonical.encode(%{projection: projection, message: message}) |> Base.encode64()
    contract = Path.expand("../contracts/assignment.band", __DIR__)

    code = """
    {:ok, contract} = ContractLab.load(#{inspect(contract)})
    data = #{inspect(input, printable_limit: :infinity)} |> Base.decode64!() |> JSON.decode!()
    decision = ContractLab.AgentGate.check(contract, data["projection"], data["message"], "actor:lead", 0)
    IO.puts(decision["status"])
    """

    paths = Path.wildcard(Path.expand("../_build/test/lib/*/ebin", __DIR__))
    args = ["--erl", "+S 2:2"] ++ Enum.flat_map(paths, &["-pa", &1]) ++ ["-e", code]
    assert {"accepted\n", 0} = System.cmd("elixir", args, stderr_to_stdout: true)
  end

  test "out-of-turn and invalid payload replies are rejected locally" do
    {c, s} = start()
    {:ok, projection, _} = AgentGate.project(s, "actor:worker", 0)

    assert AgentGate.check(
             c,
             projection,
             message("x", "submit", %{"task" => "unknown", "artifact" => "x"}, 0),
             "actor:worker",
             0
           )["reason"] == "illegal_message"

    {:ok, lead, _} = AgentGate.project(s, "actor:lead", 0)

    assert AgentGate.check(c, lead, message("x", "offer", %{"task" => 42}, 0), "actor:lead", 0)[
             "reason"
           ] == "payload"
  end

  test "redacted checkpoints retain reference checks without exposing other participants' payloads" do
    {c, s} = start()

    {_, s} =
      Engine.step(s, message("o", "offer", %{"task" => "private offer text"}, 0),
        actor: "actor:lead",
        now: 0
      )

    {:ok, projection, _} = AgentGate.project(s, "actor:worker", 0)
    snapshot = projection["checkpoint"]
    assert snapshot["state"]["facts"]["o"] == %{"kind" => "offer"}
    refute inspect(snapshot) =~ "private offer text"
    valid = message("a", "accept", %{"task" => "o"}, 1)
    assert AgentGate.check(c, projection, valid, "actor:worker", 0)["status"] == "accepted"

    assert AgentGate.check(
             c,
             projection,
             put_in(valid, ["payload", "task"], "different"),
             "actor:worker",
             0
           )["reason"] == "reference"
  end

  test "expired checkpoints reject a reply at the deadline boundary" do
    {c, s} = start()

    {_, s} =
      Engine.step(s, message("o", "offer", %{"task" => "task"}, 0), actor: "actor:lead", now: 0)

    {:ok, projection, _} = AgentGate.project(s, "actor:worker", 0)
    m = message("a", "accept", %{"task" => "o"}, 1)
    assert AgentGate.check(c, projection, m, "actor:worker", 119_999)["status"] == "accepted"
    assert AgentGate.check(c, projection, m, "actor:worker", 120_000)["reason"] == "terminal"
  end

  test "a checkpoint cannot be used for another actor or source contract" do
    {c, s} = start()
    {:ok, projection, _} = AgentGate.project(s, "actor:lead", 0)
    m = message("o", "offer", %{"task" => "task"}, 0)
    assert AgentGate.check(c, projection, m, "actor:worker", 0)["reason"] == "checkpoint_actor"

    assert AgentGate.check(%{c | source_digest: "changed"}, projection, m, "actor:lead", 0)[
             "reason"
           ] == "contract_mismatch"

    assert {:error, :unauthorized} = AgentGate.project(s, "outsider", 0)
  end

  test "malformed checkpoints, missing versions and transport spoofing fail closed" do
    {c, s} = start()
    {:ok, projection, _} = AgentGate.project(s, "actor:lead", 0)
    m = message("o", "offer", %{"task" => "task"}, 0)

    assert AgentGate.check(c, %{projection | "checkpoint" => "bad"}, m, "actor:lead", 0)["reason"] ==
             "invalid_checkpoint"

    assert AgentGate.check(c, projection, Map.delete(m, "expected_version"), "actor:lead", 0)[
             "reason"
           ] == "version_required"

    assert AgentGate.check(c, projection, Map.put(m, "actor", "actor:lead"), "actor:lead", 0)[
             "reason"
           ] == "envelope"
  end

  test "a stale version is rejected and local clock rollback cannot revive a checkpoint" do
    {c, s} = start()
    {:ok, projection, _} = AgentGate.project(s, "actor:lead", 100)

    assert AgentGate.check(
             c,
             projection,
             message("o", "offer", %{"task" => "task"}, 10),
             "actor:lead",
             100
           )["reason"] == "stale_version"

    assert AgentGate.check(
             c,
             projection,
             message("o", "offer", %{"task" => "task"}, 0),
             "actor:lead",
             99
           )["reason"] == "clock_regression"
  end

  test "the checkpoint preserves cumulative admission budgets" do
    # Under M9 an event must leave the closing stop() inside max_messages, so program-counter cannot
    # reach message_budget; the projection still carries the count (set N decision B2, 2026-10-14).
    {c, s} = start("program-counter", max_messages: 2)

    {_, s} =
      Engine.step(s, message("i", "increment", %{"by" => 1}, 0), actor: "actor:requester", now: 0)

    {:ok, projection, _} = AgentGate.project(s, "actor:counter", 0)

    assert AgentGate.check(
             c,
             projection,
             message("t", "total", %{"value" => 1}, 1),
             "actor:counter",
             0
           )["reason"] == "completion_capacity"
  end
end
