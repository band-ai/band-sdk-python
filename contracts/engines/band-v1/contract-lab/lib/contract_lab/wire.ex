defmodule ContractLab.Wire do
  @moduledoc "Strict JSON normalization; actor identity always remains outside the wire document."
  alias ContractLab.Canonical
  @fields ~w(id session kind payload expected_version)
  @carrier_fields ~w(id session kind expected_version)
  @safe_integer 9_007_199_254_740_991

  @doc "Bounded strict JSON for service requests, without envelope-specific fields."
  def parse_json(bytes, limit)
      when is_binary(bytes) and is_integer(limit) and limit > 0 and byte_size(bytes) <= limit,
      do: parse(bytes)

  def parse_json(_, _), do: {:error, :message_size}

  @doc """
  Normalizes a body with optional transport-supplied envelope fields.

  `carrier:` is a string-keyed `band-contract/carrier/1` map with `name` and
  `fields`. It maps id/session/kind/expected_version to `body` (the default) or
  a binding-defined source token. `transport:` supplies typed values keyed by
  those non-body tokens. Payload and authenticated actor identity cannot be
  mapped. With no `carrier:` option, legacy id/session-keyed transport values
  retain their existing behavior.

  Invalid maps or undeclared sources return `:transport_fields`, differing
  copies return `:transport_conflict`, and missing/invalid envelope fields
  return `:envelope`. Numeric values retain the strict JSON number domain.
  """
  def decode(bytes, opts \\ []) do
    limit = Keyword.get(opts, :max_bytes, 16_384)

    with true <-
           (is_binary(bytes) and is_integer(limit) and limit > 0 and byte_size(bytes) <= limit) ||
             {:error, :message_size},
         {:ok, transport} <- transport_fields(opts),
         {:ok, parsed} <- parse_json(bytes, limit),
         true <-
           (is_map(parsed) and Enum.all?(Map.keys(parsed), &(&1 in @fields))) ||
             {:error, :envelope},
         :ok <- consistent(parsed, transport),
         message = Map.merge(parsed, transport),
         true <- valid?(message) || {:error, :envelope},
         true <- byte_size(Canonical.encode(message)) <= limit || {:error, :message_size} do
      {:ok, message}
    else
      {:error, reason} -> {:error, reason}
    end
  end

  @doc "Encodes a normalized message, omitting only fields actually supplied by its binding."
  def encode(message, opts \\ []) do
    with {:ok, transport} <- transport_fields(opts),
         true <- valid?(message) || {:error, :envelope},
         :ok <- consistent(message, transport),
         true <-
           Enum.all?(transport, fn {k, v} -> message[k] == v end) || {:error, :transport_conflict} do
      bytes = Canonical.encode(Map.drop(message, Map.keys(transport)))

      case decode(bytes, opts) do
        {:ok, ^message} -> {:ok, bytes}
        error -> error
      end
    else
      {:error, reason} -> {:error, reason}
    end
  rescue
    FunctionClauseError -> {:error, :unsupported_number}
    Protocol.UndefinedError -> {:error, :envelope}
    ArgumentError -> {:error, :envelope}
  end

  defp valid?(m) when is_map(m) do
    Enum.all?(Map.keys(m), &(&1 in @fields)) and
      Enum.all?(~w(id kind), &(is_binary(m[&1]) and byte_size(m[&1]) > 0)) and
      is_map(m["payload"]) and (not Map.has_key?(m, "session") or is_binary(m["session"])) and
      (not Map.has_key?(m, "expected_version") or
         (is_integer(m["expected_version"]) and m["expected_version"] >= 0))
  end

  defp valid?(_), do: false

  defp transport_fields(opts) do
    supplied = Keyword.get(opts, :transport, %{})

    case Keyword.fetch(opts, :carrier) do
      :error -> legacy_transport(supplied)
      {:ok, map} -> mapped_transport(map, supplied)
    end
  end

  defp legacy_transport(m) when is_map(m) do
    cond do
      not Enum.all?(m, fn {k, v} ->
        k in ["id", "session"] and is_binary(v) and byte_size(v) > 0
      end) ->
        {:error, :transport_fields}

      Enum.any?(Map.values(m), &(not String.valid?(&1))) ->
        {:error, :json}

      true ->
        {:ok, m}
    end
  end

  defp legacy_transport(_), do: {:error, :transport_fields}

  defp mapped_transport(
         %{"profile" => "band-contract/carrier/1", "name" => name, "fields" => fields} = map,
         supplied
       )
       when map_size(map) == 3 and is_binary(name) and byte_size(name) > 0 and is_map(fields) and
              is_map(supplied) do
    valid_fields =
      String.valid?(name) and
        Enum.all?(fields, fn {field, source} ->
          field in @carrier_fields and is_binary(source) and byte_size(source) > 0 and
            String.valid?(source)
        end)

    sources = fields |> Map.values() |> Enum.reject(&(&1 == "body"))

    cond do
      not valid_fields or not Enum.all?(Map.keys(supplied), &(&1 in sources)) ->
        {:error, :transport_fields}

      Enum.any?(Map.values(supplied), &(is_binary(&1) and not String.valid?(&1))) ->
        {:error, :json}

      Enum.any?(Map.values(supplied), fn value ->
        is_float(value) or (is_integer(value) and abs(value) > @safe_integer)
      end) ->
        {:error, :unsupported_number}

      true ->
        transport =
          for {field, source} <- fields,
              source != "body",
              Map.has_key?(supplied, source),
              into: %{},
              do: {field, Map.fetch!(supplied, source)}

        {:ok, transport}
    end
  end

  defp mapped_transport(_, _), do: {:error, :transport_fields}

  defp consistent(m, supplied) do
    if Enum.all?(supplied, fn {k, v} -> not Map.has_key?(m, k) or m[k] == v end),
      do: :ok,
      else: {:error, :transport_conflict}
  end

  defp parse(bytes) do
    decoders = %{
      object_finish: fn pairs, old ->
        keys = Enum.map(pairs, &elem(&1, 0))
        if length(keys) != length(Enum.uniq(keys)), do: throw(:duplicate_key)
        {Map.new(pairs), old}
      end,
      float: fn _ -> {:unsupported_float, true} end,
      integer: fn text ->
        value = String.to_integer(text)
        if abs(value) > @safe_integer, do: throw(:unsupported_number)
        value
      end
    }

    case JSON.decode(bytes, nil, Map.to_list(decoders)) do
      {decoded, nil, rest} ->
        cond do
          unsupported_float?(decoded) -> {:error, :unsupported_number}
          String.trim(rest) == "" -> {:ok, decoded}
          true -> {:error, :json}
        end

      {:error, _} ->
        {:error, :json}
    end
  rescue
    _ -> {:error, :json}
  catch
    :duplicate_key -> {:error, :duplicate_key}
    :unsupported_number -> {:error, :unsupported_number}
  end

  defp unsupported_float?({:unsupported_float, true}), do: true
  defp unsupported_float?(v) when is_map(v), do: Enum.any?(Map.values(v), &unsupported_float?/1)
  defp unsupported_float?(v) when is_list(v), do: Enum.any?(v, &unsupported_float?/1)
  defp unsupported_float?(_), do: false
end
