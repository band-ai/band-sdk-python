defmodule ContractLab.CheckpointInvariantsTest do
  # Live states that StateCodec.restore must accept (rr3 implementation review H2, H3).
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}
  @start 200_000_000

  @lanes """
  ---
  name: review-lanes
  description: Collect useful work despite unavailable lanes.
  engine: 0.2.0-lab.1
  roles: [lead, reviewers]
  extensions: [host.presence/1]
  ---
  contract @30m => expired
  type reviewer: agent
  reviewers: list(reviewer)
  type request
  type review
    text: text(1..1000)
  type summary
  state new
    lead -> reviewers: request() => reviewing
  state reviewing
    on lead offline => paused
    collect reports from reviewers -> lead @5m => summarizing
      reply review()
  state paused
    on lead online => reviewing
    lead -> reviewers: summary() => done
  state summarizing
    lead -> reviewers: summary() => done
  end done
  """

  defp compile(bytes) do
    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => bytes,
               "sha256" => Canonical.hash(bytes)
             })

    c
  end

  defp new(c, opts \\ []) do
    assert {:ok, s} =
             Engine.new(
               c,
               [
                 session_id: "s",
                 now: @start,
                 bindings: %{
                   "lead" => "l",
                   "reviewers" => Enum.map(~w(sec web gen), &%{"name" => &1, "actor" => &1})
                 },
                 max_messages: 4,
                 capabilities: ["host.presence/1"],
                 max_controls: 1024,
                 room_policy: :unchecked
               ]
               |> Keyword.merge(opts)
             )

    s
  end

  defp step(s, actor, id, kind, at, payload \\ %{}),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: @start + at
      )

  defp presence(s, seq, value, at),
    do:
      Engine.signal(
        s,
        %{
          "id" => "p-#{seq}",
          "sequence" => seq,
          "subject" => "lead",
          "principal" => "l",
          "kind" => "presence",
          "value" => value,
          "at" => @start + at
        },
        authority: :host,
        now: @start + at
      )

  defp round_trip!(c, s) do
    assert {:ok, restored} =
             StateCodec.restore(c, StateCodec.export(s), s.now, capabilities: c.card.extensions)

    restored
  end

  defp flap(s, c, from, count) do
    Enum.reduce(from..(from + count - 1), s, fn i, s ->
      assert {%{status: :host_accepted}, s} = presence(s, 2 * i - 1, "offline", 10 * i)
      assert {%{status: :host_accepted}, s} = presence(s, 2 * i, "online", 10 * i + 1)
      round_trip!(c, s)
      s
    end)
  end

  describe "H2: cancelled collection rounds and max_messages" do
    test "presence flaps past max_messages keep every checkpoint restorable and the round open" do
      c = compile(@lanes)
      s = new(c)
      assert {%{status: :accepted}, s} = step(s, "l", "start", "request", 0)
      s = flap(s, c, 1, 6)

      assert s.status == :active and s.state == "reviewing"
      assert Enum.count(s.collection_log, &(&1["reason"] == "cancelled")) == 6
      # A round cancelled before anyone contributed carries no result.
      assert s.collection_results == []

      s = round_trip!(c, s)
      assert {%{status: :accepted}, s} = step(s, "sec", "sec-r", "review", 700, %{"text" => "x"})
      assert {%{status: :accepted}, s} = step(s, "web", "web-r", "review", 701, %{"text" => "x"})

      assert {%{status: :accepted, state: "summarizing"}, s} =
               step(s, "gen", "gen-r", "review", 702, %{"text" => "x"})

      assert s.status == :active
      assert [%{reason: "settled"}] = s.collection_results
      round_trip!(c, s)
    end

    test "a cancelled round with a contribution keeps its result and restores" do
      c = compile(@lanes)
      # Re-entry needs a reply from every reviewer, so leave room for them.
      s = new(c, max_messages: 10)
      assert {%{status: :accepted}, s} = step(s, "l", "start", "request", 0)
      assert {%{status: :accepted}, s} = step(s, "sec", "sec-r", "review", 5, %{"text" => "x"})
      s = flap(s, c, 1, 1)

      assert [%{reason: "cancelled", members: members}] = s.collection_results
      assert members["reviewers/sec"].status == "ok"
      assert members["reviewers/web"].status == "cancelled"
      s = flap(s, c, 2, 5)
      assert s.status == :active and s.state == "reviewing"
      assert length(s.collection_results) == 1
      round_trip!(c, s)
    end
  end

  describe "H3: same-value presence after a past-dated initial_presence" do
    @seeded %{"lead" => %{"value" => "online", "since" => @start - 60_000}}

    test "heartbeats keep the seeded since and every checkpoint restores" do
      c = compile(@lanes)
      s = new(c, initial_presence: @seeded)
      round_trip!(c, s)

      s =
        Enum.reduce(2..4, s, fn seq, s ->
          assert {%{status: :host_accepted}, s} = presence(s, seq, "online", seq * 10)

          assert %{"epoch" => "initial:lead", "since" => since, "sequence" => ^seq} =
                   s.signals["lead/presence"]

          assert since == @start - 60_000
          round_trip!(c, s)
        end)

      # A change of value starts a new epoch at the event time.
      assert {%{status: :host_accepted}, s} = presence(s, 5, "offline", 50)
      assert %{"epoch" => "p-5", "since" => since} = s.signals["lead/presence"]
      assert since == @start + 50
      round_trip!(c, s)
    end

    test "only the seeded epoch may predate creation" do
      c = compile(@lanes)
      s = new(c, initial_presence: @seeded)
      assert {%{status: :host_accepted}, s} = presence(s, 2, "online", 10)
      checkpoint = StateCodec.export(s)

      forged = put_in(checkpoint, ["state", "signals", "lead/presence", "epoch"], "p-2")

      assert {:error, :invalid_checkpoint} =
               StateCodec.restore(c, forged, s.now, capabilities: c.card.extensions)

      unseeded = c |> new() |> StateCodec.export()

      forged =
        put_in(unseeded, ["state", "signals"], %{
          "lead/presence" => %{
            "epoch" => "initial:lead",
            "sequence" => 1,
            "since" => @start - 60_000,
            "value" => "online"
          }
        })
        |> put_in(["state", "signal_revision"], 1)

      assert {:error, :invalid_checkpoint} =
               StateCodec.restore(c, forged, @start, capabilities: c.card.extensions)
    end
  end

  describe "L20: restore time order" do
    test "a timestamp later than now, or entry before creation, is rejected" do
      c = compile(@lanes)
      s = new(c)
      assert {%{status: :accepted}, s} = step(s, "l", "start", "request", 1_000)
      checkpoint = StateCodec.export(s)
      assert {:ok, _} = StateCodec.restore(c, checkpoint, s.now, capabilities: c.card.extensions)

      for {key, value} <- [
            {"last_activity", s.now + 1},
            {"entered_at", s.now + 1},
            {"created_at", s.entered_at + 1}
          ] do
        forged = put_in(checkpoint, ["state", key], value)

        assert {:error, :invalid_checkpoint} =
                 StateCodec.restore(c, forged, s.now, capabilities: c.card.extensions),
               key
      end
    end
  end

  describe "L19: current checkpoint hash" do
    test "a current room checkpoint restores only with the hash of its exact bytes" do
      {:ok, c} = ContractLab.load(Path.expand("../contracts/assignment.band", __DIR__))

      principals =
        Map.new(
          c.roles,
          &{"actor:" <> &1, %{kind: :agent, active: true, permission: "participant"}}
        )

      policy = %{revision: "room-1", fixed_roles: %{}, principals: principals}

      {:ok, s} =
        Engine.new(c,
          session_id: "s",
          bindings: Map.new(c.roles, &{&1, "actor:" <> &1}),
          now: 100,
          inactivity: :accepted,
          max_messages: 100,
          room_policy: policy,
          capabilities: c.card.extensions
        )

      {:ok, exported} = ContractLab.Checkpoint.export(s)
      packet = exported |> Canonical.encode() |> JSON.decode!()
      bytes = Canonical.encode(packet["data"])

      assert {:ok, ^s} =
               ContractLab.Checkpoint.restore(c, packet, 100, :hosted,
                 capabilities: c.card.extensions
               )

      for hash <- [Canonical.hash(bytes <> "x"), String.duplicate("0", 64), ""] do
        assert {:error, :invalid_checkpoint} =
                 ContractLab.Checkpoint.restore(c, %{packet | "hash" => hash}, 100, :hosted,
                   capabilities: c.card.extensions
                 )
      end
    end
  end
end
