defmodule ContractLab.ErrorLocationTest do
  # DF-10: an error must locate the mistake. `line` stays the specified one-based body line
  # (contract-dsl.md), the CLI adds `file_line` counted from the top of the file, and the four
  # authoring mistakes the dogfood probes hit (p01, p04, p05, p12b) carry an "expected" hint
  # instead of a null detail.
  use ExUnit.Case, async: false
  import ExUnit.CaptureIO
  alias ContractLab.{CLI, MixCommand}

  # Six header lines, counting both `---` delimiters.
  @header "---\nname: probe\ndescription: DF-10 probe.\nengine: 0.2.0-lab.1\nroles: [requester, assistant]\n---\n"
  @header_lines 6

  defp source(rule, regex \\ "[0-9]+") do
    @header <>
      "contract @infinity => expired\ntype question\n text: text(100, regex: ~r/#{regex}/)\n" <>
      "state new\n #{rule}\nstate answered\n assistant -> requester: question() => complete\n" <>
      "end complete\nend stalled\n"
  end

  defp contract(text) do
    dir = Path.join(System.tmp_dir!(), "df10-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    path = Path.join(dir, "probe.band")
    File.write!(path, text)
    path
  end

  defp load(rule, regex \\ "[0-9]+"), do: ContractLab.load(contract(source(rule, regex)))

  test "p01: a rule option after the timer names the option order" do
    assert {:error, %{code: :syntax, line: 5, detail: detail}} =
             load("requester -> assistant: question() @1h as q => answered | stalled")

    assert is_binary(detail)
    assert detail =~ "before the timer"
  end

  test "p04: a timed rule without its timeout outcome names the missing | outcome" do
    assert {:error, %{code: :syntax, line: 5, detail: detail}} =
             load("requester -> assistant: question() as q @1h => answered")

    assert is_binary(detail)
    assert detail =~ "timeout outcome"
  end

  # DF-07 made `@15m => outcome @self` legal (p12b now compiles), so the timed-event hint now
  # covers the renewals that still cannot follow a timer: @state and @contract.
  test "p12b: a timed event with @state names the forms that combine" do
    assert {:error, %{code: :syntax, line: 5, detail: detail}} =
             load("event requester -> assistant: question() @15m => stalled @state")

    assert is_binary(detail)
    assert detail =~ "@self"
    assert detail =~ "@state"
  end

  test "p05: a shorthand class in a pattern names its bracket replacement" do
    assert {:error, %{code: :pattern_syntax, line: 3, detail: detail}} =
             load("requester -> assistant: question() => answered", "\\d+")

    assert is_binary(detail)
    assert detail =~ "[0-9]"
  end

  test "the CLI reports file_line counted from the file, and keeps the body line" do
    path = contract(source("requester -> assistant: question() @1h as q => answered | stalled"))

    assert {:error, %{path: ^path, reason: %{code: :syntax, line: 5}, file_line: file_line}} =
             CLI.run(["check", path])

    assert file_line == 5 + @header_lines

    stderr =
      capture_io(:stderr, fn -> catch_exit(MixCommand.run("check", [path])) end)

    assert %{"error" => %{"code" => "syntax", "line" => 5, "file_line" => 11}} =
             JSON.decode!(stderr)
  end

  test "a header error already counts from the file, so file_line equals line" do
    path =
      contract(
        String.replace(
          source("requester -> assistant: question() => answered"),
          "roles:",
          "bogus: x\nroles:"
        )
      )

    assert {:error, %{reason: %{code: :unknown_field, line: 5}, file_line: 5}} =
             CLI.run(["check", path])
  end
end
