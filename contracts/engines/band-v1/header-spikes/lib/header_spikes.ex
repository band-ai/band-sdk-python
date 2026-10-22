defmodule HeaderSpikes do
  @moduledoc """
  Bounded discovery adapters and immutable source selection for the contract compiler.
  """

  alias HeaderSpikes.{FileSnapshot, Prefix, Schema, Yaml}
  @max_body_bytes 262_144

  @fields ~w(name description engine version encoding extensions roles)
  @removed_fields ~w(max-duration lifetime inactivity-timeout mentions-per-message mentions-per-session)
  @accepted_fields @fields ++ @removed_fields
  @metadata_keys Enum.map(@fields -- ["name", "description"], &("band-" <> &1)) ++
                   ["band-contract", "band-contract-sha256"]

  def discover(format, path, opts \\ [])
  def discover(:native, _path, _opts), do: {:error, :frontmatter_required}

  def discover(format, path, opts) do
    max_bytes = Keyword.get(opts, :max_bytes, 4096)

    with true <- format in [:frontmatter, :standard] || {:error, :invalid_format},
         {:ok, prefix} <- Prefix.read(path, format, max_bytes),
         {:ok, fields, reference} <- parse(format, prefix.content, Keyword.get(opts, :engines)),
         {:ok, card} <- Schema.card(fields, Keyword.get(opts, :engines)),
         :ok <- check_directory(format, path, card.name) do
      {:ok,
       %{
         format: format,
         path: Path.expand(path),
         card: card,
         reference: reference,
         prefix: prefix.raw,
         identity: prefix.identity,
         body_offset: prefix.bytes_read,
         bytes_read: prefix.bytes_read,
         contract_bytes_read: 0
       }}
    end
  end

  def load_contract(discovery) do
    with {:ok, prefix} <- Prefix.read(discovery.path, discovery.format, 4096),
         true <- prefix.raw == discovery.prefix || {:error, :header_changed},
         true <- prefix.identity == discovery.identity || {:error, :source_changed} do
      load_body(discovery)
    end
  end

  @doc "Parse a bounded in-memory entry file without normalizing its bytes."
  def discover_bytes(format, bytes, opts \\ [])
  def discover_bytes(:native, _bytes, _opts), do: {:error, :frontmatter_required}

  def discover_bytes(format, bytes, opts) do
    with true <- format in [:frontmatter, :standard] || {:error, :invalid_format},
         {:ok, prefix} <- Prefix.parse(bytes, format),
         {:ok, fields, reference} <- parse(format, prefix.content, Keyword.get(opts, :engines)),
         {:ok, card} <- Schema.card(fields, Keyword.get(opts, :engines)),
         true <-
           (format != :standard or Keyword.get(opts, :package_name) == card.name) ||
             {:error, :name_directory_mismatch} do
      {:ok,
       %{card: card, reference: reference, body_offset: prefix.bytes_read, prefix: prefix.raw}}
    end
  end

  @doc "Capture the exact entry bytes and any digest-pinned referenced body."
  def file_source(d) do
    with {:ok, content, identity} <- read_source(d.path, d.body_offset + @max_body_bytes),
         true <- identity == d.identity || {:error, :source_changed},
         true <- String.starts_with?(content, d.prefix) || {:error, :header_changed} do
      source = %{
        "profile" => "band-contract/file-source/1",
        "format" => Atom.to_string(d.format),
        "content" => content,
        "sha256" => digest(content)
      }

      if d.reference do
        with {:ok, body} <- load_body(d),
             do: {:ok, Map.merge(source, %{"body" => body, "package_name" => d.card.name})}
      else
        {:ok, source}
      end
    end
  end

  defp parse(:frontmatter, content, engines) do
    with {:ok, fields} <-
           Yaml.parse(content, &Schema.engines_first(engine_values(&1, :frontmatter), engines)),
         :ok <- Schema.allowed(fields, @fields ++ @removed_fields) do
      {:ok, fields, nil}
    end
  end

  defp parse(:standard, content, engines) do
    with {:ok, fields} <-
           Yaml.parse(content, &Schema.engines_first(engine_values(&1, :standard), engines)),
         :ok <-
           Schema.allowed(fields, [
             "name",
             "description",
             "metadata",
             "license",
             "compatibility",
             "allowed-tools"
           ]),
         metadata when is_map(metadata) <- Map.get(fields, "metadata"),
         true <-
           Enum.all?(metadata, fn {k, v} -> is_binary(k) and is_binary(v) end) ||
             {:error, :metadata_must_be_strings},
         :ok <-
           Schema.allowed(
             Map.filter(metadata, fn {k, _} -> String.starts_with?(k, "band-") end),
             @metadata_keys ++ Enum.map(@removed_fields, &("band-" <> &1))
           ),
         {:ok, reference} <- Schema.reference(metadata) do
      common = Map.take(fields, ["name", "description"])

      common =
        Enum.reduce(metadata, common, fn
          {"band-" <> key, value}, acc when key in @accepted_fields -> Map.put(acc, key, value)
          _, acc -> acc
        end)

      {:ok, common, reference}
    else
      nil -> {:error, {:missing_field, "metadata"}}
      value when not is_tuple(value) -> {:error, :metadata_must_be_strings}
      error -> error
    end
  end

  # Every raw engine value, repeats included, whatever its YAML type (Omar set P P-11).
  defp engine_values(document, :frontmatter),
    do: for({"engine", value} <- pairs(document), do: value)

  defp engine_values(document, :standard),
    do:
      for({"metadata", meta} <- pairs(document), {"band-engine", value} <- pairs(meta), do: value)

  defp pairs(value) when is_list(value), do: Enum.filter(value, &match?({_, _}, &1))
  defp pairs(_value), do: []

  defp check_directory(:standard, path, name) do
    if Path.basename(Path.dirname(Path.expand(path))) == name,
      do: :ok,
      else: {:error, :name_directory_mismatch}
  end

  defp check_directory(_, _, _), do: :ok

  defp load_body(%{reference: nil} = d) do
    with {:ok, source, identity} <- read_source(d.path, d.body_offset + @max_body_bytes),
         true <- identity == d.identity || {:error, :source_changed},
         true <- String.starts_with?(source, d.prefix) || {:error, :header_changed} do
      {:ok, binary_part(source, d.body_offset, byte_size(source) - d.body_offset)}
    end
  end

  defp load_body(%{reference: reference} = d) do
    base = Path.dirname(d.path)

    with :ok <- safe_components(base, Path.split(reference.path)),
         {:ok, body} <- read_reference(Path.join(base, reference.path)),
         :ok <- safe_components(base, Path.split(reference.path)),
         true <- digest(body) == reference.sha256 || {:error, :digest_mismatch} do
      {:ok, body}
    end
  end

  defp safe_components(_base, []), do: :ok

  defp safe_components(base, [part | rest]) do
    path = Path.join(base, part)

    case File.lstat(path) do
      {:ok, %{type: :symlink}} -> {:error, :unsafe_contract_path}
      {:ok, %{type: :directory}} when rest != [] -> safe_components(path, rest)
      {:ok, %{type: :regular}} when rest == [] -> :ok
      {:ok, _} -> {:error, :unsafe_contract_path}
      {:error, reason} -> {:error, {:contract_io, reason}}
    end
  end

  defp read_reference(path) do
    case read_source(path, @max_body_bytes) do
      {:ok, body, _identity} -> {:ok, body}
      error -> error
    end
  end

  defp read_source(path, limit) do
    case FileSnapshot.read(path, limit) do
      {:error, {:file_io, reason}} -> {:error, {:contract_io, reason}}
      result -> result
    end
  end

  defp digest(body), do: :crypto.hash(:sha256, body) |> Base.encode16(case: :lower)
end
