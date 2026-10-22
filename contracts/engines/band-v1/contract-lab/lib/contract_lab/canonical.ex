defmodule ContractLab.Canonical do
  @moduledoc "Canonical JSON for this profile: sorted UTF-8 keys and integer-only numbers."
  def encode(value), do: value |> enc() |> IO.iodata_to_binary()
  def digest(value), do: hash(encode(value))
  def hash(bytes), do: :crypto.hash(:sha256, bytes) |> Base.encode16(case: :lower)
  defp enc(nil), do: "null"
  defp enc(true), do: "true"
  defp enc(false), do: "false"
  defp enc(value) when is_binary(value), do: JSON.encode!(value)
  defp enc(value) when is_integer(value), do: Integer.to_string(value)
  defp enc(value) when is_atom(value), do: enc(Atom.to_string(value))
  defp enc(value) when is_tuple(value), do: enc(Tuple.to_list(value))

  defp enc(value) when is_list(value),
    do: ["[", Enum.intersperse(Enum.map(value, &enc/1), ","), "]"]

  defp enc(value) when is_map(value) do
    pairs = value |> Enum.map(fn {k, v} -> {to_string(k), v} end) |> Enum.sort_by(&elem(&1, 0))
    ["{", Enum.intersperse(Enum.map(pairs, fn {k, v} -> [enc(k), ":", enc(v)] end), ","), "}"]
  end
end
