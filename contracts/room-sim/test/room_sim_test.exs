defmodule RoomSimTest do
  use ExUnit.Case, async: true

  alias RoomSim.{Client, Message}

  defp room(id \\ "test-room", now_ms \\ 0) do
    spec = Supervisor.child_spec({RoomSim, id: id, now_ms: now_ms}, id: make_ref())
    start_supervised!(spec)
  end

  defp member(room, id, type \\ :agent, connected \\ true) do
    assert {:ok, client} = RoomSim.join(room, id, type)
    if connected, do: assert(:ok = RoomSim.connect(client))
    client
  end

  defp post(client, content, mentions, extra \\ []) do
    assert {:ok, message} = RoomSim.post(client, content, [mentions: mentions] ++ extra)
    message
  end

  test "rooms start without platform services or external dependencies" do
    room = room("empty", 42)
    assert Process.alive?(room)
    assert RoomSim.now(room) == 42
    assert RoomSim.members(room) == []
    assert RoomSim.pending(room) == []
    assert RoomSim.transcript(room) == []
  end

  test "sender, room, time, and message id come from the transport" do
    room = room("identity", 5_000)
    lead = member(room, "lead")
    _reviewer = member(room, "reviewer")
    content = ~s({"from":"reviewer","outcome":"approved","ts":999999})
    first = post(lead, content, ["reviewer"])

    assert %Message{sender_id: "lead", sender_type: :agent, room_id: "identity", at_ms: 5_000} =
             first

    assert first.content == content
    assert Regex.match?(~r/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/, first.id)
    second = post(lead, content, ["reviewer"])
    assert first.id != second.id
    assert second.sequence == first.sequence + 1
    assert {:error, :invalid_options} = RoomSim.post(lead, "spoof", sender_id: "reviewer")
    assert length(RoomSim.transcript(room)) == 2
  end

  test "a made-up client cannot speak or read as a participant" do
    room = room()
    fake = %Client{room: room, token: make_ref()}
    assert {:error, :not_member} = RoomSim.post(fake, "hello")
    assert {:error, :not_member} = RoomSim.history(fake)
    assert {:error, :not_member} = RoomSim.connect(fake)
    assert RoomSim.transcript(room) == []
  end

  test "membership identities are explicit and cannot silently change type" do
    room = room()
    _ = member(room, "lead")
    assert {:error, :already_member} = RoomSim.join(room, "lead", :user)
    assert {:error, :invalid_participant} = RoomSim.join(room, "", :agent)
    assert {:error, :invalid_participant} = RoomSim.join(room, "other", :system)
    assert [%{id: "lead", type: :agent, connected: true}] = RoomSim.members(room)
  end

  test "direct mentions fan out to agents; references do not; users see both" do
    room = room()
    lead = member(room, "lead")
    _reviewer = member(room, "reviewer")
    _builder = member(room, "builder")
    _human = member(room, "human", :user)
    message = post(lead, "review", ["reviewer", %{id: "builder", kind: :reference}])
    assert Enum.map(RoomSim.pending(room), & &1.participant_id) == ["human", "reviewer"]
    assert length(RoomSim.flush(room)) == 2
    assert_receive {:room_sim, "test-room", "reviewer", :message_created, ^message}
    assert_receive {:room_sim, "test-room", "human", :message_created, ^message}
    refute_received {:room_sim, _, "builder", _, _}
    refute_received {:room_sim, _, "lead", _, _}
  end

  test "agent pushes never echo their own message even in a self-mentioned stored fixture" do
    room = room()
    lead = member(room, "lead")
    _human = member(room, "human", :user)
    message = post(lead, "self fixture", ["lead"])
    RoomSim.flush(room)
    refute_received {:room_sim, _, "lead", _, _}
    assert_receive {:room_sim, _, "human", :message_created, ^message}
  end

  test "an unmentioned stored text reaches users but no agents" do
    room = room()
    lead = member(room, "lead")
    _reviewer = member(room, "reviewer")
    human = member(room, "human", :user)
    message = post(lead, "room note", [])
    RoomSim.flush(room)
    assert_receive {:room_sim, _, "human", :message_created, ^message}
    refute_received {:room_sim, _, "reviewer", _, _}
    assert {:ok, [^message]} = RoomSim.history(human)
  end

  test "event messages reach humans without waking mentioned agents" do
    room = room()
    lead = member(room, "lead")
    reviewer = member(room, "reviewer")
    human = member(room, "human", :user)
    event = post(lead, "working", ["reviewer"], message_type: :thought)
    RoomSim.flush(room)
    assert_receive {:room_sim, _, "human", :event_created, ^event}
    refute_received {:room_sim, _, "reviewer", _, _}
    assert {:ok, []} = RoomSim.history(reviewer)
    assert {:ok, [^event]} = RoomSim.history(human)
  end

  test "agent history models text and direct mentions, not the whole transcript" do
    room = room()
    lead = member(room, "lead")
    reviewer = member(room, "reviewer")
    _builder = member(room, "builder")
    human = member(room, "human", :user)
    visible = post(lead, "review", ["reviewer"])
    post(lead, "build", ["builder"])
    post(lead, "reference", [%{id: "reviewer", kind: :reference}])
    post(lead, "broadcast", [])
    post(lead, "thinking", ["reviewer"], message_type: :thought)
    post(reviewer, "my outgoing reply", ["lead"])
    assert {:ok, [^visible]} = RoomSim.history(reviewer)
    assert {:ok, all} = RoomSim.history(human)
    assert length(all) == 6
    assert all == RoomSim.transcript(room)
  end

  test "fetch has the same visibility restriction and does not reveal hidden message existence" do
    room = room()
    lead = member(room, "lead")
    reviewer = member(room, "reviewer")
    builder = member(room, "builder")
    message = post(lead, "review", ["reviewer"])
    assert {:ok, ^message} = RoomSim.fetch(reviewer, message.id)
    assert {:error, :not_found} = RoomSim.fetch(builder, message.id)
    assert {:error, :not_found} = RoomSim.fetch(builder, "missing")
  end

  test "a downstream builder cannot read an unmentioned review exchange as evidence" do
    room = room()
    lead = member(room, "lead")
    reviewer = member(room, "reviewer")
    builder = member(room, "builder")
    opening = post(lead, "review plan-7", ["reviewer"])
    closing = post(reviewer, "approved plan-7", ["lead"])
    post(lead, "build using #{opening.id} and #{closing.id}", ["builder"])
    assert {:error, :not_found} = RoomSim.fetch(builder, opening.id)
    assert {:error, :not_found} = RoomSim.fetch(builder, closing.id)
    assert {:ok, [_build]} = RoomSim.history(builder)
    assert length(RoomSim.transcript(room)) == 3
  end

  test "delivery is explicit and can be reordered without changing stored order" do
    room = room()
    lead = member(room, "lead")
    _reviewer = member(room, "reviewer")
    first = post(lead, "first", ["reviewer"])
    second = post(lead, "second", ["reviewer"])
    refute_received {:room_sim, _, _, _, _}
    [one, two] = RoomSim.pending(room)
    assert {:ok, :delivered} = RoomSim.deliver(room, two.id)
    assert {:ok, :delivered} = RoomSim.deliver(room, one.id)
    assert_receive {:room_sim, _, _, _, ^second}
    assert_receive {:room_sim, _, _, _, ^first}
    assert RoomSim.transcript(room) == [first, second]
  end

  test "delayed notifications become deliverable at exact virtual time without sleeping" do
    room = room("clock", 1_000)
    lead = member(room, "lead")
    _reviewer = member(room, "reviewer")
    message = post(lead, "later", ["reviewer"])
    [delivery] = RoomSim.pending(room)
    assert :ok = RoomSim.delay(room, delivery.id, 60_000)
    assert {:ok, 60_999} = RoomSim.advance(room, 59_999)
    assert RoomSim.flush(room) == []
    assert {:error, :not_due} = RoomSim.deliver(room, delivery.id)
    assert {:ok, 61_000} = RoomSim.advance(room, 1)
    assert [{_, :delivered}] = RoomSim.flush(room)
    assert_receive {:room_sim, _, _, _, ^message}
    assert message.at_ms == 1_000
  end

  test "duplicate delivery preserves message identity and creates no second stored post" do
    room = room()
    lead = member(room, "lead")
    _reviewer = member(room, "reviewer")
    message = post(lead, "once in history", ["reviewer"])
    [original] = RoomSim.pending(room)
    assert {:ok, duplicate} = RoomSim.duplicate(room, original.id)
    assert duplicate.id != original.id
    assert duplicate.message_id == message.id
    assert length(RoomSim.flush(room)) == 2
    assert_receive {:room_sim, _, _, _, ^message}
    assert_receive {:room_sim, _, _, _, ^message}
    assert RoomSim.transcript(room) == [message]
  end

  test "dropping a notification preserves history for explicit redelivery" do
    room = room()
    lead = member(room, "lead")
    reviewer = member(room, "reviewer")
    message = post(lead, "lost notification", ["reviewer"])
    [delivery] = RoomSim.pending(room)
    assert :ok = RoomSim.drop(room, delivery.id)
    assert RoomSim.flush(room) == []
    assert {:ok, [^message]} = RoomSim.history(reviewer)
    assert {:ok, [_]} = RoomSim.replay(reviewer)
    RoomSim.flush(room)
    assert_receive {:room_sim, _, _, _, ^message}
    assert RoomSim.transcript(room) == [message]
  end

  test "offline posts are not silently delivered on reconnect" do
    room = room()
    lead = member(room, "lead")
    reviewer = member(room, "reviewer", :agent, false)
    message = post(lead, "while offline", ["reviewer"])
    assert RoomSim.pending(room) == []
    assert {:error, :disconnected} = RoomSim.replay(reviewer)
    assert :ok = RoomSim.connect(reviewer)
    assert RoomSim.flush(room) == []
    assert {:ok, [_]} = RoomSim.replay(reviewer)
    RoomSim.flush(room)
    assert_receive {:room_sim, _, _, _, ^message}
  end

  test "queued pushes for an old connection do not jump to its replacement" do
    room = room()
    lead = member(room, "lead")
    reviewer = member(room, "reviewer")
    message = post(lead, "in flight", ["reviewer"])
    assert :ok = RoomSim.disconnect(reviewer)
    assert :ok = RoomSim.connect(reviewer)
    assert [{_, :disconnected}] = RoomSim.flush(room)
    refute_received {:room_sim, _, _, _, _}
    assert {:ok, [_]} = RoomSim.replay(reviewer)
    RoomSim.flush(room)
    assert_receive {:room_sim, _, _, _, ^message}
  end

  test "removal revokes access and old queued deliveries, including after rejoining" do
    room = room()
    lead = member(room, "lead")
    old = member(room, "reviewer")
    post(lead, "in flight", ["reviewer"])
    assert :ok = RoomSim.leave(old)
    assert {:error, :not_member} = RoomSim.history(old)
    assert {:error, :not_member} = RoomSim.post(old, "old identity")
    new = member(room, "reviewer")
    assert new.token != old.token
    assert [{_, :revoked}] = RoomSim.flush(room)
    refute_received {:room_sim, _, _, _, _}
    assert {:error, :not_member} = RoomSim.connect(old)
    assert {:ok, [_]} = RoomSim.history(new)
  end

  test "clients and message lookups cannot cross rooms" do
    one = room("one")
    two = room("two")
    sender = member(one, "lead")
    _receiver = member(one, "reviewer")
    other = member(two, "reviewer")
    message = post(sender, "private room", ["reviewer"])
    assert {:error, :not_member} = RoomSim.post(%{sender | room: two}, "wrong binding")
    assert {:error, :not_found} = RoomSim.fetch(other, message.id)
    assert {:ok, []} = RoomSim.history(other)
    assert RoomSim.pending(two) == []
  end

  test "invalid routing data rejects the entire post without consuming an id" do
    room = room()
    lead = member(room, "lead")
    _reviewer = member(room, "reviewer")
    assert {:error, :unknown_participant} = RoomSim.post(lead, "bad", mentions: ["absent"])

    assert {:error, :invalid_mentions} =
             RoomSim.post(lead, "bad", mentions: ["reviewer", "reviewer"])

    assert {:error, :invalid_mentions} =
             RoomSim.post(lead, "bad", mentions: [%{id: "reviewer", kind: :other}])

    assert {:error, :invalid_message} = RoomSim.post(lead, %{})

    assert {:error, :invalid_message_type} =
             RoomSim.post(lead, "fake membership", message_type: :participant)

    assert RoomSim.transcript(room) == []
    assert RoomSim.pending(room) == []
    assert post(lead, "valid", ["reviewer"]).sequence == 1
  end

  test "history and redelivery cursors select only later visible posts" do
    room = room()
    lead = member(room, "lead")
    reviewer = member(room, "reviewer")
    first = post(lead, "one", ["reviewer"])
    post(lead, "not visible", [])
    third = post(lead, "three", ["reviewer"])
    RoomSim.flush(room)
    assert {:ok, [^third]} = RoomSim.history(reviewer, after_sequence: first.sequence)
    assert {:ok, [again]} = RoomSim.replay(reviewer, after_sequence: first.sequence)
    assert again.message_id == third.id
    assert {:error, :invalid_options} = RoomSim.history(reviewer, after_sequence: -1)
    assert {:error, :invalid_options} = RoomSim.replay(reviewer, imaginary: true)
  end

  test "invalid time and fault commands leave state intact" do
    room = room("clock", 10)
    assert {:error, :invalid_duration} = RoomSim.advance(room, -1)
    assert {:error, :invalid_duration} = RoomSim.advance(room, 0.5)
    assert RoomSim.now(room) == 10
    assert {:error, :not_found} = RoomSim.deliver(room, 999)
    assert {:error, :not_found} = RoomSim.drop(room, 999)
    assert {:error, :not_found} = RoomSim.duplicate(room, 999)
    assert {:error, :not_found} = RoomSim.delay(room, 999, 1)
    assert RoomSim.pending(room) == []
  end

  test "running the same trace produces the same ids, times, and observable decisions" do
    run = fn ->
      room = room("deterministic", 100)
      lead = member(room, "lead")
      _reviewer = member(room, "reviewer")
      post(lead, "same input", ["reviewer"])
      [delivery] = RoomSim.pending(room)
      RoomSim.duplicate(room, delivery.id)
      RoomSim.delay(room, delivery.id, 10)
      RoomSim.flush(room)
      RoomSim.advance(room, 10)
      RoomSim.flush(room)
      {RoomSim.transcript(room), RoomSim.trace(room)}
    end

    assert run.() == run.()
  end
end
