defmodule ContractLab.CheckpointTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Checkpoint, Engine}

  defp start do
    {:ok, contract} = ContractLab.load(Path.expand("../contracts/hybrid-assistant.band", __DIR__))

    {:ok, state} =
      Engine.new(contract,
        session_id: "s",
        bindings: %{"requester" => "user", "assistant" => "agent"},
        now: 0,
        inactivity: :accepted,
        max_messages: 100,
        room_policy: :unchecked,
        capabilities: contract.card.extensions
      )

    {contract, state}
  end

  defp normalize(value), do: value |> Canonical.encode() |> JSON.decode!()

  test "private checkpoint preserves full state while the public projection is separate" do
    {contract, state} = start()
    {:ok, packet} = Checkpoint.export(state)

    assert {:ok, ^state} =
             Checkpoint.restore(contract, normalize(packet), 0, :local_reference,
               capabilities: contract.card.extensions
             )

    {:ok, projection, _} = ContractLab.AgentGate.project(state, "user", 0)
    assert {:error, :invalid_checkpoint} = Checkpoint.restore(contract, projection, 0)
  end

  test "tampered and compressed checkpoints are rejected before deserialization" do
    {contract, state} = start()
    {:ok, packet} = Checkpoint.export(state)

    assert {:error, :invalid_checkpoint} =
             Checkpoint.restore(contract, normalize(%{packet | hash: "changed"}), 0)

    bytes = :erlang.term_to_binary(Engine.snapshot(state), [:compressed])

    compressed = %{
      packet
      | profile: "contract-lab/room-checkpoint/1",
        data: Base.encode64(bytes),
        hash: Canonical.hash(bytes)
    }

    assert {:error, :invalid_checkpoint} = Checkpoint.restore(contract, normalize(compressed), 0)
  end
end
