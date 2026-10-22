defmodule ContractLab.Source do
  @moduledoc "Current file-pinned source envelope only; normalized legacy cards are retired."
  def compile(%{"profile" => "band-contract/file-source/1"} = source),
    do: ContractLab.FileSource.compile(source)

  def compile(_), do: {:error, :input}
end
