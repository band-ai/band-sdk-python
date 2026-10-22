defmodule ContractLab.Engine do
  @moduledoc """
  Pure, transport-independent contract decisions using a trusted actor and clock.

  Hosted adapters must build options with `HostInputs.new_opts/3` and restore with
  `Checkpoint.restore/4`: `room_policy: :unchecked` here, and `StateCodec.restore/4`,
  skip the hosted-profile gate and are only for trusted in-process local tooling.
  """
  alias ContractLab.{Canonical, ReplyWait, RoomPolicy}
  @safe_integer 9_007_199_254_740_991
  # Extensions this engine implements; any other fails closed even if the host lists it (S6).
  @implemented ~w(band.permissions/1 contract.replies/1 host.membership/1 host.presence/1 room.contributions/1)

  def new(%{engine: "0.2.0-lab.1"} = c, opts) do
    with {:ok, bound, options, fields} <- ContractLab.Roster.prepare(c, opts),
         {:ok, s} <- new_bound(bound, options),
         do: tick(Map.merge(s, fields), s.now)
  end

  def new(_, _), do: {:error, :unsupported_engine}

  defp new_bound(c, opts) do
    bindings = Keyword.get(opts, :bindings)
    now = Keyword.get(opts, :now)
    id = Keyword.get(opts, :session_id)

    policy =
      Keyword.get(opts, :inactivity, :accepted)

    max_messages = Keyword.get(opts, :max_messages)
    max_bytes = Keyword.get(opts, :max_bytes, 16_384)
    host_fields = ContractLab.HostSignals.initial(c, opts)

    host_policy =
      if valid_bindings?(c, bindings),
        do: ContractLab.RoomPolicy.bind(c, bindings, Keyword.get(opts, :room_policy)),
        else: :ok

    admission = admissible(c, Keyword.get(opts, :capabilities, []))

    cond do
      admission != :ok ->
        admission

      policy not in [:accepted, :transition, :marked] ->
        {:error, :inactivity_policy_required}

      not valid_bindings?(c, bindings) ->
        {:error, :invalid_bindings}

      host_policy != :ok ->
        host_policy

      match?({:error, _}, host_fields) ->
        host_fields

      ContractLab.Mentions.check(c) != :ok ->
        {:error, :mention_limit}

      not valid_time?(now) or not valid_id?(id) or not positive?(max_messages) or
          not positive?(max_bytes) ->
        {:error, :invalid_config}

      not time_range?(
        now,
        [duration_ms(c.card.lifetime)] ++
            state_durations(c.states["new"]) ++
            Enum.map(ContractLab.Collections.definitions(c, "new"), & &1.after_ms) ++
            [get_in(c, [:timeout, :maximum_ms])]
      ) ->
        {:error, :invalid_time_range}

      not replies_fit?(c, id, max_bytes) ->
        {:error, :completion_size}

      true ->
        s = %{
          contract: c,
          id: id,
          bindings: bindings,
          now: now,
          created_at: now,
          entered_at: now,
          last_activity: now,
          max_at: deadline_at(now, c.card.lifetime),
          idle_ms: nil,
          inactivity: policy,
          max_messages: max_messages,
          max_bytes: max_bytes,
          mentions_used: 0,
          state: "new",
          status: :active,
          version: 0,
          outcome: nil,
          expiry: nil,
          expired_at: nil,
          entries: %{},
          obligations: [],
          contributions: %{},
          variables: %{},
          facts: %{},
          receipts: %{},
          history: []
        }

        {:ok, fields} = host_fields

        # Persisted so a hosted adapter can refuse to restore or step it.
        s =
          if Keyword.get(opts, :room_policy) == :unchecked,
            do: Map.put(s, :policy_mode, :unchecked),
            else: s

        {:ok,
         enter(
           s
           |> Map.merge(fields)
           |> Map.merge(ContractLab.Renewal.initial(c, now))
           |> Map.merge(ContractLab.Collections.initial(c))
           |> Map.merge(ContractLab.Contributions.initial(c))
           |> Map.merge(ContractLab.Totals.initial(c))
           |> Map.merge(ReplyWait.initial(c)),
           "new",
           now
         )}
    end
  end

  # The completion reserve counts messages only, so a reply that a duty or collection in any
  # state can require must also be representable: refuse a cap below its smallest envelope.
  # A wait's alternatives count one by one, with their literal values (S3): a cap may not
  # quietly leave only the shorter of two replies.
  defp replies_fit?(c, session, max_bytes) do
    c.states
    |> Enum.flat_map(fn {name, state} ->
      Enum.map(state.deadlines, &{&1.kind, %{}}) ++
        Enum.map(ContractLab.Collections.definitions(c, name), &{&1.reply, %{}})
    end)
    |> Kernel.++(ReplyWait.envelopes(c))
    |> Enum.uniq()
    |> Enum.all?(fn {kind, literals} ->
      min_envelope_bytes(c, kind, session, literals) <= max_bytes
    end)
  end

  # A lower bound on any valid reply's canonical size: 1-byte ID, no expected_version, every
  # field at its shortest value (or its literal). Text minimums are added, not built (they
  # reach 2^53 bytes).
  defp min_envelope_bytes(c, kind, session, literals) do
    fields = Map.get(c.types, kind, %{fields: %{}}).fields

    {payload, text} =
      Enum.reduce(fields, {%{}, 0}, fn {name, field}, {payload, text} ->
        {value, more} =
          case Map.fetch(literals, name) do
            {:ok, literal} -> {literal, 0}
            :error -> shortest(field)
          end

        {Map.put(payload, name, value), text + more}
      end)

    message = %{"id" => "a", "session" => session, "kind" => kind, "payload" => payload}
    byte_size(Canonical.encode(message)) + text
  end

  # {witness, bytes beyond it}: each grapheme is at least one byte.
  defp shortest(%{kind: :text, bytes: bytes, graphemes: graphemes}),
    do: {"", max(bytes.min, if(graphemes, do: graphemes.min, else: 0))}

  defp shortest(%{kind: :text, min: min}), do: {"", min}
  defp shortest(%{kind: :integer, min: min}), do: {min, 0}
  defp shortest(%{kind: :boolean}), do: {true, 0}
  defp shortest(%{kind: :enum, values: values}), do: {Enum.min_by(values, &byte_size/1), 0}
  defp shortest(%{kind: :ref}), do: {"a", 0}

  @doc "Every extension this engine implements: what a full reference host can state."
  def implemented, do: @implemented

  @doc """
  The contract-level refusals of `new/2`, which every restore path applies too (Sofia O1): the
  host's `capabilities` must cover the contract's extensions, this engine must implement each
  one, reply structures need `contract.replies/1`, and reply blocks must be correlated. A caller
  that states no capabilities has none, at start and on restore alike (fail closed).
  """
  def admissible(c, capabilities) do
    cond do
      not RoomPolicy.supported?(c, capabilities) or
        not RoomPolicy.supported?(c, @implemented) or ReplyWait.gate(c) != :ok ->
        {:error, :unsupported_extension}

      ReplyWait.enabled?(c) and ReplyWait.correlation(c) != :ok ->
        {:error, :reply_correlation}

      true ->
        :ok
    end
  end

  def valid_bindings?(c, bindings) when is_map(bindings) do
    Enum.sort(Map.keys(bindings)) == Enum.sort(c.roles) and
      Enum.all?(Map.values(bindings), &valid_id?/1) and
      length(Enum.uniq(Map.values(bindings))) == length(c.roles)
  end

  def valid_bindings?(_, _), do: false

  def step(state, message, context) do
    case tick(state, Keyword.get(context, :now)) do
      {:error, reason, _} ->
        {reject(reason), state}

      {:ok, state} ->
        actor = Keyword.get(context, :actor)

        with :ok <-
               RoomPolicy.evidence(Keyword.get(context, :room_policy), RoomPolicy.mode(state)),
             nil <- resend(state, message, actor),
             :ok <- member_authorize(state, context) do
          state |> admit(message, actor) |> mark_unchecked()
        else
          {:error, reason} -> {reject(reason), state}
          {%{status: :duplicate}, _} = duplicate -> mark_unchecked(duplicate)
        end
    end
    |> mark_unchecked()
  end

  # An exact resend by its author is answered from the receipt before current access is
  # rechecked: a current loss of access is not evidence that the original was rejected.
  # Only a well-formed, encodable envelope is looked up; anything else falls through to the
  # normal access and admission checks, the same as for a fresh ID (N-4).
  defp resend(s, message, actor) do
    with :ok <- envelope(message),
         {:ok, _} <- encode_message(message),
         %{fingerprint: fingerprint, decision: decision} <- Map.get(s.receipts, message["id"]),
         ^fingerprint <- Canonical.digest(%{message: message, actor: actor}) do
      {%{status: :duplicate, original: decision}, s}
    else
      _ -> nil
    end
  end

  @doc false
  def member_authorize(state, context) do
    ContractLab.RoomPolicy.authorize(
      state.contract,
      state.bindings,
      Keyword.get(context, :actor),
      Keyword.get(context, :room_policy),
      ContractLab.RoomPolicy.mode(state)
    )
  end

  # Unchecked runs are visible on every decision, so they never count as policy evidence.
  @doc false
  def mark_unchecked({decision, %{policy_mode: :unchecked} = state}),
    do: {mark(decision), state}

  def mark_unchecked(result), do: result

  # An exact retry returns the stored original, marked like the first answer.
  defp mark(%{original: original} = decision) when is_map(original),
    do: %{decision | original: mark(original)} |> Map.put(:policy, :unchecked)

  defp mark(decision), do: Map.put(decision, :policy, :unchecked)

  @doc "Advisory rule check only; fresh host authorization still belongs to step/3."
  def preview(state, message, context) do
    case tick(state, Keyword.get(context, :now)) do
      {:error, reason, _} -> {reject(reason), state}
      {:ok, state} -> admit(state, message, Keyword.get(context, :actor))
    end
    |> mark_unchecked()
  end

  @doc "Trusted host control only; this is not an agent message endpoint."
  def signal(state, event, context),
    do: ContractLab.HostSignals.signal(state, event, context) |> mark_unchecked()

  @doc "An authenticated member stops its own current contribution, without a business reply."
  def stop_contribution(state, report, context),
    do: ContractLab.Contributions.stop(state, report, "member", context) |> mark_unchecked()

  @doc "Trusted host reports terminal failure of one exact contribution; never an agent endpoint."
  def fail_contribution(state, report, context),
    do: ContractLab.Contributions.stop(state, report, "host", context) |> mark_unchecked()

  @doc "Bounded host summary; unchecked results are not room-policy evidence."
  def summary(state) do
    {Map.take(state, [:id, :now, :state, :status, :outcome, :version]), state}
    |> mark_unchecked()
    |> elem(0)
  end

  @doc false
  def host_transition(s, target, at) do
    if target == "new" or ReplyWait.definition(s.contract, target) != nil or
         not (Map.has_key?(s.contract.states, target) or
                Map.has_key?(s.contract.terminals, target)),
       do:
         raise(
           ArgumentError,
           "host transition target must be a declared state other than new and not a reply wait"
         )

    pending = Enum.filter(s.obligations, &(&1.remaining != []))
    next = enter(s, target, at)

    if next.status == :terminal do
      next
    else
      next = %{next | obligations: Enum.uniq_by(pending ++ next.obligations, & &1.id)}

      duties = next.obligations ++ ContractLab.Collections.duties(next.contract, next.state)
      witnesses = ContractLab.Reserve.witnesses(duties)

      replies =
        Enum.map(witnesses, fn {kind, participant} ->
          Enum.find(
            next.contract.states[next.state].rules ++ next.contract.any_rules,
            &(&1.kind == kind and participant in &1.from)
          )
        end)

      phase_at = state_timeout_at(next)
      total_at = ContractLab.Totals.deadline_at(next)

      failure =
        cond do
          Enum.any?(
            next.obligations ++
                Enum.map(
                  ContractLab.Collections.duties(next.contract, next.state),
                  &Map.put(&1, :due_at, at + &1.after_ms)
                ),
            &(&1.due_at > @safe_integer or (not is_nil(next.max_at) and &1.due_at > next.max_at) or
                (not is_nil(phase_at) and &1.due_at > phase_at) or
                  (not is_nil(total_at) and &1.due_at > total_at))
          ) or
              not time_range?(
                at,
                state_durations(next.contract.states[next.state]) ++
                    Enum.map(
                      ContractLab.Collections.definitions(next.contract, next.state),
                      & &1.after_ms
                    )
              ) ->
            "insufficient_time"

          Enum.any?(replies, &is_nil/1) ->
            "unserviceable_obligation"

          map_size(next.facts) + ContractLab.Reserve.reserve(next.contract, next.state, duties) >
              next.max_messages ->
            "completion_capacity"

          true ->
            nil
        end

      if failure, do: enter(next, failure, at), else: next
    end
  end

  def tick(state, now) do
    cond do
      not valid_time?(now) ->
        {:error, :invalid_time, state}

      now < state.now ->
        {:error, :clock_regression, state}

      state.status == :terminal ->
        {:ok, %{state | now: now}}

      true ->
        due =
          (deadlines(state) ++
             ContractLab.HostSignals.deadlines(state) ++ ContractLab.Collections.deadlines(state))
          |> Enum.filter(&(&1.at <= now))
          # Equal times (0.2.0): duty, collection deadline, offline grace,
          # state timeout, host trigger, cap. IDs order only within a category.
          |> Enum.sort_by(&{&1.at, &1.priority, &1.id})

        case due do
          [] ->
            {:ok, %{state | now: now}}

          [%{reason: :collection} = first | _] ->
            state |> Map.put(:now, first.at) |> ContractLab.Collections.fire(first) |> tick(now)

          [%{reason: :host_signal} = first | _] ->
            state |> ContractLab.HostSignals.fire(first, now) |> tick(now)

          [first | _] ->
            expired = %{
              state
              | now: now,
                state: first.outcome,
                status: :terminal,
                outcome: first.outcome,
                expiry: Map.drop(first, [:priority]),
                expired_at: first.at,
                version: state.version + 1,
                history:
                  state.history ++
                    [%{type: :expiry, at: first.at, reason: first.reason, outcome: first.outcome}]
            }

            {:ok, expired |> ReplyWait.close() |> ContractLab.Collections.cancel(first.at)}
        end
    end
  end

  def snapshot(s) do
    %{
      profile: "contract-lab/snapshot/1",
      definition: s.contract.digest,
      source: s.contract.source_digest,
      state: Map.delete(s, :contract)
    }
  end

  @doc "Options: `capabilities` (the host's list, checked as in `new/2`; default none)."
  def restore(c, snapshot, now, opts \\ [])

  def restore(
        c,
        %{profile: "contract-lab/snapshot/1", definition: digest, source: source, state: s},
        now,
        opts
      ) do
    if digest == c.digest and source == c.source_digest do
      with :ok <- admissible(c, Keyword.get(opts, :capabilities, [])),
           {:ok, bound} <- ContractLab.Roster.restore(c, s) do
        case tick(Map.put(s, :contract, bound), now) do
          {:ok, state} -> {:ok, state}
          {:error, reason, _} -> {:error, reason}
        end
      end
    else
      {:error, :contract_mismatch}
    end
  end

  def restore(_, _, _, _), do: {:error, :invalid_snapshot}

  def view(state, context) do
    case tick(state, Keyword.get(context, :now)) do
      {:error, reason, _} -> {{:error, reason}, state}
      {:ok, state} -> project(state, context)
    end
  end

  defp project(state, context) do
    actor = Keyword.get(context, :actor)

    # Authorize before projecting, so a caller without evidence learns nothing.
    with :ok <- member_authorize(state, context),
         {:ok, role} <- role_result(role_for(state, actor)) do
      {:ok, definition} = ContractLab.for_role(state.contract, role)

      legal =
        if state.status == :active,
          do: Enum.filter(rules(state), &(role in &1.from)),
          else: []

      facts =
        Map.filter(state.facts, fn {_, fact} ->
          fact.actor == actor or actor in fact.recipients
        end)

      view = %{
        id: state.id,
        state: state.state,
        status: state.status,
        outcome: state.outcome,
        version: state.version,
        definition: definition,
        legal: legal,
        facts: facts,
        obligations: Enum.filter(state.obligations, &(role in &1.participants)),
        max_at: state.max_at,
        idle_at: idle_at(state),
        mentions_used: state.mentions_used
      }

      view =
        view
        |> Map.merge(ContractLab.Collections.view(state, role))
        |> Map.merge(ContractLab.Contributions.view(state, role))
        |> Map.merge(ReplyWait.view(state, role))

      view =
        if RoomPolicy.mode(state) == :unchecked,
          do: Map.put(view, :policy, :unchecked),
          else: view

      {{:ok, view}, state}
    else
      {:error, reason} -> {{:error, reason}, state}
    end
  end

  defp role_result(nil), do: {:error, :unauthorized}
  defp role_result(role), do: {:ok, role}

  def validate_payload(type, payload), do: payload_violation(type, payload) == nil

  @doc """
  The first payload violation, or nil. Missing fields come first, then unknown
  fields, then value checks, each in field-name order. The detail is built from
  the proposer's own payload and the compiled type only, and never echoes a value.
  """
  def payload_violation(type, payload) when is_map(payload) do
    names = type.fields |> Map.keys() |> Enum.sort()

    cond do
      missing = Enum.find(names, &(not Map.has_key?(payload, &1))) ->
        %{field: missing, constraint: "required"}

      unknown =
          payload |> Map.keys() |> Enum.sort() |> Enum.find(&(not Map.has_key?(type.fields, &1))) ->
        unknown_field(unknown)

      true ->
        Enum.find_value(names, &field_violation(&1, type.fields[&1], payload[&1]))
    end
  end

  def payload_violation(_, _), do: %{constraint: "type", expected: "object"}

  defp unknown_field(name) when is_binary(name) and byte_size(name) <= 128,
    do: %{field: name, constraint: "unknown"}

  defp unknown_field(_), do: %{constraint: "unknown"}

  defp admit(s, message, actor) do
    role = role_for(s, actor)

    with true <- role != nil || {:error, :unauthorized},
         true <- not ContractLab.HostSignals.removed?(s, role) || {:error, :participant_removed},
         :ok <- envelope(message),
         true <- message["session"] == s.id || {:error, :session_mismatch},
         {:ok, encoded} <- encode_message(message),
         true <- byte_size(encoded) <= s.max_bytes || {:error, :message_size} do
      fingerprint = Canonical.digest(%{message: message, actor: actor})

      case Map.get(s.receipts, message["id"]) do
        %{fingerprint: ^fingerprint, decision: decision} ->
          {%{status: :duplicate, original: decision}, s}

        nil ->
          new_message(s, message, role, actor, fingerprint)

        _ ->
          {reject(:id_conflict), s}
      end
    else
      {:error, reason} -> {reject(reason), s}
    end
  end

  defp new_message(s, m, role, actor, fingerprint) do
    with true <- s.status == :active || {:error, :terminal},
         true <-
           (not Map.has_key?(m, "expected_version") or m["expected_version"] == s.version) ||
             {:error, :stale_version},
         # 6: the candidates for (role, kind); 7a: the payload once against the kind's type;
         # 7b: the one alternative whose literals match; 7c: its references.
         [_ | _] = candidates <-
           Enum.filter(rules(s), &(role in &1.from and &1.kind == m["kind"])),
         type <- s.contract.types[m["kind"]],
         nil <- payload_violation(type, m["payload"]) |> payload_error(),
         rule when not is_nil(rule) <- ReplyWait.select(candidates, m["payload"]),
         true <- references_valid?(s, rule, m["payload"], actor) || {:error, :reference},
         true <- ReplyWait.answers_wait?(s, rule, m["payload"]) || {:error, :reference},
         true <-
           not (rule.each and role in Map.get(s.contributions, rule.id, [])) ||
             {:error, :already_contributed},
         recipients <-
           Enum.reject(rule.to, &(&1 == role)) |> Enum.map(&s.bindings[&1]) |> Enum.uniq(),
         true <-
           not Enum.any?(rule.to, &ContractLab.HostSignals.removed?(s, &1)) ||
             {:error, :participant_removed},
         :ok <- budgets(s, rule, recipients),
         :ok <- ContractLab.Renewal.check(s, rule, role),
         :ok <- ContractLab.Collections.check(s, rule, role),
         :ok <- completion_window(s, rule, role, recipients),
         true <- rule_time_range?(s, rule, role) || {:error, :invalid_time_range} do
      commit(s, m, role, actor, rule, recipients, fingerprint)
    else
      [] -> {reject(:illegal_message), s}
      nil -> {reject(:illegal_message), s}
      {:error, {:payload, detail}} -> {reject(:payload, detail), s}
      {:error, reason} -> {reject(reason), s}
    end
  end

  defp payload_error(nil), do: nil
  defp payload_error(detail), do: {:error, {:payload, detail}}

  defp commit(s, m, role, actor, rule, recipients, fingerprint) do
    contribution = Map.get(s.contributions, rule.id, []) ++ [role]
    advances = not rule.event and (not rule.each or Enum.all?(rule.from, &(&1 in contribution)))

    activity =
      case s.inactivity do
        :accepted -> true
        :transition -> advances
        :marked -> rule.activity
      end

    fact = %{
      id: m["id"],
      kind: m["kind"],
      payload: m["payload"],
      role: role,
      actor: actor,
      at: s.now,
      recipients: recipients,
      rule: rule.id
    }

    next = %{
      s
      | version: s.version + 1,
        mentions_used: s.mentions_used + length(recipients),
        last_activity: if(activity, do: s.now, else: s.last_activity),
        facts: Map.put(s.facts, m["id"], fact),
        contributions:
          if(rule.each,
            do: Map.put(s.contributions, rule.id, contribution),
            else: s.contributions
          ),
        obligations: discharge(s.obligations, role, rule.kind),
        variables: if(rule.bind, do: Map.put(s.variables, rule.bind, m["id"]), else: s.variables),
        history:
          s.history ++ [%{type: :accepted, id: m["id"], at: s.now, role: role, kind: rule.kind}]
    }

    next = ContractLab.Renewal.apply(next, rule)
    next = if advances, do: enter(next, rule.target, s.now, {rule.id, m["id"]}), else: next

    next = ContractLab.Collections.accept(next, rule, role, m["id"])
    {:ok, next} = tick(next, s.now)

    decision = %{
      status: :accepted,
      id: m["id"],
      session: s.id,
      role: role,
      actor: actor,
      kind: m["kind"],
      payload: m["payload"],
      recipients: recipients,
      version: next.version,
      state: next.state,
      outcome: next.outcome,
      definition: s.contract.digest,
      metadata: rule.meta
    }

    next = %{
      next
      | receipts: Map.put(next.receipts, m["id"], %{fingerprint: fingerprint, decision: decision})
    }

    {decision, next}
  end

  defp enter(s, target, at, opener \\ nil) do
    s = ContractLab.Totals.leave(s, at)
    visits = Map.get(s.entries, target, 0) + 1
    entries = Map.put(s.entries, target, visits)
    state = s.contract.states[target]

    target =
      cond do
        state && state.max_entries && visits > state.max_entries.limit ->
          state.max_entries.outcome

        budget = state && ContractLab.Totals.short(s, target) ->
          budget

        true ->
          target
      end

    result =
      if Map.has_key?(s.contract.terminals, target) do
        %{
          s
          | state: target,
            status: :terminal,
            outcome: target,
            entries: entries,
            entered_at: at,
            obligations: [],
            contributions: %{}
        }
      else
        duties =
          Enum.map(
            s.contract.states[target].deadlines,
            &Map.merge(&1, %{due_at: at + &1.after_ms, remaining: &1.participants})
          )

        %{
          s
          | state: target,
            entries: entries,
            entered_at: at,
            obligations: duties,
            contributions: %{}
        }
      end

    result
    |> ContractLab.Renewal.enter(at)
    |> ContractLab.Collections.enter(at)
    |> ReplyWait.enter(at, opener)
  end

  defp discharge(obligations, role, kind) do
    Enum.map(obligations, fn d ->
      if d.kind == kind and role in d.participants do
        %{d | remaining: if(d.quantifier == :any, do: [], else: List.delete(d.remaining, role))}
      else
        d
      end
    end)
  end

  defp deadlines(s) do
    max =
      if s.max_at,
        do: [
          %{
            at: s.max_at,
            priority: 5,
            id: "maximum",
            outcome: Map.get(s.contract, :timeout, %{outcome: "expired"}).outcome,
            reason: :lifetime
          }
        ],
        else: []

    duties =
      for d <- s.obligations,
          d.remaining != [],
          do: %{
            at: d.due_at,
            priority: 0,
            id: d.id,
            outcome: d.outcome,
            reason: :deadline,
            missing: d.remaining
          }

    phase =
      case Map.get(s.contract.states[s.state], :timeout) do
        nil ->
          []

        timeout ->
          [
            %{
              at: ContractLab.Renewal.phase_at(s),
              priority: 3,
              id: "state-" <> s.state,
              outcome: timeout.outcome,
              reason: :state_timeout
            }
          ]
      end

    max ++ duties ++ ReplyWait.deadlines(s) ++ phase ++ ContractLab.Totals.deadlines(s)
  end

  # A sender may reference only a fact it authored or received, the facts its view shows.
  # The receipt carries actor and recipients, so a redacted agent checkpoint can check it too.
  defp references_valid?(s, rule, payload, actor) do
    Enum.all?(rule.matches, fn {field, binding} ->
      id = payload[field]

      case s.facts[id] do
        nil ->
          false

        fact ->
          Map.get(s.variables, binding) == id and
            fact.kind in s.contract.types[rule.kind].fields[field].values and
            seen?(s.receipts[id].decision, actor)
      end
    end)
  end

  defp seen?(%{actor: author, recipients: recipients}, actor),
    do: author == actor or actor in recipients

  defp budgets(s, rule, recipients) do
    cond do
      not ContractLab.Mentions.within?(s.contract, rule, length(recipients)) ->
        {:error, :mention_limit}

      map_size(s.facts) >= s.max_messages ->
        {:error, :message_budget}

      true ->
        :ok
    end
  end

  defp completion_window(s, rule, role, _recipients) do
    contributed = [role | Map.get(s.contributions, rule.id, [])]
    advances = not rule.event and (not rule.each or Enum.all?(rule.from, &(&1 in contributed)))
    collection_target = ContractLab.Collections.target(s, rule, role)
    advances = advances or not is_nil(collection_target)
    target_name = if advances, do: collection_target || rule.target, else: s.state
    target = s.contract.states[target_name]

    duties =
      cond do
        is_nil(target) -> []
        advances -> Enum.map(target.deadlines, &Map.put(&1, :remaining, &1.participants))
        true -> discharge(s.obligations, role, rule.kind)
      end

    duties =
      duties ++
        if is_nil(target),
          do: [],
          else:
            ContractLab.Collections.duties(
              s.contract,
              target_name,
              if(advances, do: nil, else: Map.get(s, :collections))
            )

    # The wait a head opens, or the one still open while W stays: 1 + max R (§6).
    duties = duties ++ ReplyWait.duties(s, rule, target_name, advances)

    duties =
      if Map.has_key?(rule, :collection) and not advances,
        do:
          Enum.map(duties, fn d ->
            if d.kind == s.contract.collections[rule.collection].reply,
              do: %{d | remaining: List.delete(d.remaining, role)},
              else: d
          end),
        else: duties

    # Each owed reply keeps itself and what its own admission will reserve (DF-56).
    reserve =
      if is_nil(target), do: 0, else: ContractLab.Reserve.reserve(s.contract, target_name, duties)

    # A collection reply answers a reserved duty; only other events must leave the closing path.
    closing =
      if rule.event and not Map.has_key?(rule, :collection),
        do: ContractLab.Closing.reserve(s, duties),
        else: 0

    cond do
      (advances and s.max_at) && Enum.any?(duties, &(s.now + &1.after_ms > s.max_at)) ->
        {:error, :insufficient_time}

      map_size(s.facts) + 1 + reserve + closing > s.max_messages ->
        {:error, :completion_capacity}

      true ->
        :ok
    end
  end

  defp envelope(m) when is_map(m) do
    if Enum.sort(Map.keys(m)) in [
         Enum.sort(~w(id session kind payload)),
         Enum.sort(~w(id session kind payload expected_version))
       ] and
         valid_id?(m["id"]) and valid_id?(m["session"]) and is_binary(m["kind"]) and
         is_map(m["payload"]) and
         (not Map.has_key?(m, "expected_version") or
            (is_integer(m["expected_version"]) and m["expected_version"] >= 0)) do
      :ok
    else
      {:error, :envelope}
    end
  end

  defp envelope(_), do: {:error, :envelope}

  defp encode_message(m) do
    {:ok, Canonical.encode(m)}
  rescue
    FunctionClauseError ->
      {:error, :payload}

    ArgumentError ->
      {:error, :payload}

    Protocol.UndefinedError ->
      {:error, :payload}

    e in ErlangError ->
      case e.original do
        {:invalid_byte, _} -> {:error, :payload}
        :unexpected_end -> {:error, :payload}
        _ -> reraise e, __STACKTRACE__
      end
  end

  defp field_violation(name, %{kind: :text, encoding: "utf-8", bytes: b, graphemes: g} = t, v) do
    cond do
      not is_binary(v) ->
        %{field: name, constraint: "type", expected: "text"}

      not String.valid?(v) ->
        %{field: name, constraint: "utf8"}

      byte_size(v) not in b.min..b.max ->
        %{field: name, constraint: "bytes", min: b.min, max: b.max}

      g != nil and HeaderSpikes.Graphemes.count(v) not in g.min..g.max ->
        %{field: name, constraint: "graphemes", min: g.min, max: g.max}

      Map.has_key?(t, :regex) and
          not match?({:ok, true, _}, ContractLab.SafePattern.match(t.regex, v)) ->
        %{field: name, constraint: "regex"}

      true ->
        nil
    end
  end

  defp field_violation(name, %{kind: :integer, min: min, max: max}, v) do
    cond do
      not is_integer(v) -> %{field: name, constraint: "type", expected: "integer"}
      v not in min..max -> %{field: name, constraint: "range", min: min, max: max}
      true -> nil
    end
  end

  defp field_violation(name, %{kind: :boolean}, v),
    do: if(is_boolean(v), do: nil, else: %{field: name, constraint: "type", expected: "boolean"})

  defp field_violation(name, %{kind: :enum, values: values}, v),
    do: if(is_binary(v) and v in values, do: nil, else: %{field: name, constraint: "enum"})

  defp field_violation(name, %{kind: :ref}, v),
    do: if(valid_id?(v), do: nil, else: %{field: name, constraint: "ref"})

  defp field_violation(name, _, _), do: %{field: name, constraint: "type"}
  defp rules(s), do: s.contract.states[s.state].rules ++ s.contract.any_rules

  defp role_for(s, actor),
    do: Enum.find_value(s.bindings, fn {role, principal} -> if principal == actor, do: role end)

  defp reject(reason), do: %{status: :rejected, reason: reason}
  defp reject(reason, detail), do: %{status: :rejected, reason: reason, detail: detail}
  defp idle_at(_), do: nil
  defp duration_ms(:infinite), do: nil
  defp duration_ms({:seconds, seconds}), do: seconds * 1000
  defp deadline_at(_, :infinite), do: nil
  defp deadline_at(at, duration), do: at + duration_ms(duration)
  @doc false
  def positive?(v), do: is_integer(v) and v > 0 and v <= @safe_integer
  @doc false
  def valid_time?(v), do: is_integer(v) and v >= 0 and v <= @safe_integer
  defp valid_id?(v), do: is_binary(v) and String.valid?(v) and byte_size(v) in 1..256

  defp rule_time_range?(s, rule, role) do
    target_name = ContractLab.Collections.target(s, rule, role) || rule.target
    target = s.contract.states[target_name]

    durations =
      if target,
        do:
          state_durations(target) ++
            Enum.map(ContractLab.Collections.definitions(s.contract, target_name), & &1.after_ms) ++
            Enum.map(ReplyWait.entry_duties(s.contract, target_name), & &1.after_ms),
        else: []

    time_range?(s.now, [s.idle_ms | durations])
  end

  defp state_durations(state) do
    Enum.map(state.deadlines, & &1.after_ms) ++
      case Map.get(state, :timeout) do
        nil -> []
        timeout -> [timeout.after_ms]
      end
  end

  defp state_timeout_at(s), do: ContractLab.Renewal.phase_at(s)

  defp time_range?(at, durations),
    do:
      Enum.all?(
        durations,
        &(is_nil(&1) or (is_integer(&1) and &1 > 0 and at + &1 <= @safe_integer))
      )
end
