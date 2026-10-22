defmodule ContractLab.CollectionTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}
  @start 200_000_000

  defp compile(extra, heartbeat) do
    bytes = """
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
    type heartbeat
    type summary
    state new
      lead -> reviewers: request() => reviewing
    state reviewing
      #{heartbeat}
      collect reports from reviewers -> lead @5m => summarizing
        reply review()
        #{extra}
    state summarizing
      lead -> reviewers: summary() => done
    end done
    """

    bytes = ContractLab.TestClock.ensure(bytes)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp start(presence \\ %{}, extra \\ "offline @30s", heartbeat \\ "") do
    assert {:ok, c} = compile(extra, heartbeat)

    bindings = %{
      "lead" => "l",
      "reviewers" => Enum.map(~w(sec web gen), &%{"name" => &1, "actor" => &1})
    }

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: @start,
               bindings: bindings,
               max_messages: 12,
               capabilities: ["host.presence/1"],
               max_controls: 32,
               initial_presence: presence,
               room_policy: :unchecked
             )

    assert {%{status: :accepted}, s} = send(s, "start", "l", "request", 0, %{})
    {c, s}
  end

  defp send(s, id, actor, kind, at, payload),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: @start + at
      )

  defp report(s, actor, at),
    do: send(s, actor <> "-report", actor, "review", at, %{"text" => actor <> " findings"})

  defp presence(s, actor, value, at, seq) do
    Engine.signal(
      s,
      %{
        "id" => "presence-#{seq}",
        "sequence" => seq,
        "subject" => "reviewers/" <> actor,
        "principal" => actor,
        "kind" => "presence",
        "value" => value,
        "at" => @start + at
      },
      authority: :host,
      now: @start + at
    )
  end

  test "all replies close early and leave the session active for synthesis" do
    {_, s} = start()
    assert {%{status: :accepted}, s} = report(s, "sec", 1)
    assert {%{status: :accepted}, s} = report(s, "web", 2)
    assert {%{status: :accepted, state: "summarizing", outcome: nil}, s} = report(s, "gen", 3)
    assert s.status == :active
    result = List.last(s.collection_results)
    assert result.reason == "settled"
    assert Enum.all?(result.members, fn {_, member} -> member.status == "ok" end)
    assert {%{status: :accepted, outcome: "done"}, _} = send(s, "summary", "l", "summary", 4, %{})
  end

  test "timeout returns partial results and exact missing members; equality is late" do
    {c, s} = start()
    assert {%{status: :accepted}, s} = report(s, "sec", 1)

    assert {:ok, s} =
             StateCodec.restore(c, StateCodec.export(s), @start + 200_000,
               capabilities: c.card.extensions
             )

    assert {%{status: :rejected, reason: :illegal_message}, s} = report(s, "web", 300_000)
    assert s.state == "summarizing"
    assert s.status == :active
    result = List.last(s.collection_results)
    assert result.reason == "deadline"
    assert result.members["reviewers/sec"].status == "ok"
    assert result.members["reviewers/web"].status == "timed_out"
    assert result.members["reviewers/gen"].status == "timed_out"
    assert {{:ok, view}, _} = Engine.view(s, actor: "l", now: s.now)
    assert List.last(view.collection_results) == result
    assert view.facts["sec-report"].payload["text"] == "sec findings"

    assert {:ok, _} =
             StateCodec.restore(c, StateCodec.export(s), s.now, capabilities: c.card.extensions)
  end

  test "one reply settles one member and cannot be counted twice" do
    {_, s} = start()
    assert {%{status: :accepted}, s} = report(s, "gen", 1)
    assert {%{status: :duplicate}, s} = report(s, "gen", 2)

    assert {%{status: :rejected, reason: :already_contributed}, s} =
             send(s, "gen-again", "gen", "review", 3, %{"text" => "more"})

    assert {%{status: :accepted}, s} = report(s, "sec", 4)
    assert {%{status: :accepted, state: "summarizing"}, s} = report(s, "web", 5)
    assert List.last(s.collection_results).members["reviewers/gen"].status == "ok"
  end

  test "a member offline before creation settles immediately after entering the collection" do
    old = %{"reviewers/gen" => %{"value" => "offline", "since" => @start - 172_800_000}}
    {c, s} = start(old)
    assert s.collections["reports"].members["reviewers/gen"].status == "offline"
    assert {%{status: :accepted}, s} = report(s, "sec", 1)
    assert {%{status: :accepted, state: "summarizing"}, s} = report(s, "web", 2)
    assert s.now < @start + 300_000

    assert {:ok, restored} =
             StateCodec.restore(c, StateCodec.export(s), s.now, capabilities: c.card.extensions)

    assert restored.collection_results == s.collection_results
  end

  test "brief offline grace and reconnect use the current continuous offline interval" do
    {c, s} = start()
    assert {%{status: :accepted}, s} = report(s, "sec", 1)
    assert {%{status: :accepted}, s} = report(s, "web", 2)
    assert {%{status: :host_accepted}, s} = presence(s, "gen", "offline", 10_000, 1)
    assert {:ok, %{state: "reviewing"} = s} = Engine.tick(s, @start + 39_999)
    assert {%{status: :host_accepted}, s} = presence(s, "gen", "online", 39_999, 2)
    assert {:ok, %{state: "reviewing"} = s} = Engine.tick(s, @start + 45_000)
    assert {%{status: :host_accepted}, s} = presence(s, "gen", "offline", 50_000, 3)

    assert {:ok, s} =
             StateCodec.restore(c, StateCodec.export(s), @start + 79_999,
               capabilities: c.card.extensions
             )

    assert {:ok, %{state: "summarizing"} = s} = Engine.tick(s, @start + 80_000)
    assert List.last(s.collection_results).members["reviewers/gen"].status == "offline"
    assert {%{status: :host_accepted}, s} = presence(s, "gen", "online", 80_001, 4)
    assert s.state == "summarizing"
  end

  test "unknown presence waits; fake presence in a business payload does not settle a lane" do
    {_, s} = start(%{"reviewers/gen" => %{"value" => "unknown", "since" => @start - 100_000}})

    assert {%{status: :rejected, reason: :payload}, s} =
             send(s, "fake", "sec", "review", 1, %{"text" => "x", "offline" => true})

    assert {:ok, s} = Engine.tick(s, @start + 30_000)
    assert s.collections["reports"].members["reviewers/gen"].status == "pending"

    assert {%{status: :rejected, reason: :host_authority_required}, _} =
             Engine.signal(s, %{}, authority: :agent, now: s.now)
  end

  test "self renewal names the collection and preserves results and offline grace" do
    {_, s} = start(%{}, "offline @30s", "event reviewers -> lead: heartbeat() => @self")
    assert {%{status: :accepted}, s} = report(s, "sec", 1)
    assert {%{status: :accepted}, s} = send(s, "hb", "web", "heartbeat", 240_000, %{})
    assert s.collections["reports"].deadline_at == @start + 540_000
    assert s.collections["reports"].members["reviewers/sec"].status == "ok"
  end

  test "all members already offline close immediately, while a recent disconnect keeps its remaining grace" do
    old =
      Map.new(
        ~w(sec web gen),
        &{"reviewers/" <> &1, %{"value" => "offline", "since" => @start - 60_000}}
      )

    {_, s} = start(old)
    assert s.state == "summarizing"
    assert List.last(s.collection_results).closed_at == @start
    recent = put_in(old, ["reviewers/gen", "since"], @start - 2_000)
    {_, s} = start(recent)
    assert s.state == "reviewing"
    assert {:ok, %{state: "reviewing"} = s} = Engine.tick(s, @start + 27_999)
    assert {:ok, %{state: "summarizing"}} = Engine.tick(s, @start + 28_000)
  end

  test "member views expose their own status without another member's private result identity" do
    {_, s} = start()
    assert {%{status: :accepted}, s} = report(s, "sec", 1)
    assert {{:ok, view}, _} = Engine.view(s, actor: "web", now: s.now)
    assert Map.keys(view.collections["reports"].members) == ["reviewers/web"]
    refute Map.has_key?(view.facts, "sec-report")
  end

  test "ordinary events never renew a collection and invalid source forms fail closed" do
    {_, s} = start(%{}, "offline @30s", "event reviewers -> lead: heartbeat()")
    assert {%{status: :accepted}, next} = send(s, "hb", "web", "heartbeat", 240_000, %{})
    assert next.collections["reports"].deadline_at == s.collections["reports"].deadline_at
    assert {:error, %{code: :duplicate}} = compile("offline @30s\n offline @1m", "")
    assert {:error, %{code: :duration}} = compile("offline @0s", "")
  end
end
