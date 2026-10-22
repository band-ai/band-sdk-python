defmodule ContractLab.ContractFormatTest do
  @moduledoc """
  The .band formatter (`mix contract.format`): 2-space indent, one space around `->`, `=>`
  and an outcome `|`, `name: value` colons, a blank line between top-level declarations, at
  most one blank line, no trailing spaces, a final newline. Comments, notes and
  documentation keep their text and placement; sigils, patterns and selectors are untouched.
  """
  use ExUnit.Case, async: false
  import ExUnit.CaptureIO
  alias ContractLab.{Canonical, Format}

  @root Path.expand("../..", __DIR__)

  @messy """
  ---
  name: fmt-sample
  description: A messy contract for the formatter.
  engine: 0.2.0-lab.1
  roles: [lead, a, b]
  ---


  %% Whole-run clock.\s\s\s
  contract @1h => expired
  "Ask both reviewers."\s\s
  role lead
  type request
      label: text(bytes:1..32,graphemes:1..10, regex: ~r/[A-Z][A-Z0-9 :|_-]*/)
  type review
      \"\"\"
      The decision.
        Indented line.
      \"\"\"
  \tdecision : enum(approve|revise)
  type note
  state new
  lead -> ~w[a  b]: request() as task => reviewing
      a -> lead: note()=>reviewing



  state reviewing
        /* An author note
           across lines. */
    {a,b} -> lead: review() each @5m => collected | stalled
  end collected
  end stalled
  """

  @formatted """
  ---
  name: fmt-sample
  description: A messy contract for the formatter.
  engine: 0.2.0-lab.1
  roles: [lead, a, b]
  ---
  %% Whole-run clock.
  contract @1h => expired

  \"\"\"
  Ask both reviewers.
  \"\"\"
  role lead

  type request
    label: text(bytes: 1..32,graphemes: 1..10, regex: ~r/[A-Z][A-Z0-9 :|_-]*/)

  type review
    \"\"\"
    The decision.
      Indented line.
    \"\"\"
    decision: enum(approve|revise)

  type note

  state new
    lead -> ~w[a  b]: request() as task => reviewing
    a -> lead: note() => reviewing

  state reviewing
    /* An author note
       across lines. */
    {a,b} -> lead: review() each @5m => collected | stalled

  end collected

  end stalled
  """

  defp compile(bytes) do
    ContractLab.compile_source(%{
      "profile" => "band-contract/file-source/1",
      "format" => "frontmatter",
      "content" => bytes,
      "sha256" => Canonical.hash(bytes)
    })
  end

  # The compiled object without source positions or the source digest: what formatting must keep.
  defp shape(value) when is_map(value) and not is_struct(value),
    do: value |> Map.drop([:line, :source_digest]) |> Map.new(fn {k, v} -> {k, shape(v)} end)

  defp shape(value) when is_list(value), do: Enum.map(value, &shape/1)
  defp shape(value), do: value

  test "formats layout and spacing, keeping comments, notes, docs, sigils and selectors" do
    assert {:ok, @formatted} = Format.format(@messy)
    assert {:ok, @formatted} = Format.format(@formatted)
    assert {:ok, before} = compile(@messy)
    assert {:ok, after_format} = compile(@formatted)
    assert before.digest == after_format.digest
    assert shape(before) == shape(after_format)
  end

  test "collect options sit under their collect; events, triggers and room rules under their scope" do
    messy = """
    contract @30m => expired
    type reviewer: agent
    reviewers: list(reviewer)
    type review
    type ping
    state reviewing
    event reviewers -> lead: ping()
    collect reports from reviewers -> lead @5m => done
    reply review()
    anystate
    lead -> reviewers: ping()
    end done
    """

    assert {:ok, out} = Format.format(messy)

    assert out == """
           contract @30m => expired

           type reviewer: agent

           reviewers: list(reviewer)

           type review

           type ping

           state reviewing
             event reviewers -> lead: ping()
             collect reports from reviewers -> lead @5m => done
               reply review()

           anystate
             lead -> reviewers: ping()

           end done
           """
  end

  test "syntax U: => at 4 spaces, each | at 5 with the lines before it, through blank lines" do
    messy = """
    contract @30m => expired
    state new
    lead -> worker: task() awaits working
    =>done()&finished
    %% Comments, prompts and blank lines keep the alternatives in place.

    "The failure."
    |failed()   &broken
    |@5m&stalled
    reply -> lead: ping() => working
    timeout -> lead: ping() => working
    collect reports from workers -> lead @5m => finished
    reply report()
    timeout @5m => stalled
    lead -> worker: k() @1h => working | stalled
    end finished
    """

    assert {:ok, out} = Format.format(messy)

    assert out == """
           contract @30m => expired

           state new
             lead -> worker: task() awaits working
               => done() & finished
                %% Comments, prompts and blank lines keep the alternatives in place.

                \"\"\"
                The failure.
                \"\"\"
                | failed() & broken
                | @5m & stalled
             reply -> lead: ping() => working
             timeout -> lead: ping() => working
             collect reports from workers -> lead @5m => finished
               reply report()
             timeout @5m => stalled
             lead -> worker: k() @1h => working | stalled

           end finished
           """

    assert {:ok, ^out} = Format.format(out)
  end

  describe "syntax U vectors shared with the Python formatter" do
    @replyvectors Path.join(__DIR__, "fixtures/reply_format_vectors.json")
                   |> File.read!()
                   |> JSON.decode!()
                   |> Map.fetch!("vectors")

    for %{"name" => name, "input" => input, "expected" => expected} <- @replyvectors do
      @input input
      @expected expected
      test "formats to the canonical bytes, and formatting twice equals once: #{name}" do
        assert {:ok, once} = Format.format(@input)
        assert {:ok, ^once} = Format.format(once)
        assert once == @expected
        assert {:ok, @expected} == Format.format(@expected)
      end
    end
  end

  test "trailing blank lines, missing final newline and runs of blank lines are normalized" do
    assert {:ok, "contract @1h => expired\n\nend done\n"} =
             Format.format("\n\ncontract @1h => expired   \n\n\n\nend done")

    assert {:ok, "contract @1h => expired\n"} = Format.format("contract @1h => expired\n\n\n")
  end

  test "a body without frontmatter is formatted as a body" do
    assert {:ok, "contract @infinity => expired\n\ntype offer\n  task: text(600)\n"} =
             Format.format("contract @infinity => expired\ntype offer\n task: text(600)\n")
  end

  test "a multiline doc that cannot shift uniformly is kept byte for byte" do
    # The doc sits at 4 and belongs at 2, but a content line has only 1 space to give.
    body =
      "type review\n    \"\"\"\n Less indented than the shift.\n    \"\"\"\n  decision: enum(approve|revise)\n"

    assert {:ok, ^body} = Format.format(body)
  end

  # Sofia U8: the formatter refuses what compile/2 refuses, a body over 262,144 bytes, before
  # reading it, so an untrusted source costs nothing past the bound.
  test "a body over 262,144 bytes is refused as source_size, before any scan" do
    header = "---\nname: big\ndescription: Big.\nengine: 0.2.0-lab.1\nroles: [a]\n---\n"
    at_cap = "%%" <> String.duplicate("x", 262_144 - 3) <> "\n"
    assert byte_size(at_cap) == 262_144
    assert {:ok, _} = Format.format(header <> at_cap)
    assert {:ok, _} = Format.format(at_cap)

    over = at_cap <> "\n"
    assert {:error, :source_size} = Format.format(header <> over)
    assert {:error, :source_size} = Format.format(over)

    # Size is checked first: an over-size source is refused even when it is also invalid.
    assert {:error, :source_size} = Format.format(over <> <<0xFF>>)
    {us, _} = :timer.tc(fn -> Format.format(String.duplicate("\r", 262_145)) end)
    assert us < 5_000
  end

  test "a band fence over the bound in Markdown still formats (Markdown files bound fences)" do
    fence = "contract @1h => expired\nstate   new\n" <> String.duplicate("%% c\n", 60_000)
    assert byte_size(fence) > 262_144
    assert {:ok, out} = Format.format_markdown("```band\n" <> fence <> "```\n")
    assert out =~ "\nstate new\n"
  end

  test "CRLF sources are refused rather than rewritten" do
    assert {:error, :crlf} = Format.format("contract @1h => expired\r\n")
  end

  describe "shared vectors (spec/conformance/format)" do
    @vectors Path.wildcard(Path.join(@root, "spec/conformance/format/*/input.{band,md}"))

    test "there are vectors" do
      assert length(@vectors) >= 6
    end

    for input <- @vectors do
      @input input
      test "input formats to expected, expected is a fixed point: #{Path.basename(Path.dirname(input))}" do
        ext = Path.extname(@input)
        expected = @input |> Path.dirname() |> Path.join("expected" <> ext) |> File.read!()
        format = if ext == ".md", do: &Format.format_markdown/1, else: &Format.format/1
        assert {:ok, ^expected} = format.(File.read!(@input))
        assert {:ok, ^expected} = format.(expected)
      end
    end

    test "there is a Markdown vector" do
      assert Enum.any?(@vectors, &(Path.extname(&1) == ".md"))
    end
  end

  describe "every .band file in the repository" do
    # Tracked files only: other tests leave FIFOs and device symlinks named x.band in tmp/.
    {tracked, 0} = System.cmd("git", ["ls-files", "-z", "--", "*.band"], cd: @root)
    @files tracked |> String.split(<<0>>, trim: true) |> Enum.map(&Path.join(@root, &1))

    test "is covered (at least 54 files)" do
      assert length(@files) >= 54
    end

    for path <- @files do
      @path path
      test "keeps its compiled digest and is idempotent: #{Path.relative_to(path, @root)}" do
        source = File.read!(@path)
        assert {:ok, once} = Format.format(source)
        assert {:ok, ^once} = Format.format(once)
        header = header_for(@path, source)
        assert {:ok, before} = compile(header <> source)
        assert {:ok, after_format} = compile(header <> once)
        assert before.digest == after_format.digest
        assert shape(before) == shape(after_format)
      end
    end
  end

  # A body-only reference gets a header from its standard package's SKILL.md roles.
  defp header_for(path, source) do
    if String.starts_with?(source, "---") do
      ""
    else
      skill = path |> Path.dirname() |> Path.dirname() |> Path.join("SKILL.md") |> File.read!()
      [_, roles] = Regex.run(~r/band-roles: *(.+)/, skill)

      "---\nname: body\ndescription: Body-only reference.\nengine: 0.2.0-lab.1\nroles: [#{roles}]\n---\n"
    end
  end

  describe "mix contract.format" do
    setup do
      dir = Path.join(System.tmp_dir!(), "bandfmt-#{System.unique_integer([:positive])}")
      File.mkdir_p!(dir)
      on_exit(fn -> File.rm_rf(dir) end)
      messy = Path.join(dir, "messy.band")
      tidy = Path.join(dir, "tidy.band")
      File.write!(messy, @messy)
      File.write!(tidy, @formatted)
      %{dir: dir, messy: messy, tidy: tidy}
    end

    defp run_task(args) do
      parent = self()

      stderr =
        capture_io(:stderr, fn ->
          result =
            try do
              stdout = capture_io(fn -> Mix.Tasks.Contract.Format.run(args) end)
              {:returned, stdout}
            catch
              :exit, reason -> {:exit, reason}
            end

          send(parent, {:result, result})
        end)

      assert_received {:result, result}
      {result, stderr}
    end

    test "--check exits 1 and lists unformatted files without writing", ctx do
      {result, stderr} = run_task(["--check", ctx.messy, ctx.tidy])
      assert result == {:exit, {:shutdown, 1}}
      assert %{"error" => %{"code" => "unformatted", "files" => [path]}} = JSON.decode!(stderr)
      assert path == ctx.messy
      assert File.read!(ctx.messy) == @messy
    end

    test "--check passes on formatted files", ctx do
      assert {{:returned, stdout}, ""} = run_task(["--check", ctx.tidy])
      assert %{"checked" => 1, "unformatted" => []} = JSON.decode!(stdout)
    end

    test "rewrites files in place, directories recursively", ctx do
      assert {{:returned, stdout}, ""} = run_task([ctx.dir])
      assert %{"formatted" => [path], "unchanged" => 1} = JSON.decode!(stdout)
      assert path == ctx.messy
      assert File.read!(ctx.messy) == @formatted
    end

    test "--check passes every expected vector and flags every input vector" do
      vectors = Path.join(@root, "spec/conformance/format")
      expected = Path.wildcard(Path.join(vectors, "*/expected.{band,md}"))
      inputs = Path.wildcard(Path.join(vectors, "*/input.{band,md}"))
      assert {{:returned, stdout}, ""} = run_task(["--check" | expected])
      assert %{"checked" => n, "unformatted" => []} = JSON.decode!(stdout)
      assert n == length(expected)
      {result, stderr} = run_task(["--check" | inputs])
      assert result == {:exit, {:shutdown, 1}}
      assert %{"error" => %{"code" => "unformatted", "files" => files}} = JSON.decode!(stderr)
      assert Enum.sort(files) == Enum.sort(inputs)
    end

    # A blocked read of a FIFO is released by opening it read-write (never blocks on Linux).
    defp fifo!(path) do
      {_, 0} = System.cmd("mkfifo", [path])

      on_exit(fn ->
        with {:ok, io} <- :file.open(String.to_charlist(path), [:read, :write, :raw]),
             do: :file.close(io)
      end)

      path
    end

    @tag timeout: 15_000
    test "a directory walk takes regular files only: no deps, _build, node_modules, dot dirs, symlinks or FIFOs",
         ctx do
      outside = Path.join(ctx.dir <> "-outside", "z.band")
      File.mkdir_p!(Path.dirname(outside))
      File.write!(outside, @messy)
      on_exit(fn -> File.rm_rf(Path.dirname(outside)) end)

      for skipped <- ~w(deps _build node_modules .venv) do
        File.mkdir_p!(Path.join(ctx.dir, skipped))
        File.write!(Path.join([ctx.dir, skipped, "x.band"]), @messy)
      end

      File.ln_s!(Path.dirname(outside), Path.join(ctx.dir, "linked"))
      File.ln_s!(outside, Path.join(ctx.dir, "alias.band"))
      File.ln_s!("/dev/null", Path.join(ctx.dir, "null.band"))
      fifo!(Path.join(ctx.dir, "fifo.band"))
      {result, stderr} = run_task(["--check", ctx.dir])
      assert result == {:exit, {:shutdown, 1}}
      assert %{"error" => %{"files" => [path]}} = JSON.decode!(stderr)
      assert path == ctx.messy
    end

    @tag timeout: 15_000
    test "refuses a FIFO or a symlink named on the command line without reading it", ctx do
      fifo = fifo!(Path.join(ctx.dir, "fifo.band"))
      link = Path.join(ctx.dir, "link.band")
      File.ln_s!(ctx.messy, link)

      for path <- [fifo, link] do
        {result, stderr} = run_task(["--check", path])
        assert result == {:exit, {:shutdown, 1}}

        assert %{"error" => %{"code" => "unsafe_contract_path", "path" => ^path}} =
                 JSON.decode!(stderr)
      end

      assert File.read!(ctx.messy) == @messy
    end

    # The suite's own LoadUnsafePathTest leaves tmp/**/x.band -> /dev/zero; an unbounded read
    # of it took beam.smp to 7.7 GB (OOM kill, 2026-10-14 15:36Z). Run under a memory cap.
    @tag timeout: 15_000
    test "never reads a /dev/zero symlink, named or met in a walk", ctx do
      zero = Path.join(ctx.dir, "zero.band")
      File.ln_s!("/dev/zero", zero)

      {result, stderr} = run_task(["--check", zero])
      assert result == {:exit, {:shutdown, 1}}

      assert %{"error" => %{"code" => "unsafe_contract_path", "path" => ^zero}} =
               JSON.decode!(stderr)

      {result, stderr} = run_task(["--check", ctx.dir])
      assert result == {:exit, {:shutdown, 1}}
      assert %{"error" => %{"files" => [path]}} = JSON.decode!(stderr)
      assert path == ctx.messy
    end

    # spec/format.md, Checking: only * and ? are special, within one segment and never a
    # leading dot; Malik fixed the same backtracking in the Python twin (7edb794).
    test "ignore patterns: brackets are literal and ? is one character", ctx do
      sub = Path.join(ctx.dir, "globs")
      File.mkdir_p!(sub)
      names = ["[ab]x.band", "ax.band", "éx.band", "ab.band", "axxb.band"]
      Enum.each(names, &File.write!(Path.join(sub, &1), @messy))
      File.write!(Path.join(sub, ".band-format-ignore"), "[ab]*.band\n?x.band\na*b.band\n")

      # [ab]* names only "[ab]x"; ?x names "ax" and "éx"; a*b names "ab" and "axxb".
      assert {{:returned, stdout}, ""} = run_task(["--check", sub])
      assert %{"checked" => 0, "unformatted" => []} = JSON.decode!(stdout)

      File.write!(Path.join(sub, ".band-format-ignore"), "[ab]*.band\n")
      {_, stderr} = run_task(["--check", sub])
      assert %{"error" => %{"files" => files}} = JSON.decode!(stderr)
      assert Enum.map(files, &Path.basename/1) == ~w(ab.band ax.band axxb.band éx.band)
    end

    test "an ignore pattern with many stars matches in linear time", ctx do
      sub = Path.join(ctx.dir, "stars")
      File.mkdir_p!(sub)
      File.write!(Path.join(sub, String.duplicate("a", 240) <> "c.band"), @messy)
      File.write!(Path.join(sub, ".band-format-ignore"), String.duplicate("*a", 6) <> "b.band\n")

      task = Task.async(fn -> :timer.tc(fn -> run_task(["--check", sub]) end) end)

      assert {:ok, {us, {_, stderr}}} =
               Task.yield(task, 5_000) || Task.shutdown(task, :brutal_kill)

      assert us < 1_000_000
      assert %{"error" => %{"files" => [_]}} = JSON.decode!(stderr)
    end

    test "a directory walk skips what .band-format-ignore lists, also in an ancestor", ctx do
      sub = Path.join(ctx.dir, "sub")
      File.mkdir_p!(Path.join(sub, "vectors/one"))

      [pinned, keep, input] =
        Enum.map(~w(pinned.band keep.band vectors/one/input.band), &Path.join(sub, &1))

      Enum.each([pinned, keep, input], &File.write!(&1, @messy))

      File.write!(Path.join(ctx.dir, ".band-format-ignore"), """
      # Hash-pinned copies and deliberately unformatted vectors.

      sub/pinned.band
        sub/vectors/*/input.band
      """)

      {result, stderr} = run_task(["--check", ctx.dir])
      assert result == {:exit, {:shutdown, 1}}
      assert %{"error" => %{"files" => files}} = JSON.decode!(stderr)
      assert files == Enum.sort([ctx.messy, keep])

      {_, stderr} = run_task(["--check", sub])
      assert %{"error" => %{"files" => [^keep]}} = JSON.decode!(stderr)

      {_, stderr} = run_task(["--check", pinned])
      assert %{"error" => %{"files" => [^pinned]}} = JSON.decode!(stderr)
    end

    # Holds after the bulk reformat (chore: format .band files); drop it with that commit.
    test "the repository is formatted: --check over its root is clean" do
      assert {{:returned, stdout}, ""} = run_task(["--check", @root])
      assert %{"checked" => checked, "unformatted" => []} = JSON.decode!(stdout)
      assert checked >= 19
    end

    # DF-65: ExUnit's tmp_dir keeps header-spikes test output, such as a 2 MiB contract body,
    # and an oversized x.band there aborted every repository-root walk with source_size.
    test "a repository-root --check skips header-spikes/tmp leftovers (DF-65)" do
      tmp = Path.join(@root, "header-spikes/tmp")
      created? = not File.exists?(tmp)
      dir = Path.join(tmp, "contract-format-test-#{System.unique_integer([:positive])}")
      File.mkdir_p!(dir)

      on_exit(fn ->
        File.rm_rf!(dir)
        if created?, do: File.rmdir(tmp)
      end)

      File.write!(Path.join(dir, "x.band"), @messy <> String.duplicate("%% padding\n", 30_000))
      {result, stderr} = run_task(["--check", @root])
      refute stderr =~ "header-spikes/tmp"

      case result do
        {:returned, stdout} -> refute stdout =~ "header-spikes/tmp"
        _ -> assert %{"error" => %{"code" => "unformatted"}} = JSON.decode!(stderr)
      end
    end

    test "formats the band fences of Markdown files in place, found by a walk or named", ctx do
      md = Path.join(ctx.dir, "notes.md")
      fenced = fn body -> "# Notes\n\nText   kept.\n\n```band\n" <> body <> "```\n" end
      File.write!(md, fenced.(@messy))
      {result, stderr} = run_task(["--check", ctx.dir])
      assert result == {:exit, {:shutdown, 1}}
      assert %{"error" => %{"files" => files}} = JSON.decode!(stderr)
      assert files == Enum.sort([ctx.messy, md])

      assert {{:returned, stdout}, ""} = run_task([md])
      assert %{"formatted" => [^md]} = JSON.decode!(stdout)
      assert File.read!(md) == fenced.(@formatted)
      assert {{:returned, _}, ""} = run_task(["--check", md])
    end

    test "refuses a file that does not compile and leaves it alone", ctx do
      broken = Path.join(ctx.dir, "broken.band")
      text = String.replace(@messy, "contract @1h => expired\n", "")
      File.write!(broken, text)
      {result, stderr} = run_task([broken])
      assert result == {:exit, {:shutdown, 1}}

      assert %{"error" => %{"code" => "contract_clock_required", "path" => ^broken}} =
               JSON.decode!(stderr)

      assert File.read!(broken) == text
    end
  end
end
