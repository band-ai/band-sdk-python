defmodule ContractLab.TransportTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Wire, RoomEvents, Engine}
  @contracts Path.expand("../contracts", __DIR__)

  test "transport-supplied fields round-trip without duplication" do
    message = %{"id" => "id-1", "session" => "s1", "kind" => "ping", "payload" => %{}}
    transport = Map.take(message, ["id", "session"])
    assert {:ok, bytes} = Wire.encode(message, transport: transport)
    refute bytes =~ "session"
    assert {:ok, ^message} = Wire.decode(bytes, transport: transport)
    assert {:ok, full} = Wire.encode(message)
    assert {:ok, ^message} = Wire.decode(full)
  end

  for {json, reason} <- [
        {~s({"id":"a","id":"b","kind":"ping","payload":{}}), :duplicate_key},
        {~s({"id":"a","kind":"ping","payload":{"x":1,"x":2}}), :duplicate_key},
        {~s({"id":"a","kind":"ping","payload":{"x":1.0}}), :unsupported_number},
        {~s({"id":"a","kind":"ping","payload":{"x":9007199254740992}}), :unsupported_number},
        {~s({"id":"a","kind":"ping","payload":{}} trailing), :json},
        {~s({"id":"a","kind":"ping","payload":{},"actor":"spoof"}), :envelope},
        {~s([]), :envelope}
      ] do
    test "strict wire rejects #{inspect(reason)} in #{json}" do
      assert {:error, unquote(reason)} = Wire.decode(unquote(json))
    end
  end

  test "conflicting carrier fields and arbitrary carrier mappings are rejected" do
    bytes = ~s({"id":"body","session":"s1","kind":"ping","payload":{}})
    assert {:error, :transport_conflict} = Wire.decode(bytes, transport: %{"id" => "transport"})
    assert {:error, :transport_fields} = Wire.decode(bytes, transport: %{"actor" => "claimed"})
    assert {:error, :message_size} = Wire.decode(String.duplicate(" ", 100), max_bytes: 20)
  end

  defp room(opts \\ []) do
    {:ok, c} = ContractLab.load(Path.join(@contracts, "room-events.band"))

    {:ok, r} =
      RoomEvents.new(
        c,
        Keyword.merge(
          [
            bindings: %{"sender" => "sender-id", "receiver" => "receiver-id"},
            now: 0,
            window_ms: 1000,
            max_events: 2,
            max_mentions: 2,
            max_receipts: 100
          ],
          opts
        )
      )

    r
  end

  defp notice(id),
    do: %{"id" => id, "kind" => "notice", "payload" => %{"topic" => "build", "urgent" => false}}

  test "room event needs no session and duplicate delivery does not recharge its window" do
    r = room()
    {d, r} = RoomEvents.step(r, notice("n"), actor: "sender-id", now: 0)
    assert d.status == :accepted
    assert d.recipients == ["receiver-id"]
    refute Map.has_key?(r, :session)
    {d, r} = RoomEvents.step(r, notice("n"), actor: "sender-id", now: 200)
    assert d.status == :duplicate
    assert r.events_used == 1
    assert r.mentions_used == 1
  end

  test "room quota resets at the window boundary while deduplication persists" do
    r = room(max_events: 1, max_mentions: 1)
    {_, r} = RoomEvents.step(r, notice("one"), actor: "sender-id", now: 0)
    {d, r} = RoomEvents.step(r, notice("two"), actor: "sender-id", now: 999)
    assert d.reason == :room_budget
    {d, r} = RoomEvents.step(r, notice("two"), actor: "sender-id", now: 1000)
    assert d.status == :accepted
    {d, r} = RoomEvents.step(r, notice("one"), actor: "sender-id", now: 1000)
    assert d.status == :duplicate
    assert r.events_used == 1
  end

  test "room-event authority, schema, conflicts and retention bounds are enforced" do
    r = room(max_receipts: 1)
    {d, ^r} = RoomEvents.step(r, notice("one"), actor: "intruder", now: 0)
    assert d.reason == :unauthorized

    {d, ^r} =
      RoomEvents.step(r, put_in(notice("one"), ["payload", "urgent"], "false"),
        actor: "sender-id",
        now: 0
      )

    assert d.reason == :payload
    {_, r} = RoomEvents.step(r, notice("one"), actor: "sender-id", now: 0)

    {d, _} =
      RoomEvents.step(r, put_in(notice("one"), ["payload", "topic"], "review"),
        actor: "sender-id",
        now: 0
      )

    assert d.reason == :id_conflict
    {d, _} = RoomEvents.step(r, notice("two"), actor: "sender-id", now: 1000)
    assert d.reason == :retention_limit
  end

  test "room events cannot rescue or advance a separate expired session" do
    r = room()

    {:ok, s} =
      Engine.new(r.contract,
        session_id: "s1",
        bindings: r.bindings,
        now: 0,
        inactivity: :accepted,
        max_messages: 10,
        room_policy: :unchecked
      )

    expiry_at = r.contract.timeout.after_ms
    assert {:ok, expired} = Engine.tick(s, expiry_at)
    assert expired.outcome == "expired"
    {d, _} = RoomEvents.step(r, notice("one"), actor: "sender-id", now: expiry_at)
    assert d.status == :accepted

    {d, still_expired} =
      Engine.step(expired, Map.put(notice("one"), "session", "s1"),
        actor: "sender-id",
        now: expiry_at
      )

    assert d.reason == :terminal
    assert still_expired.status == :terminal
  end

  test "room transport identity, delayed delivery and retries feed the same engine decision" do
    room = start_supervised!({RoomSim, id: "engine-transport"})
    {:ok, alice} = RoomSim.join(room, "alice", :agent)
    {:ok, bob} = RoomSim.join(room, "bob", :agent)
    :ok = RoomSim.connect(bob)
    {:ok, c} = ContractLab.load(Path.join(@contracts, "explicit-infinity.band"))

    {:ok, s} =
      Engine.new(c,
        session_id: "s1",
        bindings: %{"a" => "alice", "b" => "bob"},
        now: 0,
        inactivity: :accepted,
        max_messages: 10,
        room_policy: :unchecked
      )

    body = ~s({"kind":"ping","payload":{}})
    {:ok, posted} = RoomSim.post(alice, body, mentions: ["bob"])
    [delivery] = RoomSim.pending(room)
    :ok = RoomSim.delay(room, delivery.id, 50)
    {:ok, _} = RoomSim.duplicate(room, delivery.id)
    assert [] = RoomSim.flush(room)
    {:ok, 50} = RoomSim.advance(room, 50)
    assert [{_, :delivered}, {_, :delivered}] = RoomSim.flush(room)
    assert_receive {:room_sim, "engine-transport", "bob", :message_created, first}
    assert_receive {:room_sim, "engine-transport", "bob", :message_created, second}
    assert first.id == posted.id
    assert second.id == first.id
    {:ok, m} = Wire.decode(first.content, transport: %{"id" => first.id, "session" => "s1"})
    {accepted, s} = Engine.step(s, m, actor: first.sender_id, now: RoomSim.now(room))
    assert accepted.status == :accepted
    {duplicate, s} = Engine.step(s, m, actor: second.sender_id, now: RoomSim.now(room))
    assert duplicate.status == :duplicate
    assert s.mentions_used == 1
  end
end
