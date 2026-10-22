defmodule ContractLab.Roster do
  @moduledoc "Instantiate declared role collections once from trusted creation bindings."
  @limit 256
  # A quarter of the 1 MiB redacted checkpoint: rosters are copied into every agent projection.
  @config_bytes 262_144

  def prepare(c, opts) do
    if Map.has_key?(c, :role_sets) do
      config = Keyword.get(opts, :bindings)

      with {:ok, bound, bindings} <- bind(c, config),
           do: {:ok, bound, Keyword.put(opts, :bindings, bindings), %{roster_config: config}}
    else
      {:ok, c, opts, %{}}
    end
  end

  def restore(c, s) do
    if Map.has_key?(c, :role_sets) do
      with {:ok, bound, bindings} <- bind(Map.get(c, :template, c), Map.get(s, :roster_config)),
           true <- bindings == s.bindings do
        {:ok, bound}
      else
        _ -> {:error, :invalid_checkpoint}
      end
    else
      {:ok, c}
    end
  end

  def bind(c, config) do
    try do
      check(is_map(config) and Enum.sort(Map.keys(config)) == Enum.sort(c.roles))

      members =
        Enum.flat_map(c.roles, fn role ->
          if Map.has_key?(c.role_sets, role) do
            values = config[role]
            check(is_list(values) and values != [] and length(values) < @limit)

            Enum.map(values, fn v ->
              typed = c.role_sets[role].member != "agent"
              allowed = if typed, do: ~w(actor name attributes), else: ~w(actor name)

              check(
                is_map(v) and
                  Enum.all?(Map.keys(v), &(&1 in (allowed ++ ["guidance"])))
              )

              check(is_binary(v["name"]) and Regex.match?(~r/^[a-z][a-z0-9_]{0,63}$/, v["name"]))
              check(id?(v["actor"]))
              guidance = Map.get(v, "guidance", "")

              check(
                is_binary(guidance) and String.valid?(guidance) and byte_size(guidance) <= 16_384
              )

              Map.merge(
                %{
                  role: role <> "/" <> v["name"],
                  group: role,
                  actor: v["actor"],
                  assignment: guidance
                },
                participant_attributes(c, role, v)
              )
            end)
          else
            check(id?(config[role]))
            [%{role: role, group: role, actor: config[role], assignment: nil}]
          end
        end)

      check(length(members) <= @limit)
      check(byte_size(ContractLab.Canonical.encode(config)) <= @config_bytes)
      check(length(Enum.uniq_by(members, & &1.role)) == length(members))
      check(length(Enum.uniq_by(members, & &1.actor)) == length(members))

      expand = fn roles ->
        Enum.flat_map(roles, fn role -> for m <- members, m.group == role, do: m.role end)
      end

      rule = fn r -> %{r | from: expand.(r.from), to: expand.(r.to)} end

      states =
        Map.new(c.states, fn {name, s} ->
          {name,
           %{
             s
             | rules: Enum.map(s.rules, rule),
               deadlines: Enum.map(s.deadlines, &%{&1 | participants: expand.(&1.participants)})
           }}
        end)

      metadata =
        Map.new(members, fn m ->
          meta = c.role_metadata[m.group] |> Map.put(:name, m.role)
          meta = Map.merge(meta, Map.take(m, [:participant_type, :attributes]))

          {m.role,
           if(m.assignment,
             do: Map.merge(meta, %{collection: m.group, assignment: m.assignment}),
             else: meta
           )}
        end)

      permissions =
        Map.new(members, &{&1.role, Map.get(c.extensions.permissions, &1.group, "participant")})

      triggers =
        Enum.flat_map(c.extensions.triggers, fn t ->
          if t.subject in c.roles,
            do: Enum.map(expand.([t.subject]), &%{t | subject: &1, id: t.id <> "/" <> &1}),
            else: [t]
        end)

      bound = %{
        c
        | roles: Enum.map(members, & &1.role),
          role_metadata: metadata,
          states: states,
          any_rules: Enum.map(c.any_rules, rule),
          room_rules: Enum.map(c.room_rules, rule),
          extensions: %{c.extensions | permissions: permissions, triggers: triggers}
      }

      bound =
        if Map.has_key?(c, :collections),
          do:
            Map.put(
              bound,
              :collections,
              Map.new(c.collections, fn {name, d} ->
                {name, %{d | from: expand.(d.from), to: expand.(d.to)}}
              end)
            ),
          else: bound

      {:ok, Map.put(bound, :template, c), Map.new(members, &{&1.role, &1.actor})}
    catch
      :invalid_bindings -> {:error, :invalid_bindings}
    end
  end

  defp id?(v), do: is_binary(v) and String.valid?(v) and byte_size(v) in 1..256

  defp participant_attributes(c, role, member) do
    name = c.role_sets[role].member

    if name == "agent" do
      %{}
    else
      attributes = Map.get(member, "attributes", %{})
      check(ContractLab.Engine.validate_payload(c.participant_types[name], attributes))
      check(byte_size(ContractLab.Canonical.encode(attributes)) <= 16_384)
      %{participant_type: name, attributes: attributes}
    end
  end

  defp check(true), do: :ok
  defp check(_), do: throw(:invalid_bindings)
end
