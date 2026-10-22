defmodule ContractLab.Runner do
  @moduledoc "Run a callback in a monitored worker with a timeout and owner-death cleanup."
  def run(fun, timeout) when is_function(fun, 0) and is_integer(timeout) and timeout > 0 do
    caller = self()
    reference = make_ref()
    {guardian, monitor} = spawn_monitor(fn -> guard(caller, reference, fun, timeout) end)

    receive do
      {^reference, result} ->
        Process.demonitor(monitor, [:flush])
        result

      {:DOWN, ^monitor, :process, ^guardian, reason} ->
        {:error, fault("guardian", inspect(reason))}
    after
      timeout + 1000 ->
        Process.exit(guardian, :kill)
        Process.demonitor(monitor, [:flush])
        {:error, fault("timeout", "Callback guardian timed out")}
    end
  end

  def fault(type, message), do: %{"type" => type, "message" => String.slice(message, 0, 4000)}

  defp guard(caller, reference, fun, timeout) do
    Process.flag(:trap_exit, true)
    owner = Process.monitor(caller)
    guardian = self()

    worker =
      spawn_link(fn ->
        result =
          try do
            {:ok, fun.()}
          rescue
            exception ->
              {:error,
               Map.put(
                 fault("exception", Exception.message(exception)),
                 "stack",
                 Exception.format_stacktrace(__STACKTRACE__) |> String.slice(0, 4000)
               )}
          catch
            kind, reason -> {:error, fault(Atom.to_string(kind), inspect(reason))}
          end

        send(guardian, {:result, self(), result})
      end)

    result =
      receive do
        {:result, ^worker, result} -> result
        {:EXIT, ^worker, reason} -> {:error, fault("exit", inspect(reason))}
        {:DOWN, ^owner, :process, ^caller, _} -> :owner_down
      after
        timeout -> {:error, fault("timeout", "Callback exceeded #{timeout} milliseconds")}
      end

    Process.exit(worker, :kill)

    receive do
      {:EXIT, ^worker, _} -> :ok
    after
      100 -> :ok
    end

    Process.demonitor(owner, [:flush])
    if result != :owner_down, do: send(caller, {reference, result})
  end
end
