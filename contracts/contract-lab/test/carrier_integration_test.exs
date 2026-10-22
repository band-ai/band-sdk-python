defmodule ContractLab.CarrierIntegrationTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Engine, Wire}

  @fields %{
    "id" => "header:Message-ID",
    "session" => "header:X-Band-Session",
    "kind" => "header:X-Band-Kind",
    "expected_version" => "header:X-Band-Version"
  }
  @email %{"profile" => "band-contract/carrier/1", "name" => "email/1", "fields" => @fields}
  @message %{
    "id" => "m",
    "session" => "s",
    "kind" => "ping",
    "payload" => %{},
    "expected_version" => 0
  }
  @raw """
  ---
  name: carrier-retry
  description: Independent QA of carrier-neutral receipts.
  engine: 0.2.0-lab.1
  roles: [sender, receiver]
  ---
  contract @infinity => expired
  type ping
  state new
    sender -> receiver: ping() => done
  end done
  """

  defp opts(values), do: [carrier: @email, transport: values]

  test "all sixteen supplied-field subsets preserve actual encode/decode identity" do
    keys = Enum.sort(Map.keys(@fields))

    for mask <- 0..15 do
      supplied =
        keys
        |> Enum.with_index()
        |> Enum.filter(fn {_, n} ->
          Bitwise.band(mask, Bitwise.bsl(1, n)) != 0
        end)
        |> Enum.map(&elem(&1, 0))

      transport = Map.new(supplied, &{@fields[&1], @message[&1]})
      assert {:ok, bytes} = Wire.encode(@message, opts(transport))
      assert JSON.decode!(bytes) == Map.drop(@message, supplied)
      assert {:ok, @message} = Wire.decode(bytes, opts(transport))
    end
  end

  test "typed external versions use the envelope integer domain, including unsafe numbers" do
    body = Canonical.encode(Map.delete(@message, "expected_version"))

    for value <- [0, 9_007_199_254_740_991] do
      assert {:ok, message} = Wire.decode(body, opts(%{@fields["expected_version"] => value}))
      assert message["expected_version"] === value
    end

    for value <- [-1, true, nil, "0"] do
      assert {:error, :envelope} =
               Wire.decode(body, opts(%{@fields["expected_version"] => value}))

      assert {:error, :envelope} =
               Wire.encode(Map.put(@message, "expected_version", value), opts(%{}))
    end

    for value <- [9_007_199_254_740_992, 1.0] do
      assert {:error, :unsupported_number} =
               Wire.decode(body, opts(%{@fields["expected_version"] => value}))

      assert {:error, :unsupported_number} =
               Wire.encode(
                 Map.put(@message, "expected_version", value),
                 opts(%{@fields["expected_version"] => value})
               )
    end
  end

  test "mapped optional fields cannot be introduced by encoding and absent values stay absent" do
    minimal = Map.drop(@message, ["session", "expected_version"])
    assert {:ok, bytes} = Wire.encode(minimal, opts(%{}))
    assert {:ok, ^minimal} = Wire.decode(bytes, opts(%{}))

    assert {:error, :transport_conflict} =
             Wire.encode(minimal, opts(%{"header:X-Band-Session" => "s"}))
  end

  test "invalid UTF-8 carrier values never reach canonicalization or engine admission" do
    for field <- ["id", "session", "kind"], invalid <- [<<255>>, <<0xED, 0xA0, 0x80>>] do
      transport = %{@fields[field] => invalid}
      body = Canonical.encode(Map.delete(@message, field))

      malformed_body =
        :binary.replace(Canonical.encode(Map.put(@message, field, "__bad__")), "__bad__", invalid)

      assert {:error, :json} = Wire.decode(malformed_body)
      assert {:error, :json} = Wire.decode(body, opts(transport))

      assert {:error, :json} =
               Wire.encode(Map.put(@message, field, invalid), opts(transport))
    end
  end

  test "cross-carrier replay keeps the original fingerprint and exact receipt after termination" do
    source = %{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => @raw,
      "sha256" => Canonical.hash(@raw)
    }

    assert {:ok, contract} = ContractLab.compile_source(source)

    policy = %{
      revision: "carrier-test",
      fixed_roles: %{},
      principals:
        Map.new(["alice", "bob"], &{&1, %{kind: :agent, active: true, permission: "participant"}})
    }

    assert {:ok, state} =
             Engine.new(contract,
               session_id: "s",
               now: 0,
               max_messages: 4,
               bindings: %{"sender" => "alice", "receiver" => "bob"},
               room_policy: policy
             )

    http = [
      carrier: %{
        "profile" => "band-contract/carrier/1",
        "name" => "http/1",
        "fields" => %{"session" => "path:id"}
      },
      transport: %{"path:id" => "s"}
    ]

    assert {:ok, first} = Wire.decode(Canonical.encode(Map.delete(@message, "session")), http)
    values = Map.new(@fields, fn {key, source} -> {source, @message[key]} end)
    assert {:ok, retry} = Wire.decode(~s({"payload":{}}), opts(values))
    assert first === retry

    assert {%{status: :accepted} = accepted, state} =
             Engine.step(state, first, actor: "alice", now: 0, room_policy: policy)

    receipt = state.receipts["m"]

    assert {%{status: :duplicate, original: ^accepted}, replayed} =
             Engine.step(state, retry, actor: "alice", now: 1, room_policy: policy)

    assert replayed.receipts["m"] === receipt
    assert replayed.version == 1

    assert {%{reason: :id_conflict}, _} =
             Engine.step(replayed, retry, actor: "bob", now: 2, room_policy: policy)

    assert {%{reason: :id_conflict}, _} =
             Engine.step(replayed, Map.put(retry, "expected_version", 1),
               actor: "alice",
               now: 3,
               room_policy: policy
             )
  end
end
