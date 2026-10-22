defmodule ContractLab.LowercaseSigilsTest do
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @body ~S'''
  ~s"""
  Coordinate literal C:\work\notes and #{data}, without evaluation.
  /* This is guidance text, not a comment. */
  """
  contract
  ~s|Explain the requested work and preserve uncertainty.|
  role lead
  type request
    label: text(20, regex: ~r/OK|NEXT/)
  type reply
    source: ref(~w[request])
    decision: enum(~w[accept reject])
  state new
    lead -> ~w[a b]: request() as task => waiting
  state waiting
    ~w[a b] -> lead: reply(source=$task) each @1m => done | stalled
  end done
  end stalled
  '''

  test "sigils are lowercase and raw, including queries and behavior identities" do
    for {lower, upper, line} <- [
          {"~s\"\"\"", "~S\"\"\"", 1},
          {"~s|Explain", "~S|Explain", 6},
          {"~r/", "~R/", 9},
          {"ref(~w[", "ref(~W[", 11},
          {"lead -> ~w[", "lead -> ~W[", 14}
        ] do
      assert {:error, %{code: :syntax, line: ^line, detail: detail}} =
               compile(String.replace(@body, lower, upper))

      assert detail == "Sigils are lowercase: ~s, ~r and ~w"
    end

    assert {:ok, c} = compile(@body)
    assert {:ok, view} = ContractLab.for_role(c, "lead")
    assert view.contract.meta.doc =~ ~S|C:\work\notes and #{data}|

    assert c.role_metadata["lead"].meta.doc ==
             "Explain the requested work and preserve uncertainty."

    assert c.types["reply"].fields["decision"].values == ["accept", "reject"]
  end

  test "unknown sigils, modifiers and bad lowercase delimiters fail closed" do
    for bad <- ["~s|x|s", "~s|unclosed", "~s!x!"] do
      assert {:error, %{code: :documentation}} = compile(bad <> "\n" <> @body)
    end

    assert {:error, _} = compile(String.replace(@body, "~w[a b]", "~w[a b]a"))
    assert {:error, _} = compile(String.replace(@body, "~r/OK|NEXT/", "~r/OK|NEXT/i"))
    assert {:error, _} = compile("~x|unknown|\n" <> @body)
  end

  defp compile(body) do
    bytes =
      "---\nname: guidance\ndescription: Document a multi-agent skill.\nengine: 0.2.0-lab.1\nroles: [lead, a, b]\n---\n" <>
        body

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end
end
