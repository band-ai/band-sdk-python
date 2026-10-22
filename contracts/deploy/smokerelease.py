#!/usr/bin/env python3
"""Exercise copied release artifacts from an isolated wheel install without Mix/Elixir on PATH."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import venv
from pathlib import Path

# Direct isolated invocation trusts this checkout or sealed release root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts.deploy.installation import install_release
from contracts.deploy.qualificationprocess import run_qualification
from contracts.deploy.releaseverification import verify_release

PROBE = r"""
import ctypes, hashlib, json, selectors, signal, subprocess, sys, tempfile, time
from pathlib import Path
from remote_agent.authority import DurableRoom
from remote_agent.program import Artifact, Program
from remote_agent.store import Store
from remote_agent.transport import EngineProcess
from remote_agent.network import HTTPRoom

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from contracts.deploy.qualificationprocess import install_termination_handler, register_process, stop_process_group
install_termination_handler()
assert ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) == 0  # systemd NoNewPrivileges=yes
from contracts.deploy.releasecontract import contract_path, smoke_contracts
contract = contract_path(root)
command = [str(root / 'engine/bin/contract-engine')]
for path, format in smoke_contracts(root):
    engine = EngineProcess(path, command=command, format=format)
    try:
        assert engine.request(op='definition', role='assistant')['role']['name'] == 'assistant'
    finally:
        engine.close()
message = {'id': 'q1', 'session': 'installed', 'kind': 'question',
           'payload': {'text': 'ping'}, 'expected_version': 0}
identity = None
for attempt in range(2):
    engine = EngineProcess(contract, command=command, format="frontmatter")
    store = Store(root / 'state/room.sqlite3')
    try:
        room = DurableRoom(store, engine, session='installed', bindings={'requester': 'user', 'assistant': 'agent'})
        receipt = room.publish(message, 'user')
        if attempt == 0:
            assert receipt['status'] == 'accepted', receipt
            first, identity = receipt, engine.identity
        else:
            assert receipt == {'status': 'duplicate', 'original': first}, (receipt, first)
            assert engine.identity == identity
        assert room.view('agent')['view']['facts']['q1']['payload'] == {'text': 'ping'}
    finally:
        engine.close()
        store.close()
artifact = Artifact.publish((root / 'examples/partial_program.py').read_text(), root / 'state/artifacts')
assert Program(artifact).describe({})['coverage']['mode'] == 'partial'
assert list((root / 'engine/lib').glob('header_spikes-*/priv/unicode16/LICENSE.txt'))
cli = Path(sys.executable).parent / 'contract-service'
processes = []
def private(name, value):
    path = root / name
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    path.chmod(0o600)
    return str(path)
def launch(name, config):
    error = tempfile.TemporaryFile()
    process = subprocess.Popen([str(cli), 'serve', private(name + '.json', config)],
                               stdout=subprocess.PIPE, stderr=error, start_new_session=True)
    processes.append((process, error))
    register_process(process)
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(10), 'service startup timeout'
        line = process.stdout.readline()
    if not line:
        error.seek(0)
        raise AssertionError(error.read().decode())
    status = json.loads(line)
    assert status['event'] == 'service_ready', status
    return process, status['url']
def stop(process):
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=6) == 0
def await_version(client, version):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if client.view('user')['view']['version'] == version: return
        time.sleep(0.05)
    raise AssertionError('installed agent did not catch up')
room_config = {'profile': 'band-contract/service/1', 'kind': 'room',
               'engine': {'command': command, 'contract': str(contract), 'format': 'frontmatter'},
               'store': {'path': str(root / 'service-room/state.sqlite3')}, 'poll_interval_ms': 100,
               'http': {'host': '127.0.0.1', 'port': 0, 'tokens_file': private('room-tokens.json',
                 {'u'*32: {'actor': 'user', 'permissions': ['room']},
                  'a'*32: {'actor': 'agent', 'permissions': ['room']}})},
               'room': {'session': 'service', 'bindings': {'requester': 'user', 'assistant': 'agent'}}}
try:
    process, url = launch('room', room_config)
    client = HTTPRoom(url, 'u'*32)
    agent_config = {'profile': 'band-contract/service/1', 'kind': 'agent',
                    'engine': room_config['engine'], 'poll_interval_ms': 100,
                    'store': {'path': str(root / 'service-agent/state.sqlite3')},
                    'http': {'host': '127.0.0.1', 'port': 0, 'tokens_file': private('agent-tokens.json',
                         {'o'*32: {'actor': 'operator', 'permissions': ['observe']}})},
                    'agent': {'actor': 'agent', 'program_source': str(root / 'examples/partial_program.py'),
                         'artifact_directory': str(root / 'service-agent/artifacts'),
                         'room': {'url': url, 'token_file': private('agent-room.token', 'a'*32)},
                         'provider': {'command': [sys.executable, '-c', "raise RuntimeError('no model expected')"]},
                         'max_repairs': 0}}
    agent, _ = launch('agent1', agent_config)
    proposal = dict(message, session='service')
    client.publish(proposal, 'user')
    await_version(client, 2)
    stop(agent)
    client.publish(dict(proposal, id='q2', expected_version=2), 'user')
    agent, _ = launch('agent2', agent_config)
    await_version(client, 4)
    stop(agent)
    with Store(root / 'service-agent/state.sqlite3') as store:
        assert (store.get('turns'), store.get('model_calls')) == (2, 0)
    stop(process)
finally:
    for process, error in processes:
        stop_process_group(process, timeout=6)
        process.stdout.close()
        error.close()
print(json.dumps({'installed_engine': True, 'header_formats': 2, 'profile': '0.2.0-lab.1', 'authority_restart_and_dedup': True,
                  'installed_program_sandbox': True, 'unicode_data_and_license': True,
                  'installed_service_cli': True, 'offline_inbox_and_restart': True,
                  'no_new_privileges': True,
                  'identity': identity}))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    source = args.release.absolute()
    verify_release(source)
    with tempfile.TemporaryDirectory(prefix="contract-install-") as temporary:
        root = Path(temporary)
        install = root / "release with spaces"
        shutil.copytree(source, install)
        venv.create(root / "venv", with_pip=True)
        python = root / "venv/bin/python"
        install_release(install, python)
        probe = root / "probe.py"
        probe.write_text(PROBE)
        run_qualification(
            [str(python), "-I", str(probe), str(install)],
            cwd=root,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            check=True,
            timeout=60,
        )


if __name__ == "__main__":
    main()
