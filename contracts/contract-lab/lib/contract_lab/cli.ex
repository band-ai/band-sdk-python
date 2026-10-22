defmodule ContractLab.CLI do
  @moduledoc "Shared implementation of contract check, query and replay commands."
  alias ContractLab.{Canonical, Compiler, Scenario}
  @formats %{"frontmatter" => :frontmatter, "standard" => :standard}
  @selectors [:role, :type, :state, :outcome, :room, :any_state]

  def run([command | args]) do
    {opts, paths, invalid} =
      OptionParser.parse(args,
        strict: [
          format: :string,
          role: :string,
          type: :string,
          state: :string,
          outcome: :string,
          room: :boolean,
          any_state: :boolean,
          observe: :boolean,
          require_prompts: :boolean
        ]
      )

    format = @formats[Keyword.get(opts, :format, "frontmatter")]
    selections = Enum.filter(opts, fn {key, _} -> key in @selectors end)

    cond do
      invalid != [] or is_nil(format) ->
        {:error, :invalid_options}

      length(selections) > 1 or (command != "query" and selections != []) or
        (command != "replay" and Keyword.has_key?(opts, :observe)) or
          (command != "check" and Keyword.has_key?(opts, :require_prompts)) ->
        {:error, :conflicting_options}

      true ->
        flags = %{
          observe: Keyword.get(opts, :observe, false),
          require_prompts: Keyword.get(opts, :require_prompts, false)
        }

        execute(command, paths, selections, format, flags)
    end
  rescue
    error -> {:error, Exception.message(error)}
  end

  def run(_), do: {:error, :usage}

  defp execute("check", [_ | _] = paths, [], format, %{observe: false} = flags) do
    Enum.reduce_while(paths, {:ok, []}, fn path, {:ok, result} ->
      with {:ok, c} <- ContractLab.load(path, format),
           :ok <- prompts(c, path, format, flags.require_prompts) do
        entry =
          c
          |> Map.take([:name, :engine, :digest, :source_digest])
          |> Map.put(:admission_limits, ContractLab.AdmissionLimits.report(c))

        entry = if flags.require_prompts, do: Map.put(entry, :prompts, "complete"), else: entry
        {:cont, {:ok, [normalize(entry) | result]}}
      else
        {:error, {:prompts_required, missing}} ->
          {:halt, {:error, %{path: path, reason: %{code: :prompts_required, missing: missing}}}}

        {:error, reason} ->
          {:halt, {:error, located(path, format, reason)}}
      end
    end)
    |> case do
      {:ok, results} -> {:ok, Enum.reverse(results)}
      error -> error
    end
  end

  defp execute("query", [path], selections, format, %{observe: false}) do
    with {:ok, contract} <- load(path, format),
         {:ok, result} <- query(contract, selections),
         do: {:ok, normalize(result)}
  end

  defp execute("replay", [path, scenario], [], format, %{observe: observe}) do
    with {:ok, contract} <- load(path, format),
         {:ok, bytes} <- File.read(scenario),
         {:ok, spec} <- JSON.decode(bytes) do
      IO.puts(
        :stderr,
        "warning: replay runs in the unchecked room_policy mode; it is not evidence of host membership or permission checks"
      )

      Scenario.run(contract, spec, observe: observe)
    end
  end

  defp execute(_, _, _, _, _), do: {:error, :usage}

  defp load(path, format) do
    case ContractLab.load(path, format) do
      {:error, reason} -> {:error, located(path, format, reason)}
      loaded -> loaded
    end
  end

  # DF-10: a compiler error's `line` is the body line the spec defines; `file_line` counts from
  # the top of the file so an author can jump to the mistake. Header and whole-file codes
  # already report a file line.
  @file_codes ~w(engine unknown_field duplicate_field missing_field header field_value input source_size)a

  # --require-prompts: every missing prompt, with the compiler line and the file line.
  defp prompts(_contract, _path, _format, false), do: :ok

  defp prompts(contract, path, format, true) do
    case ContractLab.Prompts.missing(contract) do
      [] ->
        :ok

      missing ->
        {:error,
         {:prompts_required,
          Enum.map(missing, &Map.put(&1, :file_line, file_line(path, format, :prompt, &1.line)))}}
    end
  end

  defp located(path, format, %{code: code, line: line} = reason) when is_integer(line) do
    %{path: path, reason: reason, file_line: file_line(path, format, code, line)}
  end

  defp located(path, _format, reason), do: %{path: path, reason: reason}

  defp file_line(_path, _format, code, line) when code in @file_codes, do: line

  defp file_line(path, format, _code, line) do
    case HeaderSpikes.discover(format, path, engines: Compiler.engines()) do
      # The body follows the header in the same file.
      {:ok, %{reference: nil, prefix: prefix}} ->
        line + length(:binary.matches(prefix, "\n"))

      # A standard package's body is its own file, so its body line is already a file line.
      _ ->
        line
    end
  end

  defp query(contract, []), do: ContractLab.query(contract)
  defp query(contract, [{:role, role}]), do: ContractLab.for_role(contract, role)

  defp query(contract, [{selector, true}]) when selector in [:room, :any_state],
    do: ContractLab.query(contract, selector)

  defp query(contract, [{selector, name}]) when selector in [:type, :state, :outcome],
    do: ContractLab.query(contract, {selector, name})

  defp query(_, _), do: {:error, :invalid_selector}
  defp normalize(value), do: value |> Canonical.encode() |> JSON.decode!()
end
