defmodule HeaderSpikes.FileSnapshotTest do
  # Set P review P-12: a FIFO renamed over the path after the type check must not
  # block a load; it fails closed.
  use ExUnit.Case, async: true
  alias HeaderSpikes.FileSnapshot
  @moduletag :tmp_dir

  # Opening a FIFO read-write never blocks and counts as a writer, so it releases a
  # read-only open stuck on it.
  defp release(path) do
    case :file.open(String.to_charlist(path), [:read, :write, :raw]) do
      {:ok, fd} -> :file.close(fd)
      _ -> :ok
    end
  end

  test "a FIFO renamed over the path before the open fails closed instead of blocking",
       %{tmp_dir: tmp} do
    path = Path.join(tmp, "contract.band")
    File.write!(path, "---\n")
    fifo = Path.join(tmp, "swap.fifo")
    assert {_, 0} = System.cmd("mkfifo", [fifo])
    on_exit(fn -> release(path) end)
    swap = fn -> File.rename!(fifo, path) end

    task =
      Task.async(fn ->
        FileSnapshot.with_file(path, fn _, _ -> {:ok, :read} end, before_open: swap)
      end)

    result = Task.yield(task, 10_000)

    if result == nil do
      release(path)
      Task.await(task)
    end

    assert {:ok, {:error, :unsafe_contract_path}} = result
  end

  # A writer's open of a FIFO returns at once only while some reader holds it open.
  defp reader_waiting?(path) do
    {_, status} = System.cmd("timeout", ["1", "sh", "-c", "exec 3>\"$1\"", "sh", path])
    status == 0
  end

  # Set Q review Q-L2: after the timeout the stuck helper is released and gone, so no
  # reader still holds a dirty I/O thread on the FIFO and no late reply reaches the caller.
  test "a timed-out load releases its helper and leaves no stray reply", %{tmp_dir: tmp} do
    path = Path.join(tmp, "contract.band")
    File.write!(path, "---\n")
    fifo = Path.join(tmp, "swap.fifo")
    assert {_, 0} = System.cmd("mkfifo", [fifo])
    on_exit(fn -> release(path) end)
    swap = fn -> File.rename!(fifo, path) end

    assert {:error, :unsafe_contract_path} =
             FileSnapshot.with_file(path, fn _, _ -> {:ok, :read} end, before_open: swap)

    refute reader_waiting?(path), "the helper is still blocked in its open"
    refute_received {_, :opened}
    refute_received {_, :done, _}
  end

  # Set Q review Q-L9: under I/O stalls a fixed 2 s reported legitimate files as unsafe.
  # The timeout is configurable, 5 s by default, and a call can set its own.
  test "the open timeout defaults to 5 s and a call can set its own", %{tmp_dir: tmp} do
    path = Path.join(tmp, "contract.band")
    File.write!(path, "---\n")
    fifo = Path.join(tmp, "swap.fifo")
    assert {_, 0} = System.cmd("mkfifo", [fifo])
    on_exit(fn -> release(path) end)
    swap = fn -> File.rename!(fifo, path) end
    started = System.monotonic_time(:millisecond)

    assert {:error, :unsafe_contract_path} =
             FileSnapshot.with_file(path, fn _, _ -> {:ok, :read} end,
               before_open: swap,
               open_ms: 300
             )

    assert System.monotonic_time(:millisecond) - started < 1_500
    assert FileSnapshot.open_ms([]) == 5_000
    assert FileSnapshot.open_ms(open_ms: 250) == 250
  end

  # Q-L9: an open that finishes around the timeout never leaves a reply in the caller's
  # mailbox, whichever side of the deadline it lands on.
  test "replies landing around the timeout never reach the caller", %{tmp_dir: tmp} do
    path = Path.join(tmp, "contract.band")
    File.write!(path, String.duplicate("x", 65_536))

    for _ <- 1..20 do
      result = FileSnapshot.with_file(path, fn fd, _ -> :file.read(fd, 65_536) end, open_ms: 1)
      assert match?({:ok, _, _}, result) or result == {:error, :unsafe_contract_path}
    end

    Process.sleep(100)
    assert Process.info(self(), :message_queue_len) == {:message_queue_len, 0}
  end
end
