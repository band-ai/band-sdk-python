defmodule ContractLab.Journal do
  @moduledoc "Synchronous append-only event journal with verified snapshots and one owner per path."
  use GenServer
  alias ContractLab.Canonical
  @genesis String.duplicate("0", 64)
  @safe_integer 9_007_199_254_740_991

  def child_spec(opts),
    do: %{
      id: {__MODULE__, Path.expand(Keyword.fetch!(opts, :path))},
      start: {__MODULE__, :start_link, [opts]}
    }

  def start_link(opts) do
    with path when is_binary(path) <- opts[:path],
         path <- Path.expand(path),
         :ok <- no_symlinks(path) do
      GenServer.start_link(__MODULE__, {path, opts}, name: {:global, {__MODULE__, path}})
    else
      nil -> {:error, :invalid_options}
      {:error, reason} -> {:error, reason}
      _ -> {:error, :invalid_options}
    end
  end

  def append(pid, entry), do: GenServer.call(pid, {:append, entry})
  def read(pid), do: GenServer.call(pid, :read)
  def snapshot(pid, state), do: GenServer.call(pid, {:snapshot, state})
  def latest_snapshot(pid), do: GenServer.call(pid, :snapshot)
  def info(pid), do: GenServer.call(pid, :info)

  @impl true
  def init({path, opts}) do
    max_bytes = Keyword.get(opts, :max_bytes, 64 * 1024 * 1024)
    max_snapshot = Keyword.get(opts, :max_snapshot_bytes, 4 * 1024 * 1024)
    release_ms = Keyword.get(opts, :owner_release_ms, 5_000)

    with true <-
           Enum.all?([max_bytes, max_snapshot, release_ms], &(is_integer(&1) and &1 > 0)) ||
             {:error, :invalid_options},
         :ok <- File.mkdir_p(Path.dirname(path)),
         {:ok, owner} <- take_owner(path <> ".owner", release_ms) do
      case open_owned(path, owner, max_bytes, max_snapshot, release_ms) do
        {:ok, s} ->
          {:ok, s}

        # A failed open must not return while its lock child still holds the lock.
        {:error, reason} ->
          case release_owner(owner, release_ms) do
            {:error, :owner_release_timeout} -> {:stop, :owner_release_timeout}
            _released_or_lost -> {:stop, reason}
          end
      end
    else
      {:error, reason} -> {:stop, reason}
    end
  end

  defp open_owned(path, owner, max_bytes, max_snapshot, release_ms) do
    with {:ok, bytes} <- read_bounded(path, max_bytes, :journal_limit, true),
         {:ok, newest_first, sequence, head, valid_bytes} <- verify_log(bytes),
         {:ok, fd} <- :file.open(String.to_charlist(path), [:raw, :binary, :read, :write]) do
      case trim_tail(fd, valid_bytes) do
        :ok ->
          if bytes == "", do: File.chmod(path, 0o600)

          {:ok,
           %{
             path: path,
             fd: fd,
             owner: owner,
             release_ms: release_ms,
             newest_first: newest_first,
             sequence: sequence,
             head: head,
             bytes: valid_bytes,
             max_bytes: max_bytes,
             max_snapshot: max_snapshot,
             recovered_tail_bytes: byte_size(bytes) - valid_bytes
           }}

        {:error, reason} ->
          :file.close(fd)
          {:error, reason}
      end
    end
  end

  @impl true
  def handle_call(:read, _from, s), do: {:reply, Enum.reverse(s.newest_first), s}

  def handle_call(:info, _from, s),
    do: {:reply, Map.take(s, [:path, :sequence, :head, :bytes, :recovered_tail_bytes]), s}

  def handle_call({:append, entry}, _from, s) do
    if json?(entry) and is_map(entry) do
      core = %{"sequence" => s.sequence + 1, "previous" => s.head, "entry" => entry}
      record = Map.put(core, "hash", Canonical.digest(core))
      encoded = Canonical.encode(record) <> "\n"

      if s.bytes + byte_size(encoded) > s.max_bytes do
        {:reply, {:error, :journal_limit}, s}
      else
        with :ok <- :file.write(s.fd, encoded), :ok <- :file.sync(s.fd) do
          next = %{
            s
            | newest_first: [record | s.newest_first],
              sequence: s.sequence + 1,
              head: record["hash"],
              bytes: s.bytes + byte_size(encoded)
          }

          {:reply, {:ok, record}, next}
        else
          {:error, reason} -> {:stop, {:journal_io, reason}, {:error, :write_unknown}, s}
        end
      end
    else
      {:reply, {:error, :invalid_entry}, s}
    end
  end

  def handle_call({:snapshot, state}, _from, s) do
    if json?(state) do
      core = %{
        "profile" => "contract-lab/journal-snapshot/1",
        "sequence" => s.sequence,
        "head" => s.head,
        "state" => state
      }

      snapshot = Map.put(core, "hash", Canonical.digest(core))
      bytes = Canonical.encode(snapshot) <> "\n"

      cond do
        byte_size(bytes) > s.max_snapshot ->
          {:reply, {:error, :snapshot_limit}, s}

        true ->
          case write_atomic(s.path <> ".snapshot", bytes) do
            :ok -> {:reply, {:ok, snapshot}, s}
            error -> {:reply, error, s}
          end
      end
    else
      {:reply, {:error, :invalid_entry}, s}
    end
  end

  def handle_call(:snapshot, _from, s) do
    result =
      with {:ok, bytes} <-
             read_bounded(s.path <> ".snapshot", s.max_snapshot, :snapshot_limit, false),
           {:ok, snapshot} <- decode_canonical(String.trim_trailing(bytes, "\n")),
           :ok <- verify_snapshot(snapshot, s) do
        {:ok, snapshot}
      else
        {:error, :enoent} -> {:error, :no_snapshot}
        {:error, :snapshot_position} -> {:error, :snapshot_position}
        _ -> {:error, :corrupt_snapshot}
      end

    {:reply, result, s}
  end

  # Losing the lock child means losing ownership: stop rather than keep writing.
  @impl true
  def handle_info({port, {:exit_status, _}}, %{owner: port} = s), do: {:stop, :owner_lost, s}
  def handle_info(_message, s), do: {:noreply, s}

  # The lock child already exited when ownership was lost. Otherwise stop
  # returns only after the OS released the lock, or exits with
  # owner_release_timeout (no exit in time) or owner_lost (port already gone).
  @impl true
  def terminate(:owner_lost, s), do: :file.close(s.fd)

  def terminate(_reason, s) do
    :file.close(s.fd)

    case release_owner(s.owner, s.release_ms) do
      :ok -> :ok
      {:error, reason} -> exit(reason)
    end
  end

  def json?(v) when is_binary(v), do: String.valid?(v)
  def json?(v) when is_integer(v), do: abs(v) <= @safe_integer
  def json?(v) when is_boolean(v) or is_nil(v), do: true
  def json?(v) when is_list(v), do: Enum.all?(v, &json?/1)

  def json?(v) when is_map(v) and not is_struct(v),
    do: Enum.all?(v, fn {k, value} -> is_binary(k) and String.valid?(k) and json?(value) end)

  def json?(_), do: false

  defp read_bounded(path, limit, code, missing?) do
    case File.stat(path) do
      {:ok, %{type: :regular, size: size}} when size <= limit -> File.read(path)
      {:ok, %{type: :regular}} -> {:error, code}
      {:ok, _} -> {:error, :not_regular_file}
      {:error, :enoent} when missing? -> {:ok, ""}
      error -> error
    end
  end

  defp verify_log(bytes) do
    length =
      case List.last(:binary.matches(bytes, "\n")) do
        nil -> 0
        {offset, 1} -> offset + 1
      end

    prefix = binary_part(bytes, 0, length)
    lines = String.split(prefix, "\n", trim: false) |> Enum.drop(-1)

    Enum.reduce_while(lines, {:ok, [], 0, @genesis, length}, fn line,
                                                                {:ok, records, sequence, head,
                                                                 length} ->
      case decode_canonical(line) do
        {:ok,
         %{"sequence" => next, "previous" => previous, "entry" => entry, "hash" => hash} = record}
        when next == sequence + 1 and previous == head and map_size(record) == 4 and is_map(entry) ->
          if hash == Canonical.digest(Map.delete(record, "hash")) do
            {:cont, {:ok, [record | records], next, hash, length}}
          else
            {:halt, {:error, :corrupt_journal}}
          end

        _ ->
          {:halt, {:error, :corrupt_journal}}
      end
    end)
  end

  defp decode_canonical(bytes) do
    with {:ok, value} <- JSON.decode(bytes),
         true <- json?(value) and Canonical.encode(value) == bytes do
      {:ok, value}
    else
      _ -> {:error, :corrupt}
    end
  rescue
    _ -> {:error, :corrupt}
  end

  defp trim_tail(fd, valid_bytes) do
    with {:ok, _} <- :file.position(fd, valid_bytes),
         :ok <- :file.truncate(fd),
         :ok <- :file.sync(fd),
         do: :ok
  end

  defp verify_snapshot(
         %{
           "profile" => "contract-lab/journal-snapshot/1",
           "sequence" => seq,
           "head" => head,
           "state" => _state,
           "hash" => hash
         } = snapshot,
         s
       )
       when is_integer(seq) and seq >= 0 and map_size(snapshot) == 5 do
    expected =
      if seq == 0,
        do: @genesis,
        else: get_in(Enum.at(s.newest_first, s.sequence - seq) || %{}, ["hash"])

    cond do
      hash != Canonical.digest(Map.delete(snapshot, "hash")) -> {:error, :corrupt_snapshot}
      seq > s.sequence or expected != head -> {:error, :snapshot_position}
      true -> :ok
    end
  end

  defp verify_snapshot(_, _), do: {:error, :corrupt_snapshot}

  defp write_atomic(path, bytes) do
    temporary = path <> "." <> Base.encode16(:crypto.strong_rand_bytes(8), case: :lower) <> ".tmp"

    with {:ok, fd} <-
           :file.open(String.to_charlist(temporary), [:raw, :binary, :write, :exclusive]) do
      result =
        try do
          with :ok <- File.chmod(temporary, 0o600),
               :ok <- :file.write(fd, bytes),
               :ok <- :file.sync(fd),
               do: :ok
        after
          :file.close(fd)
        end

      result = if result == :ok, do: File.rename(temporary, path), else: result
      if result != :ok, do: File.rm(temporary)
      result
    end
  end

  # An exclusive flock(2) on `<journal>.owner`, held by a flock(1) child for as
  # long as this process lives. The port closes when the owner exits, even if
  # killed, so the kernel releases the lock with it. A killed owner's child
  # exits asynchronously, so taking the lock waits up to owner_release_ms
  # (flock -w) before it reports owner_conflict.
  defp take_owner(lock, release_ms) do
    with :ok <- no_symlinks(lock),
         flock when is_binary(flock) <-
           System.find_executable("flock") || {:error, :owner_lock_unavailable},
         :ok <- File.touch(lock),
         :ok <- File.chmod(lock, 0o600) do
      port =
        Port.open({:spawn_executable, flock}, [
          :binary,
          :exit_status,
          args: [
            "-w",
            :erlang.float_to_binary(release_ms / 1000, decimals: 3),
            "-E",
            "75",
            lock,
            "-c",
            "echo owner; read -r release"
          ]
        ])

      receive do
        {^port, {:data, "owner\n"}} -> {:ok, port}
        {^port, {:exit_status, 75}} -> {:error, :owner_conflict}
        {^port, _} -> {:error, :owner_lock_unavailable}
      after
        release_ms + 5_000 ->
          Port.close(port)
          {:error, :owner_lock_unavailable}
      end
    end
  end

  # Port.close/1 returns before the flock(1) child exits, so the kernel can still
  # hold the lock. Ask the child to exit and wait, bounded, for its exit status.
  # A port that is already closed acknowledges nothing: ownership was lost.
  defp release_owner(port, release_ms) do
    Port.command(port, "release\n")

    receive do
      {^port, {:exit_status, _}} -> :ok
    after
      release_ms ->
        close_port(port)
        {:error, :owner_release_timeout}
    end
  rescue
    ArgumentError -> {:error, :owner_lost}
  end

  defp close_port(port) do
    Port.close(port)
  rescue
    ArgumentError -> :closed
  end

  defp no_symlinks(path) do
    path
    |> Path.split()
    |> Enum.reduce_while({:ok, ""}, fn part, {:ok, prefix} ->
      current = if prefix == "", do: part, else: Path.join(prefix, part)

      case File.lstat(current) do
        {:ok, %{type: :symlink}} -> {:halt, {:error, :symlink_path}}
        {:error, reason} when reason != :enoent -> {:halt, {:error, reason}}
        _ -> {:cont, {:ok, current}}
      end
    end)
    |> case do
      {:ok, _} -> :ok
      error -> error
    end
  end
end
