defmodule ContractLab.RejectionDetailTest do
  # DF-04: a payload rejection names the failing field and constraint, so an
  # agent can fix its own message. The detail comes from the proposer's own
  # message and the compiled type only, never from another role's data.
  use ExUnit.Case, async: true
  alias ContractLab.{AgentGate, Canonical, Engine}

  @secret "lead-private-7f3a"

  defp compile do
    bytes = """
    ---
    name: rejection-detail
    description: Payload rejections name the field.
    engine: 0.2.0-lab.1
    roles: [lead, a, b]
    ---
    type request
      secret: text(1..200)
    type critique
      request: ref(request)
      text: text(bytes: 1..3600, graphemes: 1..900)
      sha256: text(bytes: 64..64, regex: ~r/[0-9a-f]+/)
      count: integer(0..500)
      tag: enum(critical|suggestion|nit)
      final: boolean
    state new
      lead -> {a,b}: request() as r => reviewing
    state reviewing
      a -> lead: critique(request=$r) => done
    end done
    """

    bytes = ContractLab.TestClock.ensure(bytes)

    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

    c
  end

  defp reviewing(c) do
    {:ok, s} =
      Engine.new(c,
        session_id: "s",
        bindings: %{"lead" => "l", "a" => "a", "b" => "b"},
        now: 0,
        max_messages: 20,
        room_policy: :unchecked
      )

    {%{status: :accepted}, s} = send_message(s, "r", "l", "request", %{"secret" => @secret})
    s
  end

  defp send_message(s, id, actor, kind, payload),
    do:
      Engine.step(s, %{"id" => id, "session" => "s", "kind" => kind, "payload" => payload},
        actor: actor,
        now: 1000
      )

  defp good,
    do: %{
      "request" => "r",
      "text" => "ok",
      "sha256" => String.duplicate("a", 64),
      "count" => 1,
      "tag" => "nit",
      "final" => false
    }

  defp detail(s, payload) do
    assert {%{status: :rejected, reason: :payload} = decision, ^s} =
             send_message(s, "c", "a", "critique", payload)

    refute Canonical.encode(decision) =~ @secret
    decision[:detail]
  end

  test "a 901-grapheme text names the field, the constraint and its bounds" do
    s = reviewing(compile())

    assert detail(s, %{good() | "text" => String.duplicate("x", 901)}) ==
             %{field: "text", constraint: "graphemes", min: 1, max: 900}
  end

  test "each field check has its own constraint code" do
    s = reviewing(compile())

    cases = [
      {%{good() | "sha256" => String.duplicate("A", 64)},
       %{field: "sha256", constraint: "regex"}},
      {%{good() | "sha256" => "abc"}, %{field: "sha256", constraint: "bytes", min: 64, max: 64}},
      {%{good() | "text" => ""}, %{field: "text", constraint: "bytes", min: 1, max: 3600}},
      {%{good() | "count" => 501}, %{field: "count", constraint: "range", min: 0, max: 500}},
      {%{good() | "tag" => "CRITICAL"}, %{field: "tag", constraint: "enum"}},
      {%{good() | "final" => "no"}, %{field: "final", constraint: "type", expected: "boolean"}},
      {%{good() | "count" => "1"}, %{field: "count", constraint: "type", expected: "integer"}},
      {%{good() | "request" => ""}, %{field: "request", constraint: "ref"}},
      {Map.delete(good(), "tag"), %{field: "tag", constraint: "required"}},
      {Map.put(good(), "extra", "x"), %{field: "extra", constraint: "unknown"}}
    ]

    for {payload, expected} <- cases, do: assert(detail(s, payload) == expected)
  end

  test "the first failing field in name order is reported, and the value is never echoed" do
    s = reviewing(compile())
    bad = %{good() | "text" => String.duplicate("x", 901), "count" => 501}
    assert detail(s, bad) == %{field: "count", constraint: "range", min: 0, max: 500}

    {decision, _} = send_message(s, "c", "a", "critique", bad)
    refute Canonical.encode(decision) =~ String.duplicate("x", 901)
  end

  test "other rejections carry no detail" do
    s = reviewing(compile())

    assert {%{status: :rejected, reason: :illegal_message} = d, _} =
             send_message(s, "c", "b", "critique", good())

    refute Map.has_key?(d, :detail)
  end

  test "the agent preflight returns the same detail as JSON" do
    c = compile()
    s = reviewing(c)
    {:ok, view, _} = AgentGate.project(s, "a", 1000)

    assert %{
             "status" => "rejected",
             "reason" => "payload",
             "detail" => %{
               "field" => "text",
               "constraint" => "graphemes",
               "min" => 1,
               "max" => 900
             }
           } =
             AgentGate.check(
               c,
               view,
               %{
                 "id" => "c",
                 "session" => "s",
                 "kind" => "critique",
                 "payload" => %{good() | "text" => String.duplicate("x", 901)},
                 "expected_version" => s.version
               },
               "a",
               1000
             )
  end

  test "a room event's payload rejection carries the same detail" do
    source =
      ContractLab.TestClock.ensure("""
      ---
      name: room-notice-detail
      description: Room events name the failing field.
      engine: 0.2.0-lab.1
      roles: [a, lead]
      ---
      type notice
        text: text(1..10)
      type close
      state new
        lead -> a: close() => complete
      room
        event a -> lead: notice()
      end complete
      """)

    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => source,
        "sha256" => Canonical.hash(source)
      })

    {:ok, r} =
      ContractLab.RoomEvents.new(c,
        bindings: %{"a" => "a", "lead" => "l"},
        now: 0,
        window_ms: 1000,
        max_events: 4,
        max_mentions: 4,
        max_receipts: 6
      )

    assert {%{status: :rejected, reason: :payload, detail: detail}, _} =
             ContractLab.RoomEvents.step(
               r,
               %{"id" => "n1", "kind" => "notice", "payload" => %{"text" => "elevenchars"}},
               actor: "a",
               now: 0
             )

    assert detail == %{field: "text", constraint: "bytes", min: 1, max: 10}
  end
end
