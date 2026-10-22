defmodule ContractLab.CheckpointPortabilityTest do
  use ExUnit.Case, async: true
  alias ContractLab.{AgentGate, Canonical, Checkpoint, Engine}

  # A trusted host policy keeps the exported state free of the unchecked marker.
  defp policy(c) do
    principals =
      Map.new(
        c.roles,
        &{"actor:" <> &1, %{kind: :agent, active: true, permission: "participant"}}
      )

    %{revision: "room-1", fixed_roles: %{}, principals: principals}
  end

  defp start do
    {:ok, c} = ContractLab.load(Path.expand("../contracts/assignment.band", __DIR__))

    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        bindings: Map.new(c.roles, &{&1, "actor:" <> &1}),
        now: 100,
        inactivity: :accepted,
        max_messages: 100,
        room_policy: policy(c),
        capabilities: c.card.extensions
      )

    m = %{
      "id" => "offer",
      "session" => "s",
      "kind" => "offer",
      "payload" => %{"task" => "Unicode 👩🏽‍💻; controls \v\u001F; literal \\u001f"},
      "expected_version" => 0
    }

    {decision, s} = Engine.step(s, m, actor: "actor:lead", now: 100, room_policy: policy(c))
    assert decision.status == :accepted
    {c, s, m}
  end

  defp json(value), do: value |> Canonical.encode() |> JSON.decode!()

  # A host restores with the capabilities the contract needs, as it creates the session with them.
  defp restore(c, packet, now),
    do: Checkpoint.restore(c, packet, now, :hosted, capabilities: c.card.extensions)

  test "private state is portable JSON and preserves receipt, duty, metadata and Unicode" do
    {c, s, m} = start()
    {:ok, packet} = Checkpoint.export(s)
    packet = json(packet)
    assert packet["profile"] == "band-contract/room-checkpoint/1"
    assert is_map(packet["data"])
    # assignment waits for an answer with reply alternatives, so its state has profile 6.
    assert packet["data"]["profile"] == "band-contract/state/6"
    assert packet["hash"] == Canonical.digest(packet["data"])
    assert {:ok, ^s} = restore(c, packet, 100)
    {:ok, restored} = restore(c, packet, 120_100)
    assert restored.status == :terminal
    assert restored.expired_at == 120_100

    assert {%{status: :duplicate, original: original}, _} =
             Engine.step(restored, m, actor: "actor:lead", now: 120_100, room_policy: policy(c))

    assert original.payload == m["payload"]
  end

  test "remote preflight uses portable redacted state without private payloads" do
    {c, s, _} = start()
    {:ok, projection, _} = AgentGate.project(s, "actor:worker", 100, policy(c))
    assert projection["profile"] == "band-contract/agent-view/1"
    assert is_map(projection["checkpoint"])
    assert projection["checkpoint"]["state"]["facts"]["offer"] == %{"kind" => "offer"}
    refute Canonical.encode(projection["checkpoint"]) =~ "Unicode"

    m = %{
      "id" => "accept",
      "session" => "s",
      "kind" => "accept",
      "payload" => %{"task" => "offer"},
      "expected_version" => 1
    }

    assert AgentGate.check(c, projection, m, "actor:worker", 100)["status"] == "accepted"
  end

  test "a recomputed checksum cannot admit unknown keys, enums or malformed state" do
    {c, s, _} = start()
    {:ok, packet} = Checkpoint.export(s)
    packet = json(packet)
    assert is_map(packet["data"])

    mutations = [
      fn data -> put_in(data, ["state", "surprise_atom"], "x") end,
      fn data -> put_in(data, ["state", "status"], "surprise_status") end,
      fn data -> put_in(data, ["state", "version"], -1) end,
      fn data -> put_in(data, ["state", "bindings"], %{}) end,
      fn data -> put_in(data, ["state", "obligations"], [%{}]) end,
      fn data -> put_in(data, ["state", "facts", "offer", "payload"], []) end
    ]

    for mutate <- mutations do
      data = mutate.(packet["data"])
      bad = %{packet | "data" => data, "hash" => Canonical.digest(data)}
      assert {:error, :invalid_checkpoint} = restore(c, bad, 100)
    end
  end

  test "a nonrecipient cannot distinguish hidden answers through receipt fingerprints" do
    {:ok, c} = ContractLab.load(Path.expand("../contracts/first-responder.band", __DIR__))

    {:ok, initial} =
      Engine.new(c,
        session_id: "private",
        bindings: Map.new(c.roles, &{&1, "actor:" <> &1}),
        now: 100,
        inactivity: :accepted,
        max_messages: 100,
        room_policy: policy(c)
      )

    start = %{
      "id" => "start",
      "session" => "private",
      "kind" => "start",
      "payload" => %{},
      "expected_version" => 0
    }

    {%{status: :accepted}, waiting} =
      Engine.step(initial, start, actor: "actor:lead", now: 100, room_policy: policy(c))

    projections =
      for value <- ["yes", "no"] do
        answer = %{
          "id" => "answer",
          "session" => "private",
          "kind" => "answer",
          "payload" => %{"value" => value},
          "expected_version" => 1
        }

        {%{status: :accepted}, accepted} =
          Engine.step(waiting, answer, actor: "actor:alpha", now: 100, room_policy: policy(c))

        {:ok, outsider, _} = AgentGate.project(accepted, "actor:beta", 100, policy(c))
        refute Map.has_key?(outsider["view"]["facts"], "answer")

        # The author can still check exact retries; other actors cannot reuse its ID.
        {:ok, author, _} = AgentGate.project(accepted, "actor:alpha", 100, policy(c))
        assert AgentGate.check(c, author, answer, "actor:alpha", 100)["status"] == "duplicate"

        assert AgentGate.check(c, outsider, answer, "actor:beta", 100) ==
                 %{"status" => "rejected", "reason" => "id_conflict"}

        # Durable private history still retains the original fingerprint and payload.
        {:ok, checkpoint} = Checkpoint.export(accepted)
        assert {:ok, ^accepted} = restore(c, json(checkpoint), 100)
        Canonical.encode(outsider)
      end

    assert Enum.uniq(projections) |> length() == 1
  end

  test "the retired VM-term private profile is rejected" do
    {c, s, _} = start()
    bytes = :erlang.term_to_binary(Engine.snapshot(s))

    legacy = %{
      "profile" => "contract-lab/room-checkpoint/1",
      "data" => Base.encode64(bytes),
      "hash" => Canonical.hash(bytes)
    }

    assert {:error, :invalid_checkpoint} = restore(c, legacy, 100)
  end
end
