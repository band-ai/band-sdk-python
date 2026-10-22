defmodule HeaderSpikes.Yaml do
  @moduledoc "YAML through pinned yaml_elixir/yamerl, with explicit discovery-profile restrictions."

  @doc """
  Parse a header. `precheck` sees the raw key/value document (every repeat kept) before
  duplicate detection, so a caller can refuse an unsupported engine first.
  """
  def parse(content, precheck \\ fn _ -> :ok end) do
    # Parse tokens first, without constructing/expanding nodes. YAML anchors,
    # aliases and explicit tags are outside this intentionally small profile.
    :yamerl_parser.string(content,
      token_fun: fn token ->
        if elem(token, 0) in [:yamerl_anchor, :yamerl_alias, :yamerl_tag, :yamerl_tag_directive] or
             explicit_tag?(token),
           do: throw(:unsupported_yaml_feature)

        :ok
      end
    )

    case YamlElixir.read_all_from_string(content, maps_as_keywords: true, atoms: false) do
      {:ok, [document]} ->
        with :ok <- precheck.(document) do
          result = normalize(document)
          if is_map(result), do: {:ok, result}, else: {:error, :invalid_yaml}
        end

      _ ->
        {:error, :invalid_yaml}
    end
  catch
    :unsupported_yaml_feature -> {:error, :unsupported_yaml_feature}
    {:duplicate_field, field} -> {:error, {:duplicate_field, field}}
    _, _ -> {:error, :invalid_yaml}
  end

  @doc """
  Every mapping key as `{depth, mapping, parent, key, line}` in document order, repeats
  included. `mapping` identifies the enclosing mapping and `parent` is the key whose value it
  is (nil at the top); `line` is the key node's 1-based YAML line (LF, CRLF or CR breaks);
  top-level keys have depth 1.
  """
  def key_lines(content) do
    ref = make_ref()
    Process.put(ref, %{stack: [], next: 0, expecting: false, last: nil, parents: %{}, keys: []})

    try do
      :yamerl_parser.string(content,
        token_fun: fn token ->
          Process.put(ref, key_step(token, Process.get(ref)))
          :ok
        end
      )

      Enum.reverse(Process.get(ref).keys)
    catch
      _, _ -> []
    after
      Process.delete(ref)
    end
  end

  defp key_step(token, state) do
    case {elem(token, 0), state.stack} do
      {:yamerl_collection_start, stack} ->
        parent = if stack == [], do: nil, else: state.last

        %{
          state
          | stack: [state.next | stack],
            next: state.next + 1,
            expecting: false,
            parents: Map.put(state.parents, state.next, parent)
        }

      {:yamerl_collection_end, [_ | rest]} ->
        %{state | stack: rest, expecting: false}

      {:yamerl_mapping_key, _} ->
        %{state | expecting: true}

      {:yamerl_scalar, [mapping | _] = stack} when state.expecting ->
        key = scalar_text(token)
        entry = {length(stack), mapping, state.parents[mapping], key, elem(token, 1)}
        %{state | expecting: false, last: key, keys: [entry | state.keys]}

      _ ->
        %{state | expecting: false}
    end
  end

  defp scalar_text(token), do: token |> elem(tuple_size(token) - 1) |> List.to_string()

  defp explicit_tag?(token) when elem(token, 0) in [:yamerl_scalar, :yamerl_collection_start] do
    {:yamerl_tag, line, column, uri} = elem(token, 3)
    is_list(uri) or line != elem(token, 1) or column != elem(token, 2)
  end

  defp explicit_tag?(_), do: false

  defp normalize(value) when is_list(value) do
    if value != [] and Enum.all?(value, &match?({_, _}, &1)) do
      Enum.reduce(value, %{}, fn {key, val}, acc ->
        if not is_binary(key), do: throw(:invalid_yaml)
        if Map.has_key?(acc, key), do: throw({:duplicate_field, key})
        Map.put(acc, key, normalize(val))
      end)
    else
      Enum.map(value, &normalize/1)
    end
  end

  defp normalize(value), do: value
end
