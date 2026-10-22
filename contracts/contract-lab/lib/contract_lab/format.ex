defmodule ContractLab.Format do
  @moduledoc """
  The `.band` formatter behind `mix contract.format`.

  Layout only: top-level declarations at column 0, fields and rules at 2, collect options and a
  reply block's `=>` alternative at 4, its `|` alternatives at 5;
  one space around `->`, `=>`, an outcome or alternative `|` and `&`; `name: value` colons; a blank line between
  top-level declarations, except inside a run of one-line declarations of the same keyword
  (`end`, field-less `type`, `role`, ...); at most one blank line; no trailing spaces; a final
  newline. YAML frontmatter is kept byte for byte. Comments, author notes and documentation
  keep their text and their place; a multiline literal moves only as a whole and stays put
  when it cannot. Sigils, patterns, `{a,b}` selectors and `enum(a|b)` lists are never respaced.
  """

  import Bitwise

  @top ~w(contract role type state anystate room end watch require)
  @options ~r/^(reply|offline|quorum|failure)( |$)/

  # compile/2 and FileSource bounds (Sofia U8): a body over 262,144 bytes, or a whole source
  # over 266,240 bytes, is refused before any scan of it.
  @max_body 262_144
  @max_entry 266_240

  @doc """
  Format one source. Returns `{:ok, text}`, `{:error, :source_size}`, `{:error, :crlf}` or
  `{:error, :encoding}`.
  """
  def format(source) when is_binary(source) do
    if oversized?(source), do: {:error, :source_size}, else: format_any(source)
  end

  # Band fences in Markdown are bounded by the Markdown file (spec/format.md, Markdown), not by
  # compile/2, so they format through here without the source bound.
  defp format_any(source) do
    cond do
      not String.valid?(source) -> {:error, :encoding}
      String.contains?(source, "\r") -> {:error, :crlf}
      true -> {:ok, split_frontmatter(source) |> then(fn {h, b} -> h <> body(b) end)}
    end
  end

  @doc """
  Format the ```band fences of a Markdown source (spec/format.md, Markdown); every other byte is
  kept. Returns `{:ok, text}`, `{:error, :crlf}` or `{:error, :encoding}`.
  """
  def format_markdown(source) when is_binary(source) do
    cond do
      not String.valid?(source) ->
        {:error, :encoding}

      String.contains?(source, "\r") ->
        {:error, :crlf}

      true ->
        lines =
          Enum.flat_map(segments_md(source), fn
            {:line, line} ->
              [line]

            {:band, open, content, indent, close} ->
              [open | format_fence(content, indent, close)] ++ [close]
          end)

        {:ok, Enum.join(lines, "\n")}
    end
  end

  @doc "The content of each ```band fence of a Markdown source, without the fence's indentation."
  def band_fences(source) do
    for {:band, _, content, indent, _} <- segments_md(source), do: dedent(content, indent)
  end

  @doc "The compiled object without source positions or source digest: what formatting keeps."
  def comparable(value) when is_map(value) and not is_struct(value),
    do: value |> Map.drop([:line, :source_digest]) |> Map.new(fn {k, v} -> {k, comparable(v)} end)

  def comparable(value) when is_list(value), do: Enum.map(value, &comparable/1)
  def comparable(value), do: value

  # :binary.match gives a byte offset, so the split is by bytes too (not graphemes).
  # Markdown as {:line, line} and {:band, open, content_lines, indent, close} segments. Inside a
  # fenced block nothing is parsed; only a fence whose info string is `band` is a band segment,
  # and an unclosed fence is kept, line by line, to the end.
  defp segments_md(source), do: source |> String.split("\n") |> md_lines([])

  defp md_lines([], acc), do: Enum.reverse(acc)

  defp md_lines([line | rest], acc) do
    case Regex.run(~r/^( *)(`{3,}|~{3,})(.*)$/, line) do
      [_, indent, marker, info] ->
        case Enum.split_while(rest, &(not closing_fence?(&1, marker))) do
          {content, [close | tail]} ->
            segment =
              if String.trim(info) == "band",
                do: [{:band, line, content, indent, close}],
                else: Enum.map([line | content] ++ [close], &{:line, &1})

            md_lines(tail, Enum.reverse(segment) ++ acc)

          {_, []} ->
            Enum.reverse(acc) ++ Enum.map([line | rest], &{:line, &1})
        end

      nil ->
        md_lines(rest, [{:line, line} | acc])
    end
  end

  # A closing fence repeats the opening character at least as many times, and nothing else.
  defp closing_fence?(line, <<char::binary-size(1), _::binary>> = marker) do
    trimmed = String.trim(line)
    byte_size(trimmed) >= byte_size(marker) and String.trim(trimmed, char) == ""
  end

  # Format a fence's content at the fence's indentation. Content that is blank, has no top-level
  # declaration, has a line indented less than the fence, or would gain a line closing the fence,
  # is kept as written.
  defp format_fence(content, indent, close) do
    n = byte_size(indent)
    # Trimmed once: per line it was quadratic in a long closing line (Felix, 736b95d).
    marker = String.trim(close)

    with true <- Enum.any?(content, &(String.trim(&1) != "")),
         true <- Enum.all?(content, &(String.trim(&1) == "" or String.starts_with?(&1, indent))),
         text = dedent(content, indent),
         true <- top_level?(text),
         {:ok, formatted} when formatted != "" <- format_any(text),
         lines = formatted |> String.trim_trailing("\n") |> String.split("\n"),
         false <- Enum.any?(lines, &closing_fence?(&1, marker)) do
      Enum.map(lines, fn
        "" -> ""
        line -> String.duplicate(" ", n) <> line
      end)
    else
      _ -> content
    end
  end

  # A fragment of nested lines or header fields has no level-0 declaration to hang them on.
  defp top_level?(text) do
    {_, body} = split_frontmatter(text)
    body |> String.split("\n") |> scan([]) |> levels() |> Enum.any?(&match?({{:decl, _}, 0}, &1))
  end

  defp dedent(content, indent) do
    n = byte_size(indent)

    content
    |> Enum.map(fn line ->
      if String.starts_with?(line, indent),
        do: binary_part(line, n, byte_size(line) - n),
        else: String.trim_leading(line)
    end)
    |> Enum.join("\n")
    |> Kernel.<>("\n")
  end

  defp split_frontmatter("---\n" <> rest = source) do
    case :binary.match(rest, "\n---\n") do
      {at, _} ->
        size = 4 + at + 5
        <<head::binary-size(size), body::binary>> = source
        {head, body}

      :nomatch ->
        if String.ends_with?(rest, "\n---"), do: {source <> "\n", ""}, else: {"", source}
    end
  end

  defp split_frontmatter(source), do: {"", source}

  defp oversized?(source) when byte_size(source) > @max_entry, do: true

  defp oversized?(source) do
    {_header, body} = split_frontmatter(source)
    byte_size(body) > @max_body
  end

  defp body(text) do
    items = text |> String.split("\n") |> scan([])
    items = items |> levels() |> drop_blanks_after_docs()

    case render(items) do
      [] -> ""
      lines -> Enum.join(lines, "\n") <> "\n"
    end
  end

  # Items: {:blank}, {:comment, text}, {:doc, text}, {:block, kind, raw_lines}, {:decl, text}.
  defp scan([], acc), do: Enum.reverse(acc)

  defp scan([raw | rest], acc) do
    text = String.trim(raw)

    cond do
      text == "" ->
        if match?([{:blank} | _], acc),
          do: scan(rest, acc),
          else: scan(rest, [{:blank} | acc])

      String.starts_with?(text, "%%") ->
        scan(rest, [{:comment, text} | acc])

      String.starts_with?(text, "/*") ->
        {lines, tail} = take_note([raw | rest], [])
        scan(tail, [{:block, :note, lines} | acc])

      text in [~s("""), ~s(~s""")] ->
        {lines, tail} = take_heredoc(rest, [raw])
        scan(tail, [{:block, :doc, lines} | acc])

      String.starts_with?(text, ~s(")) or String.starts_with?(text, "~s") ->
        scan(rest, [{:doc, text} | acc])

      true ->
        scan(rest, [{:decl, text} | acc])
    end
  end

  defp take_note([raw | rest], acc) do
    if String.contains?(raw, "*/"),
      do: {Enum.reverse([raw | acc]), rest},
      else: take_note(rest, [raw | acc])
  end

  defp take_note([], acc), do: {Enum.reverse(acc), []}

  defp take_heredoc([], acc), do: {Enum.reverse(acc), []}

  defp take_heredoc([raw | rest], acc) do
    if String.trim(raw) == ~s("""),
      do: {Enum.reverse([raw | acc]), rest},
      else: take_heredoc(rest, [raw | acc])
  end

  # Declarations get their level from their keyword; collect options from directly following a
  # collect; reply alternatives (syntax U) from their leading `=>` (level 2) or `|` (:pipe, one
  # space deeper) alone, wherever they are; comments, notes and docs from the next declaration
  # they attach to, so a prompt sits at its alternative's indent (Lead decision 6).
  defp levels(items) do
    {decls, _} =
      Enum.map_reduce(items, nil, fn
        {:decl, text} = item, previous ->
          level =
            cond do
              top?(text) ->
                0

              String.starts_with?(text, "|") ->
                :pipe

              String.starts_with?(text, "=>") ->
                2

              previous in [:collect, :option] and Regex.match?(@options, text) and
                  not String.contains?(text, " -> ") ->
                2

              true ->
                1
            end

          role =
            cond do
              level == 2 and not String.starts_with?(text, "=>") -> :option
              String.starts_with?(text, "collect ") -> :collect
              true -> :other
            end

          {{item, level}, role}

        item, _ ->
          {{item, nil}, :other}
      end)

    {result, _} =
      decls
      |> Enum.reverse()
      |> Enum.map_reduce(0, fn
        {{:decl, _} = item, level}, _next -> {{item, level}, level}
        {item, nil}, next -> {{item, next}, next}
      end)

    Enum.reverse(result)
  end

  defp top?(text) do
    case String.split(text, " ", parts: 2) do
      [word | rest] when word in @top ->
        rest == [] or not String.starts_with?(hd(rest), "-> ")

      _ ->
        roster?(text)
    end
  end

  # A roster `name: list(type)`; only a line starting with a lowercase letter can be one.
  defp roster?(<<c, _::binary>> = text) when c in ?a..?z,
    do: Regex.match?(~r/^[a-z][a-z0-9_]*\s*:\s*list\(/, text)

  defp roster?(_), do: false

  defp keyword(text) do
    if Regex.match?(~r/^[a-z][a-z0-9_]*\s*:\s*list\(/, text),
      do: "roster",
      else: text |> String.split(" ", parts: 2) |> hd()
  end

  # Group items into top-level units: leading comments, notes and docs, the declaration and
  # its nested lines (and the blank lines after them). A unit is simple when nothing is nested.
  defp units(items) do
    items
    |> Enum.drop_while(&match?({{:blank}, _}, &1))
    |> Enum.reduce([], fn
      {{:blank}, _} = entry, [unit | rest] ->
        [%{unit | items: [entry | unit.items]} | rest]

      {{:decl, text}, 0} = entry, units ->
        case units do
          [%{leading: true} = unit | rest] ->
            [%{unit | items: [entry | unit.items], keyword: keyword(text), leading: false} | rest]

          _ ->
            [%{items: [entry], keyword: keyword(text), leading: false, simple: true} | units]
        end

      {_, 0} = entry, units ->
        case units do
          [%{leading: true} = unit | rest] -> [%{unit | items: [entry | unit.items]} | rest]
          _ -> [%{items: [entry], keyword: nil, leading: true, simple: true} | units]
        end

      entry, [unit | rest] ->
        [%{unit | items: [entry | unit.items], simple: false, leading: false} | rest]

      entry, [] ->
        [%{items: [entry], keyword: nil, leading: false, simple: false}]
    end)
    |> Enum.reverse()
    |> Enum.map(fn unit -> %{unit | items: Enum.reverse(unit.items)} end)
  end

  # Exactly one blank line between top-level units (Eric, 10-15: no same-keyword grouping).
  defp render(items) do
    items
    |> units()
    |> Enum.map(&(&1.items |> unit_lines() |> trim_trailing_blanks()))
    |> Enum.intersperse([""])
    |> List.flatten()
  end

  # A prompt sits directly above the item it documents: no blank line after a doc literal.
  defp drop_blanks_after_docs(items) do
    items
    |> Enum.reduce({[], false}, fn
      {{:blank}, _}, {acc, true} -> {acc, true}
      {item, _} = entry, {acc, _} -> {[entry | acc], doc?(item)}
    end)
    |> elem(0)
    |> Enum.reverse()
  end

  defp doc?({:doc, _}), do: true
  defp doc?({:block, :doc, _}), do: true
  defp doc?(_), do: false

  defp trim_trailing_blanks(lines),
    do: lines |> Enum.reverse() |> Enum.drop_while(&(&1 == "")) |> Enum.reverse()

  # Render a unit's items, collapsing blank runs to one.
  defp unit_lines(items) do
    items
    |> Enum.flat_map(fn {item, level} -> item_lines(item, level) end)
    |> Enum.chunk_while(
      [],
      fn
        "", ["" | _] = acc -> {:cont, acc}
        line, acc -> {:cont, [line | acc]}
      end,
      fn acc -> {:cont, Enum.reverse(acc), []} end
    )
    |> List.flatten()
  end

  defp item_lines({:blank}, _), do: [""]
  defp item_lines({:comment, text}, level), do: [indent(level) <> text]
  # A one-line prompt is written as a block, its content as written (spec/format.md, Prompts).
  defp item_lines({:doc, text}, level) do
    case block_form(text) do
      {opener, content} -> Enum.map([opener, content, ~s(""")], &(indent(level) <> &1))
      nil -> [indent(level) <> text]
    end
  end

  defp item_lines({:decl, text}, level), do: [indent(level) <> respace(text)]

  defp item_lines({:block, kind, lines}, level) do
    target = byte_size(indent(level))
    current = leading(hd(lines))
    delta = target - current

    shiftable =
      Enum.all?(lines, fn line ->
        ws = binary_part(line, 0, leading_ws(line))
        not String.contains?(ws, "\t") and (line == "" or delta >= 0 or leading(line) >= -delta)
      end)

    cond do
      not shiftable ->
        lines

      kind == :note ->
        Enum.map(lines, &(shift(&1, delta) |> String.trim_trailing()))

      true ->
        # Documentation content is kept exactly; only the delimiter lines lose trailing blanks.
        last = length(lines) - 1

        lines
        |> Enum.with_index()
        |> Enum.map(fn
          {line, i} when i in [0, last] -> line |> shift(delta) |> String.trim_trailing()
          {line, _} -> shift(line, delta)
        end)
    end
  end

  defp shift("", _), do: ""
  defp shift(line, delta) when delta >= 0, do: String.duplicate(" ", delta) <> line
  defp shift(line, delta), do: binary_part(line, -delta, byte_size(line) + delta)

  defp leading(line), do: byte_size(line) - byte_size(String.trim_leading(line, " "))
  defp leading_ws(line), do: byte_size(line) - byte_size(String.trim_leading(line))

  # No other declaration starts with `|` (a timed rule's outcome `|` is mid-line): a `|`
  # alternative sits one space deeper than level 2, so the alternatives line up under `=>`.
  defp indent(:pipe), do: "     "
  defp indent(level), do: String.duplicate("  ", level)

  # Respace a declaration outside sigils: one space between words, around -> and =>, after a
  # colon, and around an outcome or alternative | and an alternative's & (only outside
  # parentheses, where enum(a|b) lives).
  defp respace(text) do
    text |> pieces(0, []) |> IO.iodata_to_binary() |> String.trim()
  end

  # Plain text and sigils in one pass, sigils copied unchanged; the paren depth carries across.
  defp pieces("", _depth, acc), do: acc

  defp pieces(text, depth, acc) do
    case sigil_start(text, 0) do
      {start, open_at, close} ->
        rest = binary_part(text, open_at + 1, byte_size(text) - open_at - 1)
        stop = open_at + 1 + closing(rest, close, 0)

        {out, depth} =
          if start > 0, do: plain(binary_part(text, 0, start), depth), else: {"", depth}

        sigil = binary_part(text, start, stop - start)
        pieces(binary_part(text, stop, byte_size(text) - stop), depth, [acc, out, sigil])

      nil ->
        [acc | elem(plain(text, depth), 0)]
    end
  end

  # The respacing steps of spec/format.md, each one linear pass over bytes (regex replaces and a
  # grapheme list cost 100+ ms on a 250 KB line). They reproduce, in order: [ \t]+ -> " ";
  # " ?-> ?" -> " -> "; " ?=> ?" -> " => "; " ?: ?" -> ": "; a standalone ( ) | & grapheme sets
  # the depth or gets spaces at depth 0; " +([|&]) +" -> " \1 ".
  defp plain(s, depth) do
    case needs(s, 0) do
      0 -> {s, depth}
      flags -> respace_plain(s, depth, flags)
    end
  end

  # Which passes a piece needs, from one scan; no pass creates the bytes another one looks for,
  # so a pass whose bytes are absent here would be the identity. 1: a tab or double space;
  # 2: ->; 4: =>; 8: a colon; 16: ( ) | or &; 32: | or &.
  # binary_part keeps a sub-binary; rebuilding <<?\s, rest>> copied the rest (quadratic).
  defp needs(<<?\s, ?\s, _::binary>> = s, f),
    do: needs(binary_part(s, 1, byte_size(s) - 1), f ||| 1)

  defp needs(<<?\t, rest::binary>>, f), do: needs(rest, f ||| 1)
  defp needs(<<?-, ?>, rest::binary>>, f), do: needs(rest, f ||| 2)
  defp needs(<<?=, ?>, rest::binary>>, f), do: needs(rest, f ||| 4)
  defp needs(<<?:, rest::binary>>, f), do: needs(rest, f ||| 8)
  defp needs(<<c, rest::binary>>, f) when c in ~c"()", do: needs(rest, f ||| 16)
  defp needs(<<c, rest::binary>>, f) when c in ~c"|&", do: needs(rest, f ||| 48)
  defp needs(<<_, rest::binary>>, f), do: needs(rest, f)
  defp needs(<<>>, f), do: f

  defp respace_plain(s, depth, flags) do
    s = if (flags &&& 1) != 0, do: blanks(s, <<>>), else: s
    s = if (flags &&& 2) != 0, do: spaced(s, "->", " -> "), else: s
    s = if (flags &&& 4) != 0, do: spaced(s, "=>", " => "), else: s
    s = if (flags &&& 8) != 0, do: spaced(s, ":", ": "), else: s
    {s, depth} = if (flags &&& 16) != 0, do: operators(s, depth), else: {s, depth}
    {if((flags &&& 32) != 0, do: around(s, <<>>), else: s), depth}
  end

  defp blanks(<<c, rest::binary>>, acc) when c in ~c" \t",
    do: blanks(skip_blanks(rest), <<acc::binary, ?\s>>)

  defp blanks(<<c, rest::binary>>, acc), do: blanks(rest, <<acc::binary, c>>)
  defp blanks(<<>>, acc), do: acc

  defp skip_blanks(<<c, rest::binary>>) when c in ~c" \t", do: skip_blanks(rest)
  defp skip_blanks(rest), do: rest

  # Leftmost matches of " ?op ?": an optional space before op (unless the previous match took
  # it), then one optional space after. op holds no space, so its occurrences are the matches;
  # the text between them is copied as slices.
  defp spaced(text, op, with) do
    size = byte_size(text)
    {acc, from} = spaced_at(:binary.matches(text, op), text, size, byte_size(op), with, [], 0)
    IO.iodata_to_binary([acc | binary_part(text, from, size - from)])
  end

  defp spaced_at([], _text, _size, _n, _with, acc, from), do: {acc, from}

  defp spaced_at([{at, _} | rest], text, size, n, with, acc, from) do
    start = if at > from and :binary.at(text, at - 1) == ?\s, do: at - 1, else: at
    stop = at + n
    stop = if stop < size and :binary.at(text, stop) == ?\s, do: stop + 1, else: stop

    spaced_at(
      rest,
      text,
      size,
      n,
      with,
      [acc, binary_part(text, from, start - from) | with],
      stop
    )
  end

  # ( ) | & count only as graphemes of their own, as String.graphemes/1 would split them: the
  # only rules that join an ASCII mark to a neighbour are a Prepend before it and an Extend,
  # ZWJ or SpacingMark after it, all non-ASCII, so ASCII neighbours need no lookup. The marks
  # are found with :binary.matches and the text between them copied as slices.
  defp operators(text, depth) do
    size = byte_size(text)
    marks = :binary.matches(text, ["(", ")", "|", "&"])
    {acc, from, depth} = operators_at(marks, text, size, [], 0, depth)
    {IO.iodata_to_binary([acc | binary_part(text, from, size - from)]), depth}
  end

  defp operators_at([], _text, _size, acc, from, depth), do: {acc, from, depth}

  defp operators_at([{at, _} | rest], text, size, acc, from, depth) do
    cp = :binary.at(text, at)

    {out, depth} =
      if ascii_neighbours?(text, at, size) or
           alone?(previous(text, at), cp, binary_part(text, at + 1, size - at - 1)) do
        mark(cp, depth)
      else
        {<<cp>>, depth}
      end

    operators_at(rest, text, size, [acc, binary_part(text, from, at - from) | out], at + 1, depth)
  end

  defp mark(?(, depth), do: {"(", depth + 1}
  defp mark(?), depth), do: {")", max(depth - 1, 0)}
  defp mark(?|, 0), do: {" | ", 0}
  defp mark(?&, 0), do: {" & ", 0}
  defp mark(c, depth), do: {<<c>>, depth}

  # Both neighbours ASCII (or absent): the mark is a grapheme of its own.
  defp ascii_neighbours?(text, at, size) do
    (at == 0 or :binary.at(text, at - 1) < 128) and
      (at + 1 == size or :binary.at(text, at + 1) < 128)
  end

  # The codepoint that ends at byte `at`, or nil at the start.
  defp previous(_text, 0), do: nil

  defp previous(text, at) do
    case :binary.at(text, at - 1) do
      byte when byte < 128 -> byte
      _ -> previous_utf8(text, at)
    end
  end

  defp previous_utf8(text, at) do
    start =
      Enum.find((at - 1)..max(at - 4, 0)//-1, fn i -> :binary.at(text, i) not in 0x80..0xBF end)

    case binary_part(text, start, at - start) do
      <<cp::utf8>> -> cp
      _ -> :binary.at(text, at - 1)
    end
  end

  defp alone?(prev, cp, rest) do
    before? =
      is_nil(prev) or prev < 128 or
        match?(
          {n, _} when n == byte_size(<<prev::utf8>>),
          String.next_grapheme_size(<<prev::utf8, cp>>)
        )

    after? =
      case rest do
        <<next::utf8, _::binary>> when next >= 128 ->
          match?({1, _}, String.next_grapheme_size(<<cp, next::utf8>>))

        _ ->
          true
      end

    before? and after?
  end

  # " +([|&]) +" -> " \1 ": a run of spaces, | or &, and a run of spaces become one each side.
  defp around(<<>>, acc), do: acc

  defp around(<<?\s, _::binary>> = text, acc) do
    rest = skip_spaces(text)
    run = byte_size(text) - byte_size(rest)

    case rest do
      <<op, ?\s, tail::binary>> when op in ~c"|&" ->
        around(skip_spaces(tail), <<acc::binary, ?\s, op, ?\s>>)

      _ ->
        around(rest, <<acc::binary, :binary.copy(" ", run)::binary>>)
    end
  end

  defp around(<<c, rest::binary>>, acc), do: around(rest, <<acc::binary, c>>)

  defp skip_spaces(<<?\s, rest::binary>>), do: skip_spaces(rest)
  defp skip_spaces(rest), do: rest

  @closers %{
    "/" => "/",
    "|" => "|",
    "{" => "}",
    "[" => "]",
    "(" => ")",
    "<" => ">",
    ~s(") => ~s("),
    "'" => "'"
  }
  @closer_bytes Map.new(@closers, fn {open, close} ->
                  {:binary.first(open), :binary.first(close)}
                end)

  # {opener, content} for a one-line literal that can be a block; nil keeps it as written (text
  # after the closing delimiter, empty content, or content that would close the block).
  defp block_form(~s(") <> rest), do: rest |> plain_content(0) |> as_block(~s("""))

  defp block_form("~s" <> <<open::binary-size(1), rest::binary>>)
       when is_map_key(@closers, open) do
    case :binary.split(rest, @closers[open]) do
      [content, ""] ->
        # A raw prompt reads the same as a plain block unless a backslash or #{ would change it.
        opener =
          if String.contains?(content, ["\\", "\#{"]), do: ~s(~s"""), else: ~s(""")

        as_block(content, opener)

      _ ->
        nil
    end
  end

  defp block_form(_), do: nil

  # The content of a plain one-line literal up to its unescaped closing quote, if nothing follows.
  defp plain_content(rest, n) do
    case rest do
      <<_::binary-size(n), "\\", _, _::binary>> -> plain_content(rest, n + 2)
      <<content::binary-size(n), ~s(")>> -> content
      <<_::binary-size(n), ~s("), _::binary>> -> nil
      <<_::binary-size(n), _, _::binary>> -> plain_content(rest, n + 1)
      _ -> nil
    end
  end

  defp as_block(nil, _), do: nil
  defp as_block("", _), do: nil

  defp as_block(content, opener),
    do: if(String.trim(content) == ~s("""), do: nil, else: {opener, content})

  # The byte offsets of the next ~r, ~s or ~w, its opening delimiter and the closing byte, as
  # ~r/~[rsw]([\/|{\[(<"'])/ would find them, without a regex call per sigil.
  # Most calls start at zero; avoid constructing a scope option for every closed sigil.
  defp sigil_start(text, 0), do: sigil_match(text, :binary.match(text, "~"))

  defp sigil_start(text, from),
    do: sigil_match(text, :binary.match(text, "~", scope: {from, byte_size(text) - from}))

  defp sigil_match(text, match) do
    case match do
      {at, 1} ->
        case binary_part(text, at + 1, min(2, byte_size(text) - at - 1)) do
          <<kind, open>> when kind in ~c"rsw" and open in ~c"/|{[(<\"'" ->
            {at, at + 2, :erlang.map_get(open, @closer_bytes)}

          _ ->
            sigil_start(text, at + 1)
        end

      :nomatch ->
        nil
    end
  end

  # Bytes up to and including the closing delimiter (backslash escapes skip one byte).
  defp closing(<<?\\, _, rest::binary>>, close, n), do: closing(rest, close, n + 2)

  defp closing(<<c, rest::binary>>, close, n),
    do: if(c == close, do: n + 1, else: closing(rest, close, n + 1))

  defp closing(<<>>, _, n), do: n
end
