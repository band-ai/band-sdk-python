defmodule Mix.Tasks.Contract.Format do
  use Mix.Task

  @shortdoc "Format .band contracts: mix contract.format [--check] [PATH...]"
  @moduledoc """
  Formats `.band` files, and the ```band fences of `.md` files, in place (`ContractLab.Format`).
  Directories are searched recursively for regular `.band` and `.md` files, skipping dot entries, `deps`, `_build`, `node_modules`, symlinks,
  FIFOs and devices, and whatever a `.band-format-ignore` in a walked directory or one of its
  ancestors lists (one pattern per line, relative to that file; `#` comments). No path means
  the current directory. A named file is always formatted, but must be a regular file: a
  symlink, FIFO or device is refused (`unsafe_contract_path`) without being read.

  A file with frontmatter must compile before formatting, and its compiled digest and
  definition (ignoring source lines) must be unchanged after; otherwise nothing is written.
  Body-only references (no frontmatter) are formatted without that check. In Markdown, a band
  fence that compiles before formatting must compile to the same digest and definition after.

  `--check` writes nothing and exits 1 when a file would change. Success prints JSON on
  stdout; failure prints one JSON error object on stderr and exits 1 (DF-09):
  `unformatted` with `files`, or the first compile or format error with its `path`.
  """
  alias ContractLab.{Canonical, Format, MixCommand}
  alias HeaderSpikes.FileSnapshot

  @skip ~w(deps _build node_modules)
  @ignore_file ".band-format-ignore"
  # The largest file source the compiler accepts (ContractLab.FileSource).
  @max_bytes 266_240
  @max_markdown_bytes 4_194_304

  @impl true
  def run(args) do
    {opts, paths} = OptionParser.parse!(args, strict: [check: :boolean])
    Mix.Task.run("app.start")
    files = paths |> default_paths() |> Enum.flat_map(&expand/1) |> Enum.uniq()
    results = Enum.map(files, &{&1, prepare(&1)})

    case for({path, {:error, reason}} <- results, do: {path, reason}) do
      [{path, reason} | _] -> fail(reason |> MixCommand.error_object() |> Map.put("path", path))
      [] -> finish(results, opts[:check])
    end
  end

  defp finish(results, true) do
    case for({path, {:change, _}} <- results, do: path) do
      [] -> Mix.shell().info(JSON.encode!(%{"checked" => length(results), "unformatted" => []}))
      files -> fail(%{"code" => "unformatted", "files" => files})
    end
  end

  defp finish(results, _) do
    changed =
      for {path, {:change, text}} <- results do
        File.write!(path, text)
        path
      end

    Mix.shell().info(
      JSON.encode!(%{"formatted" => changed, "unchanged" => length(results) - length(changed)})
    )
  end

  defp prepare(path) do
    markdown? = Path.extname(path) == ".md"

    with {:ok, source, _identity} <-
           FileSnapshot.read(path, if(markdown?, do: @max_markdown_bytes, else: @max_bytes)),
         {:ok, text} <-
           if(markdown?, do: Format.format_markdown(source), else: Format.format(source)),
         :ok <- if(markdown?, do: same_fences(source, text), else: same_behavior(source, text)) do
      if text == source, do: :same, else: {:change, text}
    end
  end

  # A band fence that compiles before formatting must compile to the same contract after it;
  # fragments and deliberately invalid examples are formatted without that check.
  defp same_fences(source, text) do
    Enum.zip(Format.band_fences(source), Format.band_fences(text))
    |> Enum.find_value(:ok, fn {before, formatted} ->
      with {:ok, _} <- compile(before),
           {:error, _} = error <- same_behavior(before, formatted),
           do: error,
           else: (_ -> nil)
    end)
  end

  defp same_behavior("---" <> _ = source, text) do
    with {:ok, before} <- compile(source),
         {:ok, formatted} <- compile(text) do
      if before.digest == formatted.digest and
           Format.comparable(before) == Format.comparable(formatted),
         do: :ok,
         else: {:error, :format_changed_definition}
    end
  end

  defp same_behavior(_, _), do: :ok

  defp compile(bytes) do
    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  defp default_paths([]), do: ["."]
  defp default_paths(paths), do: paths

  defp expand(path) do
    case File.lstat(path) do
      {:ok, %{type: :directory}} ->
        inherited = path |> Path.expand() |> ancestors() |> ignored_in()
        path |> walk(inherited) |> Enum.sort()

      _ ->
        [path]
    end
  end

  defp ancestors(dir) do
    case Path.dirname(dir) do
      ^dir -> []
      parent -> [parent | ancestors(parent)]
    end
  end

  # lstat only: a symlink is never followed, and a FIFO or device is never opened. `ignored`
  # holds absolute paths listed by the ignore files of this directory and its ancestors.
  defp walk(dir, ignored) do
    ignored = MapSet.union(ignored, ignored_in([Path.expand(dir)]))

    for name <- File.ls!(dir),
        not String.starts_with?(name, "."),
        path = Path.join(dir, name),
        not MapSet.member?(ignored, Path.expand(path)),
        reduce: [] do
      acc ->
        case File.lstat(path) do
          {:ok, %{type: :directory}} when name not in @skip ->
            walk(path, ignored) ++ acc

          {:ok, %{type: :regular}} ->
            if Path.extname(name) in [".band", ".md"], do: [path | acc], else: acc

          _ ->
            acc
        end
    end
  end

  # Only a regular ignore file counts; its patterns expand relative to its directory.
  defp ignored_in(dirs) do
    for dir <- dirs,
        {:ok, text, _} <- [FileSnapshot.read(Path.join(dir, @ignore_file), 65_536)],
        line <- String.split(text, "\n"),
        pattern = String.trim(line),
        pattern != "" and not String.starts_with?(pattern, "#"),
        match <- Path.wildcard(Path.join(dir, pattern)),
        into: MapSet.new(),
        do: Path.expand(match)
  end

  defp fail(object) do
    IO.puts(:stderr, JSON.encode!(%{"error" => object}))
    exit({:shutdown, 1})
  end
end
