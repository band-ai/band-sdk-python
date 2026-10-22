defmodule ContractLab.UnknownEngineTest do
  # README: unknown engine identifiers MUST be rejected. Every compile path refuses them
  # with the compiler's own `engine` code (contract-dsl.md), before any other field check.
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @body "type task\nstate new\n  lead -> worker: task() => done\nend done\ncontract @infinity => expired\n"

  defp file_source(format, bytes),
    do: %{
      "profile" => "band-contract/file-source/1",
      "format" => format,
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    }

  defp frontmatter(engine, name \\ "probe"),
    do:
      "---\nname: #{name}\ndescription: Probe.\nengine: #{engine}\nroles: [lead, worker]\n---\n" <>
        @body

  test "a file source with an unknown engine fails with the engine code" do
    for engine <- ["9.9.9", "0.3.0-lab.1", "latest", "0.2.0-lab.2"] do
      assert {{:error, %{code: :engine, line: 1}}, ^engine} =
               {ContractLab.compile_source(file_source("frontmatter", frontmatter(engine))),
                engine}
    end

    # Native is retired (X4 wave 2, forward-only; set N decision B1): refused before any engine check.
    native = "+SKILL probe\n+SUMMARY Probe.\n+ENGINE 9.9.9\n+ROLES lead, worker\n+BODY\n" <> @body

    assert {:error, :frontmatter_required} =
             ContractLab.compile_source(file_source("native", native))
  end

  test "the engine is refused before any other header field" do
    assert {:error, %{code: :engine, line: 1}} =
             ContractLab.compile_source(
               file_source("frontmatter", frontmatter("9.9.9", "Bad Name"))
             )

    card = %{
      "name" => "Bad Name",
      "description" => "Probe.",
      "engine" => "9.9.9",
      "roles" => ["lead", "worker"],
      "lifetime" => "infinite",
      "inactivity_timeout" => "infinite",
      "mentions" => %{"per_message" => 1, "per_session" => 10}
    }

    # Normalized legacy cards are retired (X4 wave 2): the source envelope itself is refused.
    assert {:error, :input} = ContractLab.compile_source(%{"card" => card, "body" => @body})
  end

  @tag :tmp_dir
  test "loading a file with an unknown engine fails with the engine code", %{tmp_dir: dir} do
    path = Path.join(dir, "probe.band.md")
    File.write!(path, frontmatter("9.9.9"))
    assert {:error, %{code: :engine, line: 1}} = ContractLab.load(path, :frontmatter)
  end

  test "only the current engine compiles; the retired 0.1.0-lab.1 is an unknown engine" do
    assert {:ok, %{engine: "0.2.0-lab.1"}} =
             ContractLab.compile_source(file_source("frontmatter", frontmatter("0.2.0-lab.1")))

    assert {:error, %{code: :engine, line: 1}} =
             ContractLab.compile_source(file_source("frontmatter", frontmatter("0.1.0-lab.1")))
  end
end
