defmodule ContractLab.Mentions do
  @moduledoc """
  Per-message mention caps. On 0.2.0-lab.1 a rule's own `mentions N` wins,
  else the contract line's default, else there is no cap; there is no
  session-wide budget.
  """
  @safe_integer 9_007_199_254_740_991

  @doc "Parse the value after `mentions`/`mention`: a positive safe integer or `infinity` (no cap)."
  def parse("infinity"), do: {:ok, nil}

  def parse(value) when is_binary(value) do
    with true <- Regex.match?(~r/^[1-9][0-9]*$/, value),
         n when n <= @safe_integer <- String.to_integer(value) do
      {:ok, n}
    else
      _ -> :error
    end
  end

  @doc "The cap that applies to one message under `rule`, or nil for no cap."
  def cap(c, rule) do
    if Map.has_key?(rule, :mention_cap),
      do: rule.mention_cap,
      else: get_in(c, [Access.key(:mentions, %{}), Access.key(:max)])
  end

  @doc "The cumulative mention budget: profile 2 has none."
  def session_budget(_), do: :infinite

  @doc "Whether `count` delivered principals fit the rule's cap."
  def within?(c, rule, count) do
    case cap(c, rule) do
      nil -> true
      cap -> count <= cap
    end
  end

  @doc "The most principals one message under `rule` can reach: distinct recipients, sender excluded."
  def widest(rule) do
    recipients = Enum.uniq(rule.to)

    rule.from
    |> Enum.map(fn sender -> Enum.count(recipients, &(&1 != sender)) end)
    |> Enum.max(fn -> 0 end)
  end

  def rules(c),
    do: c.any_rules ++ c.room_rules ++ Enum.flat_map(c.states, fn {_, s} -> s.rules end)

  @doc """
  Session start: once rosters are expanded to their bound members, every
  rule's widest delivery must fit its cap. Fixed recipients were already
  checked at compile time.
  """
  def check(c) do
    if Enum.all?(rules(c), &within?(c, &1, widest(&1))),
      do: :ok,
      else: {:error, :mention_limit}
  end
end
