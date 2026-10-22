ExUnit.start()
Code.require_file("band_command.exs", __DIR__)

defmodule ContractQualificationCommandTest do
  use ExUnit.Case

  setup do
    root = Path.join(System.tmp_dir!(), "band-command-#{System.unique_integer([:positive])}")
    File.mkdir!(root)
    on_exit(fn -> File.rm_rf!(root) end)
    {:ok, root: root}
  end

  test "partial JSON is retained for retry, not a host crash", %{root: root} do
    command = Path.join(root, "command-2.json")
    File.write!(command, ~s({"id":"a",))

    assert :pending =
             ContractQualification.Command.consume(root, "2", fn _ ->
               flunk("partial command applied")
             end)

    assert File.exists?(command)
  end

  test "ack is published before deleting the command; a retained acknowledged command is not reapplied",
       %{root: root} do
    command = Path.join(root, "command-2.json")
    done = Path.join(root, "command-done-2.json")
    raw = ~s({"id":"a","operation":"revoke"})
    File.write!(command, raw)

    assert :done =
             ContractQualification.Command.consume(
               root,
               "2",
               fn request -> assert request["id"] == "a" end,
               fn path ->
                 assert :json.decode(File.read!(done)) == %{"id" => "a"}
                 File.rm!(path)
               end
             )

    assert :json.decode(File.read!(done)) == %{"id" => "a"}
    refute File.exists?(command)
    assert Bitwise.band(File.stat!(done).mode, 0o777) == 0o600
    File.write!(command, raw)

    assert :done =
             ContractQualification.Command.consume(root, "2", fn _ ->
               flunk("acknowledged effect reapplied")
             end)

    refute File.exists?(command)
  end
end
