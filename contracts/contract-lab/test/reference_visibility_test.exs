defmodule ContractLab.ReferenceVisibilityTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  @source """
  ---
  name: reference-visibility
  description: A reference must name a fact its sender authored or received.
  engine: 0.2.0-lab.1
  roles: [author, reviewer, observer]
  ---
  contract @30m => expired
  type draft
    text: text(1..100)
  type comment
    source: ref(draft)
  type verdict
    source: ref(draft)
  state new
    author -> reviewer: draft() as d awaits reviewing
      => verdict(source=$d) & done
       | @10m & stalled
  state reviewing
    observer -> author: comment(source=$d) => done
  end done
  end stalled
  """

  defp start do
    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => @source,
               "sha256" => Canonical.hash(@source)
             })

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: 0,
               max_messages: 10,
               bindings: %{"author" => "au", "reviewer" => "rv", "observer" => "ob"},
               room_policy: :unchecked,
               capabilities: c.card.extensions
             )

    message = %{"id" => "d1", "session" => "s", "kind" => "draft", "payload" => %{"text" => "x"}}
    assert {%{status: :accepted}, s} = Engine.step(s, message, actor: "au", now: 1)
    s
  end

  defp send(s, actor, id, kind) do
    message = %{"id" => id, "session" => "s", "kind" => kind, "payload" => %{"source" => "d1"}}
    Engine.step(s, message, actor: actor, now: 2)
  end

  test "a participant cannot reference a bound fact it neither authored nor received" do
    s = start()
    # The observer can read the bound ID d1 in its checkpoint but never saw the draft.
    assert {%{status: :rejected, reason: :reference}, same} = send(s, "ob", "c1", "comment")
    assert same.version == s.version
    assert {{:ok, view}, _} = Engine.view(s, actor: "ob", now: 2, room_policy: :unchecked)
    refute Map.has_key?(view.facts, "d1")

    # Local preflight on the redacted checkpoint reaches the same decision.
    {:ok, projection, _} = ContractLab.AgentGate.project(s, "ob", 2)

    comment = %{
      "id" => "c1",
      "session" => "s",
      "kind" => "comment",
      "payload" => %{"source" => "d1"},
      "expected_version" => s.version
    }

    assert ContractLab.AgentGate.check(s.contract, projection, comment, "ob", 2)["reason"] ==
             "reference"

    assert {%{status: :accepted}, next} = send(s, "rv", "v1", "verdict")
    assert next.outcome == "done"
  end
end
