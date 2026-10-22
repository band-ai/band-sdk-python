defmodule ContractLab.PatternLiteralTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  test "raw sigils preserve quotes and regex escapes without JSON escaping" do
    for {literal, accepted, rejected} <- [
          {~S'~r/"yes"/', ~s("yes"), "yes"},
          {~S"~r/a\+b/", "a+b", "aaab"},
          {~S"~r/a\nb/", "a\nb", "anb"},
          {~S"~r|a\\|", "a\\", "a"},
          {~S"~r/#\{value\}/", ~S"#{value}", "value"}
        ] do
      assert {:ok, c} = compile(literal), literal
      assert Engine.validate_payload(c.types["value"], %{"code" => accepted}), literal
      refute Engine.validate_payload(c.types["value"], %{"code" => rejected}), literal
    end
  end

  test "delimiter choices accommodate paths, alternatives and character classes" do
    for {literal, accepted, rejected} <- [
          {~S"~r|/api/[a-z]+|", "/api/items", "/api/ITEMS"},
          {~S"~r{/api/(cat|dog)}", "/api/dog", "/api/bird"},
          {~S"~r/(cat|dog)/", "cat", "bird"},
          {~S"~r|[/]|", "/", "x"},
          {~S"~r/[\/]/", "/", "x"},
          {~S"~r|a\|b|", "a|b", "a"},
          {~S"~r/a\\\/b/", "a\\/b", "a/b"},
          {~S"~r{a\}b}", "a}b", "ab"},
          {~S"~r/é🚀/", "é🚀", "e🚀"}
        ] do
      assert {:ok, c} = compile(literal), literal
      assert Engine.validate_payload(c.types["value"], %{"code" => accepted}), literal
      refute Engine.validate_payload(c.types["value"], %{"code" => rejected}), literal
    end
  end

  test "a pattern is written only as ~r; uppercase sigils and quoted strings were removed" do
    pattern = ~S'a\+"b"'
    assert {:ok, sigil} = compile("~r/" <> pattern <> "/")
    assert sigil.types["value"].fields["code"].regex.source == pattern

    for {literal, hint} <- [
          {"~R/" <> pattern <> "/", "Sigils are lowercase"},
          {Canonical.encode(pattern), "Quoted regexes were removed"}
        ] do
      assert {:error, %{code: :syntax, line: 2, detail: detail}} = compile(literal)
      assert detail =~ hint
    end
  end

  test "only a complete supported literal is accepted, with no modifiers or interpolation" do
    for literal <- [
          "~r/foo",
          "~r/foo/i",
          "~r/foo/ trailing",
          "~r/foo/bar/",
          "~r/foo/ , regex: ~r/bar/",
          "~r!foo!",
          "~r//",
          ~S"~r/a\/",
          "~r|[/]|i",
          "~r/[/]/",
          "~r{a}b}",
          ~S"~r/#{value}/",
          ~S"~r/(?=a)a/",
          ~S"~r/\d+/"
        ] do
      assert {:error, %{code: :pattern_syntax}} = compile(literal), literal
    end
  end

  test "raw pattern bytes and aggregate execution work remain bounded" do
    assert {:error, %{code: :pattern_limit}} = compile("~r/" <> String.duplicate("é", 513) <> "/")
    assert {:error, %{code: :pattern_limit}} = compile("~r/a*/", "1000000")
  end

  test "sigils still require every byte and grapheme constraint to pass" do
    assert {:ok, c} = compile("~r/é+/", "8, graphemes: 3")
    assert Engine.validate_payload(c.types["value"], %{"code" => "ééé"})
    refute Engine.validate_payload(c.types["value"], %{"code" => "éééé"})
    refute Engine.validate_payload(c.types["value"], %{"code" => <<255>>})
    assert {:ok, c} = compile("~r/é+/", "5")
    refute Engine.validate_payload(c.types["value"], %{"code" => "ééé"})
  end

  defp compile(literal, bounds \\ "64") do
    source = """
    ---
    name: raw-pattern
    description: A raw pattern literal.
    engine: 0.2.0-lab.1
    roles: [sender, receiver]
    ---
    type value
      code: text(#{bounds}, regex: #{literal})
    state new
      sender -> receiver: value() => complete
    end complete
    """

    source = ContractLab.TestClock.ensure(source)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => source,
      "sha256" => Canonical.hash(source)
    })
  end
end
