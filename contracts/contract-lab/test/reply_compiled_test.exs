defmodule ContractLab.ReplyCompiledTest do
  # Integration: reply blocks compiled by Kenji's compiler (COMPILED-SHAPE.md) run on the
  # engine with the same results as the RepliesFixture contracts in reply_wait_test.exs.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, RepliesFixture, StateCodec}
  import RepliesFixture, only: [send: 5, send: 6]

  @header "---\nname: deploy\ndescription: Ok and error replies with retry.\nengine: 0.2.0-lab.1\nroles: [lead, deployer]\n---\n"

  # DESIGN v2 §7, with a state clock on retrying and a renewing progress event.
  @deploy """
  contract @1h => expired
  type deploy
  type ok
    request: ref(deploy)
  type error
    request: ref(deploy)
    retryable: boolean
  type progress
  type abandon
  state new
    lead -> deployer: deploy() as job awaits deploying
      => ok(request=$job) & deployed
       | error(request=$job, retryable=true) & retrying
       | error(request=$job, retryable=false) & failed
       | @10m & stalled
  state deploying max_entries 3 => exhausted
    event deployer -> lead: progress() => @self
  state retrying @30m => stalled
    lead -> deployer: deploy() as job => deploying
    lead -> deployer: abandon() => failed
  end deployed
  end failed
  end stalled
  end exhausted
  """

  # Victor's witness: two waits on one reply kind, no cycle.
  @relay """
  contract @1h => expired
  type deploy
  type ok
    request: ref(deploy)
  type cancel
  state new
    lead -> deployer: deploy() as job awaits first
      => ok(request=$job) & done
       | @10m & stalled
  state first
    lead -> deployer: cancel() => between
  state between
    lead -> deployer: deploy() as job awaits second
      => ok(request=$job) & done
       | @10m & stalled
  end done
  end stalled
  """

  # The same witness without correlation: ok has no ref field to correlate on.
  @uncorrelated """
  contract @1h => expired
  type deploy
  type ok
  type cancel
  state new
    lead -> deployer: deploy() awaits first
      => ok() & done
       | @10m & stalled
  state first
    lead -> deployer: cancel() => between
  state between
    lead -> deployer: deploy() awaits second
      => ok() & done
       | @10m & stalled
  end done
  end stalled
  """

  defp compile(body) do
    bytes = @header <> body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp started(body) do
    assert {:ok, c} = compile(body)
    assert {:ok, s} = RepliesFixture.start(c)
    {c, s}
  end

  test "the compiled wait has the keys the engine reads" do
    assert {:ok, c} = compile(@deploy)
    assert c.card.extensions == ["contract.replies/1"]

    assert %{
             id: "due-4",
             heads: ["rule-0", "rule-6"],
             from: ["deployer"],
             to: ["lead"],
             kinds: ["ok", "error"],
             after_ms: 600_000,
             outcome: "stalled",
             alternatives: ["rule-1", "rule-2", "rule-3"]
           } = c.states["deploying"].wait

    assert %{reset: %{scope: "wait", duty: "due-4"}} =
             Enum.find(c.states["deploying"].rules, &(&1.kind == "progress"))
  end

  test "each reply lands in its own state; a retry's stale reply fails reference" do
    {_c, s} = started(@deploy)
    assert {%{status: :accepted, state: "deploying"}, s} = send(s, "d1", "l", "deploy", 10)
    assert s.wait == %{id: "due-4", head: "d1", due_at: 600_010, remaining: ["deployer"]}

    assert {%{status: :accepted, state: "retrying"}, s} =
             send(s, "e1", "d", "error", 20, %{"request" => "d1", "retryable" => true})

    assert {%{status: :accepted, state: "deploying"}, s} = send(s, "d2", "l", "deploy", 30)

    assert {%{status: :rejected, reason: :reference}, s} =
             send(s, "late", "d", "ok", 40, %{"request" => "d1"})

    assert {%{status: :accepted, state: "deploying"}, s} = send(s, "p1", "d", "progress", 50)
    assert s.wait.due_at == 50 + 600_000

    assert {%{status: :accepted, state: "failed"}, _} =
             send(s, "e2", "d", "error", 60, %{"request" => "d2", "retryable" => false})

    assert {%{status: :accepted, state: "deployed"}, _} =
             send(s, "o2", "d", "ok", 60, %{"request" => "d2"})
  end

  test "the compiled wait times out, saves and restores" do
    {c, s} = started(@deploy)
    assert {%{status: :accepted}, s} = send(s, "d1", "l", "deploy", 10)
    data = StateCodec.export(s)
    assert data["profile"] == "band-contract/state/6"
    assert {:ok, restored} = StateCodec.restore(c, data, 20, capabilities: c.card.extensions)
    assert restored.wait == s.wait
    assert {:ok, %{outcome: "stalled", expiry: %{id: "due-4"}}} = Engine.tick(restored, 600_010)
  end

  test "no structural correlation: an uncorrelated block never runs, at compile time or at start" do
    # Whichever side refuses first, the engine refuses it at start either way.
    case compile(@uncorrelated) do
      {:ok, c} -> assert {:error, :reply_correlation} = RepliesFixture.start(c)
      {:error, error} -> assert error.code == :reply_correlation
    end
  end

  test "W1 -> cancel -> W2 compiled: the late W1 reply fails reference" do
    {_c, s} = started(@relay)
    assert {%{status: :accepted, state: "first"}, s} = send(s, "d1", "l", "deploy", 10)
    assert {%{status: :accepted, state: "between"}, s} = send(s, "x1", "l", "cancel", 20)
    assert {%{status: :accepted, state: "second"}, s} = send(s, "d2", "l", "deploy", 30)

    assert {%{status: :rejected, reason: :reference}, s} =
             send(s, "late", "d", "ok", 40, %{"request" => "d1"})

    assert {%{status: :accepted, state: "done"}, _} =
             send(s, "r2", "d", "ok", 50, %{"request" => "d2"})
  end
end
