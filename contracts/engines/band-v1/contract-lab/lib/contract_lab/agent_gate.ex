defmodule ContractLab.AgentGate do
  @moduledoc "Agent-side preflight against a trusted room-issued protocol checkpoint. The room retains final authority."
  alias ContractLab.{Canonical, Engine, StateCodec}
  @max_checkpoint_bytes 1_048_576
  @redacted_fingerprint String.duplicate("0", 64)

  def project(state, actor, now, room_policy \\ nil) do
    case Engine.view(state, actor: actor, now: now, room_policy: room_policy) do
      {{:ok, view}, state} ->
        snapshot = Engine.snapshot(state)

        minimal = %{
          snapshot.state
          | facts: Map.new(state.facts, fn {id, fact} -> {id, %{kind: fact.kind}} end),
            receipts:
              Map.new(state.receipts, fn {id, receipt} ->
                # Only an author needs its payload-derived hash for exact retries.
                # Other actors retain the occupied ID without a private-value oracle.
                fingerprint =
                  if receipt.decision.actor == actor,
                    do: receipt.fingerprint,
                    else: @redacted_fingerprint

                {id,
                 %{
                   receipt
                   | fingerprint: fingerprint,
                     decision: Map.drop(receipt.decision, [:payload])
                 }}
              end),
            history: []
        }

        checkpoint =
          minimal
          |> Map.put(:contract, state.contract)
          |> ContractLab.Contributions.redact(actor)
          |> StateCodec.export()

        bytes = Canonical.encode(checkpoint)

        if byte_size(bytes) <= @max_checkpoint_bytes do
          {:ok,
           normalize(%{
             profile: "band-contract/agent-view/1",
             actor: actor,
             now: state.now,
             checkpoint: checkpoint,
             checkpoint_hash: Canonical.hash(bytes),
             view: view
           }), state}
        else
          {:error, :checkpoint_size}
        end

      {{:error, reason}, _state} ->
        {:error, reason}
    end
  end

  def check(contract, projection, message, actor, now) do
    with %{
           "actor" => bound_actor
         } <- projection,
         true <- actor == bound_actor || {:error, :checkpoint_actor},
         true <-
           (is_map(message) and Map.has_key?(message, "expected_version")) ||
             {:error, :version_required},
         {:ok, data} <- checkpoint(projection),
         # A local checker, not a host: it checks with what this engine implements, and the
         # room that issued the checkpoint already admitted the contract.
         {:ok, state} <-
           StateCodec.restore(contract, data, now,
             redacted: true,
             capabilities: Engine.implemented()
           ) do
      {decision, _uncommitted_state} = Engine.preview(state, message, actor: actor, now: now)
      normalize(decision)
    else
      {:error, reason} -> reject(reason)
      _ -> reject(:invalid_checkpoint)
    end
  rescue
    _ -> reject(:invalid_checkpoint)
  end

  defp checkpoint(%{
         "profile" => "band-contract/agent-view/1",
         "checkpoint" => data,
         "checkpoint_hash" => hash
       })
       when is_map(data) do
    bytes = Canonical.encode(data)

    if byte_size(bytes) <= @max_checkpoint_bytes and Canonical.hash(bytes) == hash,
      do: {:ok, data},
      else: {:error, :invalid_checkpoint}
  end

  defp checkpoint(_), do: {:error, :invalid_checkpoint}

  defp normalize(value), do: value |> Canonical.encode() |> JSON.decode!()
  defp reject(reason), do: %{"status" => "rejected", "reason" => to_string(reason)}
end
