defmodule RoomSim.MixProject do
  use Mix.Project

  def project do
    [app: :room_sim, version: "0.1.0", elixir: "~> 1.18", deps: []]
  end

  def application, do: [extra_applications: [:crypto]]
end
