from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.band_format import FormatError, format_bytes, format_markdown_bytes
from tests.contracts.runtime.settings import runtime_settings
from tests.paths import CONTRACT_RUNTIME_ROOT, CONTRACTS_ROOT

if os.name == "posix":
    import resource


ROOT = CONTRACTS_ROOT


RUNTIME = CONTRACT_RUNTIME_ROOT.parent


MESSY = '''---
name: fmt-sample
description: A messy contract for the formatter.
engine: 0.2.0-lab.1
roles: [lead, a, b]
---


%% Whole-run clock.\x20\x20\x20
contract @1h => expired
"Ask both reviewers."\x20\x20
role lead
type request
    label: text(bytes:1..32,graphemes:1..10, regex: ~r/[A-Z][A-Z0-9 :|_-]*/)
type review
    """
    The decision.
      Indented line.
    """
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
'''


FORMATTED = '''---
name: fmt-sample
description: A messy contract for the formatter.
engine: 0.2.0-lab.1
roles: [lead, a, b]
---
%% Whole-run clock.
contract @1h => expired

"""
Ask both reviewers.
"""
role lead

type request
  label: text(bytes: 1..32,graphemes: 1..10, regex: ~r/[A-Z][A-Z0-9 :|_-]*/)

type review
  """
  The decision.
    Indented line.
  """
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
'''


def band_files() -> list[Path]:
    # Git exclusions keep compiler scratch FIFOs out of source discovery, including
    # when a newly added fixture has not yet been committed.
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "*.band",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return sorted(ROOT / os.fsdecode(name) for name in tracked.split(b"\0") if name)


def vector_inputs(name: str = "input") -> list[Path]:
    vectors = ROOT / "spec/conformance/format"
    return [*vectors.glob(f"*/{name}.band"), *vectors.glob(f"*/{name}.md")]


def _bounded_memory() -> None:
    # A regression that reads /dev/zero or a FIFO must fail here, not exhaust the shared host.
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))


def run_cli(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(RUNTIME))
    return subprocess.run(
        [sys.executable, "-m", "remote_agent.band_format", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        preexec_fn=_bounded_memory if os.name == "posix" else None,
        check=False,
    )


class BandFormatMarkdownFixture(unittest.TestCase):
    def fmt(self, text: str) -> str:
        return format_markdown_bytes(text.encode()).decode()


PROMPTS_HEADER = (
    "---\nname: prompts\ndescription: Prompt probe.\nengine: 0.2.0-lab.1\n"
    "roles: [lead, worker]\n---\n"
)


PROMPTS_COMPLETE = (
    PROMPTS_HEADER
    + '''"""
One lead assigns one piece of work and collects the note that closes it.
"""
contract @1h => expired
"""
The lead plans and assigns the work.
"""
role lead
"The worker does the work and reports back."
role worker
"""
An offer of work to the worker.
"""
type offer
  text: text(1..100)
"""
A note back to the lead.
"""
type note
  offer: ref(offer)
"""
Waiting for the lead's offer.
"""
state new
  lead -> worker: offer() awaits waiting
    => note() & done
     | @30m & stalled
"""
The worker owes the lead a note.
"""
state waiting
end done
end stalled
'''
)


PROMPTS_PARTIAL = (
    PROMPTS_HEADER
    + '''contract @1h => expired
"""
The lead plans and assigns the work.
"""
role lead
type offer
  text: text(1..100)
"""
A note back to the lead.
"""
type note
  offer: ref(offer)
"""
Waiting for the lead's offer.
"""
state new
  lead -> worker: offer() awaits waiting
    => note() & done
     | @30m & stalled
state waiting
end done
end stalled
'''
)


PROMPTS_PARTIAL_MISSING = [
    {"item": "contract", "name": "prompts", "line": 1, "file_line": 7},
    {"item": "role", "name": "worker", "line": 1, "file_line": 7},
    {"item": "type", "name": "offer", "line": 6, "file_line": 12},
    {"item": "state", "name": "waiting", "line": 20, "file_line": 26},
]


class BandFormatCLIFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="bandfmt-py-"))
        self.messy = self.dir / "messy.band"
        self.tidy = self.dir / "tidy.band"
        self.messy.write_text(MESSY)
        self.tidy.write_text(FORMATTED)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir)

    def fifo(self, name: str) -> Path:
        path = self.dir / name
        os.mkfifo(path)
        return path

    def assert_walk_is_clean(self, root: Path) -> None:
        env = dict(os.environ, PYTHONPATH=str(RUNTIME))
        result = subprocess.run(
            [sys.executable, "-m", "remote_agent.band_format", "--check"],
            cwd=root,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            preexec_fn=_bounded_memory if os.name == "posix" else None,
            check=False,
        )
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        report = json.loads(result.stdout)
        self.assertEqual(report["unformatted"], [])
        # Keep a floor so an empty discovery cannot pass while new contracts
        # may extend the repository file set.
        self.assertGreaterEqual(report["checked"], 19)


ELIXIR_SCRIPT = r"""
[path] = System.argv()
inputs = path |> File.read!() |> JSON.decode!()
outputs =
  Enum.map(inputs, fn [kind, b64] ->
    source = Base.decode64!(b64)
    result =
      if kind == "md",
        do: ContractLab.Format.format_markdown(source),
        else: ContractLab.Format.format(source)
    case result do
      {:ok, text} -> %{"ok" => Base.encode64(text)}
      {:error, code} -> %{"error" => Atom.to_string(code)}
    end
  end)
IO.write(JSON.encode!(outputs))
"""


EDGE_CASES = [
    MESSY,
    FORMATTED,
    "",
    "\n\n\n",
    "---\nname: x\n---",
    "---\nname: x\n---\ncontract @1h => expired\n",
    "---\nname: no closing fence\ncontract @1h => expired\n",
    "type a\n　　b : text(1)\n",
    "type a\n b:text(1)\n \n",
    "type a\n  b: text(regex: ~r/a\\/b|c/, x: ~s(p|q))  \n",
    "state s\n  a->b:m()=>t|u\n  a -> b: m(x: enum(a|b)) => t | u\n",
    "type a\ntype b\nend x\nend y\nrole r\nrole s\n",
    '%% lead\n\n/* note\n   two */\n"doc"\ntype a\n  b: text(1)\n',
    'type a\n\t"""\n\tTabbed doc.\n\t"""\n  b: text(1)\n',
    "type a\n      /* deep note\n    shallow */\n  b: text(1)\n",
    'type a\n  """\n  never closed\n  b: text(1)\n',
    "/* never closed\ntype a\n",
    "state s\n  collect c from r -> l @5m => d\n  reply m()\n  quorum 2\n  offline grace @1m\n  a -> b: m()\n",
    "contract @1h => expired\r\n",
    "contract́ @1h => expired\n(́ | x\n",
    # Prompt blocks: every one-line form, the ones kept, and blank lines after a prompt.
    '"Plain."\nrole lead\n  "Nested \\"q\\"."  \n\n\ntype a\n',
    '~s"C:\\work"\nrole a\n~s(100%)\nrole b\n~s[a #{b}]\nrole c\n~s<x\\y>\nrole d\n',
    '""\nrole a\n"a" b\nrole b\n"open\nrole c\n"trail\\\nrole d\n~s()\nrole e\n',
    '~s(""")\nrole a\n~s( """ )\nrole b\n~s(a)b\nrole c\n~s(a)(b)\nrole d\n~sx\nrole e\n',
    '"é́ café"\nrole a\n~s|x|\n\n%% c\n\nrole b\n"""\nDoc.\n"""\n\n\nrole c\n',
    "type a\ntype b\nend x\nend y\nrole r\nrole s\nx: list(y)\nz: list(y)\n",
]


MD_EDGE_CASES = [
    "",
    "# x\n",
    "# Notes\n\nText   kept.  \n\n```band\n"
    + MESSY
    + "```\n\n```elixir\ntype   a\n```\n",
    "1. Step\n\n   ```band\n   type a\n        b:text(1)\n\n   end x\n   ```\n",
    "~~~ band \ntype a\nend x\n~~~~\n````band\ntype a\n```\nend x\n````\n",
    "```band\ntype a\nend x\n  ```  \n```band\n  a -> b: m()\n```\n",
    "```band\nname: x\nroles: [a]\n```\n```band\n\n   \n```\n```band\n```\n",
    "  ```band\n  type a\n end x\n  ```\n",
    "```band\ntype a\nend x\n",
    "```bandx\ntype a\nend x\n```\n```\ntype a\n```band\ntype b\nend x\n```\n",
    "```text\nx\n```\n```band\ntype a\nend x\n```",
    "\t```band\ntype a\nend x\n```\n```band\n\ttype a\nend x\n```\n",
    '```band\n"Doc."\n\n\nrole lead\n```\n',
    '```band\n---\nname: x\n---\n"P"\ncontract @1h => expired\n```\n',
    "# x\r\n",
    "```band\n\u3000type a\n```\n",
]


def python_outputs(inputs: list[bytes], kind: str = "band") -> list[dict[str, Any]]:
    fmt = format_markdown_bytes if kind == "md" else format_bytes
    outputs = []
    for data in inputs:
        try:
            outputs.append({"ok": base64.b64encode(fmt(data)).decode()})
        except FormatError as error:
            outputs.append({"error": error.code})
    return outputs


def elixir_outputs(
    module: str, inputs: list[bytes], kind: str = "band"
) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="bandfmt-cross-") as tmp:
        batch = Path(tmp) / "inputs.json"
        batch.write_text(
            json.dumps([[kind, base64.b64encode(data).decode()] for data in inputs])
        )
        script = Path(tmp) / "cross.exs"
        script.write_text(ELIXIR_SCRIPT)
        result = subprocess.run(
            ["elixir", "-r", module, str(script), str(batch)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=Path(module).parent,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"elixir failed: {result.stderr[-2000:]}")
        return json.loads(result.stdout)


class BandFormatCrossFixture(unittest.TestCase):
    def compare(
        self, labels: list[str], inputs: list[bytes], kind: str = "band"
    ) -> None:
        module = runtime_settings().band_format_ex
        assert module is not None
        expected = elixir_outputs(str(module), inputs, kind)
        actual = python_outputs(inputs, kind)
        self.assertEqual(len(expected), len(inputs))
        mismatches = [label for label, e, a in zip(labels, expected, actual) if e != a]
        self.assertEqual(mismatches, [])


def markdown_files() -> list[Path]:
    root = CONTRACTS_ROOT
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "*.md",
        ],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    return sorted(root / os.fsdecode(name) for name in tracked.split(b"\0") if name)


PROMPTS_SCRIPT: str = r"""
[path] = System.argv()
outputs =
  path
  |> File.read!()
  |> JSON.decode!()
  |> Enum.map(fn file ->
    case ContractLab.CLI.run(["check", "--require-prompts", file]) do
      {:ok, _} -> %{"missing" => []}
      {:error, %{reason: %{code: :prompts_required, missing: missing}}} ->
        %{"missing" => Enum.map(missing, &Map.new(&1, fn {k, v} -> {Atom.to_string(k), v} end))}
      {:error, other} -> %{"error" => inspect(other)}
    end
  end)
IO.write(JSON.encode!(outputs))
"""


_HEADER = (
    "---\nname: cross\ndescription: Prompt cross case.\nengine: 0.2.0-lab.1\n"
    "roles:\n  - lead\n  - workers\n---\n"
)


PROMPT_CONTRACTS = [
    _HEADER
    + (
        '"C"\ncontract @1h => expired\n"L"\nrole lead\ntype worker: agent\n"W"\nworkers: list(worker)\n'
        'type offer\n  "The field."\n  text: text(1)\n"""\n   \n"""\ntype note\n" "\nstate new\n'
        '  "Rule."\n  lead -> workers: offer() => idle\n/* note\n */\n%% comment\n~s(Raw.)\n'
        "state idle\n  workers -> lead: note() => done\nend done\n"
    ),
    _HEADER
    + (
        "contract @1h => expired\ntype worker: agent\nworkers: list(worker)\n"
        '~s"""\n  Raw block.\n  """\ntype report\n  text: text(1)\n'
        '"Collecting."\nstate new\n  "Collect prompt."\n'
        "  collect reports from workers -> lead @5m => done\n    reply report()\n"
        '"""\n\\n\n"""\nend done\n'
    ),
]
