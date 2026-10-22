defmodule ContractLab.RoomNewBoundsTest do
  @moduledoc "Sofia set Q Q-L6: room creation bounds now and every limit to the safe-integer range."
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, RoomEvents}

  @safe 9_007_199_254_740_991
  @source """
  ---
  name: room-bounds
  description: Room creation bounds.
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
  @policy [window_ms: 1000, max_events: 4, max_mentions: 4, max_receipts: 8]

  defp room(opts) do
    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => @source,
               "sha256" => Canonical.hash(@source)
             })

    RoomEvents.new(
      c,
      Keyword.merge([bindings: %{"a" => "a", "b" => "b", "lead" => "l"}, now: 0] ++ @policy, opts)
    )
  end

  test "now must be a safe nonnegative integer" do
    assert {:ok, _} = room(now: @safe)
    assert {:error, :invalid_time} = room(now: @safe + 1)
    assert {:error, :invalid_time} = room(now: -1)
  end

  # Omar set Q Q-L8: a room step bounds its time the same way.
  test "a room step's now must be a safe nonnegative integer" do
    assert {:ok, room} = room([])
    message = %{"id" => "n1", "kind" => "notice", "payload" => %{}}

    assert {%{status: :rejected, reason: :invalid_time}, _} =
             RoomEvents.step(room, message, now: @safe + 1, actor: "a")
  end

  test "every limit must be a positive safe integer" do
    assert {:ok, _} = room(max_events: @safe)

    for key <- [:window_ms, :max_events, :max_mentions, :max_receipts, :max_events_per_actor] do
      assert {:error, :room_policy_required} = room([{key, @safe + 1}])
    end
  end
end
