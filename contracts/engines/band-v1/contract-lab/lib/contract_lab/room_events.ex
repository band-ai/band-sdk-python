defmodule ContractLab.RoomEvents do
  @moduledoc "Sessionless events with a separate host quota and bounded receipt retention."
  alias ContractLab.{Canonical, Engine, Wire}

  # Optional per-actor shares of the room-wide limits; each defaults to its room-wide value.
  @actor_limits [:max_events_per_actor, :max_mentions_per_actor, :max_receipts_per_actor]

  def new(c, opts) do
    bindings = opts[:bindings]
    now = opts[:now]
    policies = Keyword.take(opts, [:window_ms, :max_events, :max_mentions, :max_receipts])
    actor_limits = Keyword.take(opts, @actor_limits)

    cond do
      not Engine.valid_bindings?(c, bindings) ->
        {:error, :invalid_bindings}

      # The same bounds as Engine.new (Sofia set Q Q-L6): safe nonnegative time, positive safe limits.
      not Engine.valid_time?(now) ->
        {:error, :invalid_time}

      length(policies) != 4 or
          not Enum.all?(policies ++ actor_limits, fn {_, v} -> Engine.positive?(v) end) ->
        {:error, :room_policy_required}

      true ->
        room = %{
          contract: c,
          bindings: bindings,
          now: now,
          window_at: now,
          policy: Map.new(policies ++ actor_limits),
          events_used: 0,
          mentions_used: 0,
          receipts: %{}
        }

        # Per-actor window counts exist only when a host sets the matching per-actor limit.
        room =
          if Keyword.has_key?(actor_limits, :max_events_per_actor),
            do: Map.put(room, :actor_events, %{}),
            else: room

        room =
          if Keyword.has_key?(actor_limits, :max_mentions_per_actor),
            do: Map.put(room, :actor_mentions, %{}),
            else: room

        {:ok, room}
    end
  end

  def step(s, message, context) do
    now = context[:now]

    cond do
      not Engine.valid_time?(now) -> {reject(:invalid_time), s}
      now < s.now -> {reject(:clock_regression), s}
      true -> admit(window(s, now), message, context[:actor])
    end
  end

  defp admit(s, m, actor) do
    role = Enum.find_value(s.bindings, fn {r, a} -> if a == actor, do: r end)

    with true <- role != nil || {:error, :unauthorized},
         true <- (is_map(m) and not Map.has_key?(m, "session")) || {:error, :envelope},
         {:ok, _} <- Wire.encode(m),
         fingerprint <- Canonical.digest(%{message: m, actor: actor}) do
      case s.receipts[m["id"]] do
        %{fingerprint: ^fingerprint, decision: d} -> {%{status: :duplicate, original: d}, s}
        nil -> fresh(s, m, role, actor, fingerprint)
        _ -> {reject(:id_conflict), s}
      end
    else
      {:error, reason} -> {reject(reason), s}
    end
  end

  defp fresh(s, m, role, actor, fingerprint) do
    with rule when not is_nil(rule) <-
           Enum.find(s.contract.room_rules, &(role in &1.from and &1.kind == m["kind"])),
         nil <-
           Engine.payload_violation(s.contract.types[rule.kind], m["payload"])
           |> payload_error(),
         recipients <- Enum.reject(rule.to, &(&1 == role)) |> Enum.map(&s.bindings[&1]),
         true <-
           (map_size(s.receipts) < s.policy.max_receipts and
              actor_receipts(s, actor) < actor_limit(s, :max_receipts_per_actor, :max_receipts)) ||
             {:error, :retention_limit},
         true <-
           ContractLab.Mentions.within?(s.contract, rule, length(recipients)) ||
             {:error, :mention_limit},
         true <-
           (s.events_used < s.policy.max_events and
              actor_events(s, actor) < actor_limit(s, :max_events_per_actor, :max_events) and
              s.mentions_used + length(recipients) <= s.policy.max_mentions and
              actor_count(s, :actor_mentions, actor) + length(recipients) <=
                actor_limit(s, :max_mentions_per_actor, :max_mentions)) ||
             {:error, :room_budget} do
      decision = %{
        status: :accepted,
        id: m["id"],
        kind: m["kind"],
        payload: m["payload"],
        actor: actor,
        role: role,
        recipients: recipients,
        metadata: rule.meta,
        scope: :room
      }

      next = %{
        s
        | events_used: s.events_used + 1,
          mentions_used: s.mentions_used + length(recipients),
          receipts: Map.put(s.receipts, m["id"], %{fingerprint: fingerprint, decision: decision})
      }

      next =
        if Map.has_key?(s, :actor_events),
          do: %{next | actor_events: Map.update(s.actor_events, actor, 1, &(&1 + 1))},
          else: next

      next =
        if Map.has_key?(s, :actor_mentions),
          do: %{
            next
            | actor_mentions:
                Map.update(
                  s.actor_mentions,
                  actor,
                  length(recipients),
                  &(&1 + length(recipients))
                )
          },
          else: next

      {decision, next}
    else
      nil -> {reject(:illegal_message), s}
      {:error, {:payload, detail}} -> {%{status: :rejected, reason: :payload, detail: detail}, s}
      {:error, reason} -> {reject(reason), s}
    end
  end

  defp payload_error(nil), do: nil
  defp payload_error(detail), do: {:error, {:payload, detail}}

  defp window(s, now) do
    windows = div(now - s.window_at, s.policy.window_ms)

    if windows > 0 do
      %{
        s
        | now: now,
          window_at: s.window_at + windows * s.policy.window_ms,
          events_used: 0,
          mentions_used: 0
      }
      |> then(&if(Map.has_key?(&1, :actor_events), do: %{&1 | actor_events: %{}}, else: &1))
      |> then(&if(Map.has_key?(&1, :actor_mentions), do: %{&1 | actor_mentions: %{}}, else: &1))
    else
      %{s | now: now}
    end
  end

  defp actor_limit(s, key, room_key), do: Map.get(s.policy, key, s.policy[room_key])

  # Without a per-actor event limit the room-wide count bounds each actor already.
  defp actor_events(s, actor), do: actor_count(s, :actor_events, actor)

  defp actor_count(s, key, actor), do: s |> Map.get(key, %{}) |> Map.get(actor, 0)

  defp actor_receipts(s, actor) do
    if Map.has_key?(s.policy, :max_receipts_per_actor),
      do: Enum.count(s.receipts, fn {_, r} -> r.decision.actor == actor end),
      else: 0
  end

  defp reject(reason), do: %{status: :rejected, reason: reason}
end
