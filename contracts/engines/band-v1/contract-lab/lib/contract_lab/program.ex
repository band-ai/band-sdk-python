defmodule ContractLab.Program do
  @moduledoc "A deterministic role program returns JSON state and declared effect intents."
  @callback init(map()) :: {:ok, map()}
  @callback handle(map(), map()) :: {:ok, map(), [map()]} | {:unhandled, term()}
  @callback state_version() :: String.t()
  def fingerprint(module),
    do:
      ContractLab.Canonical.digest(%{
        "module" => Atom.to_string(module),
        "code" => Base.encode16(module.module_info(:md5), case: :lower)
      })
end
