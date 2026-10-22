defmodule ContractLab.ContractRequirePromptsTest do
  # Eric 10-15: every contract carries an LLM prompt on the contract itself and on every role,
  # message type and state. `mix contract.check --require-prompts` enforces it.
  use ExUnit.Case, async: false
  import ExUnit.CaptureIO
  alias ContractLab.MixCommand

  @header "---\nname: prompts\ndescription: Prompt probe.\nengine: 0.2.0-lab.1\nroles: [lead, worker]\n---\n"

  @complete @header <>
              """
              \"\"\"
              One lead assigns one piece of work and collects the note that closes it.
              \"\"\"
              contract @1h => expired
              \"\"\"
              The lead plans and assigns the work.
              \"\"\"
              role lead
              "The worker does the work and reports back."
              role worker
              \"\"\"
              An offer of work to the worker.
              \"\"\"
              type offer
                text: text(1..100)
              \"\"\"
              A note back to the lead.
              \"\"\"
              type note
                offer: ref(offer)
              \"\"\"
              Waiting for the lead's offer.
              \"\"\"
              state new
                lead -> worker: offer() awaits waiting
                  => note() & done
                   | @30m & stalled
              \"\"\"
              The worker owes the lead a note.
              \"\"\"
              state waiting
              end done
              end stalled
              """

  # Missing: the contract (body line 1), role worker (no declaration: the compiler's default
  # line 1), type offer (6) and state waiting (20), whose section is its wait's prompt.
  @partial @header <>
             """
             contract @1h => expired
             \"\"\"
             The lead plans and assigns the work.
             \"\"\"
             role lead
             type offer
               text: text(1..100)
             \"\"\"
             A note back to the lead.
             \"\"\"
             type note
               offer: ref(offer)
             \"\"\"
             Waiting for the lead's offer.
             \"\"\"
             state new
               lead -> worker: offer() awaits waiting
                 => note() & done
                  | @30m & stalled
             state waiting
             end done
             end stalled
             """

  defp contract(text) do
    dir = Path.join(System.tmp_dir!(), "prompts-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    path = Path.join(dir, "prompts.band")
    File.write!(path, text)
    path
  end

  defp run_check(args) do
    parent = self()

    stderr =
      capture_io(:stderr, fn ->
        result =
          try do
            {:returned, capture_io(fn -> MixCommand.run("check", args) end)}
          catch
            :exit, reason -> {:exit, reason}
          end

        send(parent, {:result, result})
      end)

    assert_received {:result, result}
    {result, stderr}
  end

  test "a contract with every prompt passes and reports prompts complete" do
    path = contract(@complete)
    assert {{:returned, stdout}, ""} = run_check(["--require-prompts", path])
    assert [%{"name" => "prompts", "prompts" => "complete"}] = JSON.decode!(stdout)
  end

  test "lists each missing prompt with its item, name, line and file line" do
    path = contract(@partial)
    {result, stderr} = run_check(["--require-prompts", path])
    assert result == {:exit, {:shutdown, 1}}

    assert %{"error" => %{"code" => "prompts_required", "path" => ^path, "missing" => missing}} =
             JSON.decode!(stderr)

    assert missing == [
             %{"item" => "contract", "name" => "prompts", "line" => 1, "file_line" => 7},
             %{"item" => "role", "name" => "worker", "line" => 1, "file_line" => 7},
             %{"item" => "type", "name" => "offer", "line" => 6, "file_line" => 12},
             %{"item" => "state", "name" => "waiting", "line" => 20, "file_line" => 26}
           ]
  end

  test "without the flag a contract without prompts still checks" do
    path = contract(@partial)
    assert {{:returned, stdout}, ""} = run_check([path])
    assert [entry] = JSON.decode!(stdout)
    refute Map.has_key?(entry, "prompts")
  end

  test "--require-prompts belongs to check only" do
    path = contract(@complete)
    parent = self()

    stderr =
      capture_io(:stderr, fn ->
        result =
          try do
            capture_io(fn -> MixCommand.run("query", ["--require-prompts", path]) end)
          catch
            :exit, reason -> {:exit, reason}
          end

        send(parent, {:result, result})
      end)

    assert_received {:result, {:exit, {:shutdown, 1}}}
    assert %{"error" => %{"code" => "conflicting_options"}} = JSON.decode!(stderr)
  end
end
