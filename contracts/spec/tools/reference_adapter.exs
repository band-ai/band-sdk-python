defmodule BandSpec.ReferenceAdapter do
  alias ContractLab.{
    AgentGate,
    Canonical,
    Checkpoint,
    Engine,
    HostInputs,
    RoomEvents,
    StateCodec,
    Wire
  }

  # The union of the config schemas' properties (each additionalProperties: false). An unknown
  # key, such as a misspelled cap, is refused rather than replaced by its default (impl L18).
  @config_keys ~w(session bindings now inactivity max_messages max_bytes capabilities max_controls initial_presence watch_bindings room_policy)
  # The same for room_config: a misspelled per-actor share is refused, not dropped (N-6).
  @room_config_keys ~w(bindings now window_ms max_events max_mentions max_receipts max_events_per_actor max_mentions_per_actor max_receipts_per_actor)

  def run do
    Enum.reduce(IO.stream(:stdio, :line), %{contracts: %{}, sessions: %{}, rooms: %{}}, fn line,
                                                                                           context ->
      {response, context} =
        try do
          if byte_size(line) > 2_097_152, do: raise("conformance request too large")
          dispatch(JSON.decode!(line), context)
        rescue
          _ -> {%{error: "adapter_input"}, context}
        end

      IO.puts(Canonical.encode(response))
      context
    end)
  end

  defp dispatch(%{"op" => "compile", "handle" => handle, "source" => source}, context) do
    case compile(source) do
      {:ok, contract} -> {%{ok: contract}, put_in(context, [:contracts, handle], contract)}
      {:error, error} -> {%{error: error}, context}
    end
  end

  defp dispatch(
         %{"op" => "query", "contract" => handle, "selector" => selector} = request,
         context
       ) do
    contract = Map.fetch!(context.contracts, handle)

    result =
      case selector do
        "contract" -> ContractLab.query(contract)
        "role" -> ContractLab.for_role(contract, request["name"])
        "type" -> ContractLab.query(contract, {:type, request["name"]})
        "state" -> ContractLab.query(contract, {:state, request["name"]})
        "outcome" -> ContractLab.query(contract, {:outcome, request["name"]})
        "room" -> ContractLab.query(contract, :room)
        "any_state" -> ContractLab.query(contract, :any_state)
        _ -> {:error, :not_found}
      end

    {tag(result), context}
  end

  defp dispatch(
         %{"op" => "new", "contract" => handle, "handle" => id, "config" => config},
         context
       ) do
    contract = Map.fetch!(context.contracts, handle)

    # This adapter is the local-reference profile: the only place the explicit
    # {"mode":"unchecked"} room_policy is honored, and only at session start.
    with [] <- Map.keys(config) -- @config_keys,
         {:ok, state} <-
           Engine.new(contract, HostInputs.new_opts(contract, config, :local_reference)) do
      {%{ok: StateCodec.export(state)}, put_in(context, [:sessions, id], state)}
    else
      [_ | _] -> {%{error: :invalid_config}, context}
      {:error, error} -> {%{error: error}, context}
    end
  end

  defp dispatch(
         %{"op" => "step", "handle" => id, "actor" => _actor, "now" => _now, "message" => message} =
           request,
         context
       ) do
    {decision, state} =
      Engine.step(Map.fetch!(context.sessions, id), message, HostInputs.context(request))

    {%{decision: decision, state: StateCodec.export(state)},
     put_in(context, [:sessions, id], state)}
  end

  defp dispatch(%{"op" => "host_signal", "handle" => id, "event" => event, "now" => now}, context) do
    {decision, state} =
      Engine.signal(Map.fetch!(context.sessions, id), event, authority: :host, now: now)

    {%{decision: decision, state: StateCodec.export(state)},
     put_in(context, [:sessions, id], state)}
  end

  defp dispatch(
         %{"op" => "stop_contribution", "handle" => id, "report" => report} = request,
         context
       ) do
    {decision, state} =
      Engine.stop_contribution(
        Map.fetch!(context.sessions, id),
        report,
        HostInputs.context(request)
      )

    {%{decision: decision, state: StateCodec.export(state)},
     put_in(context, [:sessions, id], state)}
  end

  defp dispatch(
         %{"op" => "fail_contribution", "handle" => id, "report" => report, "now" => now},
         context
       ) do
    {decision, state} =
      Engine.fail_contribution(Map.fetch!(context.sessions, id), report,
        authority: :host,
        now: now
      )

    {%{decision: decision, state: StateCodec.export(state)},
     put_in(context, [:sessions, id], state)}
  end

  defp dispatch(%{"op" => "tick", "handle" => id, "now" => now}, context) do
    {decision, state} =
      case Engine.tick(Map.fetch!(context.sessions, id), now) do
        {:ok, state} -> {%{status: :ticked}, state}
        {:error, reason, state} -> {%{status: :rejected, reason: reason}, state}
      end
      |> Engine.mark_unchecked()

    {%{decision: decision, state: StateCodec.export(state)},
     put_in(context, [:sessions, id], state)}
  end

  defp dispatch(
         %{"op" => "view", "handle" => id, "actor" => actor, "now" => now} = request,
         context
       ) do
    policy = HostInputs.context(request)[:room_policy]

    case AgentGate.project(Map.fetch!(context.sessions, id), actor, now, policy) do
      {:ok, projection, state} -> {%{ok: projection}, put_in(context, [:sessions, id], state)}
      {:error, reason} -> {%{error: reason}, context}
    end
  end

  defp dispatch(%{"op" => "checkpoint", "handle" => id}, context),
    do: {tag(Checkpoint.export(Map.fetch!(context.sessions, id))), context}

  defp dispatch(
         %{
           "op" => "restore",
           "contract" => handle,
           "handle" => id,
           "checkpoint" => checkpoint,
           "now" => now
         } = request,
         context
       ) do
    contract = Map.fetch!(context.contracts, handle)
    # The reference host implements every extension; a stated list is checked as at new.
    capabilities = Map.get(request, "capabilities", Engine.implemented())

    case Checkpoint.restore(contract, checkpoint, now, :local_reference, capabilities: capabilities) do
      {:ok, state} -> {%{ok: StateCodec.export(state)}, put_in(context, [:sessions, id], state)}
      {:error, error} -> {%{error: error}, context}
    end
  end

  defp dispatch(
         %{
           "op" => "check",
           "contract" => handle,
           "projection" => projection,
           "message" => message,
           "actor" => actor,
           "now" => now
         },
         context
       ),
       do:
         {AgentGate.check(Map.fetch!(context.contracts, handle), projection, message, actor, now),
          context}

  defp dispatch(
         %{"op" => "room_new", "contract" => handle, "handle" => id, "config" => config},
         context
       ) do
    opts = [
      bindings: config["bindings"],
      now: config["now"],
      window_ms: config["window_ms"],
      max_events: config["max_events"],
      max_mentions: config["max_mentions"],
      max_receipts: config["max_receipts"]
    ]

    # Optional per-actor shares are passed only when the host sets them.
    opts =
      opts ++
        for {key, field} <- [
              max_events_per_actor: "max_events_per_actor",
              max_mentions_per_actor: "max_mentions_per_actor",
              max_receipts_per_actor: "max_receipts_per_actor"
            ],
            Map.has_key?(config, field),
            do: {key, config[field]}

    with [] <- Map.keys(config) -- @room_config_keys,
         {:ok, state} <- RoomEvents.new(Map.fetch!(context.contracts, handle), opts) do
      {%{ok: Map.delete(state, :contract)}, put_in(context, [:rooms, id], state)}
    else
      [_ | _] -> {%{error: :room_policy_required}, context}
      {:error, error} -> {%{error: error}, context}
    end
  end

  defp dispatch(
         %{
           "op" => "room_step",
           "handle" => id,
           "message" => message,
           "actor" => actor,
           "now" => now
         },
         context
       ) do
    {decision, state} =
      RoomEvents.step(Map.fetch!(context.rooms, id), message, actor: actor, now: now)

    {%{decision: decision, state: Map.delete(state, :contract)},
     put_in(context, [:rooms, id], state)}
  end

  defp dispatch(%{"op" => "wire", "bytes" => bytes} = request, context),
    do: {tag(Wire.decode(bytes, wire_opts(request))), context}

  defp dispatch(%{"op" => "wire_encode", "message" => message} = request, context),
    do: {tag(Wire.encode(message, wire_opts(request))), context}

  defp dispatch(
         %{
           "op" => "wire_step",
           "handle" => id,
           "actor" => _actor,
           "now" => _now,
           "bytes" => bytes
         } = request,
         context
       ) do
    case Wire.decode(bytes, wire_opts(request)) do
      {:ok, message} ->
        {decision, state} =
          Engine.step(Map.fetch!(context.sessions, id), message, HostInputs.context(request))

        {%{decision: decision, state: StateCodec.export(state)},
         put_in(context, [:sessions, id], state)}

      {:error, reason} ->
        {%{error: reason}, context}
    end
  end

  defp dispatch(%{"op" => "canonical", "value" => value}, context),
    do:
      {%{bytes: Base.encode64(Canonical.encode(value)), sha256: Canonical.digest(value)}, context}

  defp dispatch(_, context), do: {%{error: "adapter_input"}, context}

  defp wire_opts(request) do
    opts = [
      transport: Map.get(request, "transport", %{}),
      max_bytes: Map.get(request, "max_bytes", 16_384)
    ]

    Enum.reduce([:carrier], opts, fn key, opts ->
      case Map.fetch(request, Atom.to_string(key)) do
        {:ok, value} -> Keyword.put(opts, key, value)
        :error -> opts
      end
    end)
  end

  defp tag({:ok, value}), do: %{ok: value}
  defp tag({:error, error}), do: %{error: error}

  defp compile(source), do: ContractLab.compile_source(source)
end

BandSpec.ReferenceAdapter.run()
