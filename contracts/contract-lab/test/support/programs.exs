defmodule ContractLab.TestPrograms.Counter do
  @behaviour ContractLab.Program
  def state_version, do: "1"
  def init(_view), do: {:ok, %{"total" => 0}}

  def handle(%{"kind" => "increment", "payload" => %{"by" => by}}, state) do
    total = state["total"] + by

    {:ok, %{"total" => total},
     [%{"type" => "room", "kind" => "total", "payload" => %{"value" => total}}]}
  end

  def handle(_, _), do: {:unhandled, "This input needs an agent"}
end

defmodule ContractLab.TestPrograms.Broken do
  @behaviour ContractLab.Program
  alias ContractLab.TestPrograms.Counter
  defdelegate state_version(), to: Counter
  defdelegate init(view), to: Counter
  def handle(%{"payload" => %{"by" => 13}}, _), do: raise("Counter crashes on 13")
  def handle(event, state), do: Counter.handle(event, state)
end

defmodule ContractLab.TestPrograms.Fixed do
  @behaviour ContractLab.Program
  alias ContractLab.TestPrograms.Counter
  defdelegate state_version(), to: Counter
  defdelegate init(view), to: Counter
  defdelegate handle(event, state), to: Counter
end

defmodule ContractLab.TestPrograms.Extended do
  @behaviour ContractLab.Program
  alias ContractLab.TestPrograms.Counter
  defdelegate state_version(), to: Counter
  defdelegate init(view), to: Counter
  def handle(%{"kind" => "stop"}, state), do: {:ok, state, []}
  def handle(event, state), do: Counter.handle(event, state)
end

defmodule ContractLab.TestPrograms.Divergent do
  @behaviour ContractLab.Program
  alias ContractLab.TestPrograms.Counter
  defdelegate state_version(), to: Counter
  defdelegate init(view), to: Counter

  def handle(event, state),
    do: Counter.handle(update_in(event, ["payload", "by"], &(&1 * 2)), state)
end

defmodule ContractLab.TestPrograms.VersionTwo do
  @behaviour ContractLab.Program
  alias ContractLab.TestPrograms.Counter
  def state_version, do: "2"
  defdelegate init(view), to: Counter
  defdelegate handle(event, state), to: Counter
end

defmodule ContractLab.TestPrograms.Hangs do
  @behaviour ContractLab.Program
  alias ContractLab.TestPrograms.Counter
  defdelegate state_version(), to: Counter
  defdelegate init(view), to: Counter

  def handle(%{"payload" => %{"by" => 13}}, _) do
    if pid = Application.get_env(:contract_lab, :hang_observer),
      do: send(pid, {:hanging_worker, self()})

    receive do
      :never -> :never
    end
  end

  def handle(event, state), do: Counter.handle(event, state)
end

defmodule ContractLab.TestPrograms.ExternalEffect do
  @behaviour ContractLab.Program
  def state_version, do: "1"
  def init(_), do: {:ok, %{"total" => 0}}

  def handle(event, state) do
    total = state["total"] + event["payload"]["by"]

    {:ok, %{"total" => total},
     [%{"type" => "effect", "action" => "record", "payload" => %{"value" => total}}]}
  end
end

defmodule ContractLab.TestPrograms.InvalidOutput do
  @behaviour ContractLab.Program
  def state_version, do: "1"
  def init(_), do: {:ok, %{"total" => 0}}

  def handle(_event, _state),
    do:
      {:ok, %{"total" => 0},
       [%{"type" => "room", "kind" => "total", "payload" => %{"value" => "wrong type"}}]}
end

defmodule ContractLab.TestPrograms.CrashingVersion do
  @behaviour ContractLab.Program
  alias ContractLab.TestPrograms.Counter
  def state_version, do: raise("Version callback crashed")
  defdelegate init(view), to: Counter
  defdelegate handle(event, state), to: Counter
end

defmodule ContractLab.TestPrograms.ChangedInitial do
  @behaviour ContractLab.Program
  alias ContractLab.TestPrograms.Counter
  defdelegate state_version(), to: Counter
  def init(_), do: {:ok, %{"total" => 1000}}
  defdelegate handle(event, state), to: Counter
end
