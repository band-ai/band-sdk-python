from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.paths import CONTRACTS_ROOT

SCRIPT = r'''
Code.compiler_options(ignore_module_conflict: true)
[bundle] = System.argv()
for name <- ~w(compiler engine replies reply_wait totals renewal) do
  Code.compile_file(Path.join(bundle, "lib/contract_lab/#{name}.ex"))
end
alias ContractLab.{Canonical, Engine, Totals}
source = fn path ->
  head = if path == "head",
    do: "state new\n  lead -> member: go() => reviewing\nstate reviewing\n  lead -> member: request() as req awaits working",
    else: "state new\n  lead -> member: request() as req awaits working"
  resend = if path == "head", do: "", else: "state reviewing\n  lead -> member: request() as req => working\n"
  text = """
  ---
  name: wait-budget
  description: Every reply window fits the remaining cumulative state budget.
  engine: 0.2.0-lab.1
  roles: [lead, member]
  ---
  contract @12h => expired
  type go
  type request
  type deliverable
    request: ref(request)
  type partial
    request: ref(request)
  #{head}
      => deliverable(request=$req) & delivered
       | partial(request=$req) & reviewing
       | @1h & late
  state working total @2h => over_budget max_entries 3 => late
  #{resend}end delivered
  end late
  end over_budget
  """
  %{"profile" => "band-contract/file-source/1", "format" => "frontmatter",
    "content" => text, "sha256" => Canonical.hash(text)}
end
step = fn s, id, actor, kind, minute, payload ->
  Engine.step(s, %{"id" => id, "session" => "budget", "kind" => kind, "payload" => payload},
    actor: actor, now: minute * 60_000)
end
rows = for path <- ["head", "resend"], second_exit <- [80, 65] do
  {:ok, contract} = ContractLab.compile_source(source.(path))
  opts = [session_id: "budget", now: 0, max_messages: 60,
          bindings: %{"lead" => "l", "member" => "m"}, room_policy: :unchecked]
  {:error, :unsupported_extension} = Engine.new(contract, opts)
  {:ok, s} = Engine.new(contract, Keyword.put(opts, :capabilities, contract.card.extensions))
  s = if path == "head", do: elem(step.(s, "go", "l", "go", 0, %{}), 1), else: s
  {%{status: :accepted}, s} = step.(s, "r1", "l", "request", 0, %{})
  {%{status: :accepted}, s} = step.(s, "p1", "m", "partial", 40, %{"request" => "r1"})
  {%{status: :accepted}, s} = step.(s, "r2", "l", "request", 45, %{})
  {%{status: :accepted}, s} = step.(s, "p2", "m", "partial", second_exit, %{"request" => "r2"})
  {decision, s} = step.(s, "r3", "l", "request", second_exit + 5, %{})
  %{path: path, second_exit: second_exit, decision: decision, wait: s.wait,
    total_deadline: Totals.deadline_at(s), default_extension_refused: true}
end
IO.puts(Canonical.encode(rows))
'''


class BundledWaitBudgetFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = CONTRACTS_ROOT
        bundle = root / "engines/band-v1/contract-lab"
        with tempfile.TemporaryDirectory(prefix="band-wait-budget-") as directory:
            script = Path(directory) / "probe.exs"
            script.write_text(SCRIPT)
            process = subprocess.run(
                [
                    "elixir",
                    "-pa",
                    str(bundle / "_build/dev/lib/*/ebin"),
                    str(script),
                    str(bundle),
                ],
                cwd=root,
                env=dict(os.environ, ERL_FLAGS="+S 2:2 +A 2"),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        if process.returncode:
            raise AssertionError(
                f"Bundled engine probe failed its setup ({process.returncode}): {process.stderr}"
            )
        cls.rows = {
            (row["path"], row["second_exit"]): row for row in json.loads(process.stdout)
        }

    def assert_insufficient(self, path: Any) -> None:
        row = self.rows[path, 80]
        self.assertEqual(row["decision"]["status"], "accepted")
        self.assertEqual(row["decision"]["state"], "over_budget")
        self.assertEqual(row["decision"]["outcome"], "over_budget")
        self.assertIsNone(row["wait"])

    def assert_exact_fit(self, path: Any) -> None:
        row = self.rows[path, 65]
        self.assertEqual(row["decision"]["state"], "working")
        self.assertEqual(row["wait"]["due_at"], row["total_deadline"])
