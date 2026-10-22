defmodule ContractLab.ReplyRestoreAdmissionTest do
  # Sofia O1: every restore path refuses exactly what Engine.new refuses for the contract:
  # extensions this engine does not implement, reply structures without contract.replies/1,
  # uncorrelated reply blocks, and (when the host states them) missing host capabilities.
  # Each bad contract keeps the good one's digests, as a forged compiled object would.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Checkpoint, Engine, RepliesFixture, StateCodec}
  import RepliesFixture, only: [send: 5, send: 6]

  setup do
    c = RepliesFixture.deploy()
    assert {:ok, s} = RepliesFixture.start(c)
    assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)
    # Checkpoint.restore takes the packet as it travels: JSON, string keys.
    assert {:ok, packet} = Checkpoint.export(s)
    packet = packet |> Canonical.encode() |> JSON.decode!()
    %{c: c, s: s, data: StateCodec.export(s), snapshot: Engine.snapshot(s), packet: packet}
  end

  defp forged(c, fun), do: fun.(c) |> Map.merge(Map.take(c, [:digest, :source_digest]))

  defp variants(c) do
    [
      uncorrelated:
        forged(c, fn c ->
          update_in(c, [:states, "deploying", :rules], fn rules ->
            Enum.map(rules, &if(Map.has_key?(&1, :literals), do: %{&1 | matches: %{}}, else: &1))
          end)
        end),
      unimplemented:
        forged(
          c,
          &%{&1 | card: %{&1.card | extensions: ["contract.replies/1", "contract.replies/2"]}}
        ),
      ungated: forged(c, &%{&1 | card: %{&1.card | extensions: []}})
    ]
  end

  defp new_error(c) do
    assert {:error, reason} = RepliesFixture.start(c)
    reason
  end

  test "Engine.restore refuses each contract new refuses, with new's reason", ctx do
    for {name, bad} <- variants(ctx.c) do
      expected = new_error(bad)
      caps = [capabilities: bad.card.extensions]
      assert {^name, {:error, ^expected}} = {name, Engine.restore(bad, ctx.snapshot, 20, caps)}
    end
  end

  test "StateCodec.restore refuses each contract new refuses, with new's reason", ctx do
    for {name, bad} <- variants(ctx.c) do
      expected = new_error(bad)
      caps = bad.card.extensions

      assert {^name, {:error, ^expected}} =
               {name, StateCodec.restore(bad, ctx.data, 20, capabilities: caps)}

      assert {^name, {:error, ^expected}} =
               {name, StateCodec.restore(bad, ctx.data, 20, redacted: true, capabilities: caps)}
    end
  end

  test "Checkpoint.restore refuses each contract new refuses, with new's reason", ctx do
    for {name, bad} <- variants(ctx.c), profile <- [:hosted, :local_reference] do
      expected = new_error(bad)
      caps = [capabilities: bad.card.extensions]

      assert {^name, {:error, ^expected}} =
               {name, Checkpoint.restore(bad, ctx.packet, 20, profile, caps)}
    end
  end

  test "a host that lacks contract.replies/1 cannot restore the session it could not start",
       ctx do
    assert {:error, :unsupported_extension} = RepliesFixture.start(ctx.c, capabilities: [])

    assert {:error, :unsupported_extension} =
             Engine.restore(ctx.c, ctx.snapshot, 20, capabilities: [])

    assert {:error, :unsupported_extension} =
             StateCodec.restore(ctx.c, ctx.data, 20, capabilities: [])

    assert {:error, :unsupported_extension} =
             Checkpoint.restore(ctx.c, ctx.packet, 20, :local_reference, capabilities: [])

    caps = ["contract.replies/1"]
    assert {:ok, _} = Engine.restore(ctx.c, ctx.snapshot, 20, capabilities: caps)
    assert {:ok, _} = StateCodec.restore(ctx.c, ctx.data, 20, capabilities: caps)

    assert {:ok, _} =
             Checkpoint.restore(ctx.c, ctx.packet, 20, :local_reference, capabilities: caps)
  end

  test "strict: a restore that states no capabilities has none, exactly like new", ctx do
    assert {:error, :unsupported_extension} = Engine.restore(ctx.c, ctx.snapshot, 20)
    assert {:error, :unsupported_extension} = StateCodec.restore(ctx.c, ctx.data, 20)

    assert {:error, :unsupported_extension} =
             Checkpoint.restore(ctx.c, ctx.packet, 20, :local_reference)
  end

  test "a contract without extensions still restores with no capabilities stated" do
    c = RepliesFixture.base()
    assert {:ok, s} = RepliesFixture.start(c)
    assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)
    assert {:ok, _} = Engine.restore(c, Engine.snapshot(s), 20)
    assert {:ok, _} = StateCodec.restore(c, StateCodec.export(s), 20)
  end

  test "O3: the head check follows W's alternatives, not a rule's own wait key", ctx do
    c =
      ctx.c
      |> update_in([:states, "deploying", :rules], fn rules ->
        Enum.map(rules, &if(&1.id == "rule-1", do: Map.delete(&1, :wait), else: &1))
      end)

    assert {:ok, s} = RepliesFixture.start(c)
    assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)

    assert {%{status: :accepted}, s} =
             send(s, "e1", "d", "error", 20, %{"request" => "d1", "retryable" => true})

    assert {%{status: :accepted, state: "deploying"}, s} = send(s, "d2", "l", "deploy", 30)
    stale = %{s | variables: Map.put(s.variables, "job", "d1")}

    assert {%{status: :rejected, reason: :reference}, _} =
             send(stale, "late", "d", "ok", 40, %{"request" => "d1"})
  end
end
