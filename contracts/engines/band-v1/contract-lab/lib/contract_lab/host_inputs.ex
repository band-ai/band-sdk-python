defmodule ContractLab.HostInputs do
  @moduledoc "Fixed-key JSON conversion for trusted host pipes; never accepts arbitrary atoms."
  @policies %{"accepted" => :accepted, "transition" => :transition, "marked" => :marked}
  @kinds %{"agent" => :agent, "human" => :human}

  # `profile` is fixed when the adapter is built, never read from a request: only
  # the local reference profile may start a session in the explicit unchecked mode.
  def new_opts(_c, config, profile \\ :hosted) do
    [
      session_id: config["session"],
      bindings: config["bindings"],
      now: config["now"],
      inactivity: @policies[Map.get(config, "inactivity", "accepted")],
      max_messages: config["max_messages"],
      max_bytes: Map.get(config, "max_bytes", 16_384),
      capabilities: Map.get(config, "capabilities", []),
      max_controls: Map.get(config, "max_controls", 0),
      initial_presence: Map.get(config, "initial_presence", %{}),
      watch_bindings: Map.get(config, "watch_bindings", %{}),
      room_policy: start_policy(config["room_policy"], profile)
    ]
  end

  def context(config),
    do: [actor: config["actor"], now: config["now"], room_policy: policy(config["room_policy"])]

  defp start_policy(%{"mode" => "unchecked"} = p, :local_reference) when map_size(p) == 1,
    do: :unchecked

  defp start_policy(p, _), do: policy(p)

  defp policy(nil), do: nil

  defp policy(%{"revision" => revision, "principals" => principals, "fixed_roles" => fixed} = p)
       when map_size(p) == 3 and is_map(principals) and is_map(fixed) do
    try do
      typed =
        Map.new(principals, fn {id, row} ->
          %{"kind" => kind, "active" => active, "permission" => level} = row
          true = map_size(row) == 3 and is_boolean(active)
          {:ok, kind} = Map.fetch(@kinds, kind)
          {id, %{kind: kind, active: active, permission: level}}
        end)

      %{revision: revision, principals: typed, fixed_roles: fixed}
    rescue
      _ -> :invalid
    end
  end

  defp policy(_), do: :invalid
end
