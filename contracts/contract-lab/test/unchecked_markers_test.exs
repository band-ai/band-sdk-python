defmodule ContractLab.UncheckedMarkersTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  defp state(mode \\ :unchecked) do
    content = """
    ---
    name: unchecked-markers
    description: Every local unchecked result identifies its policy mode.
    engine: 0.2.0-lab.1
    roles: [lead, worker]
    extensions: [host.presence/1]
    ---
    contract @infinity => expired
    type notice
    state new
      event lead -> worker: notice()
      on worker offline for 1m => expired
    end expired
    """

    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => content,
        "sha256" => Canonical.hash(content)
      })

    policy =
      if mode == :unchecked,
        do: mode,
        else: %{
          revision: "r1",
          fixed_roles: %{},
          principals:
            Map.new(~w(L W), &{&1, %{kind: :agent, active: true, permission: "participant"}})
        }

    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        now: 0,
        max_messages: 10,
        bindings: %{"lead" => "L", "worker" => "W"},
        capabilities: ["host.presence/1"],
        max_controls: 10,
        room_policy: policy
      )

    {s, policy}
  end

  defp event do
    %{
      "id" => "h1",
      "sequence" => 1,
      "subject" => "worker",
      "principal" => "W",
      "kind" => "presence",
      "value" => "offline",
      "at" => 0
    }
  end

  defp notice do
    %{"id" => "m1", "session" => "s", "kind" => "notice", "payload" => %{}}
  end

  test "member authorization and clock rejections keep their reasons and state, with the marker" do
    {s, _} = state()

    assert {%{status: :rejected, reason: :unauthorized, policy: :unchecked}, ^s} =
             Engine.step(s, notice(), actor: "intruder", now: 0)

    assert {%{status: :rejected, reason: :invalid_time, policy: :unchecked}, ^s} =
             Engine.step(s, notice(), actor: "L", now: -1)

    assert {%{status: :rejected, reason: :room_access, policy: :unchecked}, ^s} =
             Engine.step(s, notice(),
               actor: "L",
               now: 0,
               room_policy: %{revision: "revoked", fixed_roles: %{}, principals: %{}}
             )
  end

  test "host decisions mark accepts, exact retries and authorization rejections" do
    {s, _} = state()

    assert {%{status: :rejected, reason: :host_authority_required, policy: :unchecked}, ^s} =
             Engine.signal(s, event(), now: 0)

    assert {%{status: :host_accepted, policy: :unchecked}, s} =
             Engine.signal(s, event(), authority: :host, now: 0)

    assert {%{status: :duplicate_host_event, policy: :unchecked}, ^s} =
             Engine.signal(s, event(), authority: :host, now: 0)
  end

  test "contribution pre-admission errors and advisory decisions are marked" do
    {s, _} = state()

    assert {%{status: :rejected, reason: :unsupported_extension, policy: :unchecked}, ^s} =
             Engine.stop_contribution(s, %{}, actor: "intruder", now: 0)

    assert {%{status: :rejected, reason: :unsupported_extension, policy: :unchecked}, ^s} =
             Engine.fail_contribution(s, %{}, now: 0)

    assert {%{status: :rejected, reason: :unauthorized, policy: :unchecked}, ^s} =
             Engine.preview(s, notice(), actor: "intruder", now: 0)
  end

  test "summary marks unchecked state without mutating or expanding the projection" do
    {s, _} = state()

    assert Engine.summary(s) == %{
             id: "s",
             now: 0,
             state: "new",
             status: :active,
             outcome: nil,
             version: 0,
             policy: :unchecked
           }

    assert {:ok, advanced} = Engine.tick(s, 60_000)
    assert %{status: :active, policy: :unchecked, now: 60_000} = Engine.summary(advanced)

    assert {:ok, restored} =
             StateCodec.restore(s.contract, StateCodec.export(s), 0,
               capabilities: s.contract.card.extensions
             )

    assert Engine.summary(restored) == Engine.summary(s)
  end

  test "checked decisions, controls, summaries and checkpoints remain unmarked" do
    {s, policy} = state(:checked)
    {rejected, ^s} = Engine.step(s, notice(), actor: "intruder", now: 0, room_policy: policy)
    {host, ^s} = Engine.signal(s, event(), now: 0)
    refute Map.has_key?(rejected, :policy)
    refute Map.has_key?(host, :policy)
    refute Map.has_key?(Engine.summary(s), :policy)
    refute Map.has_key?(StateCodec.export(s)["state"], "policy_mode")
  end
end
