defmodule ContractLab.StateCodec do
  @moduledoc "Portable, strictly typed state. Only fixed protocol keys and enum values become atoms.\n\n`restore/4` does not apply the hosted-profile gate; hosted adapters restore through\n`Checkpoint.restore/4`, which refuses checkpoints created in the unchecked mode.\n"
  alias ContractLab.{Canonical, Engine}
  @safe 9_007_199_254_740_991
  def export(state) do
    state |> Engine.snapshot() |> Map.put(:profile, profile(state.contract)) |> normalize()
  end

  # The reply wait (contract.replies/1) adds the saved `wait` key on top of any other profile.
  defp profile(%{card: %{extensions: extensions}} = c)
       when is_list(extensions) and extensions != [] do
    if ContractLab.ReplyWait.enabled?(c),
      do: "band-contract/state/6",
      else: profile(Map.delete(c, :card))
  end

  defp profile(%{contribution_profile: "band-contract/contribution/1"}) do
    "band-contract/state/5"
  end

  defp profile(%{participant_types: _}) do
    "band-contract/state/4"
  end

  defp profile(%{collections: _}) do
    "band-contract/state/3"
  end

  defp profile(%{role_sets: _}) do
    "band-contract/state/3"
  end

  defp profile(%{renewal_profile: "band-contract/renewal/1"}) do
    "band-contract/state/3"
  end

  defp profile(_) do
    "band-contract/state/2"
  end

  # The contract's own refusals come first, as at session start (Sofia O1).
  def restore(contract, data, now, opts \\ []) do
    case Engine.admissible(contract, Keyword.get(opts, :capabilities, [])) do
      :ok -> decode_restore(contract, data, now, opts)
      error -> error
    end
  end

  defp decode_restore(contract, data, now, opts) do
    try do
      require_keys(data, ~w(profile definition source state))
      ensure(data["profile"] == profile(contract))

      if data["definition"] != contract.digest or data["source"] != contract.source_digest do
        {:error, :contract_mismatch}
      else
        redacted = Keyword.get(opts, :redacted, false)
        {mode, fields} = policy_mode(data["state"])

        state =
          decode(
            fields,
            state_schema(redacted) ++
              host_schema(contract) ++
              clock_schema(contract) ++
              roster_schema(contract) ++
              collection_schema(contract) ++
              contribution_schema(contract) ++
              total_schema(contract) ++ ContractLab.ReplyWait.schema(contract)
          )
          |> Map.merge(mode)

        {:ok, bound} = ContractLab.Roster.restore(contract, state)
        validate(bound, state, redacted)

        Engine.restore(
          contract,
          %{
            profile: "contract-lab/snapshot/1",
            definition: contract.digest,
            source: contract.source_digest,
            state: state
          },
          now,
          capabilities: Keyword.get(opts, :capabilities, [])
        )
      end
    catch
      :invalid_checkpoint -> {:error, :invalid_checkpoint}
    end
  rescue
    _ -> {:error, :invalid_checkpoint}
  end

  defp policy_mode(%{"policy_mode" => "unchecked"} = fields) do
    {%{policy_mode: :unchecked}, Map.delete(fields, "policy_mode")}
  end

  defp policy_mode(%{"policy_mode" => _}) do
    throw(:invalid_checkpoint)
  end

  defp policy_mode(fields) do
    {%{}, fields}
  end

  defp state_schema(redacted) do
    [
      id: :id,
      bindings: {:dict, :id},
      now: :uint,
      created_at: :uint,
      entered_at: :uint,
      last_activity: :uint,
      max_at: {:nullable, :uint},
      idle_ms: {:nullable, :positive},
      inactivity: {:enum, [:accepted, :transition, :marked]},
      max_messages: :positive,
      max_bytes: :positive,
      mentions_used: :uint,
      state: :id,
      status: {:enum, [:active, :terminal]},
      version: :uint,
      outcome: {:nullable, :id},
      expiry: {:nullable, :expiry},
      expired_at: {:nullable, :uint},
      entries: {:dict, :positive},
      obligations: {:list, obligation_schema()},
      contributions: {:dict, {:list, :id}},
      variables: {:dict, :id},
      facts: {:dict, fact_schema(redacted)},
      receipts: {:dict, fingerprint: :hash, decision: decision_schema(redacted)},
      history: {:list, :history}
    ]
  end

  defp metadata_schema do
    [doc: {:nullable, :string}, line: :positive]
  end

  defp host_schema(_) do
    [
      signals: {:dict, :payload},
      signal_revision: :uint,
      signal_receipts: {:dict, :payload},
      fired_signals: {:dict, :boolean},
      control_log: {:list, :payload},
      control_limit: :uint,
      watch_bindings: {:dict, :id}
    ]
  end

  defp collection_schema(%{collections: _} = c) do
    member = [
      status: {:enum, ["pending", "ok", "failed", "offline", "timed_out", "cancelled"]},
      message: {:nullable, :id},
      at: {:nullable, :uint}
    ]

    member =
      if ContractLab.Contributions.enabled?(c) do
        member ++ [failure: {:nullable, failure_schema()}]
      else
        member
      end

    record = [
      name: :id,
      state: :id,
      entry: :positive,
      started_at: :uint,
      deadline_at: :uint,
      closed_at: {:nullable, :uint},
      reason: {:nullable, :string},
      from: {:list, :id},
      to: {:list, :id},
      members: {:dict, member}
    ]

    [
      collections: {:dict, record},
      collection_results: {:list, record},
      collection_log: {:list, :payload}
    ]
  end

  defp collection_schema(_) do
    []
  end

  defp failure_schema do
    [
      id: :id,
      origin: {:enum, ["member", "host"]},
      type: :string,
      reference: {:nullable, :id},
      message: :string
    ]
  end

  defp contribution_schema(%{contribution_profile: "band-contract/contribution/1"}) do
    decision = [
      status: {:enum, [:contribution_stopped]},
      id: :id,
      session: :id,
      collection: :id,
      entry: :positive,
      member: :id,
      origin: {:enum, ["member", "host"]},
      at: :uint,
      version: :positive,
      state: :id,
      outcome: {:nullable, :id}
    ]

    [contribution_receipts: {:dict, fingerprint: :hash, decision: decision}]
  end

  defp contribution_schema(_) do
    []
  end

  defp roster_schema(%{role_sets: _}) do
    [roster_config: :roster]
  end

  defp roster_schema(_) do
    []
  end

  # DF-21: per-state dwell, only when some state declares `total`.
  defp total_schema(c) do
    if ContractLab.Totals.enabled?(c), do: [dwell: {:dict, :uint}], else: []
  end

  defp clock_schema(%{renewal_profile: "band-contract/renewal/1"}) do
    [clocks: [phase_at: {:nullable, :uint}, hard_at: {:nullable, :uint}]]
  end

  defp clock_schema(_) do
    []
  end

  defp obligation_schema do
    [
      id: :id,
      quantifier: {:enum, [:all, :any]},
      participants: {:list, :id},
      kind: :id,
      after_ms: :positive,
      outcome: :id,
      meta: metadata_schema(),
      due_at: :uint,
      remaining: {:list, :id}
    ]
  end

  defp fact_schema(true) do
    [kind: :id]
  end

  defp fact_schema(false) do
    [
      id: :id,
      kind: :id,
      payload: :payload,
      role: :id,
      actor: :id,
      at: :uint,
      recipients: {:list, :id},
      rule: :id
    ]
  end

  defp decision_schema(redacted) do
    base = [
      status: {:enum, [:accepted]},
      id: :id,
      session: :id,
      role: :id,
      actor: :id,
      kind: :id,
      recipients: {:list, :id},
      version: :positive,
      state: :id,
      outcome: {:nullable, :id},
      definition: :hash,
      metadata: metadata_schema()
    ]

    if redacted do
      base
    else
      base ++ [payload: :payload]
    end
  end

  defp decode(value, schema) when is_list(schema) do
    require_keys(value, Enum.map(schema, fn {key, _} -> Atom.to_string(key) end))
    Map.new(schema, fn {key, type} -> {key, decode(value[Atom.to_string(key)], type)} end)
  end

  defp decode(nil, {:nullable, _}) do
    nil
  end

  defp decode(value, {:nullable, type}) do
    decode(value, type)
  end

  defp decode(value, {:enum, allowed}) do
    case Enum.find(allowed, &(to_string(&1) == value)) do
      nil -> throw(:invalid_checkpoint)
      enum -> enum
    end
  end

  defp decode(value, {:dict, type}) do
    ensure(is_map(value))
    Map.new(value, fn {key, item} -> {decode(key, :id), decode(item, type)} end)
  end

  defp decode(value, {:list, type}) do
    ensure(is_list(value))
    Enum.map(value, &decode(&1, type))
  end

  defp decode(value, :uint) do
    ensure(is_integer(value) and value >= 0 and value <= @safe)
    value
  end

  defp decode(value, :positive) do
    ensure(decode(value, :uint) > 0)
    value
  end

  defp decode(value, :boolean) do
    ensure(is_boolean(value))
    value
  end

  defp decode(value, :string) do
    ensure(is_binary(value) and String.valid?(value))
    value
  end

  defp decode(value, :id) do
    ensure(byte_size(decode(value, :string)) in 1..256)
    value
  end

  defp decode(value, :hash) do
    ensure(is_binary(value) and Regex.match?(~r/\A[0-9a-f]{64}\z/, value))
    value
  end

  defp decode(value, :roster) when is_map(value) do
    value
  end

  defp decode(value, :payload) do
    ensure(is_map(value))

    Enum.each(value, fn {key, item} ->
      decode(key, :id)
      scalar(item)
    end)

    value
  end

  defp decode(value, :expiry) do
    base = [
      at: :uint,
      id: :id,
      outcome: :id,
      reason: {:enum, [:lifetime, :deadline, :state_timeout, :state_total]}
    ]

    schema =
      if value["reason"] == "deadline" do
        base ++ [missing: {:list, :id}]
      else
        base
      end

    decode(value, schema)
  end

  defp decode(value, :history) do
    case value["type"] do
      "accepted" ->
        decode(value, type: {:enum, [:accepted]}, id: :id, at: :uint, role: :id, kind: :id)

      "expiry" ->
        decode(value,
          type: {:enum, [:expiry]},
          at: :uint,
          reason: {:enum, [:lifetime, :deadline, :state_timeout, :state_total]},
          outcome: :id
        )

      _ ->
        throw(:invalid_checkpoint)
    end
  end

  defp scalar(value) when is_binary(value) do
    decode(value, :string)
  end

  defp scalar(value) when is_boolean(value) do
    value
  end

  defp scalar(value) when is_integer(value) and abs(value) <= @safe do
    value
  end

  defp scalar(_) do
    throw(:invalid_checkpoint)
  end

  defp validate(c, s, redacted) do
    ensure(c.engine == "0.2.0-lab.1" and is_nil(s.idle_ms))
    ensure(Engine.valid_bindings?(c, s.bindings))

    ensure(
      s.created_at <= s.entered_at and s.entered_at <= s.now and s.created_at <= s.last_activity and
        s.last_activity <= s.now
    )

    ensure(
      map_size(s.facts) <= s.max_messages and
        Enum.sort(Map.keys(s.facts)) == Enum.sort(Map.keys(s.receipts))
    )

    ensure(
      s.version ==
        map_size(s.facts) + length(Map.get(s, :control_log, [])) +
          length(Map.get(s, :collection_log, [])) +
          if s.expiry do
            1
          else
            0
          end
    )

    ensure(ContractLab.HostSignals.valid_state?(c, s))

    ensure(
      (s.status == :active and Map.has_key?(c.states, s.state) and is_nil(s.outcome)) or
        (s.status == :terminal and Map.has_key?(c.terminals, s.state) and s.outcome == s.state)
    )

    ensure(is_nil(s.expiry) == is_nil(s.expired_at))

    if s.expiry do
      ensure(s.status == :terminal and s.expiry.at == s.expired_at and s.expired_at <= s.now)
    end

    if s.expiry && s.expiry.reason == :state_timeout do
      ensure(c.engine == "0.2.0-lab.1" and String.starts_with?(s.expiry.id, "state-"))
      name = String.replace_prefix(s.expiry.id, "state-", "")
      timeout = c.states[name].timeout
      ensure(timeout.profile == "band-contract/timeout/1")

      ensure(
        s.expiry.at ==
          if Map.has_key?(s, :clocks) do
            s.clocks.phase_at
          else
            s.entered_at + timeout.after_ms
          end and s.outcome == timeout.outcome
      )
    end

    if s.expiry && s.expiry.reason == :state_total do
      ensure(String.starts_with?(s.expiry.id, "total-"))
      name = String.replace_prefix(s.expiry.id, "total-", "")
      total = get_in(c, [:states, name, :total])
      ensure(not is_nil(total) and s.outcome == total.outcome)
    end

    if Map.has_key?(s, :dwell) do
      ensure(
        Enum.all?(s.dwell, fn {name, ms} ->
          total = get_in(c, [:states, name, :total])
          not is_nil(total) and ms < total.after_ms
        end)
      )
    end

    if Map.has_key?(s, :clocks) do
      maximum = get_in(c, [:timeout, :maximum_ms])

      ensure(
        s.clocks.hard_at ==
          if maximum do
            s.created_at + maximum
          else
            nil
          end
      )

      ensure(is_nil(s.clocks.hard_at) or (is_integer(s.max_at) and s.max_at <= s.clocks.hard_at))

      if s.status == :active do
        phase = get_in(c, [:states, s.state, :timeout, :after_ms])

        if phase do
          ensure(s.clocks.phase_at >= s.entered_at + phase and s.clocks.phase_at <= s.now + phase)
        else
          ensure(is_nil(s.clocks.phase_at))
        end
      end
    end

    if Map.has_key?(c, :collections) do
      ensure(ContractLab.Collections.capped(s.collection_results) <= s.max_messages)

      ensure(
        Enum.all?(
          s.collection_results,
          &(&1.reason != "cancelled" or ContractLab.Collections.evidence?(&1))
        )
      )

      messages =
        for record <- Map.values(s.collections) ++ s.collection_results,
            {_, member} <- record.members,
            member.message do
          member.message
        end

      ensure(length(messages) == length(Enum.uniq(messages)))

      for record <- Map.values(s.collections) ++ s.collection_results do
        d = c.collections[record.name]

        ensure(
          not is_nil(d) and record.state == d.state and record.from == d.from and
            record.to == d.to
        )

        ensure(Enum.sort(Map.keys(record.members)) == Enum.sort(d.from))

        ensure(
          record.started_at >= s.created_at and record.started_at <= s.now and
            record.deadline_at >= record.started_at + d.after_ms and
            record.deadline_at <= s.now + d.after_ms
        )

        for {role, member} <- record.members do
          ensure(is_nil(member.at) or (member.at >= record.started_at and member.at <= s.now))

          if member.message do
            ensure(
              (member.status == "ok" or
                 (member.status == "failed" and not ContractLab.Contributions.enabled?(c))) and
                s.receipts[member.message].decision.role == role
            )
          else
            ensure(
              member.status != "ok" and
                (member.status != "failed" or ContractLab.Contributions.enabled?(c))
            )
          end
        end
      end
    end

    ensure(ContractLab.Contributions.valid_state?(c, s, redacted))
    ensure(ContractLab.ReplyWait.valid_state?(c, s))

    if redacted do
      ensure(s.history == [])
    end

    Enum.each(s.facts, fn {id, fact} ->
      ensure(Map.has_key?(c.types, fact.kind))
      receipt = s.receipts[id].decision

      ensure(
        receipt.id == id and receipt.session == s.id and receipt.definition == c.digest and
          receipt.kind == fact.kind and receipt.actor == s.bindings[receipt.role] and
          receipt.version <= s.version
      )

      ensure(Enum.all?(receipt.recipients, &(&1 in Map.values(s.bindings))))

      unless redacted do
        ensure(
          fact.id == id and fact.actor == s.bindings[fact.role] and fact.at <= s.now and
            receipt.payload == fact.payload and
            Engine.validate_payload(c.types[fact.kind], fact.payload)
        )
      end
    end)

    Enum.each(s.variables, fn {_, id} -> ensure(Map.has_key?(s.facts, id)) end)

    Enum.each(s.obligations, fn duty ->
      definition =
        c.states
        |> Map.values()
        |> Enum.flat_map(& &1.deadlines)
        |> Enum.find(&(&1.id == duty.id))

      ensure(definition == Map.drop(duty, [:due_at, :remaining]))

      ensure(
        duty.due_at - duty.after_ms >= s.created_at and
          duty.due_at - duty.after_ms <=
            if Map.has_key?(s, :clocks) do
              s.now
            else
              s.entered_at
            end and Enum.all?(duty.remaining, &(&1 in duty.participants)) and
          Enum.uniq(duty.remaining) == duty.remaining
      )
    end)

    :ok
  end

  defp require_keys(value, keys) do
    ensure(is_map(value) and Enum.sort(Map.keys(value)) == Enum.sort(keys))
  end

  defp ensure(true) do
    :ok
  end

  defp ensure(_) do
    throw(:invalid_checkpoint)
  end

  defp normalize(value) do
    value |> Canonical.encode() |> JSON.decode!()
  end
end
