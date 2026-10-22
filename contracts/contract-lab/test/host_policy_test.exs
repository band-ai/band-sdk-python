defmodule ContractLab.HostPolicyTest do
  # D-15 (H8): a missing room_policy fails closed on new, step and view. The
  # explicit unchecked mode is a start-time choice of the local reference
  # profile only; it is persisted and never accepted by a hosted adapter.
  use ExUnit.Case, async: true
  alias ContractLab.{AgentGate, Canonical, Checkpoint, Engine, HostInputs, StateCodec}

  defp compile(body, opts \\ []) do
    extensions = Keyword.get(opts, :extensions, [])
    roles = Keyword.get(opts, :roles, "[lead, engineer]")

    bytes =
      "---\nname: host-policy\ndescription: Host evidence is required.\nengine: 0.2.0-lab.1\nroles: #{roles}\nextensions: [#{Enum.join(extensions, ", ")}]\n---\n" <>
        body

    bytes = ContractLab.TestClock.ensure(bytes)

    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

    c
  end

  defp plain do
    compile("""
    type assignment
    type done
      assignment: ref(assignment)
    state new
      lead -> engineer: assignment() awaits working
        => done() & complete
         | @1h & stalled
    end complete
    end stalled
    """)
  end

  defp policy(overrides \\ %{}) do
    %{
      revision: "room-7",
      fixed_roles: %{},
      principals:
        Map.merge(
          %{
            "agent-lead" => %{kind: :agent, active: true, permission: "participant"},
            "agent-engineer" => %{kind: :agent, active: true, permission: "participant"}
          },
          overrides
        )
    }
  end

  defp bindings, do: %{"lead" => "agent-lead", "engineer" => "agent-engineer"}

  defp new(c, opts) do
    Engine.new(
      c,
      Keyword.merge(
        [
          session_id: "s",
          bindings: bindings(),
          now: 0,
          max_messages: 10,
          capabilities: c.card.extensions
        ],
        opts
      )
    )
  end

  defp msg(id, kind, payload \\ %{}),
    do: %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload}

  describe "fail closed without host evidence" do
    test "new without a room_policy is host_policy_required" do
      assert {:error, :host_policy_required} = new(plain(), [])
    end

    test "step and an exact retry without a room_policy are host_policy_required" do
      assert {:ok, s} = new(plain(), room_policy: policy())

      assert {%{status: :accepted}, s2} =
               Engine.step(s, msg("a", "assignment"),
                 actor: "agent-lead",
                 now: 1,
                 room_policy: policy()
               )

      assert {%{status: :rejected, reason: :host_policy_required}, %{version: 0}} =
               Engine.step(s, msg("a", "assignment"), actor: "agent-lead", now: 1)

      # The exact retry needs current authorization too, before any receipt lookup.
      assert {%{status: :rejected, reason: :host_policy_required}, %{version: 1}} =
               Engine.step(s2, msg("a", "assignment"), actor: "agent-lead", now: 2)
    end

    test "view without a room_policy is host_policy_required and projects nothing" do
      assert {:ok, s} = new(plain(), room_policy: policy())

      assert {{:error, :host_policy_required}, _} =
               Engine.view(s, actor: "agent-lead", now: 0)

      assert {:error, :host_policy_required} = AgentGate.project(s, "agent-lead", 0)
      assert {:ok, _, _} = AgentGate.project(s, "agent-lead", 0, policy())
    end

    test "a revoked member can neither step nor view" do
      assert {:ok, s} = new(plain(), room_policy: policy())

      assert {%{status: :accepted}, s} =
               Engine.step(s, msg("a", "assignment"),
                 actor: "agent-lead",
                 now: 1,
                 room_policy: policy()
               )

      revoked =
        policy(%{"agent-engineer" => %{kind: :agent, active: false, permission: "participant"}})

      assert {%{status: :rejected, reason: :room_access}, %{version: 1}} =
               Engine.step(s, msg("d", "done", %{"assignment" => "a"}),
                 actor: "agent-engineer",
                 now: 2,
                 room_policy: revoked
               )

      assert {{:error, :room_access}, _} =
               Engine.view(s, actor: "agent-engineer", now: 2, room_policy: revoked)
    end

    test "every bound principal must be present in the evidence" do
      missing = %{policy() | principals: Map.delete(policy().principals, "agent-engineer")}
      assert {:error, :room_access} = new(plain(), room_policy: missing)
    end

    test "a human binding is agent_binding_required" do
      human = policy(%{"agent-engineer" => %{kind: :human, active: true, permission: "owner"}})
      assert {:error, :agent_binding_required} = new(plain(), room_policy: human)
    end

    test "a fixed_roles mismatch is role_assignment" do
      fixed = %{policy() | fixed_roles: %{"lead" => "agent-engineer"}}
      assert {:error, :role_assignment} = new(plain(), room_policy: fixed)
    end

    test "unchecked is start-time only: a checked session never accepts it per call" do
      assert {:ok, s} = new(plain(), room_policy: policy())

      assert {%{status: :rejected, reason: :host_policy_required}, %{version: 0}} =
               Engine.step(s, msg("a", "assignment"),
                 actor: "agent-lead",
                 now: 1,
                 room_policy: :unchecked
               )

      assert {{:error, :host_policy_required}, _} =
               Engine.view(s, actor: "agent-lead", now: 1, room_policy: :unchecked)
    end
  end

  describe "member contribution controls (P2)" do
    @contributions """
    type reviewer: agent
    reviewers: list(reviewer)
    type request
    type review
      text: text(1..1000)
    state new
      lead -> reviewers: request() => reviewing
    state reviewing
      collect reports from reviewers -> lead @5m => summarizing
        reply review()
    state summarizing
    """

    defp contribution_session(room_policy) do
      c =
        compile(@contributions,
          roles: "[lead, reviewers]",
          extensions: ["room.contributions/1"]
        )

      principals =
        Map.new(~w(l sec gen), &{&1, %{kind: :agent, active: true, permission: "participant"}})

      trusted = %{revision: "room-7", fixed_roles: %{}, principals: principals}

      {:ok, s} =
        Engine.new(c,
          session_id: "s",
          now: 0,
          max_messages: 30,
          capabilities: ["room.contributions/1"],
          bindings: %{
            "lead" => "l",
            "reviewers" => Enum.map(~w(sec gen), &%{"name" => &1, "actor" => &1})
          },
          room_policy: room_policy || trusted
        )

      {%{status: :accepted}, s} =
        Engine.step(s, msg("start", "request"), actor: "l", now: 0, room_policy: trusted)

      {s, trusted}
    end

    defp stop_report do
      %{
        "id" => "stop-1",
        "session" => "s",
        "collection" => "reports",
        "entry" => 1,
        "member" => "reviewers/gen",
        "cause" => %{
          "type" => "provider/budget_exhausted",
          "reference" => "provider-event-7",
          "message" => "The assigned work has ended without a report."
        }
      }
    end

    test "stop_contribution without a room_policy is host_policy_required" do
      {s, trusted} = contribution_session(nil)

      assert {%{status: :rejected, reason: :host_policy_required}, ^s} =
               Engine.stop_contribution(s, stop_report(), actor: "gen", now: 1)

      assert {%{status: :contribution_stopped}, _} =
               Engine.stop_contribution(s, stop_report(),
                 actor: "gen",
                 now: 1,
                 room_policy: trusted
               )
    end

    test "host-authority controls are unaffected" do
      {s, _} = contribution_session(nil)

      assert {%{status: :contribution_stopped}, _} =
               Engine.fail_contribution(s, stop_report(), authority: :host, now: 1)
    end
  end

  describe "current source host-policy enforcement (P3)" do
    test "a current source gets no policy bypass" do
      {:ok, c} = ContractLab.load("contracts/assignment.band", :frontmatter)
      bound = Map.new(c.roles, &{&1, "agent-" <> &1})

      assert {:error, :host_policy_required} =
               Engine.new(c,
                 session_id: "s",
                 now: 0,
                 max_messages: 10,
                 inactivity: :accepted,
                 bindings: bound,
                 capabilities: c.card.extensions
               )

      principals =
        Map.new(
          Map.values(bound),
          &{&1, %{kind: :agent, active: true, permission: "participant"}}
        )

      human = %{
        revision: "room-7",
        fixed_roles: %{},
        principals: put_in(principals, [bound[hd(c.roles)], :kind], :human)
      }

      assert {:error, :agent_binding_required} =
               Engine.new(c,
                 session_id: "s",
                 now: 0,
                 max_messages: 10,
                 inactivity: :accepted,
                 bindings: bound,
                 room_policy: human,
                 capabilities: c.card.extensions
               )
    end
  end

  describe "explicit unchecked mode on the local reference profile" do
    test "is accepted at new, persisted, and visible on decisions and views" do
      assert {:ok, s} = new(plain(), room_policy: :unchecked)
      assert s.policy_mode == :unchecked

      assert {%{status: :accepted, policy: :unchecked}, s} =
               Engine.step(s, msg("a", "assignment"), actor: "agent-lead", now: 1)

      assert {{:ok, %{policy: :unchecked}}, _} = Engine.view(s, actor: "agent-lead", now: 1)

      json = s |> StateCodec.export() |> Canonical.encode() |> JSON.decode!()
      assert json["state"]["policy_mode"] == "unchecked"
      c = plain()
      assert {:ok, restored} = StateCodec.restore(c, json, 1, capabilities: c.card.extensions)
      assert restored.policy_mode == :unchecked
    end

    test "an exact retry returns the original exactly as first answered" do
      assert {:ok, s} = new(plain(), room_policy: :unchecked)
      assert {accepted, s} = Engine.step(s, msg("a", "assignment"), actor: "agent-lead", now: 1)

      assert {%{status: :duplicate, policy: :unchecked, original: ^accepted}, _} =
               Engine.step(s, msg("a", "assignment"), actor: "agent-lead", now: 2)
    end

    test "checked sessions carry no policy marker" do
      assert {:ok, s} = new(plain(), room_policy: policy())
      json = s |> StateCodec.export() |> Canonical.encode() |> JSON.decode!()
      refute Map.has_key?(json["state"], "policy_mode")

      assert {decision, _} =
               Engine.step(s, msg("a", "assignment"),
                 actor: "agent-lead",
                 now: 1,
                 room_policy: policy()
               )

      refute Map.has_key?(decision, :policy)
    end

    test "a forged policy marker on a checkpoint is rejected" do
      assert {:ok, s} = new(plain(), room_policy: policy())
      json = s |> StateCodec.export() |> Canonical.encode() |> JSON.decode!()
      forged = put_in(json, ["state", "policy_mode"], "checked")
      c = plain()

      assert {:error, :invalid_checkpoint} =
               StateCodec.restore(c, forged, 0, capabilities: c.card.extensions)
    end
  end

  describe "host JSON inputs (P4, P5, P7)" do
    @unchecked %{"mode" => "unchecked"}

    defp config(room_policy) do
      %{
        "session" => "s",
        "bindings" => bindings(),
        "now" => 0,
        "max_messages" => 10,
        "capabilities" => plain().card.extensions
      }
      |> then(&if(room_policy == :absent, do: &1, else: Map.put(&1, "room_policy", room_policy)))
    end

    test "the local reference profile accepts the explicit unchecked mode" do
      c = plain()

      assert {:ok, s} =
               Engine.new(c, HostInputs.new_opts(c, config(@unchecked), :local_reference))

      assert s.policy_mode == :unchecked
    end

    test "hosted adapters reject the unchecked mode, even in config" do
      c = plain()

      assert {:error, :invalid_room_policy} =
               Engine.new(c, HostInputs.new_opts(c, config(@unchecked)))

      assert {:error, :host_policy_required} =
               Engine.new(c, HostInputs.new_opts(c, config(:absent)))
    end

    test "per-call context never carries the unchecked mode" do
      assert Keyword.fetch!(HostInputs.context(%{"room_policy" => @unchecked}), :room_policy) ==
               :invalid
    end

    test "empty, mixed and unknown-mode policies are invalid_room_policy" do
      c = plain()

      mixed = %{
        "mode" => "unchecked",
        "revision" => "r",
        "fixed_roles" => %{},
        "principals" => %{}
      }

      for p <- [%{}, mixed, %{"mode" => "trusted"}, %{"mode" => "unchecked", "x" => 1}] do
        assert {:error, :invalid_room_policy} =
                 Engine.new(c, HostInputs.new_opts(c, config(p), :local_reference)),
               inspect(p)
      end
    end

    test "a hosted restore refuses a checkpoint created unchecked (P6)" do
      c = plain()
      assert {:ok, s} = new(c, room_policy: :unchecked)
      assert {:ok, packet} = Checkpoint.export(s)
      packet = packet |> Canonical.encode() |> JSON.decode!()
      opts = [capabilities: c.card.extensions]
      assert {:ok, _} = Checkpoint.restore(c, packet, 0, :local_reference, opts)
      assert {:error, :host_policy_required} = Checkpoint.restore(c, packet, 0, :hosted, opts)
    end
  end
end
