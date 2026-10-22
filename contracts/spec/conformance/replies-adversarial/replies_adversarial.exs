defmodule RepliesAdversarial do
  alias ContractLab.{Canonical, Engine, Scenario, StateCodec}

  def emit(id, pass, evidence) do
    IO.puts(Canonical.encode(%{id: id, pass: pass, evidence: evidence}))
  end

  def exists?(value) do
    String.to_existing_atom(value)
    true
  rescue
    ArgumentError -> false
  end

  def candidate(dir) do
    harness_control()
    suite = dir |> Path.join("suite.json") |> File.read!() |> JSON.decode!()

    for item <- suite["cases"] do
      start = System.monotonic_time(:millisecond)
      result = ContractLab.compile_source(item["source"])
      elapsed = System.monotonic_time(:millisecond) - start

      {pass, evidence} =
        case {result, item["compile"]} do
          {{:error, error}, %{"code" => code}} ->
            line = item["compile"]["line"]

            {to_string(error.code) == code and (is_nil(line) or error.line == line),
             %{compile_error: error, elapsed_ms: elapsed}}

          {{:ok, c}, %{"ok" => true}} ->
            marker = "contract.replies/1" in c.card.extensions

            if item["scenario"] do
              spec = item["scenario"]
              replay = Scenario.run(c, spec)
              invariant = match?({:ok, _}, replay) and rejection_invariants(c, spec)

              {marker and match?({:ok, _}, replay) and invariant,
               %{
                 auto_extension: marker,
                 replay: tagged(replay),
                 rejection_invariants: invariant,
                 elapsed_ms: elapsed
               }}
            else
              {marker, %{auto_extension: marker, elapsed_ms: elapsed}}
            end

          {other, _} ->
            {false, %{compile: tagged(other), elapsed_ms: elapsed}}
        end

      emit(item["id"], pass, evidence)
    end

    # Check exact atom membership, not atom_count (which includes lazy VM/module loading).
    # Every candidate name is absent before parsing and must remain absent afterwards.
    template =
      Enum.find(suite["cases"], &(&1["id"] == "unknown-enum-literal"))["source"]["content"]

    names = Enum.map(1..128, &"felix_unknown_literal_6b7f5f_#{&1}")
    before = Enum.map(names, &exists?/1)

    codes =
      Enum.map(names, fn value ->
        bytes = String.replace(template, "felix_unknown_literal_6b7f5f", value)

        case ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => bytes,
               "sha256" => Canonical.hash(bytes)
             }) do
          {:error, error} -> to_string(error.code)
          _ -> "unexpected_acceptance"
        end
      end)

    after_parse = Enum.map(names, &exists?/1)

    emit(
      "unknown-literals-never-interned",
      Enum.all?(before, &(!&1)) and
        Enum.all?(after_parse, &(!&1)) and Enum.all?(codes, &(&1 == "reply_match")),
      %{
        names: length(names),
        existed_before: Enum.count(before, & &1),
        interned_after: Enum.count(after_parse, & &1),
        codes: Enum.uniq(codes)
      }
    )
  end

  def rejection_invariants(c, spec) do
    {:ok, s} =
      Engine.new(c,
        session_id: spec["session"],
        now: spec["now"],
        max_messages: spec["max_messages"],
        bindings: spec["bindings"],
        capabilities: c.card.extensions,
        room_policy: :unchecked
      )

    {_, valid} =
      Enum.reduce(spec["steps"], {s, true}, fn entry, {before, valid} ->
        case entry do
          %{"tick" => at} ->
            {:ok, after_tick} = Engine.tick(before, at)
            {after_tick, valid}

          _ ->
            {decision, next} =
              Engine.step(before, entry["message"], actor: entry["actor"], now: entry["now"])

            guarded =
              Map.get(decision, :reason) in [:reference, :insufficient_time, :completion_capacity]

            unchanged =
              Map.take(before, [:wait, :variables, :facts, :receipts, :version]) ==
                Map.take(next, [:wait, :variables, :facts, :receipts, :version])

            {next, valid and (not guarded or unchanged)}
        end
      end)

    valid
  end

  def legacy do
    bytes =
      "---\nname: old-reader\ndescription: Old reader probe.\nengine: 0.2.0-lab.1\nroles: [lead, worker]\n---\n" <>
        "contract @30m => expired\ntype request\nstate new\n  lead -> worker: request() => done\nend done\n"

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  def harness_control do
    {:ok, c} = legacy()

    step = %{
      "actor" => "l",
      "now" => 1,
      "message" => %{"id" => "req", "session" => "s", "kind" => "request", "payload" => %{}},
      "expect" => %{"status" => "accepted", "outcome" => "done", "version" => 1}
    }

    spec = %{
      "bindings" => %{"lead" => "l", "worker" => "w"},
      "session" => "s",
      "now" => 0,
      "max_messages" => 10,
      "steps" => [step]
    }

    green = Scenario.run(c, spec)
    red = Scenario.run(c, put_in(spec, ["steps"], [put_in(step, ["expect", "outcome"], "wrong")]))

    emit(
      "runner-real-replay-mutation-control",
      match?({:ok, _}, green) and
        match?({:error, %{"error" => "expectation_mismatch"}}, red),
      %{green: tagged(green), mutant: tagged(red)}
    )
  end

  def old(dir) do
    # A genuine old compiler sees the new core syntax and must reject it.
    suite = dir |> Path.join("suite.json") |> File.read!() |> JSON.decode!()
    source = Enum.find(suite["cases"], &(&1["id"] == "disjoint-total-control"))["source"]
    result = ContractLab.compile_source(source)
    emit("old-compiler-rejects-reply-syntax", match?({:error, _}, result), tagged(result))

    # Exercise the old public Engine and StateCodec with an unknown extension
    # marker / additional saved-state key, not a test-owned reader implementation.
    {:ok, c} = legacy()

    opts = [
      session_id: "s",
      now: 0,
      max_messages: 10,
      bindings: %{"lead" => "l", "worker" => "w"},
      room_policy: :unchecked
    ]

    marked = put_in(c, [:card, :extensions], ["contract.replies/1"])
    new = Engine.new(marked, opts)

    emit(
      "old-engine-rejects-compiled-extension",
      new == {:error, :unsupported_extension},
      tagged(new)
    )

    {:ok, s} = Engine.new(c, opts)
    original = StateCodec.export(s)
    {:ok, _} = StateCodec.restore(c, original, 0)

    saved =
      original
      |> put_in(["state", "wait"], %{
        "id" => "due-3",
        "head" => "req",
        "due_at" => 600_000,
        "remaining" => ["worker"]
      })

    restored = StateCodec.restore(c, saved, 0)

    emit(
      "old-codec-rejects-unknown-wait",
      restored == {:error, :invalid_checkpoint},
      tagged(restored)
    )
  end

  def tagged({:ok, value}), do: %{ok: value}
  def tagged({:error, value}), do: %{error: value}
end

case System.argv() do
  ["candidate", dir] -> RepliesAdversarial.candidate(dir)
  ["old", dir] -> RepliesAdversarial.old(dir)
end
