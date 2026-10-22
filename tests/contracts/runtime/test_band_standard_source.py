from __future__ import annotations

import copy
import json
from contextlib import closing

import pytest

from tests.contracts.runtime.bandstandardsource import GenericStandardSourceFixture
from tests.contracts.runtime.filesource import normalization_source


@pytest.mark.contract_isolation
@pytest.mark.contract_engine("band")
class TestGenericStandardSource(GenericStandardSourceFixture):
    def test_standard_startup_and_resume_keep_complete_source_and_room_identity(
        self,
    ) -> None:
        self.authority()
        with closing(self.start()) as first:
            self.assertEqual(first.service.engine.identity, self.room.identity)
            self.assertEqual(
                first.service.store.get("state"), {"processed": 0, "mode": "ascii"}
            )
        source_path = self.workspace / "contract-source.json"
        encoded, config = (
            source_path.read_bytes(),
            (self.workspace / "service.json").read_bytes(),
        )
        self.assertEqual(json.loads(encoded), self.source)
        self.assertEqual(source_path.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.workspace / "contract.band").exists())
        self.assertEqual(json.loads(config)["engine"]["format"], "file-source")
        with closing(self.start()) as restarted:
            self.assertEqual(restarted.service.engine.identity, self.room.identity)
        self.assertEqual(source_path.read_bytes(), encoded)
        self.assertEqual((self.workspace / "service.json").read_bytes(), config)

    def test_resume_refuses_body_package_or_entry_changes_without_replacing_pins(
        self,
    ) -> None:
        self.authority()
        with closing(self.start()):
            pass
        source_path = self.workspace / "contract-source.json"
        saved = source_path.read_bytes()
        config = (self.workspace / "service.json").read_bytes()
        original = copy.deepcopy(self.source)
        for field, replacement in (
            ("body", original["body"] + "\n"),
            ("package_name", "different"),
            ("content", original["content"] + "\n"),
        ):
            self.source = dict(original, **{field: replacement})
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(ValueError, "pinned_runtime_source_changed"),
            ):
                self.start()
            self.assertEqual(source_path.read_bytes(), saved)
            self.assertEqual((self.workspace / "service.json").read_bytes(), config)

    def test_existing_frontmatter_files_and_runtime_configuration_are_unchanged_on_resume(
        self,
    ) -> None:
        self.source = normalization_source("frontmatter")
        self.authority()
        with closing(self.start()):
            pass
        path = self.workspace / "contract.band"
        encoded, config = (
            path.read_bytes(),
            (self.workspace / "service.json").read_bytes(),
        )
        self.assertEqual(encoded, self.source["content"].encode())
        self.assertFalse((self.workspace / "contract-source.json").exists())
        self.assertEqual(
            json.loads(config)["engine"],
            {
                "command": self.host["engine_command"],
                "contract": str(path),
                "format": "frontmatter",
            },
        )
        # Retained pre-envelope contexts use the original frontmatter default.
        self.source.pop("format")
        self.source.pop("profile")
        with closing(self.start()):
            pass
        self.assertEqual(path.read_bytes(), encoded)
        self.assertEqual((self.workspace / "service.json").read_bytes(), config)

    def test_unknown_explicit_source_format_cannot_fall_back_to_frontmatter(
        self,
    ) -> None:
        self.source = normalization_source("frontmatter")
        self.authority()
        self.source["format"] = "unknown"
        with (
            self.assertRaisesRegex(ValueError, "contract_source_format"),
            closing(self.start()),
        ):
            pass
