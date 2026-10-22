defmodule ContractLab.RoomPolicy do
  @moduledoc "Host-supplied agent membership, role assignments and an optional Band permission binding."
  @levels %{"participant" => 0, "admin" => 1, "owner" => 2}

  # Route each sender/recipient pair. Adding a human recipient does not exempt
  # another agent's delivery from an independently selected contract session.
  def lane(:agent, :agent), do: :agent_exchange
  def lane(from, to) when from in [:human, :agent] and to in [:human, :agent], do: :conversation
  def lane(_, _), do: {:error, :unknown_principal_kind}

  def supported?(c, capabilities) when is_list(capabilities),
    do: Enum.all?(Map.get(c.card, :extensions, []), &(&1 in capabilities))

  def supported?(_, _), do: false

  # Host evidence is required for every current contract. The
  # explicit :unchecked mode is only reachable at session start through a
  # local-reference adapter (HostInputs.new_opts/3); hosted adapters never build it.
  def bind(_, _, nil), do: {:error, :host_policy_required}
  def bind(_, _, :unchecked), do: :ok

  def bind(c, bindings, %{revision: revision, principals: principals, fixed_roles: fixed})
      when is_binary(revision) and byte_size(revision) > 0 and is_map(principals) and
             is_map(fixed) do
    bindings
    |> Enum.sort()
    |> Enum.reduce_while(:ok, fn {role, actor}, _ ->
      result = binding(c, role, actor, principals[actor], fixed)
      if result == :ok, do: {:cont, :ok}, else: {:halt, result}
    end)
  end

  def bind(_, _, _), do: {:error, :invalid_room_policy}

  # A checked session needs fresh, well-formed host evidence on every member call, before
  # any receipt lookup, so a caller without it learns nothing; :unchecked is start-time only.
  def evidence(policy, mode) do
    cond do
      mode != :unchecked and policy in [nil, :unchecked] -> {:error, :host_policy_required}
      policy in [nil, :unchecked] or well_formed?(policy) -> :ok
      true -> {:error, :invalid_room_policy}
    end
  end

  defp well_formed?(policy), do: bind(%{}, %{}, policy) == :ok

  # Member operations (step, view, stop_contribution). Every bound principal must still be
  # an active member on each call (D-15a P11), not only the caller.
  def authorize(c, bindings, actor, policy, mode \\ :checked) do
    with :ok <- evidence(policy, mode) do
      cond do
        actor not in Map.values(bindings) -> {:error, :unauthorized}
        mode == :unchecked and policy in [nil, :unchecked] -> :ok
        true -> bind(c, bindings, policy)
      end
    end
  end

  def mode(state), do: Map.get(state, :policy_mode, :checked)

  defp binding(_, _, _, %{kind: kind}, _) when kind != :agent,
    do: {:error, :agent_binding_required}

  defp binding(c, role, actor, %{kind: :agent, active: true, permission: level}, fixed) do
    permissions = c |> Map.get(:extensions, %{}) |> Map.get(:permissions, %{})
    required = Map.get(permissions, role, "participant")

    cond do
      Map.has_key?(fixed, role) and fixed[role] != actor -> {:error, :role_assignment}
      not Map.has_key?(@levels, level) -> {:error, :room_permission}
      @levels[level] < @levels[required] -> {:error, :room_permission}
      true -> :ok
    end
  end

  defp binding(_, _, _, _, _), do: {:error, :room_access}
end
