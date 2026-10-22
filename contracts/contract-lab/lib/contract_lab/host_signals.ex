defmodule ContractLab.HostSignals do
  @moduledoc "Bounded host-only presence and membership observations; no agent envelope can enter this path."
  alias ContractLab.{Canonical, Engine}
  @safe 9_007_199_254_740_991
  @fields ~w(id sequence subject principal kind value at)

  def initial(c, opts) do
    enabled = Enum.any?(c.card.extensions, &(&1 in ["host.presence/1", "host.membership/1"]))
    limit = Keyword.get(opts, :max_controls, 0)
    watches = Keyword.get(opts, :watch_bindings, %{})

    cond do
      not is_integer(limit) or limit < 0 or limit > 1024 or (enabled and limit < 2) ->
        {:error, :control_budget_required}

      not is_map(watches) or Enum.sort(Map.keys(watches)) != Enum.sort(c.extensions.watches) or
          not Enum.all?(Map.values(watches), &id?/1) ->
        {:error, :watch_bindings}

      true ->
        seed(c, opts, %{
          signals: %{},
          signal_revision: 0,
          signal_receipts: %{},
          fired_signals: %{},
          control_log: [],
          control_limit: limit,
          watch_bindings: watches
        })
    end
  end

  defp seed(c, opts, fields) do
    presence = Keyword.get(opts, :initial_presence, %{})
    now = Keyword.get(opts, :now)

    valid =
      is_map(presence) and
        (presence == %{} or
           (ContractLab.Collections.enabled?(c) and "host.presence/1" in c.card.extensions)) and
        Enum.all?(presence, fn {role, row} ->
          role in c.roles and is_map(row) and Enum.sort(Map.keys(row)) == ~w(since value) and
            row["value"] in ~w(online offline unknown) and is_integer(row["since"]) and
            is_integer(now) and row["since"] >= 0 and row["since"] <= now
        end)

    cond do
      not valid ->
        {:error, :invalid_initial_presence}

      presence == %{} ->
        {:ok, fields}

      true ->
        signals =
          Map.new(presence, fn {role, row} ->
            {role <> "/presence",
             Map.merge(row, %{"epoch" => "initial:" <> role, "sequence" => 1})}
          end)

        {:ok,
         Map.merge(fields, %{
           signals: signals,
           signal_revision: 1,
           control_log: [
             %{"id" => "initial-presence", "at" => now, "cause" => "initial_presence"}
           ],
           version: 1
         })}
    end
  end

  def signal(s, event, context) do
    with true <- Keyword.get(context, :authority) == :host || {:error, :host_authority_required},
         :ok <- shape(s, event),
         :ok <- subject(s, event) do
      fingerprint = Canonical.digest(event)

      case Map.get(s.signal_receipts, event["id"]) do
        %{"fingerprint" => ^fingerprint} -> {%{status: :duplicate_host_event}, s}
        nil -> accept(s, event, fingerprint, Keyword.get(context, :now))
        _ -> {reject(:id_conflict), s}
      end
    else
      {:error, reason} -> {reject(reason), s}
    end
  end

  defp accept(s, e, fingerprint, now) do
    cond do
      e["kind"] == "presence" and removed?(s, e["subject"]) ->
        {reject(:participant_removed), s}

      e["sequence"] <= s.signal_revision ->
        {reject(:stale_host_event), s}

      e["at"] != now or now < s.now ->
        {reject(:stale_host_event), s}

      Enum.any?(s.contract.extensions.triggers, &(now + &1.after_ms > @safe)) ->
        {reject(:invalid_time_range), s}

      true ->
        case Engine.tick(s, now) do
          {:error, reason, _} -> {reject(reason), s}
          {:ok, %{status: :terminal} = s} -> {reject(:terminal), s}
          {:ok, s} -> observe(s, e, fingerprint)
        end
    end
  end

  defp observe(s, e, fingerprint) do
    if length(s.control_log) >= s.control_limit - 1 do
      if e["kind"] == "membership" and e["subject"] in s.contract.roles,
        do: reserved_removal(s, e, fingerprint),
        else: {reject(:control_capacity), exhaust(s, e["at"])}
    else
      key = e["subject"] <> "/" <> e["kind"]
      previous = s.signals[key]
      same = previous && previous["value"] == e["value"]

      observation = %{
        "value" => e["value"],
        "since" => if(same, do: previous["since"], else: e["at"]),
        "epoch" => if(same, do: previous["epoch"], else: e["id"]),
        "sequence" => e["sequence"]
      }

      s = %{
        s
        | signals: Map.put(s.signals, key, observation),
          signal_revision: e["sequence"],
          signal_receipts: Map.put(s.signal_receipts, e["id"], %{"fingerprint" => fingerprint})
      }

      s =
        append(s, %{
          "id" => e["id"],
          "at" => e["at"],
          "cause" => e["kind"],
          "subject" => e["subject"],
          "value" => e["value"]
        })

      {:ok, s} = Engine.tick(s, e["at"])
      {%{status: :host_accepted}, s}
    end
  end

  defp reserved_removal(s, e, fingerprint) do
    trigger =
      Enum.find(
        s.contract.extensions.triggers,
        &(&1.kind == "membership" and &1.subject == e["subject"] and &1.state in ["*", s.state])
      )

    target = if trigger, do: trigger.target, else: "participant_removed"

    s = %{
      s
      | signal_revision: e["sequence"],
        signals:
          Map.put(s.signals, e["subject"] <> "/membership", %{
            "value" => "removed",
            "since" => e["at"],
            "epoch" => e["id"],
            "sequence" => e["sequence"]
          }),
        signal_receipts: Map.put(s.signal_receipts, e["id"], %{"fingerprint" => fingerprint})
    }

    s = Engine.host_transition(s, target, e["at"])

    s =
      append(s, %{
        "id" => e["id"],
        "at" => e["at"],
        "cause" => "membership",
        "subject" => e["subject"],
        "value" => "removed",
        "state" => s.state
      })

    {%{status: :host_accepted}, s}
  end

  def deadlines(s) do
    explicit =
      for t <- s.contract.extensions.triggers,
          t.state in ["*", s.state],
          observation = s.signals[t.subject <> "/" <> t.kind],
          not is_nil(observation),
          observation["value"] == t.value,
          key = t.id <> ":" <> observation["epoch"],
          not Map.has_key?(s.fired_signals, key) do
        %{
          at: max(s.entered_at, observation["since"] + t.after_ms),
          priority: 4,
          id: t.id,
          outcome: t.target,
          reason: :host_signal,
          key: key,
          subject: t.subject,
          kind: t.kind,
          epoch: observation["epoch"]
        }
      end

    implicit =
      for role <- s.contract.roles,
          observation = s.signals[role <> "/membership"],
          not is_nil(observation),
          observation["value"] == "removed",
          not Map.has_key?(s.fired_signals, "removed:" <> role <> ":" <> observation["epoch"]),
          not Enum.any?(explicit, &(&1.subject == role and &1.kind == "membership")) do
        %{
          at: max(s.entered_at, observation["since"]),
          priority: 4,
          id: "removed:" <> role,
          outcome: "participant_removed",
          reason: :host_signal,
          key: "removed:" <> role <> ":" <> observation["epoch"],
          subject: role,
          kind: "membership",
          epoch: observation["epoch"]
        }
      end

    explicit ++ implicit
  end

  def fire(s, timer, now) do
    if length(s.control_log) >= s.control_limit - 1 and
         not Map.has_key?(s.contract.terminals, timer.outcome) do
      exhaust(%{s | now: now}, timer.at)
    else
      fired = Map.put(s.fired_signals, timer.key, true)

      fired =
        if timer.kind == "membership",
          do: Map.put(fired, "removed:" <> timer.subject <> ":" <> timer.epoch, true),
          else: fired

      s = %{s | fired_signals: fired, now: now}
      s = Engine.host_transition(s, timer.outcome, timer.at)

      append(s, %{
        "id" => timer.id,
        "at" => timer.at,
        "cause" => "transition",
        "subject" => timer.subject,
        "target" => timer.outcome,
        "state" => s.state
      })
    end
  end

  defp exhaust(%{status: :terminal} = s, _), do: s

  defp exhaust(s, at) do
    s = Engine.host_transition(s, "control_capacity_exhausted", at)

    append(s, %{"id" => "control-capacity", "at" => at, "cause" => "capacity", "state" => s.state})
  end

  defp append(s, record),
    do: %{s | control_log: s.control_log ++ [record], version: s.version + 1}

  def removed?(s, role), do: match?(%{"value" => "removed"}, s.signals[role <> "/membership"])

  def valid_state?(c, s) do
    enabled = Enum.any?(c.card.extensions, &(&1 in ["host.presence/1", "host.membership/1"]))

    length(s.control_log) <= s.control_limit and s.control_limit <= 1024 and
      (enabled or
         (s.signals == %{} and s.signal_revision == 0 and s.signal_receipts == %{} and
            s.fired_signals == %{} and s.control_log == [])) and
      map_size(s.signal_receipts) <= length(s.control_log) and
      Enum.all?(s.signal_receipts, fn {id, receipt} ->
        id?(id) and
          match?(%{"fingerprint" => hash} when is_binary(hash) and byte_size(hash) == 64, receipt) and
          map_size(receipt) == 1
      end) and
      Enum.sort(Map.keys(s.watch_bindings)) == Enum.sort(c.extensions.watches) and
      Enum.all?(s.control_log, fn row ->
        is_integer(row["at"]) and row["at"] >= s.created_at and row["at"] <= s.now
      end) and
      Enum.all?(s.signals, fn {key, row} ->
        parts = String.split(key, "/")
        subject = parts |> Enum.drop(-1) |> Enum.join("/")
        kind = List.last(parts)

        valid_subject =
          subject in c.roles or
            (String.starts_with?(subject, "watch.") and
               String.replace_prefix(subject, "watch.", "") in c.extensions.watches)

        # Host events are timed at now >= created_at, so only the seeded row can
        # predate creation; same-value events carry its since and epoch forward
        # while the sequence advances.
        length(parts) >= 2 and "host.#{kind}/1" in c.card.extensions and valid_subject and
          ((kind == "presence" and row["value"] in ~w(online offline unknown)) or
             (kind == "membership" and row["value"] == "removed")) and
          Enum.sort(Map.keys(row)) == ~w(epoch sequence since value) and
          is_integer(row["since"]) and row["since"] >= 0 and row["since"] <= s.now and
          (row["since"] >= s.created_at or
             (row["epoch"] == "initial:" <> subject and
                Enum.any?(s.control_log, &(&1["cause"] == "initial_presence")))) and
          is_integer(row["sequence"]) and row["sequence"] > 0 and
          row["sequence"] <= s.signal_revision and
          row["value"] in ~w(online offline unknown removed) and id?(row["epoch"]) and
          Enum.all?(c.extensions.triggers, &(row["since"] + &1.after_ms <= @safe))
      end)
  end

  defp shape(s, e) when is_map(e) do
    cond do
      Enum.sort(Map.keys(e)) != Enum.sort(@fields) ->
        {:error, :invalid_host_event}

      not Enum.all?(~w(id subject principal), &id?(e[&1])) ->
        {:error, :invalid_host_event}

      not is_integer(e["sequence"]) or e["sequence"] <= 0 or e["sequence"] > @safe ->
        {:error, :invalid_host_event}

      not is_integer(e["at"]) or e["at"] < 0 or e["at"] > @safe ->
        {:error, :invalid_host_event}

      e["kind"] not in ["presence", "membership"] ->
        {:error, :invalid_host_event}

      "host.#{e["kind"]}/1" not in s.contract.card.extensions ->
        {:error, :unsupported_extension}

      e["kind"] == "presence" and e["value"] not in ~w(online offline unknown) ->
        {:error, :invalid_host_event}

      e["kind"] == "membership" and e["value"] != "removed" ->
        {:error, :invalid_host_event}

      true ->
        :ok
    end
  end

  defp shape(_, _), do: {:error, :invalid_host_event}

  defp subject(s, e) do
    principal =
      if String.starts_with?(e["subject"], "watch."),
        do: s.watch_bindings[String.replace_prefix(e["subject"], "watch.", "")],
        else: s.bindings[e["subject"]]

    if principal == e["principal"], do: :ok, else: {:error, :subject_binding}
  end

  defp id?(v), do: is_binary(v) and byte_size(v) in 1..256 and String.valid?(v)
  defp reject(reason), do: %{status: :rejected, reason: reason}
end
