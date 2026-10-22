defmodule ContractLab.CollectionQuorumTest do
  @moduledoc "DF-25: a collection's `quorum` names the members that must settle ok."
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}
  @start 200_000_000

  defp source(quorum) do
    """
    ---
    name: review-lanes
    description: Floor lanes must settle ok before the run moves on.
    engine: 0.2.0-lab.1
    roles: [lead, lanes]
    extensions: [host.presence/1]
    ---
    contract @30m => expired
    type lane: agent
      required: boolean
    lanes: list(lane)
    type request
    type lane_report
      text: text(1..1000)
    type compacted
    state new
      lead -> lanes: request() => reviewing
    state reviewing
      collect reports from lanes -> lead @5m => verifying
        reply lane_report()
        offline @30s
        #{quorum}
    state verifying
      lead -> lanes: compacted() => done
    end done
    end short
    """
  end

  defp compile(quorum) do
    bytes = source(quorum)

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp start(quorum, presence \\ %{}) do
    assert {:ok, c} = compile(quorum)

    lanes =
      for {name, required} <- [{"sec", true}, {"web", true}, {"gen", false}],
          do: %{"name" => name, "actor" => name, "attributes" => %{"required" => required}}

    assert {:ok, s} =
             Engine.new(c,
               session_id: "s",
               now: @start,
               bindings: %{"lead" => "l", "lanes" => lanes},
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
    do: send(s, actor <> "-report", actor, "lane_report", at, %{"text" => actor <> " findings"})

  test "quorum compiles onto the collection and is absent when undeclared" do
    assert {:ok, c} = compile("quorum required => short")
    assert c.collections["reports"].quorum == %{attribute: "required", target: "short"}
    assert {:ok, c} = compile("quorum all => short")
    assert c.collections["reports"].quorum == %{attribute: nil, target: "short"}
    assert {:ok, c} = compile("")
    refute Map.has_key?(c.collections["reports"], :quorum)
  end

  test "DF-25 RED: every required lane timed out, so the close takes the quorum target" do
    {_, s} = start("quorum required => short")
    assert {%{status: :accepted, state: "reviewing"}, s} = report(s, "gen", 60_000)
    assert {:ok, s} = Engine.tick(s, @start + 300_000)
    assert s.state == "short"
    assert s.outcome == "short"
    [result] = s.collection_results
    assert result.reason == "deadline"
    assert result.members["lanes/sec"].status == "timed_out"
  end

  test "without quorum the same close still enters the collect target (today's behavior)" do
    {_, s} = start("")
    assert {%{status: :accepted}, s} = report(s, "gen", 60_000)
    assert {:ok, s} = Engine.tick(s, @start + 300_000)
    assert s.state == "verifying"
  end

  test "required lanes ok and an optional lane timed out meets the quorum" do
    {_, s} = start("quorum required => short")
    assert {%{status: :accepted}, s} = report(s, "sec", 60_000)
    assert {%{status: :accepted}, s} = report(s, "web", 70_000)
    assert {:ok, s} = Engine.tick(s, @start + 300_000)
    assert s.state == "verifying"
  end

  test "quorum all needs every member ok" do
    {_, s} = start("quorum all => short")
    assert {%{status: :accepted}, s} = report(s, "sec", 60_000)
    assert {%{status: :accepted}, s} = report(s, "web", 70_000)
    assert {:ok, s} = Engine.tick(s, @start + 300_000)
    assert s.state == "short"

    {_, s} = start("quorum all => short")
    assert {%{status: :accepted}, s} = report(s, "sec", 60_000)
    assert {%{status: :accepted}, s} = report(s, "web", 70_000)
    assert {%{status: :accepted, state: "verifying"}, _} = report(s, "gen", 80_000)
  end

  test "the reply that settles the last member enters the quorum target when a required lane is offline" do
    presence = %{"lanes/sec" => %{"value" => "offline", "since" => @start - 3_600_000}}
    {_, s} = start("quorum required => short", presence)
    assert {%{status: :accepted}, s} = report(s, "web", 60_000)

    assert {%{status: :accepted, state: "short", outcome: "short"}, s} =
             report(s, "gen", 70_000)

    [result] = s.collection_results
    assert result.reason == "settled"
    assert result.members["lanes/sec"].status == "offline"
  end

  test "static validation of quorum" do
    assert {:error, %{code: :type}} = compile("quorum missing => short")
    assert {:error, %{code: :unknown_state}} = compile("quorum required => nowhere")
    assert {:error, %{code: :initial_state_target}} = compile("quorum required => new")

    assert {:error, %{code: :duplicate}} =
             compile("quorum required => short\n    quorum all => short")

    assert {:error, %{code: :syntax}} = compile("QUORUM required => short")
  end
end
