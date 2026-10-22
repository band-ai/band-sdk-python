defmodule ContractLab.WireLegacyUtf8Test do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Wire}

  @message %{"id" => "m", "session" => "s", "kind" => "ping", "payload" => %{}}

  for field <- ["id", "session"] do
    test "legacy #{field} rejects invalid UTF-8 as json on decode and encode" do
      field = unquote(field)
      body = Canonical.encode(Map.delete(@message, field))

      for invalid <- [<<255>>, <<0xC3>>, <<0xC0, 0xAF>>, <<0xED, 0xA0, 0x80>>] do
        opts = [transport: %{field => invalid}]
        assert {:error, :json} = Wire.decode(body, opts)
        assert {:error, :json} = Wire.encode(Map.put(@message, field, invalid), opts)
      end
    end
  end

  test "legacy valid Unicode values remain exact without normalization" do
    for field <- ["id", "session"], text <- ["é", "e\u0301", "🚀", <<0>>] do
      message = Map.put(@message, field, text)
      opts = [transport: %{field => text}]

      assert {:ok, bytes} = Wire.encode(message, opts)
      assert {:ok, ^message} = Wire.decode(bytes, opts)
    end
  end

  test "legacy transport structure errors retain their existing classification" do
    for transport <- [%{"id" => 4}, %{"kind" => <<255>>}, %{"session" => ""}, []] do
      assert {:error, :transport_fields} =
               Wire.decode(Canonical.encode(@message), transport: transport)

      assert {:error, :transport_fields} = Wire.encode(@message, transport: transport)
    end
  end
end
