defmodule ContractLab.Totals do
  @moduledoc """
  DF-21 state budgets. `state x @4h => a total @6h => b` caps the time spent in `x` summed
  over every entry; the per-entry clock still restarts on each entry. Sessions whose contract
  declares no `total` carry no `dwell` field.
  """

  def enabled?(c), do: Enum.any?(c.states, fn {_, s} -> Map.has_key?(s, :total) end)

  def initial(c), do: if(enabled?(c), do: %{dwell: %{}}, else: %{})

  defp total(s, state), do: get_in(s.contract.states, [state, :total])

  @doc "Close the current entry at `at`: add its time to the state's dwell."
  def leave(s, at) do
    if Map.has_key?(s, :dwell) and s.status == :active and total(s, s.state) do
      put_in(s, [:dwell, s.state], Map.get(s.dwell, s.state, 0) + at - s.entered_at)
    else
      s
    end
  end

  @doc "The budget outcome when an entry into `state` cannot give every declared window."
  def short(s, state) do
    with true <- Map.has_key?(s, :dwell),
         %{} = total <- total(s, state) do
      remaining = total.after_ms - Map.get(s.dwell, state, 0)

      # A reply wait is not a deadline row, so it is counted from its definition: entering W
      # by a head or a re-send opens it, and it must fit the dwell left like any duty.
      windows =
        Enum.map(s.contract.states[state].deadlines, & &1.after_ms) ++
          Enum.map(ContractLab.Collections.definitions(s.contract, state), & &1.after_ms) ++
          Enum.map(ContractLab.ReplyWait.entry_duties(s.contract, state), & &1.after_ms)

      if remaining <= 0 or Enum.any?(windows, &(&1 > remaining)), do: total.outcome
    else
      _ -> nil
    end
  end

  @doc "Absolute budget deadline of the current entry, or nil."
  def deadline_at(s) do
    if Map.has_key?(s, :dwell) and s.status == :active do
      if total = total(s, s.state),
        do: s.entered_at + total.after_ms - Map.get(s.dwell, s.state, 0)
    end
  end

  def deadlines(s) do
    case deadline_at(s) do
      nil ->
        []

      at ->
        [
          %{
            at: at,
            priority: 3,
            id: "total-" <> s.state,
            outcome: total(s, s.state).outcome,
            reason: :state_total
          }
        ]
    end
  end
end
