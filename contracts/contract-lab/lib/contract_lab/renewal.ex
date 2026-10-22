defmodule ContractLab.Renewal do
  @moduledoc "Explicit clock renewal. Entry identity and accepted work never restart."
  @safe 9_007_199_254_740_991

  def enabled?(c), do: Map.get(c, :renewal_profile) == "band-contract/renewal/1"

  def initial(c, now) do
    if enabled?(c) do
      timeout = Map.get(c, :timeout, %{})
      hard = absolute(now, Map.get(timeout, :maximum_ms))
      window = absolute(now, Map.get(timeout, :after_ms))
      fields = %{clocks: %{phase_at: nil, hard_at: hard}}

      if Map.get(timeout, :profile) == "band-contract/timeout/2",
        do: Map.put(fields, :max_at, earlier(window, hard)),
        else: fields
    else
      %{}
    end
  end

  def enter(s, at) do
    if Map.has_key?(s, :clocks) do
      timeout = get_in(s.contract, [:states, s.state, :timeout])
      put_in(s, [:clocks, :phase_at], if(timeout, do: at + timeout.after_ms, else: nil))
    else
      s
    end
  end

  def phase_at(s) do
    if Map.has_key?(s, :clocks),
      do: s.clocks.phase_at,
      else: absolute(s.entered_at, get_in(s.contract, [:states, s.state, :timeout, :after_ms]))
  end

  def check(s, rule, role \\ nil) do
    case deadline(s, Map.get(rule, :reset)) do
      {:error, _} = error ->
        error

      nil ->
        :ok

      at when at > @safe ->
        {:error, :invalid_time_range}

      at ->
        scoped = get_in(rule, [:reset, :scope]) in ["rule", "collection", "wait"]

        cond do
          scoped and settled?(s, rule.reset, role) ->
            {:error, :already_contributed}

          scoped and
              (Enum.any?(
                 [s.max_at, phase_at(s), ContractLab.Totals.deadline_at(s)],
                 &(not is_nil(&1) and at > &1)
               ) or
                 not continuation_fits?(s, rule.reset, at)) ->
            {:error, :insufficient_time}

          true ->
            :ok
        end
    end
  end

  # A member that has replied or failed no longer waits, so it cannot extend the others' wait.
  # An open reply wait is never settled: its reply leaves W and ends it.
  defp settled?(_s, %{scope: "wait"}, _role), do: false

  defp settled?(s, %{scope: "collection", collection: name}, role) do
    case get_in(s, [:collections, name, :members, role]) do
      %{status: status} -> status != "pending"
      _ -> false
    end
  end

  defp settled?(s, %{scope: "rule", duty: id}, role) do
    case Enum.find(s.obligations, &(&1.id == id)) do
      %{quantifier: :all, participants: participants, remaining: remaining} ->
        role in participants and role not in remaining

      _ ->
        false
    end
  end

  # The renewed reply may arrive as late as `at`; the state it leads to must still be able to
  # give every declared duty, collection and reply wait (T1-1) its whole window before max_at.
  # A wait is not a deadline row, so it is counted from its definition. An untimed state
  # gets its state clock or, without one, the renewed timer's own window, so a renewal can never
  # leave the closing step no time at all.
  defp continuation_fits?(%{max_at: nil}, _, _), do: true

  defp continuation_fits?(s, reset, at) do
    targets =
      case reset do
        # A reply picks the state, so every alternative's target must fit (V4).
        %{scope: "wait"} ->
          ContractLab.ReplyWait.targets(s.contract, s.state)

        # A quorum (DF-25) can send the close to either target, so both must fit.
        %{scope: "collection", collection: name} ->
          d = s.contract.collections[name]
          [d.target | List.wrap(get_in(d, [:quorum, :target]))]

        %{rule: id} ->
          [Enum.find_value(s.contract.states[s.state].rules, &(&1.id == id and &1.target))]
      end

    Enum.all?(targets, &target_fits?(s, &1, at))
  end

  defp target_fits?(s, target, at) do
    case s.contract.states[target] do
      nil ->
        true

      state ->
        windows =
          Enum.map(state.deadlines, & &1.after_ms) ++
            Enum.map(ContractLab.Collections.definitions(s.contract, target), & &1.after_ms) ++
            Enum.map(ContractLab.ReplyWait.entry_duties(s.contract, target), & &1.after_ms)

        windows =
          cond do
            windows != [] -> windows
            Map.get(state, :timeout) -> [state.timeout.after_ms]
            true -> [at - s.now]
          end

        Enum.all?(windows, &(at + &1 <= s.max_at))
    end
  end

  def apply(s, rule) do
    case Map.get(rule, :reset) do
      nil ->
        s

      %{scope: "state"} = reset ->
        put_in(s, [:clocks, :phase_at], deadline(s, reset))

      %{scope: "contract"} = reset ->
        %{s | max_at: earlier(deadline(s, reset), s.clocks.hard_at)}

      %{scope: "collection", collection: name} = reset ->
        put_in(s, [:collections, name, :deadline_at], deadline(s, reset))

      %{scope: "wait"} = reset ->
        ContractLab.ReplyWait.renew(s, deadline(s, reset))

      %{scope: "rule", duty: id} = reset ->
        at = deadline(s, reset)

        %{
          s
          | obligations:
              Enum.map(s.obligations, fn d -> if d.id == id, do: %{d | due_at: at}, else: d end)
        }
    end
  end

  defp deadline(_, nil), do: nil
  defp deadline(s, %{scope: "state"}), do: s.now + s.contract.states[s.state].timeout.after_ms
  defp deadline(s, %{scope: "contract"}), do: s.now + s.contract.timeout.after_ms

  defp deadline(s, %{scope: "collection", collection: name}) do
    if Map.has_key?(s.collections, name),
      do: s.now + s.contract.collections[name].after_ms,
      else: {:error, :timer_complete}
  end

  defp deadline(s, %{scope: "wait", duty: id}), do: ContractLab.ReplyWait.renewal(s, id)

  defp deadline(s, %{scope: "rule", duty: id}) do
    case Enum.find(s.obligations, &(&1.id == id and &1.remaining != [])) do
      nil -> {:error, :timer_complete}
      duty -> s.now + duty.after_ms
    end
  end

  defp absolute(_, nil), do: nil
  defp absolute(now, ms), do: now + ms
  defp earlier(nil, b), do: b
  defp earlier(a, nil), do: a
  defp earlier(a, b), do: min(a, b)
end
