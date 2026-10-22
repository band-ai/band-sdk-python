defmodule ContractLab.HostKeyTest do
  # Set O review LOW: the host key must be created private and atomically and read through
  # one descriptor whose type, size, links, mode and owner are checked.
  use ExUnit.Case, async: false
  import Bitwise
  alias ContractLab.HostKey

  setup do
    dir = Path.join(System.tmp_dir!(), "contract-host-key-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf!(dir) end)
    {:ok, dir: dir, path: Path.join(dir, "j.jsonl.host-key")}
  end

  test "a created key is a private 32-byte file and nothing else is left behind", ctx do
    assert {:ok, key} = HostKey.create(ctx.path)
    assert byte_size(key) == 32
    assert {:ok, %{type: :regular, size: 32, links: 1, mode: mode}} = File.lstat(ctx.path)
    assert (mode &&& 0o777) == 0o600
    assert File.ls!(ctx.dir) == ["j.jsonl.host-key"]
    assert {:ok, ^key} = HostKey.load(ctx.path)
  end

  test "create never replaces an existing key", ctx do
    assert {:ok, key} = HostKey.create(ctx.path)
    assert {:error, :invalid_host_key} = HostKey.create(ctx.path)
    assert {:ok, ^key} = HostKey.load(ctx.path)
    assert File.ls!(ctx.dir) == ["j.jsonl.host-key"]
  end

  test "a key file owned by another user is refused", ctx do
    assert {:ok, _} = HostKey.create(ctx.path)
    # "/" is owned by root, so the key (owned by this user) appears foreign.
    Application.put_env(:contract_lab, :proc_self, "/")
    on_exit(fn -> Application.delete_env(:contract_lab, :proc_self) end)
    assert {:error, :invalid_host_key} = HostKey.load(ctx.path)
  end

  test "without /proc/self the owner check fails closed", ctx do
    assert {:ok, _} = HostKey.create(ctx.path)
    Application.put_env(:contract_lab, :proc_self, Path.join(ctx.dir, "no-proc-self"))
    on_exit(fn -> Application.delete_env(:contract_lab, :proc_self) end)
    assert {:error, :invalid_host_key} = HostKey.load(ctx.path)
  end

  test "group-readable, symlinked, hard-linked and wrong-size keys are refused", ctx do
    assert {:ok, _} = HostKey.create(ctx.path)
    File.chmod!(ctx.path, 0o640)
    assert {:error, :invalid_host_key} = HostKey.load(ctx.path)
    File.chmod!(ctx.path, 0o600)

    link = Path.join(ctx.dir, "link.host-key")
    File.ln_s!(ctx.path, link)
    assert {:error, :invalid_host_key} = HostKey.load(link)

    hard = Path.join(ctx.dir, "hard.host-key")
    File.ln!(ctx.path, hard)
    assert {:error, :invalid_host_key} = HostKey.load(ctx.path)
    File.rm!(hard)

    File.write!(ctx.path, :crypto.strong_rand_bytes(31))
    assert {:error, :invalid_host_key} = HostKey.load(ctx.path)
  end

  test "a missing key is reported as missing, not created", ctx do
    assert {:error, :enoent} = HostKey.load(ctx.path)
    refute File.exists?(ctx.path)
  end

  # Set P review P-L1 (b): after make_link the new directory entry must be durable, or a
  # crash can lose the key after its id is journaled. A recorder stands in for sync(1).
  defp sync_command(ctx, body) do
    script = Path.join(ctx.dir, "sync-stand-in")
    File.write!(script, "#!/bin/sh\n" <> body)
    File.chmod!(script, 0o755)
    Application.put_env(:contract_lab, :sync_command, script)
    on_exit(fn -> Application.delete_env(:contract_lab, :sync_command) end)
    script
  end

  test "create syncs the key's directory after linking the key into place", ctx do
    log = Path.join(ctx.dir, "sync.log")
    sync_command(ctx, "printf '%s\\n' \"$*\" >> '#{log}'\nexec sync \"$@\"\n")
    assert {:ok, _} = HostKey.create(ctx.path)
    assert File.read!(log) == "-- #{ctx.dir}\n"
  end

  test "create fails closed when the directory sync fails", ctx do
    sync_command(ctx, "exit 1\n")
    assert {:error, :invalid_host_key} = HostKey.create(ctx.path)
    Application.put_env(:contract_lab, :sync_command, Path.join(ctx.dir, "no-such-sync"))
    assert {:error, :invalid_host_key} = HostKey.create(Path.join(ctx.dir, "other.host-key"))
  end

  # Set P impl review P-6: a crash between make_link and the cleanup leaves the staged name
  # as a second hard link to the key (and a stray copy of the secret), so every load would
  # refuse it. The holder of the journal's lock removes it before loading.
  defp staged_link(ctx, name) do
    dir = Path.join(ctx.dir, name)
    File.mkdir!(dir)
    File.chmod!(dir, 0o700)
    :ok = :file.make_link(String.to_charlist(ctx.path), String.to_charlist(Path.join(dir, "key")))
    dir
  end

  test "a staged link left by a crash is removed before the key is loaded", ctx do
    assert {:ok, key} = HostKey.create(ctx.path)

    for name <- [".j.jsonl.host-key.staging-0123456789abcdef", ".host-key-0123456789abcdef"] do
      dir = staged_link(ctx, name)
      assert {:ok, ^key} = HostKey.load(ctx.path)
      refute File.exists?(dir)
    end

    assert File.ls!(ctx.dir) == ["j.jsonl.host-key"]
  end

  test "this key's unlinked staging is removed; another key's staging is kept", ctx do
    own = Path.join(ctx.dir, ".j.jsonl.host-key.staging-00000000000000aa")
    File.mkdir!(own)
    File.write!(Path.join(own, "key"), :crypto.strong_rand_bytes(32))
    other = Path.join(ctx.dir, ".host-key-00000000000000bb")
    File.mkdir!(other)
    File.write!(Path.join(other, "key"), :crypto.strong_rand_bytes(32))
    sibling = Path.join(ctx.dir, ".k.jsonl.host-key.staging-00000000000000cc")
    File.mkdir!(sibling)

    assert {:ok, _} = HostKey.create(ctx.path)
    refute File.exists?(own)
    assert File.exists?(Path.join(other, "key"))
    assert File.dir?(sibling)
  end

  # Set Q review Q-L1 (a): the sync is found at a fixed absolute path, not through PATH.
  test "the directory sync resolves to an absolute coreutils path, never PATH" do
    Application.delete_env(:contract_lab, :sync_command)
    assert {:ok, sync} = HostKey.sync_command()
    assert sync in ["/usr/bin/sync", "/bin/sync"]
  end
end
