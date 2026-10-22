defmodule ContractLab.AdmissionLimits do
  @moduledoc """
  DF-28: entering a state creates that state's duties, and the engine refuses the entry with
  `insufficient_time` when a new duty's full window cannot fit before the contract cap
  ([defaults](../../../spec/defaults-v2.md)). This reports, for each rule that enters a state with
  duties, the last time that entry is still admissible: the cap minus the longest window the
  target state creates. A renewal that has not happened yet does not count, as in the engine.
  `mix contract.check` returns the report so the author sees the limit at compile time.
  """

  @doc "One entry per state-entering rule whose target state creates a timed duty."
  def report(%{timeout: %{after_ms: cap}} = contract) when is_integer(cap) do
    rules =
      Enum.flat_map(contract.states, fn {name, state} -> Enum.map(state.rules, &{name, &1}) end) ++
        Enum.map(contract.any_rules, &{"anystate", &1})

    rules
    |> Enum.filter(fn {_from, rule} -> not rule.event and is_binary(rule.target) end)
    |> Enum.flat_map(fn {from, rule} ->
      case window(contract, rule.target) do
        0 -> []
        window -> [entry(from, rule, cap, window)]
      end
    end)
    |> Enum.sort_by(&{&1["line"], &1["rule"]})
  end

  def report(_contract), do: []

  defp window(contract, target) do
    case contract.states[target] do
      nil ->
        0

      state ->
        collections = ContractLab.Collections.definitions(contract, target)
        Enum.max([0 | Enum.map(state.deadlines ++ collections, & &1.after_ms)])
    end
  end

  defp entry(from, rule, cap, window) do
    last = cap - window

    %{
      "rule" => rule.id,
      "line" => Map.get(rule.meta, :line),
      "state" => from,
      "kind" => rule.kind,
      "target" => rule.target,
      "window_ms" => window,
      "last_admissible_ms" => if(last >= 0, do: last),
      "last_admissible" => if(last >= 0, do: duration(last), else: "never")
    }
  end

  defp duration(0), do: "0s"

  defp duration(ms) do
    Enum.find_value(
      [{86_400_000, "d"}, {3_600_000, "h"}, {60_000, "m"}, {1000, "s"}, {1, "ms"}],
      fn {unit, suffix} -> if rem(ms, unit) == 0, do: "#{div(ms, unit)}#{suffix}" end
    )
  end
end
