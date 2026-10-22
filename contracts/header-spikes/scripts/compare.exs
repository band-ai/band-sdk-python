examples = [
  {"B", :frontmatter, "examples/frontmatter/room-work.md"},
  {"C", :standard, "examples/standard/room-work/SKILL.md"}
]

IO.puts("Measured at " <> DateTime.to_iso8601(DateTime.utc_now()))

IO.puts(
  "option\tformat\theader_bytes\tsource_bytes\tcontract_bytes_read_at_discovery\tbody_bytes_after_load"
)

cards =
  for {option, format, path} <- examples do
    {:ok, discovery} = HeaderSpikes.discover(format, path)
    {:ok, body} = HeaderSpikes.load_contract(discovery)
    source_bytes = File.stat!(path).size

    IO.puts(
      Enum.join(
        [
          option,
          format,
          discovery.bytes_read,
          source_bytes,
          discovery.contract_bytes_read,
          byte_size(body)
        ],
        "\t"
      )
    )

    discovery.card
  end

true = length(Enum.uniq(cards)) == 1
IO.puts("All normalized discovery cards are equal.")
IO.inspect(hd(cards), label: "Normalized card", pretty: true, limit: :infinity)
