defmodule ContractLab.BlockCommentsTest do
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

  test "multiline comments preserve declaration lines and pending guidance" do
    prefix = "/* Author note.\n~s\"\"\" and state are inert here.\n*/\n"
    assert {:ok, c} = compile(prefix <> @body)
    assert c.meta.line == 4
    assert {:ok, without} = compile(@body)
    assert c.digest == without.digest
    assert c.meta.doc == without.meta.doc

    assert {:ok, between} =
             compile(String.replace(@body, "\ncontract\n", "\n/* author-only */\ncontract\n"))

    assert between.meta.doc == c.meta.doc
  end

  test "comments cannot hide malformed trailing code or silently consume the rest" do
    for comment <- ["/* never closed", "/* one */ trailing", "/* outer /* nested */"] do
      assert {:error, %{code: :comment, line: 1}} = compile(comment <> "\n" <> @body)
    end
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
