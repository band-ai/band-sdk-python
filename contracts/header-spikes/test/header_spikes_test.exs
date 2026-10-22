defmodule HeaderSpikesTest do
  use ExUnit.Case, async: true
  @moduletag :tmp_dir

  @formats [:frontmatter, :standard]
  @examples Path.expand("../examples", __DIR__)
  @description "Coordinate a requester and a worker to deliver reviewed work. Use when a task needs explicit acceptance and completion."
  @card %{
    name: "room-work",
    description: @description,
    engine: "0.2.0-lab.1",
    version: nil,
    encoding: "utf-8",
    extensions: [],
    roles: ["requester", "worker"],
    lifetime: :infinite,
    inactivity_timeout: :infinite
  }

  defp example(:frontmatter), do: Path.join(@examples, "frontmatter/room-work.md")
  defp example(:standard), do: Path.join(@examples, "standard/room-work/SKILL.md")

  test "description length remains Unicode 16 across host runtime upgrades" do
    fields = %{
      "name" => "portable",
      "description" => String.duplicate("a\u1ACF", 513),
      "engine" => "0.2.0-lab.1",
      "roles" => ["requester"]
    }

    assert {:error, :invalid_description} = HeaderSpikes.Schema.card(fields)

    assert {:ok, _} =
             HeaderSpikes.Schema.card(%{fields | "description" => String.duplicate("क्क", 1024)})
  end

  defp copy_example(format, tmp_dir) do
    source = example(format)
    target_dir = Path.join([tmp_dir, Atom.to_string(format), "room-work"])
    File.mkdir_p!(target_dir)

    for entry <- File.ls!(Path.dirname(source)) do
      File.cp_r!(Path.join(Path.dirname(source), entry), Path.join(target_dir, entry))
    end

    Path.join(target_dir, Path.basename(source))
  end

  defp change(path, old, new) do
    source = File.read!(path)
    assert String.contains?(source, old), "mutation must match an existing fixture"
    File.write!(path, String.replace(source, old, new, global: false))
  end

  defp field(:frontmatter, :engine), do: "engine: 0.2.0-lab.1\n"
  defp field(:standard, :engine), do: "  band-engine: 0.2.0-lab.1\n"

  for format <- @formats do
    @format format

    test "#{format}: normalizes the same complete discovery card", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      assert {:ok, result} = HeaderSpikes.discover(@format, path)
      assert result.card == @card
      assert result.bytes_read == result.body_offset
      assert result.bytes_read <= 4096
      assert result.contract_bytes_read == 0
    end

    test "#{format}: a 2 MiB body does not change discovery reads", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      assert {:ok, small} = HeaderSpikes.discover(@format, path)
      File.write!(path, :binary.copy("z", 2 * 1024 * 1024), [:append])
      assert {:ok, large} = HeaderSpikes.discover(@format, path)
      assert large.card == small.card
      assert large.bytes_read == small.bytes_read
    end

    test "#{format}: caller cannot silently exceed the header bound", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      assert {:ok, result} = HeaderSpikes.discover(@format, path)
      assert {:ok, _} = HeaderSpikes.discover(@format, path, max_bytes: result.bytes_read)

      assert {:error, :header_too_large} =
               HeaderSpikes.discover(@format, path, max_bytes: result.bytes_read - 1)
    end

    test "#{format}: rejects a missing engine", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      change(path, field(@format, :engine), "")
      assert {:error, {:missing_field, "engine"}} = HeaderSpikes.discover(@format, path)
    end

    test "#{format}: rejects unpinned engine versions", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      change(path, "0.2.0-lab.1", "latest")
      assert {:error, :invalid_engine} = HeaderSpikes.discover(@format, path)
    end

    test "#{format}: rejects SemVer with a leading zero", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      change(path, "0.2.0-lab.1", "01.0.0")
      assert {:error, :invalid_engine} = HeaderSpikes.discover(@format, path)
    end

    test "#{format}: rejects duplicate engine declarations", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      line = field(@format, :engine)
      change(path, line, line <> line)
      assert {:error, {:duplicate_field, _}} = HeaderSpikes.discover(@format, path)
    end

    test "#{format}: rejects duplicate roles", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      change(path, "requester, worker", "requester, requester")
      assert {:error, :invalid_roles} = HeaderSpikes.discover(@format, path)
    end

    test "#{format}: rejects a changed header between discovery and loading", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      assert {:ok, discovery} = HeaderSpikes.discover(@format, path)
      change(path, "description: " <> @description, "description: Changed description.")
      assert {:error, :header_changed} = HeaderSpikes.load_contract(discovery)
    end

    test "#{format}: body loading enforces its bound before returning source", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      body = :binary.copy("x", 262_145)

      if @format == :standard do
        old = File.read!(Path.join(Path.dirname(path), "references/work.band"))
        digest = fn bytes -> :crypto.hash(:sha256, bytes) |> Base.encode16(case: :lower) end
        change(path, digest.(old), digest.(body))
        File.write!(Path.join(Path.dirname(path), "references/work.band"), body)
      else
        {:ok, d} = HeaderSpikes.discover(@format, path)
        File.write!(path, d.prefix <> body)
      end

      {:ok, d} = HeaderSpikes.discover(@format, path)
      assert {:error, :source_size} = HeaderSpikes.load_contract(d)
    end

    test "#{format}: loading rejects a replaced package entry despite an identical header", %{
      tmp_dir: tmp
    } do
      path = copy_example(@format, tmp)
      {:ok, d} = HeaderSpikes.discover(@format, path)
      File.write!(path <> ".replacement", File.read!(path))
      File.rename!(path <> ".replacement", path)
      assert {:error, :source_changed} = HeaderSpikes.load_contract(d)
    end
  end

  @tag :tmp_dir
  test "both current examples load exactly the same contract body" do
    bodies =
      for format <- @formats do
        assert {:ok, discovery} = HeaderSpikes.discover(format, example(format))
        assert {:ok, body} = HeaderSpikes.load_contract(discovery)
        body
      end

    assert length(Enum.uniq(bodies)) == 1
    assert String.contains?(hd(bodies), "anystate")
  end

  test "a missing delimiter cannot trigger an unbounded read", %{tmp_dir: tmp} do
    for {format, prefix} <- [
          frontmatter: "---\n",
          standard: "---\n"
        ] do
      path = Path.join(tmp, Atom.to_string(format))
      File.write!(path, prefix <> :binary.copy("x", 2 * 1024 * 1024))
      assert {:error, :header_too_large} = HeaderSpikes.discover(format, path)
    end
  end

  test "a short incomplete header reports an unterminated header", %{tmp_dir: tmp} do
    path = Path.join(tmp, "incomplete")
    File.write!(path, "---\nname: room-work\n")
    assert {:error, :unterminated_header} = HeaderSpikes.discover(:frontmatter, path)
  end

  for format <- [:frontmatter, :standard] do
    @format format

    test "#{format}: handles a folded YAML description", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)

      change(
        path,
        "description: " <> @description,
        "description: >-\n  Coordinate a requester and a worker to deliver reviewed work.\n  Use when a task needs explicit acceptance and completion."
      )

      assert {:ok, %{card: @card}} = HeaderSpikes.discover(@format, path)
    end

    test "#{format}: rejects YAML aliases before construction", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      change(path, "name: room-work", "name: &alias room-work")
      assert {:error, :unsupported_yaml_feature} = HeaderSpikes.discover(@format, path)
    end

    test "#{format}: rejects malformed YAML", %{tmp_dir: tmp} do
      path = copy_example(@format, tmp)
      change(path, "name: room-work", "name: [broken")
      assert {:error, :invalid_yaml} = HeaderSpikes.discover(@format, path)
    end
  end

  test "frontmatter: a misspelled field is not silently ignored", %{tmp_dir: tmp} do
    path = copy_example(:frontmatter, tmp)
    change(path, "engine:", "engnie:")
    assert {:error, {:unknown_field, "engnie"}} = HeaderSpikes.discover(:frontmatter, path)
  end

  test "YAML profile rejects explicit tags", %{tmp_dir: tmp} do
    path = copy_example(:frontmatter, tmp)
    change(path, "name: room-work", "name: !!str room-work")
    assert {:error, :unsupported_yaml_feature} = HeaderSpikes.discover(:frontmatter, path)
  end

  test "standard: metadata values must be strings", %{tmp_dir: tmp} do
    path = copy_example(:standard, tmp)
    change(path, "band-engine: 0.2.0-lab.1", "band-engine: 12")
    assert {:error, :metadata_must_be_strings} = HeaderSpikes.discover(:standard, path)
  end

  test "standard: discover does not open the referenced contract", %{tmp_dir: tmp} do
    path = copy_example(:standard, tmp)
    File.rm!(Path.join(Path.dirname(path), "references/work.band"))
    assert {:ok, %{contract_bytes_read: 0} = result} = HeaderSpikes.discover(:standard, path)
    assert {:error, {:contract_io, :enoent}} = HeaderSpikes.load_contract(result)
  end

  test "standard: corrupt referenced content fails its pinned digest", %{tmp_dir: tmp} do
    path = copy_example(:standard, tmp)
    assert {:ok, result} = HeaderSpikes.discover(:standard, path)
    File.write!(Path.join(Path.dirname(path), "references/work.band"), "changed")
    assert {:error, :digest_mismatch} = HeaderSpikes.load_contract(result)
  end

  test "standard: traversal cannot escape the package", %{tmp_dir: tmp} do
    path = copy_example(:standard, tmp)
    change(path, "references/work.band", "../outside.band")
    assert {:error, :invalid_contract_path} = HeaderSpikes.discover(:standard, path)
  end

  test "standard: a symlink cannot redirect contract loading", %{tmp_dir: tmp} do
    path = copy_example(:standard, tmp)
    ref = Path.join(Path.dirname(path), "references/work.band")
    outside = Path.join(tmp, "outside.band")
    File.cp!(ref, outside)
    File.rm!(ref)
    File.ln_s!(outside, ref)
    assert {:ok, discovery} = HeaderSpikes.discover(:standard, path)
    assert {:error, :unsafe_contract_path} = HeaderSpikes.load_contract(discovery)
  end

  test "standard: an unknown required extension is rejected", %{tmp_dir: tmp} do
    path = copy_example(:standard, tmp)
    change(path, "metadata:\n", "metadata:\n  band-required-feature: future-thing\n")

    assert {:error, {:unknown_field, "band-required-feature"}} =
             HeaderSpikes.discover(:standard, path)
  end

  test "standard: name must match the package directory", %{tmp_dir: tmp} do
    path = copy_example(:standard, tmp)
    change(path, "name: room-work", "name: another-name")
    assert {:error, :name_directory_mismatch} = HeaderSpikes.discover(:standard, path)
  end
end
