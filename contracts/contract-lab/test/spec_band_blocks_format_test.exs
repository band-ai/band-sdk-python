defmodule ContractLab.SpecBandBlocksFormatTest do
  # op-promptdocs: the ```band examples on the spec site are shown as `mix contract.format`
  # writes them, so a reader can copy one into a .band file and it stays formatted.
  use ExUnit.Case, async: true
  alias ContractLab.Format

  @spec_dir Path.expand("../../spec", __DIR__)
  # conformance/format/ holds the formatter's own input/expected fixtures, not examples.
  @pages Path.join(@spec_dir, "**/*.md")
         |> Path.wildcard()
         |> Enum.reject(&String.starts_with?(&1, Path.join(@spec_dir, "conformance/format/")))
  for page <- @pages, do: @external_resource(page)

  # The body of each top-level ```band fence, with its page and opening line.
  defp band_blocks do
    for page <- @pages,
        {block, line} <- fences(File.read!(page)),
        do: {Path.relative_to(page, @spec_dir), line, block}
  end

  defp fences(text) do
    text
    |> String.split("\n")
    |> Enum.with_index(1)
    |> Enum.reduce({[], nil}, fn
      {"```band", n}, {found, nil} ->
        {found, {n, []}}

      {"```", _}, {found, {n, lines}} ->
        {[{Enum.join(Enum.reverse(lines), "\n") <> "\n", n} | found], nil}

      {line, _}, {found, {n, lines}} ->
        {found, {n, [line | lines]}}

      _, acc ->
        acc
    end)
    |> elem(0)
    |> Enum.reverse()
  end

  test "the spec site shows at least 38 band examples" do
    assert length(band_blocks()) >= 38
  end

  test "every band example is already formatted" do
    unformatted =
      for {page, line, block} <- band_blocks(),
          {:ok, formatted} = Format.format(block),
          formatted != block,
          do: "#{page}:#{line}"

    assert unformatted == []
  end
end
