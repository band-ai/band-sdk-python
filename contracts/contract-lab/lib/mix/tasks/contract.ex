defmodule ContractLab.MixCommand do
  @moduledoc """
  Runs one contract CLI command. Success prints JSON on stdout. Failure prints one JSON object,
  `{"error": {"code": ..., ...}}`, on stderr and exits with status 1, so agents and gates
  branch on `code` rather than on prose (DF-09). A compiler error keeps its `line` and
  `detail` and adds the `path`.
  """
  def run(command, args) do
    Mix.Task.run("app.start")

    case ContractLab.CLI.run([command | args]) do
      {:ok, result} ->
        Mix.shell().info(JSON.encode!(result))

      {:error, reason} ->
        IO.puts(:stderr, JSON.encode!(%{"error" => error_object(reason)}))
        exit({:shutdown, 1})
    end
  end

  @doc false
  def error_object(%{path: path, reason: reason} = failure) do
    reason
    |> error_object()
    |> Map.put("path", path)
    |> Map.merge(
      Map.new(Map.drop(failure, [:path, :reason]), fn {k, v} -> {to_string(k), jsonable(v)} end)
    )
  end

  def error_object(%{code: code} = error) do
    Map.new(error, fn {k, v} -> {to_string(k), jsonable(v)} end)
    |> Map.put("code", to_string(code))
  end

  def error_object(%{"error" => code} = report) when is_binary(code),
    do: %{"code" => code, "detail" => jsonable(Map.delete(report, "error"))}

  def error_object(code) when is_atom(code), do: %{"code" => Atom.to_string(code)}

  def error_object(message) when is_binary(message),
    do: %{"code" => "exception", "detail" => message}

  def error_object(reason) when is_tuple(reason) and is_atom(elem(reason, 0)) do
    [code | rest] = Tuple.to_list(reason)
    %{"code" => Atom.to_string(code), "detail" => jsonable(rest)}
  end

  def error_object(reason), do: %{"code" => "error", "detail" => inspect(reason)}

  defp jsonable(value) when is_boolean(value) or is_nil(value) or is_number(value), do: value
  defp jsonable(value) when is_atom(value), do: Atom.to_string(value)

  defp jsonable(value) when is_binary(value),
    do: if(String.valid?(value), do: value, else: inspect(value))

  defp jsonable(value) when is_list(value), do: Enum.map(value, &jsonable/1)
  defp jsonable(value) when is_tuple(value), do: value |> Tuple.to_list() |> jsonable()

  defp jsonable(value) when is_map(value) and not is_struct(value),
    do: Map.new(value, fn {k, v} -> {to_string(k), jsonable(v)} end)

  defp jsonable(value), do: inspect(value)
end

defmodule Mix.Tasks.Contract.Check do
  use Mix.Task

  @shortdoc "Compile contracts: mix contract.check FILE... [--format frontmatter|standard] [--require-prompts]"
  def run(args), do: ContractLab.MixCommand.run("check", args)
end

defmodule Mix.Tasks.Contract.Query do
  use Mix.Task

  @shortdoc "Query documented JSON: mix contract.query FILE [--role NAME|--type NAME|--state NAME|--outcome NAME|--room|--any-state]"
  def run(args), do: ContractLab.MixCommand.run("query", args)
end

defmodule Mix.Tasks.Contract.Replay do
  use Mix.Task
  @shortdoc "Replay a trace: mix contract.replay [--observe] CONTRACT SCENARIO.json"
  def run(args), do: ContractLab.MixCommand.run("replay", args)
end

defmodule Mix.Tasks.Contract.HostKey do
  use Mix.Task

  @usage "mix contract.host_key adopt JOURNAL [--journal-max-bytes BYTES]"
  @shortdoc "Commit a stopped journal to its host key, creating one if missing: #{@usage}"
  def run(["adopt" | args]) do
    case OptionParser.parse(args, strict: [journal_max_bytes: :integer]) do
      {opts, [path], []} ->
        Mix.Task.run("app.start")

        case ContractLab.Replacement.adopt_host_key(path, opts) do
          {:ok, result} -> Mix.shell().info(JSON.encode!(result))
          {:error, reason} -> Mix.raise("Host key adopt failed: #{inspect(reason)}")
        end

      _ ->
        run([])
    end
  end

  def run(_), do: Mix.raise("Usage: #{@usage}")
end
