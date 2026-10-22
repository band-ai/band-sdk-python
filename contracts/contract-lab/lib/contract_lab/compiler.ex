defmodule ContractLab.Compiler do
  @moduledoc false
  alias ContractLab.Canonical
  @identifier ~r/^[a-z][a-z0-9_]*$/

  # @identifier as a byte scan, for lists that can hold 100k values (enum, ~w and role sets).
  # Like the regex's `$`, it accepts one final newline.
  defp identifier?(<<c, rest::binary>>) when c in ?a..?z, do: identifier_rest?(rest)
  defp identifier?(_), do: false

  defp identifier_rest?(<<c, rest::binary>>) when c in ?a..?z or c in ?0..?9 or c == ?_,
    do: identifier_rest?(rest)

  defp identifier_rest?(<<>>), do: true
  defp identifier_rest?(<<?\n>>), do: true
  defp identifier_rest?(_), do: false
  @lowercase_sigils "Sigils are lowercase: ~s, ~r and ~w"
  @collect_failure "collect failure type() was removed; report failures with room contribution controls"
  @engines ["0.2.0-lab.1"]

  @doc "Engine identifiers this compiler implements; discovery refuses any other first."
  def engines, do: @engines

  @doc "Report discovery's refusal of an unknown engine as the compiler's `engine` error."
  def engine_error({:error, {:unsupported_engine, _}}),
    do: {:error, %{code: :engine, line: 1, detail: nil}}

  def engine_error({:error, reason}) when reason in [:unsupported_engine, :invalid_engine],
    do: {:error, %{code: :engine, line: 1, detail: nil}}

  def engine_error(result), do: result

  @doc """
  Header field errors take the compiler-error shape (B1 did this for `engine`): the field name
  is the detail and the line is the field's line in the file, counting the opening `---` as
  line 1. A missing field has no line of its own and is reported at line 1.
  """
  @header_errors [:unknown_field, :duplicate_field, :missing_field]
  def header_error({:error, {kind, field}}, text)
      when kind in @header_errors and is_binary(field) do
    {:error, %{code: kind, line: field_line(kind, field, text), detail: field}}
  end

  # Sofia set O LOW: every other header problem takes the same shape. A malformed header block
  # is `header` at line 1; an invalid value of a known field is `field_value` at its line.
  @header_block ~w(invalid_yaml unsupported_yaml_feature invalid_fields missing_frontmatter unterminated_header header_too_large)a
  @field_values %{
    invalid_name: "name",
    name_directory_mismatch: "name",
    invalid_description: "description",
    invalid_roles: "roles",
    invalid_version: "version",
    unsupported_encoding: "encoding",
    unsupported_extension: "extensions",
    invalid_lifetime: "lifetime",
    invalid_inactivity_timeout: "inactivity-timeout",
    metadata_must_be_strings: "metadata",
    invalid_contract_path: "band-contract",
    invalid_contract_digest: "band-contract-sha256"
  }

  def header_error({:error, reason}, _text) when reason in @header_block,
    do: {:error, %{code: :header, line: 1, detail: Atom.to_string(reason)}}

  def header_error({:error, reason}, text) when is_map_key(@field_values, reason) do
    field = Map.fetch!(@field_values, reason)
    {:error, %{code: :field_value, line: field_line(:field_value, field, text), detail: field}}
  end

  def header_error(result, _text), do: result

  defp field_line(:missing_field, _field, _text), do: 1

  # The header text may be a thunk, so a path loader reads the entry only when a field line is
  # needed, and only through the bounded regular-file reader (Sofia set O O2).
  defp field_line(kind, field, text) when is_function(text, 0),
    do: field_line(kind, field, text.())

  # The line of the key's YAML node (Omar set P P-10), counting the opening --- as line 1.
  # Tiers (Sofia set Q Q-L3): the bare top-level key, then metadata band-<field>, then a metadata
  # key of that name. A repeated field is reported at its first repeat within one mapping, where
  # parsing stops.
  defp field_line(kind, field, text) when is_binary(text) do
    case HeaderSpikes.Prefix.parse(text, :frontmatter) do
      {:ok, %{content: content}} -> node_line(kind, field, HeaderSpikes.Yaml.key_lines(content))
      _ -> 1
    end
  end

  defp field_line(_kind, _field, _text), do: 1

  defp node_line(:duplicate_field, field, keys) do
    keys
    |> Enum.reduce_while(MapSet.new(), fn {_, mapping, _, key, line}, seen ->
      cond do
        key != field -> {:cont, seen}
        MapSet.member?(seen, mapping) -> {:halt, {:line, line}}
        true -> {:cont, MapSet.put(seen, mapping)}
      end
    end)
    |> case do
      {:line, line} -> line + 1
      _ -> 1
    end
  end

  defp node_line(_kind, field, keys) do
    bare = for {1, _, _, ^field, line} <- keys, do: line
    band = for {2, _, "metadata", key, line} <- keys, key == "band-" <> field, do: line
    # Nested means the standard package's metadata mapping only (Omar set Q Q-L6).
    nested = for {2, _, "metadata", ^field, line} <- keys, do: line

    case Enum.find([bare, band, nested], [], &(&1 != [])) do
      [] -> 1
      [first | _] -> first + 1
    end
  end

  # `reply_required: :list` (corpus checks against RULE.md's prototype only) returns every
  # reply_required violation as `reply_required` on the contract instead of the error.
  def compile(card, source, opts \\ [])

  def compile(card, source, opts) when is_map(card) and is_binary(source) do
    try do
      ensure(byte_size(source) <= 262_144 and String.valid?(source), :source_size, 1)
      ensure(Map.get(card, :engine) in @engines, :engine, 1)

      c = %{
        name: card.name,
        engine: card.engine,
        roles: card.roles,
        card: card,
        meta: meta(nil, 1),
        role_metadata: Map.new(card.roles, &{&1, %{name: &1, meta: meta(nil, 1)}}),
        types: %{},
        states: %{},
        terminals:
          Map.new(
            ~w(expired participant_removed control_capacity_exhausted insufficient_time completion_capacity unserviceable_obligation),
            &{&1, %{name: &1, meta: meta(nil, 1)}}
          ),
        any_rules: [],
        room_rules: [],
        any_meta: meta(nil, 1),
        room_meta: meta(nil, 1)
      }

      c =
        Map.put(c, :extensions, %{
          permissions: %{},
          permission_meta: %{},
          triggers: [],
          watches: [],
          watch_meta: %{}
        })

      c =
        if "room.contributions/1" in Map.get(card, :extensions, []) do
          Map.put(c, :contribution_profile, "band-contract/contribution/1")
        else
          c
        end

      parser = %{
        contract: c,
        section: nil,
        pending: nil,
        declared: MapSet.new(),
        count: 0,
        timed_rules: %{},
        clock: false,
        pattern_work: ContractLab.SafePattern.contract_compile_limit(),
        # Reply alternatives (DESIGN v2 §2): the rule a reply block may follow, the block being
        # read, the closed blocks and the contract-wide alternative count.
        open: nil,
        block: nil,
        blocks: [],
        alternatives: 0
      }

      parsed = source |> String.split("\n") |> Enum.with_index(1) |> parse(parser)
      ensure(parsed.pending == nil, :orphan_documentation, (parsed.pending || %{line: 1}).line)

      c =
        parsed.contract
        |> in_source_order()
        |> ContractLab.Replies.compile(Enum.reverse(parsed.blocks), parsed.timed_rules)
        |> resolve_resets(parsed.timed_rules)
        |> validate()

      c =
        if opts[:reply_required] == :list,
          do:
            Map.put(
              c,
              :reply_required,
              ContractLab.ReplyRequired.violations(c, parsed.timed_rules)
            ),
          else: ContractLab.ReplyRequired.check(c, parsed.timed_rules)

      declared_clock(c, parsed.clock)
      semantic_card = Map.delete(c.card, :description)
      semantic_card = Map.delete(semantic_card, :version)
      semantic = %{c | card: semantic_card}

      semantic =
        put_in(
          semantic,
          [:extensions],
          Map.drop(semantic.extensions, [:permission_meta, :watch_meta])
        )

      semantic = strip_meta(semantic)

      {:ok,
       Map.merge(c, %{
         digest: Canonical.digest(semantic),
         source_digest: Canonical.hash(Canonical.encode(card) <> "\n" <> source)
       })}
    catch
      {:compile, code, line, detail} ->
        {:error, %{code: code, line: line, detail: authored(detail, card)}}
    end
  end

  def compile(_, _, _) do
    {:error, %{code: :input, line: 1, detail: nil}}
  end

  # 0.2.0 rewrites ordinary keywords to internal +KEYWORD forms before parsing
  # (and rejects authored plus forms with their own message), so a detail that
  # quotes a line shows the keyword the author wrote.
  defp authored(detail, %{engine: "0.2.0-lab.1"}) when is_binary(detail) do
    Regex.replace(~r/\+(CONTRACT|ROLE|TYPE|STATE|END|ANYSTATE|ROOM)\b/, detail, fn _, keyword ->
      String.downcase(keyword)
    end)
  end

  defp authored(detail, _card), do: detail

  # ~r/^"(?:[^"\\]|\\.)*"$/u as a scan (the regex took 100 ms on a 250 KB prompt): a quote,
  # then codepoints where a backslash takes the next one, and the closing quote last.
  defp quoted_line?(<<?", rest::binary>>), do: quoted_rest?(rest)
  defp quoted_line?(_), do: false

  defp quoted_rest?(<<?">>), do: true
  defp quoted_rest?(<<?", _::binary>>), do: false
  defp quoted_rest?(<<?\\, _::utf8, rest::binary>>), do: quoted_rest?(rest)
  defp quoted_rest?(<<?\\, _::binary>>), do: false
  defp quoted_rest?(<<_::utf8, rest::binary>>), do: quoted_rest?(rest)
  defp quoted_rest?(_), do: false

  # The parser prepends rules and deadlines (appending is quadratic in their number); put them
  # back in source order once.
  defp in_source_order(c) do
    states =
      Map.new(c.states, fn {name, s} ->
        {name, %{s | rules: Enum.reverse(s.rules), deadlines: Enum.reverse(s.deadlines)}}
      end)

    %{
      c
      | states: states,
        any_rules: Enum.reverse(c.any_rules),
        room_rules: Enum.reverse(c.room_rules)
    }
  end

  defp parse([], state) do
    close_block(state)
  end

  defp parse([{raw, line} | rest], state) do
    text = ordinary_declaration(String.trim(raw), state.contract.engine)

    cond do
      text == "" or String.starts_with?(text, "%%") ->
        parse(rest, state)

      String.starts_with?(text, "/*") ->
        tail = block_comment(String.replace_prefix(text, "/*", ""), rest, line)
        parse(tail, state)

      String.starts_with?(text, "~S") or String.starts_with?(text, "~s") ->
        ensure(not String.starts_with?(text, "~S"), :syntax, line, @lowercase_sigils)
        ensure(state.pending == nil, :orphan_documentation, line)

        {doc, tail} =
          if text in [~s(~S"""), ~s(~s""")] do
            raw_heredoc(rest, [], line)
          else
            {raw_literal(binary_part(text, 2, byte_size(text) - 2), :documentation, line), rest}
          end

        parse(tail, %{state | pending: %{doc: doc, line: line}})

      text == ~s(""") ->
        ensure(state.pending == nil, :orphan_documentation, line)
        {literal, tail} = heredoc(rest, [raw], line)
        doc = literal |> decode_documentation(line) |> drop_final_newline()
        parse(tail, %{state | pending: %{doc: doc, line: line}})

      String.starts_with?(text, ~s(")) ->
        ensure(state.pending == nil, :orphan_documentation, line)
        ensure(quoted_line?(text), :documentation, line)
        doc = decode_documentation(text, line)
        parse(rest, %{state | pending: %{doc: doc, line: line}})

      String.starts_with?(text, "collect ") ->
        identifier_lengths(text, line)
        {next, tail} = collection(text, rest, line, state |> close_block() |> Map.put(:open, nil))
        parse(tail, next)

      alternative_line?(text) ->
        identifier_lengths(text, line)
        parse(rest, alternative(text, line, state))

      true ->
        identifier_lengths(text, line)
        removed_form(String.trim(raw), line)
        state = state |> close_block() |> Map.put(:open, nil)
        renewal_head(text, rest, line, state)

        case wait_head(text, rest, line, state) do
          nil -> parse(rest, declaration(text, line, state))
          {desugared, w, implicit} -> parse(rest, head(desugared, w, implicit, line, state))
        end
    end
  end

  # Syntax U (Eric decision 4; Theo's U PRODUCTIONS.md). A send followed by alternative lines
  # is a head: `a -> b: k() [as X] awaits W`, or `a -> b: k() [as X]` whose W is `k_wait`. The
  # first alternative starts with `=>`, the others with `|`; `| @d & T` is the timeout. Only
  # alternative lines start with `=>` or `|` (a timed rule's outcome `|` is mid-line).
  defp alternative_line?(text), do: String.starts_with?(text, ["=>", "|"])

  # A send with a renewal target (`=> @self`) is a syntax error, unless alternatives follow
  # it: then it is a head that cannot declare a wait (reply_route). `awaits @self` is wait_head's.
  defp renewal_head(text, rest, line, p) do
    with false <- directive?(text, "EVENT", "event", p.contract.engine),
         true <- Regex.match?(~r/ => @(self|state|contract)$/, text),
         true <- wait_line_next?(rest) do
      fail(
        :reply_route,
        (p.pending || %{line: line}).line,
        "A head names its wait state with awaits W, not a renewal"
      )
    end
  end

  # The next line that is not blank, a comment, a note or documentation is an alternative.
  defp wait_line_next?([]), do: false

  defp wait_line_next?([{raw, _} | rest]) do
    text = String.trim(raw)

    cond do
      text == "" or String.starts_with?(text, "%%") ->
        wait_line_next?(rest)

      String.starts_with?(text, "/*") ->
        if String.contains?(text, "*/"),
          do: wait_line_next?(rest),
          else:
            rest |> Enum.drop_while(fn {r, _} -> not String.contains?(r, "*/") end) |> skip_one()

      text in [~s("""), ~s(~s""")] ->
        rest |> Enum.drop_while(fn {r, _} -> String.trim(r) != ~s(""") end) |> skip_one()

      String.starts_with?(text, ~s(")) or String.starts_with?(text, "~s") ->
        wait_line_next?(rest)

      true ->
        alternative_line?(text)
    end
  end

  defp skip_one([]), do: false
  defp skip_one([_ | rest]), do: wait_line_next?(rest)

  @call ~r/^([a-z][a-z0-9_]*)\([^)]*+\)/

  # A send's `{prefix through the call, tail, kind}`, in time linear in the line (Sofia U2: a
  # lazy regex over the whole line backtracked quadratically): the call follows the first
  # `: `, since role selectors hold none, and is matched anchored and possessive.
  defp split_head(text) do
    with {at, 2} <- :binary.match(text, ": "),
         rest = binary_part(text, at + 2, byte_size(text) - at - 2),
         [call, kind] <- Regex.run(@call, rest) do
      tail = binary_part(rest, byte_size(call), byte_size(rest) - byte_size(call))
      {binary_part(text, 0, at + 2) <> call, tail, kind}
    else
      _ -> nil
    end
  end

  # A head, as `{rule text, W, implicit?}`: a send ending `awaits W`, or a send with no target
  # whose next line is an alternative (W = `kind_wait`). It becomes `=> W`, and without `as` the
  # send binds W's name (the sugar), so the rule compiles exactly as the explicit form. Anything
  # else (a target, a timer) is left to the rule parser and the head checks. Lead decision 7:
  # an awaiting event or `awaits @reset` is reply_route, and `awaits W` with no alternatives is
  # reply_timeout at the head (a re-send is `=> W`, never `awaits`).
  defp wait_head(text, rest, line, p) do
    alternatives = wait_line_next?(rest)

    with {prefix, tail, kind} <- split_head(text),
         options = String.split(tail),
         false <- "=>" in options,
         {options, w, implicit, outcome} <- head_form(options, kind, alternatives) do
      head_line = (p.pending || %{line: line}).line

      ensure(
        not directive?(text, "EVENT", "event", p.contract.engine),
        :reply_route,
        head_line,
        "A head is a rule, not an event"
      )

      ensure(
        w not in ~w(@self @state @contract),
        :reply_route,
        head_line,
        "A head awaits a wait state, not a renewal"
      )

      ensure(Regex.match?(@identifier, w), :syntax, line, "awaits names a state")

      # A1b-4: anystate holds no duties, so an anystate head is untimed.
      ensure(
        outcome == nil or p.section != :any,
        :reply_route,
        head_line,
        "An anystate head is untimed: a -> b: k() awaits #{w}"
      )

      ensure(
        alternatives,
        :reply_timeout,
        head_line,
        "awaits #{w} opens a reply block (=> reply() & state, then | @d & outcome); a re-send is a -> b: k() => #{w}"
      )

      {timer, options} = Enum.split_with(options, &String.starts_with?(&1, "@"))
      options = if "as" in options or "each" in options, do: options, else: options ++ ["as", w]

      # T1 (RULE.md §A.2): `k() [as X] @d awaits W | T` is the timed rule `k() as X @d => W | T`
      # (its sender's duty, due-(N+1)) that also declares W.
      tail = if outcome, do: timer ++ ["=>", w, "|", outcome], else: timer ++ ["=>", w]
      {Enum.join([prefix | options] ++ tail, " "), w, implicit}
    else
      _ -> nil
    end
  end

  defp head_form(options, kind, alternatives) do
    case {Enum.split(options, -2), Enum.split(options, -4)} do
      {{options, ["awaits", w]}, _} ->
        {options, w, false, nil}

      {_, {options, ["awaits", w, "|", outcome]}} ->
        if match?([_], Enum.filter(options, &String.starts_with?(&1, "@"))),
          do: {options, w, false, outcome}

      _ ->
        if alternatives and not Enum.any?(options, &String.starts_with?(&1, "@")) do
          {options, kind <> "_wait", true, nil}
        end
    end
  end

  defp head(text, w, implicit, line, p) do
    p = declaration(text, line, p)
    %{p | open: Map.put(p.open, :awaits, %{state: w, implicit: implicit})}
  end

  defp close_block(%{block: nil} = p), do: p
  defp close_block(p), do: %{p | blocks: [p.block | p.blocks], block: nil}

  # The first alternative turns the head before it into a declaring head (DESIGN §2).
  defp reply_block(%{block: nil, open: nil}, line, _leader) do
    fail(
      :syntax,
      line,
      "Alternatives follow the send that declares the wait: a -> b: k() awaits W"
    )
  end

  # Errors about the head point where errors about any compiled rule do: its meta line.
  # Alternatives after a timed rule or timed event match no production (PRODUCTIONS.md):
  # `syntax`. Block-shape faults are reply_timeout, bad heads reply_route.
  defp reply_block(%{block: nil, open: open}, line, leader) do
    head = open.rule

    # T1: a timed send may declare a wait only as `@d awaits W | T`.
    ensure(
      not open.timed or Map.has_key?(open, :awaits),
      :syntax,
      line,
      "Alternatives follow an untimed send or a timed head (k() @d awaits W | outcome); the timeout alternative replaces a plain send's @duration"
    )

    ensure(
      Map.has_key?(open, :awaits),
      :reply_route,
      head.meta.line,
      "A head names its wait state with awaits W, or has no target: a -> b: k() awaits W"
    )

    # RULE.md §A A1b: a head may also sit in `anystate`; it then applies everywhere except in
    # its own wait.
    ensure(
      (match?({:state, _}, open.section) or open.section == :any) and not head.event and
        not head.each and length(head.from) == 1 and length(head.to) == 1,
      :reply_route,
      head.meta.line,
      "A reply block follows one untimed send from one role to one role in a named state or anystate"
    )

    ensure(leader == "=>", :reply_timeout, line, "The first alternative starts with =>")

    section = with {:state, name} <- open.section, do: name

    %{
      head: head,
      state: open.awaits.state,
      implicit: open.awaits.implicit,
      timed: open.timed,
      section: section,
      line: head.meta.line,
      alternatives: [],
      timeout: nil
    }
  end

  defp reply_block(%{block: block}, line, leader) do
    ensure(leader == "|", :reply_timeout, line, "Every alternative after the first starts with |")
    block
  end

  # Theo's U EBNF (reply-alt, timeout-alt); spaces after the leader and around & are tokenized,
  # as a rule's arrow tail is.
  @alternative ~r/^([a-z][a-z0-9_]*)\(([^)]*)\)(?:[ \t]+as[ \t]+([a-z][a-z0-9_]*))?[ \t]*&[ \t]*([a-z][a-z0-9_]*)$/
  @timeout ~r/^@([0-9a-z]+)[ \t]*&[ \t]*([a-z][a-z0-9_]*)$/

  defp alternative(text, line, p) do
    {leader, body} =
      case text do
        "=>" <> body -> {"=>", String.trim_leading(body)}
        "|" <> body -> {"|", String.trim_leading(body)}
      end

    block = reply_block(p, line, leader)

    if String.starts_with?(body, "@"),
      do: reply_timeout(body, block, line, p),
      else: reply_alternative(body, block, line, p)
  end

  defp reply_alternative(text, block, line, p) do
    m = p.pending || meta(nil, line)

    # Lead decision 3: an early timeout reports at the timeout's line.
    ensure(
      block.timeout == nil,
      :reply_timeout,
      block.timeout && block.timeout.meta.line,
      "The timeout is the last alternative"
    )

    [kind, args, bind, target] =
      Regex.run(@alternative, text, capture: :all_but_first) ||
        fail(:syntax, line, "Write | type(field=value, field=$binding) as name & state")

    args =
      if String.trim(args) == "",
        do: [],
        else: Enum.map(String.split(args, ","), &reply_argument(String.trim(&1), line))

    fields = Enum.map(args, &elem(&1, 1))
    ensure(length(fields) == length(Enum.uniq(fields)), :reply_match, line, "Repeated field")
    literals = for {:literal, field, value} <- args, do: {field, value}
    ensure(length(literals) <= 4, :reply_limit, line, "At most 4 literals in one alternative")
    ensure(length(block.alternatives) < 16, :reply_limit, line, "At most 16 alternatives a block")
    ensure(p.alternatives < 256, :reply_limit, line, "At most 256 alternatives in a contract")

    rule = %{
      id: "rule-#{p.count}",
      from: block.head.to,
      to: block.head.from,
      kind: kind,
      matches: Map.new(for {:binding, field, binding} <- args, do: {field, binding}),
      event: false,
      target: target,
      bind: if(bind == "", do: nil, else: bind),
      each: false,
      activity: false,
      meta: m
    }

    alternative = %{rule: rule, literals: literals, line: line}

    %{
      p
      | block: %{block | alternatives: block.alternatives ++ [alternative]},
        pending: nil,
        count: p.count + 1,
        alternatives: p.alternatives + 1
    }
  end

  # `field=$binding` or `field=value` with an identifier value (EBNF reply-arg); the value is
  # checked against the field's domain later and stays a string (literals are never interned).
  defp reply_argument(arg, line) do
    case :binary.split(arg, "=") do
      [field, "$" <> binding] ->
        ensure(identifier?(field) and identifier?(binding), :syntax, line, arg)
        {:binding, field, binding}

      [field, value] ->
        ensure(identifier?(field) and identifier?(value), :syntax, line, arg)
        {:literal, field, value}

      _ ->
        fail(:syntax, line, arg)
    end
  end

  defp reply_timeout(text, block, line, p) do
    m = p.pending || meta(nil, line)
    ensure(block.timeout == nil, :reply_timeout, line, "A reply block has exactly one timeout")

    ensure(
      block.alternatives != [],
      :reply_timeout,
      line,
      "A reply block is one or more alternatives, then its timeout"
    )

    [value, outcome] =
      Regex.run(@timeout, text, capture: :all_but_first) ||
        fail(:syntax, line, "Write | @duration & outcome")

    timeout = %{id: "due-#{p.count}", after_ms: duration(value, line), outcome: outcome, meta: m}
    %{p | block: %{block | timeout: timeout}, pending: nil, count: p.count + 1}
  end

  @keywords ~w(contract role type state end anystate room event due on collect watch require)
  @line_keywords ~r/ (MAX_ENTRIES|TOTAL|ONLINE|OFFLINE|REMOVED|FOR|MAX|MENTIONS|MENTION)( |$)/
  # The lowercase sigils of a line, removed as a global replace of ~[srw] + delimited body would,
  # but linear: a sigil whose closer is missing from its body to the end of the line makes every
  # later sigil with that closer fail as well (a scan never starts inside an escape), so each
  # closer is searched to the end at most once (an unclosed ~s( repeated took minutes).
  @openers %{?/ => ?/, ?| => ?|, ?[ => ?], ?{ => ?}, ?( => ?), ?< => ?>, ?" => ?", ?' => ?'}

  defp strip_sigils(<<?~, kind, open, body::binary>> = text, missing, acc)
       when kind in ~c"srw" and is_map_key(@openers, open) do
    close = @openers[open]

    case if(missing[close], do: :missing, else: sigil_end(body, close)) do
      :missing ->
        <<c, rest::binary>> = text
        strip_sigils(rest, Map.put(missing, close, true), [acc, c])

      rest ->
        strip_sigils(rest, missing, acc)
    end
  end

  defp strip_sigils(<<c, rest::binary>>, missing, acc), do: strip_sigils(rest, missing, [acc, c])
  defp strip_sigils(<<>>, _missing, acc), do: IO.iodata_to_binary(acc)

  # Sofia U6: no identifier is longer than 128 bytes, so details and views stay small. A run
  # of [A-Za-z0-9_] longer than that, outside ~r and ~s bodies (patterns and text), is a syntax
  # error at its line; ~w words are identifiers and count. One pass, with strip_sigils's memo
  # of closers already known to be missing.
  @max_identifier 128

  defp identifier_lengths(text, line), do: identifier_run(text, 0, %{}, line)

  defp identifier_run(<<?~, kind, open, body::binary>> = text, _run, missing, line)
       when kind in ~c"rs" and is_map_key(@openers, open) do
    close = @openers[open]

    case if(missing[close], do: :missing, else: sigil_end(body, close)) do
      :missing ->
        <<_, rest::binary>> = text
        identifier_run(rest, 0, Map.put(missing, close, true), line)

      rest ->
        identifier_run(rest, 0, missing, line)
    end
  end

  defp identifier_run(<<c, rest::binary>>, run, missing, line)
       when c in ?a..?z or c in ?A..?Z or c in ?0..?9 or c == ?_ do
    ensure(run < @max_identifier, :syntax, line, "Identifiers are at most 128 bytes")
    identifier_run(rest, run + 1, missing, line)
  end

  defp identifier_run(<<_, rest::binary>>, _run, missing, line),
    do: identifier_run(rest, 0, missing, line)

  defp identifier_run(<<>>, _run, _missing, _line), do: :ok

  # The rest after (?:[^\\c]|\\.)*c, or :missing.
  defp sigil_end(<<?\\, _, rest::binary>>, close), do: sigil_end(rest, close)
  defp sigil_end(<<?\\>>, _close), do: :missing
  defp sigil_end(<<close, rest::binary>>, close), do: rest
  defp sigil_end(<<_, rest::binary>>, close), do: sigil_end(rest, close)
  defp sigil_end(<<>>, _close), do: :missing

  defp removed_form(text, line) do
    scan = strip_sigils(text, %{}, [])
    keyword = text |> String.split(" ", parts: 2) |> hd()
    lowered = String.downcase(keyword)

    hint =
      cond do
        String.starts_with?(text, "+") ->
          "Plus-prefixed declarations were removed; write contract, role, type, state, end, anystate or room"

        lowered == "due" and not Regex.match?(~r/^\S+ -> /, text) ->
          "due … with … in was removed; put @duration on the rule (a -> b: k() @5m => next | outcome) or on an event (event a -> b: k() @5m => outcome)"

        lowered in @keywords and keyword != lowered ->
          "Keywords are lowercase: write " <> lowered

        Regex.match?(~r/^role [a-z][a-z0-9_]*: list\(/, text) ->
          "Write the roster as reviewers: list(reviewer), without role"

        Regex.match?(~r/^[a-z][a-z0-9_]*: list\(agent\)$/, text) ->
          "Builtin list(agent) was removed; declare type reviewer: agent and use reviewers: list(reviewer)"

        Regex.match?(~r/~[SRW]/, scan) ->
          @lowercase_sigils

        Regex.match?(~r/regex: *"/, scan) ->
          "Quoted regexes were removed; write regex: ~r/…/"

        keyword in ~w(contract state on) and Regex.match?(@line_keywords, scan) ->
          [word | _] = Regex.run(@line_keywords, scan, capture: :all_but_first)
          "Keywords are lowercase: write " <> String.downcase(word)

        true ->
          nil
      end

    if hint do
      fail(:syntax, line, hint)
    end
  end

  # A block's text ends at its last content line: the line break before the closing """ is
  # layout, so a block and a one-line literal with the same text decode alike (Ada's A, 10-15).
  defp drop_final_newline(doc) do
    cond do
      String.ends_with?(doc, "\r\n") -> binary_part(doc, 0, byte_size(doc) - 2)
      String.ends_with?(doc, "\n") -> binary_part(doc, 0, byte_size(doc) - 1)
      true -> doc
    end
  end

  defp decode_documentation(literal, line) do
    ensure(
      not Regex.match?(~r/(?<!\\)#\{/, literal),
      :documentation,
      line,
      "Interpolation is not executable contract metadata"
    )

    doc =
      case Code.string_to_quoted(literal <> "\n", existing_atoms_only: true) do
        {:ok, value} when is_binary(value) -> value
        _ -> fail(:documentation, line)
      end

    ensure(String.valid?(doc), :documentation, line, "Documentation must decode to valid UTF-8")
    doc
  end

  defp collection(text, rest, line, p) do
    ensure(match?({:state, _}, p.section), :syntax, line)
    {:state, state} = p.section
    c = p.contract
    m = p.pending || meta(nil, line)

    case Regex.run(
           ~r/^collect ([a-z][a-z0-9_]*) from (\S+) -> (\S+) @(\S+) => ([a-z][a-z0-9_]*)$/,
           text,
           capture: :all_but_first
         ) do
      [name, from, to, duration, target] ->
        existing = Map.get(c, :collections, %{})

        ensure(
          not Map.has_key?(existing, name) and
            not Enum.any?(existing, fn {_, d} -> d.state == state end),
          :duplicate,
          line
        )

        {options, tail} = collection_options(rest, %{}, line)
        ensure(Map.has_key?(options, "reply"), :syntax, line, "A collection needs reply type()")

        ensure(
          not Map.has_key?(options, "failure"),
          :syntax,
          line,
          @collect_failure
        )

        rules =
          Enum.flat_map([{"reply", "ok"}, {"failure", "failed"}], fn {key, status} ->
            case options[key] do
              nil ->
                []

              pattern ->
                r =
                  rule(
                    "event " <> from <> " -> " <> to <> ": " <> pattern,
                    c.roles,
                    m,
                    line,
                    p.count +
                      if key == "reply" do
                        0
                      else
                        1
                      end,
                    c.engine
                  )

                [Map.merge(r, %{collection: name, settlement: status})]
            end
          end)

        ensure(
          not Map.has_key?(options, "offline") or "host.presence/1" in c.card.extensions,
          :extension_required,
          line
        )

        definition = %{
          profile:
            if ContractLab.Contributions.enabled?(c) do
              "band-contract/collection/2"
            else
              "band-contract/collection/1"
            end,
          name: name,
          state: state,
          from: hd(rules).from,
          to: hd(rules).to,
          reply: hd(rules).kind,
          after_ms: duration(duration, line),
          offline_ms:
            if options["offline"] do
              duration(options["offline"], line)
            else
              nil
            end,
          target: target,
          meta: m
        }

        definition =
          case options["quorum"] do
            nil -> definition
            {quorum, n} -> Map.put(definition, :quorum, quorum(quorum, n))
          end

        c =
          c
          |> Map.put(:collections, Map.put(existing, name, definition))
          |> update_in([:states, state, :rules], &Enum.reverse(rules, &1))

        {%{
           p
           | contract: c,
             pending: nil,
             count: p.count + length(rules),
             timed_rules: Map.put(p.timed_rules, hd(rules).id, "collection:" <> name)
         }, tail}

      _ ->
        fail(:syntax, line, role_set_hint(text))
    end
  end

  defp collection_options([{raw, n} | rest] = rows, options, line) do
    text = String.trim(raw)

    case Regex.run(~r/^(reply|failure|offline|quorum) (.+)$/, text, capture: :all_but_first) do
      [key, value] ->
        identifier_lengths(text, n)
        ensure(not Map.has_key?(options, key), :duplicate, n)

        value =
          case key do
            "offline" -> String.trim_leading(value, "@")
            "quorum" -> {value, n}
            _ -> value
          end

        collection_options(rest, Map.put(options, key, value), line)

      _ ->
        case Regex.run(~r/^(reply|failure|offline|quorum)( |$)/i, text, capture: :all_but_first) do
          [word | _] when word not in ["reply", "offline", "quorum"] ->
            if String.downcase(word) == "failure" do
              fail(:syntax, n, @collect_failure)
            else
              fail(
                :syntax,
                n,
                "Collection options are lowercase: write " <> String.downcase(word)
              )
            end

          _ ->
            {options, rows}
        end
    end
  end

  defp collection_options([], options, _) do
    {options, []}
  end

  # `quorum all => x` or `quorum <boolean attribute> => x` (DF-25).
  defp quorum(text, line) do
    case Regex.run(~r/^([a-z][a-z0-9_]*) => ([a-z][a-z0-9_]*)$/, text, capture: :all_but_first) do
      [who, target] ->
        %{attribute: if(who == "all", do: nil, else: who), target: target}

      _ ->
        fail(
          :syntax,
          line,
          "Write quorum all => outcome or quorum <boolean attribute> => outcome"
        )
    end
  end

  defp ordinary_declaration(text, "0.2.0-lab.1") do
    case String.split(text, " ", parts: 2) do
      [keyword | rest] when keyword in ~w(contract role type state end anystate room) ->
        Enum.join(["+" <> String.upcase(keyword) | rest], " ")

      _ ->
        if Regex.match?(~r/^[a-z][a-z0-9_]*: list\([a-z][a-z0-9_]*\)$/, text) do
          "+ROLE " <> text
        else
          text
        end
    end
  end

  defp block_comment(text, rest, line) do
    ensure(not String.contains?(text, "/*"), :comment, line, "Nested comments are not supported")

    case String.split(text, "*/", parts: 2) do
      [_content, tail] ->
        ensure(String.trim(tail) == "", :comment, line, "Comment closing line has trailing code")
        rest

      [_content] ->
        case rest do
          [] -> fail(:comment, line, "Unterminated block comment")
          [{next, _} | tail] -> block_comment(next, tail, line)
        end
    end
  end

  defp directive?(text, upper, lower, _engine) do
    String.starts_with?(text, upper <> " ") or
      (String.starts_with?(text, lower <> " ") and not String.starts_with?(text, lower <> " -> "))
  end

  defp heredoc([], _, line) do
    fail(:documentation, line, "Unterminated heredoc")
  end

  defp heredoc([{raw, _} | rest], acc, line) do
    if String.trim(raw) == ~s(""") do
      {Enum.join(Enum.reverse([raw | acc]), "\n"), rest}
    else
      heredoc(rest, [raw | acc], line)
    end
  end

  defp raw_heredoc([], _, line) do
    fail(:documentation, line, "Unterminated raw heredoc")
  end

  defp raw_heredoc([{raw, _} | rest], acc, line) do
    case Regex.run(~r/^([ \t]*)"""[ \t]*\r?$/, raw, capture: :all_but_first) do
      [indent] ->
        # The line break before the closing delimiter is not part of the text (Ada's A, 10-15).
        doc =
          acc
          |> Enum.reverse()
          |> Enum.map_join(fn content -> strip_indent(content, byte_size(indent)) <> "\n" end)
          |> drop_final_newline()

        {doc, rest}

      _ ->
        raw_heredoc(rest, [raw | acc], line)
    end
  end

  defp strip_indent(<<char, rest::binary>>, n) when n > 0 and char in ~c" \t" do
    strip_indent(rest, n - 1)
  end

  defp strip_indent(text, _) do
    text
  end

  defp raw_literal(<<opening, rest::binary>>, code, line) when opening in ~c"/|{[(<\"'" do
    closing = %{123 => 125, 91 => 93, 40 => 41, 60 => 62}[opening] || opening
    ensure(sigil_closed?(rest, closing), code, line)
    binary_part(rest, 0, byte_size(rest) - 1)
  end

  defp raw_literal(_, code, line) do
    fail(code, line)
  end

  defp declaration(text, line, p) do
    m = p.pending || meta(nil, line)
    p = %{p | pending: nil}
    c = p.contract

    cond do
      text == "+CONTRACT" or String.starts_with?(text, "+CONTRACT @") ->
        {text, default_cap} = contract_mentions(text, c.engine, line)
        {declaration, timeout} = scope_timeout(text, c.engine, m, line)
        ensure(declaration == "+CONTRACT", :syntax, line)
        p = unique(p, :contract, line)

        c =
          if is_integer(default_cap) do
            Map.put(c, :mentions, %{max: default_cap})
          else
            c
          end

        c =
          if timeout do
            if opt_out?(text) do
              c
            else
              c
              |> Map.put(:timeout, timeout)
              |> put_in(
                [:card, :lifetime],
                if timeout.after_ms do
                  {:seconds, div(timeout.after_ms, 1000)}
                else
                  :infinite
                end
              )
            end
          else
            c
          end

        %{p | contract: %{c | meta: m}, section: nil, clock: p.clock or timeout != nil}

      String.starts_with?(text, "+ROLE ") ->
        declaration = String.replace_prefix(text, "+ROLE ", "")

        {name, member} =
          case String.split(declaration, ": ", parts: 2) do
            [name] ->
              {name, nil}

            [name, definition] ->
              :ok

              case Regex.run(~r/^list\(([a-z][a-z0-9_]*)\)$/, definition, capture: :all_but_first) do
                [member] -> {name, member}
                _ -> fail(:syntax, line, declaration)
              end
          end

        c =
          if member do
            Map.update(
              c,
              :role_sets,
              %{name => roster_definition(member)},
              &Map.put(&1, name, roster_definition(member))
            )
          else
            c
          end

        ensure(name in c.roles, :unknown_role, line, name)
        p = unique(p, {:role, name}, line)

        %{
          p
          | contract: %{c | role_metadata: Map.put(c.role_metadata, name, %{name: name, meta: m})},
            section: nil
        }

      String.starts_with?(text, "+TYPE ") ->
        declaration = String.replace_prefix(text, "+TYPE ", "")

        {name, category} =
          case String.split(declaration, ": ", parts: 2) do
            [name] -> {name, :types}
            [name, "agent"] -> {name, :participant_types}
            _ -> fail(:syntax, line, declaration)
          end

        ensure(Regex.match?(@identifier, name), :syntax, line)
        ensure(category != :participant_types or name != "agent", :type, line)
        p = unique(p, {:type, name}, line)
        type = %{name: name, fields: %{}, meta: m}

        type =
          if category == :participant_types do
            Map.put(type, :profile, "band-contract/participant/1")
          else
            type
          end

        c = Map.update(c, category, %{name => type}, &Map.put(&1, name, type))
        %{p | contract: c, section: {category, name}}

      String.starts_with?(text, "+STATE ") ->
        {text, timeout} = scope_timeout(text, c.engine, m, line)

        grammar =
          ~r/^\+STATE ([a-z][a-z0-9_]*)(?: total @(\S+) => ([a-z][a-z0-9_]*))?(?: (?:MAX_ENTRIES|max_entries) ([0-9]+) => ([a-z][a-z0-9_]*))?$/

        {name, total, cap} =
          case Regex.run(
                 grammar,
                 text,
                 capture: :all_but_first
               ) do
            [name | rest] ->
              rest = rest ++ List.duplicate("", 4 - length(rest))
              [budget, budget_outcome, n, target] = rest

              total =
                if budget != "" do
                  %{
                    profile: "band-contract/total/1",
                    after_ms: duration(budget, line),
                    outcome: budget_outcome,
                    meta: m
                  }
                end

              cap =
                if n != "" do
                  n = String.to_integer(n)
                  ensure(n > 0 and n <= 9_007_199_254_740_991, :rounds, line)
                  %{limit: n, outcome: target}
                end

              {name, total, cap}

            _ ->
              fail(:syntax, line, text)
          end

        p = unique(p, {:state, name}, line)
        state = %{name: name, rules: [], deadlines: [], max_entries: cap, meta: m}

        state =
          if timeout do
            Map.put(state, :timeout, timeout)
          else
            state
          end

        # DF-21: a cumulative budget over every entry; absent unless declared.
        state =
          if total do
            Map.put(state, :total, total)
          else
            state
          end

        %{p | contract: %{c | states: Map.put(c.states, name, state)}, section: {:state, name}}

      text in ["+ANYSTATE", "+ROOM"] ->
        p = unique(p, text, line)

        if text == "+ANYSTATE" do
          %{p | contract: %{c | any_meta: m}, section: :any}
        else
          %{p | contract: %{c | room_meta: m}, section: :room}
        end

      String.starts_with?(text, "+END ") ->
        name = String.replace_prefix(text, "+END ", "")
        ensure(Regex.match?(@identifier, name), :syntax, line)
        p = unique(p, {:state, name}, line)

        %{
          p
          | contract: %{c | terminals: Map.put(c.terminals, name, %{name: name, meta: m})},
            section: nil
        }

      String.starts_with?(text, "watch ") ->
        ensure(p.section == nil, :syntax, line)

        ensure(
          Enum.any?(c.card.extensions, &(&1 in ["host.presence/1", "host.membership/1"])),
          :extension_required,
          line
        )

        name = String.replace_prefix(text, "watch ", "")
        ensure(Regex.match?(@identifier, name), :syntax, line)
        p = unique(p, {:watch, name}, line)

        c =
          c
          |> update_in([:extensions, :watches], &(&1 ++ [name]))
          |> put_in([:extensions, :watch_meta, name], m)

        %{p | contract: c}

      directive?(text, "ON", "on", c.engine) ->
        ensure(
          match?({:state, _}, p.section) or p.section == :any,
          :syntax,
          line
        )

        ensure(length(c.extensions.triggers) < 32, :host_trigger_limit, line)

        state =
          case p.section do
            {:state, name} -> name
            :any -> "*"
          end

        trigger = host_trigger(text, c, state, m, line, p.count)
        c = update_in(c, [:extensions, :triggers], &(&1 ++ [trigger]))
        %{p | contract: c, count: p.count + 1}

      String.starts_with?(text, "require ") ->
        ensure(p.section == nil, :syntax, line)
        ensure("band.permissions/1" in c.card.extensions, :extension_required, line)

        case Regex.run(
               ~r/^require ([a-z][a-z0-9_]*) band\.permission (owner|admin|participant)$/,
               text,
               capture: :all_but_first
             ) do
          [role, permission] ->
            ensure(role in c.roles, :unknown_role, line, role)
            p = unique(p, {:permission, role}, line)

            c =
              c
              |> put_in([:extensions, :permissions, role], permission)
              |> put_in([:extensions, :permission_meta, role], m)

            %{p | contract: c}

          _ ->
            fail(:syntax, line, text)
        end

      match?({category, _} when category in [:types, :participant_types], p.section) ->
        {category, name} = p.section

        [field, definition] =
          case String.split(text, ":", parts: 2) do
            [field, definition] -> [String.trim(field), String.trim(definition)]
            _ -> fail(:syntax, line)
          end

        ensure(Regex.match?(@identifier, field), :syntax, line)
        p = unique(p, {:field, name, field}, line)
        {field_type, pattern_work} = field_type(definition, line, c.engine, p.pattern_work)
        field_type = Map.put(field_type, :meta, m)

        ensure(
          category != :participant_types or field_type.kind != :ref,
          :type,
          line,
          "Participant attributes cannot reference messages that do not exist at creation"
        )

        %{
          p
          | contract: put_in(c, [category, name, :fields, field], field_type),
            pattern_work: pattern_work
        }

      true ->
        {text, timeout} = inline_deadline(text, c.engine, line)
        rule = rule(text, c.roles, m, line, p.count, c.engine)
        ensure(is_nil(timeout) or match?({:state, _}, p.section), :syntax, line)

        c =
          case p.section do
            {:state, name} ->
              update_in(c, [:states, name, :rules], &[rule | &1])

            :any ->
              %{c | any_rules: [rule | c.any_rules]}

            :room ->
              ensure(rule.event and rule.matches == %{} and rule.bind == nil, :syntax, line)
              %{c | room_rules: [rule | c.room_rules]}

            _ ->
              fail(:syntax, line, text)
          end

        c =
          if timeout do
            {:state, name} = p.section

            duty = %{
              id: "due-#{p.count + 1}",
              quantifier:
                if rule.each or Map.get(timeout, :each, false) or
                     (length(rule.from) == 1 and
                        not Map.has_key?(Map.get(c, :role_sets, %{}), hd(rule.from))) do
                  :all
                else
                  :any
                end,
              participants: rule.from,
              kind: rule.kind,
              after_ms: timeout.after_ms,
              outcome: timeout.outcome,
              meta: m
            }

            update_in(c, [:states, name, :deadlines], &[duty | &1])
          else
            c
          end

        %{
          p
          | contract: c,
            open: %{rule: rule, section: p.section, timed: timeout != nil},
            count:
              p.count +
                if timeout do
                  2
                else
                  1
                end,
            timed_rules:
              if timeout do
                Map.put(p.timed_rules, rule.id, "due-#{p.count + 1}")
              else
                p.timed_rules
              end
        }
    end
  end

  defp inline_deadline(text, "0.2.0-lab.1", line) do
    timed = ~r/^(.*) @(\S+) => ([a-z][a-z0-9_]*) \| ([a-z][a-z0-9_]*)$/
    timed_event = ~r/^(event .*) @(\S+) => ([a-z][a-z0-9_]*)$/
    # DF-07: a timed event may also renew the immediately following timed rule. Its own duty
    # is derived as for any timed event; the `@self` target is resolved as for `=> @self`.
    timed_renewal = ~r/^(event .*) @(\S+) => ([a-z][a-z0-9_]*) @self$/

    cond do
      match = Regex.run(timed, text, capture: :all_but_first) ->
        [rule, value, target, outcome] = match
        {rule <> " => " <> target, %{after_ms: duration(value, line), outcome: outcome}}

      match = Regex.run(timed_event, text, capture: :all_but_first) ->
        [rule, value, outcome] = match
        {rule, each} = timed_event_each(rule)
        {rule, %{after_ms: duration(value, line), outcome: outcome, each: each}}

      match = Regex.run(timed_renewal, text, capture: :all_but_first) ->
        [rule, value, outcome] = match
        {rule, each} = timed_event_each(rule)
        {rule <> " => @self", %{after_ms: duration(value, line), outcome: outcome, each: each}}

      true ->
        {text, nil}
    end
  end

  defp timed_event_each(rule) do
    case split_head(rule) do
      {head, options, _kind} ->
        words = String.split(options)

        if "each" in words do
          {Enum.join([head | List.delete(words, "each")], " "), true}
        else
          {rule, false}
        end

      nil ->
        {rule, false}
    end
  end

  defp contract_mentions("+CONTRACT @" <> _ = text, "0.2.0-lab.1", line) do
    case Regex.run(~r/^(\+CONTRACT @.+?) mentions? (\S+)( => [a-z][a-z0-9_]*)$/, text,
           capture: :all_but_first
         ) do
      [head, value, tail] ->
        case ContractLab.Mentions.parse(value) do
          {:ok, cap} -> {head <> tail, cap}
          :error -> fail(:syntax, line, text)
        end

      nil ->
        {text, nil}
    end
  end

  defp contract_mentions(text, _engine, _line) do
    {text, nil}
  end

  defp roster_definition(member) do
    %{
      profile:
        if member == "agent" do
          "band-contract/roster/1"
        else
          "band-contract/roster/2"
        end,
      member: member
    }
  end

  defp scope_timeout("+CONTRACT " <> tail = text, "0.2.0-lab.1", m, line) do
    if String.contains?(tail, " max ") or String.starts_with?(tail, "@infinity") do
      case Regex.run(~r/^@([^ ]+)(?: max @?([^ ]+))? => ([a-z][a-z0-9_]*)$/, tail,
             capture: :all_but_first
           ) do
        [window, maximum, outcome] ->
          interval =
            if window == "infinity" do
              nil
            else
              duration(window, line)
            end

          ceiling =
            if maximum in ["", "infinity"] do
              nil
            else
              duration(maximum, line)
            end

          ensure(
            is_nil(ceiling) or (not is_nil(interval) and ceiling >= interval),
            :duration,
            line,
            "The maximum must be at least the initial window; infinity needs an infinite maximum"
          )

          {"+CONTRACT",
           %{
             profile: "band-contract/timeout/2",
             after_ms: interval,
             maximum_ms: ceiling,
             renewable: maximum != "" and not is_nil(interval),
             outcome: outcome,
             meta: m
           }}

        _ ->
          fail(:syntax, line, text)
      end
    else
      fixed_scope_timeout(text, m, line)
    end
  end

  defp scope_timeout(text, "0.2.0-lab.1", m, line) do
    fixed_scope_timeout(text, m, line)
  end

  defp fixed_scope_timeout(text, m, line) do
    case Regex.run(
           ~r/^(\+CONTRACT|\+STATE [a-z][a-z0-9_]*) @(\S+) => ([a-z][a-z0-9_]*)(.*)$/,
           text,
           capture: :all_but_first
         ) do
      [declaration, value, outcome, tail] ->
        {declaration <> tail,
         %{
           profile: "band-contract/timeout/1",
           after_ms: duration(value, line),
           outcome: outcome,
           meta: m
         }}

      _ ->
        {text, nil}
    end
  end

  defp field_type(text, line, engine, pattern_work) do
    if String.starts_with?(text, "text(") do
      utf8_text(text, line, pattern_work)
    else
      {field_type(text, line, engine), pattern_work}
    end
  end

  defp field_type(text, line, _engine) do
    cond do
      text == "boolean" ->
        %{kind: :boolean}

      Regex.match?(~r/^(enum|ref)\(~[Ww]/, text) ->
        case Regex.run(~r/^(enum|ref)\((~[Ww].*)\)$/, text, capture: :all_but_first) do
          [kind, literal] ->
            values = word_list(literal, :type, line)

            ensure(
              Enum.all?(values, &identifier?/1) and
                length(values) == length(Enum.uniq(values)),
              :type,
              line
            )

            %{
              kind:
                if kind == "enum" do
                  :enum
                else
                  :ref
                end,
              values: values
            }

          _ ->
            fail(:type, line)
        end

      Regex.match?(~r/^integer\([0-9]+\.\.[0-9]+\)$/, text) ->
        [_, low, high] = Regex.run(~r/^integer\(([0-9]+)\.\.([0-9]+)\)$/, text)
        low = String.to_integer(low)
        high = String.to_integer(high)
        ensure(low <= high and high <= 9_007_199_254_740_991, :type, line)
        %{kind: :integer, min: low, max: high}

      Regex.match?(~r/^(enum|ref)\([a-z][a-z0-9_|]*\)$/, text) ->
        [_, kind, values] = Regex.run(~r/^(enum|ref)\((.*)\)$/, text)
        values = String.split(values, "|")

        ensure(
          Enum.all?(values, &identifier?/1) and
            length(values) == length(Enum.uniq(values)),
          :type,
          line
        )

        %{
          kind:
            if kind == "enum" do
              :enum
            else
              :ref
            end,
          values: values
        }

      true ->
        fail(:type, line, text)
    end
  end

  defp utf8_text(text, line, pattern_work) do
    {text, pattern} =
      case Regex.split(~r/,\s*regex:\s*/, text, parts: 2) do
        [plain] ->
          {plain, nil}

        [bounds, literal] ->
          ensure(String.ends_with?(literal, ")"), :type, line)
          literal = binary_part(literal, 0, byte_size(literal) - 1)
          {bounds <> ")", pattern_literal(String.trim(literal), line)}
      end

    field = utf8_bounds(text, line)

    if is_nil(pattern) do
      {field, pattern_work}
    else
      case ContractLab.SafePattern.compile(pattern, field.bytes.max, pattern_work) do
        {:ok, compiled, spent} -> {Map.put(field, :regex, compiled), pattern_work - spent}
        {:error, reason} -> fail(reason, line, pattern_hint(reason, pattern))
      end
    end
  end

  # DF-10: the safe pattern subset has no shorthand classes; name the bracket spelling.
  @shorthand %{"d" => "[0-9]", "w" => "[A-Za-z0-9_]", "s" => "[ \\t\\r\\n]"}

  defp pattern_hint(:pattern_syntax, pattern) do
    case Regex.run(~r/(?<!\\)\\([dDwWsSbB])/, pattern, capture: :all_but_first) do
      [class] when is_map_key(@shorthand, class) ->
        "Shorthand class \\#{class} is not in the safe pattern subset: write #{@shorthand[class]}"

      [class] ->
        "\\#{class} is not in the safe pattern subset; spell the characters out in a [class]"

      nil ->
        "Pattern is outside the safe pattern subset"
    end
  end

  defp pattern_hint(_reason, _pattern), do: nil

  defp pattern_literal(<<"~", sigil, opening, rest::binary>>, line)
       when sigil in ~c"Rr" and opening in ~c"/|{" do
    ensure(byte_size(rest) <= 1025, :pattern_limit, line)

    closing =
      if opening == 123 do
        125
      else
        opening
      end

    ensure(sigil_closed?(rest, closing), :pattern_syntax, line)
    binary_part(rest, 0, byte_size(rest) - 1)
  end

  defp pattern_literal(literal, line) do
    case ContractLab.Wire.parse_json(literal, 8192) do
      {:ok, pattern} when is_binary(pattern) -> pattern
      _ -> fail(:pattern_syntax, line)
    end
  end

  defp sigil_closed?(<<closing>>, closing) do
    true
  end

  defp sigil_closed?(<<closing, _::binary>>, closing) do
    false
  end

  defp sigil_closed?(<<92, _, rest::binary>>, closing) do
    sigil_closed?(rest, closing)
  end

  defp sigil_closed?(<<_, rest::binary>>, closing) do
    sigil_closed?(rest, closing)
  end

  defp sigil_closed?(<<>>, _) do
    false
  end

  defp utf8_bounds(text, line) do
    case Regex.run(
           ~r/^text\((?:bytes:\s*)?([0-9]+(?:\.\.[0-9]+)?)(?:,\s*graphemes:\s*([0-9]+(?:\.\.[0-9]+)?))?\)$/,
           text,
           capture: :all_but_first
         ) do
      [bytes] ->
        %{kind: :text, encoding: "utf-8", bytes: text_bound(bytes, line), graphemes: nil}

      [bytes, graphemes] ->
        %{
          kind: :text,
          encoding: "utf-8",
          bytes: text_bound(bytes, line),
          graphemes: text_bound(graphemes, line)
        }

      _ ->
        fail(:type, line, text)
    end
  end

  defp text_bound(value, line) do
    {low, high} =
      case String.split(value, "..") do
        [max] -> {0, String.to_integer(max)}
        [min, max] -> {String.to_integer(min), String.to_integer(max)}
      end

    ensure(low <= high and high <= 9_007_199_254_740_991, :type, line)
    %{min: low, max: high}
  end

  defp host_trigger(text, c, state, m, line, id) do
    case Regex.run(
           ~r/^(?:ON|on) ((?:watch\.)?[a-z][a-z0-9_]*) (ONLINE|OFFLINE|REMOVED|online|offline|removed)(?: (?:FOR|for) (\S+))? => ([a-z][a-z0-9_]*)$/,
           text,
           capture: :all_but_first
         ) do
      [subject, condition, after_value, target] ->
        condition = String.downcase(condition)

        kind =
          if condition == "removed" do
            "membership"
          else
            "presence"
          end

        ensure("host.#{kind}/1" in c.card.extensions, :extension_required, line)
        ensure(condition != "removed" or after_value == "", :syntax, line, "Removal is immediate")

        valid_subject =
          if String.starts_with?(subject, "watch.") do
            String.replace_prefix(subject, "watch.", "") in c.extensions.watches
          else
            subject in c.roles
          end

        ensure(valid_subject, :unknown_role, line, subject)

        %{
          id: "host-#{id}",
          state: state,
          subject: subject,
          kind: kind,
          value: condition,
          after_ms:
            if after_value == "" do
              0
            else
              duration(after_value, line)
            end,
          target: target,
          meta: m
        }

      _ ->
        fail(:syntax, line, text)
    end
  end

  defp selector("*", roles, _line) do
    roles
  end

  defp selector(value, roles, line) do
    selected = value |> String.trim_leading("{") |> String.trim_trailing("}") |> String.split(",")
    ensure(selected != [] and Enum.all?(selected, &identifier?/1), :syntax, line)
    ensure(length(selected) == length(Enum.uniq(selected)), :duplicate, line)
    known = MapSet.new(roles)
    ensure(Enum.all?(selected, &MapSet.member?(known, &1)), :unknown_role, line, selected)
    selected
  end

  defp word_list(<<"~", sigil, literal::binary>>, code, line) when sigil in ~c"Ww" do
    values =
      literal |> raw_literal(code, line) |> :binary.split([" ", "\t"], [:global, :trim_all])

    ensure(values != [], code, line)
    values
  end

  defp selector(<<"~", sigil, _::binary>> = literal, roles, line, "0.2.0-lab.1")
       when sigil in ~c"Ww" do
    selected = word_list(literal, :syntax, line)
    ensure(length(selected) == length(Enum.uniq(selected)), :duplicate, line)
    known = MapSet.new(roles)
    ensure(Enum.all?(selected, &MapSet.member?(known, &1)), :unknown_role, line, selected)
    selected
  end

  defp selector(value, roles, line, _engine) do
    selector(value, roles, line)
  end

  defp duration(value, line) do
    case Regex.run(~r/^([1-9][0-9]*)(s|m|h|d)$/, value) do
      [_, n, unit] ->
        ms =
          String.to_integer(n) *
            %{"s" => 1000, "m" => 60000, "h" => 3_600_000, "d" => 86_400_000}[unit]

        ensure(ms <= 9_007_199_254_740_991, :duration, line)
        ms

      _ ->
        fail(:duration, line, value)
    end
  end

  defp rule_parts(text, "0.2.0-lab.1") do
    {event, text} =
      if directive?(text, "EVENT", "event", "0.2.0-lab.1") do
        {"EVENT ", binary_part(text, 6, byte_size(text) - 6)}
      else
        {"", text}
      end

    with [from, rest] <- String.split(text, " -> ", parts: 2),
         [to, message] <- String.split(rest, ": ", parts: 2),
         true <- route_selector?(from) and route_selector?(to),
         [kind, args, tail] <-
           Regex.run(~r/^([a-z][a-z0-9_]*)\(([^)]*)\)(.*)$/, message, capture: :all_but_first) do
      [event, from, to, kind, args, tail]
    else
      _ -> nil
    end
  end

  # ~r/^([a-z][a-z0-9_]*)=\$([a-z][a-z0-9_]*)$/ as a scan, in Regex.run's result shape.
  defp rule_argument(arg) do
    with [field, "$" <> binding] <- :binary.split(arg, "="),
         true <- identifier?(field) and identifier?(binding) do
      [arg, field, binding]
    else
      _ -> nil
    end
  end

  defp route_selector?(value) do
    String.starts_with?(value, "~W") or String.starts_with?(value, "~w") or
      Regex.match?(~r/^(\*|[a-z][a-z0-9_]*|\{[a-z0-9_,]+\})$/, value)
  end

  defp rule(text, roles, m, line, id, engine) do
    case rule_parts(text, engine) do
      [event, from, to, kind, args, tail] ->
        event = event == "EVENT "

        {options, target} =
          case String.split(String.trim(tail), "=>", parts: 2) do
            [opts, target] -> {String.split(opts), String.trim(target)}
            [opts] -> {String.split(opts), nil}
          end

        if Enum.any?(options, &String.starts_with?(&1, "@")) do
          fail(:syntax, line, timer_hint(event, target))
        end

        reset =
          if event and is_binary(target) and String.starts_with?(target, "@") do
            String.trim_leading(target, "@")
          else
            nil
          end

        if reset do
          ensure(
            reset in ["self", "state", "contract"],
            :timer_target,
            line
          )
        end

        target =
          if reset do
            nil
          else
            target
          end

        ensure(
          (event and target == nil) or
            (not event and is_binary(target) and Regex.match?(@identifier, target)),
          :syntax,
          line
        )

        opts = rule_options(options, %{bind: nil, each: false, activity: false}, line, engine)
        ensure(not (event and opts.each) and not (opts.each and opts.bind != nil), :syntax, line)

        matches =
          if String.trim(args) == "" do
            %{}
          else
            Enum.reduce(String.split(args, ","), %{}, fn arg, acc ->
              case rule_argument(String.trim(arg)) do
                [_, field, binding] ->
                  ensure(not Map.has_key?(acc, field), :duplicate, line)
                  Map.put(acc, field, binding)

                _ ->
                  fail(:syntax, line, arg)
              end
            end)
          end

        parsed_rule =
          Map.merge(opts, %{
            id: "rule-#{id}",
            from: selector(from, roles, line, engine),
            to: selector(to, roles, line, engine),
            kind: kind,
            matches: matches,
            event: event,
            target: target,
            meta: m
          })

        if reset do
          Map.put(parsed_rule, :reset, %{profile: "band-contract/renewal/1", scope: reset})
        else
          parsed_rule
        end

      _ ->
        fail(:syntax, line, role_set_hint(text))
    end
  end

  # DF-26: comma-separated role sets contain no spaces (contract-dsl.md grammar), so a spaced
  # set names the spelling that compiles instead of echoing the whole line.
  defp role_set_hint(text) do
    case Regex.run(~r/\{[a-z0-9_,]*[ \t][a-z0-9_, \t]*\}/, text) do
      [set] -> "Role sets have no spaces: write " <> String.replace(set, ~r/[ \t]+/, "")
      nil -> text
    end
  end

  # DF-10: a timer still among the options means the line missed every timed form, so name
  # the form the author most likely meant.
  defp timer_hint(true, _target),
    do:
      "A timed event ends at its outcome, optionally followed by @self: " <>
        "event x -> y: k() @15m => outcome @self. @state and @contract renewals are " <>
        "separate event rules without a timer"

  defp timer_hint(false, target) when is_binary(target) do
    if String.contains?(target, "|") do
      "Rule options (as, each, mentions) go before the timer: x -> y: k() as b @1h => state | outcome"
    else
      timer_hint(false, nil)
    end
  end

  defp timer_hint(false, _target),
    do: "A timed rule needs its timeout outcome: x -> y: k() @1h => state | outcome"

  defp rule_options([], opts, _, _) do
    opts
  end

  defp rule_options([word | _], _opts, line, "0.2.0-lab.1")
       when word in ~w(AS AWAITS EACH MENTIONS MENTION) do
    fail(:syntax, line, "Rule options are lowercase: write " <> String.downcase(word))
  end

  defp rule_options([word | _], _opts, line, "0.2.0-lab.1") when word in ~w(activity ACTIVITY) do
    fail(:syntax, line, "activity was removed; profile 2 has no idle timer")
  end

  defp rule_options([word, name | rest], %{bind: nil} = opts, line, engine)
       when word == "AS" or word == "as" do
    ensure(Regex.match?(@identifier, name), :syntax, line)
    rule_options(rest, %{opts | bind: name}, line, engine)
  end

  defp rule_options([word | rest], %{each: false} = opts, line, engine)
       when word == "EACH" or word == "each" do
    rule_options(rest, %{opts | each: true}, line, engine)
  end

  defp rule_options([word, value | rest], opts, line, "0.2.0-lab.1" = engine)
       when word in ["mentions", "mention"] and not is_map_key(opts, :mention_cap) do
    case ContractLab.Mentions.parse(value) do
      {:ok, cap} -> rule_options(rest, Map.put(opts, :mention_cap, cap), line, engine)
      :error -> fail(:syntax, line)
    end
  end

  defp rule_options(_, _, line, _) do
    fail(:syntax, line)
  end

  defp resolve_resets(c, timed_rules) do
    for r <- c.any_rules ++ c.room_rules,
        Map.has_key?(r, :reset) do
      fail(:timer_target, r.meta.line, "Reset events belong to one named state")
    end

    states =
      Map.new(c.states, fn {name, state} ->
        indexed = List.to_tuple(state.rules)

        rules =
          Enum.with_index(state.rules)
          |> Enum.map(fn {r, index} ->
            case Map.get(r, :reset) do
              nil ->
                r

              %{scope: "state"} ->
                ensure(
                  Map.has_key?(state, :timeout),
                  :timer_target,
                  r.meta.line,
                  "The state needs a declared clock"
                )

                r

              %{scope: "contract"} ->
                ensure(
                  get_in(c, [:timeout, :renewable]) == true,
                  :timer_target,
                  r.meta.line,
                  "Declare max @duration or max infinity on a finite contract window"
                )

                r

              %{scope: "self"} = reset when is_map_key(state, :wait) ->
                # In a wait state, @self renews the wait (DESIGN v2 §3, Renewal).
                %{r | reset: Map.merge(reset, %{scope: "wait", duty: state.wait.id})}

              %{scope: "self"} = reset ->
                next = if index + 1 < tuple_size(indexed), do: elem(indexed, index + 1)
                duty = next && timed_rules[next.id]

                ensure(
                  not is_nil(duty),
                  :timer_target,
                  r.meta.line,
                  "@self attaches to the immediately following rule, which must be a timed message rule or collect in the same state; only documentation and comments may come between"
                )

                if String.starts_with?(duty, "collection:") do
                  %{
                    r
                    | reset:
                        Map.merge(reset, %{
                          scope: "collection",
                          rule: next.id,
                          collection: String.replace_prefix(duty, "collection:", "")
                        })
                  }
                else
                  %{r | reset: Map.merge(reset, %{scope: "rule", rule: next.id, duty: duty})}
                end
            end
          end)

        {name, %{state | rules: rules}}
      end)

    c = %{c | states: states}

    if get_in(c, [:timeout, :profile]) == "band-contract/timeout/2" or
         Enum.any?(states, fn {_, s} -> Enum.any?(s.rules, &Map.has_key?(&1, :reset)) end) do
      Map.put(c, :renewal_profile, "band-contract/renewal/1")
    else
      c
    end
  end

  defp initial_window(timeout) do
    case Enum.reject([Map.get(timeout, :after_ms), Map.get(timeout, :maximum_ms)], &is_nil/1) do
      [] -> nil
      limits -> Enum.min(limits)
    end
  end

  defp validate(c) do
    for {role, roster} <- Map.get(c, :role_sets, %{}), roster.member != "agent" do
      ensure(
        Map.has_key?(Map.get(c, :participant_types, %{}), roster.member),
        :unknown_type,
        c.role_metadata[role].meta.line,
        roster.member
      )
    end

    ensure(Map.has_key?(c.states, "new"), :unknown_state, 1, "Initial state new is required")

    ensure(
      Map.has_key?(c.terminals, "expired"),
      :unknown_state,
      1,
      "Whole-session expiry requires expired"
    )

    if timeout = Map.get(c, :timeout) do
      entry_target(c, timeout.outcome, timeout.meta.line)

      ensure(
        Map.has_key?(c.terminals, timeout.outcome),
        :unknown_state,
        timeout.meta.line,
        timeout.outcome
      )

      ensure(
        is_nil(initial_window(timeout)) or
          Enum.all?(c.states["new"].deadlines, &(&1.after_ms <= initial_window(timeout))),
        :duration,
        timeout.meta.line,
        "The whole-run cap cannot shorten an initial response window"
      )
    end

    for {_, d} <- Map.get(c, :collections, %{}) do
      entry_target(c, d.target, d.meta.line)

      ensure(
        Map.has_key?(c.states, d.target) or Map.has_key?(c.terminals, d.target),
        :unknown_state,
        d.meta.line,
        d.target
      )

      # Static quorum errors point at the collect line; the compiled quorum carries no meta.
      if q = Map.get(d, :quorum) do
        entry_target(c, q.target, d.meta.line)

        ensure(
          Map.has_key?(c.states, q.target) or Map.has_key?(c.terminals, q.target),
          :unknown_state,
          d.meta.line,
          q.target
        )

        ensure(
          is_nil(q.attribute) or
            Enum.all?(d.from, fn role ->
              roster = get_in(c, [:role_sets, role])
              type = roster && get_in(c, [:participant_types, roster.member])
              get_in(type || %{}, [:fields, q.attribute, :kind]) == :boolean
            end),
          :type,
          d.meta.line,
          "quorum names a boolean attribute of every member's participant type"
        )
      end

      cap = get_in(c, [:states, d.state, :timeout, :after_ms])
      ensure(is_nil(cap) or d.after_ms <= cap, :duration, d.meta.line)
      global = initial_window(Map.get(c, :timeout, %{}))
      ensure(d.state != "new" or is_nil(global) or d.after_ms <= global, :duration, d.meta.line)
    end

    rules = c.any_rules ++ c.room_rules ++ Enum.flat_map(c.states, fn {_, s} -> s.rules end)

    bindings =
      for r <- rules, r.bind != nil do
        r.bind
      end

    for t <- Map.get(c, :extensions, %{triggers: []}).triggers do
      entry_target(c, t.target, t.meta.line)

      if t.kind == "membership" and t.subject in c.roles do
        ensure(
          Map.has_key?(c.terminals, t.target),
          :membership_outcome,
          t.meta.line,
          "Removing a bound agent terminates this session"
        )
      end

      ensure(
        Map.has_key?(c.states, t.target) or Map.has_key?(c.terminals, t.target),
        :unknown_state,
        t.meta.line,
        t.target
      )
    end

    for {_name, type} <- Map.merge(c.types, Map.get(c, :participant_types, %{})) do
      work =
        Enum.reduce(type.fields, 0, fn {_name, field}, total ->
          total +
            if Map.has_key?(field, :regex) do
              field.regex.max_steps
            else
              0
            end
        end)

      ensure(work <= ContractLab.SafePattern.work_limit(), :pattern_limit, type.meta.line)
    end

    for {_name, type} <- c.types,
        {_field, f} <- type.fields,
        f.kind == :ref do
      Enum.each(f.values, &ensure(Map.has_key?(c.types, &1), :unknown_type, f.meta.line, &1))
    end

    for r <- rules do
      entry_target(c, r.target, r.meta.line)
      ensure(Map.has_key?(c.types, r.kind), :unknown_type, r.meta.line, r.kind)

      if r.target do
        ensure(
          Map.has_key?(c.states, r.target) or Map.has_key?(c.terminals, r.target),
          :unknown_state,
          r.meta.line,
          r.target
        )
      end

      fields = c.types[r.kind].fields

      for {name, type} <- fields, type.kind == :ref do
        ensure(Map.has_key?(r.matches, name), :unmatched_reference, r.meta.line, name)
      end

      for {name, binding} <- r.matches do
        ensure(
          Map.has_key?(fields, name) and fields[name].kind == :ref,
          :unmatched_reference,
          r.meta.line,
          name
        )

        ensure(binding in bindings, :unknown_binding, r.meta.line, binding)
      end
    end

    # Anystate rules apply in every state: index their (kind, sender) pairs and (sender, kind)
    # keys once rather than rescanning them per state.
    any_senders = MapSet.new(for r <- c.any_rules, role <- r.from, do: {r.kind, role})
    any_keys = any_keys(c.any_rules)

    for {_name, s} <- c.states do
      ensure(not Map.has_key?(c.terminals, s.name), :duplicate, s.meta.line, s.name)
      senders = MapSet.new(for r <- s.rules, role <- r.from, do: {r.kind, role})
      # A reply block's wait is a response window of its state (DESIGN v2 §3).
      windows = s.deadlines ++ List.wrap(Map.get(s, :wait))

      if wait = Map.get(s, :wait) do
        entry_target(c, wait.outcome, wait.meta.line)

        ensure(
          Map.has_key?(c.terminals, wait.outcome),
          :unknown_state,
          wait.meta.line,
          wait.outcome
        )
      end

      if timeout = Map.get(s, :timeout) do
        entry_target(c, timeout.outcome, timeout.meta.line)

        ensure(
          Map.has_key?(c.terminals, timeout.outcome),
          :unknown_state,
          timeout.meta.line,
          timeout.outcome
        )

        ensure(
          Enum.all?(windows, &(&1.after_ms <= timeout.after_ms)),
          :duration,
          timeout.meta.line,
          "A state timeout cannot shorten a declared response window"
        )
      end

      if total = Map.get(s, :total) do
        entry_target(c, total.outcome, s.meta.line)

        ensure(
          Map.has_key?(c.terminals, total.outcome),
          :unknown_state,
          s.meta.line,
          total.outcome
        )

        ensure(
          Enum.all?(
            Enum.map(windows, & &1.after_ms) ++
              Enum.map(ContractLab.Collections.definitions(c, s.name), & &1.after_ms) ++
              List.wrap(get_in(s, [:timeout, :after_ms])),
            &(&1 <= total.after_ms)
          ),
          :duration,
          s.meta.line,
          "A state total cannot be shorter than its per-entry clock or a declared response window"
        )
      end

      if s.max_entries do
        entry_target(c, s.max_entries.outcome, s.meta.line)
        ensure(Map.has_key?(c.terminals, s.max_entries.outcome), :unknown_state, s.meta.line)
      end

      for d <- s.deadlines do
        entry_target(c, d.outcome, d.meta.line)
        ensure(Map.has_key?(c.types, d.kind), :unknown_type, d.meta.line, d.kind)
        ensure(Map.has_key?(c.terminals, d.outcome), :unknown_state, d.meta.line, d.outcome)

        for role <- d.participants do
          ensure(
            MapSet.member?(senders, {d.kind, role}) or
              MapSet.member?(any_senders, {d.kind, role}),
            :unsatisfiable_deadline,
            d.meta.line,
            role
          )
        end
      end

      unambiguous(s.rules, any_keys)
    end

    unambiguous(c.room_rules)
    mention_limits(c)
    c
  end

  defp mention_limits(%{engine: "0.2.0-lab.1"} = c) do
    rosters = Map.keys(Map.get(c, :role_sets, %{}))

    for rule <- Enum.sort_by(ContractLab.Mentions.rules(c), & &1.meta.line),
        not Enum.any?(rule.to, &(&1 in rosters)) do
      count = ContractLab.Mentions.widest(rule)

      ensure(
        ContractLab.Mentions.within?(c, rule, count),
        :mention_limit,
        rule.meta.line,
        "#{rule.id} reaches #{count} principals; the cap is #{ContractLab.Mentions.cap(c, rule)}"
      )
    end
  end

  defp declared_clock(%{engine: "0.2.0-lab.1"}, declared) do
    ensure(
      declared,
      :contract_clock_required,
      1,
      "Declare contract @<duration> => <outcome>; use contract @infinity => expired to opt out"
    )
  end

  defp opt_out?(text) do
    text == "+CONTRACT @infinity => expired"
  end

  defp entry_target(%{engine: "0.2.0-lab.1"}, "new", line) do
    fail(:initial_state_target, line, "new is the initial state and cannot be re-entered")
  end

  defp entry_target(_, _, _) do
    :ok
  end

  defp unambiguous(rules), do: unambiguous_keys(rules, MapSet.new())

  # A state's rules followed by the anystate rules, as unambiguous(s.rules ++ c.any_rules) but
  # without rescanning the anystate rules per state: the first anystate rule that clashes is the
  # first one holding a key of the state's rules, or the first that repeats an earlier anystate
  # key; it is checked again in order for the exact key reported.
  defp unambiguous(rules, any) do
    seen = unambiguous_keys(rules, MapSet.new())
    clashes = for key <- seen, index = any.first[key], do: index
    # Integers sort before atoms, so :infinity means no anystate rule clashes.
    first = Enum.min([any.repeat | clashes])

    if first != :infinity do
      seen = Enum.reduce(Enum.take(any.rules, first), seen, &keys_of/2)
      unambiguous_keys([Enum.at(any.rules, first)], seen)
    end

    :ok
  end

  defp any_keys(any_rules) do
    rules = uniq_alternatives(any_rules)

    {first, repeat, _} =
      Enum.reduce(rules, {%{}, nil, 0}, fn r, {first, repeat, index} ->
        {first, repeat} =
          Enum.reduce(r.from, {first, repeat}, fn role, {first, repeat} ->
            key = {role, r.kind}

            if Map.has_key?(first, key),
              do: {first, repeat || index},
              else: {Map.put(first, key, index), repeat}
          end)

        {first, repeat, index + 1}
      end)

    %{rules: rules, first: first, repeat: repeat || :infinity}
  end

  defp keys_of(r, seen), do: Enum.reduce(r.from, seen, &MapSet.put(&2, {&1, r.kind}))

  defp unambiguous_keys(rules, seen) do
    rules
    |> uniq_alternatives()
    |> Enum.reduce(seen, fn r, seen ->
      Enum.reduce(r.from, seen, fn role, seen ->
        key = {role, r.kind}
        ensure(not MapSet.member?(seen, key), :ambiguous_rule, r.meta.line, key)
        MapSet.put(seen, key)
      end)
    end)
  end

  # The alternatives of one wait share each (role, kind): they count once, so they still clash
  # with any other rule or event of that pair (DESIGN v2 §4, N1).
  defp uniq_alternatives(rules) do
    Enum.uniq_by(rules, fn r ->
      case Map.get(r, :wait) do
        nil -> r.id
        wait -> {wait, r.kind}
      end
    end)
  end

  defp strip_meta(value) when is_map(value) do
    Map.new(Map.delete(value, :meta), fn {k, v} -> {k, strip_meta(v)} end)
    |> Map.drop([:any_meta, :room_meta])
  end

  defp strip_meta(value) when is_list(value) do
    Enum.map(value, &strip_meta/1)
  end

  defp strip_meta(value) do
    value
  end

  defp unique(p, key, line) do
    ensure(not MapSet.member?(p.declared, key), :duplicate, line, key)
    %{p | declared: MapSet.put(p.declared, key)}
  end

  defp meta(doc, line) do
    %{doc: doc, line: line}
  end

  defp ensure(condition, code, line, detail \\ nil) do
    if condition do
      :ok
    else
      fail(code, line, detail)
    end
  end

  defp fail(code, line, detail \\ nil) do
    throw({:compile, code, line, detail})
  end
end
