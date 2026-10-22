defmodule ContractLab.ReplyExitsViewTest do
  # Sofia K-2: the views list every rule that applies in a wait state W and moves the state,
  # with its sender roles: the requester's own rule in `state W`, a third role's and the
  # replier's anystate rules. Events and the reply alternatives themselves are not exits.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, RepliesFixture}
  import RepliesFixture, only: [send: 5]

  @source """
  ---
  name: exits
  description: Who can end a wait.
  engine: 0.2.0-lab.1
  roles: [lead, deployer, admin]
  ---
  contract @1h => expired
  type deploy
  type ok
    request: ref(deploy)
  type cancel
  type halt
  type quit
  type progress
  state new
    lead -> deployer: deploy() as job awaits deploying
      => ok(request=$job) & deployed
       | @10m & stalled
  state deploying
    lead -> deployer: cancel() => stopped
    event deployer -> lead: progress()
  anystate
    admin -> lead: halt() => stopped
    deployer -> lead: quit() => stopped
  end deployed
  end stopped
  end stalled
  """

  setup do
    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => @source,
               "sha256" => Canonical.hash(@source)
             })

    id = fn kind ->
      Enum.find(c.states["deploying"].rules ++ c.any_rules, &(&1.kind == kind)).id
    end

    exits = [
      %{rule: id.("cancel"), kind: "cancel", target: "stopped", from: ["lead"]},
      %{rule: id.("halt"), kind: "halt", target: "stopped", from: ["admin"]},
      %{rule: id.("quit"), kind: "quit", target: "stopped", from: ["deployer"]}
    ]

    %{c: c, exits: exits}
  end

  test "the live view lists the requester's, a third role's and the replier's exits", ctx do
    bindings = %{"lead" => "l", "deployer" => "d", "admin" => "a"}
    assert {:ok, s} = RepliesFixture.start(ctx.c, bindings: bindings)
    assert {%{status: :accepted, state: "deploying"}, s} = send(s, "d1", "l", "deploy", 10)

    for actor <- ["l", "d"] do
      assert {{:ok, view}, _} = Engine.view(s, actor: actor, now: 20, room_policy: :unchecked)
      assert view.wait.exits == ctx.exits
    end

    # The third role is not party to the wait: it sees no wait at all (O4).
    assert {{:ok, view}, _} = Engine.view(s, actor: "a", now: 20, room_policy: :unchecked)
    assert view.wait == nil
  end

  test "the role definition view lists the same exits", ctx do
    for role <- ["lead", "deployer"] do
      assert {:ok, view} = ContractLab.for_role(ctx.c, role)
      assert view.states["deploying"].wait.exits == ctx.exits
    end
  end
end
