defmodule RoomSim.Client do
  @moduledoc "An identity binding issued by the test host, local to one room."
  defstruct [:room, :token]
end

defmodule RoomSim.Message do
  @moduledoc "An immutable room post. Posting is not a protocol acceptance decision."
  defstruct [
    :id,
    :room_id,
    :sequence,
    :at_ms,
    :sender_id,
    :sender_type,
    :content,
    message_type: :text,
    mentions: []
  ]
end

defmodule RoomSim.Delivery do
  @moduledoc "A controllable notification of an existing message."
  defstruct [:id, :message_id, :participant_id, :event, :due_at_ms]
end

defmodule RoomSim do
  @moduledoc """
  A dependency-free room transport for local tests, backed by one GenServer.

  The test host joins identities and receives room-bound client capabilities.
  Clients post opaque text; sender attribution comes from those capabilities.
  Agents see directly mentioned text, while participating users see the room
  stream. This models normalized stored messages, not HTTP request validation.

  Posts are recorded immediately. Notifications wait for `deliver/2` or
  `flush/1`; advancing time never sleeps or implicitly delivers anything.
  `replay/2` redelivers visible posts with their original IDs. It does not
  replay accepted protocol decisions. A contract engine stays outside this
  simulator and must supply its own validation, timers, and accepted journal.

  Notification shape:
      {:room_sim, room_id, participant_id, event, %RoomSim.Message{}}

  This is test support, not an authentication or persistence service. Host
  controls and the complete transcript must not be exposed as participant APIs.
  """

  use GenServer
  import Bitwise, only: [band: 2, bor: 2]
  alias RoomSim.{Client, Delivery, Message}

  @message_types [:text, :thought, :tool_call, :tool_result, :error, :task, :attention]

  def child_spec(opts),
    do: %{id: {__MODULE__, opts[:id]}, start: {__MODULE__, :start_link, [opts]}}

  @doc "Start one isolated room with an explicit ID and a virtual millisecond clock (default 0)."
  def start_link(opts) do
    with :ok <- options(opts, [:id, :now_ms]),
         id when is_binary(id) and byte_size(id) > 0 <- Keyword.get(opts, :id),
         now when is_integer(now) and now >= 0 <- Keyword.get(opts, :now_ms, 0) do
      GenServer.start_link(__MODULE__, {id, now})
    else
      _ -> {:error, :invalid_room_options}
    end
  end

  @doc "Host control: register an :agent or :user, returning a client bound to this room."
  def join(room, id, type), do: GenServer.call(room, {:join, id, type})
  def leave(client), do: request(client, :leave)

  @doc "Subscribe a local process. A replacement connection invalidates old queued pushes."
  def connect(client, pid \\ self()), do: request(client, {:connect, pid})
  def disconnect(client), do: request(client, :disconnect)

  @doc "Store opaque content; options are :message_type and resolved :mentions (IDs or maps)."
  def post(client, content, opts \\ []), do: request(client, {:post, content, opts})

  @doc "Visible stored posts, in sequence order. Models status=all; accepts :after_sequence."
  def history(client, opts \\ []), do: request(client, {:history, opts})

  @doc "Test helper using history's visibility rules; not a claim of a hosted read-by-ID API."
  def fetch(client, id), do: request(client, {:fetch, id})

  @doc "Queue visible history for this connection, preserving message IDs; accepts :after_sequence."
  def replay(client, opts \\ []), do: request(client, {:replay, opts})

  # Host-only inspection and deterministic delivery controls.
  def members(room), do: GenServer.call(room, :members)
  def transcript(room), do: GenServer.call(room, :transcript)
  def trace(room), do: GenServer.call(room, :trace)
  def now(room), do: GenServer.call(room, :now)
  def advance(room, ms), do: GenServer.call(room, {:advance, ms})
  def pending(room), do: GenServer.call(room, :pending)
  def flush(room), do: GenServer.call(room, :flush)
  def deliver(room, id), do: GenServer.call(room, {:deliver, id})
  def delay(room, id, ms), do: GenServer.call(room, {:delay, id, ms})
  def duplicate(room, id), do: GenServer.call(room, {:duplicate, id})
  def drop(room, id), do: GenServer.call(room, {:drop, id})

  defp request(%Client{room: room, token: token}, operation) do
    GenServer.call(room, {:client, token, operation})
  end

  @impl true
  def init({id, now}) do
    {:ok,
     %{
       id: id,
       now: now,
       members: %{},
       clients: %{},
       messages: %{},
       order: [],
       pending: %{},
       next_message: 1,
       next_delivery: 1,
       trace: []
     }}
  end

  @impl true
  def handle_call({:join, id, type}, _from, state) do
    cond do
      not (is_binary(id) and byte_size(id) > 0 and type in [:agent, :user]) ->
        {:reply, {:error, :invalid_participant}, state}

      Map.has_key?(state.members, id) ->
        {:reply, {:error, :already_member}, state}

      true ->
        token = make_ref()
        member = %{id: id, type: type, token: token, subscriber: nil, connection: 0}

        state = %{
          state
          | members: Map.put(state.members, id, member),
            clients: Map.put(state.clients, token, id)
        }

        {:reply, {:ok, %Client{room: self(), token: token}},
         record(state, :joined, %{participant: id, type: type})}
    end
  end

  def handle_call({:client, token, operation}, _from, state) do
    case Map.fetch(state.clients, token) do
      :error ->
        {:reply, {:error, :not_member}, state}

      {:ok, id} ->
        {reply, updated} = client_operation(operation, Map.fetch!(state.members, id), state)
        {:reply, reply, updated}
    end
  end

  def handle_call(:members, _from, state) do
    members =
      state.members
      |> Map.values()
      |> Enum.sort_by(& &1.id)
      |> Enum.map(&%{id: &1.id, type: &1.type, connected: connected?(&1)})

    {:reply, members, state}
  end

  def handle_call(:transcript, _from, state), do: {:reply, messages(state), state}
  def handle_call(:trace, _from, state), do: {:reply, Enum.reverse(state.trace), state}
  def handle_call(:now, _from, state), do: {:reply, state.now, state}
  def handle_call(:pending, _from, state), do: {:reply, pending_deliveries(state), state}

  def handle_call({:advance, ms}, _from, state) do
    if is_integer(ms) and ms >= 0 do
      updated = record(%{state | now: state.now + ms}, :time_advanced, %{by_ms: ms})
      {:reply, {:ok, updated.now}, updated}
    else
      {:reply, {:error, :invalid_duration}, state}
    end
  end

  def handle_call({:deliver, id}, _from, state) do
    {reply, updated} = dispatch(state, id)
    {:reply, reply, updated}
  end

  def handle_call(:flush, _from, state) do
    due = Enum.filter(pending_deliveries(state), &(&1.due_at_ms <= state.now))

    {results, updated} =
      Enum.map_reduce(due, state, fn delivery, acc ->
        {{:ok, status}, next} = dispatch(acc, delivery.id)
        {{delivery.id, status}, next}
      end)

    {:reply, results, updated}
  end

  def handle_call({:delay, id, ms}, _from, state) do
    cond do
      not (is_integer(ms) and ms >= 0) ->
        {:reply, {:error, :invalid_duration}, state}

      not Map.has_key?(state.pending, id) ->
        {:reply, {:error, :not_found}, state}

      true ->
        entry = state.pending[id]
        delivery = %{entry.delivery | due_at_ms: max(entry.delivery.due_at_ms, state.now) + ms}
        state = put_in(state.pending[id], %{entry | delivery: delivery})
        {:reply, :ok, record(state, :delayed, %{delivery: id, due_at_ms: delivery.due_at_ms})}
    end
  end

  def handle_call({:duplicate, id}, _from, state) do
    case Map.fetch(state.pending, id) do
      :error ->
        {:reply, {:error, :not_found}, state}

      {:ok, entry} ->
        copy = %{entry.delivery | id: state.next_delivery}

        updated = %{
          state
          | pending: Map.put(state.pending, copy.id, %{entry | delivery: copy}),
            next_delivery: state.next_delivery + 1
        }

        {:reply, {:ok, copy}, record(updated, :duplicated, %{original: id, delivery: copy.id})}
    end
  end

  def handle_call({:drop, id}, _from, state) do
    case Map.pop(state.pending, id) do
      {nil, _} ->
        {:reply, {:error, :not_found}, state}

      {_entry, pending} ->
        {:reply, :ok, record(%{state | pending: pending}, :dropped, %{delivery: id})}
    end
  end

  defp client_operation(:leave, member, state) do
    state = %{
      state
      | members: Map.delete(state.members, member.id),
        clients: Map.delete(state.clients, member.token)
    }

    {:ok, record(state, :left, %{participant: member.id})}
  end

  defp client_operation({:connect, pid}, member, state) do
    if is_pid(pid) and node(pid) == node() and Process.alive?(pid) do
      updated = %{member | subscriber: pid, connection: member.connection + 1}

      {:ok,
       record(put_in(state.members[member.id], updated), :connected, %{
         participant: member.id,
         connection: updated.connection
       })}
    else
      {{:error, :invalid_subscriber}, state}
    end
  end

  defp client_operation(:disconnect, member, state) do
    updated = %{member | subscriber: nil, connection: member.connection + 1}

    {:ok,
     record(put_in(state.members[member.id], updated), :disconnected, %{participant: member.id})}
  end

  defp client_operation({:post, content, opts}, member, state) do
    case validate_post(state, content, opts) do
      {:error, reason} ->
        {{:error, reason}, state}

      {:ok, type, mentions} ->
        message = %Message{
          id: message_id(state.id, state.next_message),
          room_id: state.id,
          sequence: state.next_message,
          at_ms: state.now,
          sender_id: member.id,
          sender_type: member.type,
          content: content,
          message_type: type,
          mentions: mentions
        }

        updated =
          %{
            state
            | messages: Map.put(state.messages, message.id, message),
              order: [message.id | state.order],
              next_message: state.next_message + 1
          }
          |> record(:posted, %{message: message.id, sender: member.id})

        updated =
          state.members
          |> Map.values()
          |> Enum.sort_by(& &1.id)
          |> Enum.reduce(updated, fn recipient, acc ->
            if connected?(recipient) and push_visible?(recipient, message) do
              {_delivery, next} = enqueue(acc, recipient, message)
              next
            else
              acc
            end
          end)

        {{:ok, message}, updated}
    end
  end

  defp client_operation({:history, opts}, member, state) do
    {visible_history(state, member, opts), state}
  end

  defp client_operation({:fetch, id}, member, state) do
    case Map.fetch(state.messages, id) do
      {:ok, message} ->
        if history_visible?(member, message),
          do: {{:ok, message}, state},
          else: {{:error, :not_found}, state}

      :error ->
        {{:error, :not_found}, state}
    end
  end

  defp client_operation({:replay, opts}, member, state) do
    with {:ok, messages} <- visible_history(state, member, opts),
         true <- connected?(member) do
      {deliveries, updated} =
        messages
        |> Enum.filter(&push_visible?(member, &1))
        |> Enum.map_reduce(state, fn message, acc -> enqueue(acc, member, message) end)

      {{:ok, deliveries},
       record(updated, :replayed, %{participant: member.id, count: length(deliveries)})}
    else
      false -> {{:error, :disconnected}, state}
      {:error, reason} -> {{:error, reason}, state}
    end
  end

  defp options(opts, allowed) do
    if is_list(opts) and Keyword.keyword?(opts) and
         length(Keyword.keys(opts)) == length(Enum.uniq(Keyword.keys(opts))) and
         Enum.all?(Keyword.keys(opts), &(&1 in allowed)),
       do: :ok,
       else: {:error, :invalid_options}
  end

  defp validate_post(state, content, opts) do
    with :ok <- options(opts, [:mentions, :message_type]),
         {:ok, mentions} <- normalize_mentions(Keyword.get(opts, :mentions, [])) do
      type = Keyword.get(opts, :message_type, :text)

      cond do
        not is_binary(content) ->
          {:error, :invalid_message}

        type not in @message_types ->
          {:error, :invalid_message_type}

        Enum.any?(mentions, &(not Map.has_key?(state.members, &1.id))) ->
          {:error, :unknown_participant}

        true ->
          {:ok, type, mentions}
      end
    end
  end

  defp normalize_mentions(mentions) when is_list(mentions) do
    normalized =
      Enum.map(mentions, fn
        id when is_binary(id) and byte_size(id) > 0 ->
          %{id: id, kind: :mention}

        %{id: id} = mention when is_binary(id) and byte_size(id) > 0 ->
          if Enum.all?(Map.keys(mention), &(&1 in [:id, :kind])) and
               Map.get(mention, :kind, :mention) in [:mention, :reference],
             do: %{id: id, kind: Map.get(mention, :kind, :mention)},
             else: :invalid

        _ ->
          :invalid
      end)

    if :invalid in normalized or length(Enum.uniq_by(normalized, & &1.id)) != length(normalized),
      do: {:error, :invalid_mentions},
      else: {:ok, normalized}
  end

  defp normalize_mentions(_), do: {:error, :invalid_mentions}

  defp visible_history(state, member, opts) do
    with :ok <- options(opts, [:after_sequence]),
         cursor when is_integer(cursor) and cursor >= 0 <- Keyword.get(opts, :after_sequence, 0) do
      {:ok,
       Enum.filter(messages(state), &(&1.sequence > cursor and history_visible?(member, &1)))}
    else
      _ -> {:error, :invalid_options}
    end
  end

  defp history_visible?(%{type: :user}, _message), do: true

  defp history_visible?(member, message) do
    message.message_type == :text and
      Enum.any?(message.mentions, &(&1.id == member.id and &1.kind == :mention))
  end

  defp push_visible?(%{type: :user}, _message), do: true

  defp push_visible?(member, message) do
    history_visible?(member, message) and
      not (message.sender_id == member.id and message.sender_type == :agent)
  end

  defp connected?(member), do: is_pid(member.subscriber) and Process.alive?(member.subscriber)

  defp messages(state),
    do: state.order |> Enum.reverse() |> Enum.map(&Map.fetch!(state.messages, &1))

  defp pending_deliveries(state),
    do: state.pending |> Map.values() |> Enum.map(& &1.delivery) |> Enum.sort_by(& &1.id)

  defp enqueue(state, member, message) do
    delivery = %Delivery{
      id: state.next_delivery,
      message_id: message.id,
      participant_id: member.id,
      event: if(message.message_type == :text, do: :message_created, else: :event_created),
      due_at_ms: state.now
    }

    entry = %{delivery: delivery, token: member.token, connection: member.connection}

    updated = %{
      state
      | pending: Map.put(state.pending, delivery.id, entry),
        next_delivery: state.next_delivery + 1
    }

    {delivery,
     record(updated, :queued, %{
       delivery: delivery.id,
       message: message.id,
       participant: member.id
     })}
  end

  defp dispatch(state, id) do
    case Map.fetch(state.pending, id) do
      :error ->
        {{:error, :not_found}, state}

      {:ok, %{delivery: %{due_at_ms: due}}} when due > state.now ->
        {{:error, :not_due}, state}

      {:ok, entry} ->
        delivery = entry.delivery
        member = state.members[delivery.participant_id]

        status =
          cond do
            is_nil(member) or member.token != entry.token ->
              :revoked

            is_nil(member.subscriber) or member.connection != entry.connection ->
              :disconnected

            not Process.alive?(member.subscriber) ->
              :recipient_down

            true ->
              send(
                member.subscriber,
                {:room_sim, state.id, member.id, delivery.event,
                 state.messages[delivery.message_id]}
              )

              :delivered
          end

        updated =
          record(%{state | pending: Map.delete(state.pending, id)}, :delivery, %{
            delivery: id,
            status: status,
            message: delivery.message_id
          })

        {{:ok, status}, updated}
    end
  end

  defp record(state, event, details) do
    %{state | trace: [Map.merge(details, %{at_ms: state.now, event: event}) | state.trace]}
  end

  # UUID-shaped, deterministic fixture identity; never advertised as production randomness.
  defp message_id(room_id, sequence) do
    <<a::32, b::16, c::16, d::16, e::48, _::binary>> =
      :crypto.hash(:sha256, "#{byte_size(room_id)}:#{room_id}:#{sequence}")

    [
      {a, 8},
      {b, 4},
      {bor(band(c, 0x0FFF), 0x4000), 4},
      {bor(band(d, 0x3FFF), 0x8000), 4},
      {e, 12}
    ]
    |> Enum.map_join("-", fn {n, width} ->
      n |> Integer.to_string(16) |> String.downcase() |> String.pad_leading(width, "0")
    end)
  end
end
