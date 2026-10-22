defmodule ContractLab.JournalTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Journal, Canonical}

  @moduletag capture_log: true

  setup do
    Process.flag(:trap_exit, true)
    dir = Path.join(System.tmp_dir!(), "contract-journal-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf!(dir) end)
    {:ok, path: Path.join(dir, "events.jsonl")}
  end

  defp open(path, opts \\ []) do
    {:ok, pid} = Journal.start_link(Keyword.put(opts, :path, path))

    on_exit(fn ->
      if Process.alive?(pid), do: GenServer.stop(pid)
    end)

    pid
  end

  defp input(id), do: %{"type" => "input", "id" => id, "payload" => %{"by" => 1}}

  test "append is durable, sequenced and hash chained across restarts", %{path: path} do
    j = open(path)
    assert {:ok, first} = Journal.append(j, input("one"))
    assert {:ok, second} = Journal.append(j, input("two"))
    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert second["previous"] == first["hash"]
    assert second["hash"] == Canonical.digest(Map.delete(second, "hash"))
    assert File.read!(path) =~ "one"
    GenServer.stop(j)
    j = open(path)
    assert Journal.read(j) == [first, second]
    assert {:ok, third} = Journal.append(j, input("three"))
    assert third["sequence"] == 3
    assert third["previous"] == second["hash"]
    GenServer.stop(j)
  end

  test "normal stop waits for a scheduler-delayed OS owner before returning", %{path: path} do
    j = open(path)
    {:os_pid, owner_pid} = Port.info(:sys.get_state(j).owner, :os_pid)
    owner_pid = Integer.to_string(owner_pid)
    assert {_, 0} = System.cmd("kill", ["-STOP", owner_pid])

    # Delay the real flock process, not a fake lock or a replacement retry.
    # Always resume it even if the assertion below fails.
    on_exit(fn -> System.cmd("kill", ["-CONT", owner_pid], stderr_to_stdout: true) end)

    spawn(fn ->
      Process.sleep(100)
      System.cmd("kill", ["-CONT", owner_pid], stderr_to_stdout: true)
    end)

    GenServer.stop(j)
    next = open(path)
    assert Journal.read(next) == []
    GenServer.stop(next)
  end

  test "normal stop reports failure if the OS owner does not acknowledge release", %{path: path} do
    j = open(path)
    {:os_pid, owner_pid} = Port.info(:sys.get_state(j).owner, :os_pid)
    owner_pid = Integer.to_string(owner_pid)
    assert {_, 0} = System.cmd("kill", ["-STOP", owner_pid])
    on_exit(fn -> System.cmd("kill", ["-CONT", owner_pid], stderr_to_stdout: true) end)

    spawn(fn ->
      Process.sleep(6_000)
      System.cmd("kill", ["-CONT", owner_pid], stderr_to_stdout: true)
    end)

    reason = catch_exit(GenServer.stop(j, :normal, 8_000))
    assert inspect(reason) =~ "owner_release_timeout"
  end

  test "an unavailable owner port is not a successful release acknowledgement", %{path: path} do
    j = open(path)
    Port.close(:sys.get_state(j).owner)
    reason = catch_exit(GenServer.stop(j))
    assert inspect(reason) =~ "owner_lost"
  end

  test "an incomplete final append is discarded after verifying the completed prefix", %{
    path: path
  } do
    j = open(path)
    {:ok, first} = Journal.append(j, input("one"))
    GenServer.stop(j)
    prefix = File.read!(path)
    File.write!(path, "{\"sequence\":2,", [:append])
    j = open(path)
    assert Journal.read(j) == [first]
    assert Journal.info(j).recovered_tail_bytes > 0
    assert File.read!(path) == prefix
    assert {:ok, second} = Journal.append(j, input("two"))
    assert second["sequence"] == 2
    GenServer.stop(j)
  end

  test "corrupt completed records fail closed and remain available for diagnosis", %{path: path} do
    j = open(path)
    {:ok, _} = Journal.append(j, input("one"))
    GenServer.stop(j)
    corrupted = File.read!(path) |> String.replace("one", "two")
    File.write!(path, corrupted)
    assert {:error, :corrupt_journal} = Journal.start_link(path: path)
    assert File.read!(path) == corrupted
  end

  test "reordered and removed middle records fail chain verification", %{path: path} do
    j = open(path)
    for n <- 1..3, do: Journal.append(j, input(Integer.to_string(n)))
    GenServer.stop(j)
    [a, b, c] = String.split(File.read!(path), "\n", trim: true)
    File.write!(path, Enum.join([a, c, b], "\n") <> "\n")
    assert {:error, :corrupt_journal} = Journal.start_link(path: path)
    File.write!(path, Enum.join([a, c], "\n") <> "\n")
    assert {:error, :corrupt_journal} = Journal.start_link(path: path)
  end

  test "snapshots bind state to an exact journal sequence and hash", %{path: path} do
    j = open(path)
    {:ok, first} = Journal.append(j, input("one"))
    assert {:ok, snapshot} = Journal.snapshot(j, %{"total" => 1, "version" => 1})
    assert snapshot["sequence"] == 1
    assert snapshot["head"] == first["hash"]
    {:ok, _} = Journal.append(j, input("two"))
    GenServer.stop(j)
    j = open(path)
    assert {:ok, ^snapshot} = Journal.latest_snapshot(j)
    assert snapshot["state"]["total"] == 1
    assert length(Journal.read(j)) == 2
    GenServer.stop(j)
  end

  test "snapshot corruption is reported separately from an intact journal", %{path: path} do
    j = open(path)
    {:ok, _} = Journal.append(j, input("one"))
    {:ok, _} = Journal.snapshot(j, %{"total" => 1})
    File.write!(path <> ".snapshot", "bad data")
    assert {:error, :corrupt_snapshot} = Journal.latest_snapshot(j)
    assert length(Journal.read(j)) == 1
    GenServer.stop(j)
  end

  test "snapshot cannot refer past the surviving journal", %{path: path} do
    j = open(path)
    {:ok, _} = Journal.append(j, input("one"))
    {:ok, _} = Journal.snapshot(j, %{"total" => 1})
    GenServer.stop(j)
    File.write!(path, "")
    j = open(path)
    assert {:error, :snapshot_position} = Journal.latest_snapshot(j)
    GenServer.stop(j)
  end

  test "a missing snapshot allows replay from the beginning", %{path: path} do
    j = open(path)
    assert {:error, :no_snapshot} = Journal.latest_snapshot(j)
    assert Journal.read(j) == []
    GenServer.stop(j)
  end

  test "only one journal owner can open a path in this BEAM", %{path: path} do
    j = open(path)
    assert {:error, {:already_started, ^j}} = Journal.start_link(path: path)

    assert {:error, {:already_started, ^j}} =
             Journal.start_link(path: Path.join(Path.dirname(path), "./events.jsonl"))

    GenServer.stop(j)
    next = open(path)
    GenServer.stop(next)
  end

  test "journal capacity is enforced before writing a partial entry", %{path: path} do
    j = open(path, max_bytes: 500)
    {:ok, _} = Journal.append(j, input("one"))
    before = File.read!(path)
    assert {:error, :journal_limit} = Journal.append(j, %{"large" => String.duplicate("x", 500)})
    assert File.read!(path) == before
    assert length(Journal.read(j)) == 1
    GenServer.stop(j)
  end

  test "snapshot capacity failure preserves the previous snapshot", %{path: path} do
    j = open(path, max_snapshot_bytes: 500)
    {:ok, first} = Journal.snapshot(j, %{"total" => 1})

    assert {:error, :snapshot_limit} =
             Journal.snapshot(j, %{"large" => String.duplicate("x", 500)})

    assert {:ok, ^first} = Journal.latest_snapshot(j)
    GenServer.stop(j)
  end

  test "opaque runtime objects and non-JSON state are not journal data", %{path: path} do
    j = open(path)
    assert {:error, :invalid_entry} = Journal.append(j, %{"pid" => self()})
    assert {:error, :invalid_entry} = Journal.append(j, %{"fun" => fn -> :no end})
    assert Journal.read(j) == []
    GenServer.stop(j)
  end

  test "symlink aliases cannot obtain a second owner", %{path: path} do
    j = open(path)
    linked = Path.join(Path.dirname(path), "alias.jsonl")
    File.ln_s!(path, linked)
    assert {:error, :symlink_path} = Journal.start_link(path: linked)
    GenServer.stop(j)
  end

  test "reopening verifies the chain in linear work", %{path: path} do
    # Write a valid chain directly (no per-record fsync), then compare the
    # owner's reductions for opening 4,000 and 16,000 records. Linear
    # verification costs about 4x; appending to the end of a list per record
    # cost 6x or more and minutes near the 64 MiB cap.
    open_work = fn count ->
      {lines, _} =
        Enum.map_reduce(1..count, String.duplicate("0", 64), fn sequence, previous ->
          core = %{
            "sequence" => sequence,
            "previous" => previous,
            "entry" => input("e#{sequence}")
          }

          record = Map.put(core, "hash", Canonical.digest(core))
          {Canonical.encode(record) <> "\n", record["hash"]}
        end)

      file = "#{path}.#{count}"
      File.write!(file, lines)
      j = open(file)
      {:reductions, work} = Process.info(j, :reductions)
      assert %{sequence: ^count} = Journal.info(j)
      assert {:ok, %{"sequence" => next}} = Journal.append(j, input("next"))
      assert next == count + 1
      assert [%{"sequence" => 1} | _] = records = Journal.read(j)
      assert length(records) == count + 1
      GenServer.stop(j)
      work
    end

    small = open_work.(4_000)
    large = open_work.(16_000)
    assert large < 5 * small, "16,000 records cost #{Float.round(large / small, 2)}x 4,000"
  end

  # Another OS process holding `<path>.owner` with flock(2), as the Python
  # store does for its own database owner file.
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

  # Closing the port returns before flock(1) exits; wait for its exit instead.
  defp release_held(port) do
    Port.command(port, "release\n")
    assert_receive {^port, {:exit_status, _}}, 5_000
  end

  test "an owner in another OS process blocks the journal, and closing releases it",
       %{path: path} do
    j = open(path)
    assert {:ok, first} = Journal.append(j, input("one"))
    assert {:error, :busy} = hold_owner(path)
    GenServer.stop(j)

    assert {:ok, other} = hold_owner(path)
    assert {:error, :owner_conflict} = Journal.start_link(path: path, owner_release_ms: 100)
    release_held(other)

    j = open(path)
    assert Journal.read(j) == [first]
    assert File.stat!(path <> ".owner").mode |> Bitwise.band(0o777) == 0o600
    Process.exit(j, :kill)
    assert_receive {:EXIT, ^j, :killed}
    assert {:ok, other} = wait_for_owner(path, 50)
    release_held(other)
  end

  defp wait_for_owner(path, 0), do: hold_owner(path)

  defp wait_for_owner(path, tries) do
    case hold_owner(path) do
      {:ok, port} ->
        {:ok, port}

      {:error, :busy} ->
        Process.sleep(20)
        wait_for_owner(path, tries - 1)
    end
  end

  # The journal's real flock(1) lock child, paused with SIGSTOP: it still holds
  # flock(2) on `<path>.owner` until SIGCONT lets it exit. Always resumed.
  defp pause_owner(os_pid, resume_after) do
    os_pid = Integer.to_string(os_pid)
    assert {_, 0} = System.cmd("kill", ["-STOP", os_pid])
    on_exit(fn -> System.cmd("kill", ["-CONT", os_pid], stderr_to_stdout: true) end)

    spawn(fn ->
      Process.sleep(resume_after)
      System.cmd("kill", ["-CONT", os_pid], stderr_to_stdout: true)
    end)
  end

  defp owner_os_pid(j) do
    {:os_pid, os_pid} = Port.info(:sys.get_state(j).owner, :os_pid)
    os_pid
  end

  test "a normal stop returns only after the OS owner has released the lock", %{path: path} do
    j = open(path)
    {:ok, first} = Journal.append(j, input("one"))
    pause_owner(owner_os_pid(j), 500)
    assert :ok = GenServer.stop(j)
    next = open(path)
    assert Journal.read(next) == [first]
    GenServer.stop(next)
  end

  test "a stop whose OS owner does not release in time exits owner_release_timeout",
       %{path: path} do
    j = open(path, owner_release_ms: 200)
    pause_owner(owner_os_pid(j), 1_000)
    assert {:owner_release_timeout, _stacktrace} = catch_exit(GenServer.stop(j))
  end

  # Drives a failing open through states that persist until the test acts, so
  # no step races the journal or needs a retry:
  # 1. an outside holder keeps the lock, so the journal's flock -w child waits
  #    and the journal sits in take_owner; it is suspended there;
  # 2. the holder lets go; the child takes the lock and its "owner" line queues
  #    in the suspended journal; the child is then stopped, holding the lock;
  # 3. the journal resumes and fails init. It must still be alive, waiting in
  #    release_owner for the stopped child, not gone with the lock left held.
  # `stall` stands in for the test process being descheduled after it finds
  # the journal, as in a busy full run; no step depends on timing.
  defp failing_open_with_paused_owner(path, stall) do
    {:ok, holder} = hold_owner(path)

    task =
      Task.async(fn ->
        Process.flag(:trap_exit, true)
        Journal.start_link(path: path, owner_release_ms: 60_000)
      end)

    name = {Journal, Path.expand(path)}
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

  test "a failed open releases the OS owner before it returns", %{path: path} do
    j = open(path)
    {:ok, _} = Journal.append(j, input("one"))
    GenServer.stop(j)
    File.write!(path, String.replace(File.read!(path), "one", "two"))
    assert {:releasing, {:error, :corrupt_journal}} = failing_open_with_paused_owner(path, 100)
    # flock -w would hide a missing release from a second open; probe with -n.
    assert lock_free?(path)
  end

  test "a reopen after a killed journal waits for its lock child instead of failing",
       %{path: path} do
    j = open(path)
    {:ok, first} = Journal.append(j, input("one"))
    # A link kill skips terminate; the paused lock child still holds the lock.
    pause_owner(owner_os_pid(j), 300)
    Process.exit(j, :kill)
    assert_receive {:EXIT, ^j, :killed}
    next = open(path)
    assert Journal.read(next) == [first]
    GenServer.stop(next)
  end
end
