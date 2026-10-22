defmodule ContractLab.Examples.InitialCounter do
  @behaviour ContractLab.Program
  def state_version, do: "1"
  def init(_view), do: {:ok, %{"total" => 0}}
  def handle(%{"payload" => %{"by" => 13}}, _state), do: raise("Counter crashes on 13")

  def handle(%{"kind" => "increment", "payload" => %{"by" => by}}, state) do
    total = state["total"] + by

    {:ok, %{"total" => total},
     [%{"type" => "room", "kind" => "total", "payload" => %{"value" => total}}]}
  end

  def handle(_, _), do: {:unhandled, "Unknown counter input"}
end

defmodule ContractLab.Examples.RoomRecovery do
  @moduledoc "Executable room bridge example. Its authority and room remain alive during program restart."
  alias ContractLab.{CommandSidecar, Engine, Replacement, RoomEvents, Wire}
  alias ContractLab.Examples.InitialCounter
  @session "counter-demo"

  def run(directory) do
    directory = Path.expand(directory)
    File.mkdir_p!(directory)
    path = Path.join(directory, "participant.jsonl")

    if File.exists?(path),
      do: raise("Use a fresh demo directory; existing journals are preserved")

    {:ok, contract} = ContractLab.load(Path.expand("../contracts/agent-control.band", __DIR__))
    bindings = %{"requester" => "alice", "counter" => "bot"}

    {:ok, engine} =
      Engine.new(contract,
        session_id: @session,
        bindings: bindings,
        now: 0,
        inactivity: :accepted,
        max_messages: 100,
        # A local demo without a room host: explicitly unchecked, never evidence.
        room_policy: :unchecked
      )

    {:ok, authority} = Agent.start_link(fn -> engine end)
    {:ok, room} = RoomSim.start_link(id: "recovery-demo")
    {:ok, alice} = RoomSim.join(room, "alice", :agent)
    {:ok, bot} = RoomSim.join(room, "bot", :agent)
    :ok = RoomSim.connect(alice)
    :ok = RoomSim.connect(bot)
    workspace = Path.join(directory, "artifacts")

    sidecar =
      CommandSidecar.new(
        workspace: workspace,
        executable: System.find_executable("python3"),
        args: [Path.join(__DIR__, "generate_counter.py"), "{bundle}", "{output}"],
        validate:
          {System.find_executable("elixir"),
           [
             "-pa",
             :code.which(ContractLab.Program) |> to_string() |> Path.dirname(),
             Path.join(__DIR__, "validate_counter.exs"),
             "{candidate}"
           ]},
        timeout_ms: 5000
      )

    opts = [
      path: path,
      contract: contract,
      bindings: bindings,
      role: "counter",
      session_id: @session,
      program: InitialCounter,
      snapshot_every: 2,
      max_repairs: 2,
      execution_timeout: 5000,
      sidecar_timeout: 15000,
      controls: ["alice"],
      sidecar: sidecar
    ]

    adapter = fn effect -> publish(effect, authority, bot, room) end
    {:ok, program} = Replacement.start_link(opts)

    try do
      [1, 2] |> Enum.each(&round_trip(&1, false, alice, room, authority, program, adapter))
      ordinary = Replacement.status(program).sidecar_calls
      repeated = round_trip(13, true, alice, room, authority, program, adapter)
      before_restart = Replacement.status(program)
      GenServer.stop(program)
      {:ok, generated} = CommandSidecar.load_programs(workspace)
      {:ok, resumed} = Replacement.start_link(Keyword.put(opts, :programs, generated))

      try do
        restored = Replacement.status(resumed)

        {:ok, sent} =
          Replacement.flush(resumed, adapter, name: "room-simulator", idempotent: false)

        {:ok, history} = RoomSim.history(alice)

        totals =
          for m <- history, m.sender_id == "bot", do: JSON.decode!(m.content)["payload"]["value"]

        [request | _] = Path.wildcard(Path.join(workspace, "requests/*/bundle.json"))
        bundle = request |> File.read!() |> JSON.decode!()
        controls = control_round_trip(contract, bindings, alice, bot, room, resumed)

        Map.merge(controls, %{
          "totals" => totals,
          "ordinary_sidecar_calls" => ordinary,
          "repair_sidecar_calls" => before_restart.sidecar_calls,
          "state_after_restart" => restored.state,
          "replayed_after_snapshot" => restored.replayed_inputs,
          "repeated_delivery" => to_string(repeated),
          "restart_publications" => sent,
          "completed_inputs" => restored.completed_inputs,
          "generated_releases" => length(generated),
          "snapshot_at_fault" => bundle["snapshot"]["state"]["state"],
          "journal_path" => path,
          "provider" => "scripted code generator; no live model call"
        })
      after
        if Process.alive?(resumed), do: GenServer.stop(resumed)
      end
    after
      if Process.alive?(program), do: GenServer.stop(program)
      if Process.alive?(authority), do: Agent.stop(authority)
      if Process.alive?(room), do: GenServer.stop(room)
    end
  end

  defp round_trip(by, duplicate?, alice, room, authority, program, adapter) do
    {:ok, _} =
      RoomSim.post(alice, JSON.encode!(%{"kind" => "increment", "payload" => %{"by" => by}}),
        mentions: ["bot"]
      )

    if duplicate? do
      [delivery] = RoomSim.pending(room)
      {:ok, _} = RoomSim.duplicate(room, delivery.id)
    end

    RoomSim.flush(room)
    decision = admit(receive_post("bot"), authority, room)
    {:ok, :completed} = Replacement.deliver(program, decision)

    repeated =
      if duplicate? do
        %{status: :duplicate, original: original} = admit(receive_post("bot"), authority, room)
        {:ok, :duplicate} = Replacement.deliver(program, original)
        :duplicate
      end

    {:ok, 1} = Replacement.flush(program, adapter, name: "room-simulator", idempotent: false)
    RoomSim.flush(room)
    _reply = receive_post("alice")
    repeated
  end

  defp admit(message, authority, room) do
    {:ok, input} =
      Wire.decode(message.content, transport: %{"id" => message.id, "session" => @session})

    Agent.get_and_update(
      authority,
      &Engine.step(&1, input, actor: message.sender_id, now: RoomSim.now(room))
    )
  end

  defp publish(effect, authority, bot, room) do
    input =
      effect["intent"]
      |> Map.take(["kind", "payload"])
      |> Map.merge(%{"id" => effect["key"], "session" => @session})

    decision =
      Agent.get_and_update(
        authority,
        &Engine.step(&1, input, actor: "bot", now: RoomSim.now(room))
      )

    case decision do
      %{status: :accepted, recipients: recipients} ->
        {:ok, bytes} = Wire.encode(input, transport: %{"session" => @session})
        {:ok, post} = RoomSim.post(bot, bytes, mentions: recipients)
        {:ok, %{"room_message_id" => post.id, "effect_key" => effect["key"]}}

      _ ->
        {:not_applied, decision}
    end
  end

  defp control_round_trip(contract, bindings, alice, bot, room, program) do
    {:ok, controls} =
      RoomEvents.new(contract,
        bindings: bindings,
        now: 0,
        window_ms: 60_000,
        max_events: 2,
        max_mentions: 2,
        max_receipts: 10
      )

    bytes =
      JSON.encode!(%{
        "kind" => "activate",
        "payload" => %{"reason" => "Check the new operating instruction"}
      })

    {:ok, _} = RoomSim.post(alice, bytes, mentions: ["bot"])
    [delivery] = RoomSim.pending(room)
    {:ok, _} = RoomSim.duplicate(room, delivery.id)
    RoomSim.flush(room)
    first = receive_post("bot")
    {:ok, input} = Wire.decode(first.content, transport: %{"id" => first.id})

    {%{status: :accepted} = decision, controls} =
      RoomEvents.step(controls, input, actor: first.sender_id, now: 0)

    {:ok, :repaired} =
      Replacement.activate(program, actor: decision.actor, reason: decision.payload["reason"])

    count = Replacement.status(program).sidecar_calls
    second = receive_post("bot")
    {:ok, duplicate} = Wire.decode(second.content, transport: %{"id" => second.id})

    {%{status: :duplicate}, controls} =
      RoomEvents.step(controls, duplicate, actor: second.sender_id, now: 0)

    # Duplicate control deliveries do not invoke activate/2 again.
    {:ok, _} = RoomSim.post(bot, bytes, mentions: ["alice"])
    RoomSim.flush(room)
    wrong = receive_post("alice")
    {:ok, wrong_input} = Wire.decode(wrong.content, transport: %{"id" => wrong.id})

    {%{status: :rejected, reason: reason}, _} =
      RoomEvents.step(controls, wrong_input, actor: wrong.sender_id, now: 0)

    %{
      "control_activation" => "repaired",
      "control_duplicate_calls" => Replacement.status(program).sidecar_calls - count,
      "unauthorized_control" => to_string(reason),
      "control_contract_documentation" => decision.metadata.doc
    }
  end

  defp receive_post(participant) do
    receive do
      {:room_sim, "recovery-demo", ^participant, :message_created, message} -> message
    after
      1000 -> raise "Expected a room notification for #{participant}"
    end
  end
end

if System.get_env("CONTRACT_LAB_DEMO") == "1" do
  [directory] = System.argv()
  directory |> ContractLab.Examples.RoomRecovery.run() |> JSON.encode!() |> IO.puts()
end
