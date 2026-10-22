defmodule ContractLab.BlockDocTextTest do
  # Ada's choice A (10-15): a block's text ends at its last content line. The line break before
  # the closing """ is layout, so a one-line prompt and its block form document alike, and the
  # formatter's rewrite of one into the other keeps the compiled definition.
  use ExUnit.Case, async: true

  @header "---\nname: block-doc\ndescription: Block doc text.\nengine: 0.2.0-lab.1\nroles: [lead, worker]\n---\n"
  @body "contract @1h => expired\ntype offer\nstate new\n  lead -> worker: offer() => done\nend done\n"

  defp contract_doc(literal) do
    source = @header <> literal <> @body

    {:ok, compiled} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => source,
        "sha256" => ContractLab.Canonical.hash(source)
      })

    compiled.meta.doc
  end

  test "a block and a one-line prompt decode to the same text" do
    assert contract_doc(~s("Plan the work."\n)) == "Plan the work."
    assert contract_doc(~s("""\nPlan the work.\n"""\n)) == "Plan the work."
    assert contract_doc(~s(~s"Plan the work."\n)) == "Plan the work."
    assert contract_doc(~s(~s"""\nPlan the work.\n"""\n)) == "Plan the work."
  end

  test "inner line breaks, indentation and a final blank line stay" do
    assert contract_doc(~s(  """\n  First.\n    Second.\n  """\n)) == "First.\n  Second."
    assert contract_doc(~s("""\nFirst.\n\n"""\n)) == "First.\n"
    assert contract_doc(~s(~s"""\nC:\\work\n\n"""\n)) == "C:\\work\n"
  end

  test "escapes decode as before, and a continuation before the delimiter adds nothing" do
    assert contract_doc(~S("""
           Say \"hi\"\tnow.
           """) <> "\n") == "Say \"hi\"\tnow."

    assert contract_doc(~S("""
           Joined \
           """) <> "\n") == "Joined "
  end
end
