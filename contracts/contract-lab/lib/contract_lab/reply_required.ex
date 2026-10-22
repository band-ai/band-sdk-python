defmodule ContractLab.ReplyRequired do
  @moduledoc """
  `reply_required` (ubf-replies/old-form/RULE.md §1): the old send-then-answer form, a request
  whose answer is written as a separate rule instead of a reply block.

  - A *request* is an ordinary send p: not an event, a reply alternative, a head or a re-send
    (its target is no wait state), in a `state` or `anystate` section, from one role A to one
    role B (no group, roster (`role_sets`) or `each`: those are §3), into a nonterminal state W.
  - An *answer* is a rule r of W's own section, not an event, sent by B with A among its
    recipients, that moves the state. `anystate` rules are never answers.
  - p is an error when it has an answer and p is untimed or some answer is timed. A timed p
    whose answers are all untimed is an owed report, and B's moves after it are new decisions.

  The compiler reports the earliest request, after the reply_* checks and before
  contract_clock_required. The rule is a structural guard; RULE.md lists its known limits.
  """

  @doc "Every request that needs a reply block, earliest line first, with its first answer."
  def violations(c, timed_rules) do
    timed? = fn r -> match?("due-" <> _, Map.get(timed_rules, r.id)) end
    sends = for({_, s} <- c.states, r <- s.rules, do: r) ++ c.any_rules
    requests = Enum.filter(sends, &request?(c, &1, timed_rules))
    answers = answers(c, requests, timed?)

    for p <- requests,
        {first, any_timed} <- [Map.get(answers, {p.target, hd(p.to), hd(p.from)})],
        not timed?.(p) or any_timed do
      %{request: p, answer: first}
    end
    |> Enum.sort_by(& &1.request.meta.line)
  end

  @doc "Throw the compile error for the earliest violation, if any."
  def check(c, timed_rules) do
    case violations(c, timed_rules) do
      [] ->
        c

      [%{request: p, answer: r} | _] ->
        [a] = p.from
        [b] = p.to

        throw(
          {:compile, :reply_required, p.meta.line,
           "line #{r.meta.line} answers this request; write it as a reply head: " <>
             "#{a} -> #{b}: #{p.kind}() awaits #{p.target} => #{r.kind}() & #{r.target} | @d & T"}
        )
    end
  end

  defp request?(c, p, timed_rules) do
    w = c.states[p.target]

    rosters = Map.get(c, :role_sets, %{})
    single? = &(match?([_], &1) and hd(&1) in c.roles and not Map.has_key?(rosters, hd(&1)))

    not p.event and not p.each and not Map.has_key?(p, :wait) and
      not match?("collection:" <> _, Map.get(timed_rules, p.id)) and single?.(p.from) and
      single?.(p.to) and w != nil and not Map.has_key?(w, :wait)
  end

  # Per state W, sender B and recipient A that some request asks about: the earliest answer and
  # whether any answer is timed. Only requested (B, A) pairs are looked up, each from the smaller
  # side, so a group route never costs its senders x recipients (Eric 10-15: bounded parse time).
  defp answers(c, requests, timed?) do
    wanted =
      Enum.reduce(requests, %{}, fn p, acc ->
        Map.update(acc, {p.target, hd(p.to)}, MapSet.new(p.from), &MapSet.put(&1, hd(p.from)))
      end)

    for {name, s} <- c.states,
        r <- s.rules,
        not r.event,
        not Map.has_key?(r, :wait),
        r.target != nil,
        to = MapSet.new(r.to),
        b <- r.from,
        asked = Map.get(wanted, {name, b}),
        asked != nil,
        a <- if(MapSet.size(asked) <= MapSet.size(to), do: asked, else: to),
        MapSet.member?(asked, a) and MapSet.member?(to, a),
        reduce: %{} do
      acc ->
        Map.update(acc, {name, b, a}, {r, timed?.(r)}, fn {first, any} ->
          {if(r.meta.line < first.meta.line, do: r, else: first), any or timed?.(r)}
        end)
    end
  end
end
