defmodule ContractLab.MixProject do
  use Mix.Project

  def project,
    do: [
      app: :contract_lab,
      version: "0.1.0",
      elixir: "~> 1.18",
      releases: [
        contract_engine: [
          include_executables_for: [:unix],
          # env.sh forces distribution off for every release entrypoint.
          # This inert marker is not a shared runtime authentication secret.
          cookie: "non_distributed_contract_engine"
        ]
      ],
      test_ignore_filters: [~r{^test/support/}],
      deps: [
        {:header_spikes, path: "../header-spikes"},
        {:room_sim, path: "../room-sim", only: :test}
      ]
    ]

  def application, do: [extra_applications: [:crypto]]
end
