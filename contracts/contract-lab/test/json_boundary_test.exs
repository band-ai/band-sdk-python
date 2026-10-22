defmodule ContractLab.JsonBoundaryTest do
  use ExUnit.Case, async: true
  alias ContractLab.Wire

  test "generic service JSON preserves exact integers and rejects ambiguous documents" do
    assert {:ok, %{"card" => %{"roles" => ["a"]}, "limit" => 9_007_199_254_740_991}} =
             Wire.parse_json(~s({"card":{"roles":["a"]},"limit":9007199254740991}), 128)

    for {bytes, error} <- [
          {~s({"x":1,"x":2}), :duplicate_key},
          {~s({"nested":[{"x":1,"x":2}]}), :duplicate_key},
          {~s({"n":9007199254740992}), :unsupported_number},
          {~s({"n":1e0}), :unsupported_number},
          {~s({"n":0.0}), :unsupported_number},
          {~s({}{}), :json},
          {<<255>>, :json}
        ] do
      assert {:error, ^error} = Wire.parse_json(bytes, 128)
    end
  end

  test "size limit applies before decoding and cannot be disabled by invalid options" do
    assert {:ok, %{}} = Wire.parse_json("{}", 2)

    for {bytes, limit} <- [{"{}", 1}, {"{}", 0}, {"{}", :infinite}, {nil, 128}] do
      assert {:error, :message_size} = Wire.parse_json(bytes, limit)
    end
  end
end
