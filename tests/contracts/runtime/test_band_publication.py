from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from remote_agent.band.arithmetic import RuntimeDriver
from tests.contracts.runtime.bandpublication import publish


@pytest.mark.contract_linux
class TestPublication(unittest.TestCase):
    def test_process_death_after_atomic_link_keeps_complete_output_retryable(
        self,
    ) -> None:
        script = """
import os,sys
from pathlib import Path
os.umask(0o077)
from remote_agent.band.generic import immutable_file
from remote_agent.band.arithmetic import write_runtime_configuration
from remote_agent.band.qualification import _immutable_bytes
original_link=os.link
def interrupted_link(*args,**kwargs):
    original_link(*args,**kwargs)
    os._exit(73)
os.link=interrupted_link
kind,path=sys.argv[1],Path(sys.argv[2])
if kind=='source':immutable_file(path,'qualified source')
elif kind=='quality':_immutable_bytes(path,b'qualified quality')
else:write_runtime_configuration(path,{'agent':{'provider':{'command':['provider']},'validator':{'command':['validator']}},'identity':'original'})
os._exit(74)
"""
        with tempfile.TemporaryDirectory() as directory:
            for kind in ("source", "quality", "configuration"):
                with self.subTest(kind=kind):
                    path = Path(directory) / (kind + ".pin")
                    process = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            "-W",
                            "error",
                            "-c",
                            script,
                            kind,
                            str(path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertEqual(process.returncode, 73, process.stderr)
                    expected = path.read_bytes()
                    self.assertGreater(len(expected), 0)
                    self.assertEqual(path.stat().st_nlink, 2)
                    unrelated = Path(directory) / ".journal-export-unrelated"
                    unrelated.write_bytes(b"another publication")
                    publish(kind, path)
                    self.assertEqual(path.read_bytes(), expected)
                    self.assertEqual(path.stat().st_nlink, 1)
                    self.assertEqual(unrelated.read_bytes(), b"another publication")

    def test_process_death_after_create_keeps_each_final_pin_absent_and_retryable(
        self,
    ) -> None:
        script = """
import os,sys
from pathlib import Path
os.umask(0o077)
from remote_agent.band.generic import immutable_file
from remote_agent.band.arithmetic import write_runtime_configuration
from remote_agent.band.qualification import _immutable_bytes
original_open=Path.open
def interrupted_open(self,mode='r',*args,**kwargs):
    result=original_open(self,mode,*args,**kwargs)
    if any(flag in mode for flag in 'wx'):os._exit(73)
    return result
Path.open=interrupted_open
os.fdopen=lambda *args,**kwargs:os._exit(73)
kind,path=sys.argv[1],Path(sys.argv[2])
if kind=='source':immutable_file(path,'qualified source')
elif kind=='quality':_immutable_bytes(path,b'qualified quality')
else:write_runtime_configuration(path,{'agent':{'provider':{'command':['provider']},'validator':{'command':['validator']}},'identity':'original'})
os._exit(74)
"""
        with tempfile.TemporaryDirectory() as directory:
            for kind in ("source", "quality", "configuration"):
                with self.subTest(kind=kind):
                    path = Path(directory) / (kind + ".pin")
                    process = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            "-W",
                            "error",
                            "-c",
                            script,
                            kind,
                            str(path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    self.assertEqual(process.returncode, 73, process.stderr)
                    self.assertFalse(
                        path.exists(),
                        "An incomplete file must not acquire the immutable final name",
                    )
                    publish(kind, path)
                    content = path.read_bytes()
                    publish(kind, path)
                    self.assertEqual(path.read_bytes(), content)

    def test_completed_identical_retry_preserves_bytes_and_changed_content_is_refused(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for kind, reason in (
                ("source", "pinned_runtime_source_changed"),
                ("quality", "quality_plan_identity_conflict"),
                ("configuration", "pinned_runtime_configuration_changed"),
            ):
                with self.subTest(kind=kind):
                    path = Path(directory) / (kind + ".pin")
                    publish(kind, path)
                    expected = path.read_bytes()
                    publish(kind, path)
                    with self.assertRaisesRegex(ValueError, reason):
                        publish(kind, path, changed=True)
                    self.assertEqual(path.read_bytes(), expected)
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(path.stat().st_nlink, 1)

    def test_legacy_runtime_retries_do_not_rewrite_the_source_and_contract_pins(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "agent.key"
            key.write_text("k" * 32)
            key.chmod(0o600)
            model = root / "model.json"
            model.write_text(
                json.dumps(
                    {
                        "profile": "band-contract/structured-model/1",
                        "command": [
                            sys.executable,
                            "-c",
                            'raise RuntimeError("unexpected model call")',
                        ],
                        "credentials": {},
                    }
                )
            )
            model.chmod(0o600)
            value = {
                "room": "room",
                "session": "session",
                "actor": "User:owner",
                "request": {"id": "start", "journal": True, "mode": "program"},
                "context": {
                    "config": {
                        "bindings": {"calculator": "Agent:c", "requester": "Agent:r"}
                    },
                    "source": {
                        "sha256": "9b02c7efb74809f8b1192a7bd3002db9c3fdce775cad02ea9db984244036e1a0",
                        "content": "pinned contract",
                    },
                },
            }
            host = {
                "agent_keys": {"Agent:c": str(key)},
                "engine_command": [sys.executable],
                "model_config": str(model),
                "platform_url": "https://band.example",
            }
            workspace = root / "runtime"
            with (
                patch(
                    "remote_agent.band.arithmetic.validate_candidate",
                    return_value={"coverage": {"mode": "full"}},
                ),
                patch(
                    "remote_agent.band.arithmetic.Service",
                    side_effect=ValueError("fixture_service_boundary"),
                ) as service,
            ):
                with self.assertRaisesRegex(ValueError, "fixture_service_boundary"):
                    RuntimeDriver(
                        value,
                        {"role": "calculator", "source": "qualified source"},
                        workspace,
                        host_config=host,
                    )
                for path in (workspace / "initial.py", workspace / "contract.band"):
                    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
                with self.assertRaisesRegex(ValueError, "fixture_service_boundary"):
                    RuntimeDriver(
                        value,
                        {"role": "calculator", "source": "qualified source"},
                        workspace,
                        host_config=host,
                    )
                self.assertEqual(service.call_count, 2)
                for path in (workspace / "initial.py", workspace / "contract.band"):
                    self.assertEqual(
                        path.stat().st_mtime_ns,
                        1_000_000_000,
                        "A completed immutable pin must be reused",
                    )
                changed = dict(
                    value,
                    context=dict(
                        value["context"],
                        source=dict(
                            value["context"]["source"], content="changed contract"
                        ),
                    ),
                )
                with self.assertRaisesRegex(
                    ValueError, "pinned_runtime_source_changed"
                ):
                    RuntimeDriver(
                        changed,
                        {"role": "calculator", "source": "qualified source"},
                        workspace,
                        host_config=host,
                    )
                self.assertEqual(service.call_count, 2)
