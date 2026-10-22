defmodule ContractLab.ReplyAlternativesTest do
  @moduledoc """
  Reply alternatives (ubf-replies/DESIGN.md v2 §2, §4, §5) in syntax U (Eric decision 4;
  SYNTAX-OPTIONS.md §Option U, §sugar; Theo's U PRODUCTIONS.md): a send that awaits its wait
  state, `=> reply() & state` and `| reply() & state` alternatives closed by `| @d & outcome`,
  the reply_* compile codes, and the compiled `wait`, `literals` and numbering. Core syntax:
  the compiler records contract.replies/1 itself.
  """
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @types """
  type deploy
  type ok
    request: ref(deploy)
  type error
    request: ref(deploy)
    retryable: boolean
  type progress
  type abandon
  """

  @deploy """
  state new
    lead -> deployer: deploy() as job awaits deploying
      => ok(request=$job) & deployed
       | error(request=$job, retryable=true) & retrying
       | error(request=$job, retryable=false) & failed
       | @10m & stalled
  state deploying max_entries 3 => exhausted
    event deployer -> lead: progress() => @self
  state retrying
    "Resend; a reply to the earlier deploy can no longer match."
    lead -> deployer: deploy() as job => deploying
    lead -> deployer: abandon() => failed
  end deployed
  end failed
  end stalled
  end exhausted
  """

  # SYNTAX-OPTIONS.md's deploy: the same contract with the sugar (no `as`, no `=$`).
  @sugared """
  state new
    lead -> deployer: deploy() awaits deploying
      => ok() & deployed
       | error(retryable=true) & retrying
       | error(retryable=false) & failed
       | @10m & stalled
  state deploying max_entries 3 => exhausted
    event deployer -> lead: progress() => @self
  state retrying
    "Resend; a reply to the earlier deploy can no longer match."
    lead -> deployer: deploy() => deploying
    lead -> deployer: abandon() => failed
  end deployed
  end failed
  end stalled
  end exhausted
  """

  defp compile(body, roles \\ "lead, deployer, reviewer", extensions \\ nil) do
    bytes =
      "---\nname: replies\ndescription: Reply alternatives.\nengine: 0.2.0-lab.1\nroles: [#{roles}]\n" <>
        if(extensions, do: "extensions: [#{extensions}]\n", else: "") <>
        "---\ncontract @1h => expired\n" <> body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp error(body, roles \\ "lead, deployer, reviewer") do
    assert {:error, %{code: code, line: line}} = compile(body, roles)
    {code, line}
  end

  # Lines count from the end of the frontmatter; the contract line comes first.
  defp line(n), do: n + 1

  defp rule(c, state, id), do: Enum.find(c.states[state].rules, &(&1.id == id))

  describe "the ok/error deploy with retry (DESIGN §7)" do
    setup do
      assert {:ok, c} = compile(@types <> @deploy)
      %{c: c}
    end

    test "records contract.replies/1 in the compiled extensions", %{c: c} do
      assert c.card.extensions == ["contract.replies/1"]
    end

    test "numbers the head, its alternatives, the timeout and what follows", %{c: c} do
      assert [%{id: "rule-0", target: "deploying", bind: "job"}] = c.states["new"].rules

      assert Enum.map(c.states["deploying"].rules, & &1.id) ==
               ~w(rule-1 rule-2 rule-3 rule-5)

      assert Enum.map(c.states["retrying"].rules, & &1.id) == ~w(rule-6 rule-7)
      refute Map.has_key?(rule(c, "new", "rule-0"), :wait)
      refute Map.has_key?(rule(c, "new", "rule-0"), :literals)
    end

    test "compiles each alternative with its literals, references and wait", %{c: c} do
      assert %{
               from: ["deployer"],
               to: ["lead"],
               kind: "ok",
               event: false,
               matches: %{"request" => "job"},
               literals: literals,
               wait: "due-4",
               target: "deployed",
               bind: nil
             } = rule(c, "deploying", "rule-1")

      assert literals == %{}

      assert %{kind: "error", literals: %{"retryable" => true}, target: "retrying"} =
               rule(c, "deploying", "rule-2")

      assert %{kind: "error", literals: %{"retryable" => false}, target: "failed"} =
               rule(c, "deploying", "rule-3")
    end

    test "state W carries the wait; its deadlines are unchanged", %{c: c} do
      s = c.states["deploying"]
      assert s.deadlines == []
      assert s.max_entries == %{limit: 3, outcome: "exhausted"}

      assert %{
               id: "due-4",
               heads: ["rule-0", "rule-6"],
               from: ["deployer"],
               to: ["lead"],
               kinds: ["ok", "error"],
               after_ms: 600_000,
               outcome: "stalled",
               alternatives: ["rule-1", "rule-2", "rule-3"]
             } = s.wait
    end

    test "@self in state W renews the wait", %{c: c} do
      assert %{
               reset: %{profile: "band-contract/renewal/1", scope: "wait", duty: "due-4"}
             } = rule(c, "deploying", "rule-5")

      assert c.renewal_profile == "band-contract/renewal/1"
    end

    test "the canonical form encodes literals as JSON booleans", %{c: c} do
      json = Canonical.encode(rule(c, "deploying", "rule-2"))
      assert json =~ ~s("literals":{"retryable":true})
      assert json =~ ~s("wait":"due-4")
    end
  end

  test "one-review: an undeclared W is created from the block" do
    body = """
    type request
    type review
      request: ref(request)
    state new
      lead -> reviewer: request() as req awaits reviewing
        "Submit the report within the response window."
        => review(request=$req) & collected
         | @5m & stalled
    end collected
    end stalled
    """

    assert {:ok, c} = compile(body)

    assert %{
             name: "reviewing",
             deadlines: [],
             max_entries: nil,
             rules: [
               %{
                 id: "rule-1",
                 kind: "review",
                 matches: %{"request" => "req"},
                 literals: %{},
                 wait: "due-2",
                 meta: %{doc: "Submit the report within the response window."}
               }
             ],
             wait: %{id: "due-2", heads: ["rule-0"], after_ms: 300_000, outcome: "stalled"}
           } = c.states["reviewing"]
  end

  test "enum literals compile to member strings; open fields count toward coverage" do
    body = """
    type request
    type verdict
      request: ref(request)
      decision: enum(approve|revise|reject)
      urgent: boolean
    state new
      lead -> reviewer: request() as req awaits reviewing
        => verdict(request=$req, decision=approve) as v & approved
         | verdict(request=$req, decision=revise, urgent=true) as v & revising
         | verdict(request=$req, decision=revise, urgent=false) as v & revising
         | verdict(request=$req, decision=reject) as v & rejected
         | @5m & stalled
    state revising
      lead -> reviewer: request() as next => done
    end approved
    end rejected
    end done
    end stalled
    """

    assert {:ok, c} = compile(body)

    assert [
             %{literals: %{"decision" => "approve"}, bind: "v"},
             %{literals: %{"decision" => "revise", "urgent" => true}},
             %{literals: %{"decision" => "revise", "urgent" => false}},
             %{literals: %{"decision" => "reject"}}
           ] = c.states["reviewing"].rules

    assert c.states["reviewing"].wait.kinds == ["verdict"]
  end

  test "a contract without reply lines gains no extension and no reply keys" do
    # One way: the reviewer may post notes, and only the lead moves the state.
    body = """
    type request
    type note
    type close
    state new
      lead -> reviewer: request() => reviewing
    state reviewing
      event reviewer -> lead: note()
      lead -> reviewer: close() => done
    end done
    """

    assert {:ok, c} = compile(body)
    assert c.card.extensions == []
    refute Enum.any?(c.states, fn {_, s} -> Map.has_key?(s, :wait) end)
    refute Canonical.encode(c) =~ "literals"
  end

  test "the extension stays sorted among declared ones" do
    body = """
    type request
    type review
      request: ref(request)
    state new
      lead -> reviewer: request() as req awaits reviewing
        => review(request=$req) & done
         | @5m & stalled
    end done
    end stalled
    """

    bytes =
      "---\nname: replies\ndescription: Reply alternatives.\nengine: 0.2.0-lab.1\nroles: [lead, reviewer]\nextensions: [host.presence/1]\n---\ncontract @1h => expired\n" <>
        body

    assert {:ok, c} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => bytes,
               "sha256" => Canonical.hash(bytes)
             })

    assert c.card.extensions == ["contract.replies/1", "host.presence/1"]
  end

  test "authors cannot declare contract.replies/1 in the header" do
    assert {:error, %{code: :field_value, detail: "extensions"}} =
             compile(
               "type request\nstate new\n  lead -> reviewer: request() => done\nend done\n",
               "lead, reviewer",
               "contract.replies/1"
             )
  end

  test "a compiled reply contract is refused by a host without the capability" do
    assert {:ok, c} = compile(@types <> @deploy)
    refute ContractLab.RoomPolicy.supported?(c, [])
    assert ContractLab.RoomPolicy.supported?(c, ["contract.replies/1"])
  end

  describe "reply_not_exhaustive" do
    test "a gap in a kind's literals is refused at the head" do
      body =
        String.replace(@deploy, "     | error(request=$job, retryable=false) & failed\n", "")

      assert error(@types <> body) == {:reply_not_exhaustive, line(10)}
    end

    test "16 rows over 4 booleans cover; 15 do not" do
      rows =
        for a <- [true, false], b <- [true, false], c <- [true, false], d <- [true, false] do
          "     | flags(request=$req, a=#{a}, b=#{b}, c=#{c}, d=#{d}) & done\n"
        end

      rows = List.update_at(rows, 0, &String.replace(&1, "     |", "    =>"))

      head = """
      type request
      type flags
        request: ref(request)
        a: boolean
        b: boolean
        c: boolean
        d: boolean
      state new
        lead -> reviewer: request() as req awaits waiting
      """

      tail = "     | @5m & stalled\nend done\nend stalled\n"
      assert {:ok, _} = compile(head <> Enum.join(rows) <> tail)
      [_, second | rest] = rows
      fifteen = [String.replace(second, "     |", "    =>") | rest]
      assert error(head <> Enum.join(fifteen) <> tail) == {:reply_not_exhaustive, line(9)}
    end

    test "staggered rows over 4 booleans cover without naming every field" do
      body = """
      type request
      type flags
        request: ref(request)
        a: boolean
        b: boolean
        c: boolean
        d: boolean
      state new
        lead -> reviewer: request() as req awaits waiting
          => flags(request=$req, a=true) & done
           | flags(request=$req, a=false, b=true) & done
           | flags(request=$req, a=false, b=false, c=true) & done
           | flags(request=$req, a=false, b=false, c=false, d=true) & done
           | flags(request=$req, a=false, b=false, c=false, d=false) & done
           | @5m & stalled
      end done
      end stalled
      """

      assert {:ok, _} = compile(body)
    end
  end

  describe "reply_overlap" do
    test "the same alternative twice is refused at the later line" do
      body =
        String.replace(
          @deploy,
          "    => ok(request=$job) & deployed\n",
          "    => ok(request=$job) & deployed\n     | ok(request=$job) & failed\n"
        )

      assert error(@types <> body) == {:reply_overlap, line(12)}
    end

    test "a bare row overlaps a row with literals of its kind" do
      body =
        String.replace(
          @deploy,
          "     | error(request=$job, retryable=true) & retrying\n",
          "     | error(request=$job) & retrying\n"
        )

      assert error(@types <> body) == {:reply_overlap, line(13)}
    end
  end

  describe "reply_match" do
    test "a value outside a boolean's domain" do
      body = String.replace(@deploy, "retryable=false", "retryable=maybe")
      assert error(@types <> body) == {:reply_match, line(13)}
    end

    test "a repeated field" do
      body = String.replace(@deploy, "retryable=false", "retryable=false, retryable=true")
      assert error(@types <> body) == {:reply_match, line(13)}
    end

    test "a literal on a ref, text or integer field, or on a field the type lacks" do
      types =
        String.replace(
          @types,
          "  retryable: boolean\n",
          "  retryable: boolean\n  note: text(1..10)\n"
        )

      for literal <- ["request=job", "note=x", "missing=true"] do
        body =
          String.replace(@deploy, "=> ok(request=$job)", "=> ok(request=$job, #{literal})")

        assert error(types <> body) == {:reply_match, line(12)}, literal
      end
    end

    test "alternatives of one kind with different references" do
      types =
        String.replace(
          @types,
          "  retryable: boolean\n",
          "  retryable: boolean\n  origin: ref(deploy)\n"
        )

      body =
        @deploy
        |> String.replace("retryable=true", "retryable=true, origin=$job")
        |> String.replace("retryable=false", "retryable=false, origin=$other")
        |> String.replace("abandon() =>", "abandon() as other =>")

      assert error(types <> body) == {:reply_match, line(14)}
    end

    test "mixed as names, or the head's name" do
      body = String.replace(@deploy, "& deployed", "as r & deployed")
      assert error(@types <> body) == {:reply_match, line(12)}

      body =
        @deploy
        |> String.replace("& deployed", "as job & deployed")
        |> String.replace("& retrying\n", "as job & retrying\n")
        |> String.replace("& failed\n", "as job & failed\n")

      assert error(@types <> body) == {:reply_match, line(11)}
    end

    test "an unknown literal is refused without creating an atom" do
      value = "zz_kenji_reply_literal_#{System.unique_integer([:positive])}"
      body = String.replace(@deploy, "retryable=false", "retryable=" <> value)
      assert error(@types <> body) == {:reply_match, line(13)}
      assert_raise ArgumentError, fn -> String.to_existing_atom(value) end
    end
  end

  describe "implicit correlation sugar (SYNTAX-OPTIONS.md)" do
    test "a head without as binds W's name; a row without =$ gets the single ref field" do
      assert {:ok, c} = compile(@types <> @sugared)
      assert [%{id: "rule-0", target: "deploying", bind: "deploying"}] = c.states["new"].rules

      assert %{id: "rule-6", target: "deploying", bind: "deploying"} =
               rule(c, "retrying", "rule-6")

      for id <- ~w(rule-1 rule-2 rule-3) do
        assert %{matches: %{"request" => "deploying"}} = rule(c, "deploying", id)
      end

      assert c.states["deploying"].wait.heads == ["rule-0", "rule-6"]
    end

    test "the sugar compiles exactly as the explicit form" do
      explicit = String.replace(@deploy, "job", "deploying")
      assert {:ok, sugared} = compile(@types <> @sugared)
      assert {:ok, written} = compile(@types <> explicit)
      assert sugared.digest == written.digest
      assert Canonical.encode(sugared.states) == Canonical.encode(written.states)
    end

    test "a written as is used for the rows that omit =$; written and omitted rows mix" do
      body =
        @deploy
        |> String.replace("ok(request=$job)", "ok()")
        |> String.replace("error(request=$job, retryable=false)", "error(retryable=false)")

      assert {:ok, c} = compile(@types <> body)
      assert {:ok, written} = compile(@types <> @deploy)
      assert c.digest == written.digest
    end

    test "with no ref field to the head's kind, or several, a row without =$ is reply_correlation" do
      # Removing ok's field moves every later line up by one.
      types = String.replace(@types, "type ok\n  request: ref(deploy)\n", "type ok\n")
      assert error(types <> @sugared) == {:reply_correlation, line(10)}

      types =
        String.replace(
          @types,
          "type ok\n  request: ref(deploy)\n",
          "type ok\n  request: ref(abandon)\n"
        )

      assert error(types <> @sugared) == {:reply_correlation, line(11)}

      types =
        String.replace(
          @types,
          "  retryable: boolean\n",
          "  retryable: boolean\n  origin: ref(deploy)\n"
        )

      assert error(types <> @sugared) == {:reply_correlation, line(13)}

      written =
        String.replace(
          @sugared,
          "error(retryable=",
          "error(request=$deploying, origin=$deploying, retryable="
        )

      assert {:ok, c} = compile(types <> written)
      assert %{matches: %{"request" => "deploying"}} = rule(c, "deploying", "rule-2")
    end

    test "without awaits, W is <kind>_wait and takes the head's prompt (Lead decision 6)" do
      body = """
      type request
      type review
        request: ref(request)
      state new
        "Ask for a review."
        lead -> reviewer: request()
          => review() & collected
           | @5m & stalled
      end collected
      end stalled
      """

      assert {:ok, c} = compile(body)
      assert [%{target: "request_wait", bind: "request_wait"}] = c.states["new"].rules

      assert %{
               meta: %{doc: "Ask for a review."},
               rules: [%{id: "rule-1", matches: %{"request" => "request_wait"}}],
               wait: %{id: "due-2", heads: ["rule-0"]}
             } = c.states["request_wait"]
    end

    test "a declared state W keeps its own prompt" do
      body =
        String.replace(
          @deploy,
          "state deploying max_entries",
          "\"Deploying.\"\nstate deploying max_entries"
        )

      assert {:ok, c} = compile(@types <> body)
      assert c.states["deploying"].meta.doc == "Deploying."
    end
  end

  describe "duplicate (an implied W, or a W equal to a terminal), at the later line" do
    @one_review """
    type request
    type review
      request: ref(request)
    state new
      lead -> reviewer: request()
        => review() & collected
         | @5m & stalled
    end collected
    end stalled
    """

    test "a state W section requires awaits" do
      assert error(@one_review <> "state request_wait max_entries 2 => stalled\n") ==
               {:duplicate, line(10)}

      assert error("state request_wait\n" <> @one_review) == {:duplicate, line(6)}
    end

    test "an implied W that is a terminal, or a named W that is a terminal" do
      assert error(@one_review <> "end request_wait\n") == {:duplicate, line(10)}

      body = String.replace(@deploy, "awaits deploying", "awaits deployed")
      assert error(@types <> body) == {:duplicate, line(21)}
    end

    # Lead decision 7.4 (07:55Z): a second block for a W is the existing reply_route check.
    # Sofia SECURITY-U.md U4: W is never the initial state or the state that holds its head.
    test "awaits new, or awaiting the head's own state, is duplicate at the later line" do
      body = String.replace(@deploy, "awaits deploying", "awaits new")
      assert error(@types <> body) == {:duplicate, line(10)}

      own =
        String.replace(
          @deploy,
          "  lead -> deployer: abandon() => failed\n",
          "  lead -> deployer: abandon() awaits retrying\n    => ok(request=$job) & failed\n     | @1m & stalled\n"
        )

      assert error(@types <> own) == {:duplicate, line(20)}

      initial =
        String.replace(
          @deploy,
          "  lead -> deployer: abandon() => failed\n",
          "  lead -> deployer: abandon() awaits new\n    => ok(request=$job) & failed\n     | @1m & stalled\n"
        )

      assert error(@types <> initial) == {:duplicate, line(20)}
    end

    test "two blocks implying the same W, or one naming another's implied W, are reply_route" do
      second =
        "state again\n  lead -> reviewer: request()\n    => review() & collected\n     | @5m & stalled\n"

      assert error(@one_review <> second) == {:reply_route, line(11)}

      named = String.replace(second, "request()", "request() awaits request_wait")
      assert error(@one_review <> named) == {:reply_route, line(11)}
    end
  end

  describe "reply_timeout" do
    test "a missing timeout is refused at the head" do
      body = String.replace(@deploy, "     | @10m & stalled\n", "")
      assert error(@types <> body) == {:reply_timeout, line(10)}
    end

    test "a second timeout is refused at its line" do
      body =
        String.replace(
          @deploy,
          "     | @10m & stalled\n",
          "     | @10m & stalled\n     | @5m & stalled\n"
        )

      assert error(@types <> body) == {:reply_timeout, line(15)}
    end

    test "a timeout before any reply alternative is refused at that timeout" do
      body =
        String.replace(
          @deploy,
          "    => ok(request=$job) & deployed\n",
          "    => @1m & stalled\n     | ok(request=$job) & deployed\n"
        )

      assert error(@types <> body) == {:reply_timeout, line(11)}
    end

    test "an early timeout, with an alternative after it, is refused at the timeout (decision 3)" do
      body =
        @deploy
        |> String.replace("     | @10m & stalled\n", "")
        |> String.replace(
          "    => ok(request=$job) & deployed\n",
          "    => ok(request=$job) & deployed\n     | @10m & stalled\n"
        )

      assert error(@types <> body) == {:reply_timeout, line(12)}
    end

    test "the first alternative starts with =>, every later one with |" do
      body = String.replace(@deploy, "    => ok(", "     | ok(")
      assert error(@types <> body) == {:reply_timeout, line(11)}

      body =
        String.replace(
          @deploy,
          "     | error(request=$job, retryable=true)",
          "    => error(request=$job, retryable=true)"
        )

      assert error(@types <> body) == {:reply_timeout, line(12)}
    end

    test "a non-terminal timeout outcome fails the deadline check" do
      body = String.replace(@deploy, "| @10m & stalled", "| @10m & retrying")
      assert error(@types <> body) == {:unknown_state, line(14)}
    end
  end

  describe "Theo's U EBNF (wait-head, alternative, reply-alt, reply-arg, timeout-alt)" do
    test "a literal value is an identifier; anything else is syntax at its line" do
      for value <- ["1", "True", "a-b", "x.y"] do
        body = String.replace(@deploy, "retryable=false", "retryable=" <> value)
        assert error(@types <> body) == {:syntax, line(13)}, value
      end
    end

    test "an alternative is call [as name] & state; anything else is syntax at its line" do
      for row <- [
            "    => ok(request=$job) => deployed\n",
            "    => ok(request=$job)\n",
            "    => ok(request=$job) &\n",
            "    => ok(request=$job) & deployed | error() & failed\n",
            "    => reply ok(request=$job) & deployed\n",
            "    => ok(request=$job) & deployed as r\n"
          ] do
        body = String.replace(@deploy, "    => ok(request=$job) & deployed\n", row)
        assert error(@types <> body) == {:syntax, line(11)}, row
      end

      body = String.replace(@deploy, "| @10m & stalled", "| @10m => stalled")
      assert error(@types <> body) == {:syntax, line(14)}
    end

    test "spaces after a leader and around & are tokenized" do
      body =
        @deploy
        |> String.replace("    => ok(request=$job) & deployed", "    =>ok(request=$job)&deployed")
        |> String.replace("     | @10m & stalled", "     |  @10m   &   stalled")

      assert {:ok, compiled} = compile(@types <> body)
      assert {:ok, plain} = compile(@types <> @deploy)
      assert compiled.digest == plain.digest
    end

    test "syntax B lines are gone: a reply or timeout line is syntax" do
      body =
        @deploy
        |> String.replace("deploy() as job awaits deploying", "deploy() as job => deploying",
          global: false
        )
        |> String.replace(
          "    => ok(request=$job) & deployed\n",
          "    reply ok(request=$job) => deployed\n"
        )

      assert error(@types <> body) == {:syntax, line(11)}

      body = String.replace(@deploy, "     | @10m & stalled\n", "    timeout @10m => stalled\n")
      assert error(@types <> body) == {:syntax, line(14)}
    end

    # Lead decision 7: a re-send is `=> W`, never `awaits`.
    test "awaits W with no block is reply_timeout at the head; awaits a renewal or an event is reply_route" do
      for {to, code, n} <- [
            {"  lead -> deployer: abandon() awaits paused\n", :reply_timeout, 20},
            {"  lead -> deployer: abandon() awaits @self\n", :reply_route, 20},
            {"  lead -> deployer: abandon() as job awaits @contract\n", :reply_route, 20},
            {"  event lead -> deployer: abandon() awaits paused\n", :reply_route, 20}
          ] do
        body = String.replace(@deploy, "  lead -> deployer: abandon() => failed\n", to)
        assert error(@types <> body) == {code, line(n)}, to
      end

      body =
        String.replace(
          @deploy,
          "deploy() as job => deploying",
          "deploy() as job awaits deploying"
        )

      assert error(@types <> body) == {:reply_timeout, line(18)}
    end

    # Lead decision (BandKeywordsTest): awaits is a reserved rule word, with the lowercase hint.
    test "awaits is reserved: AWAITS is syntax with the lowercase hint" do
      body = String.replace(@deploy, "as job awaits deploying", "as job AWAITS deploying")

      assert {:error,
              %{code: :syntax, line: 11, detail: "Rule options are lowercase: write awaits"}} =
               compile(@types <> body)
    end

    # N4: the two meanings of `|` are told apart by position.
    test "a timed rule keeps its mid-line |; a line-start | after it is syntax" do
      timed = String.replace(@deploy, "abandon() => failed", "abandon() @5m => failed | stalled")
      assert {:ok, c} = compile(@types <> timed)
      assert [%{kind: "abandon", outcome: "stalled"}] = c.states["retrying"].deadlines

      body = String.replace(timed, "| stalled\n", "| stalled\n     | @1m & stalled\n")
      assert error(@types <> body) == {:syntax, line(21)}
    end
  end

  describe "reply_route" do
    test "an alternative with no send before it is a syntax error" do
      assert {:syntax, _} = error("type request\n  => ok() & done\n" <> @types)
      assert {:syntax, _} = error("type request\n   | ok() & done\n" <> @types)
    end

    test "alternatives after a timed rule or timed event are syntax, at the first alternative" do
      for head <- [
            "  lead -> deployer: deploy() as job @5m => deploying | stalled\n",
            "  event lead -> deployer: deploy() as job @5m => stalled\n"
          ] do
        body =
          String.replace(@deploy, "  lead -> deployer: deploy() as job awaits deploying\n", head)

        assert error(@types <> body) == {:syntax, line(11)}, head
      end
    end

    # RULE.md §A A1b: a head may sit in anystate (reply_counter_request_test.exs).
    test "a head that is an event, names a target or a renewal; an anystate head compiles" do
      for to <- [
            "  event lead -> deployer: deploy() as job awaits deploying\n",
            "  event lead -> deployer: deploy() as job\n",
            "  lead -> deployer: deploy() as job => deploying\n",
            "  lead -> deployer: deploy() as job awaits @self\n"
          ] do
        body =
          String.replace(@deploy, "  lead -> deployer: deploy() as job awaits deploying\n", to)

        assert error(@types <> body) == {:reply_route, line(10)}, to
      end

      body =
        "type request\ntype go\ntype review\n  request: ref(request)\nstate new\n  lead -> reviewer: go() => done\nanystate\n" <>
          "  lead -> reviewer: request() awaits waiting\n    => review() & done\n     | @5m & stalled\nend done\nend stalled\n"

      assert {:ok, _} = compile(body)
    end

    test "a send with a renewal target followed by alternatives is reply_route; alone it stays syntax" do
      for target <- ~w(@self @state @contract) do
        head = "  lead -> deployer: deploy() as job => #{target}\n"

        body =
          String.replace(@deploy, "  lead -> deployer: deploy() as job awaits deploying\n", head)

        assert error(@types <> body) == {:reply_route, line(10)}, target

        alone =
          String.replace(
            @deploy,
            "  lead -> deployer: abandon() => failed\n",
            "  lead -> deployer: abandon() => #{target}\n"
          )

        assert error(@types <> alone) == {:syntax, line(20)}, target
      end

      prompted =
        String.replace(
          @deploy,
          "  lead -> deployer: deploy() as job awaits deploying\n",
          "  \"The deploy.\"\n  lead -> deployer: deploy() as job => @self\n  %% why\n"
        )

      assert error(@types <> prompted) == {:reply_route, line(10)}
    end

    test "a head with a roster or several recipients" do
      body = """
      type reviewer: agent
      reviewers: list(reviewer)
      type request
      type review
      state new
        lead -> reviewers: request() awaits waiting
          => review() & done
           | @5m & stalled
      end done
      end stalled
      """

      assert error(body, "lead, reviewers") == {:reply_route, line(6)}

      body = """
      type request
      type review
      state new
        lead -> {deployer,reviewer}: request() awaits waiting
          => review() & done
           | @5m & stalled
      end done
      end stalled
      """

      assert error(body) == {:reply_route, line(4)}
    end

    # RULE.md §A.2 A4: an entry of another kind on the head's route is a re-send, so it must
    # bind the head's name (this one binds W's name: reply_correlation); a W-entry from anystate
    # or on another route stays reply_route.
    test "an entry into W other than its heads" do
      body = String.replace(@deploy, "abandon() => failed", "abandon() => deploying")
      assert error(@types <> body) == {:reply_correlation, line(20)}

      body = @deploy <> "anystate\n  lead -> deployer: abandon() => deploying\n"
      assert error(@types <> body) == {:reply_route, line(26)}
    end

    # The re-send in retrying is documented; errors about a rule point at its prompt (line 18).
    test "a re-send that differs from the declaring head or has alternatives" do
      body =
        String.replace(
          @deploy,
          "deploy() as job => deploying\n  lead",
          "deploy() as job => deploying\n  reviewer -> deployer: deploy() as job => deploying\n  lead"
        )

      assert error(@types <> body) == {:reply_route, line(20)}

      body =
        String.replace(
          @deploy,
          "  lead -> deployer: deploy() as job => deploying\n  lead -> deployer: abandon()",
          "  lead -> deployer: deploy() as job awaits deploying\n    => ok(request=$job) & deployed\n     | @1m & stalled\n  lead -> deployer: abandon()"
        )

      assert error(@types <> body) == {:reply_route, line(18)}
    end

    # RULE.md §A A1: the replier may leave W into an end terminal or by a counter-request; any
    # other move stays reply_route.
    test "a forbidden line in state W" do
      for {line, n} <- [
            {"  deployer -> lead: progress() => retrying\n", 16},
            {"  lead -> deployer: abandon() @1m => failed | stalled\n", 16}
          ] do
        body = String.replace(@deploy, "  event deployer -> lead: progress() => @self\n", line)
        assert error(@types <> body) == {:reply_route, line(n)}, line
      end

      body =
        String.replace(
          @deploy,
          "  event deployer -> lead: progress() => @self\n",
          "  deployer -> lead: progress() => failed\n"
        )

      assert {:ok, _} = compile(@types <> body)
    end

    # Sofia K-1 (SECURITY-CODE.md): only the replier sends W's reply kinds while W waits.
    test "in W or anystate, a reply kind of W from another role (K-1)" do
      for line <- [
            "  reviewer -> lead: ok(request=$job) => deployed\n",
            "  event reviewer -> lead: ok(request=$job)\n",
            "  event lead -> deployer: error(request=$job)\n"
          ] do
        body = String.replace(@deploy, "  event deployer -> lead: progress() => @self\n", line)
        assert error(@types <> body) == {:reply_route, line(16)}, line
      end

      body = @deploy <> "anystate\n  reviewer -> lead: error(request=$job) => failed\n"
      assert error(@types <> body) == {:reply_route, line(26)}

      elsewhere =
        String.replace(
          @deploy,
          "  lead -> deployer: abandon() => failed\n",
          "  reviewer -> lead: ok(request=$job) => failed\n"
        )

      assert {:ok, _} = compile(@types <> elsewhere)
    end
  end

  describe "reply_correlation" do
    test "a re-send with another as name, or none under a head that has one, at the re-send" do
      body =
        String.replace(@deploy, "deploy() as job => deploying", "deploy() as again => deploying")

      assert error(@types <> body) == {:reply_correlation, line(18)}

      body = String.replace(@deploy, "deploy() as job => deploying", "deploy() => deploying")
      assert error(@types <> body) == {:reply_correlation, line(18)}
    end

    test "an alternative whose =$ names another binding" do
      body =
        @deploy
        |> String.replace("=> ok(request=$job)", "=> ok(request=$other)")
        |> String.replace("abandon() =>", "abandon() as other =>")

      assert error(@types <> body) == {:reply_correlation, line(11)}
    end

    # RULE.md §A A1: a rule that binds W's name moves the state and so ends the wait; an event
    # may not bind it.
    test "an event in W or anystate that binds the head's name, written or implied; a rule may" do
      body =
        String.replace(
          @deploy,
          "  event deployer -> lead: progress() => @self\n",
          "  lead -> deployer: abandon() as job => failed\n"
        )

      assert {:ok, _} = compile(@types <> body)

      body =
        String.replace(
          @deploy,
          "  event deployer -> lead: progress() => @self\n",
          "  event lead -> deployer: progress() as job\n"
        )

      assert error(@types <> body) == {:reply_correlation, line(16)}

      body = @deploy <> "anystate\n  event lead -> deployer: progress() as job\n"
      assert error(@types <> body) == {:reply_correlation, line(26)}

      body =
        String.replace(
          @sugared,
          "  event deployer -> lead: progress() => @self\n",
          "  event lead -> deployer: progress() as deploying\n"
        )

      assert error(@types <> body) == {:reply_correlation, line(16)}
    end

    test "a (replier, kind) used as an alternative is reply-only (SR3)" do
      body =
        String.replace(
          @deploy,
          "  event deployer -> lead: progress() => @self\n",
          "  event deployer -> lead: ok(request=$job)\n"
        )

      assert error(@types <> body) == {:reply_correlation, line(16)}

      body =
        String.replace(
          @deploy,
          "  lead -> deployer: abandon() => failed\n",
          "  lead -> deployer: abandon() => failed\n  deployer -> lead: error(request=$job) => failed\n"
        )

      assert error(@types <> body) == {:reply_correlation, line(21)}
    end
  end

  # Victor's witness (RECHECK-CHECKS.json): with no cycle, W1 -> cancel -> W2 on the same reply
  # kind let a late W1 answer land in W2 unless every block is correlated (Lead decision 2).
  test "acyclic W1 -> cancel -> W2 is correlated, written or implied; a crossed =$ is refused" do
    correlated = """
    type request
    type cancel
    type answer
      request: ref(request)
    state new
      lead -> worker: request() as req awaits w1
        => answer(request=$req) & first_done
         | @5m & stalled
    state w1
      lead -> worker: cancel() => between
    state between
      lead -> worker: request() as again awaits w2
        => answer(request=$again) & second_done
         | @5m & stalled
    end first_done
    end second_done
    end stalled
    """

    assert {:ok, c} = compile(correlated, "lead, worker")
    assert c.states["w2"].wait.heads == ["rule-4"]

    implied =
      correlated
      |> String.replace(" as req", "")
      |> String.replace(" as again", "")
      |> String.replace("(request=$req)", "()")
      |> String.replace("(request=$again)", "()")

    assert {:ok, c} = compile(implied, "lead, worker")
    assert %{bind: "w2"} = Enum.find(c.states["between"].rules, &(&1.id == "rule-4"))
    assert [%{matches: %{"request" => "w2"}}] = c.states["w2"].rules

    crossed = String.replace(correlated, "answer(request=$again)", "answer(request=$req)")
    assert error(crossed, "lead, worker") == {:reply_correlation, line(13)}
  end

  describe "reply_cycle" do
    test "W on a cycle without max_entries, at the head" do
      body = String.replace(@deploy, " max_entries 3 => exhausted", "")
      assert error(@types <> body) == {:reply_cycle, line(10)}
    end

    test "a W reachable again only through a host trigger is on a cycle (SR3 edges)" do
      body =
        @deploy
        |> String.replace(" max_entries 3 => exhausted", "")
        |> String.replace("retryable=true) & retrying", "retryable=true) & paused")
        |> String.replace("  lead -> deployer: abandon() => failed\n", "")
        |> String.replace(
          "  event deployer -> lead: progress() => @self\n",
          "  event deployer -> lead: progress() => @self\n  on deployer offline => retrying\n"
        )
        |> Kernel.<>("state paused\n")

      assert {:error, %{code: :reply_cycle, line: 11}} =
               compile(@types <> body, "lead, deployer", "host.presence/1")

      acyclic = String.replace(body, "  on deployer offline => retrying\n", "")
      assert {:ok, _} = compile(@types <> acyclic, "lead, deployer", "host.presence/1")
    end

    test "a W reachable again only through an anystate rule is on a cycle" do
      body =
        @deploy
        |> String.replace(" max_entries 3 => exhausted", "")
        |> String.replace("retryable=true) & retrying", "retryable=true) & paused")
        |> String.replace("  lead -> deployer: abandon() => failed\n", "")
        |> Kernel.<>("state paused\nanystate\n  lead -> deployer: abandon() => retrying\n")

      assert error(@types <> body) == {:reply_cycle, line(10)}
    end
  end

  # n alternatives of pick, the first opening the block.
  defp rows(n) do
    1..n
    |> Enum.map_join("", &"     | pick(choice=m#{&1}) & done\n")
    |> String.replace_prefix("     |", "    =>")
  end

  describe "reply_limit" do
    test "more than 16 alternatives in a block, at the 17th" do
      members = Enum.map_join(1..17, "|", &"m#{&1}")

      body =
        "type request\ntype pick\n  choice: enum(#{members})\nstate new\n  lead -> reviewer: request() awaits waiting\n" <>
          rows(17) <> "     | @5m & stalled\nend done\nend stalled\n"

      assert error(body) == {:reply_limit, line(22)}
    end

    test "more than 4 literals in one alternative" do
      fields = Enum.map_join(1..5, "", &"  f#{&1}: boolean\n")
      literals = Enum.map_join(1..5, ", ", &"f#{&1}=true")

      body =
        "type request\ntype flags\n#{fields}state new\n  lead -> reviewer: request() awaits waiting\n" <>
          "    => flags(#{literals}) & done\n     | @5m & stalled\nend done\nend stalled\n"

      assert error(body) == {:reply_limit, line(10)}
    end

    test "more than 256 alternatives in the contract" do
      members = Enum.map_join(1..16, "|", &"m#{&1}")

      blocks =
        for b <- 1..17 do
          "state s#{b}\n  lead -> reviewer: request() awaits w#{b}\n" <>
            rows(16) <> "     | @5m & stalled\n"
        end

      body =
        "type request\ntype pick\n  choice: enum(#{members})\nstate new\n  lead -> reviewer: request() => s1\n" <>
          Enum.join(blocks) <> "end done\nend stalled\n"

      # Block 17's first row is alternative 257.
      first_row_17 = 5 + 16 * 19 + 3
      assert error(body) == {:reply_limit, line(first_row_17)}
    end
  end

  test "prompts document the alternative after them; the timeout's prompt documents the wait" do
    body = """
    type request
    type review
      request: ref(request)
    state new
      lead -> reviewer: request() as req awaits reviewing
        "The review."
        => review(request=$req) & done
        "Give up after five minutes."
         | @5m & stalled
    end done
    end stalled
    """

    assert {:ok, c} = compile(body)
    assert [%{meta: %{doc: "The review.", line: 7}}] = c.states["reviewing"].rules
    assert %{meta: %{doc: "Give up after five minutes.", line: 9}} = c.states["reviewing"].wait
  end
end
