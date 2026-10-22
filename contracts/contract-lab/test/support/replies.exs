defmodule ContractLab.RepliesFixture do
  @moduledoc false
  # Reply contracts compiled from head-form sources (contract-dsl.md, Reply alternatives),
  # and a one-way base contract with no reply block. The compiler numbers the deploy
  # contract's head rule-0, its alternatives rule-1..rule-3 and the timeout due-4.
  alias ContractLab.Canonical

  @header "---\nname: deploy\ndescription: Ok and error replies with retry.\nengine: 0.2.0-lab.1\nroles: [lead, deployer]\n---\n"

  # One way: nothing answers the deploy, so the contract has no reply block.
  @base """
  ---
  name: deploy
  description: One deploy request, never answered.
  engine: 0.2.0-lab.1
  roles: [lead, deployer]
  ---
  contract @1h => expired
  type deploy
  type progress
  type cancel
  state new
    lead -> deployer: deploy() as job => deploying
  state deploying
    event deployer -> lead: progress()
    lead -> deployer: cancel() => failed
  end failed
  """

  @doc "A contract without reply lines: no extension, no wait."
  def base, do: compile(@base)

  # retrying re-sends into deploying (rule-6), so deploying lies on a cycle and needs
  # max_entries; postmortem's owed ack (rule-8, due-9) sizes the head's reserve; the anystate
  # cancel and quit are rule-10 and rule-11.
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
  type cancel
  type quit
  type ack
  state new
    lead -> deployer: deploy() as job awaits deploying
      => ok(request=$job) & deployed
       | error(request=$job, retryable=true) & retrying
       | error(request=$job, retryable=false) & postmortem
       | @10m & stalled
  state deploying max_entries 3 => exhausted
    event deployer -> lead: progress() => @self
  state retrying @30m => stalled
    lead -> deployer: deploy() as job => deploying
    lead -> deployer: abandon() => failed
  state postmortem
    lead -> deployer: ack() @5m => failed | stalled
  anystate
    lead -> deployer: cancel() => failed
    deployer -> lead: quit() => failed
  end deployed
  end failed
  end stalled
  end exhausted
  """

  @doc """
  The deploy contract with a reply wait in `deploying` (timeout @10m => stalled); `after_ms`
  sets the timeout, in whole seconds.
  """
  def deploy(opts \\ []) do
    wait = duration(Keyword.get(opts, :after_ms, 600_000))
    compile(@header <> String.replace(@deploy, "| @10m & stalled", "| @#{wait} & stalled"))
  end

  defp duration(ms) when rem(ms, 60_000) == 0, do: "#{div(ms, 60_000)}m"
  defp duration(ms) when rem(ms, 1_000) == 0, do: "#{div(ms, 1_000)}s"

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

  @doc """
  Two waits on the same reply kind with no cycle (Victor's witness, Lead decision 2):
  W1 `first` (head rule-0, alternative rule-1, due-2), the requester cancels into `between`,
  whose head rule-4 opens W2 `second` (alternative rule-5, due-6).
  """
  def relay, do: compile(@header <> @relay)

  defp compile(bytes) do
    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

    c
  end

  # A fresh digest over a test's rewritten object, so it never claims the compiled identity.
  def redigest(c) do
    semantic = Map.drop(c, [:digest, :source_digest, :card])
    %{c | digest: Canonical.digest(%{replies: semantic, base: c.digest})}
  end

  def start(c, opts \\ []) do
    ContractLab.Engine.new(
      c,
      Keyword.merge(
        [
          session_id: "s",
          now: 0,
          max_messages: 40,
          bindings: %{"lead" => "l", "deployer" => "d"},
          room_policy: :unchecked,
          capabilities: c.card.extensions
        ],
        opts
      )
    )
  end

  def send(s, id, actor, kind, now, payload \\ %{}),
    do:
      ContractLab.Engine.step(
        s,
        %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: now
      )
end
