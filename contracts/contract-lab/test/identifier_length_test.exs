defmodule ContractLab.IdentifierLengthTest do
  @moduledoc """
  Sofia U6: identifiers are at most 128 bytes, wherever they appear in a body, so error
  details and views stay small. The longest identifier in every local corpus is 28 bytes
  (recommended_with_constraints).
  """
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @header "---\nname: ids\ndescription: Identifier bounds.\nengine: 0.2.0-lab.1\nroles: [lead, worker]\n---\n"

  defp compile(body) do
    bytes = @header <> body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  # Each source names `X` where an identifier goes; the line holding X is reported.
  @places [
    {"state name",
     "contract @1h => expired\ntype k\nstate new\n  lead -> worker: k() => X\nstate X\n  lead -> worker: k() => done\nend done\n"},
    {"type and kind",
     "contract @1h => expired\ntype X\nstate new\n  lead -> worker: X() => done\nend done\n"},
    {"field",
     "contract @1h => expired\ntype k\n  X: boolean\nstate new\n  lead -> worker: k() => done\nend done\n"},
    {"enum value",
     "contract @1h => expired\ntype k\n  v: enum(a|X)\nstate new\n  lead -> worker: k() => done\nend done\n"},
    {"~w enum value",
     "contract @1h => expired\ntype k\n  v: enum(~w[a X])\nstate new\n  lead -> worker: k() => done\nend done\n"},
    {"binding",
     "contract @1h => expired\ntype k\nstate new\n  lead -> worker: k() as X => done\nend done\n"},
    {"terminal",
     "contract @1h => expired\ntype k\nstate new\n  lead -> worker: k() => X\nend X\n"},
    {"wait state and alternative",
     "contract @1h => expired\ntype k\ntype r\n  request: ref(k)\nstate new\n  lead -> worker: k() awaits X\n    => r() & done\n     | @5m & stalled\nend done\nend stalled\n"},
    {"collect reply type",
     "contract @1h => expired\ntype k\ntype X\nstate new\n  lead -> worker: k() => c\nstate c\n  collect reports from worker -> lead @5m => done\n    reply X()\nend done\n"}
  ]

  for {place, source} <- @places do
    @source source
    test "128 bytes is accepted, 129 is syntax at its first line: #{place}" do
      ok = String.replace(@source, "X", "x" <> String.duplicate("y", 127))
      assert {:ok, _} = compile(ok), inspect(compile(ok))

      long = "x" <> String.duplicate("y", 128)
      bad = String.replace(@source, "X", long)
      first = bad |> String.split("\n") |> Enum.find_index(&String.contains?(&1, long))

      assert {:error, %{code: :syntax, line: line, detail: "Identifiers are at most 128 bytes"}} =
               compile(bad)

      assert line == first + 1
    end
  end

  test "documentation and comments are not identifiers" do
    long = String.duplicate("y", 300)

    body =
      "contract @1h => expired\n\"#{long}\"\ntype k\n  %% #{long}\n  t: text(1..300)\n" <>
        "state new\n  lead -> worker: k() => done\nend done\n"

    assert {:ok, _} = compile(body)
  end
end
