defmodule ContractLab.RoomEventActorQuotaTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, RoomEvents}

  @source """
  ---
  name: shared-room-notices
  description: Two senders share one room-event quota.
  engine: 0.2.0-lab.1
  roles: [a, b, lead]
  ---
  contract @30m => expired
  type notice
  type close
  state new
    lead -> {a,b}: close() => complete
  room
    event {a,b} -> lead: notice()
  end complete
  """

  defp room(policy) do
    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => @source,
               "sha256" => Canonical.hash(@source)
             })

    RoomEvents.new(
      c,
      [bindings: %{"a" => "a", "b" => "b", "lead" => "l"}, now: 0, window_ms: 1000] ++ policy
    )
  end

  defp notice(r, actor, id, now),
    do:
      RoomEvents.step(r, %{"id" => id, "kind" => "notice", "payload" => %{}},
        actor: actor,
        now: now
      )

  @room_wide [max_events: 4, max_mentions: 4, max_receipts: 6]

  test "per-actor limits keep one sender from exhausting the room's events and receipts" do
    assert {:ok, r} = room(@room_wide ++ [max_events_per_actor: 2, max_receipts_per_actor: 3])
    assert {%{status: :accepted}, r} = notice(r, "a", "a1", 0)
    assert {%{status: :accepted}, r} = notice(r, "a", "a2", 1)
    assert {%{status: :rejected, reason: :room_budget}, same} = notice(r, "a", "a3", 2)
    assert same.events_used == r.events_used
    # B still has its share of the window.
    assert {%{status: :accepted}, r} = notice(r, "b", "b1", 3)

    # Next window: A may send once more, then its retained receipts reach three.
    assert {%{status: :accepted}, r} = notice(r, "a", "a3", 1000)
    assert {%{status: :rejected, reason: :retention_limit}, same} = notice(r, "a", "a4", 1001)
    assert same.receipts == r.receipts
    assert {%{status: :accepted}, r} = notice(r, "b", "b2", 1002)
    # A's exact retry still answers from its receipt.
    assert {%{status: :duplicate}, _} = notice(r, "a", "a1", 1003)
  end

  test "without per-actor limits the room-wide values apply to each actor" do
    assert {:ok, r} = room(@room_wide)
    refute Map.has_key?(r.policy, :max_events_per_actor)

    r =
      Enum.reduce(1..4, r, fn i, r ->
        assert {%{status: :accepted}, r} = notice(r, "a", "a#{i}", i)
        r
      end)

    assert {%{status: :rejected, reason: :room_budget}, _} = notice(r, "b", "b1", 5)
  end

  test "a per-actor limit must be a positive integer" do
    for bad <- [0, -1, "2", nil] do
      assert {:error, :room_policy_required} = room(@room_wide ++ [max_events_per_actor: bad])
      assert {:error, :room_policy_required} = room(@room_wide ++ [max_receipts_per_actor: bad])
    end
  end

  # From Tessa's reproducer (tessa-rafael-security-20261014/m16): A's notices fan out to two
  # recipients, so two of them exhaust max_mentions = 4 although A is within its event share.
  @fan_out """
  ---
  name: independent-room-quota
  description: One sender fans out to two recipients.
  engine: 0.2.0-lab.1
  roles: [a, b, lead, witness]
  ---
  contract @30m => expired
  type notice
  type close
  state new
    lead -> {a,b}: close() => complete
  room
    event a -> {lead,witness}: notice()
    event b -> lead: notice()
  end complete
  """

  defp fan_out_room(policy) do
    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => @fan_out,
               "sha256" => Canonical.hash(@fan_out)
             })

    RoomEvents.new(
      c,
      [
        bindings: %{"a" => "a", "b" => "b", "lead" => "l", "witness" => "w"},
        now: 0,
        window_ms: 1000,
        max_events: 4,
        max_mentions: 4,
        max_receipts: 6,
        max_events_per_actor: 2,
        max_receipts_per_actor: 3
      ] ++ policy
    )
  end

  test "a per-actor mention share keeps one sender's fan-out from exhausting the room's mentions" do
    assert {:ok, r} = fan_out_room(max_mentions_per_actor: 3)
    assert {%{status: :accepted}, r} = notice(r, "a", "a1", 0)
    assert {%{status: :rejected, reason: :room_budget}, same} = notice(r, "a", "a2", 1)
    assert same.mentions_used == 2
    assert {%{status: :accepted}, r} = notice(r, "b", "b1", 2)
    assert r.mentions_used == 3

    # The share renews with the window, like the room-wide count.
    assert {%{status: :accepted}, _} = notice(r, "a", "a2", 1000)

    # Unset, the share is the room-wide max_mentions, as before.
    assert {:ok, r} = fan_out_room([])
    refute Map.has_key?(r.policy, :max_mentions_per_actor)
    assert {%{status: :accepted}, r} = notice(r, "a", "a1", 0)
    assert {%{status: :accepted}, r} = notice(r, "a", "a2", 1)
    assert {%{status: :rejected, reason: :room_budget}, _} = notice(r, "b", "b1", 2)

    for bad <- [0, "3", nil] do
      assert {:error, :room_policy_required} = fan_out_room(max_mentions_per_actor: bad)
    end
  end
end
