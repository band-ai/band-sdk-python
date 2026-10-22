defmodule HeaderSpikes.Graphemes do
  @moduledoc "Unicode 16.0.0 extended grapheme boundaries, independent of host Unicode tables."

  # UAX #29 revision 45, GB1–GB999. Data and its license are pinned in priv/unicode16.
  # Compile immutable sorted ranges once; each scalar uses three binary searches.
  root = Path.expand("../../priv/unicode16", __DIR__)

  parse = fn path, select ->
    path
    |> File.read!()
    |> String.split("\n")
    |> Enum.flat_map(fn line ->
      fields = line |> String.split("#", parts: 2) |> hd() |> String.split(";")

      case Enum.map(fields, &String.trim/1) do
        [range | properties] when properties != [] ->
          case select.(properties) do
            nil ->
              []

            property ->
              bounds = String.split(range, "..")

              [
                {String.to_integer(hd(bounds), 16), String.to_integer(List.last(bounds), 16),
                 property}
              ]
          end

        _ ->
          []
      end
    end)
    |> Enum.sort()
    |> List.to_tuple()
  end

  @external_resource Path.relative_to_cwd(Path.join(root, "GraphemeBreakProperty.txt"))
  @external_resource Path.relative_to_cwd(Path.join(root, "DerivedCoreProperties.txt"))
  @external_resource Path.relative_to_cwd(Path.join(root, "emoji-data.txt"))
  @gcb parse.(Path.join(root, "GraphemeBreakProperty.txt"), fn [property] -> property end)
  @incb parse.(Path.join(root, "DerivedCoreProperties.txt"), fn
          ["InCB", property] -> property
          _ -> nil
        end)
  @ep parse.(Path.join(root, "emoji-data.txt"), fn
        ["Extended_Pictographic"] -> true
        _ -> nil
      end)
  @initial {nil, 0, false, false, 0}

  def version, do: "16.0.0"

  def count(text) when is_binary(text),
    do: fold(text, @initial, 0, fn boundary, n -> if boundary, do: n + 1, else: n end)

  @doc "Boundary offsets measured in Unicode scalar values, including start and end."
  def boundaries(text) when is_binary(text) do
    {size, reverse} =
      fold(text, @initial, {0, []}, fn boundary, {offset, positions} ->
        {offset + 1, if(boundary, do: [offset | positions], else: positions)}
      end)

    Enum.reverse([size | reverse])
  end

  defp fold(<<>>, _context, result, _update), do: result

  defp fold(<<cp::utf8, rest::binary>>, {previous, ri, ep_suffix, zwj_ep, indic}, result, update) do
    current = lookup(@gcb, cp, "Other")
    conjunct = lookup(@incb, cp, "None")
    pictograph = lookup(@ep, cp, false)

    boundary =
      cond do
        previous == nil ->
          true

        previous == "CR" and current == "LF" ->
          false

        previous in ["Control", "CR", "LF"] ->
          true

        current in ["Control", "CR", "LF"] ->
          true

        previous == "L" and current in ["L", "V", "LV", "LVT"] ->
          false

        previous in ["LV", "V"] and current in ["V", "T"] ->
          false

        previous in ["LVT", "T"] and current == "T" ->
          false

        current in ["Extend", "ZWJ", "SpacingMark"] ->
          false

        previous == "Prepend" ->
          false

        conjunct == "Consonant" and indic == 2 ->
          false

        pictograph and zwj_ep ->
          false

        previous == "Regional_Indicator" and current == "Regional_Indicator" and rem(ri, 2) == 1 ->
          false

        true ->
          true
      end

    next_indic =
      case conjunct do
        "Consonant" -> 1
        "Extend" -> indic
        "Linker" when indic > 0 -> 2
        _ -> 0
      end

    next = {
      current,
      if(current == "Regional_Indicator", do: ri + 1, else: 0),
      pictograph or (current == "Extend" and ep_suffix),
      current == "ZWJ" and ep_suffix,
      next_indic
    }

    fold(rest, next, update.(boundary, result), update)
  end

  defp fold(_, _, _, _), do: raise(ArgumentError, "invalid UTF-8")

  defp lookup(ranges, cp, default), do: search(ranges, cp, 0, tuple_size(ranges) - 1, default)
  defp search(_ranges, _cp, low, high, default) when low > high, do: default

  defp search(ranges, cp, low, high, default) do
    middle = div(low + high, 2)
    {first, last, property} = elem(ranges, middle)

    cond do
      cp < first -> search(ranges, cp, low, middle - 1, default)
      cp > last -> search(ranges, cp, middle + 1, high, default)
      true -> property
    end
  end
end
