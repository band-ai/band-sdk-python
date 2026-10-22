defmodule ContractLab.RoomPolicyTest do
  use ExUnit.Case, async: true
  alias ContractLab.{AgentGate, Canonical, Engine, RoomPolicy}

  defp compile(extension \\ "band.permissions/1") do
    bytes = """
    ---
    name: room-policy
    description: A room chooses the lead agent.
    engine: 0.2.0-lab.1
    roles: [lead, engineer]
    extensions: [#{extension}]
    ---
    require lead band.permission admin
    type assignment
    state new
      lead -> engineer: assignment() => complete
    end complete
    """

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp policy do
    %{
      revision: "room-policy-7",
      fixed_roles: %{"lead" => "agent-lead"},
      principals: %{
        "agent-lead" => %{kind: :agent, active: true, permission: "admin"},
        "agent-engineer" => %{kind: :agent, active: true, permission: "participant"},
        "human-owner" => %{kind: :human, active: true, permission: "owner"}
      }
    }
  end

  defp bindings, do: %{"lead" => "agent-lead", "engineer" => "agent-engineer"}

  defp new(c, p, b \\ bindings()),
    do:
      Engine.new(c,
        session_id: "s",
        bindings: b,
        now: 0,
        max_messages: 2,
        capabilities: ["band.permissions/1"],
        room_policy: p
      )

  test "humans and agent-to-human exchanges are outside contract admission" do
    assert RoomPolicy.lane(:human, :agent) == :conversation
    assert RoomPolicy.lane(:agent, :human) == :conversation
    assert RoomPolicy.lane(:human, :human) == :conversation
    assert RoomPolicy.lane(:agent, :agent) == :agent_exchange
    assert {:ok, c} = compile()

    assert {:error, :agent_binding_required} =
             new(c, policy(), %{bindings() | "lead" => "human-owner"})
  end

  test "room policy fixes lead to one agent without putting that identity in the contract" do
    assert {:ok, c} = compile()
    assert {:ok, _} = new(c, policy())

    assert {:error, :role_assignment} =
             new(c, policy(), %{"lead" => "agent-engineer", "engineer" => "agent-lead"})

    refute Canonical.encode(c) =~ "agent-lead"
    assert {:ok, definition} = ContractLab.for_role(c, "lead")
    assert definition.contract.extensions.permissions == %{"lead" => "admin"}
  end

  test "permission is rechecked from current host evidence before every proposal" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c, policy())
    message = %{"id" => "a", "session" => "s", "kind" => "assignment", "payload" => %{}}

    assert {%{status: :rejected, reason: :host_policy_required}, ^s} =
             Engine.step(s, message, actor: "agent-lead", now: 0)

    revoked = put_in(policy(), [:principals, "agent-lead", :permission], "participant")

    assert {%{status: :rejected, reason: :room_permission}, ^s} =
             Engine.step(s, message, actor: "agent-lead", now: 0, room_policy: revoked)

    assert {%{status: :accepted}, _} =
             Engine.step(s, message, actor: "agent-lead", now: 0, room_policy: policy())
  end

  test "extension support is explicit and unknown versions are refused" do
    assert {:error, %{code: :field_value, line: 6, detail: "extensions"}} =
             compile("band.permissions/2")

    assert {:ok, c} = compile()

    assert {:error, :unsupported_extension} =
             Engine.new(c,
               session_id: "s",
               bindings: bindings(),
               now: 0,
               max_messages: 2,
               room_policy: policy()
             )
  end

  test "advisory preflight checks structure but never supplies host permission" do
    assert {:ok, c} = compile()
    assert {:ok, s} = new(c, policy())
    assert {:ok, projection, _} = AgentGate.project(s, "agent-lead", 0, policy())

    message = %{
      "id" => "a",
      "session" => "s",
      "kind" => "assignment",
      "payload" => %{},
      "expected_version" => 0
    }

    assert %{"status" => "accepted"} = AgentGate.check(c, projection, message, "agent-lead", 0)

    assert {%{status: :rejected, reason: :host_policy_required}, ^s} =
             Engine.step(s, message, actor: "agent-lead", now: 0)
  end
end
