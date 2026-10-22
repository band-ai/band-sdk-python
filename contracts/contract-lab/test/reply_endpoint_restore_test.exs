defmodule ContractLab.ReplyEndpointRestoreTest do
  # The endpoint's restore op carries the host's capabilities, like its new op; none stated is
  # none at all (fail closed), so a reply contract is refused without contract.replies/1.
  use ExUnit.Case, async: true
  import ExUnit.CaptureIO
  alias ContractLab.Endpoint

  @source "---\nname: deploy\ndescription: Ok and error replies.\nengine: 0.2.0-lab.1\nroles: [lead, deployer]\n---\ncontract @1h => expired\ntype deploy\ntype ok\n  request: ref(deploy)\nstate new\n  lead -> deployer: deploy() as job awaits deploying\n    => ok(request=$job) & deployed\n     | @10m & stalled\nend deployed\nend stalled\n"
  @member %{"kind" => "agent", "active" => true, "permission" => "participant"}
  @policy %{
    "revision" => "r",
    "fixed_roles" => %{},
    "principals" => %{"l" => @member, "d" => @member}
  }

  setup do
    dir = Path.join(System.tmp_dir!(), "reply-endpoint-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf!(dir) end)
    path = Path.join(dir, "deploy.band")
    File.write!(path, @source)
    %{path: path}
  end

  defp exchange(path, requests) do
    input = Enum.map_join(requests, &(JSON.encode!(&1) <> "\n"))
    output = capture_io(input, fn -> assert :ok = Endpoint.run(path) end)
    [_ready | replies] = output |> String.split("\n", trim: true) |> Enum.map(&JSON.decode!/1)
    replies
  end

  test "restore takes the host's capabilities and refuses when they lack contract.replies/1",
       ctx do
    new = %{
      "op" => "new",
      "session" => "s1",
      "bindings" => %{"lead" => "l", "deployer" => "d"},
      "now" => 0,
      "inactivity" => "accepted",
      "max_messages" => 20,
      "room_policy" => @policy,
      "capabilities" => ["contract.replies/1"]
    }

    [started, packet] = exchange(ctx.path, [new, %{"op" => "checkpoint"}])
    refute Map.has_key?(started, "error")

    restore = %{"op" => "restore", "checkpoint" => packet, "now" => 5}

    assert [
             %{"error" => "unsupported_extension"},
             %{"error" => "unsupported_extension"},
             %{"ok" => true}
           ] =
             exchange(ctx.path, [
               restore,
               Map.put(restore, "capabilities", []),
               Map.put(restore, "capabilities", ["contract.replies/1"])
             ])
  end
end
