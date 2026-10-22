#!/usr/bin/env python3
"""Behavioral checks for v2 safety, evidence, immutable history and projections."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import archive_v2 as archive


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "archive"
        archive.initialize(self.root, "Reader")

    def tearDown(self):
        self.temp.cleanup()

    def node(self, key="ai", path="ai", parent=None):
        return {"id": key, "title": key, "path": path, "parent_id": parent,
                "domain": path.split("/")[0], "knowledge_updated_at": "2026-10-01"}

    def bundle(self, sid="20261022-test", nodes=None, report_count=1):
        files = {"README.md": "# A real learning session\n"}
        artifacts = []
        for number in range(report_count):
            filename = f"knowledge/ai/report-{number}.md"
            files[filename] = f"# Report {number}\n\nA useful finding.\n"
            artifacts.append({"id": f"{sid}-report-{number}", "title": f"Report {number}",
                "path": f"journal/{sid}/{filename}", "type": "report", "status": "published",
                "created_at": f"2026-10-{number + 1:02d}T12:00:00Z", "node_ids": ["ai"], "source_ids": []})
        return {"schema_version": 2, "session_id": sid, "title": "Test session",
                "created_at": "2026-10-22T12:00:00Z", "files": files, "artifacts": artifacts,
                "nodes": [self.node()] if nodes is None else nodes, "sources": [], "evidence": []}

    def digest(self):
        return {p.relative_to(self.root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def edit_registry(self, key, change):
        path = self.root / archive.REGISTRIES[key]
        data = json.loads(path.read_text())
        change(data)
        path.write_text(archive.encoded(data))

    def test_validate_is_read_only(self):
        archive.record(self.root, self.bundle())
        before = self.digest()
        archive.validate(self.root)
        self.assertEqual(before, self.digest())

    def test_refresh_preserves_prose_and_history_and_is_idempotent(self):
        archive.record(self.root, self.bundle())
        page = self.root / "knowledge/ai/README.md"
        page.write_text("# My carefully written synthesis\n\nHuman explanation.\n")
        profile = self.root / "profile/ai/README.md"
        profile.write_text(profile.read_text() + "\nMy personal goal remains.\n")
        history = archive.hashes(self.root / "journal/20261022-test")
        archive.refresh(self.root)
        self.assertTrue(page.read_text().startswith("# My carefully written synthesis\n\nHuman explanation."))
        self.assertIn("My personal goal remains.", profile.read_text())
        self.assertEqual(history, archive.hashes(self.root / "journal/20261022-test"))
        self.assertEqual([], archive.refresh(self.root)["changed"])

    def test_latest_ten_filters_and_parent_deduplication(self):
        bundle = self.bundle(nodes=[self.node(), self.node("judge", "ai/judge", "ai")], report_count=13)
        for artifact in bundle["artifacts"]:
            artifact["node_ids"] = ["ai", "judge"]
        bundle["artifacts"][0]["type"] = "case"
        bundle["artifacts"][1]["type"] = "share"
        bundle["artifacts"][2]["status"] = "draft"
        archive.record(self.root, bundle)
        archive.refresh(self.root)
        state = json.loads((self.root / ".cognirings/state/nodes.json").read_text())["nodes"]
        self.assertEqual({"report": 10, "case": 1, "share": 1}, state["ai"]["counts"])
        self.assertEqual(12, len(state["ai"]["artifact_ids"]))
        recent = json.loads((self.root / ".cognirings/state/overview.json").read_text())["recent_report_ids"]
        self.assertEqual(10, len(recent))
        self.assertTrue(recent[0].endswith("-12"))
        self.assertTrue(all(not item.endswith(("-0", "-1", "-2")) for item in recent))
        self.assertIn("profile/ai/judge/README.md", (self.root / "README.md").read_text())

    def test_branch_dates_coverage_and_human_readable_metrics(self):
        parent = dict(self.node(), content_status="synthesis")
        child = dict(self.node("judge", "ai/judge", "ai"), knowledge_updated_at="2026-10-22", content_status="foundation")
        archive.record(self.root, self.bundle(nodes=[parent, child]))
        archive.refresh(self.root)
        state = json.loads((self.root / ".cognirings/state/nodes.json").read_text())["nodes"]
        self.assertEqual("2026-10-22", state["ai"]["knowledge_updated_at"])
        self.assertEqual("2026-10-01", state["ai"]["own_knowledge_updated_at"])
        branch_text = archive.metric_line(state["ai"])
        self.assertIn("已有综述", branch_text)
        self.assertIn("叶子校准：0/1", branch_text)
        self.assertIn("实践：未验证", branch_text)
        self.assertIn("检查点待设定", branch_text)
        self.assertNotIn("0项中", branch_text)
        leaf_text = archive.metric_line(state["judge"])
        self.assertIn("理解：U", leaf_text)
        self.assertIn("基础卡", leaf_text)
        hypothetical = dict(state["judge"], checkpoints_total=4, checkpoints_evidenced=2, practice="assisted", content_status="outline")
        self.assertIn("4项检查点：2项有证据、2项待验证", archive.metric_line(hypothetical))
        self.assertIn("辅助完成", archive.metric_line(hypothetical))
        self.assertIn("待扩展", archive.metric_line(hypothetical))

    def test_recent_topics_are_bounded(self):
        nodes = [self.node()] + [self.node(f"topic-{i}", f"ai/topic-{i}", "ai") for i in range(38)]
        bundle = self.bundle(nodes=nodes)
        bundle["artifacts"][0]["node_ids"] = [node["id"] for node in nodes]
        archive.record(self.root, bundle)
        archive.refresh(self.root)
        text = (self.root / "README.md").read_text()
        recent = text.split("<!-- cognirings:recent:start -->")[1].split("<!-- cognirings:recent:end -->")[0]
        self.assertIn("等39个主题", recent)
        self.assertIn("topic-0、topic-1、topic-2", recent)
        self.assertNotIn("topic-3", recent)

    def test_tree_uses_sibling_order_dfs_and_compact_metrics(self):
        nodes = [self.node(), dict(self.node("z-first", "ai/z-first", "ai"), order=0),
                 self.node("nested", "ai/z-first/nested", "z-first"),
                 dict(self.node("a-second", "ai/a-second", "ai"), order=1),
                 self.node("b-unsorted", "ai/b-unsorted", "ai")]
        archive.record(self.root, self.bundle(nodes=nodes))
        archive.refresh(self.root)
        text = (self.root / "README.md").read_text()
        tree = text.split("<!-- cognirings:tree:start -->")[1].split("<!-- cognirings:tree:end -->")[0]
        positions = [tree.index(f"[{name}]") for name in ("ai", "z-first", "nested", "a-second", "b-unsorted")]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("知识2026-10-01", tree)
        self.assertIn("无正式成果", tree)
        self.assertIn("叶子校准", tree)
        self.assertNotIn("证据确认", tree)
        self.assertNotIn("实践：", tree)
        self.assertNotIn("检查点待设定", tree)
        self.assertNotIn("目标待设定", tree)
        detailed = (self.root / "knowledge/ai/README.md").read_text()
        self.assertIn("证据确认", detailed)
        self.assertIn("实践：", detailed)

    def test_record_rejects_history_overwrite_and_path_escape(self):
        archive.record(self.root, self.bundle())
        before = self.digest()
        with self.assertRaises(archive.ArchiveError):
            archive.record(self.root, self.bundle())
        self.assertEqual(before, self.digest())
        for bad in ("../escape.md", "/tmp/escape.md", "x/../../escape.md", "x\\escape.md"):
            bundle = self.bundle("bad", nodes=[])
            bundle["files"][bad] = "bad"
            with self.assertRaises(archive.ArchiveError):
                archive.record(self.root, bundle)
        self.assertEqual(before, self.digest())

    def test_invalid_staged_bundle_does_not_change_archive(self):
        before = self.digest()
        bundle = self.bundle()
        bundle["artifacts"][0]["node_ids"] = ["missing-node"]
        with self.assertRaises(archive.ArchiveError):
            archive.record(self.root, bundle)
        self.assertEqual(before, self.digest())

    def test_duplicate_original_and_missing_source_are_rejected(self):
        before = self.digest()
        bundle = self.bundle()
        duplicate = dict(bundle["artifacts"][0], id="another-id")
        bundle["artifacts"].append(duplicate)
        with self.assertRaisesRegex(archive.ArchiveError, "registered twice"):
            archive.record(self.root, bundle)
        bundle["artifacts"].pop()
        bundle["artifacts"][0]["source_ids"] = ["unregistered"]
        with self.assertRaisesRegex(archive.ArchiveError, "Unknown artifact source"):
            archive.record(self.root, bundle)
        self.assertEqual(before, self.digest())

    def test_publish_failure_rolls_back_and_same_id_can_be_retried(self):
        before = self.digest()
        original = archive.atomic_write
        failed = False

        def inject(path, text):
            nonlocal failed
            if path == self.root / archive.REGISTRIES["evidence"] and not failed:
                failed = True
                raise OSError("Simulated disk failure during installation")
            return original(path, text)

        with mock.patch.object(archive, "atomic_write", side_effect=inject):
            with self.assertRaises(OSError):
                archive.record(self.root, self.bundle())
        # Rollback restores exact contents. Replaced files may have new mtimes.
        after = self.digest()
        self.assertEqual({k: v[0] for k, v in before.items()}, {k: v[0] for k, v in after.items()})
        self.assertFalse((self.root / "journal/20261022-test").exists())
        archive.validate(self.root)
        archive.record(self.root, self.bundle())
        archive.validate(self.root)

    def test_journal_tampering_cannot_be_refrozen(self):
        archive.record(self.root, self.bundle())
        target = self.root / "journal/20261022-test/knowledge/ai/report-0.md"
        target.write_text("Tampered with original report")
        with self.assertRaisesRegex(archive.ArchiveError, "Immutable journal changed"):
            archive.validate(self.root)
        with self.assertRaisesRegex(archive.ArchiveError, "Immutable journal changed"):
            archive.freeze(self.root, "20261022-test")

    def test_symlink_escape_is_rejected(self):
        archive.record(self.root, self.bundle())
        page = self.root / "knowledge/ai/README.md"
        page.unlink()
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("Outside archive")
        page.symlink_to(outside)
        with self.assertRaisesRegex(archive.ArchiveError, "Symlink"):
            archive.validate(self.root)
        with self.assertRaisesRegex(archive.ArchiveError, "Symlink"):
            archive.refresh(self.root)
        self.assertEqual("Outside archive", outside.read_text())

    def test_unknown_and_evidence_rules(self):
        bundle = self.bundle()
        bundle["files"]["profile/ai/reflection.md"] = "User wants to prepare a talk.\n"
        bundle["evidence"] = [{"id": "goal", "node_id": "ai", "path": "journal/20261022-test/profile/ai/reflection.md",
            "kind": "self_reported", "assistance": "independent", "observed_at": "2026-10-22", "scope": "User goal only"}]
        archive.record(self.root, bundle)
        page = self.root / "profile/ai/README.md"
        text = page.read_text().replace("evidence_ids: []", 'evidence_ids: ["goal"]')
        page.write_text(text)
        archive.validate(self.root)  # A goal may be recorded while understanding stays U.
        page.write_text(text.replace("level: U", "level: L1").replace("confirmed_at: null", "confirmed_at: 2026-10-22"))
        with self.assertRaisesRegex(archive.ArchiveError, "observed evidence"):
            archive.validate(self.root)
        self.edit_registry("evidence", lambda d: d["evidence"][0].update(kind="observed", scope="Independently explained the concept"))
        archive.validate(self.root)
        page.write_text(page.read_text().replace('evidence_ids: ["goal"]', "evidence_ids: []"))
        with self.assertRaisesRegex(archive.ArchiveError, "requires evidence"):
            archive.validate(self.root)

    def test_prerequisite_only_dag_and_mirrored_paths(self):
        bundle = self.bundle(nodes=[self.node(), self.node("judge", "ai/judge", "ai")])
        bundle["edges"] = [{"from": "ai", "to": "judge", "type": "related"},
                           {"from": "judge", "to": "ai", "type": "related"}]
        archive.record(self.root, bundle)
        archive.validate(self.root)
        self.edit_registry("nodes", lambda d: [e.update(type="prerequisite") for e in d["edges"]])
        with self.assertRaisesRegex(archive.ArchiveError, "DAG"):
            archive.validate(self.root)
        self.edit_registry("nodes", lambda d: d.update(edges=[]))
        (self.root / "profile/ai/judge/README.md").unlink()
        with self.assertRaisesRegex(archive.ArchiveError, "mirrored"):
            archive.validate(self.root)

    def test_bridges_preserve_source_closure_and_prerequisite_dag(self):
        bundle = self.bundle(nodes=[self.node(), self.node("stats", "stats")])
        bundle["sources"] = [{"id": "paper", "url": "https://example.org/paper"}]
        bridge = {"id": "candidate", "from": {"domain": "ai", "node": "ai"},
                  "to": {"domain": "measurement", "node": "agreement"},
                  "type": "analogy", "status": "proposed", "source_refs": ["paper"]}
        bundle["bridges"] = [bridge]
        archive.record(self.root, bundle)
        self.assertEqual(1, archive.validate(self.root)["bridges"])
        self.edit_registry("nodes", lambda d: d["bridges"][0].update(status="active"))
        with self.assertRaisesRegex(archive.ArchiveError, "proposed status"):
            archive.validate(self.root)
        self.edit_registry("nodes", lambda d: d["bridges"][0].update(status="proposed", source_refs=["missing"]))
        with self.assertRaisesRegex(archive.ArchiveError, "Unknown bridge source"):
            archive.validate(self.root)
        self.edit_registry("nodes", lambda d: d["bridges"][0].update(source_refs=["paper"], to={"domain": "wrong", "node": "stats"}))
        with self.assertRaisesRegex(archive.ArchiveError, "domain mismatch"):
            archive.validate(self.root)
        self.edit_registry("nodes", lambda d: d["bridges"][0].update(type="prerequisite", to={"domain": "stats", "node": "stats"}))
        self.edit_registry("nodes", lambda d: d["edges"].append({"from": "stats", "to": "ai", "type": "prerequisite"}))
        with self.assertRaisesRegex(archive.ArchiveError, "DAG"):
            archive.validate(self.root)
        self.edit_registry("nodes", lambda d: d.update(edges=[]))
        self.edit_registry("nodes", lambda d: d["bridges"][0].update(to="legacy:stats"))
        with self.assertRaisesRegex(archive.ArchiveError, "legacy bridges"):
            archive.validate(self.root)

    def test_cli_defaults_to_v2_and_rejects_v1_commit(self):
        target = Path(self.temp.name) / "cli"
        script = str(Path(__file__).with_name("archive.py"))
        result = subprocess.run([sys.executable, script, "init", "--root", str(target), "--person-name", "Reader"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, json.loads(result.stdout)["schema_version"])
        result = subprocess.run([sys.executable, script, "init", "--root", str(target),
                                 "--person-name", "Reader", "--schema-version", "1"], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((target / "person.json").exists())
        bundle = Path(self.temp.name) / "bundle.json"
        bundle.write_text(archive.encoded(self.bundle()))
        result = subprocess.run([sys.executable, script, "record", "--root", str(target), "--bundle", str(bundle)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        result = subprocess.run([sys.executable, script, "commit", "--root", str(target), "--domain", "ai", "--bundle", str(bundle)], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Use record", result.stderr)

    def test_concurrent_duplicate_has_one_winner(self):
        source = Path(self.temp.name) / "bundle.json"
        source.write_text(archive.encoded(self.bundle()))
        command = [sys.executable, str(Path(__file__).with_name("archive.py")), "record",
                   "--root", str(self.root), "--bundle", str(source)]
        processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(2)]
        outputs = [process.communicate(timeout=30) for process in processes]
        self.assertEqual([0, 1], sorted(process.returncode for process in processes), outputs)
        self.assertEqual(1, archive.validate(self.root)["sessions"])


if __name__ == "__main__":
    unittest.main()
