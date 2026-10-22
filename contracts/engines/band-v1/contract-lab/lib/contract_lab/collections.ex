defmodule ContractLab.Collections do
  @moduledoc "All-settled collections. A failed lane is data, not a terminal session."
  alias ContractLab.Engine

  def enabled?(c), do: Map.has_key?(c, :collections)

  def initial(c),
    do:
      if(enabled?(c),
        do: %{collections: %{}, collection_results: [], collection_log: []},
        else: %{}
      )

  def definitions(c, state),
    do: for({_, d} <- Map.get(c, :collections, %{}), d.state == state, do: d)

  def enter(s, at) do
    if enabled?(s.contract) do
      s = cancel(s, at)

      active =
        Map.new(definitions(s.contract, s.state), fn d ->
          {d.name,
           %{
             name: d.name,
             state: s.state,
             entry: s.entries[s.state],
             started_at: at,
             deadline_at: at + d.after_ms,
             closed_at: nil,
             reason: nil,
             from: d.from,
             to: d.to,
             members: Map.new(d.from, &{&1, member(s.contract, "pending", nil, nil)})
           }}
        end)

      %{s | collections: active}
    else
      s
    end
  end

  def cancel(s, at) do
    if enabled?(s.contract) do
      Enum.reduce(s.collections, s, fn {name, _}, acc ->
        finish(acc, name, at, "cancelled", false)
      end)
    else
      s
    end
  end

  def check(s, rule, role) do
    if name = Map.get(rule, :collection) do
      if get_in(s.collections, [name, :members, role, :status]) == "pending",
        do: :ok,
        else: {:error, :already_contributed}
    else
      :ok
    end
  end

  def target(s, rule, role) do
    if name = Map.get(rule, :collection) do
      members = s.collections[name].members

      if Enum.all?(members, fn {member, row} -> member == role or row.status != "pending" end),
        do: close_target(s, name, Map.update!(members, role, &%{&1 | status: rule.settlement}))
    end
  end

  # DF-25: a close where a quorum member did not settle ok enters the quorum target.
  defp close_target(s, name, members) do
    d = s.contract.collections[name]

    case Map.get(d, :quorum) do
      nil ->
        d.target

      q ->
        met =
          Enum.all?(members, fn {member, row} ->
            row.status == "ok" or not quorum_member?(s, q, member)
          end)

        if met, do: d.target, else: q.target
    end
  end

  defp quorum_member?(_, %{attribute: nil}, _), do: true

  defp quorum_member?(s, %{attribute: attribute}, member),
    do: get_in(s.contract.role_metadata, [member, :attributes, attribute]) == true

  def accept(s, rule, role, id) do
    if name = Map.get(rule, :collection) do
      s =
        put_in(
          s,
          [:collections, name, :members, role],
          member(s.contract, rule.settlement, id, s.now)
        )

      settle(s, name, s.now)
    else
      s
    end
  end

  @doc false
  def stop(s, name, role, failure) do
    row = member(s.contract, "failed", nil, s.now) |> Map.put(:failure, failure)
    s = put_in(s, [:collections, name, :members, role], row)
    s = log(s, name, s.now, "failed", role, %{"control" => failure.id})
    settle(s, name, s.now)
  end

  defp member(c, status, message, at) do
    row = %{status: status, message: message, at: at}
    if ContractLab.Contributions.enabled?(c), do: Map.put(row, :failure, nil), else: row
  end

  def deadlines(s) do
    for {name, record} <- Map.get(s, :collections, %{}),
        timer <- timers(s, name, record),
        do: timer
  end

  defp timers(s, name, record) do
    deadline = %{
      at: record.deadline_at,
      priority: 1,
      id: "collection-" <> name,
      reason: :collection,
      name: name,
      role: nil
    }

    d = s.contract.collections[name]

    absent =
      if d.offline_ms do
        for {role, %{status: "pending"}} <- record.members,
            observation = s.signals[role <> "/presence"],
            observation && observation["value"] == "offline" do
          %{
            at: max(record.started_at, observation["since"] + d.offline_ms),
            priority: 2,
            id: "offline-" <> name <> "/" <> role,
            reason: :collection,
            name: name,
            role: role
          }
        end
      else
        []
      end

    [deadline | absent]
  end

  def fire(s, %{name: name, role: nil, at: at}), do: finish(s, name, at, "deadline", true)

  def fire(s, %{name: name, role: role, at: at}) do
    s =
      put_in(s, [:collections, name, :members, role], member(s.contract, "offline", nil, at))

    s |> log(name, at, "offline", role) |> settle(name, at)
  end

  defp settle(s, name, at) do
    if Enum.all?(s.collections[name].members, fn {_, r} -> r.status != "pending" end),
      do: finish(s, name, at, "settled", true),
      else: s
  end

  defp finish(s, name, at, reason, advance) do
    record = s.collections[name]

    members =
      Map.new(record.members, fn {role, row} ->
        {role,
         if(row.status == "pending",
           do: %{
             row
             | status: if(reason == "deadline", do: "timed_out", else: "cancelled"),
               at: at
           },
           else: row
         )}
      end)

    result = %{record | members: members, closed_at: at, reason: reason}

    # A round cancelled before any reply or failure has no result to keep; its
    # collection log row still records the cancellation.
    results =
      if reason != "cancelled" or evidence?(result),
        do: s.collection_results ++ [result],
        else: s.collection_results

    s = %{s | collections: Map.delete(s.collections, name), collection_results: results}
    s = log(s, name, at, reason, nil)

    if advance do
      target =
        if capped(s.collection_results) >= s.max_messages,
          do: "completion_capacity",
          else: close_target(s, name, members)

      Engine.host_transition(%{s | obligations: []}, target, at)
    else
      s
    end
  end

  @doc """
  Rounds that count against max_messages: those that settled or reached their
  deadline. A kept cancelled round is bounded by its own reply or failure, each
  of which already consumed message or contribution-control capacity.
  """
  def capped(results), do: Enum.count(results, &(&1.reason in ["settled", "deadline"]))

  @doc "Whether a closed round holds a reply or a failure."
  def evidence?(record),
    do: Enum.any?(record.members, fn {_, row} -> row.status in ~w(ok failed) end)

  defp log(s, name, at, reason, role, extra \\ %{}) do
    row =
      Map.merge(%{"name" => name, "at" => at, "reason" => reason, "role" => role || ""}, extra)

    %{s | version: s.version + 1, collection_log: s.collection_log ++ [row]}
  end

  def duties(c, state, active \\ nil) do
    for d <- definitions(c, state) do
      remaining =
        if active,
          do: for({role, %{status: "pending"}} <- active[d.name].members, do: role),
          else: d.from

      %{
        kind: d.reply,
        participants: d.from,
        remaining: remaining,
        quantifier: :all,
        after_ms: d.after_ms
      }
    end
  end

  def view(s, role) do
    filter = fn r ->
      cond do
        role in r.to -> r
        role in r.from -> %{r | members: Map.take(r.members, [role])}
        true -> nil
      end
    end

    if enabled?(s.contract) do
      %{
        collections:
          Map.new(s.collections, fn {name, r} -> {name, filter.(r)} end)
          |> Map.reject(fn {_, v} -> is_nil(v) end),
        collection_results: Enum.map(s.collection_results, filter) |> Enum.reject(&is_nil/1)
      }
    else
      %{}
    end
  end
end
