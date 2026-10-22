defmodule ContractLab.TestClock do
  @moduledoc false
  # Profile-2 contracts must declare a whole-run clock. Fixtures that are not
  # about the clock get the explicit opt-out: a bare `contract` line becomes
  # `contract @infinity => expired` in place, otherwise the line is appended.
  # Either way body line numbers and documentation attachment are unchanged.
  @opt_out "contract @infinity => expired"

  def ensure(bytes) when is_binary(bytes) do
    case split(bytes) do
      {header, body} ->
        if String.contains?(header, "0.2.0-lab.1") and not declared?(header, body),
          do: header <> add_clock(body),
          else: bytes

      nil ->
        bytes
    end
  end

  def ensure_body(body) when is_binary(body),
    do: if(declared?("", body), do: body, else: add_clock(body))

  defp split(bytes) do
    case Regex.run(~r/\A(---\r?\n.*?\r?\n---\r?\n)(.*)\z/s, bytes, capture: :all_but_first) do
      [header, body] ->
        {header, body}

      nil ->
        nil
    end
  end

  defp declared?(_header, body), do: Regex.match?(~r/^\s*(contract|\+CONTRACT) @/m, body)

  defp add_clock(body) do
    if Regex.match?(~r/^\s*(contract|\+CONTRACT)\s*$/m, body) do
      Regex.replace(
        ~r/^(\s*)(contract|\+CONTRACT)(\s*)$/m,
        body,
        "\\1\\2 @infinity => expired\\3",
        global: false
      )
    else
      if body == "" or String.ends_with?(body, "\n"),
        do: body <> @opt_out <> "\n",
        else: body <> "\n" <> @opt_out
    end
  end
end
