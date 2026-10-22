defmodule ContractLab.EndpointTest do
  use ExUnit.Case, async: true
  import ExUnit.CaptureIO
  alias ContractLab.Endpoint

  @contract Path.expand("../contracts/program-counter.band", __DIR__)
  @bindings %{"requester" => "alice", "counter" => "bot"}
  @member %{"kind" => "agent", "active" => true, "permission" => "participant"}
  @policy %{
    "revision" => "r",
    "fixed_roles" => %{},
    "principals" => %{"alice" => @member, "bot" => @member}
  }

  # Drives the public JSON-lines loop, exactly as a host pipe does.
  defp exchange(requests) do
    input = Enum.map_join(requests, &(JSON.encode!(&1) <> "\n"))
    output = capture_io(input, fn -> assert :ok = Endpoint.run(@contract) end)
    [ready | replies] = output |> String.split("\n", trim: true) |> Enum.map(&JSON.decode!/1)
    assert ready["ready"] == true
    replies
  end

  defp new(changes \\ %{}) do
    Map.merge(
      %{
        "op" => "new",
        "session" => "s1",
        "bindings" => @bindings,
        "now" => 0,
        "inactivity" => "accepted",
        "max_messages" => 100,
        "room_policy" => @policy
      },
      changes
    )
  end

  defp step(id) do
    message = %{"id" => id, "session" => "s1", "kind" => "increment", "payload" => %{"by" => 1}}

    %{
      "op" => "step",
      "message" => message,
      "actor" => "alice",
      "now" => 0,
      "room_policy" => @policy
    }
  end

  test "a rejected new keeps the live session" do
    # The hosted endpoint refuses the local-only unchecked mode.
    unchecked = new(%{"session" => "s2", "room_policy" => %{"mode" => "unchecked"}})

    assert [
             %{"ok" => true},
             %{"error" => "invalid_room_policy"},
             %{"id" => "s1", "version" => 0, "status" => "active"},
             %{"status" => "accepted"},
             %{"id" => "s1", "version" => 1}
           ] =
             exchange([new(), unchecked, %{"op" => "summary"}, step("m1"), %{"op" => "summary"}])
  end

  defp source_file(bytes) do
    path =
      Path.join(System.tmp_dir!(), "endpoint-source-#{System.unique_integer([:positive])}.json")

    File.write!(path, bytes)
    File.chmod!(path, 0o600)
    on_exit(fn -> File.rm(path) end)
    path
  end

  test "file-source host loading preserves canonical standard identities" do
    fixture =
      "../../spec/conformance/case-v2-frontmatter-and-standard-discovery.json"
      |> Path.expand(__DIR__)
      |> File.read!()
      |> JSON.decode!()

    source = Enum.at(fixture["requests"], 1)["source"]
    {:ok, compiled} = ContractLab.FileSource.compile(source)
    path = source_file(JSON.encode!(source))
    output = capture_io("", fn -> assert :ok = Endpoint.run(path, "file-source") end)

    assert JSON.decode!(output) == %{
             "ready" => true,
             "source" => source["sha256"],
             "definition" => compiled.digest
           }
  end

  test "file-source host bounds encoded bytes before JSON decoding" do
    path = source_file(String.duplicate(" ", 4_194_305))
    error = assert_raise MatchError, fn -> Endpoint.run(path, "file-source") end
    assert error.term == {:error, :source_size}
  end
end
