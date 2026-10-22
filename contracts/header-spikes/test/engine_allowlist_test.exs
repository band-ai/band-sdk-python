defmodule HeaderSpikes.EngineAllowlistTest do
  # A caller that implements a fixed set of engines refuses any other identifier before
  # applying any engine's field rules (README: unknown engine identifiers MUST be rejected).
  use ExUnit.Case, async: true

  # The current-only engine list (X4 wave 2 retires 0.1.0-lab.1; set N decision B1).
  @engines ["0.2.0-lab.1"]

  defp frontmatter(engine, extra \\ "") do
    "---\nname: probe\ndescription: Probe.\nengine: #{engine}\nroles: [a, b]\n#{extra}---\n"
  end

  test "an unknown engine is refused before the other header fields are checked" do
    for engine <- ["9.9.9", "0.3.0-lab.1", "latest", "0.2.0-lab.2"] do
      assert {:error, {:unsupported_engine, ^engine}} =
               HeaderSpikes.discover_bytes(:frontmatter, frontmatter(engine), engines: @engines)
    end

    # A header that is also wrong in other ways still reports the engine first.
    bad = "---\nname: Bad Name\nengine: 9.9.9\n---\n"

    assert {:error, {:unsupported_engine, "9.9.9"}} =
             HeaderSpikes.discover_bytes(:frontmatter, bad, engines: @engines)

    # Native is retired (X4 wave 2): refused before any engine check.
    native = "+SKILL probe\n+ENGINE 9.9.9\n+BODY\n"

    assert {:error, :frontmatter_required} =
             HeaderSpikes.discover_bytes(:native, native, engines: @engines)
  end

  test "a missing engine is still a missing field; a retired engine is refused first" do
    assert {:error, {:missing_field, "engine"}} =
             HeaderSpikes.discover_bytes(
               :frontmatter,
               "---\nname: probe\ndescription: Probe.\nroles: [a, b]\n---\n",
               engines: @engines
             )

    assert {:error, {:unsupported_engine, "0.1.0-lab.1"}} =
             HeaderSpikes.discover_bytes(:frontmatter, frontmatter("0.1.0-lab.1"),
               engines: @engines
             )

    assert {:ok, %{card: %{engine: "0.2.0-lab.1"}}} =
             HeaderSpikes.discover_bytes(:frontmatter, frontmatter("0.2.0-lab.1"),
               engines: @engines
             )
  end

  test "without an engine list, discovery still refuses a non-current engine" do
    assert {:error, :unsupported_engine} =
             HeaderSpikes.discover_bytes(:frontmatter, frontmatter("9.9.9"))
  end
end
