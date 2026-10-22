defmodule ContractLab.Scenario do
  @moduledoc "Replay an explicit JSON scenario with checked expectations or observation only."
  alias ContractLab.{Canonical, Engine}
  @policies %{"accepted" => :accepted, "transition" => :transition, "marked" => :marked}
  @expect_keys ~w(status state outcome reason version mentions_used)

  def run(contract, spec, opts \\ [])

  def run(contract, spec, opts) when is_map(spec) do
    with true <-
           Enum.all?(
             Map.keys(spec),
             &(&1 in ~w(bindings session now inactivity max_messages max_bytes steps capabilities
                        max_controls initial_presence watch_bindings))
           ) || {:error, :scenario_fields},
         true <-
           (is_list(spec["steps"]) and length(spec["steps"]) in 1..10_000) ||
             {:error, :scenario_steps},
         {:ok, state} <-
           Engine.new(contract,
             bindings: spec["bindings"],
             session_id: spec["session"],
             now: spec["now"],
             inactivity: @policies[Map.get(spec, "inactivity", "accepted")],
             max_messages: spec["max_messages"],
             max_bytes: Map.get(spec, "max_bytes", 16_384),
             capabilities: Map.get(spec, "capabilities", contract.card.extensions),
             max_controls: Map.get(spec, "max_controls", default_controls(contract)),
             initial_presence: Map.get(spec, "initial_presence", %{}),
             watch_bindings: Map.get(spec, "watch_bindings", %{}),
             # Local replay has no room host; the run is marked unchecked, never evidence.
             room_policy: :unchecked
           ) do
      spec["steps"]
      |> Enum.with_index(1)
      |> Enum.reduce_while({:ok, state, []}, fn {step, index}, {:ok, state, records} ->
        case execute(state, step, Keyword.get(opts, :observe, false)) do
          {:ok, next, record} -> {:cont, {:ok, next, [record | records]}}
          {:error, reason} -> {:halt, {:error, Map.put(normalize_error(reason), "step", index)}}
        end
      end)
      |> finish()
    end
  end

  def run(_, _, _), do: {:error, :scenario_object}

  defp default_controls(contract) do
    if Enum.any?(contract.card.extensions, &(&1 in ~w(host.presence/1 host.membership/1))),
      do: 128,
      else: 0
  end

  defp execute(state, step, true) when is_map(step) do
    with {:ok, decision, next} <- apply_step(state, Map.delete(step, "expect")),
         do: {:ok, next, record(decision, next)}
  end

  defp execute(state, %{"expect" => expected} = step, false)
       when is_map(expected) and map_size(expected) > 0 do
    with true <-
           Enum.all?(Map.keys(expected), &(&1 in @expect_keys)) || {:error, :expectation_fields},
         {:ok, decision, next} <- apply_step(state, Map.delete(step, "expect")) do
      record = record(decision, next)

      if Enum.all?(expected, fn {key, value} -> record[key] == value end),
        do: {:ok, next, record},
        else:
          {:error,
           %{"error" => "expectation_mismatch", "expected" => expected, "actual" => record}}
    end
  end

  defp execute(_, _, _), do: {:error, :expectation_required}

  defp record(decision, next) do
    normalize(%{
      decision: decision,
      status: decision.status,
      reason: Map.get(decision, :reason),
      state: next.state,
      outcome: next.outcome,
      version: next.version,
      mentions_used: next.mentions_used
    })
  end

  defp apply_step(state, %{"tick" => now} = step) when map_size(step) == 1 do
    case Engine.tick(state, now) do
      {:ok, next} -> {:ok, %{status: :ticked}, next}
      {:error, reason, next} -> {:ok, %{status: :rejected, reason: reason}, next}
    end
  end

  # Scenario files are trusted host-owned fixtures, never live agent messages.
  # These controls use the same engine validation and remain explicitly unchecked.
  defp apply_step(state, %{"host_signal" => event, "now" => now} = step)
       when map_size(step) == 2 do
    {decision, next} = Engine.signal(state, event, authority: :host, now: now)
    {:ok, decision, next}
  end

  defp apply_step(state, %{"fail_contribution" => report, "now" => now} = step)
       when map_size(step) == 2 do
    {decision, next} = Engine.fail_contribution(state, report, authority: :host, now: now)
    {:ok, decision, next}
  end

  defp apply_step(state, %{"stop_contribution" => report, "actor" => actor, "now" => now} = step)
       when map_size(step) == 3 do
    {decision, next} = Engine.stop_contribution(state, report, actor: actor, now: now)
    {:ok, decision, next}
  end

  defp apply_step(state, %{"actor" => actor, "now" => now, "message" => message} = step)
       when map_size(step) == 3 do
    {decision, next} = Engine.step(state, message, actor: actor, now: now)
    {:ok, decision, next}
  end

  defp apply_step(_, _), do: {:error, :step_fields}

  defp finish({:ok, state, records}),
    do:
      {:ok,
       normalize(%{
         state: state.state,
         outcome: state.outcome,
         version: state.version,
         mentions_used: state.mentions_used,
         steps: Enum.reverse(records)
       })}

  defp finish(error), do: error
  defp normalize(value), do: value |> Canonical.encode() |> JSON.decode!()
  defp normalize_error(%{} = reason), do: reason
  defp normalize_error(reason), do: %{"error" => to_string(reason)}
end
