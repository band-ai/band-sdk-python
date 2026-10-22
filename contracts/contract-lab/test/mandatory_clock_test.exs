defmodule ContractLab.MandatoryClockTest do
  use ExUnit.Case, async: true
  alias ContractLab.Canonical

  @body """
  type request
  type review
    request: ref(request)
  state new
    lead -> reviewer: request() awaits reviewing
      => review() & collected
       | @5m & stalled
  end collected
  end stalled
  """

  defp compile(clock, extra \\ "") do
    bytes =
      """
      ---
      name: mandatory-clock
      description: Every contract declares its whole-run clock.
      engine: 0.2.0-lab.1
      roles: [lead, reviewer]
      #{extra}---
      #{clock}
      """ <> @body

    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  test "a contract without a contract clock is a compile error" do
    assert {:error, %{code: :contract_clock_required, line: 1, detail: detail}} = compile("")
    assert detail =~ "contract @infinity => expired"
  end

  test "a bare contract metadata line does not declare a clock" do
    assert {:error, %{code: :contract_clock_required, line: 1}} = compile("contract")
  end

  test "a malformed contract clock reports the authored line, not the internal form" do
    for line <- [
          "contract @infinity",
          "contract @infinity => expired extra",
          "contract  @30m => expired"
        ] do
      assert {:error, %{code: :syntax, detail: detail}} = compile(line)
      assert detail == line
    end
  end

  test "contract @infinity => expired is the explicit opt-out" do
    assert {:ok, c} = compile("contract @infinity => expired")
    refute Map.has_key?(c, :timeout)
    refute Map.has_key?(c, :renewal_profile)
    assert c.card.lifetime == :infinite

    assert {:error, %{code: :unknown_state}} = compile("contract @infinity => nowhere")
  end

  test "a finite body clock satisfies the rule" do
    assert {:ok, c} = compile("contract @30m => expired")
    assert c.timeout.after_ms == 1_800_000
  end

  test "the header max-duration and lifetime fields are removed" do
    for {field, value} <- [
          {"max-duration", "30m"},
          {"max-duration", "infinite"},
          {"lifetime", "30m"},
          {"lifetime", "infinite"}
        ],
        clock <- ["contract @30m => expired", "contract @infinity => expired", ""] do
      assert {:error, %{code: :unknown_field, line: 6, detail: ^field}} =
               compile(clock, "#{field}: #{value}\n")
    end
  end

  test "the flagship corpus examples teach a real one-hour clock" do
    for name <- ~w(one-review two-reviewers sigil-review) do
      path = Path.expand("../contracts/v2/#{name}.band", __DIR__)
      assert {:ok, c} = ContractLab.load(path, :frontmatter)
      assert %{after_ms: 3_600_000, outcome: "expired"} = Map.get(c, :timeout), name
    end
  end

  test "other validation errors are still reported first" do
    bytes = frontmatter() <> "type a\nstate new\n  lead -> reviewer: a() => nowhere\n"

    assert {:error, %{code: :unknown_state, line: 3}} =
             ContractLab.compile_source(%{
               "profile" => "band-contract/file-source/1",
               "format" => "frontmatter",
               "content" => bytes,
               "sha256" => Canonical.hash(bytes)
             })
  end

  defp frontmatter,
    do: """
    ---
    name: mandatory-clock
    description: Every contract declares its whole-run clock.
    engine: 0.2.0-lab.1
    roles: [lead, reviewer]
    ---
    """
end
