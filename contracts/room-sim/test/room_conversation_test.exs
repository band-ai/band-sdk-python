defmodule RoomConversationTest do
  use ExUnit.Case, async: true

  # These actors exercise the transport, not a replacement contract engine.
  # The host supplies identities and the script supplies fixed responses.
  defp responder(client, expected, reply, notify) do
    start_supervised!(
      {Task,
       fn ->
         :ok = RoomSim.connect(client)
         send(notify, {:ready, reply})

         receive do
           {:room_sim, _room, _participant, :message_created, message} ->
             assert message.content == expected
             {:ok, response} = RoomSim.post(client, reply, mentions: [message.sender_id])
             send(notify, {:responded, response})
         end
       end},
      id: make_ref()
    )
  end

  test "lead, reviewer, and builder exchange messages through separate processes" do
    room = start_supervised!({RoomSim, id: "conversation", now_ms: 0})
    {:ok, lead} = RoomSim.join(room, "lead", :agent)
    {:ok, reviewer} = RoomSim.join(room, "reviewer", :agent)
    {:ok, builder} = RoomSim.join(room, "builder", :agent)
    :ok = RoomSim.connect(lead)
    responder(reviewer, "review plan-7", "approved plan-7", self())
    responder(builder, "build plan-7", "built plan-7", self())
    assert_receive {:ready, "approved plan-7"}, 1_000
    assert_receive {:ready, "built plan-7"}, 1_000

    {:ok, review} = RoomSim.post(lead, "review plan-7", mentions: ["reviewer"])
    [{_, :delivered}] = RoomSim.flush(room)
    assert_receive {:responded, approval}, 1_000
    assert approval.sender_id == "reviewer"

    # Lose the live approval notification. Reading and redelivery recover the
    # original post without rerunning the reviewer or manufacturing a result.
    [notice] = RoomSim.pending(room)
    :ok = RoomSim.drop(room, notice.id)
    {:ok, [^approval]} = RoomSim.history(lead)
    {:ok, [_]} = RoomSim.replay(lead)
    [{_, :delivered}] = RoomSim.flush(room)
    assert_receive {:room_sim, "conversation", "lead", :message_created, ^approval}

    {:ok, build} = RoomSim.post(lead, "build plan-7", mentions: ["builder"])
    [{_, :delivered}] = RoomSim.flush(room)
    assert_receive {:responded, completion}, 1_000
    [{_, :delivered}] = RoomSim.flush(room)
    assert_receive {:room_sim, "conversation", "lead", :message_created, ^completion}
    assert completion.sender_id == "builder"
    assert RoomSim.transcript(room) == [review, approval, build, completion]
  end

  test "redelivery includes invalid protocol claims rather than inventing an acceptance journal" do
    room = start_supervised!({RoomSim, id: "opaque-content"})
    {:ok, lead} = RoomSim.join(room, "lead", :agent)
    {:ok, builder} = RoomSim.join(room, "builder", :agent)
    :ok = RoomSim.connect(builder)
    {:ok, post} = RoomSim.post(lead, "build with nonexistent approval", mentions: ["builder"])
    [{_, :delivered}] = RoomSim.flush(room)
    assert_receive {:room_sim, _, _, _, ^post}

    # The engine under test owns rejection and recovery. The transport must
    # not silently treat a stored post as a valid transition or hide it.
    assert {:ok, [^post]} = RoomSim.history(builder)
    assert {:ok, [_]} = RoomSim.replay(builder)
    [{_, :delivered}] = RoomSim.flush(room)
    assert_receive {:room_sim, _, _, _, ^post}
    assert RoomSim.transcript(room) == [post]
  end
end
