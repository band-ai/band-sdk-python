defmodule ContractLab.DocumentationSigilTest do
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @body """
  contract
  type value
    text: text(100)
  state new
    sender -> receiver: value() => complete
  end complete
  """

  test "raw heredocs preserve backslashes, quotes and interpolation-shaped text" do
    doc = ~S|Keep "quotes", C:\work\notes, \n and #{not_a_call()}. Must remain data.|
    assert {:ok, c} = compile("~s\"\"\"\n" <> doc <> "\n\"\"\"\n" <> @body)
    # A block's text ends at its last content line (Ada's choice A, 10-15).
    assert c.meta.doc == doc
    assert c.meta.line == 1
    assert {:ok, view} = ContractLab.for_role(c, "sender")
    assert view.contract.meta == c.meta
  end

  test "single-line delimiters preserve exact content, with no implicit newline" do
    for {open, close} <- [
          {"/", "/"},
          {"|", "|"},
          {"{", "}"},
          {"[", "]"},
          {"(", ")"},
          {"<", ">"},
          {"\"", "\""},
          {"'", "'"}
        ] do
      doc = ~S( Keep \n and 🚀. )
      assert {:ok, c} = compile("~s" <> open <> doc <> close <> "\n" <> @body)
      assert c.meta.doc == doc
    end

    assert {:ok, c} = compile(~S(~s|a\|b|) <> "\n" <> @body)
    assert c.meta.doc == ~S(a\|b)
  end

  test "raw heredocs remove closing indentation and preserve line endings" do
    literal = "  ~s\"\"\"\r\n  One\\n\r\n    Two\r\n X\r\n\r\n  \"\"\"\r\n"
    assert {:ok, c} = compile(literal <> @body)
    assert c.meta.doc == "One\\n\r\n  Two\r\nX\r\n"
    assert {:ok, empty} = compile("~s\"\"\"\n\"\"\"\n" <> @body)
    assert empty.meta.doc == ""
  end

  test "raw docs attach to fields, rules and duties and preserve source lines" do
    body = ~S'''
    type value
      ~s|A literal \n is not a newline.|
      text: text(100)
    state new
      ~s|Send the text within a minute.|
      sender -> receiver: value() @1m => complete | stalled
    end complete
    end stalled
    '''

    assert {:ok, c} = compile(body)
    assert {:ok, value} = ContractLab.query(c, {:type, "value"})
    assert value.fields["text"].meta == %{doc: ~S(A literal \n is not a newline.), line: 2}
    assert hd(c.states["new"].rules).meta == %{doc: "Send the text within a minute.", line: 5}
    assert hd(c.states["new"].deadlines).meta == hd(c.states["new"].rules).meta
  end

  test "equivalent docs keep behavior but change the exact file pin" do
    assert {:ok, a} = compile("\"\"\"\nSame text.\n\"\"\"\n" <> @body)
    assert {:ok, b} = compile("~s\"\"\"\nSame text.\n\"\"\"\n" <> @body)
    assert a.meta == b.meta
    assert a.digest == b.digest
    refute a.source_digest == b.source_digest
  end

  test "malformed literals, modifiers and orphan docs fail closed" do
    for literal <- [
          "~s|open",
          "~s|text|s",
          "~s|text| trailing",
          "~s{text}more}",
          "~s!text!",
          "~s\"\"\"\nopen"
        ] do
      assert {:error, %{code: :documentation, line: 1}} = compile(literal <> "\n" <> @body)
    end

    assert {:error, %{code: :orphan_documentation}} = compile(@body <> "~s|orphan|\n")
    assert {:error, %{code: :orphan_documentation}} = compile("~s|one|\n~s|two|\n" <> @body)
  end

  test "raw contents neither execute nor intern supplied names" do
    name = "untrusted_sigil_doc_" <> Integer.to_string(System.unique_integer([:positive]))
    assert_raise ArgumentError, fn -> String.to_existing_atom(name) end
    doc = "\#{" <> name <> "()}\n\"\"\"; [:" <> name <> "]; \"\"\"\n"
    assert {:ok, c} = compile("~s\"\"\"\n" <> doc <> "\"\"\"\n" <> @body)
    assert c.meta.doc == String.replace_suffix(doc, "\n", "")
    assert_raise ArgumentError, fn -> String.to_existing_atom(name) end
  end

  defp compile(body, engine \\ "0.2.0-lab.1")

  defp compile(body, "0.2.0-lab.1") do
    source =
      ContractLab.TestClock.ensure(
        "---\nname: literal-docs\ndescription: Raw contract documentation.\nengine: 0.2.0-lab.1\nroles: [sender, receiver]\n---\n" <>
          body
      )

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => source,
      "sha256" => Canonical.hash(source)
    })
  end
end
