defmodule ContractLab.Reserve do
  @moduledoc """
  Known response capacity: each remaining duty reserves its reply and the reserve that
  reply's own admission will need (DF-56), so the engine never accepts a duty whose reply
  it would then refuse. A follow-on that leads back into the same cycle of owed replies
  counts nothing, so a cycle keeps a finite reserve.
  """

  @doc "Reply pairs owed by `duties`: every remaining participant, or one witness for `any`."
  def witnesses(duties) do
    for(
      duty <- duties,
      participant <-
        if(duty.quantifier == :any, do: Enum.take(duty.remaining, 1), else: duty.remaining),
      do: {duty.kind, participant}
    )
    |> Enum.uniq()
  end

  @doc "Messages to keep for `duties` remaining in state `name`: the pairs plus each follow-on."
  def reserve(c, name, duties) do
    length(witnesses(duties)) +
      (duties
       |> Enum.filter(&(&1.remaining != []))
       |> Enum.map(&follow(c, name, &1, duties))
       |> Enum.sum())
  end

  @doc "The duties a state creates on entry, before any reply."
  def entry_duties(c, name) do
    Enum.map(c.states[name].deadlines, &Map.put(&1, :remaining, &1.participants)) ++
      ContractLab.Collections.duties(c, name) ++ ContractLab.ReplyWait.entry_duties(c, name)
  end

  # What the duty's reply will itself reserve: the reserve of the state it enters (the worse
  # of a quorum collection's two targets, DF-25), or, for a timed event that stays, the
  # closing path its own admission adds.
  defp follow(c, name, duty, duties) do
    case reply(c, name, duty) do
      {:event, _} ->
        ContractLab.Closing.reserve(%{contract: c, state: name}, List.delete(duties, duty))

      {:enters, targets} ->
        targets
        |> Enum.map(fn target ->
          if Map.has_key?(c.states, target) and not cycle?(c, name, target),
            do: reserve(c, target, entry_duties(c, target)),
            else: 0
        end)
        |> Enum.max(fn -> 0 end)

      nil ->
        0
    end
  end

  # A collection reply closes into the collection's target; a duty's own rule is the one the
  # compiler created with it (`due-N` follows `rule-(N-1)`), else the state's rule of that
  # kind from those senders.
  # A reply wait (§6) enters whichever state its alternatives name, so it keeps the worst.
  defp reply(c, name, %{wait: true}), do: {:enters, ContractLab.ReplyWait.targets(c, name)}

  defp reply(c, name, duty) do
    collection =
      unless Map.has_key?(duty, :id),
        do:
          Enum.find(
            ContractLab.Collections.definitions(c, name),
            &(&1.reply == duty.kind and &1.from == duty.participants)
          )

    rules = c.states[name].rules ++ c.any_rules

    rule =
      with "due-" <> n <- Map.get(duty, :id),
           {n, ""} <- Integer.parse(n),
           %{} = rule <- Enum.find(rules, &(&1.id == "rule-#{n - 1}" and &1.kind == duty.kind)) do
        rule
      else
        _ ->
          Enum.find(rules, fn rule ->
            rule.kind == duty.kind and Enum.all?(duty.participants, &(&1 in rule.from))
          end)
      end

    cond do
      collection -> {:enters, ContractLab.Closing.close_targets(collection)}
      is_nil(rule) -> nil
      rule.event -> {:event, rule}
      is_binary(rule.target) -> {:enters, [rule.target]}
      true -> nil
    end
  end

  # Both states lie on one cycle of owed replies (a state re-entered by its own reply too).
  defp cycle?(c, from, to), do: from == to or (reaches?(c, from, to) and reaches?(c, to, from))

  defp reaches?(c, from, to), do: walk(c, [from], MapSet.new(), to)

  defp walk(_, [], _, _), do: false

  defp walk(c, [name | rest], seen, to) do
    next =
      for duty <- entry_duties(c, name),
          {:enters, targets} <- [reply(c, name, duty)],
          target <- targets,
          Map.has_key?(c.states, target),
          not MapSet.member?(seen, target),
          do: target

    to in next or walk(c, rest ++ next, MapSet.union(seen, MapSet.new([name | next])), to)
  end
end
