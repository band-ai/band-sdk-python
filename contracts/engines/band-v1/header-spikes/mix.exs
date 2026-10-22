defmodule HeaderSpikes.MixProject do
  use Mix.Project

  def project do
    [
      app: :header_spikes,
      version: "0.1.0",
      elixir: "~> 1.18",
      deps: [{:yaml_elixir, "== 2.9.0 or == 2.12.2"}]
    ]
  end

  def application, do: [extra_applications: [:crypto]]
end
