defmodule ContractLab.SafePatternTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, SafePattern}

  test "full-string validation supports bounded regular syntax and Unicode scalars" do
    for {pattern, accepted, rejected} <- [
          {"[A-Z][A-Z0-9_-]*", ["A", "ABC_2"], ["", "a", " A", "A\n"]},
          {"(cat|dog)s?", ["cat", "dogs"], ["catsx", "xcat", ""]},
          {"é+", ["é", "éé"], ["e\u0301", "e"]},
          {"[^0-9]+", ["🚀", "abc"], ["1", "a1"]},
          {"a.*b", ["ab", "a🚀b"], ["a\nb", "xab"]},
          {"a\\+b", ["a+b"], ["ab", "aaab"]}
        ] do
      assert {:ok, machine} = SafePattern.compile(pattern, 64)
      for text <- accepted, do: assert({:ok, true, _} = SafePattern.match(machine, text))
      for text <- rejected, do: assert({:ok, false, _} = SafePattern.match(machine, text))
    end
  end

  test "nested quantifiers and overlapping alternatives cannot cause runtime backtracking" do
    for pattern <- ["(a+)+b", "(a|aa)+b", "(a*)*b", "a?a?a?a?a?a?a?a?a?a?aaaaaaaaaa"] do
      assert {:ok, machine} = SafePattern.compile(pattern, 4096)

      for size <- [32, 256, 4096] do
        assert {:ok, false, %{steps: steps}} =
                 SafePattern.match(machine, String.duplicate("a", size))

        assert steps <= (size + 1) * machine.cost_per_scalar
        assert steps <= machine.max_steps
      end
    end
  end

  test "compiler bounds pattern size, nesting, automaton growth and input work" do
    for pattern <- [
          String.duplicate("a", 1025),
          String.duplicate("(", 17) <> "a" <> String.duplicate(")", 17),
          "(a|b)*a" <> String.duplicate("(a|b)", 10)
        ] do
      assert {:error, :pattern_limit} = SafePattern.compile(pattern, 100)
    end

    assert {:error, :pattern_limit} = SafePattern.compile("a*", 1_000_000)
  end

  test "unsupported or malformed syntax fails at compilation without fallback" do
    for pattern <- [
          "(a)\\1",
          "(?=a)a",
          "(?<=a)b",
          "(?i)a",
          "a{1000000000}",
          "\\p{L}",
          "a**",
          "[z-a]",
          "(",
          "a|",
          "^a$"
        ] do
      assert {:error, :pattern_syntax} = SafePattern.compile(pattern, 100), pattern
    end
  end

  test "input and execution limits fail closed" do
    assert {:ok, machine} = SafePattern.compile("a*", 4)
    assert {:error, :invalid_text} = SafePattern.match(machine, <<255>>)
    assert {:error, :invalid_text} = SafePattern.match(machine, "aaaaa")
    assert {:error, :validation_limit} = SafePattern.match(%{machine | max_steps: 1}, "aaaa")
  end

  test "small languages agree with an independent full-match oracle" do
    words =
      Enum.reduce(1..6, [""], fn _, previous ->
        Enum.uniq(previous ++ for(word <- previous, char <- ["a", "b"], do: word <> char))
      end)

    for pattern <- [
          "a",
          "a*",
          "a+",
          "a?",
          "(ab|ba)+",
          "(a|b)*",
          "a*b*",
          "(a*)*",
          "(a+)+",
          "a?b?a?",
          "[a-b]+",
          "[^a]*",
          "(a|ab)*b",
          "a.*b",
          "((ab)?b)*"
        ] do
      assert {:ok, machine} = SafePattern.compile(pattern, 32)
      # The oracle sees only fixed, short test inputs. Production never uses PCRE.
      oracle = Regex.compile!("\\A(?:" <> pattern <> ")\\z", "u")

      for word <- words do
        assert {:ok, actual, _} = SafePattern.match(machine, word)
        assert actual == Regex.match?(oracle, word), inspect({pattern, word})
      end
    end
  end

  test "all patterned fields share a compile-time validation-work ceiling" do
    {:ok, m} = SafePattern.compile("a*", 100_000)
    assert 3 * m.max_steps > SafePattern.work_limit()

    {:ok, d} =
      HeaderSpikes.discover_bytes(:frontmatter, """
      ---
      name: work-ceiling
      description: Aggregate validation work.
      engine: 0.2.0-lab.1
      roles: [sender, receiver]
      ---
      """)

    body = """
    type value
      a: text(100000, regex: ~r/a*/)
      b: text(100000, regex: ~r/a*/)
      c: text(100000, regex: ~r/a*/)
    state new
      sender -> receiver: value() => complete
    end complete
    end expired
    """

    assert {:error, %{code: :pattern_limit}} = ContractLab.Compiler.compile(d.card, body)
  end

  test "all patterns in one contract share one compile-work budget" do
    # 26 optional letters charge 179,172 of the 200,000 per-pattern units.
    pattern = Enum.map_join(?a..?z, &<<&1, ??>>)

    compile = fn count ->
      types =
        Enum.map_join(1..count, fn i -> "type t#{i}\n  f: text(16, regex: ~r/#{pattern}/)\n" end)

      bytes =
        """
        ---
        name: compile-budget
        description: Share compile work across types.
        engine: 0.2.0-lab.1
        roles: [sender, receiver]
        ---
        """ <>
          types <>
          "state new\n  sender -> receiver: t1() => complete\nend complete\nend expired\n"

      bytes = ContractLab.TestClock.ensure(bytes)

      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })
    end

    assert {:ok, _} = SafePattern.compile(pattern, 16)
    assert {:ok, c} = compile.(5)
    assert map_size(c.types) == 5
    assert {:error, %{code: :pattern_limit, line: 12}} = compile.(6)
    assert {:error, %{code: :pattern_limit, line: 12}} = compile.(40)
  end

  test "field validation combines bytes, graphemes and a compiled pattern" do
    bytes = """
    ---
    name: patterned-text
    description: Validate an agent's code.
    engine: 0.2.0-lab.1
    roles: [sender, receiver]
    ---
    type value
      code: text(8, graphemes: 4, regex: ~r/[A-Z]+/)
    state new
      sender -> receiver: value() => complete
    end complete
    end expired
    """

    bytes = ContractLab.TestClock.ensure(bytes)

    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => bytes,
               "sha256" => Canonical.hash(bytes)
             })

    assert Engine.validate_payload(c.types["value"], %{"code" => "ABCD"})

    for text <- ["ABCDE", "abc", "", "A\n", "Ａ"] do
      refute Engine.validate_payload(c.types["value"], %{"code" => text})
    end

    assert c.types["value"].fields["code"].regex.profile == "band-contract/pattern-dfa/1"
  end
end
