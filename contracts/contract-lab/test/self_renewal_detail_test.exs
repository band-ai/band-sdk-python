defmodule ContractLab.SelfRenewalDetailTest do
  # DF-29: `@self` attaches to the immediately following timed rule (renewable-timeouts.md).
  # With an untimed rule in between, the error must say "immediately"; the old text said only
  # "must precede", which the event did.
  use ExUnit.Case, async: true

  @header "---\nname: probe\ndescription: DF-29 probe.\nengine: 0.2.0-lab.1\nroles: [lead, executor]\n---\n"

  test "an untimed rule between @self and the timed rule names the immediate-successor rule" do
    dir = Path.join(System.tmp_dir!(), "df29-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf(dir) end)
    path = Path.join(dir, "probe.band")

    File.write!(
      path,
      @header <>
        "contract @2h => expired\ntype note\n text: text(100)\ntype heartbeat\ntype result\n text: text(100)\n" <>
        "state new\n event executor -> lead: heartbeat() => @self\n executor -> lead: note() => new\n" <>
        " executor -> lead: result() @1h => done | silent\nend done\nend silent\n"
    )

    assert {:error, %{code: :timer_target, line: 8, detail: detail}} = ContractLab.load(path)
    assert detail =~ "immediately following rule"
  end
end
