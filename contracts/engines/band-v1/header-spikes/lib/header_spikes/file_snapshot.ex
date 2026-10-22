defmodule HeaderSpikes.FileSnapshot do
  @moduledoc "Bounded regular-file reads with descriptor and path identity checks."

  # file_info fields excluding access mode and atime, which can change on a read.
  @identity_fields [1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13]
  @default_open_ms 5_000

  def with_file(path, reader, opts \\ []) do
    path = String.to_charlist(path)

    with {:ok, before} <- io(:file.read_link_info(path, time: :posix)),
         true <- elem(before, 2) == :regular || {:error, :unsafe_contract_path},
         :ok <- before_open(opts) do
      opened(path, before, reader, open_ms(opts))
    end
  end

  @doc """
  How long the open may take before a load fails closed (milliseconds): the call's
  `:open_ms` option, else the `:header_spikes, :open_ms` application setting, else
  5000. Raise it where storage can stall for longer.
  """
  def open_ms(opts) do
    Keyword.get_lazy(opts, :open_ms, fn ->
      Application.get_env(:header_spikes, :open_ms, @default_open_ms)
    end)
  end

  # Erlang has no O_NONBLOCK or O_NOFOLLOW open, and a read-only open of a FIFO
  # blocks until a writer appears. So the open, the read and the checks run in a
  # helper (a raw descriptor is usable only by the process that opened it), and the
  # open must return within open_ms/1: anything that blocks there, such as a FIFO
  # renamed over the path after the type check, fails closed. The descriptor is then
  # fstat'ed and must be the regular file read_link_info saw (type and inode
  # included), which also rejects a symlink or another file swapped in.
  #
  # After a timeout the helper is released rather than left holding a dirty I/O
  # thread: a FIFO still at the path is opened read-write, which never blocks on
  # Linux and counts as the writer its read-only open waits for. The helper then
  # fails its identity check, closes the descriptor and exits. Its replies go to a
  # process alias that is deactivated first, so a late {ref, :opened} or
  # {ref, :done, _} is dropped, and any that arrived in time is flushed.
  defp opened(path, before, reader, open_ms) do
    ref = :erlang.alias([:explicit_unalias])

    {pid, monitor} =
      spawn_monitor(fn ->
        opened = io(:file.open(path, [:read, :binary, :raw]))
        send(ref, {ref, :opened})
        send(ref, {ref, :done, checked(opened, path, before, reader)})
      end)

    receive do
      {^ref, :opened} ->
        receive do
          {^ref, :done, result} ->
            :erlang.unalias(ref)
            Process.demonitor(monitor, [:flush])
            outcome(result)

          {:DOWN, ^monitor, :process, ^pid, reason} ->
            :erlang.unalias(ref)
            exit(reason)
        end

      {:DOWN, ^monitor, :process, ^pid, reason} ->
        :erlang.unalias(ref)
        exit(reason)
    after
      open_ms ->
        :erlang.unalias(ref)
        release_fifo(path)

        receive do
          {:DOWN, ^monitor, :process, ^pid, _} -> :ok
        after
          open_ms ->
            Process.exit(pid, :kill)
            Process.demonitor(monitor, [:flush])
        end

        flush(ref)
        {:error, :unsafe_contract_path}
    end
  end

  # Only a path that is still a FIFO (file_info type :other) is opened, so the
  # read-write open neither truncates nor creates anything; a rename racing this
  # check is the swapper's own and is closed again at once.
  defp release_fifo(path) do
    with {:ok, info} <- :file.read_link_info(path),
         :other <- elem(info, 2),
         {:ok, fd} <- :file.open(path, [:read, :write, :raw]) do
      :file.close(fd)
    end

    :ok
  end

  defp flush(ref) do
    receive do
      {^ref, _} -> flush(ref)
      {^ref, _, _} -> flush(ref)
    after
      0 -> :ok
    end
  end

  defp checked({:ok, descriptor}, path, before, reader) do
    try do
      with :ok <- unchanged(descriptor, path, before),
           {:ok, value} <- reader.(descriptor, before),
           :ok <- unchanged(descriptor, path, before) do
        {:ok, value, identity(before)}
      end
    catch
      kind, reason -> {:raise, kind, reason, __STACKTRACE__}
    after
      :file.close(descriptor)
    end
  end

  defp checked(error, _path, _before, _reader), do: error

  defp outcome({:raise, kind, reason, stack}), do: :erlang.raise(kind, reason, stack)
  defp outcome(result), do: result

  # Test seam: runs between the type check and the open, where a rename can land.
  defp before_open(opts) do
    if hook = opts[:before_open], do: hook.()
    :ok
  end

  def read(path, limit) when is_integer(limit) and limit > 0 do
    with_file(path, fn descriptor, info ->
      if elem(info, 1) > limit do
        {:error, :source_size}
      else
        case :file.read(descriptor, limit + 1) do
          {:ok, bytes} when byte_size(bytes) <= limit -> {:ok, bytes}
          {:ok, _} -> {:error, :source_size}
          :eof -> {:ok, ""}
          error -> io(error)
        end
      end
    end)
  end

  defp unchanged(descriptor, path, before) do
    with {:ok, opened} <- io(:file.read_file_info(descriptor, time: :posix)),
         {:ok, named} <- io(:file.read_link_info(path, time: :posix)),
         true <-
           (identity(before) == identity(opened) and identity(before) == identity(named)) ||
             {:error, :source_changed} do
      :ok
    end
  end

  defp identity(info), do: Enum.map(@identity_fields, &elem(info, &1))
  defp io({:error, reason}), do: {:error, {:file_io, reason}}
  defp io(result), do: result
end
