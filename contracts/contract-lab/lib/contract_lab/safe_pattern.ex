defmodule ContractLab.SafePattern do
  @moduledoc "Bounded regular-language compiler and forward-only Unicode DFA matcher."
  @max_pattern_bytes 1024
  @max_nesting 16
  @max_nfa 128
  @max_dfa 128
  @max_edges 4096
  @compile_work 200_000
  @contract_compile_work 1_000_000
  @runtime_work 1_000_000
  @last_scalar 0x10FFFF

  def work_limit, do: @runtime_work
  def contract_compile_limit, do: @contract_compile_work

  def compile(source, max_bytes) do
    case compile(source, max_bytes, @compile_work) do
      {:ok, machine, _spent} -> {:ok, machine}
      error -> error
    end
  end

  # One contract shares a compile budget across its patterns. Each pattern may
  # spend at most the per-pattern limit and at most what the contract has left.
  def compile(source, max_bytes, budget)
      when is_binary(source) and is_integer(max_bytes) and max_bytes >= 0 and
             is_integer(budget) and budget >= 0 do
    budget = min(budget, @compile_work)

    try do
      limit(byte_size(source) <= @max_pattern_bytes)
      syntax(String.valid?(source))
      {ast, rest} = alternative(String.to_charlist(source), 0)
      syntax(rest == [])
      {start, nfa} = build(ast, 0, %{0 => :accept})
      alphabet = alphabet(nfa)
      initial = closure([start], nfa)
      {states, left} = determinize([initial], %{initial => 0}, [], nfa, alphabet, budget, 0)
      cost = length(states) + Enum.max(Enum.map(states, &length(&1.transitions))) + 2
      bound = (max_bytes + 1) * cost
      limit(bound <= @runtime_work)

      {:ok,
       %{
         profile: "band-contract/pattern-dfa/1",
         source: source,
         states: states,
         max_bytes: max_bytes,
         cost_per_scalar: cost,
         max_steps: bound
       }, budget - left}
    catch
      error when error in [:pattern_syntax, :pattern_limit] -> {:error, error}
    end
  end

  def compile(_, _, _), do: {:error, :pattern_syntax}

  def match(machine, text) when is_binary(text) do
    if byte_size(text) <= machine.max_bytes and String.valid?(text) do
      try do
        run(machine, text, 0, 0)
      catch
        :validation_limit -> {:error, :validation_limit}
      end
    else
      {:error, :invalid_text}
    end
  end

  def match(_, _), do: {:error, :invalid_text}

  # Each call consumes one scalar, or returns. There is no input rewind, recursive
  # alternative, capture stack, dynamic compilation, or fallback matching engine.
  defp run(m, text, state_id, steps) do
    steps = spend_runtime(steps, state_id + 1, m.max_steps)
    state = Enum.at(m.states, state_id)

    case text do
      "" ->
        {:ok, state.accept, %{steps: steps}}

      <<scalar::utf8, rest::binary>> ->
        {target, steps} = edge(state.transitions, scalar, steps, m.max_steps)
        if is_nil(target), do: {:ok, false, %{steps: steps}}, else: run(m, rest, target, steps)
    end
  end

  defp edge([], _, steps, _), do: {nil, steps}

  defp edge([[lo, hi, target] | rest], scalar, steps, cap) do
    steps = spend_runtime(steps, 1, cap)
    if scalar >= lo and scalar <= hi, do: {target, steps}, else: edge(rest, scalar, steps, cap)
  end

  defp spend_runtime(used, count, cap) do
    next = used + count
    if next > cap, do: throw(:validation_limit), else: next
  end

  # A small, closed grammar. Unsupported constructs are errors, not literalized
  # approximations. Parentheses only group; matching is always the entire value.
  defp alternative(chars, depth) do
    {first, rest} = sequence(chars, depth, [])
    alternatives(rest, depth, [first])
  end

  defp alternatives([?| | rest], depth, acc) do
    {next, rest} = sequence(rest, depth, [])
    alternatives(rest, depth, [next | acc])
  end

  defp alternatives(rest, _, [only]), do: {only, rest}
  defp alternatives(rest, _, acc), do: {{:alt, Enum.reverse(acc)}, rest}

  defp sequence(chars, depth, acc) do
    if chars == [] or hd(chars) in [?|, ?)] do
      syntax(acc != [])
      {{:seq, Enum.reverse(acc)}, chars}
    else
      {node, rest} = atom(chars, depth)

      {node, rest} =
        case rest do
          [op | tail] when op in [?*, ?+, ??] -> {{op, node}, tail}
          _ -> {node, rest}
        end

      sequence(rest, depth, [node | acc])
    end
  end

  defp atom([?( | rest], depth) do
    limit(depth < @max_nesting)
    {node, rest} = alternative(rest, depth + 1)

    case rest do
      [?) | tail] -> {node, tail}
      _ -> throw(:pattern_syntax)
    end
  end

  defp atom([?[ | rest], _) do
    {negative, rest} =
      case rest do
        [?^ | tail] -> {true, tail}
        _ -> {false, rest}
      end

    {ranges, rest} = character_class(rest, [])
    ranges = normalize(ranges)
    {{:char, if(negative, do: complement(ranges), else: ranges)}, rest}
  end

  defp atom([?. | rest], _), do: {{:char, [[0, 9], [11, @last_scalar]]}, rest}

  defp atom([?\\ | rest], _) do
    {char, tail} = escaped(rest)
    {{:char, [[char, char]]}, tail}
  end

  defp atom([char | rest], _) when char not in [?*, ?+, ??, ?), ?], ?{, ?}, ?^, ?$, ?|],
    do: {{:char, [[char, char]]}, rest}

  defp atom(_, _), do: throw(:pattern_syntax)

  defp escaped([char | rest]) when char in ~c"\\.[]()|*+?{}^$-/\"", do: {char, rest}
  defp escaped([?n | rest]), do: {?\n, rest}
  defp escaped([?r | rest]), do: {?\r, rest}
  defp escaped([?t | rest]), do: {?\t, rest}
  defp escaped(_), do: throw(:pattern_syntax)

  defp class_char([?\\ | rest]), do: escaped(rest)
  defp class_char([char | rest]) when char not in [?], ?[, ?-], do: {char, rest}
  defp class_char(_), do: throw(:pattern_syntax)

  defp character_class([?] | rest], acc) do
    syntax(acc != [])
    {Enum.reverse(acc), rest}
  end

  defp character_class(chars, acc) do
    limit(length(acc) < 64)
    {lo, rest} = class_char(chars)

    case rest do
      [?-, ?] | tail] ->
        limit(length(acc) + 2 <= 64)
        character_class([?] | tail], [[?-, ?-], [lo, lo] | acc])

      [?- | tail] ->
        {hi, tail} = class_char(tail)
        syntax(lo <= hi)
        character_class(tail, [[lo, hi] | acc])

      _ ->
        character_class(rest, [[lo, lo] | acc])
    end
  end

  defp normalize(ranges) do
    ranges
    |> Enum.sort()
    |> Enum.reduce([], fn [lo, hi], acc ->
      case acc do
        [[old_lo, old_hi] | rest] when lo <= old_hi + 1 -> [[old_lo, max(hi, old_hi)] | rest]
        _ -> [[lo, hi] | acc]
      end
    end)
    |> Enum.reverse()
  end

  defp complement(ranges) do
    {next, acc} =
      Enum.reduce(ranges, {0, []}, fn [lo, hi], {next, acc} ->
        {hi + 1, if(lo > next, do: [[next, lo - 1] | acc], else: acc)}
      end)

    Enum.reverse(if(next <= @last_scalar, do: [[next, @last_scalar] | acc], else: acc))
  end

  # Thompson construction. NFA nodes are bounded before allocation. Repetition
  # adds an edge to an existing node; it never expands a repetition count.
  defp put_node(nfa, node) do
    limit(map_size(nfa) < @max_nfa)
    id = map_size(nfa)
    {id, Map.put(nfa, id, node)}
  end

  defp build({:char, ranges}, next, nfa), do: put_node(nfa, {:char, ranges, next})

  defp build({:seq, nodes}, next, nfa),
    do:
      Enum.reduce(Enum.reverse(nodes), {next, nfa}, fn node, {next, nfa} ->
        build(node, next, nfa)
      end)

  defp build({:alt, nodes}, next, nfa) do
    {starts, nfa} = Enum.map_reduce(nodes, nfa, fn node, nfa -> build(node, next, nfa) end)
    put_node(nfa, {:epsilon, starts})
  end

  defp build({op, node}, next, nfa) when op in [?*, ?+] do
    {split, nfa} = put_node(nfa, {:epsilon, []})
    {start, nfa} = build(node, split, nfa)
    nfa = Map.put(nfa, split, {:epsilon, [start, next]})
    {if(op == ?*, do: split, else: start), nfa}
  end

  defp build({??, node}, next, nfa) do
    {start, nfa} = build(node, next, nfa)
    put_node(nfa, {:epsilon, [start, next]})
  end

  defp closure(start, nfa), do: close(start, nfa, MapSet.new()) |> MapSet.to_list() |> Enum.sort()
  defp close([], _, seen), do: seen

  defp close([id | rest], nfa, seen) do
    if MapSet.member?(seen, id) do
      close(rest, nfa, seen)
    else
      seen = MapSet.put(seen, id)

      case nfa[id] do
        {:epsilon, targets} -> close(targets ++ rest, nfa, seen)
        _ -> close(rest, nfa, seen)
      end
    end
  end

  defp alphabet(nfa) do
    cuts =
      Enum.flat_map(nfa, fn
        {_, {:char, ranges, _}} -> Enum.flat_map(ranges, fn [lo, hi] -> [lo, hi + 1] end)
        _ -> []
      end)

    cuts = Enum.sort(Enum.uniq([0, @last_scalar + 1 | cuts]))
    limit(length(cuts) <= 257)
    Enum.chunk_every(cuts, 2, 1, :discard) |> Enum.map(fn [lo, next] -> [lo, next - 1] end)
  end

  defp determinize([], _, built, _, _, budget, _), do: {Enum.reverse(built), budget}

  defp determinize([set | pending], ids, built, nfa, alphabet, budget, edges) do
    # Precharge an upper bound for scalar predicates plus the closure walk.
    cost =
      3 * map_size(nfa) +
        Enum.reduce(nfa, 0, fn
          {_, {:char, ranges, _}}, sum -> sum + length(ranges)
          {_, {:epsilon, targets}}, sum -> sum + length(targets)
          _, sum -> sum
        end)

    {transitions, pending, ids, budget, edges} =
      Enum.reduce(alphabet, {[], pending, ids, budget, edges}, fn [lo, hi],
                                                                  {ts, queue, ids, budget, edges} ->
        limit(budget >= cost)

        targets =
          Enum.flat_map(set, fn id ->
            case nfa[id] do
              {:char, ranges, target} ->
                if Enum.any?(ranges, fn [a, b] -> lo >= a and lo <= b end), do: [target], else: []

              _ ->
                []
            end
          end)
          |> closure(nfa)

        if targets == [] do
          {ts, queue, ids, budget - cost, edges}
        else
          {id, queue, ids} =
            case Map.fetch(ids, targets) do
              {:ok, id} ->
                {id, queue, ids}

              :error ->
                limit(map_size(ids) < @max_dfa)
                id = map_size(ids)
                {id, queue ++ [targets], Map.put(ids, targets, id)}
            end

          limit(edges < @max_edges)
          {[[lo, hi, id] | ts], queue, ids, budget - cost, edges + 1}
        end
      end)

    state = %{accept: 0 in set, transitions: Enum.reverse(transitions)}
    determinize(pending, ids, [state | built], nfa, alphabet, budget, edges)
  end

  defp syntax(true), do: :ok
  defp syntax(false), do: throw(:pattern_syntax)
  defp limit(true), do: :ok
  defp limit(false), do: throw(:pattern_limit)
end
