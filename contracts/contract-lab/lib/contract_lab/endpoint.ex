defmodule ContractLab.Endpoint do
  @moduledoc "JSON-lines engine host adapter. Its input pipe is trusted host control, never a public room API."
  alias ContractLab.{AgentGate, Canonical, Checkpoint, Engine, FileSource, HostInputs}

  @formats %{"frontmatter" => :frontmatter, "standard" => :standard}
  # Covers the bounded entry/body with JSON escaping, before allocating a decoded envelope.
  @max_file_source_bytes 4_194_304

  def run_from_env do
    {:ok, _} = Application.ensure_all_started(:contract_lab)

    run(
      System.fetch_env!("BAND_CONTRACT_SOURCE"),
      System.get_env("BAND_CONTRACT_FORMAT", "frontmatter")
    )
  end

  def run(path, format \\ "frontmatter") do
    {:ok, contract} = load(path, format)
    emit(%{ready: true, definition: contract.digest, source: contract.source_digest})
    loop(contract, nil)
  end

  defp load(path, "file-source") do
    with {:ok, bytes, _identity} <- HeaderSpikes.FileSnapshot.read(path, @max_file_source_bytes),
         {:ok, source} <- JSON.decode(bytes) do
      FileSource.compile(source)
    end
  end

  defp load(path, format) do
    {:ok, format} = Map.fetch(@formats, format)
    ContractLab.load(path, format)
  end

  defp loop(contract, state) do
    case IO.read(:stdio, :line) do
      :eof ->
        :ok

      {:error, reason} ->
        raise inspect(reason)

      line ->
        {result, next} =
          try do
            if byte_size(line) > 16_777_216, do: raise("Request too large")
            dispatch(contract, state, JSON.decode!(line))
          rescue
            error -> {%{error: Exception.message(error)}, state}
          end

        emit(result)
        loop(contract, next)
    end
  end

  defp dispatch(c, s, %{
         "op" => "check",
         "projection" => p,
         "message" => m,
         "actor" => actor,
         "now" => now
       }),
       do: {AgentGate.check(c, p, m, actor, now), s}

  # A refused new keeps the live session, like every other op's error path.
  defp dispatch(c, s, %{"op" => "new"} = opts) do
    case Engine.new(c, HostInputs.new_opts(c, opts)) do
      {:ok, created} -> {%{ok: true}, created}
      {:error, reason} -> {%{error: reason}, s}
    end
  end

  defp dispatch(_c, s, %{"op" => "checkpoint"}) when is_map(s) do
    case Checkpoint.export(s) do
      {:ok, packet} -> {packet, s}
      {:error, reason} -> {%{error: reason}, s}
    end
  end

  # Like new, restore states the host's capabilities; none stated is none (fail closed).
  defp dispatch(c, s, %{"op" => "restore", "checkpoint" => packet, "now" => now} = op) do
    case Checkpoint.restore(c, packet, now, :hosted,
           capabilities: Map.get(op, "capabilities", [])
         ) do
      {:ok, restored} -> {%{ok: true}, restored}
      {:error, reason} -> {%{error: reason}, s}
    end
  end

  defp dispatch(_c, s, %{"op" => "summary"}) when is_map(s), do: {summary(s), s}

  defp dispatch(_c, s, %{"op" => "tick", "now" => now}) when is_map(s) do
    case Engine.tick(s, now) do
      {:ok, advanced} -> {summary(advanced), advanced}
      {:error, reason, _} -> Engine.mark_unchecked({%{error: reason}, s})
    end
  end

  defp dispatch(
         _c,
         s,
         %{"op" => "step", "message" => m, "actor" => _actor, "now" => _now} = request
       )
       when is_map(s),
       do: Engine.step(s, m, HostInputs.context(request))

  defp dispatch(_c, s, %{"op" => "host_signal", "event" => event, "now" => now}) when is_map(s),
    do: Engine.signal(s, event, authority: :host, now: now)

  defp dispatch(_c, s, %{"op" => "stop_contribution", "report" => report} = request)
       when is_map(s),
       do: Engine.stop_contribution(s, report, HostInputs.context(request))

  defp dispatch(_c, s, %{"op" => "fail_contribution", "report" => report, "now" => now})
       when is_map(s),
       do: Engine.fail_contribution(s, report, authority: :host, now: now)

  defp dispatch(_c, s, %{"op" => "view", "actor" => actor, "now" => now} = request)
       when is_map(s) do
    case AgentGate.project(s, actor, now, HostInputs.context(request)[:room_policy]) do
      {:ok, view, next} -> {view, next}
      {:error, reason} -> Engine.mark_unchecked({%{error: reason}, s})
    end
  end

  defp dispatch(c, s, %{"op" => "definition", "role" => role}) do
    case ContractLab.for_role(c, role) do
      {:ok, view} -> {view, s}
      {:error, reason} -> {%{error: reason}, s}
    end
  end

  defp dispatch(_, s, _), do: {%{error: :invalid_request}, s}
  defp summary(s), do: Engine.summary(s)
  defp emit(value), do: value |> Canonical.encode() |> IO.puts()
end
