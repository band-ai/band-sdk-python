defmodule ContractLab.Checkpoint do
  @moduledoc "Private durable room state. Never return this payload to a participant or model."
  alias ContractLab.{Canonical, StateCodec}
  @profile "band-contract/room-checkpoint/1"
  @max_bytes 8_388_608

  def export(state) do
    data = StateCodec.export(state)
    bytes = Canonical.encode(data)

    if byte_size(bytes) <= @max_bytes do
      {:ok, %{profile: @profile, data: data, hash: Canonical.hash(bytes)}}
    else
      {:error, :checkpoint_size}
    end
  end

  # Hosted by default: a checkpoint created in the local unchecked mode is never
  # restorable on a hosted profile, so it cannot be laundered into a room.
  # Options: `capabilities`, the host's list, refused as at session start (StateCodec.restore/4).
  def restore(contract, packet, now, profile \\ :hosted, opts \\ []) do
    with {:ok, state} <- decode(contract, packet, now, opts) do
      if profile != :local_reference and Map.get(state, :policy_mode) == :unchecked,
        do: {:error, :host_policy_required},
        else: {:ok, state}
    end
  end

  defp decode(
         contract,
         %{"profile" => @profile, "data" => data, "hash" => hash} = packet,
         now,
         opts
       ) do
    with true <- map_size(packet) == 3 and is_map(data),
         bytes <- Canonical.encode(data),
         true <- byte_size(bytes) <= @max_bytes and Canonical.hash(bytes) == hash do
      StateCodec.restore(contract, data, now, opts)
    else
      _ -> {:error, :invalid_checkpoint}
    end
  rescue
    _ -> {:error, :invalid_checkpoint}
  end

  defp decode(_, _, _, _), do: {:error, :invalid_checkpoint}
end
