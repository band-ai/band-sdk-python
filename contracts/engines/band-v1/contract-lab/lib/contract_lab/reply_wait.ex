defmodule ContractLab.ReplyWait do
  @moduledoc """
  Reply alternatives, engine side (`contract.replies/1`, DESIGN v2 §3, §5, §6).

  A state W with a `wait` is entered only by its heads. Entering it opens the wait: the head's
  recipient owes exactly one reply within `after_ms`, and that reply's kind and literals pick
  the next state. Leaving W by any path ends the wait; it never carries over, not even through
  a host transition. The wait fires as the work-duty timer `due-M`. Contracts without the
  extension never get the `wait` key, so their saved state and digests are unchanged.
  """
  @extension "contract.replies/1"
  @safe 9_007_199_254_740_991

  def extension, do: @extension
  def enabled?(c), do: @extension in Map.get(c.card, :extensions, [])

  @doc "Reply structures in a compiled object that does not declare the extension fail closed (S6)."
  def gate(c) do
    if structures?(c) and not enabled?(c), do: {:error, :unsupported_extension}, else: :ok
  end

  defp structures?(c) do
    Enum.any?(c.states, fn {_, state} ->
      Map.has_key?(state, :wait) or Enum.any?(state.rules, &reply_rule?/1)
    end) or Enum.any?(c.any_rules ++ c.room_rules, &reply_rule?/1)
  end

  defp reply_rule?(rule),
    do:
      Map.has_key?(rule, :literals) or Map.has_key?(rule, :wait) or
        match?(%{reset: %{scope: "wait"}}, rule)

  @doc """
  Every reply block is correlated (Lead decision 2): each head binds the same `as` name,
  each alternative matches a ref field that can hold the head's kind to that name, no rule in
  W or anystate rebinds it, and a reply kind is reply-only for its replier (SR3). There is no
  structural correlation: anything else is `reply_correlation`.
  """
  def correlation(c) do
    rules = Enum.flat_map(c.states, fn {_, state} -> state.rules end) ++ c.any_rules

    waits =
      for {name, state} <- c.states, Map.has_key?(state, :wait), do: {name, state.wait}

    replies = MapSet.new(for {_, w} <- waits, id <- w.alternatives, do: id)

    ok =
      Enum.all?(waits, fn {name, wait} ->
        heads = Enum.filter(rules, &(&1.id in wait.heads))
        binding = heads |> Enum.map(& &1.bind) |> Enum.uniq()

        case binding do
          [bind] when is_binary(bind) ->
            length(heads) == length(wait.heads) and
              Enum.all?(alternatives(c, name), &correlated_rule?(c, &1, bind, heads)) and
              not Enum.any?(c.states[name].rules ++ c.any_rules, &(&1.bind == bind)) and
              not Enum.any?(
                rules,
                &(not MapSet.member?(replies, &1.id) and &1.kind in wait.kinds and
                    Enum.any?(wait.from, fn role -> role in &1.from end))
              )

          _ ->
            false
        end
      end)

    if ok, do: :ok, else: {:error, :reply_correlation}
  end

  defp correlated_rule?(c, rule, bind, heads) do
    fields = get_in(c, [:types, rule.kind, :fields]) || %{}

    Enum.any?(rule.matches, fn {field, binding} ->
      binding == bind and
        match?(%{kind: :ref}, fields[field]) and
        Enum.all?(heads, &(&1.kind in fields[field].values))
    end)
  end

  @doc """
  A reply answers the open wait's own request: its correlated field names the head. Keyed on
  the current W's alternatives, not on the rule's own `wait` key (Sofia O3).
  """
  def answers_wait?(s, rule, payload) do
    case definition(s.contract, s.state) do
      %{id: id, alternatives: alternatives} ->
        if rule.id in alternatives do
          with %{id: ^id, head: head} <- Map.get(s, :wait),
               bind when is_binary(bind) <- head_bind(s.contract, s.state) do
            Enum.any?(rule.matches, fn {field, binding} ->
              binding == bind and payload[field] == head
            end)
          else
            _ -> false
          end
        else
          true
        end

      nil ->
        true
    end
  end

  defp head_bind(c, name) do
    heads = definition(c, name).heads

    c.states
    |> Enum.flat_map(fn {_, state} -> state.rules end)
    |> Enum.find_value(&(&1.id in heads && &1.bind))
  end

  def initial(c), do: if(enabled?(c), do: %{wait: nil}, else: %{})

  @doc "W's wait definition, or nil for any other state or terminal."
  def definition(c, name) do
    case c.states do
      %{^name => state} -> Map.get(state, :wait)
      _ -> nil
    end
  end

  def alternatives(c, name) do
    case definition(c, name) do
      nil -> []
      wait -> Enum.filter(c.states[name].rules, &(&1.id in wait.alternatives))
    end
  end

  @doc """
  7b: the candidate a payload selects. Without alternatives the candidate is the state's single
  rule of that (role, kind), as before. With alternatives, only the one whose literals all match
  is selected; nil when none does (never a fallback to the first rule).
  """
  def select(candidates, payload) do
    case Enum.filter(candidates, &Map.has_key?(&1, :literals)) do
      [] -> List.first(candidates)
      alternatives -> Enum.find(alternatives, &matches?(&1.literals, payload))
    end
  end

  defp matches?(literals, payload),
    do: Enum.all?(literals, fn {field, value} -> Map.fetch(payload, field) == {:ok, value} end)

  @doc """
  After `Engine.enter/4` settled the state: open W's wait when one of its heads entered it,
  else end any wait. `opener` is `{rule_id, message_id}` for an accepted message, else nil.
  """
  def enter(s, at, opener) do
    if Map.has_key?(s, :wait) do
      wait = if s.status == :active, do: definition(s.contract, s.state)

      %{
        s
        | wait:
            case {wait, opener} do
              {%{} = wait, {rule, message}} ->
                if rule in wait.heads,
                  do: %{
                    id: wait.id,
                    head: message,
                    due_at: at + wait.after_ms,
                    remaining: wait.from
                  }

              _ ->
                nil
            end
      }
    else
      s
    end
  end

  def close(s), do: if(Map.has_key?(s, :wait), do: %{s | wait: nil}, else: s)

  @doc "The open wait's timer: a work duty (priority 0) with ID `due-M`."
  def deadlines(%{wait: %{remaining: [_ | _]} = wait} = s) do
    [
      %{
        at: wait.due_at,
        priority: 0,
        id: wait.id,
        outcome: definition(s.contract, s.state).outcome,
        reason: :deadline,
        missing: wait.remaining
      }
    ]
  end

  def deadlines(_), do: []

  @doc """
  The wait as an internal reserve duty: one owed reply per (wait, participant), whichever kind
  (§6). It is never a compiled deadline row or a saved obligation; `reply_kinds` lets a closing
  path treat every alternative as already reserved.
  """
  def duty(wait) do
    %{
      id: wait.id,
      wait: true,
      quantifier: :all,
      participants: wait.from,
      remaining: wait.from,
      kind: wait.id,
      reply_kinds: wait.kinds,
      after_ms: wait.after_ms,
      outcome: wait.outcome
    }
  end

  @doc "The duties entering `name` creates: W is entered only by a head, which opens its wait."
  def entry_duties(c, name) do
    case definition(c, name) do
      nil -> []
      wait -> [duty(wait)]
    end
  end

  @doc "The wait duty an admission opens (a head of `target`'s wait) or keeps (staying in W)."
  def duties(s, rule, target, advances) do
    cond do
      not Map.has_key?(s, :wait) ->
        []

      advances ->
        case definition(s.contract, target) do
          %{heads: heads} = wait -> if rule.id in heads, do: [duty(wait)], else: []
          nil -> []
        end

      s.wait ->
        [duty(definition(s.contract, s.state))]

      true ->
        []
    end
  end

  @doc "Every state an alternative of W leads to: a reply may pick any of them."
  def targets(c, name), do: c |> alternatives(name) |> Enum.map(& &1.target) |> Enum.uniq()

  @doc "Alternatives as `{kind, literals}`: each smallest envelope must fit `max_bytes` (S3)."
  def envelopes(c) do
    for {name, _} <- c.states, rule <- alternatives(c, name), do: {rule.kind, rule.literals}
  end

  @doc "The wait renewal deadline, or `{:error, :timer_complete}` when no wait is open."
  def renewal(s, id) do
    case Map.get(s, :wait) do
      %{id: ^id, remaining: [_ | _]} -> s.now + definition(s.contract, s.state).after_ms
      _ -> {:error, :timer_complete}
    end
  end

  def renew(s, at), do: put_in(s, [:wait, :due_at], at)

  @doc """
  View of the open wait for a role it involves (`from` or `to`): its alternatives and every
  exit from W with its sender roles (S1, K-2). Any other role sees `wait: nil` even while a wait is open: who owes
  whom a reply is private to the two roles, like their facts (Sofia O4).
  """
  def view(s, role) do
    if Map.has_key?(s, :wait) do
      wait = s.wait
      definition = wait && definition(s.contract, s.state)

      if definition && (role in definition.from or role in definition.to) do
        %{
          wait:
            Map.merge(wait, %{
              alternatives:
                Enum.map(
                  alternatives(s.contract, s.state),
                  &%{rule: &1.id, kind: &1.kind, literals: &1.literals, target: &1.target}
                ),
              exits: exits(s.contract, s.state)
            })
        }
      else
        %{wait: nil}
      end
    else
      %{}
    end
  end

  @doc """
  Every exit from W (Sofia S1, K-2): each rule that applies in W, other than its reply
  alternatives, and moves the state, from `state W` and from `anystate`, with its sender roles,
  whether replier, requester or a third role. Participants see who else can end the wait.
  """
  def exits(c, name) do
    wait = definition(c, name)

    (c.states[name].rules ++ c.any_rules)
    |> Enum.filter(&(not &1.event and is_binary(&1.target) and &1.id not in wait.alternatives))
    |> Enum.map(&%{rule: &1.id, kind: &1.kind, target: &1.target, from: &1.from})
  end

  @doc "W's wait in a role's definition view, with every exit from W (K-2)."
  def definition_view(c, state) do
    case Map.get(state, :wait) do
      nil -> state
      wait -> %{state | wait: Map.put(wait, :exits, exits(c, state.name))}
    end
  end

  def schema(c) do
    if enabled?(c),
      do: [wait: {:nullable, [id: :id, head: :id, due_at: :uint, remaining: {:list, :id}]}],
      else: []
  end

  @doc "A saved wait belongs to W, to a head accepted into W, and to W's window."
  def valid_state?(c, s) do
    if Map.has_key?(s, :wait) do
      wait = if s.status == :active, do: definition(c, s.state)

      case {wait, s.wait} do
        {nil, nil} ->
          true

        {%{} = d, %{} = w} ->
          heads =
            for {_, state} <- c.states, rule <- state.rules, rule.id in d.heads, do: rule.kind

          receipt = get_in(s.receipts, [w.head, :decision])

          w.id == d.id and not is_nil(receipt) and receipt.kind in heads and
            Map.get(s.variables, head_bind(c, s.state)) == w.head and
            receipt.state == s.state and w.remaining == d.from and
            w.due_at >= s.entered_at + d.after_ms and w.due_at <= s.now + d.after_ms and
            w.due_at <= @safe

        _ ->
          false
      end
    else
      true
    end
  end
end
