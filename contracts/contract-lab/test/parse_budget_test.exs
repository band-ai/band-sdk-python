defmodule ContractLab.ParseBudgetTest do
  @moduledoc """
  Parse-time budgets on untrusted source (Eric 10-15, op-hesv2ykw; Sofia SECURITY-U.md U2):
  any 250 KB line compiles and formats within min(250 ms, max(10x a same-size plain line, 50 ms))
  (Sofia U7, Lead decision 8); doubling an input at most multiplies its time by 2.5, and a 1 MB pathological file is formatted, or refused by the compiler, in
  under 1 s. Timings are the minimum of several runs in a synchronous module, so concurrent
  tests do not count against them; ratios allow 10 ms of timer noise.
  """
  use ExUnit.Case, async: false
  alias ContractLab.{Canonical, Format}

  @header "---\nname: budget\ndescription: Parse budgets.\nengine: 0.2.0-lab.1\nroles: [a, b, c]\n---\n"
  @pre "contract @1h => expired\ntype k\ntype r\n  request: ref(k)\nstate new\n"
  @ends "end s\nend t\nend done\nend stalled\n"

  defp compile(body) do
    bytes = @header <> body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  # The fastest of `runs` timed calls, each in a fresh process, so heap growth and collections
  # left over from building the inputs or from earlier runs are not counted.
  defp best_ms(runs, fun) do
    timed(fun)
    Enum.map(1..runs, fn _ -> timed(fun) end) |> Enum.min() |> Kernel./(1000)
  end

  defp timed(fun), do: Task.async(fn -> elem(:timer.tc(fun), 0) end) |> Task.await(:infinity)

  # Both sizes timed alternately, so load from other processes on the host falls on both.
  defp best_pair(runs, small, large) do
    {timed(small), timed(large)}

    1..runs
    |> Enum.map(fn _ -> {timed(small), timed(large)} end)
    |> Enum.unzip()
    |> then(fn {a, b} -> {Enum.min(a) / 1000, Enum.min(b) / 1000} end)
  end

  defp rep(unit, bytes), do: String.duplicate(unit, max(div(bytes, byte_size(unit)), 1))

  defp lines(fun, bytes) do
    Stream.iterate(0, &(&1 + 1))
    |> Stream.map(fun)
    |> Enum.reduce_while({[], 0}, fn l, {acc, n} ->
      if n >= bytes, do: {:halt, acc}, else: {:cont, {[l | acc], n + byte_size(l)}}
    end)
    |> Enum.reverse()
    |> IO.iodata_to_binary()
  end

  @kb250 250_000

  # One pathological line each, ~250 KB, in the context where the parser reads it.
  @line_shapes_names [
    "head x: y(",
    "head unclosed call",
    "head long options",
    "timed event x: y(",
    "unclosed ~s(",
    "unclosed ~w[ and ~s quote",
    "closed sigils",
    "rule args",
    "rule options",
    "timed rule @",
    "colons",
    "arrows",
    "pipes and ampersands",
    "one-line prompt",
    "prompt escapes",
    "unterminated prompt",
    "enum",
    "enum ~w",
    "role set",
    "role set ~w",
    "spaced role set",
    "alternative args",
    "alternative options",
    "contract mentions",
    "state clauses",
    "collect",
    "tabs and spaces"
  ]

  defp line_shapes do
    [
      {"head x: y(",
       fn n ->
         @pre <> "  a -> b: " <> rep("x: y(", n) <> "\n    => r() & done\n     | @5m & stalled\n"
       end},
      {"head unclosed call",
       fn n ->
         @pre <> "  a -> b: k(" <> rep("x: y", n) <> "\n    => r() & done\n     | @5m & stalled\n"
       end},
      {"head long options",
       fn n ->
         @pre <>
           "  a -> b: k() " <> rep("as: y(", n) <> "\n    => r() & done\n     | @5m & stalled\n"
       end},
      {"timed event x: y(",
       fn n -> @pre <> "  event a -> b: " <> rep("x: y(", n) <> " @5m => stalled\n" end},
      {"unclosed ~s(", fn n -> @pre <> "  a -> b: k() " <> rep("~s(", n) <> "\n" end},
      {"unclosed ~w[ and ~s quote",
       fn n -> @pre <> "  a -> b: k() " <> rep("~w[ ~s\" ", n) <> "\n" end},
      {"closed sigils", fn n -> @pre <> "  a -> b: k() " <> rep("~s/x/ ~r|y| ", n) <> "\n" end},
      {"rule args", fn n -> @pre <> "  a -> b: k(" <> rep("f=$x, ", n) <> ") => s\n" end},
      {"rule options", fn n -> @pre <> "  a -> b: k() " <> rep("as x ", n) <> "=> s\n" end},
      {"timed rule @", fn n -> @pre <> "  a -> b: k()" <> rep(" @1m", n) <> " => s | t\n" end},
      {"colons", fn n -> @pre <> "  a" <> rep(": ", n) <> "\n" end},
      {"arrows", fn n -> @pre <> "  a" <> rep("->=>", n) <> "\n" end},
      {"pipes and ampersands", fn n -> @pre <> "  a -> b: k() =>" <> rep(" | & ", n) <> "\n" end},
      {"one-line prompt",
       fn n -> @pre <> "  \"" <> rep("a", n) <> "\"\n  a -> b: k() => s\n" end},
      {"prompt escapes",
       fn n -> @pre <> "  \"" <> rep("\\a", n) <> "\"\n  a -> b: k() => s\n" end},
      {"unterminated prompt", fn n -> @pre <> "  \"" <> rep("\\\"a", n) <> "\n" end},
      {"enum",
       fn n -> "contract @1h => expired\ntype k\n  f: enum(" <> rep("a|", n) <> "b)\n" end},
      {"enum ~w",
       fn n -> "contract @1h => expired\ntype k\n  f: enum(~w[" <> rep("a ", n) <> "])\n" end},
      {"role set", fn n -> @pre <> "  {" <> rep("a,", n) <> "b} -> b: k() => s\n" end},
      {"role set ~w", fn n -> @pre <> "  ~w[" <> rep("a ", n) <> "] -> b: k() => s\n" end},
      {"spaced role set", fn n -> @pre <> "  {" <> rep("a a ", n) <> "\n" end},
      {"alternative args",
       fn n -> @pre <> "  a -> b: k()\n    => r(" <> rep("a=b, ", n) <> ") & done\n" end},
      {"alternative options",
       fn n -> @pre <> "  a -> b: k()\n    => r() " <> rep("as ", n) <> "& done\n" end},
      {"contract mentions",
       fn n -> "contract @1h" <> rep(" mentions 1", n) <> " => expired\n" end},
      {"state clauses",
       fn n -> "contract @1h => expired\nstate new" <> rep(" total @1m => s", n) <> "\n" end},
      {"collect",
       fn n -> @pre <> "  collect r from " <> rep("b ", n) <> "-> a @5m => done\n" end},
      {"tabs and spaces", fn n -> @pre <> "  a" <> rep(" \t  \t", n) <> "-> b: k() => s\n" end}
    ]
  end

  # Sofia U7 and Lead decision 8 (09:19Z, as Felix's suite encodes it): a fixed "< 50 ms"
  # flakes under gate load, so an evil line is held to min(250 ms, max(10 x plain, 50 ms)),
  # where plain is a same-size rule line with word options, timed in the same run.
  describe "any 250 KB line is within min(250 ms, max(10x a same-size plain line, 50 ms))" do
    for name <- @line_shapes_names do
      @name name
      test name do
        body = Map.new(line_shapes())[@name].(@kb250) <> @ends
        plain = @pre <> "  a -> b: k() " <> rep("x ", @kb250) <> "=> s\n" <> @ends

        for {label, fun} <- [{"compile", &compile/1}, {"format", &Format.format(@header <> &1)}] do
          base = best_ms(5, fn -> fun.(plain) end)
          evil = best_ms(5, fn -> fun.(body) end)
          allowed = min(250, max(10 * base, 50))

          assert evil <= allowed,
                 "#{label}: #{evil} ms, plain line #{base} ms, allowed #{allowed} ms"
        end
      end
    end
  end

  # Many lines; each doubles from 64 KB to 128 KB.
  @file_shapes_names [
    "rules in one state",
    "timed rules in one state",
    "@self renewals",
    "anystate rules across states",
    "reply blocks among rules",
    "chain of wait states",
    "types with fields",
    "one-line prompts",
    "orphan pipes",
    "blank lines"
  ]

  defp file_shapes do
    [
      {"rules in one state", fn n -> @pre <> lines(&"  a -> b: k#{&1}() => s\n", n) end},
      {"timed rules in one state",
       fn n -> @pre <> lines(&"  a -> b: k#{&1}() @1m => s | t\n", n) end},
      {"@self renewals", fn n -> @pre <> lines(&"  event a -> b: k#{&1}() => @self\n", n) end},
      {"anystate rules across states",
       fn n ->
         half = div(n, 2)

         @pre <>
           lines(&"state s#{&1}\n", half) <>
           "anystate\n" <> lines(&"  a -> b: k#{&1}() => s\n", half)
       end},
      {"reply blocks among rules",
       fn n ->
         "contract @1h => expired\ntype k\ntype r\n  request: ref(k)\nstate new\n  a -> b: k() => q0\n" <>
           lines(
             &("state q#{&1}\n  a -> b: k() awaits w#{&1}\n    => r() & s\n     | @5m & stalled\n" <>
                 Enum.map_join(1..60, "", fn j -> "  c -> a: x#{j}() => s\n" end)),
             n
           )
       end},
      {"chain of wait states",
       fn n ->
         "contract @1h => expired\ntype k\ntype r\n  request: ref(k)\nstate new\n  a -> b: k() => c0\n" <>
           lines(
             &"state c#{&1}\n  a -> b: k() awaits w#{&1}\n    => r() & c#{&1 + 1}\n     | @5m & stalled\n",
             n
           )
       end},
      {"types with fields",
       fn n -> "contract @1h => expired\n" <> lines(&"type k#{&1}\n  f: boolean\n", n) end},
      {"one-line prompts", fn n -> @pre <> lines(&"  \"doc\"\n  a -> b: k#{&1}() => s\n", n) end},
      {"orphan pipes", fn n -> @pre <> lines(fn _ -> "     | @5m & s\n" end, n) end},
      {"blank lines", fn n -> @pre <> "  a -> b: k() => s\n" <> rep("\n", n) end}
    ]
  end

  describe "doubling an input at most multiplies its time by 2.5" do
    for name <- @file_shapes_names do
      @name name
      test name do
        gen = Map.new(file_shapes())[@name]
        [small, large] = Enum.map([64_000, 128_000], &(gen.(&1) <> @ends))

        for {label, fun} <- [{"compile", &compile/1}, {"format", &Format.format(@header <> &1)}] do
          {t1, t2} = best_pair(7, fn -> fun.(small) end, fn -> fun.(large) end)
          assert t2 <= 2.5 * t1 + 10, "#{label}: #{t1} ms at 64 KB, #{t2} ms at 128 KB"
        end
      end
    end
  end

  # Felix (736b95d): a closed Markdown fence whose closing line is long (spaces before the
  # backticks) was quadratic: the closing marker was trimmed again for every formatted line.
  test "a Markdown band fence with a long closing line formats in linear time" do
    fence = fn n ->
      "Before fence.\n```band\ncontract @1h => expired\nstate new\n" <>
        rep("%% comment\n", div(n, 2)) <> rep(" ", div(n, 2)) <> "```\nAfter fence.\n"
    end

    {small, large} = {fence.(64_000), fence.(128_000)}

    {t1, t2} =
      best_pair(7, fn -> Format.format_markdown(small) end, fn ->
        Format.format_markdown(large)
      end)

    assert t2 <= 2.5 * t1 + 10, "format_markdown: #{t1} ms at 64 KB, #{t2} ms at 128 KB"
  end

  describe "a 1 MB pathological file in under 1 s" do
    @mb 1_000_000
    for name <- ["unclosed sigils", "closed sigils", "rules", "one-line prompts", "orphan pipes"] do
      @name name
      test name do
        body = Map.new(mb_shapes())[@name].(@mb)
        assert best_ms(1, fn -> Format.format(@header <> body) end) < 1000
        assert best_ms(1, fn -> assert {:error, :source_size} = compile(body) end) < 1000
      end
    end
  end

  defp mb_shapes do
    [
      {"unclosed sigils", fn n -> @pre <> "  a -> b: k() " <> rep("~s(", n) <> "\n" end},
      {"closed sigils", fn n -> @pre <> "  a -> b: k() " <> rep("~s/x/ ~r|y| ", n) <> "\n" end},
      {"rules", fn n -> @pre <> lines(&"  a -> b: k#{&1}() => s\n", n) end},
      {"one-line prompts", fn n -> @pre <> lines(&"  \"doc\"\n  a -> b: k#{&1}() => s\n", n) end},
      {"orphan pipes", fn n -> @pre <> lines(fn _ -> "     | @5m & s\n" end, n) end}
    ]
  end

  test "the shape lists match their test names" do
    assert Enum.map(line_shapes(), &elem(&1, 0)) == @line_shapes_names
    assert Enum.map(file_shapes(), &elem(&1, 0)) == @file_shapes_names
  end

  test "the sigil scan still finds removed uppercase sigils after unclosed ones" do
    body = @pre <> "  a -> b: k() ~s( ~s( ~S/x/\n" <> @ends

    assert {:error, %{code: :syntax, detail: "Sigils are lowercase: ~s, ~r and ~w"}} =
             compile(body)

    body = @pre <> "  a -> b: k() ~s(~S/x/)\n" <> @ends
    refute match?({:error, %{detail: "Sigils are lowercase: ~s, ~r and ~w"}}, compile(body))
  end
end
