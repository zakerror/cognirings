"""Persistence invariants, including real process contention and injected failures."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("archive.py")
SPEC = importlib.util.spec_from_file_location("archive", SCRIPT)
archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archive)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "learning"
        archive.initialize(self.root, "测试用户")
        self.bundle = {
            "session_id": "session-1", "created_at": "2026-10-21T10:00:00+00:00",
            "domain": {"id": "systems", "title": "系统思维"}, "profile": {"status": "untested"},
            "architecture": {"nodes": [{"id": "a"}, {"id": "b"}],
                             "edges": [{"from": "a", "to": "b", "type": "prerequisite"}]},
            "session": {"user_feedback": None, "questions": [], "profile_changes": []},
            "report_markdown": "# 原始报告\n", "profile_markdown": "# 暂未验证\n",
            "architecture_markdown": "a → b\n", "storybook_markdown": "# 成长故事\n",
            "topics": [{"id": "intro", "source_ids": ["source-1"]}],
            "sources": [{"id": "source-1", "url": "https://example.org"}],
            "items": [{"id": "item-1", "source_id": "source-1"}],
        }

    def commit(self, bundle=None):
        return archive.commit(self.root, "systems", bundle or self.bundle)

    def pointer(self):
        return (self.root / "domains/systems/current.json").read_bytes()

    def test_history_and_optional_markdown_preserved(self):
        self.commit()
        first = self.root / "domains/systems/sessions/session-1/bundle.json"
        original = first.read_bytes()
        second = copy.deepcopy(self.bundle)
        second.update(session_id="session-2", report_markdown="# 后续报告\n")
        self.commit(second)
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(json.loads(original), self.bundle)
        self.assertEqual(archive.status(self.root)["domains"][0]["session_id"], "session-2")
        self.assertEqual(archive.validate(self.root)["snapshots"], 2)
        index = (self.root / "INDEX.md").read_text()
        self.assertIn("历史 · session-1", index)
        self.assertIn("session-2/profile.md", index)
        self.assertIn("session-2/storybook.md", index)

    def test_duplicate_is_rejected_without_overwrite(self):
        self.commit()
        pointer = self.pointer()
        self.bundle["report_markdown"] = "must not overwrite"
        with self.assertRaisesRegex(archive.ArchiveError, "already exists"):
            self.commit()
        self.assertEqual(self.pointer(), pointer)
        self.assertEqual((self.root / "domains/systems/sessions/session-1/report.md").read_text(),
                         "# 原始报告\n")

    def test_unsafe_identifiers_and_symlink_are_rejected(self):
        for key in ("session_id", "domain"):
            for value in ("../escape", "/absolute", "..", "x/y", "x\\y", "x\n"):
                bad = copy.deepcopy(self.bundle)
                if key == "session_id":
                    bad[key] = value
                else:
                    bad[key]["id"] = value
                with self.subTest(key=key, value=value), self.assertRaises(archive.ArchiveError):
                    archive.commit(self.root, bad["domain"]["id"], bad)
        (self.root / "domains/systems").symlink_to(Path(self.temp.name), target_is_directory=True)
        with self.assertRaisesRegex(archive.ArchiveError, "Symlink"):
            self.commit()

    def test_prerequisite_dag_only_and_cross_domain_bridges(self):
        edge = {"from": "b", "to": "a", "type": "prerequisite"}
        self.bundle["architecture"]["edges"].append(edge)
        with self.assertRaisesRegex(archive.ArchiveError, "DAG"):
            self.commit()
        edge["type"] = "analogy"
        self.bundle["architecture"]["bridges"] = [
            {"from": "systems:a", "to": "external:z", "type": "analogy"},
            {"from": "external:z", "to": "systems:a", "type": "analogy"}]
        self.commit()
        self.assertTrue(archive.validate(self.root)["valid"])
        bad = copy.deepcopy(self.bundle)
        bad["session_id"] = "missing"
        bad["architecture"]["edges"].append({"from": "a", "to": "absent", "type": "analogy"})
        with self.assertRaisesRegex(archive.ArchiveError, "Missing local endpoint"):
            self.commit(bad)

    def test_failed_snapshot_and_failed_pointer_leave_current_unchanged(self):
        self.commit()
        previous = self.pointer()
        self.bundle["session_id"] = "session-2"
        with mock.patch.object(archive, "write_file", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.commit()
        self.assertEqual(self.pointer(), previous)
        self.assertFalse((self.root / "domains/systems/sessions/session-2").exists())
        real_replace = archive.os.replace

        def fail_pointer(source, destination):
            if Path(destination).name == "current.json":
                raise OSError("pointer replacement failed")
            return real_replace(source, destination)

        with mock.patch.object(archive.os, "replace", side_effect=fail_pointer):
            with self.assertRaises(OSError):
                self.commit()
        self.assertEqual(self.pointer(), previous)
        self.assertEqual(archive.validate(self.root)["snapshots"], 2)

    def test_index_failure_is_not_a_failed_commit_and_corruption_is_detected(self):
        with mock.patch.object(archive, "rebuild_index", side_effect=OSError("index unavailable")):
            result = self.commit()
        self.assertTrue(result["warnings"])
        self.assertEqual(archive.status(self.root)["domains"][0]["session_id"], "session-1")
        (self.root / "domains/systems/sessions/session-1/profile.json").write_text("{}")
        with self.assertRaisesRegex(archive.ArchiveError, "differs"):
            archive.validate(self.root)

    def test_cli_concurrent_duplicate_has_exactly_one_winner(self):
        source = Path(self.temp.name) / "bundle.json"
        source.write_text(json.dumps(self.bundle), encoding="utf-8")
        command = [sys.executable, str(SCRIPT), "commit", "--root", str(self.root),
                   "--domain", "systems", "--bundle", str(source)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(2)]
        outputs = [process.communicate(timeout=20) for process in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0, 1], outputs)
        for action in ("status", "validate"):
            result = subprocess.run([sys.executable, str(SCRIPT), action, "--root", str(self.root)],
                                    capture_output=True, text=True, check=True)
            self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertEqual(archive.validate(self.root)["snapshots"], 1)

    def test_init_is_idempotent_and_ambiguous_json_is_rejected(self):
        original = (self.root / "person.json").read_bytes()
        archive.initialize(self.root, "测试用户")
        self.assertEqual((self.root / "person.json").read_bytes(), original)
        with self.assertRaisesRegex(archive.ArchiveError, "different person"):
            archive.initialize(self.root, "另一用户")
        source = Path(self.temp.name) / "ambiguous.json"
        source.write_text('{"a": 1, "a": 2}')
        with self.assertRaisesRegex(archive.ArchiveError, "Duplicate JSON key"):
            archive.read_json(source)

    def test_source_registry_closure_failure_preserves_current(self):
        self.bundle["profile"]["evidence_refs"] = ["sessions/earlier/work.md"]
        self.commit()  # Profile evidence is deliberately outside the source registry.
        previous = self.pointer()
        mutations = {
            "item": lambda b: b["items"][0].update(source_id="missing"),
            "topic": lambda b: b["topics"][0].update(source_ids=["missing"]),
            "node": lambda b: b["architecture"]["nodes"][0].update(source_refs=["missing"]),
            "bridge": lambda b: b["architecture"].update(bridges=[{
                "from": "systems:a", "to": "other:z", "source_refs": ["missing"]}]),
            "duplicate": lambda b: b["sources"].append(dict(b["sources"][0])),
            "unsafe": lambda b: b["sources"][0].update(id="../unsafe"),
        }
        for label, mutate in mutations.items():
            bad = copy.deepcopy(self.bundle)
            bad["session_id"] = "invalid-" + label
            mutate(bad)
            with self.subTest(label=label), self.assertRaises(archive.ArchiveError):
                self.commit(bad)
            self.assertEqual(self.pointer(), previous)
            self.assertFalse((self.root / "domains/systems/sessions" / bad["session_id"]).exists())
        minimal = copy.deepcopy(self.bundle)
        minimal["session_id"] = "without-sources"
        for key in ("sources", "items", "topics"):
            minimal.pop(key)
        self.commit(minimal)
        self.assertTrue(archive.validate(self.root)["valid"])


if __name__ == "__main__":
    unittest.main()
