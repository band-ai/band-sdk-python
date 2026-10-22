defmodule ContractLab.GuidancePlacementDocTest do
  # DF-15: spec/multi-agent-skills.md must say which guidance travels with an
  # accepted message, and its example must behave as the prose says.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @doc_path Path.expand("../../spec/multi-agent-skills.md", __DIR__)
  @external_resource @doc_path

  test "the guidance-placement example: only the rule's string travels with the message" do
    section =
      case Regex.run(~r/^## Where guidance travels\n(.*?)(?=^## )/ms, File.read!(@doc_path)) do
        [_, body] -> body
        nil -> flunk("spec/multi-agent-skills.md has no \"## Where guidance travels\" section")
      end

    assert section =~ "`metadata.doc`"
    assert section =~ "`view.definition`"
    assert [_, example] = Regex.run(~r/```band\n(.*?)```/s, section)
    assert {:ok, c} = compile(example)

    [rule] = c.states["new"].rules
    assert is_binary(rule.meta.doc)
    assert is_binary(c.states["new"].meta.doc)

    assert {:ok, s} = start(c)

    assert {%{status: :accepted, metadata: metadata}, s} =
             Engine.step(
               s,
               %{
                 "id" => "m1",
                 "session" => "s",
                 "kind" => "request",
                 "payload" => %{"text" => "diff"}
               },
               actor: "l",
               now: 0
             )

    assert metadata == rule.meta
    assert metadata.doc == rule.meta.doc
    refute metadata.doc == c.states["new"].meta.doc

    assert {{:ok, view}, _} = Engine.view(s, actor: "r", now: 0)
    assert view.definition.states["new"].meta.doc == c.states["new"].meta.doc
    assert hd(view.definition.states["new"].rules).meta.doc == rule.meta.doc

    # A reply alternative is a rule too: the review carries its alternative's string.
    [reply] = c.states["reviewing"].rules
    assert is_binary(reply.meta.doc)

    assert {%{status: :accepted, metadata: reply_metadata}, _} =
             Engine.step(
               s,
               %{
                 "id" => "m2",
                 "session" => "s",
                 "kind" => "review",
                 "payload" => %{"request" => "m1", "text" => "looks fine"}
               },
               actor: "r",
               now: 1
             )

    assert reply_metadata == reply.meta
    refute reply_metadata.doc == c.states["reviewing"].meta.doc
  end

  defp compile(body) do
    bytes =
      "---\nname: guidance-placement\ndescription: Show where guidance travels.\nengine: 0.2.0-lab.1\nroles: [lead, reviewer]\n---\n" <>
        body

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
        max_messages: 4,
        bindings: %{"lead" => "l", "reviewer" => "r"},
        room_policy: :unchecked,
        capabilities: c.card.extensions
      )
end
