defmodule ContractLab.Prompts do
  @moduledoc """
  The prompt-carrying items of a compiled contract that have no prompt (`meta.doc`): the
  contract itself, every role, every message type and every state (Eric, 10-15). Lines are
  compiler lines; a role with no `role` or roster line reports the compiler's default, 1.
  Participant types, outcomes, fields and rules are not required to carry one.
  """

  def missing(contract) do
    ([{"contract", contract.name, contract.meta}] ++
       for(role <- contract.roles, do: {"role", role, contract.role_metadata[role].meta}) ++
       for({name, type} <- Enum.sort(contract.types), do: {"type", name, type.meta}) ++
       for({name, state} <- Enum.sort(contract.states), do: {"state", name, state.meta}))
    |> Enum.reject(fn {_, _, meta} -> prompt?(meta) end)
    |> Enum.map(fn {item, name, meta} -> %{item: item, name: name, line: meta.line} end)
    |> Enum.sort_by(& &1.line)
  end

  defp prompt?(%{doc: doc}) when is_binary(doc), do: String.trim(doc) != ""
  defp prompt?(_), do: false
end
