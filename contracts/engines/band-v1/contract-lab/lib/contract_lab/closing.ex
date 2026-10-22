defmodule ContractLab.Closing do
  @moduledoc """
  The closing reserve: the fewest accepted messages that can still end a session in a
  terminal state. An event does not leave its state, so it must not spend that path.
  """

  @doc """
  Messages to keep for the cheapest message path from the current state to a terminal,
  beyond the remaining `duties` (already reserved). 0 when no message path exists.
  """
  def reserve(s, duties) do
    covered = fn rule ->
      Enum.any?(duties, fn d ->
        (d.kind == rule.kind or rule.kind in Map.get(d, :reply_kinds, [])) and d.remaining != [] and
          Enum.all?(rule.from, &(&1 in d.participants))
      end)
    end

    costs = costs(s.contract)

    s.contract
    |> edges(s.state, covered)
    |> Enum.map(fn {cost, targets} -> add(cost, worst(costs, targets)) end)
    |> Enum.reject(&is_nil/1)
    |> Enum.min(fn -> 0 end)
  end

  # Cheapest message count from every state to any terminal; nil when none is reachable.
  defp costs(c) do
    init = Map.new(c.terminals, fn {name, _} -> {name, 0} end)

    Enum.reduce(1..(map_size(c.states) + 1), init, fn _, acc ->
      Enum.reduce(c.states, acc, fn {name, _}, acc ->
        best =
          c
          |> edges(name, fn _ -> false end)
          |> Enum.map(fn {cost, targets} -> add(cost, worst(acc, targets)) end)
          |> Enum.reject(&is_nil/1)
          |> Enum.min(fn -> nil end)

        if is_nil(best) or (acc[name] && acc[name] <= best),
          do: acc,
          else: Map.put(acc, name, best)
      end)
    end)
  end

  # A message rule costs one message per required sender; a reserved duty's rule costs none.
  # A collection close and a timer into another state cost nothing. A timer into a terminal
  # is a failure path, not a closing path, and host triggers are not guaranteed to fire.
  # Each edge lists the states it can enter: a collection with a quorum (DF-25) closes into
  # either target, and nobody chooses which, so the edge costs the worse one.
  defp edges(c, name, covered) do
    state = c.states[name]

    messages =
      for rule <- state.rules ++ c.any_rules,
          not rule.event,
          is_binary(rule.target),
          Map.has_key?(c.states, rule.target) or Map.has_key?(c.terminals, rule.target),
          do:
            {if(covered.(rule), do: 0, else: if(rule.each, do: length(rule.from), else: 1)),
             [rule.target]}

    timers =
      for targets <-
            Enum.map(ContractLab.Collections.definitions(c, name), &close_targets/1) ++
              Enum.map(state.deadlines, &[&1.outcome]) ++
              Enum.map(List.wrap(get_in(state, [:timeout, :outcome])), &[&1]),
          Enum.any?(targets, &Map.has_key?(c.states, &1)),
          do: {0, targets}

    messages ++ timers
  end

  @doc "The states a collection's close can enter: its target, and its quorum target if any."
  def close_targets(d), do: [d.target | List.wrap(get_in(d, [:quorum, :target]))]

  # The worse of the costs from the possible targets; a terminal costs 0 and a target with
  # no message path to a terminal adds nothing. nil when no target has such a path.
  defp worst(costs, targets) do
    targets
    |> Enum.map(&costs[&1])
    |> Enum.reject(&is_nil/1)
    |> Enum.max(fn -> nil end)
  end

  defp add(_, nil), do: nil
  defp add(cost, total), do: cost + total
end
