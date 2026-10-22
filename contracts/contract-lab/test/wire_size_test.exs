defmodule ContractLab.WireSizeTest do
  use ExUnit.Case, async: true
  alias ContractLab.{Canonical, Wire}

  @carrier %{
    "profile" => "band-contract/carrier/1",
    "name" => "size-test/1",
    "fields" => %{"id" => "header:ID", "kind" => "header:Kind"}
  }

  test "carrier growth cannot bypass the default normalized-envelope limit" do
    message = %{"id" => "m", "kind" => String.duplicate("k", 16_384), "payload" => %{}}
    opts = [carrier: @carrier, transport: %{"header:ID" => "m", "header:Kind" => message["kind"]}]

    assert {:error, :message_size} = Wire.decode(~s({"payload":{}}), opts)
    assert {:error, :message_size} = Wire.encode(message, opts)
  end

  test "exact canonical merged-byte limit passes and one byte below rejects" do
    message = %{"id" => "m", "kind" => "\n\\🚀", "payload" => %{}}
    transport = %{"header:ID" => "m", "header:Kind" => message["kind"]}
    limit = byte_size(Canonical.encode(message))
    opts = [carrier: @carrier, transport: transport, max_bytes: limit]

    assert {:ok, ^message} = Wire.decode(~s({"payload":{}}), opts)
    assert {:ok, ~s({"payload":{}})} = Wire.encode(message, opts)

    assert {:error, :message_size} =
             Wire.decode(~s({"payload":{}}), Keyword.put(opts, :max_bytes, limit - 1))

    assert {:error, :message_size} =
             Wire.encode(message, Keyword.put(opts, :max_bytes, limit - 1))
  end

  test "legacy id and session also count toward the normalized-envelope limit" do
    message = %{"id" => "m", "session" => "s", "kind" => "ping", "payload" => %{}}
    limit = byte_size(Canonical.encode(message))
    opts = [transport: Map.take(message, ["id", "session"]), max_bytes: limit]
    body = Canonical.encode(Map.drop(message, ["id", "session"]))

    assert {:ok, ^message} = Wire.decode(body, opts)
    assert {:error, :message_size} = Wire.decode(body, Keyword.put(opts, :max_bytes, limit - 1))

    assert {:error, :message_size} =
             Wire.encode(message, Keyword.put(opts, :max_bytes, limit - 1))
  end
end
