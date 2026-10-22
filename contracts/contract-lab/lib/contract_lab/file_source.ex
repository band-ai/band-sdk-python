defmodule ContractLab.FileSource do
  @moduledoc "Exact UTF-8 file bytes identify a session type in engine 0.2.0-lab.1."
  alias ContractLab.{Canonical, Compiler}
  @formats %{"native" => :native, "frontmatter" => :frontmatter, "standard" => :standard}
  @keys ~w(profile format content sha256)
  @max_body 262_144
  @max_entry 266_240

  def compile(source) do
    with :ok <- shape(source),
         bytes <- source["content"],
         true <- byte_size(bytes) <= @max_entry || {:error, :source_size},
         true <- String.valid?(bytes) || {:error, :invalid_utf8},
         true <- Canonical.hash(bytes) == source["sha256"] || {:error, :file_digest_mismatch},
         format <- @formats[source["format"]],
         {:ok, d} <-
           HeaderSpikes.discover_bytes(format, bytes,
             package_name: source["package_name"],
             engines: Compiler.engines()
           )
           |> Compiler.engine_error()
           |> Compiler.header_error(bytes),
         true <- byte_size(bytes) - d.body_offset <= @max_body || {:error, :source_size},
         {:ok, body} <- body(source, d),
         {:ok, contract} <- Compiler.compile(d.card, body) do
      {:ok, %{contract | source_digest: source["sha256"]}}
    end
  end

  defp shape(source) when is_map(source) do
    keys = if source["format"] == "standard", do: @keys ++ ~w(body package_name), else: @keys

    if Enum.sort(Map.keys(source)) == Enum.sort(keys) and
         source["profile"] == "band-contract/file-source/1" and
         Map.has_key?(@formats, source["format"]) and is_binary(source["content"]) and
         is_binary(source["sha256"]) and Regex.match?(~r/\A[0-9a-f]{64}\z/, source["sha256"]) do
      :ok
    else
      {:error, :input}
    end
  end

  defp shape(_), do: {:error, :input}

  defp body(source, %{reference: nil, body_offset: offset}) do
    bytes = source["content"]
    {:ok, binary_part(bytes, offset, byte_size(bytes) - offset)}
  end

  defp body(%{"body" => bytes}, %{reference: reference}) when is_binary(bytes) do
    cond do
      byte_size(bytes) > @max_body -> {:error, :source_size}
      not String.valid?(bytes) -> {:error, :invalid_utf8}
      Canonical.hash(bytes) != reference.sha256 -> {:error, :digest_mismatch}
      true -> {:ok, bytes}
    end
  end

  defp body(_, _), do: {:error, :input}
end
