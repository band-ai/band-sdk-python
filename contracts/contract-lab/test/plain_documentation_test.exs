defmodule ContractLab.PlainDocumentationTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @body ~S'''
  "Coordinate useful work with clear evidence."
  contract
  """
  Work independently. Cite evidence and state uncertainty.
  Use your assigned specialty; name anything you could not check.
  """
  role reviewer
  type request
  "Findings and the remaining uncertainty."
  type summary
    "Explain the evidence supporting the findings."
    text: text(1..100)
  state new
    lead -> reviewer: request() => summarizing
  "Combine supported findings and identify missing coverage."
  state summarizing
    "Send the findings, their evidence and the remaining gaps."
    lead -> reviewer: summary() => complete
  "The report arrived; its quality still needs evaluation."
  end complete
  '''

  test "plain prompt metadata attaches to the next item and reaches the agent view" do
    assert {:ok, c} = compile(@body)
    assert c.meta == %{doc: "Coordinate useful work with clear evidence.", line: 1}
    assert {:ok, role} = ContractLab.query(c, {:role, "reviewer"})

    assert role.meta == %{
             doc:
               "Work independently. Cite evidence and state uncertainty.\n" <>
                 "Use your assigned specialty; name anything you could not check.",
             line: 3
           }

    assert {:ok, summary} = ContractLab.query(c, {:type, "summary"})
    assert summary.meta.doc == "Findings and the remaining uncertainty."
    assert summary.fields["text"].meta.doc == "Explain the evidence supporting the findings."
    assert c.states["new"].meta.doc == nil
    assert hd(c.states["new"].rules).meta.doc == nil
    assert c.states["summarizing"].meta.line == 15
    assert hd(c.states["summarizing"].rules).meta.line == 17
    assert c.terminals["complete"].meta.doc =~ "quality still needs evaluation"

    assert {:ok, s} = start(c)
    assert {%{status: :accepted}, s} = send_message(s, "request", %{})
    assert {{:ok, view}, _} = Engine.view(s, actor: "r", now: 0)
    assert view.definition.role == role
    assert view.definition.contract.meta == c.meta
    assert view.definition.types["summary"].fields["text"].meta == summary.fields["text"].meta

    assert hd(view.definition.states["summarizing"].rules).meta.doc ==
             "Send the findings, their evidence and the remaining gaps."
  end

  test "one-line strings decode escapes, preserve Unicode and add no newline" do
    literal = ~S("Keep \"quotes\", C:\\work, \n and \u{1F680}.")
    assert {:ok, c} = compile(literal <> "\n" <> bare_body())
    assert c.meta.doc == "Keep \"quotes\", C:\\work, \n and 🚀."
    assert {:ok, empty} = compile("\"\"\n" <> bare_body())
    assert empty.meta.doc == ""
  end

  test "guidance does not become a room predicate or alter the behavior digest" do
    assert {:ok, a} = compile(@body)

    assert {:ok, b} =
             compile(
               String.replace(
                 @body,
                 "Send the findings, their evidence and the remaining gaps.",
                 "Discuss a business rule that the room cannot check."
               )
             )

    assert a.digest == b.digest
    refute a.source_digest == b.source_digest

    for c <- [a, b] do
      assert {:ok, s} = start(c)
      assert {%{status: :accepted}, s} = send_message(s, "request", %{})

      assert {%{status: :accepted, outcome: "complete"}, _} =
               send_message(s, "summary", %{"text" => "A structurally valid report."})
    end
  end

  test "plain and raw one-line forms retain equivalent metadata and behavior" do
    assert {:ok, plain} = compile("\"Keep useful evidence.\"\n" <> bare_body())
    assert {:ok, raw} = compile("~s|Keep useful evidence.|\n" <> bare_body())
    assert plain.meta == raw.meta
    assert plain.digest == raw.digest
    refute plain.source_digest == raw.source_digest
  end

  test "invalid, executable-looking and orphan strings fail closed without interning names" do
    name = "untrusted_plain_doc_" <> Integer.to_string(System.unique_integer([:positive]))
    assert_raise ArgumentError, fn -> String.to_existing_atom(name) end

    for literal <- [
          ~S("never closed),
          ~S("one" "two"),
          ~S("one" <> "two"),
          ~S("one" role reviewer),
          ~S|"one" # trailing comment|,
          ~S|"one"; send(self(), :message)|,
          ~S("bad \xFF"),
          "\"\#{" <> name <> "()}\"",
          "\"data\"; :" <> name
        ] do
      assert {:error, %{code: :documentation, line: 1}} = compile(literal <> "\n" <> bare_body())
    end

    assert_raise ArgumentError, fn -> String.to_existing_atom(name) end
    refute_received :message
    assert {:error, %{code: :orphan_documentation}} = compile("\"a\"\n\"b\"\n" <> bare_body())
    assert {:error, %{code: :orphan_documentation}} = compile(bare_body() <> "\"orphan\"\n")
  end

  defp bare_body do
    "contract\ntype request\nstate new\n  lead -> reviewer: request() => complete\nend complete\n"
  end

  defp compile(body) do
    bytes =
      "---\nname: plain-prompts\ndescription: Retain agent guidance.\nengine: 0.2.0-lab.1\nroles: [lead, reviewer]\n---\n" <>
        body

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp start(c),
    do:
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 2,
        bindings: %{"lead" => "l", "reviewer" => "r"},
        room_policy: :unchecked
      )

  defp send_message(s, kind, payload),
    do:
      Engine.step(s, %{"id" => kind, "session" => "s", "kind" => kind, "payload" => payload},
        actor: "l",
        now: 0
      )
end
