defmodule ContractLab.HeaderErrorShapeTest do
  @moduledoc "Header errors take the compiler-error shape, as B1 did for engine (Ada, 10-14)."
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @head "name: shape\ndescription: Header errors take the compiler shape.\nengine: 0.2.0-lab.1\n"
  @body "contract @infinity => expired\ntype ping\nstate new\n  a -> b: ping() => done\nend done\n"

  defp compile(header) do
    bytes = "---\n" <> header <> "---\n" <> @body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  test "an unknown field is reported at its own file line with its name as detail" do
    assert {:error, %{code: :unknown_field, line: 6, detail: "inactivity-timeout"}} =
             compile(@head <> "roles: [a, b]\ninactivity-timeout: 5m\n")

    assert {:error, %{code: :unknown_field, line: 3, detail: "colour"}} =
             compile(
               "name: shape\ncolour: red\ndescription: x\nengine: 0.2.0-lab.1\nroles: [a, b]\n"
             )
  end

  test "a duplicate field is reported at the repeated line" do
    assert {:error, %{code: :duplicate_field, line: 6, detail: "roles"}} =
             compile(@head <> "roles: [a, b]\nroles: [a, b]\n")
  end

  test "a missing field has no line of its own and is reported at line 1" do
    assert {:error, %{code: :missing_field, line: 1, detail: "roles"}} = compile(@head)
  end

  test "a malformed header block is the compiler error header at line 1" do
    assert {:error, %{code: :header, line: 1}} = compile("name: [unclosed\n")
    bytes = "---\nname: shape\n"

    assert {:error, %{code: :header, line: 1}} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => bytes,
               "sha256" => Canonical.hash(bytes)
             })
  end

  test "an invalid field value is field_value at that field's line" do
    assert {:error, %{code: :field_value, line: 2, detail: "name"}} =
             compile("name: Bad Name\ndescription: x\nengine: 0.2.0-lab.1\nroles: [a, b]\n")

    assert {:error, %{code: :field_value, line: 5, detail: "encoding"}} =
             compile(@head <> "encoding: utf-16\nroles: [a, b]\n")

    assert {:error, %{code: :field_value, line: 5, detail: "roles"}} =
             compile(@head <> "roles: [a, a]\n")
  end

  test "an unknown engine is still reported first, at line 1" do
    assert {:error, %{code: :engine, line: 1}} =
             compile("name: shape\ncolour: red\ndescription: x\nengine: 9.9.9\nroles: [a, b]\n")
  end
end
