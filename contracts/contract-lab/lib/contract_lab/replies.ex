defmodule ContractLab.Replies do
  @moduledoc """
  Reply alternatives (ubf-replies/DESIGN.md v2 §2–§5, written in syntax U: SYNTAX-OPTIONS.md):
  the static checks of reply blocks and their compiled form. A block is the send that declares
  a wait state W (its *head*), the `=> reply() & state` / `| reply() & state` alternatives that
  each lead to a state, and one `| @duration & outcome` timeout.

  A contract without reply blocks is returned unchanged, so its normalized output and digest
  are byte-identical to a compiler without this feature. With reply blocks, the compiled object
  records `contract.replies/1` among its extensions, so engines that lack the capability refuse
  it (fail closed).

  Literal values stay strings or JSON booleans; nothing here creates an atom.
  """
  @extension "contract.replies/1"

  def extension, do: @extension

  @doc """
  Check the parsed blocks (source order) and fold them into the contract.

  This is the boundary between the surface syntax and everything else: a front end (today
  syntax U in `ContractLab.Compiler`) only has to produce, per block,

      %{head: rule, state: w, implicit: boolean, section: head_state, line: head_line,
        alternatives: [%{rule: rule, literals: [{field, raw}], line: line}],
        timeout: nil | %{id: "due-M", after_ms: ms, outcome: name, meta: meta}}

  where `head` is the compiled send (target W, bound as written or as W's name), `implicit`
  says W is the implied `<kind>_wait`, each alternative `rule` is an ordinary compiled rule
  (from the replier to the requester, `event: false`) numbered `rule-N+1…` with the `=$` it
  wrote, and `raw` literal values are unchecked identifier strings. Every reply_* check, the
  correlation sugar, the compiled shape and the numbering contract live here, so a surface
  syntax swaps only the front end.
  """
  def compile(c, [], _timed_rules), do: c

  def compile(c, blocks, timed_rules) do
    names(c, blocks)
    c = implied_binds(c, blocks)
    waits = Enum.map(blocks, &block(c, &1))

    Enum.reduce(waits, MapSet.new(), fn w, seen ->
      ensure(
        not MapSet.member?(seen, w.state),
        :reply_route,
        w.line,
        "#{w.state} already has a reply block; a re-send is a -> b: k() => #{w.state}, with no alternatives"
      )

      MapSet.put(seen, w.state)
    end)

    c = Enum.reduce(waits, c, &merge/2)
    # Every check below reads one index built here, so their cost is linear in the contract
    # rather than in waits x rules (Eric 10-15: bounded parse time on untrusted source).
    index = index(c)
    c = Enum.reduce(waits, c, &routes(&2, &1, timed_rules, index))
    Enum.each(waits, &correlation(c, &1, index))
    cyclic = cyclic(graph(c))
    Enum.each(waits, &cycle(c, &1, cyclic))
    update_in(c, [:card], &Map.put(&1, :extensions, Enum.sort([@extension | extensions(&1)])))
  end

  defp extensions(card), do: Map.get(card, :extensions, [])

  # W's name (Theo's U PRODUCTIONS.md, Lead decision 7): a W equals no terminal, and an implied
  # `<kind>_wait` equals no declared state, so a `state W` section requires `awaits`. W is
  # never the initial state or the state that holds its head (Sofia U4). Reported at the later
  # of the two lines. Two blocks for one W, named or implied, are not a name clash: compile/3
  # refuses the later one with reply_route.
  defp names(c, blocks) do
    for b <- blocks do
      # T1-2 (Sofia): a timed head never awaits its own state, or its duty and its wait would
      # coexist on every entry and `@self` would be ambiguous.
      if Map.get(b, :timed, false) and b.state == b.section do
        fail(:reply_route, b.line, "A timed head awaits another state, never its own")
      end

      clash =
        cond do
          terminal = c.terminals[b.state] -> terminal.meta.line
          b.implicit and Map.has_key?(c.states, b.state) -> c.states[b.state].meta.line
          b.state in [b.section, "new"] -> get_in(c.states, [b.state, :meta, :line]) || b.line
          true -> nil
        end

      if clash, do: fail(:duplicate, max(clash, b.line), b.state)
    end
  end

  # The sugar's head binding: a send into W that writes no `as` binds W's name, as a head
  # does, so a plain re-send `a -> b: k() => W` matches a head that wrote none.
  defp implied_binds(c, blocks) do
    waits = MapSet.new(blocks, & &1.state)

    bind = fn r ->
      if r.bind == nil and not r.event and not r.each and MapSet.member?(waits, r.target),
        do: %{r | bind: r.target},
        else: r
    end

    states = Map.new(c.states, fn {name, s} -> {name, %{s | rules: Enum.map(s.rules, bind)}} end)
    %{c | states: states, any_rules: Enum.map(c.any_rules, bind)}
  end

  # The rules, triggers and collections each check looks up, grouped once, each group in the
  # order the per-wait scans used to visit them.
  defp index(c) do
    state_rules = Enum.flat_map(c.states, fn {_, s} -> s.rules end)
    collections = Map.get(c, :collections, %{})

    touching =
      Enum.reduce(collections, %{}, fn {_, d}, acc ->
        [d.target, get_in(d, [:quorum, :target]), d.state]
        |> Enum.reject(&is_nil/1)
        |> Enum.uniq()
        |> Enum.reduce(acc, &Map.update(&2, &1, [d], fn ds -> [d | ds] end))
      end)

    waits = for {name, s} <- c.states, Map.has_key?(s, :wait), into: MapSet.new(), do: name

    moves = Enum.reject(c.any_rules, & &1.event)
    counters = Enum.filter(moves, &counter?(c, &1, waits))

    %{
      # A1: anystate rules that apply in a wait and are neither a counter-request (a rule into
      # another wait) nor an exit into a terminal, by sender role.
      any_stays:
        moves
        |> Enum.reject(&(counter?(c, &1, waits) or terminal?(c, &1)))
        |> Enum.flat_map(fn r -> Enum.map(r.from, &{&1, r}) end)
        |> Enum.group_by(&elem(&1, 0), &elem(&1, 1)),
      # A1-2: anystate counter-requests by sender, and how many of a sender's reach each role,
      # so a wait finds the ones that skip its requester without scanning them all.
      any_counters:
        counters
        |> Enum.flat_map(fn r -> Enum.map(r.from, &{&1, r}) end)
        |> Enum.group_by(&elem(&1, 0), &elem(&1, 1)),
      any_counters_to:
        Enum.reduce(counters, %{}, fn r, acc ->
          for b <- r.from, a <- Enum.uniq(r.to), reduce: acc do
            acc -> Map.update(acc, {b, a}, 1, &(&1 + 1))
          end
        end),
      waits: waits,
      sends: Enum.group_by(state_rules, & &1.target),
      any_by_target: Enum.group_by(c.any_rules, & &1.target),
      any_by_kind: Enum.group_by(c.any_rules, & &1.kind),
      any_by_bind: Enum.group_by(c.any_rules, & &1.bind),
      triggers: Enum.group_by(c.extensions.triggers, & &1.target),
      collections: Map.new(touching, fn {state, ds} -> {state, Enum.reverse(ds)} end),
      by_kind:
        (c.any_rules ++ c.room_rules ++ state_rules)
        |> Enum.reject(&Map.has_key?(&1, :wait))
        |> Enum.group_by(& &1.kind)
    }
  end

  # A1 exits from a wait: a rule into a terminal, or a counter-request, which is a head or a
  # re-send of another block (routes/4 of that block checks that it is one) and, under A1-2,
  # includes W's requester among its recipients.
  defp terminal?(c, r), do: is_binary(r.target) and Map.has_key?(c.terminals, r.target)

  defp counter?(c, r, waits),
    do: is_binary(r.target) and MapSet.member?(waits, r.target) and not terminal?(c, r)

  defp leaves?(c, r, w, waits) do
    terminal?(c, r) or
      (r.target != w.state and counter?(c, r, waits) and Enum.any?(w.head.from, &(&1 in r.to)))
  end

  # Per block: the timeout, the head's route and binding, each row (reply_match,
  # reply_correlation), then reply_match, reply_overlap and reply_not_exhaustive across rows.
  defp block(c, b) do
    head = b.head

    ensure(
      b.timeout != nil,
      :reply_timeout,
      b.line,
      "A reply block ends with one timeout @duration => outcome"
    )

    rosters = Map.get(c, :role_sets, %{})

    ensure(
      not Enum.any?(head.from ++ head.to, &Map.has_key?(rosters, &1)),
      :reply_route,
      b.line,
      "A reply block's send names one role on each side, not a roster"
    )

    # Lead decision 2: correlation is always required (the front end binds W's name when the
    # head writes no `as`).
    ensure(
      head.bind != nil,
      :reply_correlation,
      b.line,
      "Bind the send that declares the wait: a -> b: k() as req awaits wait"
    )

    # Single-line checks for every row first, then the checks across rows (Lead decision 3).
    alternatives = Enum.map(b.alternatives, &single(c, &1, head))
    matching(alternatives)
    overlap(alternatives)
    coverage(c, alternatives, b.line)

    %{
      state: head.target,
      head: head,
      line: b.line,
      alternatives:
        Enum.map(alternatives, fn a ->
          Map.merge(a.rule, %{literals: Map.new(a.literals), wait: b.timeout.id})
        end),
      wait: %{
        id: b.timeout.id,
        heads: [head.id],
        from: head.to,
        to: head.from,
        kinds: alternatives |> Enum.map(& &1.rule.kind) |> Enum.uniq(),
        after_ms: b.timeout.after_ms,
        outcome: b.timeout.outcome,
        alternatives: Enum.map(alternatives, & &1.rule.id),
        meta: b.timeout.meta
      }
    }
  end

  # One row: its literals, its `as` name and its correlation to the head.
  defp single(c, a, head) do
    a = literals(c, a)

    ensure(
      is_nil(a.rule.bind) or a.rule.bind != head.bind,
      :reply_match,
      a.line,
      "An alternative's as name differs from the head's"
    )

    a = implied_reference(c, a, head)

    ensure(
      Enum.any?(c.types[a.rule.kind].fields, fn {field, type} ->
        type.kind == :ref and head.kind in type.values and a.rule.matches[field] == head.bind
      end),
      :reply_correlation,
      a.line,
      "Match a ref(#{head.kind}) field of #{a.rule.kind} to $#{head.bind}"
    )

    a
  end

  # The sugar's reply reference: a row that writes no `=$` on any ref field whose kinds include
  # the head's gets `field=$<head binding>` when its type has exactly one such field. With none
  # or several the row stays as written, and the check after this reports reply_correlation.
  defp implied_reference(c, a, head) do
    fields =
      for {field, type} <- Enum.sort(c.types[a.rule.kind].fields),
          type.kind == :ref and head.kind in type.values,
          do: field

    case fields do
      [field] when not is_map_key(a.rule.matches, field) ->
        put_in(a, [:rule, :matches, field], head.bind)

      _ ->
        a
    end
  end

  # A literal names an enum member or true/false of a boolean field (DESIGN §2, Literals).
  defp literals(c, a) do
    kind = a.rule.kind
    ensure(Map.has_key?(c.types, kind), :unknown_type, a.line, kind)
    fields = c.types[kind].fields

    resolved =
      Enum.map(a.literals, fn {field, raw} ->
        value =
          case Map.get(fields, field) do
            %{kind: :enum, values: values} ->
              if raw in values, do: raw, else: :invalid

            %{kind: :boolean} ->
              Map.get(%{"true" => true, "false" => false}, raw, :invalid)

            _ ->
              fail(:reply_match, a.line, "#{field} is not an enum or boolean field of #{kind}")
          end

        ensure(
          value != :invalid,
          :reply_match,
          a.line,
          "#{raw} is not a value of #{kind}.#{field}"
        )

        {field, value}
      end)

    %{a | literals: resolved}
  end

  # Alternatives of one kind carry the same references; a block binds one `as` name or none
  # (DESIGN §3, Bindings), reported at the later row.
  defp matching(alternatives) do
    [first | _] = alternatives

    Enum.reduce(alternatives, %{}, fn a, refs ->
      refs = Map.put_new(refs, a.rule.kind, a.rule.matches)

      ensure(
        refs[a.rule.kind] == a.rule.matches,
        :reply_match,
        a.line,
        "Alternatives of #{a.rule.kind} carry the same =$ references"
      )

      ensure(
        a.rule.bind == first.rule.bind,
        :reply_match,
        a.line,
        "The alternatives of a block bind the same as name or none"
      )

      refs
    end)
  end

  # Two rows of one kind overlap when they agree on every field they both name; reported at
  # the later row (pairwise, O(n²·k)).
  defp overlap(alternatives) do
    alternatives
    |> Enum.with_index()
    |> Enum.each(fn {a, j} ->
      for b <- Enum.take(alternatives, j), b.rule.kind == a.rule.kind, overlaps?(a, b) do
        fail(
          :reply_overlap,
          a.line,
          "Some #{a.rule.kind} payload matches both this row and line #{b.line}"
        )
      end
    end)
  end

  defp overlaps?(a, b) do
    left = Map.new(a.literals)
    Enum.all?(b.literals, fn {field, value} -> Map.get(left, field, value) == value end)
  end

  # With disjoint rows, coverage is a count: per kind, the rows' payload counts over the
  # fields any row names sum to the product of those fields' domain sizes.
  defp coverage(c, alternatives, line) do
    alternatives
    |> Enum.group_by(& &1.rule.kind)
    |> Enum.sort_by(fn {_, [a | _]} -> a.line end)
    |> Enum.each(fn {kind, rows} ->
      fields = c.types[kind].fields
      named = rows |> Enum.flat_map(fn a -> Enum.map(a.literals, &elem(&1, 0)) end) |> Enum.uniq()
      size = Map.new(named, &{&1, domain(fields[&1])})
      total = Enum.reduce(named, 1, &(size[&1] * &2))

      covered =
        Enum.reduce(rows, 0, fn a, sum ->
          set = MapSet.new(a.literals, &elem(&1, 0))
          sum + Enum.reduce(named, 1, &if(MapSet.member?(set, &1), do: &2, else: size[&1] * &2))
        end)

      ensure(
        covered == total,
        :reply_not_exhaustive,
        line,
        "The #{kind} rows cover #{covered} of #{total} combinations of #{Enum.join(named, ", ")}"
      )
    end)
  end

  defp domain(%{kind: :boolean}), do: 2
  defp domain(%{kind: :enum, values: values}), do: length(values)

  defp merge(w, c) do
    state =
      Map.get(c.states, w.state) ||
        %{
          name: w.state,
          rules: [],
          deadlines: [],
          max_entries: nil,
          # Lead decision 6: an undeclared W takes its doc from the head's prompt.
          meta: %{doc: w.head.meta.doc, line: w.line}
        }

    state = %{state | rules: w.alternatives ++ state.rules} |> Map.put(:wait, w.wait)
    %{c | states: Map.put(c.states, w.state, state)}
  end

  # Only W's heads enter W: the declaring head and its re-sends (same sender, recipient, kind
  # and as name, untimed, in a named state). `state W` holds no timed rule, no collect and no
  # state-moving rule of the replier (S1).
  defp routes(c, w, timed_rules, index) do
    head = w.head
    ids = MapSet.new(w.wait.alternatives)
    sends = for r <- Map.get(index.sends, w.state, []), r.id != head.id, do: r

    for r <- Map.get(index.any_by_target, w.state, []), r.id != head.id do
      fail(:reply_route, r.meta.line, "Only the sends of #{w.state}'s reply block enter it")
    end

    resends =
      for r <- Enum.sort_by(sends, & &1.meta.line) do
        # A4: an entry may be of another kind (one wait for several request kinds).
        ensure(
          not Map.has_key?(r, :wait) and not r.event and r.from == head.from and r.to == head.to and
            not Map.has_key?(timed_rules, r.id),
          :reply_route,
          r.meta.line,
          "Only a re-send of line #{w.line} (same roles and binding, untimed: a -> b: k() => #{w.state}) enters #{w.state}"
        )

        ensure(
          r.bind == head.bind,
          :reply_correlation,
          r.meta.line,
          "Every send that enters #{w.state} binds as #{head.bind}"
        )

        r
      end

    for t <- Map.get(index.triggers, w.state, []) do
      fail(:reply_route, t.meta.line, "Only the sends of #{w.state}'s reply block enter it")
    end

    for d <- Map.get(index.collections, w.state, []) do
      ensure(
        d.target != w.state and get_in(d, [:quorum, :target]) != w.state,
        :reply_route,
        d.meta.line,
        "Only the sends of #{w.state}'s reply block enter it"
      )

      ensure(d.state != w.state, :reply_route, d.meta.line, "A wait state holds no collect")
    end

    for r <- c.states[w.state].rules, not MapSet.member?(ids, r.id), not r.event do
      ensure(
        not Map.has_key?(timed_rules, r.id),
        :reply_route,
        r.meta.line,
        "A wait state holds no timed rule; its timeout is the reply block's"
      )

      # A1: the replier leaves W only by a counter-request to W's requester (A1-2) or into a
      # terminal.
      ensure(
        not Enum.any?(r.from, &(&1 in head.to)) or leaves?(c, r, w, index.waits),
        :reply_route,
        r.meta.line,
        "The replier leaves #{w.state} only through the reply block, a counter-request to #{Enum.join(head.from, ", ")} (a head or re-send of another block) or a rule into an end terminal"
      )
    end

    # A1 closes the anystate loophole: an anystate rule of the replier applies in W too, and
    # its counter-requests must reach W's requester (A1-2).
    [requester] = head.from

    skipped =
      for role <- head.to,
          counters = Map.get(index.any_counters, role, []),
          length(counters) > Map.get(index.any_counters_to, {role, requester}, 0),
          r <- counters,
          requester not in r.to,
          do: r

    stays = for(role <- head.to, r <- Map.get(index.any_stays, role, []), do: r) ++ skipped

    with [r | _] <- Enum.sort_by(stays, & &1.meta.line) do
      fail(
        :reply_route,
        r.meta.line,
        "#{r.kind} applies in #{w.state}, where #{Enum.join(head.to, ", ")} leaves only through the reply block, a counter-request or a rule into an end terminal"
      )
    end

    # K-1 (Sofia, SECURITY-CODE.md): while W waits, its reply kinds come only from the replier,
    # so no other role records such a fact or moves the state with it.
    any = Enum.flat_map(w.wait.kinds, &Map.get(index.any_by_kind, &1, []))

    for r <- Enum.sort_by(c.states[w.state].rules ++ any, & &1.meta.line),
        not MapSet.member?(ids, r.id),
        r.kind in w.wait.kinds,
        Enum.any?(r.from, &(&1 not in head.to)) do
      fail(
        :reply_route,
        r.meta.line,
        "In #{w.state}, #{r.kind} comes only from #{Enum.join(head.to, ", ")}, through its alternatives"
      )
    end

    # A4: every alternative's correlated ref lists every kind that enters W.
    for r <- resends, r.kind != head.kind, a <- w.alternatives do
      ensure(
        Enum.any?(c.types[a.kind].fields, fn {field, type} ->
          type.kind == :ref and r.kind in type.values and a.matches[field] == head.bind
        end),
        :reply_correlation,
        r.meta.line,
        "#{r.kind} enters #{w.state}, so #{a.kind}'s ref to $#{head.bind} lists #{r.kind}"
      )
    end

    heads = [head | resends] |> Enum.sort_by(& &1.meta.line) |> Enum.map(& &1.id)
    put_in(c, [:states, w.state, :wait, :heads], heads)
  end

  # While W waits, nothing rebinds the head's name; and a (replier, kind) used as an
  # alternative is reply-only: no ordinary rule or event outside a reply block accepts it, so a
  # late reply cannot be taken there (Lead decision 2; Sofia SR3).
  defp correlation(c, w, index) do
    head = w.head

    # A1: a rule (not an event) that rebinds W's name moves the state, which ends the wait.
    for r <- c.states[w.state].rules ++ Map.get(index.any_by_bind, head.bind, []),
        Map.get(r, :wait) != w.wait.id,
        r.id != head.id,
        r.bind == head.bind,
        r.event or r.target == nil do
      fail(
        :reply_correlation,
        r.meta.line,
        "#{head.bind} cannot be rebound by an event while #{w.state} waits"
      )
    end

    rules = Enum.flat_map(w.wait.kinds, &Map.get(index.by_kind, &1, []))

    for r <- Enum.sort_by(rules, & &1.meta.line),
        Enum.any?(r.from, &(&1 in w.wait.from)) do
      fail(
        :reply_correlation,
        r.meta.line,
        "#{r.kind} from #{Enum.join(w.wait.from, ", ")} is a reply of line #{w.line}; only reply blocks accept it"
      )
    end
  end

  # A W that can reach itself needs max_entries (S2); the graph has every edge (SR4).
  defp cycle(c, w, cyclic) do
    if MapSet.member?(cyclic, w.state) do
      ensure(
        c.states[w.state].max_entries != nil,
        :reply_cycle,
        w.line,
        "#{w.state} lies on a cycle: declare state #{w.state} max_entries N => outcome"
      )
    end
  end

  # Every state a rule, trigger, collect, clock or deadline of a state can move to. Clock and
  # deadline outcomes must be terminals, but are counted anyway (SR4). Anystate rules and `*`
  # triggers apply in every state, so they hang off one virtual node each (:any, :star) rather
  # than being copied into every state: the graph stays linear in the contract.
  #
  # A1b: an anystate head does not apply in its own wait. Its wait reaches the other anystate
  # targets through :plain and through prefix/suffix chains over the anystate-head waits h1..hk
  # ({:pre, i} reaches h1..hi, {:suf, i} reaches hi..hk), so hi skips only itself and the graph
  # stays linear.
  defp graph(c) do
    known = &Map.has_key?(c.states, &1)
    wait? = &Map.has_key?(c.states[&1], :wait)
    any_all = for r <- c.any_rules, r.target, known.(r.target), do: r.target
    heads = any_all |> Enum.filter(wait?) |> Enum.uniq() |> Enum.sort()
    any = Enum.reject(any_all, wait?) ++ if(heads == [], do: [], else: [{:pre, length(heads)}])
    plain = Enum.reject(any_all, wait?)
    at = heads |> Enum.with_index(1) |> Map.new()
    k = length(heads)

    chains =
      for {h, i} <- at, reduce: %{} do
        acc ->
          acc
          |> Map.put({:pre, i}, [h | if(i > 1, do: [{:pre, i - 1}], else: [])])
          |> Map.put({:suf, i}, [h | if(i < k, do: [{:suf, i + 1}], else: [])])
      end

    star = for t <- c.extensions.triggers, t.state == "*", known.(t.target), do: t.target

    triggers =
      for t <- c.extensions.triggers, t.state != "*", reduce: %{} do
        acc -> Map.update(acc, t.state, [t.target], &[t.target | &1])
      end

    collections =
      for {_, d} <- Map.get(c, :collections, %{}),
          t <- [d.target | List.wrap(get_in(d, [:quorum, :target]))],
          reduce: %{} do
        acc -> Map.update(acc, d.state, [t], &[t | &1])
      end

    virtual = if(any == [], do: [], else: [:any]) ++ if(star == [], do: [], else: [:star])

    c.states
    |> Map.new(fn {name, s} ->
      clocks =
        [Map.get(s, :timeout), Map.get(s, :total), s.max_entries, Map.get(s, :wait) | s.deadlines]
        |> Enum.reject(&is_nil/1)
        |> Enum.map(& &1.outcome)

      targets =
        (for(r <- s.rules, r.target, do: r.target) ++
           clocks ++ Map.get(collections, name, []) ++ Map.get(triggers, name, []))
        |> Enum.filter(known)

      virtual =
        case at do
          %{^name => i} ->
            [:plain] ++
              if(i > 1, do: [{:pre, i - 1}], else: []) ++
              if(i < k, do: [{:suf, i + 1}], else: []) ++
              List.delete(virtual, :any)

          _ ->
            virtual
        end

      {name, virtual ++ targets}
    end)
    |> Map.merge(chains)
    |> Map.put(:plain, plain)
    |> Map.put(:any, any)
    |> Map.put(:star, star)
  end

  # The nodes on a cycle (Kosaraju: a strongly connected component with more than one node, or
  # a node with an edge to itself), in time linear in nodes plus edges.
  defp cyclic(graph) do
    {order, _} =
      Enum.reduce(Map.keys(graph), {[], MapSet.new()}, fn v, {order, seen} ->
        if MapSet.member?(seen, v),
          do: {order, seen},
          else: finish(graph, [{v, Map.get(graph, v, [])}], order, MapSet.put(seen, v))
      end)

    reverse =
      for {v, us} <- graph, u <- us, reduce: %{} do
        acc -> Map.update(acc, u, [v], &[v | &1])
      end

    {cyclic, _} =
      Enum.reduce(order, {MapSet.new(), MapSet.new()}, fn v, {cyclic, seen} ->
        if MapSet.member?(seen, v) do
          {cyclic, seen}
        else
          {component, seen} = component(reverse, [v], MapSet.put(seen, v), [])

          case component do
            [only] ->
              if only in Map.get(graph, only, []),
                do: {MapSet.put(cyclic, only), seen},
                else: {cyclic, seen}

            _ ->
              {Enum.into(component, cyclic), seen}
          end
        end
      end)

    cyclic
  end

  # Iterative depth-first search; `order` gets each node as it finishes, so its head finished last.
  defp finish(_graph, [], order, seen), do: {order, seen}
  defp finish(graph, [{v, []} | stack], order, seen), do: finish(graph, stack, [v | order], seen)

  defp finish(graph, [{v, [u | rest]} | stack], order, seen) do
    if MapSet.member?(seen, u),
      do: finish(graph, [{v, rest} | stack], order, seen),
      else:
        finish(graph, [{u, Map.get(graph, u, [])}, {v, rest} | stack], order, MapSet.put(seen, u))
  end

  defp component(_reverse, [], seen, acc), do: {acc, seen}

  defp component(reverse, [v | rest], seen, acc) do
    next = reverse |> Map.get(v, []) |> Enum.uniq() |> Enum.reject(&MapSet.member?(seen, &1))
    component(reverse, next ++ rest, Enum.into(next, seen), [v | acc])
  end

  defp ensure(condition, code, line, detail) do
    if condition, do: :ok, else: fail(code, line, detail)
  end

  defp fail(code, line, detail), do: throw({:compile, code, line, detail})
end
