defmodule ContractLab.CommandCapture do
  @moduledoc false
  defstruct data: "", limit: 65_536, truncated: false
end

defimpl Collectable, for: ContractLab.CommandCapture do
  def into(initial) do
    {initial,
     fn
       acc, {:cont, bytes} ->
         bytes = IO.iodata_to_binary(bytes)
         remaining = acc.limit - byte_size(acc.data)
         take = min(remaining, byte_size(bytes))

         %{
           acc
           | data: acc.data <> binary_part(bytes, 0, take),
             truncated: acc.truncated or take < byte_size(bytes)
         }

       acc, :done ->
         acc

       _acc, :halt ->
         :ok
     end}
  end
end

defmodule ContractLab.CommandSidecar do
  @moduledoc "Provider-neutral, local code-generation command followed by required candidate validation."
  alias ContractLab.{Canonical, CommandCapture, Program}

  def new(opts) do
    workspace = opts |> Keyword.fetch!(:workspace) |> Path.expand()
    executable = Keyword.fetch!(opts, :executable)
    args = Keyword.fetch!(opts, :args)
    {validator, validation_args} = Keyword.fetch!(opts, :validate)
    timeout = Keyword.get(opts, :timeout_ms, 30_000)

    unless is_binary(executable) and is_list(args) and is_binary(validator) and
             is_list(validation_args) and
             is_integer(timeout) and timeout > 0 do
      raise ArgumentError,
            "A generator command, validation command, and positive timeout are required"
    end

    fn bundle ->
      generate(workspace, executable, args, validator, validation_args, timeout, bundle)
    end
  end

  def load_programs(workspace) do
    workspace = Path.expand(workspace)
    manifests = Path.wildcard(Path.join([workspace, "releases", "*", "manifest.json"]))

    cond do
      length(manifests) > 1000 ->
        {:error, :registry_limit}

      manifests != [] and
          (private(workspace, :directory) != :ok or
             private(Path.join(workspace, "releases"), :directory) != :ok) ->
        {:error, :artifact_permissions}

      true ->
        Enum.reduce_while(manifests, {:ok, []}, fn path, {:ok, programs} ->
          case load_artifact(path) do
            {:ok, program} -> {:cont, {:ok, programs ++ [program]}}
            {:error, reason} -> {:halt, {:error, reason}}
          end
        end)
    end
  end

  # A release's BEAM is loaded into this VM, so only the host user may write the
  # workspace or a release: owned by this OS user, not a symlink, no group/other write.
  # The OS user comes from /proc/self; without it (off Linux) nothing is private.
  defp private(path, type) do
    with {:ok, %{type: ^type, uid: uid, mode: mode}} <- File.lstat(path),
         {:ok, %{uid: ^uid}} <-
           File.stat(Application.get_env(:contract_lab, :proc_self, "/proc/self")),
         0 <- Bitwise.band(mode, 0o022) do
      :ok
    else
      _ -> {:error, :artifact_permissions}
    end
  end

  defp private_dir(path) do
    with :ok <- File.mkdir_p(path), :ok <- File.chmod(path, 0o700), do: private(path, :directory)
  end

  defp generate(workspace, executable, args, validator, validation_args, timeout, bundle) do
    nonce = Base.encode16(:crypto.strong_rand_bytes(12))
    request = Path.join([workspace, "requests", nonce])
    expected_module = "ContractLab.Generated.P" <> nonce
    bundle_path = Path.join(request, "bundle.json")
    output = Path.join(request, "candidate.ex")

    with :ok <- private_dir(workspace),
         :ok <- private_dir(Path.join(workspace, "requests")),
         :ok <- private_dir(Path.join(workspace, "releases")),
         :ok <- private_dir(request),
         :ok <-
           File.write(
             bundle_path,
             Canonical.encode(Map.put(bundle, "candidate_module", expected_module)) <> "\n",
             [:exclusive]
           ),
         {:ok, capture} <-
           stage_command(
             :generation_failed,
             executable,
             substitute(args, bundle_path, output),
             request,
             timeout
           ),
         :ok <- write_log(request, "generation", capture),
         {:ok, source} <- read_source(output),
         {:ok, module} <- module_name(source, expected_module),
         {:ok, release, candidate} <- stage_source(workspace, source),
         {:ok, validation} <-
           stage_command(
             :validation_failed,
             validator,
             substitute(validation_args, bundle_path, candidate),
             request,
             timeout
           ),
         :ok <- write_log(request, "validation", validation),
         true <- File.read!(candidate) == source || {:error, :candidate_changed},
         {:ok, beam} <- compile_candidate(module, candidate),
         :ok <- File.write(Path.join(release, "program.beam"), beam, [:exclusive]),
         :ok <- File.chmod(Path.join(release, "program.beam"), 0o600),
         :ok <- write_manifest(release, module, source, beam, executable, validator),
         :ok <- File.chmod(Path.join(release, "manifest.json"), 0o600) do
      {:ok, module}
    else
      {:error, reason} ->
        {:error, reason}

      other ->
        {:error, {:candidate_failed, inspect(other)}}
    end
  rescue
    exception -> {:error, {:candidate_failed, Exception.message(exception)}}
  end

  defp stage_command(phase, executable, args, directory, timeout) do
    case command(executable, args, directory, timeout) do
      {capture, 0} ->
        {:ok, capture}

      {%CommandCapture{} = capture, code} ->
        write_log(
          directory,
          if(phase == :generation_failed, do: "generation", else: "validation"),
          capture
        )

        {:error, {phase, code, capture.data}}

      error ->
        error
    end
  end

  defp command(executable, args, directory, timeout_ms) do
    case System.find_executable("timeout") do
      nil ->
        {:error, :timeout_command_missing}

      limiter ->
        duration = Float.to_string(timeout_ms / 1000) <> "s"

        System.cmd(limiter, ["--signal=TERM", "--kill-after=1s", duration, executable | args],
          cd: directory,
          stderr_to_stdout: true,
          into: %CommandCapture{}
        )
    end
  end

  defp substitute(args, bundle, candidate) do
    Enum.map(
      args,
      &(&1
        |> String.replace("{bundle}", bundle)
        |> String.replace("{candidate}", candidate)
        |> String.replace("{output}", candidate))
    )
  end

  defp read_source(path) do
    with {:ok, %{type: :regular, size: size}} when size in 1..131_072 <- File.lstat(path),
         {:ok, source} <- File.read(path),
         true <- String.valid?(source) do
      {:ok, source}
    else
      _ -> {:error, :candidate_source}
    end
  end

  defp module_name(source, expected) do
    case Code.string_to_quoted(source) do
      {:ok, {:defmodule, _, [{:__aliases__, _, parts}, [do: _]]}} ->
        if Enum.map_join(parts, ".", &Atom.to_string/1) == expected do
          module = Module.concat(parts)

          if :code.is_loaded(module) == false,
            do: {:ok, module},
            else: {:error, :module_already_loaded}
        else
          {:error, :candidate_module_mismatch}
        end

      _ ->
        {:error, :candidate_module_mismatch}
    end
  end

  defp stage_source(workspace, source) do
    release = Path.join([workspace, "releases", Canonical.hash(source)])
    candidate = Path.join(release, "candidate.ex")

    with :ok <- private_dir(release),
         :ok <- File.write(candidate, source, [:exclusive]),
         :ok <- File.chmod(candidate, 0o400),
         do: {:ok, release, candidate}
  end

  defp compile_candidate(module, candidate) do
    case Code.compile_file(candidate) do
      [{^module, beam}] ->
        if function_exported?(module, :init, 1) and function_exported?(module, :handle, 2) and
             function_exported?(module, :state_version, 0),
           do: {:ok, beam},
           else: {:error, :program_callbacks}

      _ ->
        {:error, :candidate_module_mismatch}
    end
  end

  defp write_manifest(release, module, source, beam, generator, validator) do
    manifest = %{
      "profile" => "contract-lab/program-artifact/1",
      "program" => Program.fingerprint(module),
      "module" => Atom.to_string(module),
      "source_sha256" => Canonical.hash(source),
      "beam_sha256" => Canonical.hash(beam),
      "generator" => generator,
      "validator" => validator,
      "validation_exit" => 0
    }

    File.write(Path.join(release, "manifest.json"), Canonical.encode(manifest) <> "\n", [
      :exclusive
    ])
  end

  defp write_log(directory, name, capture) do
    suffix = if capture.truncated, do: "\n[output truncated]\n", else: ""
    File.write(Path.join(directory, name <> ".log"), capture.data <> suffix)
  end

  defp load_artifact(path) do
    root = Path.dirname(path)

    with :ok <- private(root, :directory),
         :ok <- private(path, :regular),
         :ok <- private(Path.join(root, "candidate.ex"), :regular),
         :ok <- private(Path.join(root, "program.beam"), :regular),
         {:ok, manifest} <- File.read(path),
         {:ok, %{"profile" => "contract-lab/program-artifact/1"} = manifest} <-
           JSON.decode(manifest),
         {:ok, source} <- File.read(Path.join(root, "candidate.ex")),
         {:ok, beam} <- File.read(Path.join(root, "program.beam")),
         true <-
           (Canonical.hash(source) == manifest["source_sha256"] and
              Canonical.hash(beam) == manifest["beam_sha256"]) || {:error, :artifact_mismatch},
         {:ok, {module, _}} <- :beam_lib.chunks(beam, [:attributes]),
         true <- Atom.to_string(module) == manifest["module"] || {:error, :artifact_mismatch},
         :ok <- load_binary(module, beam, Path.join(root, "program.beam"), manifest["program"]),
         true <-
           Program.fingerprint(module) == manifest["program"] || {:error, :artifact_mismatch} do
      {:ok, module}
    else
      {:error, reason} when reason in [:module_conflict, :artifact_permissions] ->
        {:error, reason}

      _ ->
        {:error, :artifact_mismatch}
    end
  rescue
    _ -> {:error, :artifact_mismatch}
  end

  defp load_binary(module, beam, path, fingerprint) do
    case :code.is_loaded(module) do
      false ->
        case :code.load_binary(module, String.to_charlist(path), beam) do
          {:module, ^module} -> :ok
          _ -> {:error, :artifact_mismatch}
        end

      _ ->
        if Program.fingerprint(module) == fingerprint, do: :ok, else: {:error, :module_conflict}
    end
  end
end
