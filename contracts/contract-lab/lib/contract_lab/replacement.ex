defmodule ContractLab.Replacement do
  @moduledoc "Journaled role-program router with snapshots, bounded sidecar repair, and an effect outbox."
  use GenServer
  alias ContractLab.{Canonical, Engine, HostKey, Journal, Program, Runner}

  def child_spec(opts),
    do: %{
      id: {__MODULE__, Path.expand(Keyword.fetch!(opts, :path))},
      start: {__MODULE__, :start_link, [opts]}
    }

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts)
  def deliver(pid, event), do: GenServer.call(pid, {:deliver, event}, :infinity)
  def status(pid), do: GenServer.call(pid, :status)
  def journal(pid), do: GenServer.call(pid, :journal)
  def snapshot(pid), do: GenServer.call(pid, :snapshot)
  def repair(pid, program), do: GenServer.call(pid, {:repair, program}, :infinity)
  def activate(pid, context), do: GenServer.call(pid, {:activate, context}, :infinity)
  def flush(pid, adapter, opts), do: GenServer.call(pid, {:flush, adapter, opts}, :infinity)
  def reconcile(pid, key, receipt), do: GenServer.call(pid, {:reconcile, key, receipt}, :infinity)

  @doc """
  Operator op (P-7): commits the stopped journal at `path` to its host key. A present key
  is committed (`"adopted"`, or `"unchanged"` when it already is); a missing one is created
  and committed (`"created"`). Journaled effects keep their keys, which replay never
  recomputes, so only new completions use the committed key. This upgrades a journal with
  effects but no key (written before HMAC keys), replaces a lost key, and rotates a key
  once the old key file is moved away. The journal's owner lock refuses it while running.
  Pass the participant's `journal_max_bytes` when it is above the 64 MiB default.

  It runs in a process of its own (Q-L11), which traps exits and owns the journal link and
  sync(1)'s port, so the caller's trap flag, links and mailbox are left as they were. Once
  started it runs to the end even if the caller exits.
  """
  def adopt_host_key(path, opts \\ []) do
    path = Path.expand(path)

    {pid, ref} =
      spawn_monitor(fn ->
        Process.flag(:trap_exit, true)

        exit(
          try do
            {:adopted, adopt_stopped(path, opts)}
          catch
            kind, reason -> {:adopt_raised, kind, reason, __STACKTRACE__}
          end
        )
      end)

    receive do
      {:DOWN, ^ref, :process, ^pid, {:adopted, result}} -> result
      {:DOWN, ^ref, :process, ^pid, {:adopt_raised, k, r, stack}} -> :erlang.raise(k, r, stack)
      {:DOWN, ^ref, :process, ^pid, reason} -> exit(reason)
    end
  end

  defp adopt_stopped(path, opts) do
    with true <- File.regular?(path) || {:error, :enoent},
         {:ok, _sync} <- HostKey.sync_command(),
         {:ok, journal} <- open_for_adoption(path, opts) do
      try do
        adopt(journal, Journal.read(journal), path <> ".host-key")
      after
        stop_journal(journal)
      end
    end
  end

  # The participant's own journal limit (Q-L13), or it could not adopt a journal above the
  # default.
  defp open_for_adoption(path, opts) do
    case Journal.start_link(
           path: path,
           max_bytes: Keyword.get(opts, :journal_max_bytes, 64 * 1024 * 1024),
           owner_release_ms: Keyword.get(opts, :owner_release_ms, 5_000)
         ) do
      {:ok, journal} -> {:ok, journal}
      {:error, {:already_started, _}} -> {:error, :owner_conflict}
      {:error, reason} -> {:error, reason}
    end
  end

  defp adopt(journal, [%{"entry" => %{"type" => "configuration"}} | _] = records, key_path) do
    committed =
      for(%{"entry" => %{"type" => "host_key", "id" => id}} <- records, do: id) |> List.last()

    with {:ok, key, origin} <- adoptable_key(key_path) do
      id = HostKey.id(key)

      if origin == :loaded and committed == id do
        {:ok, %{"action" => "unchanged", "id" => id}}
      else
        with :ok <- durable(origin, key_path),
             {:ok, _} <- Journal.append(journal, %{"type" => "host_key", "id" => id}) do
          {:ok, %{"action" => if(origin == :loaded, do: "adopted", else: "created"), "id" => id}}
        end
      end
    end
  end

  defp adopt(_, _, _), do: {:error, :configuration_missing}

  defp adoptable_key(key_path) do
    case HostKey.load(key_path) do
      {:ok, key} -> {:ok, key, :loaded}
      {:error, :enoent} -> with {:ok, key} <- HostKey.create(key_path), do: {:ok, key, :created}
      {:error, reason} -> {:error, reason}
    end
  end

  # create syncs a new key itself; a loaded key not yet committed is synced before its id
  # is journaled (Q-L12).
  defp durable(:created, _key_path), do: :ok
  defp durable(:loaded, key_path), do: HostKey.sync(key_path)

  @impl true
  def init(opts) do
    with {:ok, s} <- configure(opts),
         {:ok, _sync} <- HostKey.sync_command(),
         {:ok, journal} <-
           Journal.start_link(
             [
               path: opts[:path],
               max_bytes: Keyword.get(opts, :journal_max_bytes, 64 * 1024 * 1024)
             ] ++ journal_release(opts)
           ) do
      with {:ok, s} <- boot(%{s | journal: journal}, Path.expand(opts[:path]) <> ".host-key") do
        {:ok, s, {:continue, :drain}}
      else
        # A link kill would skip the journal's terminate and leave its lock
        # behind; stop it so the lock is released before this start fails.
        {:error, reason} ->
          stop_journal(journal)
          {:stop, reason}
      end
    else
      {:error, reason} -> {:stop, reason}
    end
  end

  # How long the journal waits for a previous owner's lock (and for its own lock
  # child on stop); the journal's default unless set.
  defp journal_release(opts) do
    case Keyword.fetch(opts, :journal_owner_release_ms) do
      {:ok, ms} -> [owner_release_ms: ms]
      :error -> []
    end
  end

  defp stop_journal(journal) do
    GenServer.stop(journal)
  catch
    :exit, _ -> :ok
  end

  @impl true
  def handle_continue(:drain, s), do: {:noreply, drain(s)}

  # The host key never appears in crash reports or sys status output. The state holds it
  # only behind a closure (P-2): a crash reason that carries the raw state, such as a
  # function clause error's arguments, prints a fun without its captured key.
  @impl true
  def format_status(%{state: %{host_key: key} = s} = status) when not is_nil(key),
    do: %{status | state: %{s | host_key: :redacted}}

  def format_status(status), do: status

  @impl true
  def handle_call(:status, _from, s) do
    public = %{
      mode: effective_mode(s),
      program: Program.fingerprint(s.program),
      state: s.state,
      completed_inputs: map_size(s.completed),
      pending_inputs: length(pending(s)),
      sidecar_calls: s.sidecar_calls,
      last_fault: s.last_fault,
      replayed_inputs: s.replayed,
      effects: Enum.map(s.effect_order, &s.effects[&1]),
      definition: s.view
    }

    {:reply, public, s}
  end

  def handle_call(:journal, _from, s), do: {:reply, Journal.read(s.journal), s}
  def handle_call(:snapshot, _from, s), do: {:reply, save_snapshot(s), s}

  def handle_call({:deliver, raw}, _from, s) do
    case normalize_delivery(raw, s) do
      {:error, reason} ->
        {:reply, {:error, reason}, s}

      {:ok, event} ->
        id = event["id"]

        cond do
          Map.has_key?(s.inputs, id) and s.inputs[id] != event ->
            {:reply, {:error, :id_conflict}, s}

          Map.has_key?(s.completed, id) ->
            {:reply, {:ok, :duplicate}, s}

          Map.has_key?(s.inputs, id) ->
            {:reply, {:ok, :queued}, s}

          length(pending(s)) >= s.config["max_pending"] ->
            {:reply, {:error, :pending_limit}, s}

          true ->
            case Journal.append(s.journal, %{"type" => "input", "event" => event}) do
              {:ok, _} ->
                s =
                  %{s | inputs: Map.put(s.inputs, id, event), input_order: s.input_order ++ [id]}
                  |> drain()

                reply = if Map.has_key?(s.completed, id), do: :completed, else: :queued
                {:reply, {:ok, reply}, s}

              {:error, reason} ->
                {:reply, {:error, reason}, s}
            end
        end
    end
  end

  def handle_call({:repair, candidate}, _from, s) do
    case install(s, candidate) do
      {:ok, updated} -> {:reply, {:ok, :repaired}, drain(updated)}
      {:error, fault} -> {:reply, {:error, fault}, s}
    end
  end

  def handle_call({:activate, context}, _from, s) do
    if context[:actor] in s.config["controls"] and is_binary(context[:reason]) and
         byte_size(context[:reason]) in 1..4000 do
      s =
        mark_fault(s, Runner.fault("activation", context[:reason])) |> invoke_sidecar() |> drain()

      result = if s.mode == :program, do: {:ok, :repaired}, else: {:error, :needs_agent}
      {:reply, result, s}
    else
      {:reply, {:error, :unauthorized}, s}
    end
  end

  def handle_call({:flush, adapter, opts}, _from, s) do
    name = opts[:name]
    idempotent = opts[:idempotent]

    cond do
      not is_function(adapter, 1) or not is_binary(name) or name == "" or
          not is_boolean(idempotent) ->
        {:reply, {:error, :invalid_adapter}, s}

      Enum.any?(s.effects, fn {_, e} ->
        (e["status"] != "done" and e["adapter"]) &&
            (e["adapter"] != name or e["idempotent"] != idempotent)
      end) ->
        {:reply, {:error, :adapter_mismatch}, s}

      true ->
        # Strictly in effect_order: the pass halts at the first effect that is not done,
        # so a later effect never overtakes an unresolved earlier one.
        {s, count} =
          Enum.reduce_while(s.effect_order, {s, 0}, fn key, {s, count} ->
            e = s.effects[key]

            cond do
              e["status"] == "done" ->
                {:cont, {s, count}}

              e["status"] == "pending" or (e["status"] == "unknown" and e["idempotent"] == true) ->
                case dispatch(s, e, adapter, name, idempotent) do
                  {updated, true} -> {:cont, {updated, count + 1}}
                  {updated, false} -> {:halt, {updated, count}}
                end

              true ->
                {:halt, {s, count}}
            end
          end)

        s = drain(s)

        result =
          cond do
            unresolved?(s) ->
              {:error, :unresolved_effects}

            Enum.any?(s.effects, fn {_, e} -> e["status"] == "pending" end) ->
              {:error, :outbox_blocked}

            true ->
              {:ok, count}
          end

        {:reply, result, s}
    end
  end

  def handle_call({:reconcile, key, outcome}, _from, s) do
    with %{"status" => "unknown"} = effect <- s.effects[key],
         {:ok, status, receipt} <- reconciliation(outcome),
         {:ok, _} <-
           Journal.append(s.journal, %{
             "type" => "effect_result",
             "key" => key,
             "status" => status,
             "receipt" => receipt
           }) do
      updated =
        %{
          s
          | effects:
              Map.put(
                s.effects,
                key,
                Map.merge(effect, %{"status" => status, "receipt" => receipt})
              )
        }
        |> drain()

      {:reply, {:ok, :reconciled}, updated}
    else
      _ -> {:reply, {:error, :invalid_reconciliation}, s}
    end
  end

  def handle_call(_request, _from, s), do: {:reply, {:error, :unknown_call}, s}

  # An unknown cast is ignored, as an unknown call is refused, instead of crashing (Q-L16).
  @impl true
  def handle_cast(_request, s), do: {:noreply, s}

  @impl true
  def terminate(_reason, s) do
    if is_pid(s.journal) and Process.alive?(s.journal), do: GenServer.stop(s.journal)
    :ok
  end

  defp configure(opts) do
    c = opts[:contract]
    role = opts[:role]

    with true <-
           (is_map(c) and role in c.roles and Engine.valid_bindings?(c, opts[:bindings])) ||
             {:error, :invalid_binding},
         {:ok, view} <- ContractLab.for_role(c, role),
         :ok <- program_valid(opts[:program]),
         {:ok, state_version} <-
           program_version(opts[:program], Keyword.get(opts, :execution_timeout, 1000)) do
      config = %{
        "profile" => "contract-lab/replacement/1",
        "definition" => c.digest,
        "source" => c.source_digest,
        "role" => role,
        "session" => opts[:session_id],
        "bindings" => opts[:bindings],
        "initial_program" => Program.fingerprint(opts[:program]),
        "state_version" => state_version,
        "snapshot_every" => Keyword.get(opts, :snapshot_every, 20),
        "max_repairs" => Keyword.get(opts, :max_repairs, 2),
        "execution_timeout" => Keyword.get(opts, :execution_timeout, 1000),
        "sidecar_timeout" => Keyword.get(opts, :sidecar_timeout, 60_000),
        "max_pending" => Keyword.get(opts, :max_pending, 100),
        "max_effect_attempts" => Keyword.get(opts, :max_effect_attempts, 3),
        "controls" => Keyword.get(opts, :controls, []),
        "allowed_actions" => Keyword.get(opts, :allowed_actions, [])
      }

      integers =
        ~w(snapshot_every max_repairs execution_timeout sidecar_timeout max_pending max_effect_attempts)

      if Enum.all?(integers, &(is_integer(config[&1]) and config[&1] > 0)) and
           is_binary(config["session"]) and
           Journal.json?(config) and
           Enum.all?(config["controls"], &(&1 in Map.values(opts[:bindings]))) do
        {:ok,
         %{
           contract: c,
           view: view,
           config: config,
           program: opts[:program],
           programs: Enum.uniq([opts[:program] | Keyword.get(opts, :programs, [])]),
           sidecar: opts[:sidecar],
           journal: nil,
           host_key: nil,
           state: nil,
           initial_state: nil,
           mode: :program,
           inputs: %{},
           input_order: [],
           completed: %{},
           effects: %{},
           effect_order: [],
           sidecar_calls: 0,
           last_fault: nil,
           replayed: 0
         }}
      else
        {:error, :invalid_config}
      end
    else
      {:error, reason} -> {:error, reason}
    end
  end

  defp boot(s, key_path) do
    records = Journal.read(s.journal)

    with :ok <- verify_configuration(s, records),
         {:ok, s} <- host_key(s, key_path, records),
         {:ok, s} <- fold_records(s, records),
         {:ok, s} <- ensure_initial(s, records),
         {:ok, state, replayed} <- reconstruct(s, s.program, :snapshot) do
      {:ok, %{s | state: state, replayed: replayed}}
    end
  end

  defp verify_configuration(s, []) do
    case Journal.append(s.journal, %{"type" => "configuration", "config" => s.config}) do
      {:ok, _} -> :ok
      {:error, reason} -> {:error, reason}
    end
  end

  defp verify_configuration(s, [
         %{"entry" => %{"type" => "configuration", "config" => config}} | _
       ]) do
    if config == s.config, do: :ok, else: {:error, :configuration_mismatch}
  end

  defp verify_configuration(_, _), do: {:error, :configuration_mismatch}

  # The journal commits to the host key by id (O1). A key is created only while no effect
  # has been journaled, and its id is recorded; a lost key with effects refuses the start
  # rather than re-keying the outbox. Once an id is journaled, a different key file refuses
  # it too, effects or not (Q-L14, Ada): only adopt commits it. While no id is journaled, a
  # present key (a first create whose journal append was lost to a crash, P-6, or a pre-O1
  # journal) is synced and committed.
  defp host_key(s, path, records) do
    committed =
      for(%{"entry" => %{"type" => "host_key", "id" => id}} <- records, do: id) |> List.last()

    with {:ok, key, origin} <- load_or_create_key(path, records) do
      id = HostKey.id(key)

      cond do
        origin == :loaded and committed == id ->
          {:ok, %{s | host_key: sealed(key)}}

        origin == :created or is_nil(committed) ->
          with :ok <- durable(origin, path),
               {:ok, _} <- Journal.append(s.journal, %{"type" => "host_key", "id" => id}) do
            {:ok, %{s | host_key: sealed(key)}}
          end

        true ->
          {:error, :host_key_mismatch}
      end
    end
  end

  defp sealed(key), do: fn -> key end

  defp load_or_create_key(path, records) do
    case HostKey.load(path) do
      {:ok, key} ->
        {:ok, key, :loaded}

      {:error, :enoent} ->
        if Enum.any?(records, &effect_record?/1) do
          {:error, :host_key_missing}
        else
          with {:ok, key} <- HostKey.create(path), do: {:ok, key, :created}
        end

      {:error, reason} ->
        {:error, reason}
    end
  end

  defp effect_record?(%{"entry" => %{"type" => "completed", "effects" => [_ | _]}}), do: true

  defp effect_record?(%{"entry" => %{"type" => type}}),
    do: type in ~w(effect_started effect_result)

  defp effect_record?(_), do: false

  defp fold_records(s, records) do
    Enum.reduce_while(records, {:ok, s}, fn %{"entry" => entry}, {:ok, s} ->
      case entry do
        %{"type" => "configuration"} ->
          {:cont, {:ok, s}}

        %{"type" => "host_key"} ->
          {:cont, {:ok, s}}

        %{"type" => "initial", "state" => state} when is_map(state) and is_nil(s.initial_state) ->
          {:cont, {:ok, %{s | initial_state: state}}}

        %{"type" => "input", "event" => event} ->
          {:cont,
           {:ok,
            %{
              s
              | inputs: Map.put(s.inputs, event["id"], event),
                input_order: s.input_order ++ [event["id"]]
            }}}

        %{"type" => "completed", "input" => id} ->
          {:cont, {:ok, add_completion(s, id, entry)}}

        %{"type" => "fault", "fault" => fault} ->
          {:cont, {:ok, %{s | mode: :needs_agent, last_fault: fault}}}

        %{"type" => "agent_call"} ->
          {:cont, {:ok, %{s | sidecar_calls: s.sidecar_calls + 1, mode: :needs_agent}}}

        %{"type" => "program", "program" => fingerprint} ->
          case Enum.find(s.programs, &(Program.fingerprint(&1) == fingerprint)) do
            nil -> {:halt, {:error, :program_unavailable}}
            program -> {:cont, {:ok, %{s | program: program, mode: :program}}}
          end

        %{
          "type" => "effect_started",
          "key" => key,
          "adapter" => adapter,
          "idempotent" => idempotent
        } ->
          effect =
            s.effects[key]
            |> Map.merge(%{
              "status" => "unknown",
              "adapter" => adapter,
              "idempotent" => idempotent
            })
            |> Map.update!("attempts", &(&1 + 1))

          {:cont, {:ok, %{s | effects: Map.put(s.effects, key, effect)}}}

        %{"type" => "effect_result", "key" => key, "status" => status, "receipt" => receipt} ->
          effect = Map.merge(s.effects[key], %{"status" => status, "receipt" => receipt})
          {:cont, {:ok, %{s | effects: Map.put(s.effects, key, effect)}}}

        _ ->
          {:halt, {:error, :invalid_journal_entry}}
      end
    end)
  end

  defp reconstruct(s, program, mode) do
    records = Journal.read(s.journal)

    with {:ok, initial} <- initial(program, s),
         true <- initial == s.initial_state || {:error, :initial_state_mismatch},
         {:ok, state, after_sequence} <- starting_state(s, records, initial, mode) do
      records
      |> Enum.filter(&(&1["sequence"] > after_sequence and &1["entry"]["type"] == "completed"))
      |> Enum.reduce_while({:ok, state, 0}, fn %{"entry" => completion}, {:ok, state, count} ->
        event = s.inputs[completion["input"]]

        # Keys are journaled once and never recomputed (O1): replay compares the program's
        # intents only, so a moved journal or a rotated key keeps its destination IDs.
        case execute(program, event, state, s) do
          {:ok, next, intents} ->
            if Canonical.digest(next) == completion["state_hash"] and
                 intents == Enum.map(completion["effects"], & &1["intent"]) do
              {:cont, {:ok, next, count + 1}}
            else
              {:halt, {:error, :replay_diverged}}
            end

          _ ->
            {:halt, {:error, :replay_failed}}
        end
      end)
    end
  end

  defp starting_state(_s, _records, initial, :full), do: {:ok, initial, 0}

  defp starting_state(s, records, initial, :snapshot) do
    case Journal.latest_snapshot(s.journal) do
      {:ok, %{"sequence" => position, "state" => data}} ->
        latest =
          records
          |> Enum.filter(&(&1["sequence"] <= position and &1["entry"]["type"] == "completed"))
          |> List.last()

        expected = if latest, do: latest["entry"]["state_hash"], else: Canonical.digest(initial)

        if data["definition"] == s.config["definition"] and
             data["state_version"] == s.config["state_version"] and
             is_map(data["state"]) and Canonical.digest(data["state"]) == expected do
          {:ok, data["state"], position}
        else
          {:ok, initial, 0}
        end

      {:error, :snapshot_position} ->
        {:error, :snapshot_position}

      _ ->
        {:ok, initial, 0}
    end
  end

  defp initial(program, s) do
    case Runner.run(fn -> program.init(s.view) end, s.config["execution_timeout"]) do
      {:ok, {:ok, state}} ->
        if is_map(state) and Journal.json?(state),
          do: {:ok, state},
          else: {:error, :invalid_initial_state}

      _ ->
        {:error, :invalid_initial_state}
    end
  end

  defp drain(s) do
    case {effective_mode(s), pending(s)} do
      {:program, [id | _]} ->
        case execute(s.program, s.inputs[id], s.state, s) do
          {:ok, state, intents} ->
            entry = %{
              "type" => "completed",
              "input" => id,
              "state_hash" => Canonical.digest(state),
              "effects" => keyed(s, s.inputs[id], intents),
              "program" => Program.fingerprint(s.program)
            }

            case Journal.append(s.journal, entry) do
              {:ok, _} ->
                s = add_completion(%{s | state: state}, id, entry)

                if rem(map_size(s.completed), s.config["snapshot_every"]) == 0,
                  do: save_snapshot(s)

                drain(s)

              {:error, reason} ->
                %{s | mode: :needs_agent, last_fault: Runner.fault("journal", inspect(reason))}
            end

          {:error, fault} ->
            next = mark_fault(s, fault) |> invoke_sidecar()
            if next.mode == :program, do: drain(next), else: next
        end

      _ ->
        s
    end
  end

  defp execute(program, event, state, s) do
    case Runner.run(fn -> program.handle(event, state) end, s.config["execution_timeout"]) do
      {:ok, {:ok, next, intents}} ->
        if is_map(next) and Journal.json?(next) and is_list(intents) and length(intents) <= 32 and
             Enum.all?(intents, &intent_valid?(&1, s)) do
          {:ok, next, intents}
        else
          {:error, Runner.fault("program_output", "Invalid state or effect intent")}
        end

      {:ok, {:unhandled, reason}} ->
        {:error, Runner.fault("unhandled", inspect(reason))}

      {:ok, _} ->
        {:error, Runner.fault("program_output", "Expected JSON state and effect intents")}

      {:error, fault} ->
        {:error, fault}
    end
  end

  # Keys are assigned once, when a completion is first journaled. A public digest would let
  # a co-recipient preclaim the room message ID; the private host key makes it unpredictable.
  defp keyed(s, event, intents) do
    intents
    |> Enum.with_index()
    |> Enum.map(fn {intent, index} ->
      key =
        HostKey.mac(s.host_key, [
          "contract-lab/effect-key/1",
          s.config["session"],
          s.config["role"],
          event["id"],
          index
        ])

      %{"key" => key, "input" => event["id"], "intent" => intent}
    end)
  end

  defp intent_valid?(%{"type" => "room", "kind" => kind, "payload" => payload} = intent, s)
       when map_size(intent) == 3 do
    type = s.contract.types[kind]

    all_rules =
      s.contract.any_rules ++ Enum.flat_map(s.contract.states, fn {_, state} -> state.rules end)

    type != nil and Engine.validate_payload(type, payload) and
      Enum.any?(all_rules, &(&1.kind == kind and s.config["role"] in &1.from)) and
      Journal.json?(intent)
  end

  defp intent_valid?(%{"type" => "effect", "action" => action, "payload" => payload} = intent, s)
       when map_size(intent) == 3 do
    action in s.config["allowed_actions"] and is_map(payload) and Journal.json?(intent)
  end

  defp intent_valid?(_, _), do: false

  defp add_completion(s, id, entry) do
    effects =
      Map.new(entry["effects"], fn e ->
        {e["key"],
         Map.merge(e, %{
           "status" => "pending",
           "attempts" => 0,
           "adapter" => nil,
           "idempotent" => nil
         })}
      end)

    %{
      s
      | completed: Map.put(s.completed, id, entry),
        effects: Map.merge(s.effects, effects),
        effect_order: s.effect_order ++ Enum.map(entry["effects"], & &1["key"])
    }
  end

  defp mark_fault(s, fault) do
    case Journal.append(s.journal, %{"type" => "fault", "fault" => fault}) do
      {:ok, _} ->
        %{s | mode: :needs_agent, last_fault: fault}

      {:error, reason} ->
        %{s | mode: :needs_agent, last_fault: Runner.fault("journal", inspect(reason))}
    end
  end

  defp invoke_sidecar(s) do
    if is_function(s.sidecar, 1) and s.sidecar_calls < s.config["max_repairs"] do
      case Journal.append(s.journal, %{"type" => "agent_call", "number" => s.sidecar_calls + 1}) do
        {:ok, _} ->
          s = %{s | sidecar_calls: s.sidecar_calls + 1}

          case Runner.run(fn -> s.sidecar.(bundle(s)) end, s.config["sidecar_timeout"]) do
            {:ok, {:ok, candidate}} ->
              case install(s, candidate) do
                {:ok, updated} -> updated
                {:error, fault} -> mark_fault(s, fault) |> invoke_sidecar()
              end

            {:ok, response} ->
              mark_fault(s, Runner.fault("sidecar", inspect(response)))

            {:error, fault} ->
              mark_fault(s, fault)
          end

        {:error, reason} ->
          %{s | mode: :needs_agent, last_fault: Runner.fault("journal", inspect(reason))}
      end
    else
      s
    end
  end

  defp install(s, candidate) do
    with :ok <- program_valid(candidate),
         {:ok, version} <- program_version(candidate, s.config["execution_timeout"]),
         true <-
           version == s.config["state_version"] ||
             {:error, :state_version_mismatch},
         {:ok, state, _} <- reconstruct(s, candidate, :full),
         :ok <- probe_pending(s, candidate, state),
         {:ok, _} <-
           Journal.append(s.journal, %{
             "type" => "program",
             "program" => Program.fingerprint(candidate)
           }) do
      updated = %{
        s
        | program: candidate,
          programs: Enum.uniq([candidate | s.programs]),
          state: state,
          mode: :program
      }

      save_snapshot(updated)
      {:ok, updated}
    else
      {:error, %{} = fault} -> {:error, fault}
      {:error, reason} -> {:error, Runner.fault("candidate", inspect(reason))}
    end
  end

  defp probe_pending(s, candidate, state) do
    case pending(s) do
      [] ->
        :ok

      [id | _] ->
        case execute(candidate, s.inputs[id], state, s) do
          {:ok, _, _} -> :ok
          {:error, fault} -> {:error, fault}
        end
    end
  end

  defp bundle(s) do
    records = Journal.read(s.journal)

    snapshot =
      case Journal.latest_snapshot(s.journal) do
        {:ok, value} -> value
        _ -> nil
      end

    normalize(%{
      fault: s.last_fault,
      contract: s.view,
      snapshot: snapshot,
      program: Program.fingerprint(s.program),
      state_version: s.config["state_version"],
      journal: Journal.info(s.journal),
      recent_records: Enum.take(records, -20),
      pending_input:
        case pending(s) do
          [id | _] -> s.inputs[id]
          [] -> nil
        end,
      unresolved_effects: Enum.filter(Map.values(s.effects), &(&1["status"] == "unknown")),
      remaining_repairs: s.config["max_repairs"] - s.sidecar_calls
    })
  end

  defp save_snapshot(s) do
    Journal.snapshot(s.journal, %{
      "definition" => s.config["definition"],
      "state_version" => s.config["state_version"],
      "program" => Program.fingerprint(s.program),
      "state" => s.state,
      "completed" => map_size(s.completed)
    })
  end

  defp dispatch(s, effect, adapter, name, idempotent) do
    if effect["attempts"] >= s.config["max_effect_attempts"] do
      {s, false}
    else
      entry = %{
        "type" => "effect_started",
        "key" => effect["key"],
        "adapter" => name,
        "idempotent" => idempotent
      }

      case Journal.append(s.journal, entry) do
        {:ok, _} ->
          effect =
            Map.merge(effect, %{
              "status" => "unknown",
              "adapter" => name,
              "idempotent" => idempotent,
              "attempts" => effect["attempts"] + 1
            })

          s = %{s | effects: Map.put(s.effects, effect["key"], effect)}

          {status, receipt} =
            case Runner.run(fn -> adapter.(effect) end, s.config["execution_timeout"]) do
              {:ok, {:ok, receipt}} ->
                if Journal.json?(receipt),
                  do: {"done", receipt},
                  else: {"unknown", %{"error" => "Invalid destination receipt"}}

              {:ok, {:not_applied, reason}} ->
                {"unknown", %{"not_applied" => inspect(reason)}}

              {:ok, response} ->
                {"unknown", %{"response" => inspect(response)}}

              {:error, fault} ->
                {"unknown", fault}
            end

          case Journal.append(s.journal, %{
                 "type" => "effect_result",
                 "key" => effect["key"],
                 "status" => status,
                 "receipt" => receipt
               }) do
            {:ok, _} ->
              s = %{
                s
                | effects:
                    Map.put(
                      s.effects,
                      effect["key"],
                      Map.merge(effect, %{"status" => status, "receipt" => receipt})
                    )
              }

              {s, status == "done"}

            _ ->
              {s, false}
          end

        _ ->
          {s, false}
      end
    end
  end

  defp reconciliation({:applied, receipt}) do
    if Journal.json?(receipt), do: {:ok, "done", receipt}, else: {:error, :invalid_receipt}
  end

  defp reconciliation({:not_applied, receipt}) do
    if Journal.json?(receipt), do: {:ok, "pending", receipt}, else: {:error, :invalid_receipt}
  end

  defp reconciliation(_), do: {:error, :invalid_receipt}

  defp normalize_delivery(raw, s) when is_map(raw) do
    event = normalize(raw)

    with "accepted" <- event["status"],
         true <-
           event["definition"] == s.config["definition"] and
             event["session"] == s.config["session"],
         true <- is_binary(event["id"]) and byte_size(event["id"]) in 1..256,
         true <- Map.has_key?(s.config["bindings"], event["role"]) and is_binary(event["actor"]),
         true <- s.config["bindings"][event["role"]] == event["actor"],
         recipients when is_list(recipients) <- event["recipients"],
         true <- s.config["bindings"][s.config["role"]] in recipients,
         type when is_map(type) <- s.contract.types[event["kind"]],
         true <- Engine.validate_payload(type, event["payload"]) and Journal.json?(event) do
      {:ok, event}
    else
      _ -> {:error, :invalid_delivery}
    end
  rescue
    _ -> {:error, :invalid_delivery}
  end

  defp normalize_delivery(_, _), do: {:error, :invalid_delivery}

  defp normalize(value), do: value |> Canonical.encode() |> JSON.decode!()

  defp program_valid(module) when is_atom(module) and not is_nil(module) do
    if Code.ensure_loaded?(module) and function_exported?(module, :init, 1) and
         function_exported?(module, :handle, 2) and
         function_exported?(module, :state_version, 0),
       do: :ok,
       else: {:error, :invalid_program}
  end

  defp program_valid(_), do: {:error, :invalid_program}

  defp program_version(module, timeout) when is_integer(timeout) and timeout > 0 do
    case Runner.run(fn -> module.state_version() end, timeout) do
      {:ok, value} when is_binary(value) and byte_size(value) in 1..128 -> {:ok, value}
      {:error, fault} -> {:error, fault}
      _ -> {:error, :invalid_state_version}
    end
  end

  defp program_version(_, _), do: {:error, :invalid_config}

  # A start whose init failed leaves only its configuration and host-key records; the next
  # start creates the initial record then (P-1). Anything else without one is damaged.
  defp ensure_initial(%{initial_state: nil} = s, records) do
    if Enum.all?(records, &(&1["entry"]["type"] in ["configuration", "host_key"])) do
      with {:ok, initial} <- initial(s.program, s),
           {:ok, _} <- Journal.append(s.journal, %{"type" => "initial", "state" => initial}) do
        {:ok, %{s | initial_state: initial}}
      end
    else
      {:error, :missing_initial_state}
    end
  end

  defp ensure_initial(s, _), do: {:ok, s}
  defp pending(s), do: Enum.reject(s.input_order, &Map.has_key?(s.completed, &1))
  defp unresolved?(s), do: Enum.any?(s.effects, fn {_, e} -> e["status"] == "unknown" end)
  defp effective_mode(s), do: if(unresolved?(s), do: :needs_reconciliation, else: s.mode)
end
