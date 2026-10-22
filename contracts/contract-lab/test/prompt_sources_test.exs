defmodule ContractLab.PromptSourcesTest do
  @moduledoc """
  Eric, 10-15: every contract example carries an LLM prompt on the contract and on every
  role, message type and state, always as a block (a line of three double quotes, the
  prompt, a closing line of three double quotes). No single-line prompts and no sigils.
  The hash-pinned conformance sources and their contract-lab copies are checked here.
  """
  use ExUnit.Case, async: true

  @names ~w(agent-control all-participants assignment bounded-review explicit-infinity
            first-responder history-review history-review-v2 hybrid-assistant program-counter
            progress-events room-events selected-deadlines team-review)

  @dirs %{
    "sources-v2" => Path.expand("../../spec/conformance/sources-v2", __DIR__),
    "contracts" => Path.expand("../contracts", __DIR__)
  }

  # The declarations that need a prompt: the contract line, then role, type, state and end
  # sections, each at the start of a line.
  @declaration ~r/^(contract|role|type|state|end) /
  # A one-line documentation string, anywhere (fields and rules too).
  @single_line ~r/^\s*"(?!"")[^"].*"\s*$/
  # A sigil used for a prompt.
  @sigil ~r/~[A-Za-z]+"/

  defp block_before?(lines, index) do
    previous = lines |> Enum.take(index) |> Enum.reverse() |> Enum.find(&(String.trim(&1) != ""))
    previous == ~s(""")
  end

  for {label, dir} <- @dirs, name <- @names do
    @tag dir: label
    test "#{label}/#{name}.band has a prompt block on the contract, every role, type and state" do
      path = Path.join(unquote(dir), unquote(name) <> ".band")
      source = File.read!(path)
      lines = String.split(source, "\n")

      missing =
        for {line, i} <- Enum.with_index(lines),
            Regex.match?(@declaration, line),
            not block_before?(lines, i),
            do: "line #{i + 1}: #{line}"

      assert missing == [], "#{path}: no prompt block before\n" <> Enum.join(missing, "\n")

      singles = for line <- lines, Regex.match?(@single_line, line), do: line
      assert singles == [], "#{path}: single-line prompts\n" <> Enum.join(singles, "\n")
      refute Regex.match?(@sigil, source), "#{path}: a sigil prompt"

      # Every role in the front matter needs its own section and prompt, and the compiled
      # contract must carry each prompt.
      assert {:ok, c} = ContractLab.load(path, :frontmatter)
      declared_ends = for [_, n] <- Regex.scan(~r/^end (\S+)/m, source), do: n

      docs =
        [{"contract", c.meta}] ++
          Enum.map(c.roles, &{"role " <> &1, c.role_metadata[&1].meta}) ++
          Enum.map(c.types, fn {n, t} -> {"type " <> n, t.meta} end) ++
          Enum.map(c.states, fn {n, s} -> {"state " <> n, s.meta} end) ++
          Enum.map(declared_ends, &{"end " <> &1, c.terminals[&1].meta})

      undocumented = for {what, meta} <- docs, not doc?(meta), do: what
      assert undocumented == [], "#{path}: no compiled prompt on #{Enum.join(undocumented, ", ")}"
    end
  end

  test "the contract-lab copies are byte copies of the pinned sources" do
    for name <- @names do
      assert File.read!(Path.join(@dirs["contracts"], name <> ".band")) ==
               File.read!(Path.join(@dirs["sources-v2"], name <> ".band"))
    end
  end

  defp doc?(%{doc: doc}) when is_binary(doc), do: String.trim(doc) != ""
  defp doc?(_), do: false
end
