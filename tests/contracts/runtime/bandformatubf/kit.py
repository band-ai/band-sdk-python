from __future__ import annotations

import json
from typing import Any

from tests.paths import CONTRACT_RUNTIME_TESTS


def markdown(source: Any) -> Any:
    # An indented band fence and unrelated prose/fence whose bytes must stay put.
    indented = "\n".join(
        "   " + line if line else "" for line in source.rstrip("\n").split("\n")
    )
    return (
        "# Reply syntax  \n\n1. Example\n\n   ```band\n"
        + indented
        + "\n   ```\n\n```text\nreply   kept\n```\n"
    )


UBF_VECTORS = [
    (
        "implicit-head-and-reference",
        "state new\nlead->reviewer:request()\n=>review()&collected\n|@5m&stalled",
        (
            "state new\n  lead -> reviewer: request()\n"
            "    => review() & collected\n     | @5m & stalled\n"
        ),
    ),
    (
        "awaits-implicit-binding",
        (
            "state new\nlead->deployer:deploy() awaits deploying\n"
            "=>ok()&deployed\n|error(retryable=true)&retrying\n"
            "|error(retryable=false)&failed\n|@10m&stalled\n"
        ),
        (
            "state new\n  lead -> deployer: deploy() awaits deploying\n"
            "    => ok() & deployed\n     | error(retryable=true) & retrying\n"
            "     | error(retryable=false) & failed\n     | @10m & stalled\n"
        ),
    ),
    (
        "explicit-binding-and-reference",
        (
            "state new\nlead->deployer:deploy() as job awaits deploying\n"
            "=>ok(request=$job) as response&deployed\n"
            "|error(request=$job, retryable=true) as response&retrying\n|@10m&stalled\n"
        ),
        (
            "state new\n  lead -> deployer: deploy() as job awaits deploying\n"
            "    => ok(request=$job) as response & deployed\n"
            "     | error(request=$job, retryable=true) as response & retrying\n"
            "     | @10m & stalled\n"
        ),
    ),
    (
        "explicit-head-implicit-reference",
        "state new\nlead->worker:task() as job\n=>done()&finished\n|@5m&stalled\n",
        (
            "state new\n  lead -> worker: task() as job\n"
            "    => done() & finished\n     | @5m & stalled\n"
        ),
    ),
    (
        "implicit-head-explicit-reference",
        (
            "state new\nlead->worker:task() awaits working\n"
            "=>done(task=$working)&finished\n|@5m&stalled\n"
        ),
        (
            "state new\n  lead -> worker: task() awaits working\n"
            "    => done(task=$working) & finished\n     | @5m & stalled\n"
        ),
    ),
    (
        "prompts-before-every-alternative",
        (
            "state new\nlead->worker:task() awaits working\n"
            '"Done."\n=>done()&finished\n"Failed."\n|failed()&broken\n'
            '"Expired."\n|@5m&stalled\n'
        ),
        (
            "state new\n  lead -> worker: task() awaits working\n"
            '    """\n    Done.\n    """\n    => done() & finished\n'
            '     """\n     Failed.\n     """\n     | failed() & broken\n'
            '     """\n     Expired.\n     """\n     | @5m & stalled\n'
        ),
    ),
    (
        "raw-block-prompts-and-blank-runs",
        (
            "state new\nlead->worker:task()\n"
            '       """\n       Done.  \n         Keep indent.\n       """\n\n\n'
            "=>done()&finished\n"
            '       ~s"""\n       C:\\reply #{kept}\n       """\n\n'
            "|@5m&stalled\n"
        ),
        (
            "state new\n  lead -> worker: task()\n"
            '    """\n    Done.  \n      Keep indent.\n    """\n'
            "    => done() & finished\n"
            '     ~s"""\n     C:\\reply #{kept}\n     """\n     | @5m & stalled\n'
        ),
    ),
    (
        "comments-notes-and-blanks",
        (
            "state new\nlead->worker:task()\n%% first\n/* before\n   first */\n"
            "=>done()&finished\n\n\n%% last\n/* timeout */\n|@5m&stalled\n"
        ),
        (
            "state new\n  lead -> worker: task()\n    %% first\n    /* before\n"
            "       first */\n    => done() & finished\n\n"
            "     %% last\n     /* timeout */\n     | @5m & stalled\n"
        ),
    ),
    (
        "consecutive-heads",
        (
            "state new\na->b:one()\n=>answer()&done\n|@1m&late\n"
            "a->c:two() awaits second\n=>other()&done\n|@2m&late\nend done\nend late\n"
        ),
        (
            "state new\n  a -> b: one()\n    => answer() & done\n     | @1m & late\n"
            "  a -> c: two() awaits second\n    => other() & done\n     | @2m & late\n"
            "\nend done\n\nend late\n"
        ),
    ),
    (
        "legacy-timed-pipe-and-enum",
        (
            "type answer\nmode: enum(ok|error)\nstate new\n"
            "a->b:request()\n=>answer(mode=ok)&done\n|answer(mode=error)&failed\n"
            "|@5m&late\na->b:ping() @1m=>done|late\n"
        ),
        (
            "type answer\n  mode: enum(ok|error)\n\nstate new\n"
            "  a -> b: request()\n    => answer(mode=ok) & done\n"
            "     | answer(mode=error) & failed\n     | @5m & late\n"
            "  a -> b: ping() @1m => done | late\n"
        ),
    ),
    (
        "sigil-contents-and-ref-union",
        (
            "type answer\nrequest: ref(one|two)\nstate new\n"
            "a->b:request(pattern=~s[ -> : => | & ])\n"
            "=>answer(pattern=~r/a|b&c/, request=$request_wait)&done\n|@1m&late\n"
        ),
        (
            "type answer\n  request: ref(one|two)\n\nstate new\n"
            "  a -> b: request(pattern=~s[ -> : => | & ])\n"
            "    => answer(pattern=~r/a|b&c/, request=$request_wait) & done\n"
            "     | @1m & late\n"
        ),
    ),
    (
        "collect-and-top-level-boundaries",
        (
            "state new\ncollect reports from workers -> lead @5m=>done\n"
            "reply report()\nquorum 2\nfailure =>failed\n"
            "lead->worker:task() awaits working\n=>done()&finished\n|@5m&stalled\n"
            "state working max_entries 3=>exhausted\nevent worker->lead:progress()=>@self\n"
            "end finished\n"
        ),
        (
            "state new\n  collect reports from workers -> lead @5m => done\n"
            "    reply report()\n    quorum 2\n    failure => failed\n"
            "  lead -> worker: task() awaits working\n    => done() & finished\n"
            "     | @5m & stalled\n\nstate working max_entries 3 => exhausted\n"
            "  event worker -> lead: progress() => @self\n\nend finished\n"
        ),
    ),
    (
        "frontmatter-kept",
        (
            "---\nname: u\ndescription: café\nroles: [a, b]\n---\n"
            "state new\na->b:request()\n=>answer()&done\n|@5m&late\n"
        ),
        (
            "---\nname: u\ndescription: café\nroles: [a, b]\n---\n"
            "state new\n  a -> b: request()\n    => answer() & done\n     | @5m & late\n"
        ),
    ),
]


KENJI_UBF_VECTORS = [
    ("kenji/" + vector["name"], vector["input"], vector["expected"])
    for vector in json.loads(
        (CONTRACT_RUNTIME_TESTS / "fixtures" / "ubf_format_vectors.json").read_text()
    )["vectors"]
]


ALL_UBF_VECTORS = UBF_VECTORS + KENJI_UBF_VECTORS
