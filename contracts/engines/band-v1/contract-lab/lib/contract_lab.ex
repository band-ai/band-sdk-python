defmodule ContractLab do
  @moduledoc "Experimental Elixir contract compiler and metadata-preserving queries."
  alias ContractLab.Compiler

  def load(path, format \\ :frontmatter) do
    with {:ok, discovery} <-
           HeaderSpikes.discover(format, path, engines: Compiler.engines())
           |> Compiler.engine_error()
           |> Compiler.header_error(fn -> header_text(path, format) end),
         {:ok, source} <- HeaderSpikes.file_source(discovery),
         do: compile_source(source)
  end

  def compile(_card, _body), do: {:error, :file_source_required}

  # Only the bounded prefix, through the reader that refuses symlinks, FIFOs and devices.
  defp header_text(path, format) do
    case HeaderSpikes.Prefix.read(path, format, 4096) do
      {:ok, prefix} -> prefix.raw
      _ -> nil
    end
  end

  @doc "Compile the strict, JSON-compatible normalized source defined by the portable protocol."
  def compile_source(source), do: ContractLab.Source.compile(source)
  def query(contract, selector \\ :contract)
  def query(contract, :contract), do: {:ok, contract}

  def query(contract, {:type, name}),
    do: lookup(Map.merge(contract.types, Map.get(contract, :participant_types, %{})), name)

  def query(contract, {:state, name}), do: lookup(contract.states, name)
  def query(contract, {:outcome, name}), do: lookup(contract.terminals, name)
  def query(contract, {:role, name}), do: lookup(contract.role_metadata, name)
  def query(contract, :room), do: {:ok, %{rules: contract.room_rules, meta: contract.room_meta}}

  def query(contract, :any_state),
    do: {:ok, %{rules: contract.any_rules, meta: contract.any_meta}}

  def query(_, _), do: {:error, :not_found}

  def for_role(c, role) do
    if role in c.roles do
      states =
        Map.new(c.states, fn {name, state} ->
          {name,
           ContractLab.ReplyWait.definition_view(c, %{
             state
             | rules: Enum.filter(state.rules, &involves?(&1, role)),
               deadlines: Enum.filter(state.deadlines, &(role in &1.participants))
           })}
        end)

      {:ok,
       %{
         contract:
           Map.take(c, [
             :name,
             :engine,
             :digest,
             :source_digest,
             :meta,
             :card,
             :extensions,
             :timeout,
             :renewal_profile,
             :role_sets,
             :participant_types,
             :contribution_profile,
             :collections
           ]),
         role: c.role_metadata[role],
         types: c.types,
         states: states,
         terminals: c.terminals,
         any_rules: Enum.filter(c.any_rules, &involves?(&1, role)),
         any_meta: c.any_meta,
         room_rules: Enum.filter(c.room_rules, &involves?(&1, role)),
         room_meta: c.room_meta
       }}
    else
      {:error, :unknown_role}
    end
  end

  defp involves?(rule, role), do: role in rule.from or role in rule.to

  defp lookup(map, key) do
    case Map.fetch(map, key) do
      {:ok, value} -> {:ok, value}
      :error -> {:error, :not_found}
    end
  end
end
