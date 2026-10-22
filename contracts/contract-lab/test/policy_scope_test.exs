defmodule ContractLab.PolicyScopeTest do
  # Every bound principal must be an active member on each call (D-15a P11), so impl M24
  # is obsolete. M7: an exact resend is answered from its receipt after the host evidence
  # check (D-15 P1) and before current access is rechecked, because a current loss of
  # access is not evidence that the original was rejected.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  defp compile do
    bytes = """
    ---
    name: policy-scope
    description: Three agents share one room.
    engine: 0.2.0-lab.1
    roles: [lead, engineer, reviewer]
    extensions: [band.permissions/1]
    ---
    contract @1h => expired
    require lead band.permission admin
    type assignment
    type note
    state new
      lead -> engineer: assignment() => working
    state working
      event lead -> engineer: note()
      event engineer -> lead: note()
    """

    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

    c
  end

  defp policy(changes \\ %{}) do
    principals = %{
      "agent-lead" => %{kind: :agent, active: true, permission: "admin"},
      "agent-engineer" => %{kind: :agent, active: true, permission: "participant"},
      "agent-reviewer" => %{kind: :agent, active: true, permission: "participant"}
    }

    principals =
      Enum.reduce(changes, principals, fn {actor, fields}, acc ->
        Map.update!(acc, actor, &Map.merge(&1, fields))
      end)

    %{revision: "room-1", fixed_roles: %{}, principals: principals}
  end

  defp start do
    {:ok, s} =
      Engine.new(compile(),
        session_id: "s",
        now: 0,
        max_messages: 10,
        capabilities: ["band.permissions/1"],
        bindings: %{
          "lead" => "agent-lead",
          "engineer" => "agent-engineer",
          "reviewer" => "agent-reviewer"
        },
        room_policy: policy()
      )

    s
  end

  defp msg(id, kind, payload \\ %{}),
    do: %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload}

  defp step(s, actor, m, p, now \\ 1),
    do: Engine.step(s, m, actor: actor, now: now, room_policy: p)

  describe "D-15a P11: every bound principal is rechecked on each call (M24 obsolete)" do
    test "an inactive bound member refuses another member's new proposal and view" do
      s = start()
      reviewer_gone = policy(%{"agent-reviewer" => %{active: false}})

      assert {%{status: :rejected, reason: :room_access}, _} =
               step(s, "agent-lead", msg("a", "assignment"), reviewer_gone)

      assert {{:error, :room_access}, _} =
               Engine.view(s, actor: "agent-lead", now: 1, room_policy: reviewer_gone)
    end

    test "the caller's own revocation still refuses its new proposals and views" do
      s = start()
      demoted = policy(%{"agent-lead" => %{permission: "participant"}})
      gone = policy(%{"agent-lead" => %{active: false}})

      assert {%{status: :rejected, reason: :room_permission}, _} =
               step(s, "agent-lead", msg("a", "assignment"), demoted)

      assert {%{status: :rejected, reason: :room_access}, _} =
               step(s, "agent-lead", msg("a", "assignment"), gone)

      assert {{:error, :room_access}, _} =
               Engine.view(s, actor: "agent-lead", now: 1, room_policy: gone)
    end

    test "every binding is still validated when the session is created" do
      gone = policy(%{"agent-reviewer" => %{active: false}})

      assert {:error, :room_access} =
               Engine.new(compile(),
                 session_id: "s",
                 now: 0,
                 max_messages: 10,
                 capabilities: ["band.permissions/1"],
                 bindings: %{
                   "lead" => "agent-lead",
                   "engineer" => "agent-engineer",
                   "reviewer" => "agent-reviewer"
                 },
                 room_policy: gone
               )
    end
  end

  describe "M7: an exact resend is answered from its receipt" do
    setup do
      {%{status: :accepted} = accepted, s} =
        step(start(), "agent-lead", msg("a", "assignment"), policy())

      %{s: s, accepted: accepted}
    end

    test "after a demotion or deactivation the exact resend is a duplicate", %{s: s, accepted: a} do
      for changes <- [%{permission: "participant"}, %{active: false}] do
        p = policy(%{"agent-lead" => changes})

        assert {%{status: :duplicate, original: ^a}, _} =
                 step(s, "agent-lead", msg("a", "assignment"), p, 2)
      end
    end

    test "after the lead role is reassigned the exact resend is a duplicate", %{
      s: s,
      accepted: a
    } do
      moved = %{policy() | fixed_roles: %{"lead" => "agent-engineer"}}

      assert {%{status: :duplicate, original: ^a}, _} =
               step(s, "agent-lead", msg("a", "assignment"), moved, 2)

      assert {%{status: :rejected, reason: :role_assignment}, _} =
               step(s, "agent-lead", msg("b", "note"), moved, 2)
    end

    test "a new proposal by the revoked caller is still refused", %{s: s} do
      gone = policy(%{"agent-lead" => %{active: false}})

      assert {%{status: :rejected, reason: :room_access}, _} =
               step(s, "agent-lead", msg("b", "note"), gone, 2)

      # A non-exact reuse of the ID is refused for access, revealing nothing about the receipt.
      assert {%{status: :rejected, reason: :room_access}, _} =
               step(s, "agent-lead", msg("a", "assignment", %{"x" => 1}), gone, 2)
    end

    test "an inactive bound member does not turn the exact resend into a rejection", %{
      s: s,
      accepted: a
    } do
      reviewer_gone = policy(%{"agent-reviewer" => %{active: false}})

      assert {%{status: :duplicate, original: ^a}, _} =
               step(s, "agent-lead", msg("a", "assignment"), reviewer_gone, 2)
    end

    test "malformed host evidence is refused before any receipt lookup", %{s: s} do
      assert {%{status: :rejected, reason: :invalid_room_policy}, _} =
               step(s, "agent-lead", msg("a", "assignment"), %{revision: ""}, 2)
    end

    test "host evidence is still required before any receipt lookup (D-15 P1)", %{s: s} do
      assert {%{status: :rejected, reason: :host_policy_required}, _} =
               Engine.step(s, msg("a", "assignment"), actor: "agent-lead", now: 2)
    end

    test "a float payload under an existing ID is a payload rejection, not a crash or an oracle",
         %{s: s} do
      float = %{"x" => 1.5}
      gone = policy(%{"agent-lead" => %{active: false}})

      # N-4: the receipt lookup must not digest a message the encoder would refuse. The
      # answer is the same for an existing and a fresh ID, and nothing is admitted.
      for {p, reason} <- [{policy(), :payload}, {gone, :room_access}], id <- ["a", "fresh"] do
        assert {%{status: :rejected, reason: ^reason}, next} =
                 step(s, "agent-lead", msg(id, "assignment", float), p, 2)

        assert {next.version, next.receipts, next.facts} == {s.version, s.receipts, s.facts}
      end
    end

    test "another actor cannot read the receipt by resending the same envelope", %{s: s} do
      assert {%{status: :rejected, reason: :id_conflict}, _} =
               step(s, "agent-engineer", msg("a", "assignment"), policy(), 2)
    end
  end
end
