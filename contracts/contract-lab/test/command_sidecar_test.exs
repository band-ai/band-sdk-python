defmodule ContractLab.CommandSidecarTest do
  use ExUnit.Case, async: false
  alias ContractLab.{CommandSidecar, Program}

  setup do
    dir = Path.join(System.tmp_dir!(), "contract-generator-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dir)
    on_exit(fn -> File.rm_rf!(dir) end)
    generator = Path.join(dir, "generate.py")
    validator = Path.join(dir, "validate.exs")

    source = ~S"""
    defmodule MODULE do
      @behaviour ContractLab.Program
      def state_version, do: "1"
      def init(_view), do: {:ok, %{"total" => 0}}
      def handle(event, state) do
        total = state["total"] + event["payload"]["by"]
        {:ok, %{"total" => total}, [%{"type" => "room", "kind" => "total", "payload" => %{"value" => total}}]}
      end
    end
    """

    File.write!(
      generator,
      "import json,sys\nbundle=json.load(open(sys.argv[1]))\n" <>
        "source=" <>
        JSON.encode!(source) <>
        "\n" <>
        "open(sys.argv[2], 'w').write(source.replace('MODULE', bundle['candidate_module']))\n"
    )

    File.write!(validator, ~S'''
    [{module, _}] = Code.compile_file(hd(System.argv()))
    {:ok, state} = module.init(%{})
    {:ok, %{"total" => 13}, [_]} = module.handle(%{"payload" => %{"by" => 13}}, state)
    ''')

    opts = [
      workspace: Path.join(dir, "artifacts"),
      executable: System.find_executable("python3"),
      args: [generator, "{bundle}", "{output}"],
      validate:
        {System.find_executable("elixir"),
         ["-pa", Path.expand("_build/test/lib/contract_lab/ebin"), validator, "{candidate}"]},
      timeout_ms: 5000
    ]

    {:ok, dir: dir, generator: generator, opts: opts}
  end

  test "a command writes and validates a real program artifact which can be loaded again", ctx do
    sidecar = CommandSidecar.new(ctx.opts)
    assert {:ok, program} = sidecar.(%{"fault" => %{"message" => "Counter crashed"}})
    assert {:ok, state} = program.init(%{})
    assert {:ok, %{"total" => 13}, [_]} = program.handle(%{"payload" => %{"by" => 13}}, state)
    assert {:ok, [^program]} = CommandSidecar.load_programs(ctx.opts[:workspace])
    [manifest] = Path.wildcard(Path.join(ctx.opts[:workspace], "releases/*/manifest.json"))
    {:ok, receipt} = JSON.decode(File.read!(manifest))
    assert receipt["program"] == Program.fingerprint(program)
    assert receipt["validation_exit"] == 0
    assert File.exists?(Path.join(Path.dirname(manifest), "candidate.ex"))
    assert File.exists?(Path.join(Path.dirname(manifest), "program.beam"))
  end

  test "a failed validator leaves the program unactivated", ctx do
    opts =
      Keyword.put(
        ctx.opts,
        :validate,
        {System.find_executable("python3"), ["-c", "raise SystemExit(7)"]}
      )

    assert {:error, {:validation_failed, 7, _}} = CommandSidecar.new(opts).(%{})
    assert Path.wildcard(Path.join(ctx.opts[:workspace], "releases/*/manifest.json")) == []
  end

  test "generator failures and timeouts are reported", ctx do
    opts = Keyword.merge(ctx.opts, args: ["-c", "raise SystemExit(9)"], timeout_ms: 100)
    assert {:error, {:generation_failed, 9, _}} = CommandSidecar.new(opts).(%{})
    opts = Keyword.put(opts, :args, ["-c", "import time; time.sleep(10)"])
    start = System.monotonic_time(:millisecond)
    assert {:error, {:generation_failed, 124, _}} = CommandSidecar.new(opts).(%{})
    assert System.monotonic_time(:millisecond) - start < 2000
  end

  test "a generated file cannot redefine an existing program module", ctx do
    File.write!(ctx.generator, ~S'''
    import sys
    open(sys.argv[2],"w").write('defmodule ContractLab.Program do\n def broken, do: true\nend\n')
    ''')

    assert {:error, :candidate_module_mismatch} = CommandSidecar.new(ctx.opts).(%{})
    assert function_exported?(Program, :fingerprint, 1)
  end

  test "release bytes are checked before reloading a program", ctx do
    assert {:ok, _} = CommandSidecar.new(ctx.opts).(%{})
    [beam] = Path.wildcard(Path.join(ctx.opts[:workspace], "releases/*/program.beam"))
    File.write!(beam, "damaged")
    assert {:error, :artifact_mismatch} = CommandSidecar.load_programs(ctx.opts[:workspace])
  end

  # L2: load_programs/1 loads a release's BEAM into this VM, so only the host user may
  # write the workspace or a release.
  test "the workspace and its releases are private to the host user", ctx do
    assert {:ok, _} = CommandSidecar.new(ctx.opts).(%{})
    [manifest] = Path.wildcard(Path.join(ctx.opts[:workspace], "releases/*/manifest.json"))
    release = Path.dirname(manifest)

    for dir <- [ctx.opts[:workspace], Path.join(ctx.opts[:workspace], "releases"), release] do
      assert Bitwise.band(File.stat!(dir).mode, 0o077) == 0, dir
    end

    for name <- ["manifest.json", "candidate.ex", "program.beam"] do
      assert Bitwise.band(File.stat!(Path.join(release, name)).mode, 0o077) == 0, name
    end
  end

  test "a release that group or others can write is not loaded", ctx do
    assert {:ok, program} = CommandSidecar.new(ctx.opts).(%{})
    [manifest] = Path.wildcard(Path.join(ctx.opts[:workspace], "releases/*/manifest.json"))
    releases = Path.join(ctx.opts[:workspace], "releases")

    for {path, open, private} <- [
          {manifest, 0o666, 0o600},
          {Path.dirname(manifest), 0o770, 0o700},
          {releases, 0o777, 0o700},
          {ctx.opts[:workspace], 0o722, 0o700}
        ] do
      File.chmod!(path, open)
      assert {:error, :artifact_permissions} = CommandSidecar.load_programs(ctx.opts[:workspace])
      File.chmod!(path, private)
    end

    assert {:ok, [^program]} = CommandSidecar.load_programs(ctx.opts[:workspace])
  end

  test "without /proc/self the owner check fails closed instead of raising", ctx do
    assert {:ok, _} = CommandSidecar.new(ctx.opts).(%{})
    # Off Linux there is no /proc/self to learn this OS user's uid from.
    Application.put_env(:contract_lab, :proc_self, Path.join(ctx.dir, "no-proc-self"))
    on_exit(fn -> Application.delete_env(:contract_lab, :proc_self) end)

    assert {:error, :artifact_permissions} = CommandSidecar.load_programs(ctx.opts[:workspace])
    assert {:error, :artifact_permissions} = CommandSidecar.new(ctx.opts).(%{})
  end

  test "command output capture is bounded", ctx do
    File.write!(ctx.generator, File.read!(ctx.generator) <> "\nprint('x'*100000)\n")
    assert {:ok, _} = CommandSidecar.new(ctx.opts).(%{})
    [log] = Path.wildcard(Path.join(ctx.opts[:workspace], "requests/*/generation.log"))
    assert File.stat!(log).size <= 65_600
  end
end
