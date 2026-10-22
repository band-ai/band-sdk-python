defmodule ContractLab.HeaderFieldLineTest do
  @moduledoc "Sofia set P P-L2: a header error names the bare key's line before any band-<field> key."
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @body "contract @infinity => expired\ntype ping\nstate new\n  a -> b: ping() => done\nend done\n"

  defp standard(extra, roles \\ ~s("a,b")) do
    digest = Base.encode16(:crypto.hash(:sha256, @body), case: :lower)

    entry =
      "---\nname: pkg\ndescription: d\nmetadata:\n  band-engine: \"0.2.0-lab.1\"\n" <>
        "  band-roles: #{roles}\n  band-contract: \"protocol.band\"\n" <>
        "  band-contract-sha256: \"#{digest}\"\n#{extra}---\nPackage guidance.\n"

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "standard",
      "content" => entry,
      "sha256" => Canonical.hash(entry),
      "body" => @body,
      "package_name" => "pkg"
    })
  end

  test "a stray top-level key is reported at its own line, not at its band- metadata twin" do
    assert {:error, %{code: :unknown_field, line: 9, detail: "roles"}} =
             standard("roles: [a, b]\n")
  end

  defp package(meta, top \\ "") do
    digest = Base.encode16(:crypto.hash(:sha256, @body), case: :lower)

    entry =
      "---\nname: pkg\ndescription: d\nmetadata:\n" <>
        meta <>
        "  band-contract: \"protocol.band\"\n  band-contract-sha256: \"#{digest}\"\n" <>
        top <> "---\nPackage guidance.\n"

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "standard",
      "content" => entry,
      "sha256" => Canonical.hash(entry),
      "body" => @body,
      "package_name" => "pkg"
    })
  end

  # Sofia set Q Q-L3: bare key, then band-<field>, then any nested key of that name.
  test "a band- key wins over an unrelated nested key of the bare name" do
    meta =
      "  version: \"1.0\"\n  band-engine: \"0.2.0-lab.1\"\n  band-roles: \"a,b\"\n" <>
        "  band-version: \"bad\"\n"

    assert {:error, %{code: :field_value, line: 8, detail: "version"}} = package(meta)
  end

  # Omar set Q Q-L6: the nested tier is metadata only (depth 2), never another mapping.
  test "a nested key under another mapping never stands in for the metadata key" do
    digest = Base.encode16(:crypto.hash(:sha256, @body), case: :lower)

    entry =
      "---\nname: pkg\ndescription: d\ncompatibility:\n  band-colour: x\nmetadata:\n" <>
        "  band-engine: \"0.2.0-lab.1\"\n  band-roles: \"a,b\"\n  band-colour: y\n" <>
        "  band-contract: \"protocol.band\"\n  band-contract-sha256: \"#{digest}\"\n" <>
        "---\nPackage guidance.\n"

    assert {:error, %{code: :unknown_field, line: 9, detail: "band-colour"}} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "standard",
               "content" => entry,
               "sha256" => Canonical.hash(entry),
               "body" => @body,
               "package_name" => "pkg"
             })
  end

  test "a deeper key outside metadata never supplies the line either" do
    digest = Base.encode16(:crypto.hash(:sha256, @body), case: :lower)

    entry =
      "---\nname: pkg\ndescription: d\ncompatibility:\n  x:\n    band-colour: z\nmetadata:\n" <>
        "  band-engine: \"0.2.0-lab.1\"\n  band-roles: \"a,b\"\n  band-colour: y\n" <>
        "  band-contract: \"protocol.band\"\n  band-contract-sha256: \"#{digest}\"\n" <>
        "---\nPackage guidance.\n"

    assert {:error, %{code: :unknown_field, line: 10, detail: "band-colour"}} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "standard",
               "content" => entry,
               "sha256" => Canonical.hash(entry),
               "body" => @body,
               "package_name" => "pkg"
             })
  end

  test "a duplicate is the first repeat within the same mapping" do
    meta =
      "  description: x\n  description: y\n  band-engine: \"0.2.0-lab.1\"\n" <>
        "  band-roles: \"a,b\"\n"

    assert {:error, %{code: :duplicate_field, line: 6, detail: "description"}} = package(meta)
  end

  defp frontmatter(header) do
    entry = "---\n" <> header <> "---\n" <> @body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => entry,
      "sha256" => Canonical.hash(entry)
    })
  end

  # Omar set P P-10: the line is the YAML key node's position, never a text match.
  test "a key-like line inside a block scalar is not the field's line" do
    assert {:error, %{code: :field_value, line: 6, detail: "roles"}} =
             frontmatter(
               "name: shape\ndescription: |\n  roles: no\nengine: 0.2.0-lab.1\nroles: [a, a]\n"
             )
  end

  test "a field repeated three times is reported at its first repeat, where parsing stops" do
    assert {:error, %{code: :duplicate_field, line: 7, detail: "colour"}} =
             frontmatter(
               "name: shape\ndescription: d\nengine: 0.2.0-lab.1\nroles: [a, b]\n" <>
                 "colour: a\ncolour: b\ncolour: c\n"
             )
  end

  test "a bare CR is a YAML line break, and a quoted key keeps its node line" do
    assert {:error, %{code: :unknown_field, line: 6, detail: "colour"}} =
             frontmatter(
               "name: shape\ndescription: d\nengine: 0.2.0-lab.1\nroles: [a, b]\rcolour: red\n"
             )

    assert {:error, %{code: :unknown_field, line: 6, detail: "colour"}} =
             frontmatter(
               "name: shape\ndescription: d\nengine: 0.2.0-lab.1\nroles: [a, b]\n\"colour\" : red\n"
             )
  end

  # Sofia set Q Q-L4: one trailing CR belongs to a CRLF break; any other CR is its own break.
  test "a line ending in CR CR LF counts two YAML breaks" do
    assert {:error, %{code: :unknown_field, line: 7, detail: "colour"}} =
             frontmatter(
               "name: shape\ndescription: d\nengine: 0.2.0-lab.1\nroles: [a, b]\r\r\ncolour: red\n"
             )
  end

  test "a delimiter line with two trailing CRs is not a delimiter" do
    entry = "---\r\r\nname: shape\n---\n" <> @body

    assert {:error, %{code: :header, line: 1}} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => entry,
               "sha256" => Canonical.hash(entry)
             })
  end

  # Sofia set Q Q-L5: a null engine is an unsupported engine, in either order with a repeat.
  test "a null engine is refused as engine, before or after an unsupported repeat" do
    for engines <- ["engine: ~\nengine: 9.9.9\n", "engine: 9.9.9\nengine: ~\n", "engine: ~\n"] do
      assert {:error, %{code: :engine, line: 1}} =
               frontmatter("name: shape\ndescription: d\n" <> engines <> "roles: [a, b]\n")
    end
  end

  # Omar set P P-11: the engine is checked first whatever its type, and across repeats.
  test "a non-string or repeated unsupported engine is reported before any other field" do
    assert {:error, %{code: :engine, line: 1}} =
             frontmatter("name: shape\ndescription: d\nengine: 1\nroles: [a, b]\ncolour: red\n")

    assert {:error, %{code: :engine, line: 1}} =
             frontmatter(
               "name: shape\ndescription: d\nengine: 9.9.9\nengine: 9.9.9\nroles: [a, b]\n"
             )

    assert {:error, %{code: :duplicate_field, line: 5, detail: "engine"}} =
             frontmatter(
               "name: shape\ndescription: d\nengine: 0.2.0-lab.1\nengine: 0.2.0-lab.1\nroles: [a, b]\n"
             )
  end

  test "with no bare key, a band- metadata value error falls back to that key's line" do
    assert {:error, %{code: :field_value, line: 6, detail: "roles"}} =
             standard("", ~s("A,B"))
  end
end
