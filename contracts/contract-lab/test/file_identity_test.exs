defmodule ContractLab.FileIdentityTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, StateCodec}

  @body """
  type question
    text: text(1..2000)
  type answer
    question: ref(question)
    text: text(1..2000)
  state new
    requester -> assistant: question() awaits answering
      => answer() & complete
       | @5m & stalled
  end complete
  end stalled
  end expired
  contract @infinity => expired
  """

  defp file(version \\ "0.4.1") do
    """
    ---
    name: file-pinned
    description: One request, one answer.
    engine: 0.2.0-lab.1
    version: #{version}
    roles: [requester, assistant]
    ---
    """ <> @body
  end

  defp source(content, format \\ "frontmatter") do
    %{
      "profile" => "band-contract/file-source/1",
      "format" => format,
      "content" => content,
      "sha256" => Canonical.hash(content)
    }
  end

  defp new(c) do
    Engine.new(c,
      session_id: "s",
      bindings: %{"requester" => "r", "assistant" => "a"},
      now: 0,
      inactivity: :accepted,
      max_messages: 10,
      room_policy: :unchecked,
      capabilities: c.card.extensions
    )
  end

  test "source identity is the SHA-256 of every exact file byte, including the header" do
    bytes = file()
    assert {:ok, a} = ContractLab.compile_source(source(bytes))
    assert a.source_digest == Canonical.hash(bytes)
    assert a.card.version == "0.4.1"

    assert {:ok, b} =
             ContractLab.compile_source(
               source(
                 String.replace(
                   bytes,
                   "roles: [requester, assistant]",
                   "roles: [requester,assistant]"
                 )
               )
             )

    assert a.digest == b.digest
    refute a.source_digest == b.source_digest
  end

  test "the declared file hash is verified rather than trusted" do
    request = source(file())

    assert {:error, :file_digest_mismatch} =
             ContractLab.compile_source(%{request | "content" => file("0.4.2")})

    assert {:error, :input} =
             ContractLab.compile_source(%{request | "sha256" => String.upcase(request["sha256"])})

    assert {:error, :input} = ContractLab.compile_source(Map.put(request, "ignored", true))
  end

  test "a new version cannot replace a running or restored session" do
    assert {:ok, old} = ContractLab.compile_source(source(file()))
    assert {:ok, next} = ContractLab.compile_source(source(file("0.4.2")))
    assert old.digest == next.digest
    refute old.source_digest == next.source_digest
    assert {:ok, s} = new(old)

    question = %{
      "id" => "q",
      "session" => "s",
      "kind" => "question",
      "payload" => %{"text" => "hello"}
    }

    assert {%{status: :accepted}, pending} = Engine.step(s, question, actor: "r", now: 0)

    assert {:error, :contract_mismatch} =
             StateCodec.restore(next, StateCodec.export(pending), 1,
               capabilities: next.card.extensions
             )

    assert {:ok, restored} =
             StateCodec.restore(old, StateCodec.export(pending), 1,
               capabilities: old.card.extensions
             )

    answer = %{
      "id" => "a",
      "session" => "s",
      "kind" => "answer",
      "payload" => %{"question" => "q", "text" => "world"}
    }

    assert {%{status: :accepted, outcome: "complete"}, done} =
             Engine.step(restored, answer, actor: "a", now: 2)

    assert done.contract.source_digest == old.source_digest
    assert {:ok, new_session} = new(next)
    assert new_session.contract.source_digest == next.source_digest
    assert new_session.contract.card.version == "0.4.2"
  end

  test "the file loader uses the same exact-byte identity as remote creation" do
    path =
      Path.join(System.tmp_dir!(), "contract-file-#{System.unique_integer([:positive])}.band")

    on_exit(fn -> File.rm(path) end)
    File.write!(path, file())
    assert {:ok, local} = ContractLab.load(path, :frontmatter)
    assert {:ok, remote} = ContractLab.compile_source(source(file()))
    assert local == remote

    assert {:error, :file_source_required} =
             ContractLab.compile(local.card, "state new\nend expired\n")
  end

  test "flow and block role lists keep semantics but identify their own file bytes" do
    bytes =
      """
      ---
      name: file-pinned
      description: One request, one answer.
      engine: 0.2.0-lab.1
      version: 0.4.1
      roles:
        - requester
        - assistant
      ---
      """ <> @body

    assert {:ok, flow} = ContractLab.compile_source(source(file()))
    assert {:ok, block} = ContractLab.compile_source(source(bytes))
    assert flow.digest == block.digest
    assert block.source_digest == Canonical.hash(bytes)
    refute flow.source_digest == block.source_digest
  end

  test "standard packages pin the entry file plus its verified body digest" do
    body = @body

    bytes = """
    ---
    name: file-pinned
    description: One request, one answer.
    metadata:
      band-engine: "0.2.0-lab.1"
      band-version: "0.4.1"
      band-roles: "requester,assistant"
      band-contract: "protocol.band"
      band-contract-sha256: "#{Canonical.hash(body)}"
    ---
    Human-readable package documentation.
    """

    request =
      source(bytes, "standard") |> Map.put("body", body) |> Map.put("package_name", "file-pinned")

    assert {:ok, c} = ContractLab.compile_source(request)
    assert c.source_digest == Canonical.hash(bytes)

    assert {:error, :digest_mismatch} =
             ContractLab.compile_source(%{request | "body" => body <> "\n"})

    assert {:error, %{code: :field_value, line: 2, detail: "name"}} =
             ContractLab.compile_source(%{request | "package_name" => "other"})

    assert {:ok, changed} =
             ContractLab.compile_source(%{
               request
               | "content" => bytes <> "\n",
                 "sha256" => Canonical.hash(bytes <> "\n")
             })

    assert c.digest == changed.digest
    refute c.source_digest == changed.source_digest
  end

  test "only the file-pinned profile can use the new source envelope" do
    old = """
    +SKILL file-pinned
    +SUMMARY One request, one answer.
    +ENGINE 0.1.0-lab.1
    +ROLES requester, assistant
    +LIFETIME infinite
    +INACTIVITY_TIMEOUT infinite
    +MENTIONS_PER_MESSAGE 1
    +MENTIONS_PER_SESSION 20
    +BODY
    +TYPE question
    +STATE new
      requester -> assistant: question() => complete
    +END complete
    """

    assert {:error, :frontmatter_required} = ContractLab.compile_source(source(old, "native"))
    assert {:error, :invalid_utf8} = ContractLab.compile_source(source(file() <> <<255>>))

    assert {:error, :source_size} =
             ContractLab.compile_source(source(file() <> String.duplicate("x", 262_145)))
  end
end
