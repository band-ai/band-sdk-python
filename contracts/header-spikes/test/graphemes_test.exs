defmodule HeaderSpikes.GraphemesTest do
  use ExUnit.Case, async: true
  alias HeaderSpikes.Graphemes
  @data Path.expand("../priv/unicode16", __DIR__)

  test "all official Unicode 16 extended grapheme boundary vectors" do
    vectors =
      Path.join(@data, "GraphemeBreakTest.txt")
      |> File.stream!()
      |> Enum.with_index(1)
      |> Enum.reject(fn {line, _} ->
        String.trim(line) == "" or String.starts_with?(line, "#")
      end)

    assert length(vectors) == 1093

    for {line, number} <- vectors do
      tokens = line |> String.split("#", parts: 2) |> hd() |> String.split()

      {reversed, positions, _} =
        Enum.reduce(tokens, {[], [], 0}, fn
          "÷", {scalars, breaks, n} -> {scalars, [n | breaks], n}
          "×", state -> state
          hex, {scalars, breaks, n} -> {[String.to_integer(hex, 16) | scalars], breaks, n + 1}
        end)

      text = reversed |> Enum.reverse() |> List.to_string()
      expected = Enum.reverse(positions)
      assert Graphemes.boundaries(text) == expected, "Unicode corpus line #{number}"
      assert Graphemes.count(text) == length(expected) - 1, "Unicode corpus line #{number}"
    end
  end

  test "pinned source bytes match their provenance manifest" do
    manifest = @data |> Path.join("manifest.json") |> File.read!() |> JSON.decode!()
    assert manifest["unicode"] == Graphemes.version()

    for item <- manifest["sources"] do
      digest = :crypto.hash(:sha256, File.read!(Path.join(@data, item["path"])))
      assert Base.encode16(digest, case: :lower) == item["sha256"]
    end
  end

  test "empty and malformed input" do
    assert Graphemes.count("") == 0
    assert Graphemes.boundaries("") == [0]
    assert_raise ArgumentError, fn -> Graphemes.count(<<255>>) end
  end
end
