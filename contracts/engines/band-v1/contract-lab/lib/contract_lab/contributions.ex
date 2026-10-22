defmodule ContractLab.Contributions do
  @moduledoc "Versioned room controls for terminal contribution outcomes, independent of business message types."
  alias ContractLab.{Canonical, Collections, Engine}
  @safe 9_007_199_254_740_991
  @redacted String.duplicate("0", 64)
  # Checker projections name hidden reports "redacted:<digest>", so no real report may.
  @reserved "redacted:"

  def enabled?(c), do: Map.get(c, :contribution_profile) == "band-contract/contribution/1"
  def initial(c), do: if(enabled?(c), do: %{contribution_receipts: %{}}, else: %{})

  def stop(s, report, origin, context) do
    with true <- enabled?(s.contract) || {:error, :unsupported_extension},
         :ok <- shape(report),
         :ok <- unreserved(report),
         :ok <- authorize(s, report, origin, context) do
      case Engine.tick(s, Keyword.get(context, :now)) do
        {:error, reason, _} -> {reject(reason), s}
        {:ok, next} -> next |> admit(report, origin) |> Engine.mark_unchecked()
      end
    else
      {:error, reason} -> {reject(reason), s}
    end
  end

  defp authorize(_s, _r, "host", context) do
    if Keyword.get(context, :authority) == :host,
      do: :ok,
      else: {:error, :host_authority_required}
  end

  defp authorize(s, r, "member", context) do
    actor = Keyword.get(context, :actor)

    with :ok <- Engine.member_authorize(s, context) do
      if s.bindings[r["member"]] == actor,
        do: :ok,
        else: {:error, :unauthorized_contribution}
    end
  end

  defp admit(s, r, origin) do
    fingerprint = fingerprint(r, origin)

    case s.contribution_receipts[r["id"]] do
      %{fingerprint: ^fingerprint, decision: decision} ->
        {Map.put(decision, :status, :duplicate_contribution), s}

      nil ->
        case scope(s, r) do
          :ok -> accept(s, r, origin, fingerprint)
          {:error, reason} -> {reject(reason), s}
        end

      _ ->
        {reject(:id_conflict), s}
    end
  end

  defp scope(s, r) do
    record = Map.get(s, :collections, %{})[r["collection"]]

    cond do
      s.status == :terminal ->
        {:error, :terminal}

      r["session"] != s.id or is_nil(record) ->
        {:error, :contribution_scope}

      record.entry != r["entry"] or r["member"] not in record.from ->
        {:error, :contribution_scope}

      record.members[r["member"]].status != "pending" ->
        {:error, :already_contributed}

      map_size(s.contribution_receipts) >= s.max_messages ->
        {:error, :contribution_capacity}

      true ->
        :ok
    end
  end

  defp accept(s, r, origin, fingerprint) do
    failure = %{
      id: r["id"],
      origin: origin,
      type: r["cause"]["type"],
      reference: r["cause"]["reference"],
      message: r["cause"]["message"]
    }

    s = Collections.stop(s, r["collection"], r["member"], failure)
    {:ok, s} = Engine.tick(s, s.now)

    decision = %{
      status: :contribution_stopped,
      id: r["id"],
      session: s.id,
      collection: r["collection"],
      entry: r["entry"],
      member: r["member"],
      origin: origin,
      at: s.now,
      version: s.version,
      state: s.state,
      outcome: s.outcome
    }

    receipt = %{fingerprint: fingerprint, decision: decision}
    {decision, put_in(s, [:contribution_receipts, r["id"]], receipt)}
  end

  defp fingerprint(report, origin), do: Canonical.digest(%{report: report, origin: origin})

  defp shape(r) when is_map(r) do
    if Enum.sort(Map.keys(r)) == ~w(cause collection entry id member session) and
         Enum.all?(~w(collection id member session), &text?(r[&1], 256)) and
         is_integer(r["entry"]) and r["entry"] > 0 and r["entry"] <= @safe and
         cause?(r["cause"]),
       do: :ok,
       else: {:error, :invalid_contribution}
  end

  defp shape(_), do: {:error, :invalid_contribution}

  # Admission only: restore must still accept the placeholders a projection carries.
  defp unreserved(r) do
    if String.starts_with?(r["id"], @reserved), do: {:error, :invalid_contribution}, else: :ok
  end

  defp cause?(cause) when is_map(cause),
    do:
      Enum.sort(Map.keys(cause)) == ~w(message reference type) and
        text?(cause["type"], 128) and text?(cause["message"], 2048) and
        (is_nil(cause["reference"]) or text?(cause["reference"], 256))

  defp cause?(_), do: false
  defp text?(v, max), do: is_binary(v) and byte_size(v) in 1..max and String.valid?(v)
  defp reject(reason), do: %{status: :rejected, reason: reason}

  def view(s, role) do
    if enabled?(s.contract) do
      controls =
        for {name, record} <- Map.get(s, :collections, %{}),
            get_in(record.members, [role, :status]) == "pending" do
          %{
            operation: "stop_contribution",
            session: s.id,
            collection: name,
            entry: record.entry,
            member: role
          }
        end

      %{contribution_controls: controls}
    else
      %{}
    end
  end

  @doc "Remove private failure details from another contributor's optional checker checkpoint."
  def redact(s, actor) do
    if enabled?(s.contract) and Collections.enabled?(s.contract) do
      role = Enum.find_value(s.bindings, fn {role, bound} -> if bound == actor, do: role end)
      records = records(s)

      # Reports this actor may not see: another member's stop in a collection it does not
      # receive. Their IDs are private too, so every copy becomes an opaque placeholder
      # derived only from public fields.
      hidden =
        for record <- records,
            {member, %{failure: %{id: id}}} <- record.members,
            member != role and role not in record.to,
            into: %{},
            do: {id, placeholder(record, member)}

      redact = fn record ->
        members =
          Map.new(record.members, fn {member, row} ->
            case row.failure && hidden[row.failure.id] do
              nil ->
                {member, row}

              opaque ->
                failure = %{
                  row.failure
                  | id: opaque,
                    type: "redacted",
                    reference: nil,
                    message: "redacted"
                }

                {member, %{row | failure: failure}}
            end
          end)

        %{record | members: members}
      end

      receipts =
        Map.new(s.contribution_receipts, fn {id, receipt} ->
          case hidden[id] do
            nil ->
              {id, receipt}

            opaque ->
              {opaque,
               %{receipt | fingerprint: @redacted, decision: %{receipt.decision | id: opaque}}}
          end
        end)

      log =
        Enum.map(s.collection_log, fn row ->
          case hidden[row["control"]] do
            nil -> row
            opaque -> Map.put(row, "control", opaque)
          end
        end)

      %{
        s
        | collections: Map.new(s.collections, fn {name, record} -> {name, redact.(record)} end),
          collection_results: Enum.map(s.collection_results, redact),
          contribution_receipts: receipts,
          collection_log: log
      }
    else
      s
    end
  end

  defp placeholder(record, member),
    do: @reserved <> binary_part(Canonical.digest([record.name, record.entry, member]), 0, 32)

  def valid_state?(c, s, redacted) do
    if enabled?(c) do
      records = records(s)

      failed =
        for record <- records,
            {member, %{status: "failed"} = row} <- record.members,
            do: {record, member, row}

      map_size(s.contribution_receipts) <= s.max_messages and
        length(failed) == map_size(s.contribution_receipts) and
        length(Enum.uniq_by(failed, fn {_, _, row} -> row.failure.id end)) == length(failed) and
        Enum.all?(records, fn record ->
          record.entry <= s.entries[record.state] and
            Enum.all?(record.members, fn {_, row} ->
              row.status == "failed" == not is_nil(row.failure)
            end)
        end) and
        Enum.all?(failed, fn {record, member, row} ->
          valid_failure?(s, record, member, row, redacted)
        end)
    else
      true
    end
  end

  defp valid_failure?(s, record, member, row, redacted) do
    f = row.failure
    receipt = s.contribution_receipts[f.id]

    report = %{
      "id" => f.id,
      "session" => s.id,
      "collection" => record.name,
      "entry" => record.entry,
      "member" => member,
      "cause" => %{"type" => f.type, "reference" => f.reference, "message" => f.message}
    }

    shape(report) == :ok and is_nil(row.message) and receipt != nil and
      receipt.decision.id == f.id and receipt.decision.session == s.id and
      receipt.decision.collection == record.name and receipt.decision.entry == record.entry and
      receipt.decision.member == member and receipt.decision.origin == f.origin and
      receipt.decision.at == row.at and receipt.decision.version <= s.version and
      (redacted or receipt.fingerprint == fingerprint(report, f.origin)) and
      Enum.count(
        s.collection_log,
        &(&1["control"] == f.id and &1["name"] == record.name and
            &1["role"] == member and &1["at"] == row.at and &1["reason"] == "failed")
      ) == 1
  end

  defp records(s),
    do: Map.values(Map.get(s, :collections, %{})) ++ Map.get(s, :collection_results, [])
end
