defmodule ContractLab.HostInputsTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Engine, HostInputs}

  test "JSON host inputs preserve defaults, capabilities and typed room evidence" do
    assert {:ok, c} = ContractLab.load("contracts/v2/assistant-answer.band", :frontmatter)

    policy = %{
      "revision" => "r1",
      "fixed_roles" => %{"requester" => "r"},
      "principals" => %{
        "r" => %{"kind" => "agent", "active" => true, "permission" => "owner"},
        "a" => %{"kind" => "agent", "active" => true, "permission" => "participant"}
      }
    }

    config = %{
      "session" => "s",
      "bindings" => %{"requester" => "r", "assistant" => "a"},
      "now" => 0,
      "max_messages" => 2,
      "room_policy" => policy,
      "capabilities" => ["contract.replies/1"]
    }

    assert {:ok, _} = Engine.new(c, HostInputs.new_opts(c, config))

    assert HostInputs.context(%{"actor" => "r", "now" => 5, "room_policy" => policy})[
             :room_policy
           ].principals["r"].kind == :agent

    assert HostInputs.context(%{"room_policy" => %{"bad" => true}})[:room_policy] == :invalid

    assert HostInputs.context(%{
             "room_policy" => put_in(policy, ["principals", "r", "kind"], "unknown")
           })[:room_policy] == :invalid
  end
end
