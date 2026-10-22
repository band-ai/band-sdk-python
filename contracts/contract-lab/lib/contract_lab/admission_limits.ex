defmodule ContractLab.AdmissionLimits do
  @moduledoc """
  DF-28: entering a state creates that state's duties, and the engine refuses the entry with
  `insufficient_time` when a new duty's full window cannot fit before the contract cap
  ([defaults](../../../spec/defaults-v2.md)). This reports, for each rule that enters a state with
  duties, the last time that entry is still admissible: the cap minus the longest window the
  target state creates (a head's reply wait included). A renewal that has not happened yet does
  not count, as in the engine. A stop (an anystate head that reaches only `end` terminals on
  every path out of its wait) is admissible until just before the cap, as far as its own wait
  goes: from `wait_cut` (cap minus the wait) the cap cuts that wait and ends the run instead
  (DF-28).
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
      stop = ContractLab.ReplyWait.stop?(contract, rule, rule.target)

      case {window(contract, rule, stop), stop} do
        {0, false} -> []
        {window, false} -> [entry(from, rule, cap, window)]
        {window, true} -> [stop_entry(entry(from, rule, cap, window), contract, rule, cap)]
      end
    end)
    |> Enum.sort_by(&{&1["line"], &1["rule"]})
  end

  def report(_contract), do: []

  # A head's entry opens its reply wait, which must fit like any duty, except a stop's: the cap
  # may end that wait.
  defp window(contract, rule, stop) do
    target = rule.target

    case contract.states[target] do
      nil ->
        0

      state ->
        collections = ContractLab.Collections.definitions(contract, target)

        waits =
          if stop,
            do: [],
            else: ContractLab.ReplyWait.entry_duties(contract, target)

        Enum.max([0 | Enum.map(state.deadlines ++ collections ++ waits, & &1.after_ms)])
    end
  end

  # Admissible strictly before the cap: a message at the cap meets an expired run, so a stop
  # (window 0) reports the cap minus 1 ms.
  defp entry(from, rule, cap, window) do
    last = min(cap - window, cap - 1)

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

  # When the cap starts cutting a stop's wait: cleanup can no longer get its whole window.
  defp stop_entry(entry, contract, rule, cap) do
    [wait] = ContractLab.ReplyWait.entry_duties(contract, rule.target)
    cut = cap - wait.after_ms

    Map.merge(entry, %{
      "stop" => true,
      "wait_cut_ms" => max(cut, 0),
      "wait_cut" => duration(max(cut, 0))
    })
  end

  defp duration(0), do: "0s"

  defp duration(ms) do
    Enum.find_value(
      [{86_400_000, "d"}, {3_600_000, "h"}, {60_000, "m"}, {1000, "s"}, {1, "ms"}],
      fn {unit, suffix} -> if rem(ms, unit) == 0, do: "#{div(ms, unit)}#{suffix}" end
    )
  end
end
