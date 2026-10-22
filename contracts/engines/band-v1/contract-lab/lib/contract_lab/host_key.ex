defmodule ContractLab.HostKey do
  @moduledoc "Private per-host key for outbound effect IDs; never journaled, snapshotted or shown."
  import Bitwise
  alias ContractLab.Canonical

  @size 32

  # `<journal>.host-key` is an owner-only 32-byte regular file. The journal commits to
  # its id, so the caller decides whether a missing key may be created; this module never
  # creates one on its own. The caller holds the journal's owner lock.
  #
  # The path is checked without following links, then the open descriptor is checked
  # again (same inode, regular, 32 bytes, one link, 0600, owned by this OS user), so a
  # swap between the two cannot hand over another file.
  def load(path) do
    discard_staging(path)

    with {:ok, uid} <- own_uid(),
         {:ok, before} <- lstat(path),
         true <- private?(before, uid) || {:error, :invalid_host_key},
         {:ok, fd} <- open(path) do
      result =
        with {:ok, record} <- :file.read_file_info(fd, time: :posix),
             opened = File.Stat.from_record(record),
             true <- same_file?(before, opened) and private?(opened, uid),
             {:ok, <<_::binary-size(@size)>> = key} <- :file.read(fd, @size + 1) do
          {:ok, key}
        else
          _ -> {:error, :invalid_host_key}
        end

      :file.close(fd)
      result
    end
  end

  defp lstat(path) do
    case File.lstat(path, time: :posix) do
      {:ok, stat} -> {:ok, stat}
      {:error, :enoent} -> {:error, :enoent}
      {:error, _} -> {:error, :invalid_host_key}
    end
  end

  defp open(path) do
    case :file.open(String.to_charlist(path), [:read, :raw, :binary]) do
      {:ok, fd} -> {:ok, fd}
      {:error, _} -> {:error, :invalid_host_key}
    end
  end

  defp private?(%File.Stat{type: :regular, size: @size, links: 1, mode: mode, uid: uid}, uid),
    do: (mode &&& 0o777) == 0o600

  defp private?(_, _), do: false

  defp same_file?(a, b),
    do: {a.inode, a.major_device, a.minor_device} == {b.inode, b.major_device, b.minor_device}

  # This OS user, from /proc/self; without it (off Linux) the owner check fails closed.
  defp own_uid do
    case File.stat(Application.get_env(:contract_lab, :proc_self, "/proc/self")) do
      {:ok, %{uid: uid}} -> {:ok, uid}
      _ -> {:error, :invalid_host_key}
    end
  end

  @doc "A public, non-secret identifier the journal records to commit to this key."
  def id(key), do: binary_part(mac(key, ["contract-lab/host-key-id/1"]), 0, 16)

  # HMAC-SHA256 over a canonical, domain-labelled tuple, as lowercase hex. The key may be
  # sealed in a zero-arity closure, so callers never hold the raw bytes in their state.
  def mac(key, parts) when is_function(key, 0), do: mac(key.(), parts)

  def mac(key, parts) do
    :crypto.mac(:hmac, :sha256, key, Canonical.encode(parts)) |> Base.encode16(case: :lower)
  end

  # The key is written inside a fresh 0700 directory, so it is never readable by anyone
  # else whatever the umask, then hard-linked into place: the link fails if anything
  # already exists there, so a key is never replaced. The private directory is removed.
  # The parent directory is then synced, so the new entry survives a crash before the
  # journal commits to the key's id; if that sync fails, creation fails closed.
  def create(path) do
    discard_staging(path)
    key = :crypto.strong_rand_bytes(@size)
    nonce = Base.encode16(:crypto.strong_rand_bytes(8), case: :lower)
    private = Path.join(Path.dirname(path), staging_prefix(path) <> nonce)
    staged = Path.join(private, "key")

    linked =
      with :ok <- File.mkdir(private),
           :ok <- File.chmod(private, 0o700),
           :ok <- write_staged(staged, key) do
        :file.make_link(String.to_charlist(staged), String.to_charlist(path))
      end

    File.rm_rf(private)

    with :ok <- linked,
         :ok <- run_sync([Path.dirname(path)]) do
      load(path)
    else
      _ -> {:error, :invalid_host_key}
    end
  end

  # A crash inside create can leave its private directory behind (P-6): before the link it
  # holds a stray copy of a never-used key; after it, a second link that makes every load
  # refuse the key. The caller holds the journal's owner lock, so no create for this key
  # runs concurrently: this key's staging directories are removed, and a legacy
  # `.host-key-<nonce>` directory (shared by every journal in the directory) only when its
  # entry is a link to this very key.
  defp discard_staging(path) do
    dir = Path.dirname(path)
    key = stat(path)

    with {:ok, names} <- File.ls(dir) do
      for name <- names, staging?(dir, name, staging_prefix(path), key) do
        File.rm_rf(Path.join(dir, name))
      end
    end

    :ok
  end

  defp staging_prefix(path), do: "." <> Path.basename(path) <> ".staging-"

  defp staging?(dir, name, own, key) do
    staged = Path.join(dir, name)

    cond do
      nonce?(name, own) ->
        match?(%File.Stat{type: :directory}, stat(staged))

      nonce?(name, ".host-key-") and key != nil ->
        match?(%File.Stat{type: :directory}, stat(staged)) and
          linked_to?(stat(Path.join(staged, "key")), key)

      true ->
        false
    end
  end

  defp linked_to?(%File.Stat{type: :regular} = entry, key), do: same_file?(entry, key)
  defp linked_to?(_, _), do: false

  defp nonce?(name, prefix) do
    String.starts_with?(name, prefix) and
      String.replace_prefix(name, prefix, "") =~ ~r/\A[0-9a-f]{16}\z/
  end

  defp stat(path) do
    case File.lstat(path, time: :posix) do
      {:ok, stat} -> stat
      _ -> nil
    end
  end

  @sync_paths ["/usr/bin/sync", "/bin/sync"]

  @doc """
  The coreutils sync(1) that fsyncs a named directory (Erlang cannot open one to fsync
  it), at a fixed absolute path and never found through PATH. `:contract_lab,
  :sync_command` overrides it. A Replacement checks it at boot.
  """
  def sync_command do
    candidates =
      case Application.fetch_env(:contract_lab, :sync_command) do
        {:ok, command} -> [command]
        :error -> @sync_paths
      end

    case Enum.find(candidates, &executable?/1) do
      nil -> {:error, :sync_unavailable}
      command -> {:ok, command}
    end
  end

  defp executable?(path) do
    Path.type(path) == :absolute and
      match?(
        {:ok, %File.Stat{type: :regular, mode: mode}} when (mode &&& 0o111) != 0,
        File.stat(path)
      )
  end

  @doc """
  Makes a present key durable before a journal first commits to its id (Q-L12): sync(1)
  fsyncs the key file and its directory. A key left behind by a create whose sync failed,
  or by a crash before its id was journaled, is committed only once both survive a crash.
  Like `create/1`, it fails closed.
  """
  def sync(path) do
    case run_sync([path, Path.dirname(path)]) do
      :ok -> :ok
      :error -> {:error, :invalid_host_key}
    end
  end

  defp run_sync(paths) do
    with {:ok, command} <- sync_command(),
         {_, 0} <- System.cmd(command, ["--" | paths], stderr_to_stdout: true) do
      :ok
    else
      _ -> :error
    end
  rescue
    ErlangError -> :error
  end

  defp write_staged(staged, key) do
    case :file.open(String.to_charlist(staged), [:write, :exclusive, :raw, :binary]) do
      {:ok, fd} ->
        written =
          with :ok <- File.chmod(staged, 0o600),
               :ok <- :file.write(fd, key),
               do: :file.sync(fd)

        :file.close(fd)
        written

      {:error, reason} ->
        {:error, reason}
    end
  end
end
