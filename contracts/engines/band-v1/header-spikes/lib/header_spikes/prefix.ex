defmodule HeaderSpikes.Prefix do
  @moduledoc "A hard cap on application bytes read, including delimiters. No read-ahead."

  def read(path, format, limit) when is_integer(limit) and limit > 0 and limit <= 4096 do
    result =
      HeaderSpikes.FileSnapshot.with_file(path, fn fd, _ ->
        scan(fd, format, limit, <<>>, <<>>, 0)
      end)

    case result do
      {:ok, prefix, identity} -> {:ok, Map.put(prefix, :identity, identity)}
      {:error, {:file_io, reason}} -> {:error, {:header_io, reason}}
      error -> error
    end
  end

  def read(_, _, _), do: {:error, :invalid_header_limit}

  def parse(bytes, format, limit \\ 4096)
      when is_binary(bytes) and is_integer(limit) and limit > 0 and limit <= 4096,
      do: scan({:bytes, bytes}, format, limit, <<>>, <<>>, 0)

  defp scan(_fd, _format, limit, raw, _line, _count) when byte_size(raw) >= limit,
    do: {:error, :header_too_large}

  defp scan(fd, format, limit, raw, line, count) do
    case next_byte(fd) do
      {:ok, "\n", next} ->
        raw = raw <> "\n"
        # Only one trailing CR belongs to a CRLF break; any other CR is its own YAML line
        # break (Sofia set Q Q-L4), so a delimiter line is exactly "---" plus at most one CR.
        text = strip_one_cr(line)

        cond do
          count == 0 and text != "---" ->
            {:error, :missing_frontmatter}

          count > 0 and text == "---" ->
            # The exact bytes between the delimiter lines, so YAML sees every original break.
            [opening | _] = :binary.split(raw, "\n")
            first = byte_size(opening) + 1
            content = binary_part(raw, first, byte_size(raw) - first - byte_size(line) - 1)

            if String.valid?(content) do
              {:ok, %{content: content, raw: raw, bytes_read: byte_size(raw)}}
            else
              {:error, :invalid_utf8}
            end

          true ->
            scan(next, format, limit, raw, <<>>, count + 1)
        end

      {:ok, byte, next} ->
        scan(next, format, limit, raw <> byte, line <> byte, count)

      :eof ->
        {:error, :unterminated_header}

      {:error, reason} ->
        {:error, {:header_io, reason}}
    end
  end

  defp strip_one_cr(line) do
    if String.ends_with?(line, "\r"), do: binary_part(line, 0, byte_size(line) - 1), else: line
  end

  defp next_byte({:bytes, <<>>}), do: :eof
  defp next_byte({:bytes, <<byte, rest::binary>>}), do: {:ok, <<byte>>, {:bytes, rest}}

  defp next_byte(fd) do
    case :file.read(fd, 1) do
      {:ok, byte} -> {:ok, byte, fd}
      result -> result
    end
  end
end
