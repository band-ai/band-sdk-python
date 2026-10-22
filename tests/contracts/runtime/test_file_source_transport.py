from __future__ import annotations

import copy
import hashlib
import json
from contextlib import closing

import pytest

from remote_agent.transport import EngineCompileError, EngineProcess, TransportError
from tests.contracts.runtime.filesource import (
    ROOT,
    engine_command,
    normalization_source,
    package,
)
from tests.contracts.runtime.filesourcetransport import FileSourceTransportFixture


class TestFileSourceTransport(FileSourceTransportFixture):
    @pytest.mark.contract_linux
    @pytest.mark.contract_engine
    @pytest.mark.contract_engine("band")
    def test_both_engine_variants_preserve_canonical_conformance_identities(
        self,
    ) -> None:
        fixture = json.loads(
            (
                ROOT
                / "spec/conformance/case-v2-frontmatter-and-standard-discovery.json"
            ).read_text()
        )
        for pinned in (False, True):
            for index in (0, 1):
                with self.subTest(pinned=pinned, source=index):
                    source = fixture["requests"][index]["source"]
                    expected = fixture["expected"][index]["ok"]
                    with closing(
                        EngineProcess(
                            self.write(source),
                            format="file-source",
                            command=engine_command(pinned=pinned),
                        )
                    ) as engine:
                        self.assertEqual(
                            engine.identity,
                            {
                                "ready": True,
                                "definition": expected["digest"],
                                "source": source["sha256"],
                            },
                        )

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_full_yaml_and_nested_reference_match_existing_standard_package_loading(
        self,
    ) -> None:
        source = normalization_source()
        with (
            closing(
                EngineProcess(
                    package(self.root, source),
                    format="standard",
                    command=engine_command(),
                )
            ) as existing,
            closing(
                EngineProcess(
                    self.write(source), format="file-source", command=engine_command()
                )
            ) as engine,
        ):
            self.assertEqual(engine.identity, existing.identity)
            self.assertEqual(engine.identity["source"], source["sha256"])
            self.assertEqual(
                engine.request(op="definition", role="normalizer"),
                existing.request(op="definition", role="normalizer"),
            )

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_incomplete_unknown_and_inconsistent_source_envelopes_fail_before_ready(
        self,
    ) -> None:
        source = normalization_source()
        invalid = [
            None,
            [],
            dict(source, unexpected=True),
            dict(source, profile="other"),
            dict(source, format="unknown"),
            dict(source, format="native"),
            dict(source, sha256="0" * 64),
            dict(source, body=source["body"] + "\n"),
            dict(source, body=None),
            dict(source, package_name="different"),
        ]
        missing = copy.deepcopy(source)
        missing.pop("body")
        invalid.append(missing)
        for index, value in enumerate(invalid):
            with (
                self.subTest(case=index),
                self.assertRaises(TransportError),
                closing(
                    EngineProcess(
                        self.write(value),
                        format="file-source",
                        command=engine_command(),
                    )
                ),
            ):
                self.fail("Invalid source became ready")

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_malformed_and_oversized_json_are_refused(self) -> None:
        for encoded in (b'{"profile":', b" " * (4_194_304 + 1)):
            with self.subTest(size=len(encoded)):
                self.path.write_bytes(encoded)
                self.path.chmod(0o600)
                with (
                    self.assertRaises(TransportError),
                    closing(
                        EngineProcess(
                            self.path, format="file-source", command=engine_command()
                        )
                    ),
                ):
                    self.fail("Invalid encoded source became ready")

    @pytest.mark.contract_linux
    @pytest.mark.contract_engine("band")
    def test_compiler_diagnostics_remain_safe_and_structured(self) -> None:
        source = normalization_source("frontmatter")
        source["content"] = source["content"].replace(
            "contract @30m => expired\n", "contract\n", 1
        )
        source["sha256"] = hashlib.sha256(source["content"].encode()).hexdigest()
        with self.assertRaises(EngineCompileError) as error:
            EngineProcess(
                self.write(source), format="file-source", command=engine_command()
            )
        self.assertEqual(
            error.exception.diagnostic, {"code": "contract_clock_required", "line": 1}
        )
        self.assertNotIn(str(self.path), str(error.exception))
