defmodule ContractLab.LoadUnsafePathTest do
  @moduledoc "Sofia set O O2: load reads the entry only through the bounded, regular-file reader."
  use ExUnit.Case, async: true

  @moduletag :tmp_dir

  defp load_within(path, ms) do
    task = Task.async(fn -> ContractLab.load(path) end)
    Task.yield(task, ms) || Task.shutdown(task, :brutal_kill) || :timeout
  end

  @tag :fifo
  test "a FIFO entry is refused promptly, never opened for reading", %{tmp_dir: dir} do
    path = Path.join(dir, "x.band")
    {_, 0} = System.cmd("mkfifo", [path])
    result = load_within(path, 2_000)

    # A regression would block a reader on the FIFO; give it EOF so the suite fails instead of hanging.
    if result == :timeout,
      do: spawn(fn -> with {:ok, f} <- File.open(path, [:write, :raw]), do: File.close(f) end)

    assert {:ok, {:error, :unsafe_contract_path}} = result
  end

  @tag :device_symlink
  test "a symlink to /dev/zero is refused promptly, never read to EOF", %{tmp_dir: dir} do
    path = Path.join(dir, "x.band")
    :ok = File.ln_s("/dev/zero", path)
    assert {:ok, {:error, :unsafe_contract_path}} = load_within(path, 2_000)
  end

  test "a header field error on a regular file still reports the field line", %{tmp_dir: dir} do
    path = Path.join(dir, "x.band")

    File.write!(
      path,
      "---\nname: x\ndescription: d\nengine: 0.2.0-lab.1\nroles: [a, b]\ncolour: red\n---\n" <>
        "contract @infinity => expired\ntype ping\nstate new\n  a -> b: ping() => done\nend done\n"
    )

    assert {:error, %{code: :unknown_field, line: 6, detail: "colour"}} = ContractLab.load(path)
  end
end
