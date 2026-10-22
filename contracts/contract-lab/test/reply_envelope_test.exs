defmodule ContractLab.ReplyEnvelopeTest do
  # L7: the completion reserve counts messages, not bytes. Creation refuses a session whose
  # max_bytes cannot carry the smallest envelope of a reply that a duty can require.
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine}

  defp compile(body) do
    bytes =
      ContractLab.TestClock.ensure(
        """
        ---
        name: reply-envelope
        description: Ask for one report.
        engine: 0.2.0-lab.1
        roles: [lead, worker]
        ---
        type task
        type report
          task: ref(task)
          text: text(bytes: 3..100, graphemes: 5..10)
          count: integer(12..99)
          done: boolean
          level: enum(~w[urgent ok])
        """ <> body
      )

    {:ok, c} =
      ContractLab.compile_source(%{
        "profile" => "band-contract/file-source/1",
        "format" => "frontmatter",
        "content" => bytes,
        "sha256" => Canonical.hash(bytes)
      })

    c
  end

  # The task awaits one report within 5m: the wait's alternative is the promised reply.
  @timed """
  state new
    lead -> worker: task() awaits working
      => report() & done
       | @5m & stalled
  end done
  end stalled
  """

  defp new(c, max_bytes, session \\ "s") do
    Engine.new(c,
      session_id: session,
      now: 0,
      max_messages: 10,
      max_bytes: max_bytes,
      bindings: %{"lead" => "l", "worker" => "w"},
      room_policy: :unchecked,
      capabilities: c.card.extensions
    )
  end

  # The smallest valid report: 1-byte ID, no expected_version, each field at its minimum.
  # text needs 5 graphemes even though 3 bytes would do; task names the 1-byte task ID t.
  defp smallest(session \\ "s") do
    %{
      "id" => "r",
      "session" => session,
      "kind" => "report",
      "payload" => %{
        "task" => "t",
        "text" => "aaaaa",
        "count" => 12,
        "done" => true,
        "level" => "ok"
      }
    }
  end

  test "a cap one byte below the smallest promised reply is refused at creation" do
    c = compile(@timed)
    size = byte_size(Canonical.encode(smallest()))

    assert {:error, :completion_size} = new(c, size - 1)
    assert {:ok, _} = new(c, size)
  end

  test "the bound is exact: at that cap the smallest reply is accepted" do
    c = compile(@timed)
    {:ok, s} = new(c, byte_size(Canonical.encode(smallest())))

    {%{status: :accepted}, s} =
      Engine.step(s, %{"id" => "t", "session" => "s", "kind" => "task", "payload" => %{}},
        actor: "l",
        now: 1
      )

    assert {%{status: :accepted}, _} = Engine.step(s, smallest(), actor: "w", now: 2)
  end

  test "the session ID counts toward the envelope" do
    c = compile(@timed)
    size = byte_size(Canonical.encode(smallest()))
    long = String.duplicate("s", 200)

    assert {:ok, _} = new(c, size)
    assert {:error, :completion_size} = new(c, size, long)
    assert {:ok, _} = new(c, byte_size(Canonical.encode(smallest(long))), long)
  end

  test "a timed event and a collection reply are promised replies too" do
    for body <- [
          """
          state new
            lead -> worker: task() as t => working
          state working
            event worker -> lead: report(task=$t) @5m => stalled
            lead -> worker: task() => done
          end done
          end stalled
          """,
          """
          state new
            lead -> worker: task() as t => working
          state working
            collect reports from worker -> lead @5m => done
              reply report(task=$t)
          end done
          """
        ] do
      c = compile(body)
      size = byte_size(Canonical.encode(smallest()))
      assert {{:error, :completion_size}, _} = {new(c, size - 1), body}
      assert {{:ok, _}, _} = {new(c, size), body}
    end
  end

  test "a text minimum near 2^53 bytes is counted without being built" do
    c = compile(@timed)
    report = c.types["report"]

    huge =
      put_in(report.fields["text"].bytes, %{
        min: 9_007_199_254_740_991,
        max: 9_007_199_254_740_991
      })

    c = put_in(c.types["report"], huge)

    assert {:error, :completion_size} = new(c, 9_007_199_254_740_991)
  end

  test "a kind no duty requires is not checked" do
    # One way: the worker may report while it works, but nothing waits for a report.
    c =
      compile("""
      type close
      state new
        lead -> worker: task() as t => working
      state working
        event worker -> lead: report(task=$t)
        lead -> worker: close() => done
      end done
      """)

    assert {:ok, _} = new(c, 1)
  end
end
