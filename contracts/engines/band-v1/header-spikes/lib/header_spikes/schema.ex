defmodule HeaderSpikes.Schema do
  @moduledoc "Common discovery-card semantics, independent of the authoring format."
  @safe_integer 9_007_199_254_740_991

  def allowed(fields, allowed) when is_map(fields) do
    case Enum.sort(Map.keys(fields) -- allowed) do
      [] -> :ok
      [key | _] -> {:error, {:unknown_field, key}}
    end
  end

  def allowed(_, _), do: {:error, :invalid_fields}

  @doc """
  An engine the caller does not implement is refused before any other header field is checked
  (spec README; Ada's set N decision B1), whatever its YAML type and across repeated keys
  (Omar set P P-11). An absent engine is left to the field rules.
  """
  def engines_first(values, engines) when is_list(engines) do
    # find_index, not find: a null engine is itself unsupported (Sofia set Q Q-L5).
    case Enum.find_index(values, &(&1 not in engines)) do
      nil -> :ok
      index -> {:error, {:unsupported_engine, Enum.at(values, index)}}
    end
  end

  def engines_first(_values, _engines), do: :ok

  @doc """
  Validate discovery fields. `engines`, when given, lists the identifiers the caller
  implements; any other present engine is refused before any field rule is applied.
  """
  def card(fields, engines \\ nil) do
    with :ok <- supported(fields, engines),
         :ok <- current_engine(fields),
         :ok <- required(fields),
         {:ok, fields} <- defaults(fields),
         :ok <- version_field(fields),
         :ok <- encoding_field(fields),
         {:ok, extensions} <- extensions(fields),
         :ok <- valid_name(fields["name"]),
         :ok <- valid_description(fields["description"]),
         :ok <- engine(fields["engine"]),
         {:ok, roles} <- roles(fields["roles"]),
         {:ok, lifetime} <- duration(fields["lifetime"], :invalid_lifetime),
         {:ok, inactivity} <- duration(fields["inactivity-timeout"], :invalid_inactivity_timeout) do
      card = %{
        name: fields["name"],
        description: fields["description"],
        engine: fields["engine"],
        roles: roles,
        lifetime: lifetime,
        inactivity_timeout: inactivity
      }

      {:ok,
       Map.merge(card, %{version: fields["version"], encoding: "utf-8", extensions: extensions})}
    end
  end

  defp supported(%{"engine" => engine}, engines) when is_list(engines) do
    if engine in engines, do: :ok, else: {:error, {:unsupported_engine, engine}}
  end

  defp supported(_, _), do: :ok

  defp version_field(%{"engine" => "0.2.0-lab.1", "version" => version}) do
    case engine(version) do
      :ok -> :ok
      _ -> {:error, :invalid_version}
    end
  end

  defp version_field(%{"version" => _}), do: {:error, {:unknown_field, "version"}}
  defp version_field(_), do: :ok

  defp encoding_field(%{"engine" => "0.2.0-lab.1"} = fields) do
    if Map.get(fields, "encoding", "utf-8") == "utf-8",
      do: :ok,
      else: {:error, :unsupported_encoding}
  end

  defp encoding_field(%{"encoding" => _}), do: {:error, {:unknown_field, "encoding"}}
  defp encoding_field(_), do: :ok

  defp extensions(%{"engine" => "0.2.0-lab.1"} = fields) do
    values = Map.get(fields, "extensions", [])

    values =
      if is_binary(values),
        do: String.split(values, ",") |> Enum.map(&String.trim/1),
        else: values

    if is_list(values) and length(values) == length(Enum.uniq(values)) and
         Enum.all?(
           values,
           &(&1 in [
               "band.permissions/1",
               "host.presence/1",
               "host.membership/1",
               "room.contributions/1"
             ])
         ),
       do: {:ok, Enum.sort(values)},
       else: {:error, :unsupported_extension}
  end

  defp extensions(%{"extensions" => _}), do: {:error, {:unknown_field, "extensions"}}
  defp extensions(_), do: {:ok, []}

  defp current_engine(fields) do
    case Map.get(fields, "engine") do
      nil -> {:error, {:missing_field, "engine"}}
      "0.2.0-lab.1" -> :ok
      value -> with :ok <- engine(value), do: {:error, :unsupported_engine}
    end
  end

  defp required(fields) do
    required = ~w(name description engine roles)

    case Enum.find(required, &(not Map.has_key?(fields, &1))) do
      nil -> :ok
      key -> {:error, {:missing_field, key}}
    end
  end

  # Removed on 0.2.0: the whole-run clock is the body `contract @…` line,
  # mention caps are `mentions N` on the contract line or a rule, and there
  # is no idle timer.
  @removed_v2_fields ~w(max-duration lifetime mentions-per-message mentions-per-session inactivity-timeout)

  defp defaults(%{"engine" => "0.2.0-lab.1"} = fields) do
    cond do
      removed = Enum.find(@removed_v2_fields, &Map.has_key?(fields, &1)) ->
        {:error, {:unknown_field, removed}}

      true ->
        with {:ok, _roles} <- roles(fields["roles"]) do
          {:ok,
           fields
           |> Map.put("lifetime", "infinite")
           |> Map.put("inactivity-timeout", "infinite")}
        end
    end
  end

  defp valid_name(name) when is_binary(name) do
    if byte_size(name) <= 64 and Regex.match?(~r/^[a-z0-9]+(?:-[a-z0-9]+)*$/, name),
      do: :ok,
      else: {:error, :invalid_name}
  end

  defp valid_name(_), do: {:error, :invalid_name}

  defp valid_description(description) when is_binary(description) do
    if String.valid?(description) and
         HeaderSpikes.Graphemes.count(String.trim(description)) in 1..1024 do
      :ok
    else
      {:error, :invalid_description}
    end
  end

  defp valid_description(_), do: {:error, :invalid_description}

  defp engine(value) when is_binary(value) do
    core = value |> String.split(["-", "+"], parts: 2) |> hd()

    if Regex.match?(~r/^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/, core) and
         match?({:ok, _}, Version.parse(value)),
       do: :ok,
       else: {:error, :invalid_engine}
  end

  defp engine(_), do: {:error, :invalid_engine}

  defp roles(value) when is_binary(value),
    do: roles(String.split(value, ",") |> Enum.map(&String.trim/1))

  defp roles(value) when is_list(value) and value != [] do
    if Enum.all?(value, &(valid_name(&1) == :ok)) and length(Enum.uniq(value)) == length(value),
      do: {:ok, value},
      else: {:error, :invalid_roles}
  end

  defp roles(_), do: {:error, :invalid_roles}

  defp duration("infinite", _error), do: {:ok, :infinite}

  defp duration(value, error) when is_binary(value) do
    case Regex.run(~r/^([1-9][0-9]*)(s|m|h|d)$/, value) do
      [_, number, unit] ->
        seconds =
          String.to_integer(number) * %{"s" => 1, "m" => 60, "h" => 3600, "d" => 86400}[unit]

        if seconds <= @safe_integer,
          do: {:ok, {:seconds, seconds}},
          else: {:error, error}

      _ ->
        {:error, error}
    end
  end

  defp duration(_, error), do: {:error, error}

  def reference(metadata) do
    path = metadata["band-contract"]
    digest = metadata["band-contract-sha256"]

    cond do
      not is_binary(path) or not Regex.match?(~r/^[a-zA-Z0-9_\/-]+\.band$/, path) or
        Path.type(path) != :relative or Enum.any?(Path.split(path), &(&1 in [".", ".."])) ->
        {:error, :invalid_contract_path}

      not is_binary(digest) or not Regex.match?(~r/^[a-f0-9]{64}$/, digest) ->
        {:error, :invalid_contract_digest}

      true ->
        {:ok, %{path: path, sha256: digest}}
    end
  end
end
