defmodule ContractLab.WireCarrierTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Wire}

  @message %{
    "id" => "m1",
    "session" => "s1",
    "kind" => "ping",
    "payload" => %{"topic" => "build"},
    "expected_version" => 3
  }
  @carrier %{
    "profile" => "band-contract/carrier/1",
    "name" => "http/1",
    "fields" => %{
      "id" => "header:Message-ID",
      "session" => "path:id",
      "kind" => "route:kind",
      "expected_version" => "header:X-Version"
    }
  }
  @transport %{
    "header:Message-ID" => "m1",
    "path:id" => "s1",
    "route:kind" => "ping",
    "header:X-Version" => 3
  }

  test "all mappable fields normalize and are omitted only when supplied" do
    opts = [carrier: @carrier, transport: @transport]
    assert {:ok, bytes} = Wire.encode(@message, opts)
    assert JSON.decode!(bytes) == Map.take(@message, ["payload"])
    assert {:ok, @message} = Wire.decode(bytes, opts)
    assert {:ok, @message} = Wire.decode(Canonical.encode(@message), opts)
  end

  test "unlisted fields default to body and absent carrier values do not drop fields" do
    carrier = put_in(@carrier, ["fields"], %{"session" => "path:id"})
    opts = [carrier: carrier, transport: %{"path:id" => "s1"}]
    assert {:ok, bytes} = Wire.encode(@message, opts)
    assert JSON.decode!(bytes) == Map.delete(@message, "session")
    assert {:ok, @message} = Wire.decode(bytes, opts)
    assert {:ok, full} = Wire.encode(@message, carrier: @carrier)
    assert JSON.decode!(full) == @message
  end

  test "empty fields map and explicit body fields keep the ordinary envelope" do
    for fields <- [%{}, %{"id" => "body", "kind" => "body"}] do
      opts = [carrier: put_in(@carrier, ["fields"], fields)]
      assert {:ok, bytes} = Wire.encode(@message, opts)
      assert JSON.decode!(bytes) == @message
      assert {:ok, @message} = Wire.decode(bytes, opts)
    end
  end

  test "typed expected_version remains an integer and zero is valid" do
    message = Map.put(@message, "expected_version", 0)
    opts = [carrier: @carrier, transport: Map.put(@transport, "header:X-Version", 0)]
    assert {:ok, bytes} = Wire.encode(message, opts)
    assert {:ok, ^message} = Wire.decode(bytes, opts)
  end

  test "matching body copies are accepted but differing copies conflict on decode and encode" do
    for {source, field} <- [
          {"header:Message-ID", "id"},
          {"path:id", "session"},
          {"route:kind", "kind"},
          {"header:X-Version", "expected_version"}
        ] do
      different = if field == "expected_version", do: 4, else: "different"
      opts = [carrier: @carrier, transport: Map.put(@transport, source, different)]
      assert {:error, :transport_conflict} = Wire.decode(Canonical.encode(@message), opts)
      assert {:error, :transport_conflict} = Wire.encode(@message, opts)
    end
  end

  test "missing required fields remain envelope errors" do
    for field <- ["id", "kind", "payload"] do
      assert {:error, :envelope} =
               Wire.decode(Canonical.encode(Map.delete(@message, field)), carrier: @carrier)
    end

    assert {:error, :envelope} = Wire.decode("{}", carrier: @carrier, transport: @transport)
  end

  test "optional fields are not required merely because they have a carrier mapping" do
    message = Map.drop(@message, ["session", "expected_version"])

    opts = [
      carrier: @carrier,
      transport: Map.take(@transport, ["header:Message-ID", "route:kind"])
    ]

    assert {:ok, bytes} = Wire.encode(message, opts)
    assert {:ok, ^message} = Wire.decode(bytes, opts)
  end

  for {source, value} <- [
        {"header:Message-ID", ""},
        {"route:kind", ""},
        {"path:id", nil},
        {"header:X-Version", "3"},
        {"header:X-Version", -1},
        {"route:kind", ["ping"]}
      ] do
    test "carrier value #{inspect({source, value})} obeys envelope field validation" do
      transport = Map.put(@transport, unquote(source), unquote(Macro.escape(value)))

      assert {:error, :envelope} =
               Wire.decode(~s({"payload":{}}), carrier: @carrier, transport: transport)
    end
  end

  test "carrier numbers have the same strict numeric domain as JSON body values" do
    for value <- [3.0, 9_007_199_254_740_992] do
      assert {:error, :unsupported_number} =
               Wire.decode(~s({"payload":{}}),
                 carrier: @carrier,
                 transport: Map.put(@transport, "header:X-Version", value)
               )
    end
  end

  test "optional empty session follows existing body semantics" do
    message = Map.put(@message, "session", "")
    opts = [carrier: @carrier, transport: Map.put(@transport, "path:id", "")]
    assert {:ok, bytes} = Wire.encode(message, opts)
    assert {:ok, ^message} = Wire.decode(bytes, opts)
  end

  test "only declared non-body source tokens may be supplied" do
    for transport <- [
          %{"unknown:source" => "m1"},
          %{"id" => "m1"},
          %{"body" => "m1"},
          %{:id => "m1"},
          []
        ] do
      opts = [carrier: @carrier, transport: transport]
      assert {:error, :transport_fields} = Wire.decode(Canonical.encode(@message), opts)
      assert {:error, :transport_fields} = Wire.encode(@message, opts)
    end
  end

  test "malformed maps cannot relocate payload, actor, clock or unknown envelope fields" do
    malformed = [
      nil,
      [],
      %{},
      Map.delete(@carrier, "name"),
      Map.put(@carrier, "name", ""),
      Map.put(@carrier, "profile", "band-contract/carrier/2"),
      Map.put(@carrier, "extra", true),
      put_in(@carrier, ["fields"], []),
      put_in(@carrier, ["fields", "id"], ""),
      put_in(@carrier, ["fields", "id"], 7)
    ]

    malformed =
      malformed ++
        for field <- ["payload", "actor", "clock", "extra"],
            do: put_in(@carrier, ["fields", field], "header:untrusted")

    for carrier <- malformed do
      opts = [carrier: carrier]
      assert {:error, :transport_fields} = Wire.decode(Canonical.encode(@message), opts)
      assert {:error, :transport_fields} = Wire.encode(@message, opts)
    end
  end

  test "transport data never changes payload or authenticates an actor" do
    body = @message |> Map.put("actor", "claimed") |> Canonical.encode()
    assert {:error, :envelope} = Wire.decode(body, carrier: @carrier, transport: @transport)
  end

  test "mapped string values reject invalid UTF-8 just like JSON body strings" do
    for {field, source} <- [
          {"id", "header:Message-ID"},
          {"kind", "route:kind"},
          {"session", "path:id"}
        ],
        invalid <- [<<255>>, <<0xC3>>, <<0xC0, 0xAF>>, <<0xED, 0xA0, 0x80>>] do
      opts = [carrier: @carrier, transport: %{source => invalid}]
      body = Canonical.encode(Map.delete(@message, field))

      assert {:error, :json} = Wire.decode(body, opts)
      assert {:error, :json} = Wire.encode(Map.put(@message, field, invalid), opts)
    end
  end

  test "carrier identity and source tokens must be valid UTF-8 strings" do
    for carrier <- [
          Map.put(@carrier, "name", <<255>>),
          put_in(@carrier, ["fields", "id"], <<255>>)
        ] do
      assert {:error, :transport_fields} =
               Wire.decode(Canonical.encode(@message), carrier: carrier)

      assert {:error, :transport_fields} = Wire.encode(@message, carrier: carrier)
    end
  end

  test "valid Unicode carrier values are preserved without normalization" do
    for text <- ["é", "e\u0301", "🚀", <<0>>, ""] do
      message = Map.put(@message, "session", text)

      carrier = %{
        "profile" => "band-contract/carrier/1",
        "name" => "郵便/1",
        "fields" => %{"session" => "header:会話"}
      }

      opts = [carrier: carrier, transport: %{"header:会話" => text}]

      assert {:ok, bytes} = Wire.encode(message, opts)
      assert {:ok, ^message} = Wire.decode(bytes, opts)
    end
  end

  test "existing omitted-carrier id/session options remain compatible" do
    opts = [transport: Map.take(@message, ["id", "session"])]
    assert {:ok, bytes} = Wire.encode(@message, opts)
    assert {:ok, @message} = Wire.decode(bytes, opts)

    for transport <- [%{"kind" => "ping"}, %{"session" => ""}, %{"id" => 4}] do
      assert {:error, :transport_fields} =
               Wire.decode(Canonical.encode(@message), transport: transport)
    end
  end
end
