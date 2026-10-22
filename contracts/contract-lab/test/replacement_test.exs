defmodule ContractLab.ReplacementTest.FlakyInit do
  # P-1: init fails while the flag is set, as a transient timeout or crash would.
  @behaviour ContractLab.Program
  def state_version, do: "1"

  def init(view) do
    if :persistent_term.get({__MODULE__, :fail}, false),
      do: raise("transient init failure"),
      else: ContractLab.TestPrograms.Counter.init(view)
  end

  defdelegate handle(event, state), to: ContractLab.TestPrograms.Counter
end

defmodule ContractLab.ReplacementTest do
  use ExUnit.Case, async: false
  alias ContractLab.{Canonical, Engine, Replacement, Program, Journal}

  alias ContractLab.TestPrograms.{
    Counter,
    Broken,
    Fixed,
    Extended,
    Divergent,
    VersionTwo,
    Hangs,
    ExternalEffect,
    InvalidOutput
  }

  @moduletag capture_log: true

  setup do
    Process.flag(:trap_exit, true)

    dir =
      Path.join(System.tmp_dir!(), "contract-replacement-#{System.unique_integer([:positive])}")

    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf!(dir) end)
    {:ok, contract} = ContractLab.load(Path.expand("../contracts/program-counter.band", __DIR__))
    bindings = %{"requester" => "alice", "counter" => "bot"}

    {:ok, initial} =
      Engine.new(contract,
        session_id: "counter-1",
        bindings: bindings,
        now: 0,
        inactivity: :accepted,
        max_messages: 1000,
        room_policy: :unchecked
      )

    authority = start_supervised!({Agent, fn -> initial end})

    {:ok,
     contract: contract,
     bindings: bindings,
     authority: authority,
     path: Path.join(dir, "program.jsonl")}
  end

  defp start(ctx, overrides \\ []) do
    {:ok, pid} = Replacement.start_link(start_options(ctx, overrides))
    on_exit(fn -> if Process.alive?(pid), do: GenServer.stop(pid) end)
    pid
  end

  defp start_options(ctx, overrides) do
    Keyword.merge(
      [
        path: ctx.path,
        contract: ctx.contract,
        bindings: ctx.bindings,
        role: "counter",
        session_id: "counter-1",
        program: Counter,
        programs: [
          Counter,
          Broken,
          Fixed,
          Extended,
          Divergent,
          VersionTwo,
          Hangs,
          ExternalEffect,
          InvalidOutput
        ],
        snapshot_every: 2,
        max_repairs: 2,
        execution_timeout: 100,
        sidecar_timeout: 1000,
        max_pending: 10,
        controls: ["alice"],
        allowed_actions: []
      ],
      overrides
    )
  end

  defp event(ctx, id, by) do
    Agent.get_and_update(ctx.authority, fn state ->
      msg = %{
        "id" => id,
        "session" => "counter-1",
        "kind" => "increment",
        "payload" => %{"by" => by}
      }

      {d, next} = Engine.step(state, msg, actor: "alice", now: 0)
      assert d.status == :accepted
      {d, next}
    end)
  end

  defp complete(pid, evt), do: assert({:ok, :completed} = Replacement.deliver(pid, evt))

  test "ordinary work calls no sidecar and takes periodic snapshots", ctx do
    parent = self()

    p =
      start(ctx,
        sidecar: fn _ ->
          send(parent, :sidecar_called)
          {:ok, Fixed}
        end
      )

    for n <- 1..5, do: complete(p, event(ctx, "i#{n}", n))
    status = Replacement.status(p)
    assert status.state == %{"total" => 15}
    assert status.completed_inputs == 5
    assert status.sidecar_calls == 0
    assert status.mode == :program
    refute_receive :sidecar_called
    assert {:ok, snap} = Replacement.snapshot(p)
    assert snap["state"]["state"] == %{"total" => 15}
    assert length(Enum.filter(Replacement.journal(p), &(&1["entry"]["type"] == "input"))) == 5
  end

  test "crash wakes a sidecar once, validates a replacement, and resumes the failed input", ctx do
    parent = self()

    p =
      start(ctx,
        program: Broken,
        sidecar: fn bundle ->
          send(parent, {:bundle, bundle})
          {:ok, Fixed}
        end
      )

    complete(p, event(ctx, "one", 1))
    complete(p, event(ctx, "two", 2))
    complete(p, event(ctx, "fault", 13))
    assert_receive {:bundle, bundle}
    assert bundle["fault"]["message"] =~ "13"
    assert bundle["snapshot"]["state"]["state"]["total"] == 3
    assert bundle["contract"]["contract"]["meta"]["doc"] =~ "deterministic counter"
    assert bundle["journal"]["path"] == ctx.path
    status = Replacement.status(p)
    assert status.state["total"] == 16
    assert status.sidecar_calls == 1
    assert status.program == Program.fingerprint(Fixed)
    assert status.pending_inputs == 0
    assert status.completed_inputs == 3
  end

  test "duplicate receipt invokes neither the program nor sidecar and changed identity conflicts",
       ctx do
    p = start(ctx)
    e = event(ctx, "one", 1)
    complete(p, e)
    assert {:ok, :duplicate} = Replacement.deliver(p, e)
    assert {:error, :id_conflict} = Replacement.deliver(p, %{e | payload: %{"by" => 2}})
    assert Replacement.status(p).completed_inputs == 1
    assert Replacement.status(p).state["total"] == 1
    assert length(Replacement.status(p).effects) == 1
  end

  test "raw posts, wrong actors and wrong contract receipts cannot reach a program", ctx do
    p = start(ctx)
    e = event(ctx, "one", 1)
    assert {:error, :invalid_delivery} = Replacement.deliver(p, %{"kind" => "increment"})
    assert {:error, :invalid_delivery} = Replacement.deliver(p, %{e | actor: "intruder"})
    assert {:error, :invalid_delivery} = Replacement.deliver(p, %{e | definition: "other"})
    assert Replacement.status(p).completed_inputs == 0
    assert Replacement.status(p).sidecar_calls == 0
  end

  test "without an agent the failed input is retained and manual repair resumes it", ctx do
    p = start(ctx, program: Broken)
    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "fault", 13))
    assert Replacement.status(p).mode == :needs_agent
    assert Replacement.status(p).pending_inputs == 1
    assert {:ok, :repaired} = Replacement.repair(p, Fixed)
    assert Replacement.status(p).state["total"] == 13
  end

  test "candidate validation replays committed history even when a recent snapshot exists", ctx do
    p = start(ctx, program: Broken, sidecar: fn _ -> {:ok, Divergent} end)
    complete(p, event(ctx, "one", 1))
    complete(p, event(ctx, "two", 2))
    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "fault", 13))
    status = Replacement.status(p)
    assert status.state["total"] == 3
    assert status.sidecar_calls == 2
    assert status.mode == :needs_agent
    assert status.last_fault["message"] =~ "replay"
  end

  test "repair budget and unresolved fault survive a controller restart", ctx do
    options = [program: Broken, sidecar: fn _ -> {:ok, Divergent} end]
    p = start(ctx, options)
    complete(p, event(ctx, "one", 1))
    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "fault", 13))
    GenServer.stop(p)
    p = start(ctx, options)
    assert Replacement.status(p).sidecar_calls == 2
    assert Replacement.status(p).mode == :needs_agent
    assert Replacement.status(p).pending_inputs == 1
  end

  test "an incompatible state version is held for an explicit migration", ctx do
    p = start(ctx, program: Broken, sidecar: fn _ -> {:ok, VersionTwo} end)
    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "fault", 13))
    assert Replacement.status(p).last_fault["message"] =~ "state_version"
    assert Replacement.status(p).state["total"] == 0
  end

  test "hung workers are stopped before a replacement continues", ctx do
    Application.put_env(:contract_lab, :hang_observer, self())
    on_exit(fn -> Application.delete_env(:contract_lab, :hang_observer) end)
    p = start(ctx, program: Hangs, execution_timeout: 20, sidecar: fn _ -> {:ok, Fixed} end)
    complete(p, event(ctx, "fault", 13))
    assert_receive {:hanging_worker, worker}
    refute Process.alive?(worker)
    assert Replacement.status(p).sidecar_calls == 1
    assert Replacement.status(p).state["total"] == 13
  end

  test "unhandled legal input can wake the sidecar to extend the program", ctx do
    p = start(ctx, sidecar: fn _ -> {:ok, Extended} end)

    stop =
      Agent.get_and_update(ctx.authority, fn state ->
        Engine.step(
          state,
          %{"id" => "stop", "session" => "counter-1", "kind" => "stop", "payload" => %{}},
          actor: "alice",
          now: 0
        )
      end)

    complete(p, stop)
    assert Replacement.status(p).sidecar_calls == 1
  end

  test "restarts restore snapshots and replay only their completed tail", ctx do
    p = start(ctx)
    for n <- 1..3, do: complete(p, event(ctx, "i#{n}", n))
    GenServer.stop(p)
    p = start(ctx)
    status = Replacement.status(p)
    assert status.state["total"] == 6
    assert status.completed_inputs == 3
    assert status.replayed_inputs == 1
    assert status.sidecar_calls == 0
    assert length(status.effects) == 3
  end

  test "a damaged snapshot recovers from the intact event journal", ctx do
    p = start(ctx)
    for n <- 1..3, do: complete(p, event(ctx, "i#{n}", n))
    GenServer.stop(p)
    File.write!(ctx.path <> ".snapshot", "torn snapshot")
    p = start(ctx)
    assert Replacement.status(p).state["total"] == 6
    assert Replacement.status(p).replayed_inputs == 3
    assert Replacement.status(p).sidecar_calls == 0
  end

  test "an authorized participant can explicitly activate the sidecar", ctx do
    p = start(ctx, sidecar: fn _ -> {:ok, Fixed} end)
    assert {:error, :unauthorized} = Replacement.activate(p, actor: "intruder", reason: "Fix it")
    assert Replacement.status(p).sidecar_calls == 0

    assert {:ok, :repaired} =
             Replacement.activate(p, actor: "alice", reason: "Check unexpected behavior")

    assert Replacement.status(p).sidecar_calls == 1
  end

  test "validated outputs wait in an outbox and destination receipts prevent resend after restart",
       ctx do
    p = start(ctx)
    complete(p, event(ctx, "one", 1))
    {:ok, counter} = Agent.start_link(fn -> 0 end)
    on_exit(fn -> if Process.alive?(counter), do: Agent.stop(counter) end)

    adapter = fn effect ->
      Agent.update(counter, &(&1 + 1))
      {:ok, %{"key" => effect["key"]}}
    end

    assert {:ok, 1} = Replacement.flush(p, adapter, name: "test-destination", idempotent: false)
    assert {:ok, 0} = Replacement.flush(p, adapter, name: "test-destination", idempotent: false)
    GenServer.stop(p)
    p = start(ctx)
    assert {:ok, 0} = Replacement.flush(p, adapter, name: "test-destination", idempotent: false)
    assert Agent.get(counter, & &1) == 1
  end

  test "uncertain non-idempotent effects are held across restart until reconciled", ctx do
    opts = [program: ExternalEffect, allowed_actions: ["record"]]
    p = start(ctx, opts)
    complete(p, event(ctx, "one", 1))
    {:ok, destination} = Agent.start_link(fn -> 0 end)
    on_exit(fn -> if Process.alive?(destination), do: Agent.stop(destination) end)

    adapter = fn _ ->
      Agent.update(destination, &(&1 + 1))
      raise "Lost receipt after commit"
    end

    assert {:error, :unresolved_effects} =
             Replacement.flush(p, adapter, name: "external", idempotent: false)

    [effect] = Replacement.status(p).effects
    assert effect["status"] == "unknown"
    GenServer.stop(p)
    p = start(ctx, opts)

    assert {:error, :unresolved_effects} =
             Replacement.flush(p, adapter, name: "external", idempotent: false)

    assert Agent.get(destination, & &1) == 1

    assert {:ok, :reconciled} =
             Replacement.reconcile(
               p,
               effect["key"],
               {:applied, %{"receipt" => "verified-by-destination"}}
             )

    assert Replacement.status(p).mode == :program
  end

  test "an idempotent destination receives the same key when a missing receipt is retried", ctx do
    p = start(ctx, program: ExternalEffect, allowed_actions: ["record"])
    complete(p, event(ctx, "one", 1))
    {:ok, destination} = Agent.start_link(fn -> %{} end)
    on_exit(fn -> if Process.alive?(destination), do: Agent.stop(destination) end)

    adapter = fn effect ->
      old =
        Agent.get_and_update(destination, fn seen ->
          {Map.has_key?(seen, effect["key"]), Map.put(seen, effect["key"], effect["intent"])}
        end)

      if old, do: {:ok, %{"receipt" => effect["key"]}}, else: raise("Receipt lost")
    end

    assert {:error, :unresolved_effects} =
             Replacement.flush(p, adapter, name: "idempotent", idempotent: true)

    assert {:ok, 1} = Replacement.flush(p, adapter, name: "idempotent", idempotent: true)
    assert map_size(Agent.get(destination, & &1)) == 1
  end

  test "idempotent retries stop at the attempt cap, across a restart", ctx do
    p = start(ctx, max_effect_attempts: 3)
    complete(p, event(ctx, "one", 1))
    {:ok, calls} = Agent.start_link(fn -> 0 end)
    on_exit(fn -> if Process.alive?(calls), do: Agent.stop(calls) end)

    adapter = fn _ ->
      Agent.update(calls, &(&1 + 1))
      raise "Destination unavailable"
    end

    flush = &Replacement.flush(&1, adapter, name: "idempotent", idempotent: true)
    assert {:error, :unresolved_effects} = flush.(p)
    assert {:error, :unresolved_effects} = flush.(p)
    GenServer.stop(p)
    p = start(ctx, max_effect_attempts: 3)
    assert {:error, :unresolved_effects} = flush.(p)
    assert {:error, :unresolved_effects} = flush.(p)
    assert Agent.get(calls, & &1) == 3
    assert [%{"status" => "unknown", "attempts" => 3}] = Replacement.status(p).effects
    assert Replacement.status(p).mode == :needs_reconciliation
  end

  test "changing an adapter capability does not authorize retry of an unknown effect", ctx do
    p = start(ctx)
    complete(p, event(ctx, "one", 1))
    adapter = fn _ -> raise "unknown" end

    assert {:error, :unresolved_effects} =
             Replacement.flush(p, adapter, name: "unsafe", idempotent: false)

    assert {:error, :adapter_mismatch} =
             Replacement.flush(p, adapter, name: "unsafe", idempotent: true)
  end

  test "a later effect is never published while an earlier one is unresolved", ctx do
    p = start(ctx, program: ExternalEffect, allowed_actions: ["record"])
    complete(p, event(ctx, "one", 1))
    complete(p, event(ctx, "two", 2))
    {:ok, destination} = Agent.start_link(fn -> [] end)
    on_exit(fn -> if Process.alive?(destination), do: Agent.stop(destination) end)

    # The first publication of total 1 loses its receipt; later attempts succeed.
    adapter = fn effect ->
      value = effect["intent"]["payload"]["value"]
      seen = Agent.get_and_update(destination, &{&1, &1 ++ [value]})
      if value == 1 and 1 not in seen, do: raise("Lost receipt"), else: {:ok, %{"v" => value}}
    end

    values = fn p ->
      Enum.map(Replacement.status(p).effects, &{&1["intent"]["payload"]["value"], &1["status"]})
    end

    assert {:error, :unresolved_effects} =
             Replacement.flush(p, adapter, name: "external", idempotent: false)

    assert Agent.get(destination, & &1) == [1]
    assert values.(p) == [{1, "unknown"}, {3, "pending"}]
    [first, _] = Replacement.status(p).effects

    assert {:ok, :reconciled} =
             Replacement.reconcile(p, first["key"], {:not_applied, %{"checked" => "destination"}})

    assert {:ok, 2} = Replacement.flush(p, adapter, name: "external", idempotent: false)
    # The destination ends on the latest total, never on a stale earlier one.
    assert Agent.get(destination, & &1) == [1, 1, 3]
    assert values.(p) == [{1, "done"}, {3, "done"}]
  end

  test "invalid program outputs cannot enter the outbox", ctx do
    p = start(ctx, program: InvalidOutput)
    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "one", 1))
    assert Replacement.status(p).effects == []
    assert Replacement.status(p).completed_inputs == 0
    assert Replacement.status(p).mode == :needs_agent
  end

  test "program effects require an explicit host action grant", ctx do
    p = start(ctx, program: ExternalEffect)
    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "one", 1))
    assert Replacement.status(p).effects == []
    assert Replacement.status(p).last_fault["message"] =~ "effect"
  end

  test "the pending-input queue is bounded while waiting for an agent", ctx do
    p = start(ctx, program: Broken, max_pending: 1)
    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "fault", 13))
    assert {:error, :pending_limit} = Replacement.deliver(p, event(ctx, "next", 1))
  end

  @tag :recovery_guards
  test "delivery needs both an attributed actor and its bound role", ctx do
    p = start(ctx)
    e = event(ctx, "one", 1)
    assert {:error, :invalid_delivery} = Replacement.deliver(p, Map.drop(e, [:actor, :role]))
    assert Replacement.status(p).completed_inputs == 0
  end

  @tag :recovery_guards
  test "a broken candidate metadata callback cannot crash the controller", ctx do
    p =
      start(ctx,
        program: Broken,
        sidecar: fn _ -> {:ok, ContractLab.TestPrograms.CrashingVersion} end
      )

    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "fault", 13))
    assert Process.alive?(p)
    assert Replacement.status(p).mode == :needs_agent
  end

  @tag :recovery_guards
  test "repair cannot silently change the initial state before the first completion", ctx do
    p =
      start(ctx,
        program: Broken,
        sidecar: fn _ -> {:ok, ContractLab.TestPrograms.ChangedInitial} end
      )

    assert {:ok, :queued} = Replacement.deliver(p, event(ctx, "fault", 13))
    assert Replacement.status(p).state["total"] == 0
    assert Replacement.status(p).mode == :needs_agent
  end

  @tag :recovery_guards
  test "a full journal cannot make a blocked outbox look flushed", ctx do
    p = start(ctx)
    complete(p, event(ctx, "one", 1))
    GenServer.stop(p)
    limit = File.stat!(ctx.path).size + 10
    p = start(ctx, journal_max_bytes: limit)

    assert {:error, :outbox_blocked} =
             Replacement.flush(p, fn _ -> {:ok, %{}} end, name: "blocked", idempotent: false)

    assert hd(Replacement.status(p).effects)["status"] == "pending"
  end

  test "controller death stops its worker and leaves an uncertain started effect for reconciliation",
       ctx do
    opts = [program: ExternalEffect, allowed_actions: ["record"], execution_timeout: 5000]
    p = start(ctx, opts)
    complete(p, event(ctx, "one", 1))
    parent = self()

    adapter = fn _ ->
      send(parent, {:dispatch_worker, self()})

      receive do
        :never -> {:ok, %{}}
      end
    end

    spawn(fn ->
      try do
        Replacement.flush(p, adapter, name: "crash-window", idempotent: false)
      catch
        :exit, _ -> :ok
      end
    end)

    assert_receive {:dispatch_worker, worker}
    worker_monitor = Process.monitor(worker)
    journal = :global.whereis_name({Journal, Path.expand(ctx.path)})
    journal_monitor = Process.monitor(journal)
    Process.exit(p, :kill)
    assert_receive {:DOWN, ^worker_monitor, :process, ^worker, _}, 1000
    assert_receive {:DOWN, ^journal_monitor, :process, ^journal, _}, 1000
    p = start(ctx, opts)
    assert Replacement.status(p).state["total"] == 1
    assert hd(Replacement.status(p).effects)["status"] == "unknown"
    assert Replacement.status(p).mode == :needs_reconciliation
  end

  # Another OS process probing `<path>.owner` without waiting.
  defp lock_free?(path) do
    port =
      Port.open({:spawn_executable, System.find_executable("flock")}, [
        :binary,
        :exit_status,
        args: ["-n", "-E", "75", path <> ".owner", "-c", "true"]
      ])

    receive do
      {^port, {:exit_status, 0}} -> true
      {^port, {:exit_status, 75}} -> false
    after
      5_000 -> flunk("flock did not answer")
    end
  end

  # Another OS process holding `<path>.owner` until it is told to release it.
  defp hold_owner(path) do
    port =
      Port.open({:spawn_executable, System.find_executable("flock")}, [
        :binary,
        :exit_status,
        args: ["-n", "-E", "75", path <> ".owner", "-c", "echo held; read -r release"]
      ])

    receive do
      {^port, {:data, "held\n"}} -> {:ok, port}
      {^port, {:exit_status, 75}} -> {:error, :busy}
    after
      5_000 -> flunk("flock did not answer")
    end
  end

  defp release_held(port) do
    Port.command(port, "release\n")
    assert_receive {^port, {:exit_status, _}}, 5_000
  end

  # Drives a failing start through states that persist until the test acts, so
  # no step races the Replacement or needs a retry:
  # 1. an outside holder keeps the lock, so the journal's flock -w child waits
  #    and the journal sits in take_owner; it is suspended there;
  # 2. the holder lets go; the child takes the lock and its "owner" line queues
  #    in the suspended journal; the child is then stopped, holding the lock;
  # 3. the journal resumes and the Replacement's boot fails. The journal must
  #    still be alive, stopped by the start and waiting in release_owner for the
  #    stopped child, not killed with the lock left held.
  # `stall` stands in for the test process being descheduled after it finds the
  # journal, as in a busy full run; no step depends on timing.
  defp failing_start_with_paused_owner(opts, stall) do
    {:ok, holder} = hold_owner(opts[:path])

    task =
      Task.async(fn ->
        Process.flag(:trap_exit, true)
        Replacement.start_link(Keyword.put(opts, :journal_owner_release_ms, 60_000))
      end)

    name = {Journal, Path.expand(opts[:path])}
    journal = await_state(fn -> pid?(:global.whereis_name(name)) end)
    Process.sleep(stall)
    port = await_state(fn -> waiting_for_lock(journal) end)
    true = :erlang.suspend_process(journal)

    # Resumed on every path, so a failed step never leaves the journal suspended.
    os_pid =
      try do
        release_held(holder)
        await_state(fn -> owner_line?(journal, port) end)
        {:os_pid, os_pid} = Port.info(port, :os_pid)
        os_pid = Integer.to_string(os_pid)
        assert {_, 0} = System.cmd("kill", ["-STOP", os_pid])
        on_exit(fn -> System.cmd("kill", ["-CONT", os_pid], stderr_to_stdout: true) end)
        os_pid
      after
        resume(journal)
      end

    state = await_state(fn -> settled(journal) end)
    assert {_, 0} = System.cmd("kill", ["-CONT", os_pid])
    {state, Task.await(task, 30_000)}
  end

  # Polls for a state that, once reached, holds until the test acts. The
  # deadline only turns a broken invariant into a clear failure.
  defp await_state(fun, deadline \\ System.monotonic_time(:millisecond) + 30_000) do
    cond do
      value = fun.() ->
        value

      System.monotonic_time(:millisecond) > deadline ->
        flunk("state never reached")

      # Sleep between polls: no busy spin on the shared host.
      true ->
        Process.sleep(10)
        await_state(fun, deadline)
    end
  end

  defp pid?(pid) when is_pid(pid), do: pid
  defp pid?(_), do: nil

  defp resume(pid) do
    :erlang.resume_process(pid)
  rescue
    ArgumentError -> :ok
  end

  defp port_of(pid),
    do: Enum.find(Port.list(), &(Port.info(&1, :connected) == {:connected, pid}))

  defp waiting_for_lock(journal) do
    with {:current_function, {Journal, :take_owner, _arity}} <-
           Process.info(journal, :current_function),
         port when is_port(port) <- port_of(journal),
         do: port,
         else: (_ -> nil)
  end

  defp owner_line?(journal, port) do
    {:messages, messages} = Process.info(journal, :messages)
    Enum.any?(messages, &match?({^port, {:data, "owner\n"}}, &1))
  end

  defp settled(journal) do
    case Process.info(journal, :current_function) do
      nil -> :gone
      {:current_function, {Journal, :release_owner, _arity}} -> :releasing
      _ -> nil
    end
  end

  test "a failed start stops its journal and frees the lock before returning", ctx do
    p = start(ctx)
    GenServer.stop(p)
    opts = start_options(ctx, max_pending: 11)

    assert {:releasing, {:error, :configuration_mismatch}} =
             failing_start_with_paused_owner(opts, 100)

    assert lock_free?(ctx.path)
  end

  defp effect_key(key, input, index) do
    message =
      Canonical.encode(["contract-lab/effect-key/1", "counter-1", "counter", input, index])

    :crypto.mac(:hmac, :sha256, key, message) |> Base.encode16(case: :lower)
  end

  test "outbox keys are HMAC-SHA256 under a private per-host key that survives restarts", ctx do
    opts = [program: ExternalEffect, allowed_actions: ["record"]]
    p = start(ctx, opts)
    complete(p, event(ctx, "one", 1))
    [effect] = Replacement.status(p).effects

    public =
      Canonical.digest(%{
        "session" => "counter-1",
        "role" => "counter",
        "input" => effect["input"],
        "index" => 0
      })

    refute effect["key"] == public, "M26: a co-recipient can predict the outbox key"
    key_path = ctx.path <> ".host-key"
    assert {:ok, %{type: :regular, size: 32, mode: mode}} = File.lstat(key_path)
    assert Bitwise.band(mode, 0o777) == 0o600
    key = File.read!(key_path)
    assert effect["key"] == effect_key(key, effect["input"], 0)
    journal = File.read!(ctx.path)
    assert :binary.match(journal, key) == :nomatch
    assert :binary.match(journal, Base.encode16(key, case: :lower)) == :nomatch

    GenServer.stop(p)
    p = start(ctx, opts)
    assert File.read!(key_path) == key
    assert hd(Replacement.status(p).effects)["key"] == effect["key"]
    complete(p, event(ctx, "two", 2))
    second = Enum.find(Replacement.status(p).effects, &(&1["input"] != effect["input"]))
    assert second["key"] == effect_key(key, second["input"], 0)
  end

  # O1 (set O review): the host key is committed in the journal by id. A key that is missing
  # while effects exist, or a different key, refuses the start instead of being replaced.
  defp host_key_records(path) do
    {:ok, j} = Journal.start_link(path: path)
    records = Journal.read(j)
    GenServer.stop(j)
    for %{"entry" => %{"type" => "host_key"} = e} <- records, do: e
  end

  test "a missing host key with journaled effects refuses the start and creates no key", ctx do
    # Two completions are covered by the snapshot; a third leaves a replayed tail.
    for {inputs, n} <- [{2, 1}, {3, 2}] do
      path = Path.join(Path.dirname(ctx.path), "missing-#{n}.jsonl")
      key_path = path <> ".host-key"
      p = start(ctx, path: path)
      for i <- 1..inputs, do: complete(p, event(ctx, "k#{n}-#{i}", 1))
      GenServer.stop(p)
      File.rm!(key_path)

      assert {:error, :host_key_missing} = Replacement.start_link(start_options(ctx, path: path))
      refute File.exists?(key_path)
    end
  end

  test "a different host key refuses the start with host_key_mismatch", ctx do
    key_path = ctx.path <> ".host-key"
    p = start(ctx)
    complete(p, event(ctx, "one", 1))
    GenServer.stop(p)
    File.write!(key_path, :crypto.strong_rand_bytes(32))
    File.chmod!(key_path, 0o600)

    assert {:error, :host_key_mismatch} = Replacement.start_link(start_options(ctx, []))
  end

  test "a host key is created only with a journal record that commits to its id", ctx do
    key_path = ctx.path <> ".host-key"
    p = start(ctx)
    GenServer.stop(p)
    key = File.read!(key_path)
    id = :crypto.mac(:hmac, :sha256, key, Canonical.encode(["contract-lab/host-key-id/1"]))
    assert [%{"id" => first}] = host_key_records(ctx.path)
    assert first == binary_part(Base.encode16(id, case: :lower), 0, 16)

    # No effects yet: a lost key may be recreated, and the new id is journaled, not silent.
    File.rm!(key_path)
    p = start(ctx)
    GenServer.stop(p)
    assert [%{"id" => ^first}, %{"id" => second}] = host_key_records(ctx.path)
    refute second == first
  end

  test "replay reuses the journaled effect keys and compares only intents", ctx do
    p = start(ctx)
    complete(p, event(ctx, "one", 1))
    [%{"key" => original}] = Replacement.status(p).effects
    records = Replacement.journal(p)
    GenServer.stop(p)

    # Re-journal the same history with keys this host would never compute, as a journal
    # written before the HMAC keys (or under a rotated key) has.
    moved = Path.join(Path.dirname(ctx.path), "moved.jsonl")
    {:ok, j} = Journal.start_link(path: moved)

    for %{"entry" => entry} <- records do
      entry =
        if entry["type"] == "completed",
          do: %{entry | "effects" => Enum.map(entry["effects"], &%{&1 | "key" => "legacy-0"})},
          else: entry

      {:ok, _} = Journal.append(j, entry)
    end

    GenServer.stop(j)
    File.cp!(ctx.path <> ".host-key", moved <> ".host-key")
    File.chmod!(moved <> ".host-key", 0o600)

    p = start(ctx, path: moved)
    refute original == "legacy-0"
    assert [%{"key" => "legacy-0"}] = Replacement.status(p).effects
    assert {:ok, :repaired} = Replacement.repair(p, Fixed)
    assert [%{"key" => "legacy-0"}] = Replacement.status(p).effects
  end

  # Set P impl review P-1: a failed first init leaves [configuration, host_key] behind, and
  # the next start must still create the initial record instead of refusing forever.
  test "a first start whose init fails can be retried (P-1)", ctx do
    alias ContractLab.ReplacementTest.FlakyInit
    opts = start_options(ctx, program: FlakyInit, programs: [FlakyInit])
    :persistent_term.put({FlakyInit, :fail}, true)
    on_exit(fn -> :persistent_term.erase({FlakyInit, :fail}) end)
    assert {:error, _} = Replacement.start_link(opts)
    :persistent_term.put({FlakyInit, :fail}, false)

    assert {:ok, p} = Replacement.start_link(opts)
    complete(p, event(ctx, "after-retry", 1))
    types = Enum.map(Replacement.journal(p), & &1["entry"]["type"])
    assert Enum.take(types, 3) == ["configuration", "host_key", "initial"]
    GenServer.stop(p)
  end

  # P-2: neither a crash report nor any formatted view of the state may carry the key.
  defp key_needle(ctx) do
    File.read!(ctx.path <> ".host-key")
    |> :binary.bin_to_list()
    |> Enum.take(12)
    |> Enum.join(",")
  end

  defp flat(text), do: String.replace(IO.chardata_to_string(text), ~r/\s+/, "")

  # Set Q review Q-L16: an unknown call or cast leaves the participant running; neither
  # produces a crash.
  test "an unknown call or cast is refused without stopping the participant (Q-L16)", ctx do
    p = start(ctx)

    log =
      ExUnit.CaptureLog.capture_log(fn ->
        assert {:error, :unknown_call} = GenServer.call(p, :bogus)
        GenServer.cast(p, :bogus)
        assert %{completed_inputs: 0} = Replacement.status(p)
      end)

    refute log =~ "terminating"
    assert Process.alive?(p)
  end

  # Q-L16: a real crash report. An abnormal stop makes the GenServer report print the
  # participant's state, where the key appears only as :redacted.
  test "a crash report prints the state with the host key redacted (P-2, Q-L16)", ctx do
    p = start(ctx)
    needle = key_needle(ctx)
    log = ExUnit.CaptureLog.capture_log(fn -> GenServer.stop(p, :boom) end)
    assert log =~ "terminating"
    # Erlang's report format, or Elixir's when Logger translates it.
    assert flat(log) =~ ~r/host_key(=>|::)redacted/
    refute String.contains?(flat(log), needle), "P-2: the host key is in a crash report"
  end

  test "the state holds the host key only behind a closure (P-2)", ctx do
    p = start(ctx)
    needle = key_needle(ctx)
    state = :sys.get_state(p)

    for text <- [
          inspect(state, limit: :infinity, printable_limit: :infinity, binaries: :as_binaries),
          :io_lib.format(~c"~tp", [state])
        ] do
      refute String.contains?(flat(text), needle), "P-2: a formatted state shows the host key"
    end

    # The key still derives the same effect keys.
    complete(p, event(ctx, "closure", 1))
    [effect] = Replacement.status(p).effects
    key = File.read!(ctx.path <> ".host-key")
    assert effect["key"] == effect_key(key, "closure", 0)
  end

  # P-6: a first key linked into place whose journal append was lost to a crash is
  # committed at the next start, since no key id was journaled yet.
  test "a first key whose id was never journaled is committed at the next start (P-6)", ctx do
    key_path = ctx.path <> ".host-key"
    p = start(ctx)
    GenServer.stop(p)
    strip_host_key_records(ctx.path)
    File.rm!(key_path)
    {:ok, key} = ContractLab.HostKey.create(key_path)

    assert {:ok, p} = Replacement.start_link(start_options(ctx, []))
    GenServer.stop(p)
    assert [%{"id" => id}] = host_key_records(ctx.path)
    assert id == ContractLab.HostKey.id(key)
  end

  # Set Q review Q-L14 (Ada): once a key id is journaled, a different key file refuses the
  # start even with no effects; an operator's adopt commits it deliberately. A crash between
  # linking the replacement for a lost key and journaling its id also lands here.
  test "a different key refuses the start once an id is journaled, even without effects (Q-L14)",
       ctx do
    key_path = ctx.path <> ".host-key"
    p = start(ctx)
    GenServer.stop(p)
    assert [%{"id" => first}] = host_key_records(ctx.path)
    File.rm!(key_path)
    {:ok, key} = ContractLab.HostKey.create(key_path)

    assert {:error, :host_key_mismatch} = Replacement.start_link(start_options(ctx, []))
    assert [%{"id" => ^first}] = host_key_records(ctx.path)
    assert {:ok, %{"action" => "adopted", "id" => second}} = Replacement.adopt_host_key(ctx.path)
    assert second == ContractLab.HostKey.id(key)
    assert {:ok, p} = Replacement.start_link(start_options(ctx, []))
    GenServer.stop(p)
  end

  # P-7: an operator op commits a journal to a new or present key: a pre-O1 journal with
  # effects and no key, or a lost key. Journaled effects keep their keys.
  defp strip_host_key_records(path) do
    {:ok, j} = Journal.start_link(path: path)
    records = Journal.read(j)
    GenServer.stop(j)
    File.rm!(path)
    {:ok, j} = Journal.start_link(path: path)

    for %{"entry" => entry} <- records,
        entry["type"] != "host_key",
        do: {:ok, _} = Journal.append(j, entry)

    GenServer.stop(j)
  end

  test "an operator adopts a new key for a pre-O1 journal with effects (P-7)", ctx do
    key_path = ctx.path <> ".host-key"
    p = start(ctx)
    complete(p, event(ctx, "old", 1))
    [%{"key" => old_key}] = Replacement.status(p).effects
    GenServer.stop(p)
    strip_host_key_records(ctx.path)
    File.rm!(key_path)

    assert {:error, :host_key_missing} = Replacement.start_link(start_options(ctx, []))
    assert {:ok, %{"action" => "created", "id" => id}} = Replacement.adopt_host_key(ctx.path)
    key = File.read!(key_path)
    assert id == ContractLab.HostKey.id(key)
    assert [%{"id" => ^id}] = host_key_records(ctx.path)

    p = start(ctx)
    assert [%{"key" => ^old_key}] = Replacement.status(p).effects
    complete(p, event(ctx, "new", 1))
    new = Enum.find(Replacement.status(p).effects, &(&1["input"] == "new"))
    assert new["key"] == effect_key(key, "new", 0)
  end

  test "an operator rotates a lost key with journaled effects, idempotently (P-7)", ctx do
    key_path = ctx.path <> ".host-key"
    p = start(ctx)
    complete(p, event(ctx, "old", 1))
    [%{"key" => old_key}] = Replacement.status(p).effects
    GenServer.stop(p)
    File.rm!(key_path)
    assert [%{"id" => first}] = host_key_records(ctx.path)

    assert {:ok, %{"action" => "created", "id" => second}} =
             Replacement.adopt_host_key(ctx.path)

    assert {:ok, %{"action" => "unchanged", "id" => ^second}} =
             Replacement.adopt_host_key(ctx.path)

    refute second == first
    assert [%{"id" => ^first}, %{"id" => ^second}] = host_key_records(ctx.path)
    p = start(ctx)
    assert [%{"key" => ^old_key}] = Replacement.status(p).effects
  end

  test "an operator commits a present but uncommitted key, never while running (P-7)", ctx do
    key_path = ctx.path <> ".host-key"
    p = start(ctx)
    complete(p, event(ctx, "old", 1))

    assert {:error, :owner_conflict} =
             Replacement.adopt_host_key(ctx.path, owner_release_ms: 100)

    GenServer.stop(p)
    File.rm!(key_path)
    {:ok, key} = ContractLab.HostKey.create(key_path)
    assert {:error, :host_key_mismatch} = Replacement.start_link(start_options(ctx, []))

    assert {:ok, %{"action" => "adopted", "id" => id}} = Replacement.adopt_host_key(ctx.path)
    assert id == ContractLab.HostKey.id(key)
    assert {:ok, p} = Replacement.start_link(start_options(ctx, []))
    GenServer.stop(p)

    missing = Path.join(Path.dirname(ctx.path), "never-started.jsonl")
    assert {:error, :enoent} = Replacement.adopt_host_key(missing)
    refute File.exists?(missing)
  end

  # Set Q review Q-L1 (a): a host without coreutils sync refuses at boot with a clear
  # error, instead of failing every key creation as invalid_host_key.
  test "a host without an absolute sync command refuses to boot", ctx do
    missing = Path.join(Path.dirname(ctx.path), "no-such-sync")
    Application.put_env(:contract_lab, :sync_command, missing)
    on_exit(fn -> Application.delete_env(:contract_lab, :sync_command) end)
    assert {:error, :sync_unavailable} = Replacement.start_link(start_options(ctx, []))
  end

  # A stand-in for sync(1), as in host_key_test.exs.
  defp sync_stand_in(ctx, body) do
    script = Path.join(Path.dirname(ctx.path), "sync-stand-in")
    File.write!(script, "#!/bin/sh\n" <> body)
    File.chmod!(script, 0o755)
    Application.put_env(:contract_lab, :sync_command, script)
    on_exit(fn -> Application.delete_env(:contract_lab, :sync_command) end)
  end

  defp await_file(path, tries \\ 500) do
    cond do
      File.exists?(path) ->
        :ok

      tries == 0 ->
        flunk("#{path} never appeared")

      true ->
        Process.sleep(10)
        await_file(path, tries - 1)
    end
  end

  # Set Q review Q-L11: adopt runs apart from its caller. Creating a key runs sync(1) through
  # a port, and the caller's links stay as they were: no port EXIT is left in its mailbox,
  # its trap flag is unchanged, and an abnormal exit from another link still stops it.
  test "adopt leaves its caller's trap flag, links and mailbox alone (Q-L11)", ctx do
    key_path = ctx.path <> ".host-key"
    p = start(ctx)
    GenServer.stop(p)
    File.rm!(key_path)
    parent = self()

    spawn(fn ->
      result = Replacement.adopt_host_key(ctx.path)
      send(parent, {:adopted, result, Process.info(self(), [:trap_exit, :messages])})
    end)

    assert_receive {:adopted, {:ok, %{"action" => "created"}}, info}, 5_000
    assert info == [trap_exit: false, messages: []]

    # A linked process dies while adopt waits on sync(1): the caller, which does not trap
    # exits, stops with it. The adoption still completes and releases the journal.
    File.rm!(key_path)
    marker = Path.join(Path.dirname(ctx.path), "sync-started")
    sync_stand_in(ctx, "touch '#{marker}'\nsleep 1\nexec sync \"$@\"\n")

    {caller, ref} =
      spawn_monitor(fn ->
        send(parent, {:partner, spawn_link(fn -> Process.sleep(:infinity) end)})
        Replacement.adopt_host_key(ctx.path)
      end)

    assert_receive {:partner, partner}
    await_file(marker)
    journal = :global.whereis_name({Journal, Path.expand(ctx.path)})
    assert is_pid(journal)
    watch = Process.monitor(journal)
    Process.exit(partner, :boom)
    assert_receive {:DOWN, ^ref, :process, ^caller, :boom}, 5_000
    assert_receive {:DOWN, ^watch, :process, ^journal, _}, 5_000
    assert [_, _, %{"id" => id}] = host_key_records(ctx.path)
    assert id == ContractLab.HostKey.id(File.read!(key_path))
  end

  # Set Q review Q-L12: a journal commits to a loaded key only once sync(1) has made the
  # key and its directory entry durable, so a failed sync fails every start and adopt until
  # it succeeds, not only the start that created the key.
  test "a key whose sync failed is committed by no later start or adopt (Q-L12)", ctx do
    key_path = ctx.path <> ".host-key"
    log = Path.join(Path.dirname(ctx.path), "sync.log")
    sync_stand_in(ctx, "exit 1\n")
    assert {:error, :invalid_host_key} = Replacement.start_link(start_options(ctx, []))
    assert File.exists?(key_path)
    assert {:error, :invalid_host_key} = Replacement.start_link(start_options(ctx, []))
    assert {:error, :invalid_host_key} = Replacement.adopt_host_key(ctx.path)
    assert [] = host_key_records(ctx.path)

    sync_stand_in(ctx, "printf '%s\\n' \"$*\" >> '#{log}'\nexec sync \"$@\"\n")
    p = start(ctx)
    GenServer.stop(p)
    assert File.read!(log) == "-- #{key_path} #{Path.dirname(key_path)}\n"
    assert [%{"id" => id}] = host_key_records(ctx.path)
    assert id == ContractLab.HostKey.id(File.read!(key_path))
  end

  # Set Q review Q-L13: adopt opens the journal under the participant's journal_max_bytes,
  # so a participant configured above the 64 MiB default can still adopt its key.
  test "adopt opens the journal under the participant's journal_max_bytes (Q-L13)", ctx do
    p = start(ctx)
    complete(p, event(ctx, "one", 1))
    GenServer.stop(p)
    File.rm!(ctx.path <> ".host-key")
    size = File.stat!(ctx.path).size

    assert {:error, :journal_limit} =
             Replacement.adopt_host_key(ctx.path, journal_max_bytes: size - 1)

    assert_raise Mix.Error, ~r/journal_limit/, fn ->
      Mix.Tasks.Contract.HostKey.run(["adopt", ctx.path, "--journal-max-bytes", "#{size - 1}"])
    end

    assert {:ok, %{"action" => "created"}} =
             Replacement.adopt_host_key(ctx.path, journal_max_bytes: size + 1024)
  end
end
