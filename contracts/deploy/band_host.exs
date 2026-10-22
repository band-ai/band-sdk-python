# Qualification only: run with `MIX_ENV=test mix run --no-start --no-halt`.
# Uses a dedicated migrated database; never a production database or the Sandbox pool.
Code.require_file("band_command.exs", __DIR__)

defmodule ContractQualification do
  import Ecto.Query
  import ThenvoiCom.Factory
  alias ThenvoiCom.Repo
  alias ThenvoiCom.Thenvoi.Agents
  alias ThenvoiCom.Thenvoi.Chat.{ChatMessage, ChatRoomParticipant}
  alias ThenvoiCom.ContractRooms.{Session, Receipt}

  def private(path, data) do
    temp = path <> ".tmp"
    File.write!(temp, Jason.encode!(data))
    File.chmod!(temp, 0o600)
    File.rename!(temp, path)
  end

  def provision(path) do
    if File.exists?(path) do
      path |> File.read!() |> Jason.decode!()
    else
      suffix = Ecto.UUID.generate()
      owner = insert(:user, email: "owner-#{suffix}@example.com", handle: "owner-#{suffix}")

      observer =
        insert(:user, email: "observer-#{suffix}@example.com", handle: "observer-#{suffix}")

      outsider =
        insert(:user, email: "outsider-#{suffix}@example.com", handle: "outsider-#{suffix}")

      {_, owner_key} = create_api_key_with_value(%{user: owner})
      {_, observer_key} = create_api_key_with_value(%{user: observer})
      {_, outsider_key} = create_api_key_with_value(%{user: outsider})

      {:ok, agent, agent_key} =
        Agents.register_external_agent(
          %{
            "name" => "contract-qualification-#{suffix}",
            "description" => "Isolated qualification"
          },
          owner
        )

      agents =
        for kind <- ["requester", "observer", "outsider"], into: %{} do
          {:ok, participant, key} =
            Agents.register_external_agent(
              %{
                "name" => "contract-qualification-#{kind}-#{suffix}",
                "description" => "Isolated qualification"
              },
              owner
            )

          {kind, {participant, key}}
        end

      {requester, requester_key} = agents["requester"]
      {observer_agent, observer_agent_key} = agents["observer"]
      {_outsider_agent, outsider_agent_key} = agents["outsider"]
      other_room = insert(:chat_room, status: :active, type: :group, task: nil)

      for {participant, role} <- [{requester, :owner}, {agent, :member}] do
        insert(:chat_room_participant,
          chat_room: other_room,
          participant_id: participant.id,
          participant_type: "Agent",
          role: role,
          status: :active
        )
      end

      room = insert(:chat_room, status: :active, type: :group, task: nil)

      for {id, type, role} <- [
            {owner.fusionauth_uuid, "User", :owner},
            {observer.fusionauth_uuid, "User", :member},
            {requester.id, "Agent", :owner},
            {observer_agent.id, "Agent", :member},
            {agent.id, "Agent", :member}
          ] do
        insert(:chat_room_participant,
          chat_room: room,
          participant_id: id,
          participant_type: type,
          role: role,
          status: :active
        )
      end

      {:ok, discovery} =
        HeaderSpikes.discover(:frontmatter, System.fetch_env!("CONTRACT_LAB_CONTRACT"))

      {:ok, file_source} = HeaderSpikes.file_source(discovery)
      # Canonical encoding maps atom keys/durations into portable JSON.
      source =
        file_source
        |> ContractLab.Canonical.encode()
        |> Jason.decode!()

      data = %{
        "room" => room.id,
        "other_room" => other_room.id,
        "requester" => requester.id,
        "requester_key" => requester_key,
        "observer_agent_key" => observer_agent_key,
        "outsider_agent_key" => outsider_agent_key,
        "session" => Ecto.UUID.generate(),
        "owner" => owner.fusionauth_uuid,
        "agent" => agent.id,
        "observer" => observer.fusionauth_uuid,
        "source" => source,
        "owner_key" => owner_key,
        "agent_key" => agent_key,
        "observer_key" => observer_key,
        "outsider_key" => outsider_key
      }

      private(path, data)
      data
    end
  end

  def watch(root, data, index) do
    room = data["room"]

    ContractQualification.Command.consume(root, index, fn request ->
      case request["operation"] do
        "revoke" ->
          Repo.update_all(
            from(p in ChatRoomParticipant,
              where: p.chat_room_id == ^room and p.participant_id == ^data["agent"]
            ),
            set: [status: :blocked]
          )

        "restore" ->
          Repo.update_all(
            from(p in ChatRoomParticipant,
              where: p.chat_room_id == ^room and p.participant_id == ^data["agent"]
            ),
            set: [status: :active]
          )
      end
    end)

    sessions =
      Repo.all(
        from(s in Session,
          where: s.chat_room_id == ^room,
          select: %{id: s.id, version: s.sequence, status: s.status}
        )
      )

    messages =
      Repo.all(
        from(m in ChatMessage,
          where: m.chat_room_id == ^room,
          select: %{content: m.content, metadata: m.metadata}
        )
      )

    safe =
      Enum.all?(messages, fn m ->
        m.content == "Contract event accepted." and Map.keys(m.metadata) == ["contract_receipt"] and
          Enum.sort(Map.keys(m.metadata["contract_receipt"])) == ["definition", "id", "session"]
      end)

    receipts =
      Repo.aggregate(
        from(r in Receipt,
          join: s in Session,
          on: r.session_id == s.id,
          where: s.chat_room_id == ^room
        ),
        :count
      )

    private(Path.join(root, "status-#{index}.json"), %{
      node: node(),
      peers: Node.list(),
      port: System.fetch_env!("CONTRACT_LAB_PORT"),
      database: Repo.config()[:database],
      pool: inspect(Repo.config()[:pool]),
      sessions: sessions,
      messages: length(messages),
      receipts: receipts,
      mirrors_safe: safe,
      telemetry: Agent.get(ContractQualification.Metrics, & &1),
      sweeper: ThenvoiCom.ContractRooms.Sweeper.status()
    })

    Process.sleep(200)
    watch(root, data, index)
  end
end

root = System.fetch_env!("CONTRACT_LAB_STATE")
index = System.fetch_env!("CONTRACT_LAB_INDEX")
name = System.fetch_env!("CONTRACT_LAB_NODE") |> String.to_atom()
{:ok, _} = Node.start(name, :longnames)
Node.set_cookie(root |> Path.join("cookie") |> File.read!() |> String.to_atom())
Code.require_file("band_database.exs", __DIR__)
repo = Application.fetch_env!(:thenvoi_com, ThenvoiCom.Repo)

Application.put_env(
  :thenvoi_com,
  ThenvoiCom.Repo,
  ContractQualification.Database.configure!(repo)
)

# Repo.config runs the installed Repo.init and Ecto normalization without
# opening a connection. Check their effective destination before app.start.
:ok = ContractQualification.Database.verify!(ThenvoiCom.Repo.config())

endpoint = Application.fetch_env!(:thenvoi_com, ThenvoiComWeb.Endpoint)

Application.put_env(
  :thenvoi_com,
  ThenvoiComWeb.Endpoint,
  Keyword.merge(endpoint,
    server: true,
    http: [ip: {127, 0, 0, 1}, port: String.to_integer(System.fetch_env!("CONTRACT_LAB_PORT"))]
  )
)

Application.put_env(:thenvoi_com, :contract_rooms, enabled: true)
Application.put_env(:thenvoi_com, :contract_room_sweeper, true)
Application.put_env(:thenvoi_com, :billing_enabled, false)
Application.put_env(:thenvoi_com, :oban, enabled: false)
Application.put_env(:thenvoi_com, :sandbox, false)
Mix.Task.run("app.start")
{:ok, _} = Agent.start_link(fn -> %{} end, name: ContractQualification.Metrics)

:telemetry.attach_many(
  "contract-qualification",
  [
    [:thenvoi_com, :contract_room, :decision],
    [:thenvoi_com, :repo, :query],
    [:phoenix, :channel_joined]
  ],
  fn event, _, _, _ ->
    Agent.update(
      ContractQualification.Metrics,
      &Map.update(&1, Enum.join(event, "."), 1, fn n -> n + 1 end)
    )
  end,
  nil
)

for peer <- String.split(System.fetch_env!("CONTRACT_LAB_PEERS"), ","),
    do: Node.connect(String.to_atom(peer))

data = ContractQualification.provision(Path.join(root, "fixture.json"))
ContractQualification.watch(root, data, index)
