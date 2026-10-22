defmodule ContractLab.CliJsonErrorsTest do
  # DF-09: a failing mix contract.* command prints one JSON error object on stderr and exits
  # with status 1, so agents and gates can branch on `code` (contract-dsl.md: callers MUST
  # branch on the code). Before, the failure was Elixir inspect output from Mix.raise.
  use ExUnit.Case, async: false
  import ExUnit.CaptureIO
  alias ContractLab.MixCommand

  @header "---\nname: probe\ndescription: DF-09 probe.\nengine: 0.2.0-lab.1\nroles: [requester, assistant]\n---\n"
  @body "type question\n text: text(100)\nstate new\n requester -> assistant: question() => complete\nend complete\n"

  defp contract(text) do
    dir = Path.join(System.tmp_dir!(), "df09-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    path = Path.join(dir, "probe.band")
    File.write!(path, text)
    path
  end

  defp mix_failure(command, args) do
    parent = self()

    stderr =
      capture_io(:stderr, fn ->
        result =
          try do
            capture_io(fn -> MixCommand.run(command, args) end)
            :returned
          rescue
            error -> {:raised, Exception.message(error)}
          catch
            :exit, reason -> {:exit, reason}
          end

        send(parent, {:result, result})
      end)

    assert_received {:result, result}
    {result, stderr}
  end

  test "a compiler error is a JSON object on stderr with exit status 1" do
    path = contract(@header <> @body)
    {result, stderr} = mix_failure("check", ["--format", "frontmatter", path])
    assert result == {:exit, {:shutdown, 1}}

    assert %{"error" => %{"code" => "contract_clock_required", "line" => 1} = error} =
             JSON.decode!(stderr)

    assert error["path"] == path
    assert is_binary(error["detail"])
  end

  test "usage and option errors are JSON objects too" do
    {result, stderr} = mix_failure("check", [])
    assert result == {:exit, {:shutdown, 1}}
    assert %{"error" => %{"code" => "usage"}} = JSON.decode!(stderr)

    path = contract(@header <> "contract @infinity => expired\n" <> @body)
    {result, stderr} = mix_failure("query", [path, "--bogus"])
    assert result == {:exit, {:shutdown, 1}}
    assert %{"error" => %{"code" => "invalid_options"}} = JSON.decode!(stderr)
  end

  test "a failed replay expectation keeps its structured report" do
    path = contract(@header <> "contract @infinity => expired\n" <> @body)
    scenario = Path.join(Path.dirname(path), "scenario.json")

    File.write!(
      scenario,
      JSON.encode!(%{
        "bindings" => %{"requester" => "a", "assistant" => "b"},
        "session" => "s",
        "now" => 0,
        "inactivity" => "accepted",
        "max_messages" => 10,
        "steps" => [%{"tick" => 0, "expect" => %{"outcome" => "complete"}}]
      })
    )

    {result, stderr} = mix_failure("replay", [path, scenario])
    assert result == {:exit, {:shutdown, 1}}
    json = stderr |> String.split("\n", trim: true) |> List.last()

    assert %{"error" => %{"code" => "expectation_mismatch", "detail" => %{"step" => 1}}} =
             JSON.decode!(json)
  end

  test "success still prints JSON on stdout" do
    path = contract(@header <> "contract @infinity => expired\n" <> @body)
    out = capture_io(fn -> MixCommand.run("check", [path]) end)
    assert [%{"name" => "probe"}] = JSON.decode!(out)
  end
end
