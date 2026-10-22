defmodule ContractLab.BandKeywordsTest do
  # op-sitehl: the spec site's highlighter (spec/tools/bandhighlight.py) must mark exactly the
  # keywords this compiler accepts. spec/tools/band-keywords.json records them; the site build
  # and spec/tools/test_band_highlight.py hold the highlighter to the record, and this test holds
  # the record to the compiler, so a keyword added here (like total and quorum) fails until the
  # highlighter learns it.
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @tools Path.expand("../../spec/tools", __DIR__)
  @record_path Path.join(@tools, "band-keywords.json")
  @compiler_path Path.expand("../lib/contract_lab/compiler.ex", __DIR__)
  @external_resource @record_path
  @external_resource @compiler_path

  defp record, do: @record_path |> File.read!() |> :json.decode()

  defp compile(bytes) do
    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  # The compiler's reserved words: its declaration keywords, its line keywords, its rule options
  # and its collect-line words. These are the lists its uppercase and removed-form checks use.
  defp reserved do
    source = File.read!(@compiler_path)

    lists = [
      ~r/@keywords ~w\(([^)]*)\)/,
      ~r/@line_keywords ~r\/ \(([A-Z_|]+)\)/,
      ~r/when word in ~w\(([A-Z ]+)\) do\s+fail\(:syntax, line, "Rule options are lowercase/,
      ~r/~r\/\^\(([a-z|]+)\) \(\.\+\)\$\//
    ]

    for pattern <- lists, reduce: MapSet.new() do
      acc ->
        assert [_, words] = Regex.run(pattern, source),
               "compiler.ex no longer has #{inspect(pattern)}"

        words
        |> String.downcase()
        |> String.split(["|", " "], trim: true)
        |> MapSet.new()
        |> MapSet.union(acc)
    end
  end

  test "the record is the compiler's reserved words, minus removed forms, plus its connector words" do
    r = record()
    removed = r["removed"] |> Map.keys() |> MapSet.new()
    connectors = r["connectors"] |> Map.keys() |> MapSet.new()

    assert MapSet.subset?(removed, reserved())

    expected = reserved() |> MapSet.difference(removed) |> MapSet.union(connectors)
    assert MapSet.new(r["keywords"]) == expected
  end

  test "each connector word is a literal of the compiler's grammar" do
    source = File.read!(@compiler_path)

    for {word, _construct} <- record()["connectors"] do
      assert Regex.match?(~r/(~r\/[^\n]*\b#{word}\b|"#{word}")/, source),
             "#{word} is not in compiler.ex"
    end
  end

  test "the probe compiles and uses every recorded keyword" do
    probe = File.read!(Path.join(@tools, record()["probe"]))
    assert {:ok, _} = compile(probe)
    words = ~r/[a-z_]+/ |> Regex.scan(probe) |> List.flatten() |> MapSet.new()
    assert MapSet.difference(MapSet.new(record()["keywords"]), words) == MapSet.new()
  end

  test "each removed keyword is rejected as a syntax error" do
    for {word, source} <- record()["removed"] do
      assert {:error, %{code: :syntax}} = compile(source), "#{word} still compiles"
      # Without its line the probe compiles, so the rejection is the removed keyword's.
      control =
        source
        |> String.split("\n")
        |> Enum.reject(&String.starts_with?(String.trim_leading(&1), word <> " "))
        |> Enum.join("\n")

      assert {:ok, _} = compile(control), "the #{word} probe fails without its #{word} line"
    end
  end
end
