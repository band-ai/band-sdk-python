# Run with: mix run examples/review_and_recover.exs
# These fixed messages demonstrate transport controls, not workflow validation.
{:ok, room} = RoomSim.start_link(id: "review-demo", now_ms: 0)
{:ok, lead} = RoomSim.join(room, "lead", :agent)
{:ok, reviewer} = RoomSim.join(room, "reviewer", :agent)
{:ok, builder} = RoomSim.join(room, "builder", :agent)
:ok = RoomSim.connect(lead)
:ok = RoomSim.connect(reviewer)
:ok = RoomSim.connect(builder)

{:ok, request} = RoomSim.post(lead, "review plan-7", mentions: ["reviewer"])
[review_delivery] = RoomSim.pending(room)
:ok = RoomSim.delay(room, review_delivery.id, 60_000)
[] = RoomSim.flush(room)
{:ok, 60_000} = RoomSim.advance(room, 60_000)
[{_, :delivered}] = RoomSim.flush(room)

receive do
  {:room_sim, "review-demo", "reviewer", :message_created, ^request} ->
    IO.puts("Reviewer received the request after 60 simulated seconds; no sleep was needed.")
after
  1_000 -> raise "Expected reviewer delivery"
end

{:ok, approval} = RoomSim.post(reviewer, "approved plan-7", mentions: ["lead"])
[approval_delivery] = RoomSim.pending(room)
:ok = RoomSim.drop(room, approval_delivery.id)
{:error, :not_found} = RoomSim.fetch(builder, approval.id)
IO.puts("Builder cannot read an approval that did not mention it.")

{:ok, [^approval]} = RoomSim.history(lead)
{:ok, [retry]} = RoomSim.replay(lead)
{:ok, _duplicate} = RoomSim.duplicate(room, retry.id)
[{_, :delivered}, {_, :delivered}] = RoomSim.flush(room)

for _ <- 1..2 do
  receive do
    {:room_sim, "review-demo", "lead", :message_created, ^approval} -> :ok
  after
    1_000 -> raise "Expected redelivery"
  end
end

IO.puts("Lost approval recovered; duplicate notifications preserve message ID #{approval.id}.")

IO.puts(
  "Stored posts: #{length(RoomSim.transcript(room))}; virtual time: #{RoomSim.now(room)} ms."
)

GenServer.stop(room)
