defmodule ContractLab.RoleSetSpaceTest do
  # DF-26: the grammar has no spaces inside a comma-separated role set (contract-dsl.md:
  # "Comma-separated role sets do not contain spaces"), so `{a, b}` stays a syntax error, but
  # the detail names the space and the spelling that compiles instead of echoing the line.
  use ExUnit.Case, async: true

  @header "---\nname: probe\ndescription: DF-26 probe.\nengine: 0.2.0-lab.1\nroles: [requester, assistant]\n---\n"

  defp load(rule) do
    dir = Path.join(System.tmp_dir!(), "df26-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    path = Path.join(dir, "probe.band")

    File.write!(
      path,
      @header <>
        "contract @infinity => expired\ntype question\n text: text(100)\nstate new\n #{rule}\n" <>
        "end answered\n"
    )

    ContractLab.load(path)
  end

  test "the spelling without spaces compiles" do
    assert {:ok, _} = load("{requester,assistant} -> assistant: question() => answered")
  end

  for {side, rule} <- [
        sender: "{requester, assistant} -> assistant: question() => answered",
        recipient: "requester -> {requester, assistant}: question() => answered"
      ] do
    test "a space inside a #{side} role set is named in the error" do
      assert {:error, %{code: :syntax, line: 5, detail: detail}} = load(unquote(rule))
      assert detail =~ "no spaces"
      assert detail =~ "{requester,assistant}"
    end
  end
end
