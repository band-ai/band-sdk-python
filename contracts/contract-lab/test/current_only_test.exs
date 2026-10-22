defmodule ContractLab.CurrentOnlyTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Compiler, Engine, StateCodec}

  defp old_card do
    %{
      name: "old",
      description: "Retired contract profile.",
      engine: "0.1.0-lab.1",
      roles: ["a", "b"],
      lifetime: :infinite,
      inactivity_timeout: :infinite,
      mentions: %{per_message: 1, per_session: 20}
    }
  end

  @old_body "+TYPE ping\n+STATE new\n a -> b: ping() => done\n+END done\n+END expired\n"

  test "the normalized legacy envelope and direct compiler no longer admit 0.1.0" do
    assert {:error, :input} =
             ContractLab.compile_source(%{
               "card" => JSON.decode!(Canonical.encode(old_card())),
               "body" => @old_body
             })
  end

  test "direct compiler rejects the retired engine" do
    assert {:error, %{code: :engine}} = Compiler.compile(old_card(), @old_body)
  end

  test "native headers have no discovery path, even for the old engine" do
    bytes =
      "+SKILL old\n+SUMMARY Retired.\n+ENGINE 0.1.0-lab.1\n+ROLES a, b\n+LIFETIME infinite\n+INACTIVITY_TIMEOUT infinite\n+MENTIONS_PER_MESSAGE 1\n+MENTIONS_PER_SESSION 20\n+BODY\n" <>
        @old_body

    assert {:error, :frontmatter_required} = HeaderSpikes.discover_bytes(:native, bytes)
  end

  test "YAML card discovery rejects the retired engine" do
    fields = %{
      "name" => "old",
      "description" => "Retired.",
      "engine" => "0.1.0-lab.1",
      "roles" => ["a", "b"],
      "lifetime" => "infinite",
      "inactivity-timeout" => "infinite",
      "mentions-per-message" => 1,
      "mentions-per-session" => 20
    }

    assert {:error, :unsupported_engine} = HeaderSpikes.Schema.card(fields)
  end

  test "an old compiled value cannot start a session" do
    {:ok, current} = ContractLab.load("contracts/v2/assistant-answer.band", :frontmatter)

    assert {:error, :unsupported_engine} =
             Engine.new(%{current | engine: "0.1.0-lab.1"},
               session_id: "s",
               now: 0,
               max_messages: 10,
               inactivity: :accepted,
               bindings: Map.new(current.roles, &{&1, &1}),
               room_policy: :unchecked
             )
  end

  test "VM-term upgrade readers are gone, not just disabled by a version check" do
    Code.ensure_loaded!(StateCodec)
    refute function_exported?(StateCodec, :legacy, 3)
  end

  test "forged idle checkpoint state cannot reenable the removed timer" do
    {:ok, current} = ContractLab.load("contracts/v2/assistant-answer.band", :frontmatter)

    {:ok, state} =
      Engine.new(current,
        session_id: "s",
        now: 0,
        max_messages: 10,
        bindings: Map.new(current.roles, &{&1, &1}),
        room_policy: :unchecked,
        capabilities: current.card.extensions
      )

    packet = StateCodec.export(state)

    assert {:error, :invalid_checkpoint} =
             StateCodec.restore(current, put_in(packet, ["state", "idle_ms"], 1), 0,
               capabilities: current.card.extensions
             )
  end
end
