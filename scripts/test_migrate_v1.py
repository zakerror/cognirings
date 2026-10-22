"""Isolated migration regression tests; never touch the personal archive.

Every source and destination is created under TemporaryDirectory. These tests
exercise supported v1 input, content preservation, and filesystem boundaries.
"""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import archive
import archive_v2
import migrate_v1


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / "v1"
        self.destination = self.base / "v2"
        archive.initialize(self.source, "迁移测试")
        self.bundle = {
            "session_id": "20261021-research",
            "created_at": "2026-10-21T10:00:00+00:00",
            "domain": {"id": "ai", "title": "人工智能"},
            "profile": {
                "context": [{"text": "目标是准备工程团队分享"}],
                "capabilities": [{"node_id": "judge", "status": "unknown",
                                  "next_check": "解释一个失败案例"}],
            },
            "architecture": {
                "nodes": [
                    {"id": "ai", "title": "人工智能", "definition": "知识领域"},
                    {"id": "judge", "title": "评判可靠性", "definition": "评估评判表现",
                     "source_refs": ["source-1"]},
                ],
                "edges": [{"from": "judge", "to": "ai", "type": "part_of"}],
                "bridges": [{"id": "judge-agreement",
                             "from": {"domain": "ai", "node": "judge"},
                             "to": {"domain": "statistics", "node": "agreement"},
                             "type": "analogy", "status": "proposed",
                             "source_refs": ["source-1"]}],
            },
            "session": {
                "user_feedback": [{"text": "想用它做分享", "node_ids": ["judge"]}],
                "questions": [], "profile_changes": [],
                "mentor_deliveries": [{"type": "research"}],
            },
            "report_markdown": "# 测试报告\n\n[当时画像](profile.md)\n",
            "profile_markdown": "# 当时画像\n",
            "architecture_markdown": "# 当时知识树\n",
            "storybook_markdown": "# 当时反馈\n",
            "topics": [{"id": "judge-topic", "source_ids": ["source-1"]}],
            "sources": [{"id": "source-1", "name": "原始来源", "url": "https://example.org/one"}],
            "items": [{"id": "item-1", "source_id": "source-1", "node_ids": ["judge"]}],
        }

    def commit(self, bundle=None):
        archive.commit(self.source, "ai", bundle or self.bundle)

    def migrate(self, plan=None, source=None, destination=None):
        return migrate_v1.migrate(source or self.source, destination or self.destination, plan)

    @staticmethod
    def files(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and not p.is_symlink()}

    def read_json(self, relative):
        return json.loads((self.destination / relative).read_text(encoding="utf-8"))

    def test_preserves_source_and_all_snapshot_bytes_and_research(self):
        self.commit()
        second = copy.deepcopy(self.bundle)
        second.update(session_id="20261022-followup", created_at="2026-10-22T10:00:00+00:00")
        second["report_markdown"] = "# 下一份报告\n"
        self.commit(second)
        (self.source / "research").mkdir()
        (self.source / "research/raw.bin").write_bytes(b"\x00\xff\r\nraw\x00")
        before = self.files(self.source)
        result = self.migrate()
        self.assertEqual(self.files(self.source), before)
        backup = self.destination / ".cognirings/archive/v1"
        for path, expected in before.items():
            if "/sessions/" in path or path.startswith("research/"):
                self.assertEqual((backup / path).read_bytes(), expected, path)
        self.assertEqual(result["nodes"], 2)
        self.assertEqual(result["reports"], 2)
        registry = self.read_json(".cognirings/registry/nodes.json")
        self.assertEqual({n["id"] for n in registry["nodes"]}, {"ai", "judge"})
        self.assertEqual(registry["bridges"], self.bundle["architecture"]["bridges"])
        self.assertEqual(self.read_json(".cognirings/registry/sources.json")["sources"],
                         self.bundle["sources"])
        self.assertEqual(self.read_json(".cognirings/research/items.json")["items"],
                         self.bundle["items"])

    def test_maintenance_is_excluded_from_reports_and_does_not_advance_knowledge(self):
        self.commit()
        second = copy.deepcopy(self.bundle)
        second.update(session_id="20261022-formatting", created_at="2026-10-22T10:00:00+00:00",
                      report_markdown="# 排版调整\n")
        self.commit(second)
        self.migrate({"session_types": {second["session_id"]: "maintenance"}})
        reports = self.read_json(".cognirings/registry/artifacts.json")["artifacts"]
        self.assertEqual([r["legacy_session_id"] for r in reports], [self.bundle["session_id"]])
        self.assertFalse((self.destination / "journal" / second["session_id"]).exists())
        self.assertTrue((self.destination / ".cognirings/maintenance" / second["session_id"] / "README.md").is_file())
        for node in self.read_json(".cognirings/registry/nodes.json")["nodes"]:
            self.assertEqual(node["knowledge_updated_at"], self.bundle["created_at"])

    def test_feedback_and_generated_reports_do_not_promote_personal_ability(self):
        self.commit()
        self.migrate()
        for node in self.read_json(".cognirings/registry/nodes.json")["nodes"]:
            content = (self.destination / "profile" / node["path"] / "README.md").read_text()
            self.assertIn("\nlevel: U\n", content)
            self.assertIn("\npractice: unverified\n", content)
            self.assertIn("\nevidence_ids: []\n", content)
            self.assertIn("\ncheckpoints_evidenced: 0\n", content)

    def test_existing_destination_is_rejected_without_modification(self):
        self.commit()
        self.destination.mkdir()
        marker = self.destination / "mine.txt"
        marker.write_bytes(b"existing content")
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertEqual(marker.read_bytes(), b"existing content")

    def test_destination_inside_source_is_rejected(self):
        self.commit()
        before = self.files(self.source)
        with self.assertRaises(ValueError):
            self.migrate(destination=self.source / "nested-v2")
        self.assertEqual(before, self.files(self.source))

    def test_retained_symlinks_are_rejected(self):
        self.commit()
        (self.source / "research").mkdir()
        target = self.base / "outside.txt"
        target.write_text("external")
        (self.source / "research/external.txt").symlink_to(target)
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertFalse(self.destination.exists())
        self.assertEqual(target.read_text(), "external")

    def test_symlink_source_root_is_rejected(self):
        self.commit()
        alias = self.base / "source-alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.migrate(source=alias)
        self.assertFalse(self.destination.exists())

    def test_symlink_destination_ancestor_is_rejected(self):
        self.commit()
        target = self.base / "outside"
        target.mkdir()
        alias = self.base / "destination-alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.migrate(destination=alias / "v2")
        self.assertFalse((target / "v2").exists())

    def test_unsafe_override_slug_is_rejected_and_source_unchanged(self):
        self.commit()
        before = self.files(self.source)
        for slug in ("../escape", "/absolute", "x/y", "x\\y", ".."):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                self.migrate({"node_overrides": {"judge": {"slug": slug}}})
            self.assertFalse(self.destination.exists())
        self.assertEqual(before, self.files(self.source))

    def test_colliding_node_paths_are_rejected(self):
        self.commit()
        plan = {"node_overrides": {"judge": {"slug": "ai", "parent_id": None}}}
        with self.assertRaises(ValueError):
            self.migrate(plan)
        self.assertFalse(self.destination.exists())

    def test_escaping_historical_link_is_rejected(self):
        self.bundle["report_markdown"] = "# Report\n[escape](../../../../../outside.md)\n"
        self.commit()
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertFalse(self.destination.exists())

    def test_historical_relative_link_points_to_retained_snapshot(self):
        self.commit()
        self.migrate()
        report = self.destination / "journal" / self.bundle["session_id"] / "knowledge/ai/report.md"
        text = report.read_text()
        target = text.split("[当时画像](", 1)[1].split(")", 1)[0]
        resolved = (report.parent / target).resolve()
        expected = self.destination / ".cognirings/archive/v1/domains/ai/sessions" / self.bundle["session_id"] / "profile.md"
        self.assertEqual(resolved, expected)
        self.assertEqual(resolved.read_text(), self.bundle["profile_markdown"])

    def test_absolute_archive_links_survive_original_and_copied_source_roots(self):
        research = self.source / "research/reference.md"
        research.parent.mkdir()
        research.write_text("# 原始研究\n\n## finding\n\n保留原文。\n", encoding="utf-8")
        external = self.base / "private-material.md"
        external.write_text("# 档案外的私有材料\n", encoding="utf-8")
        self.bundle["report_markdown"] = (
            "# 带有历史绝对链接的报告\n\n"
            f"[研究]({research}#finding)\n"
            f"[索引]({self.source / 'INDEX.md'})\n"
            f"[本地材料]({external})\n"
        )
        self.commit()
        before = self.files(self.source)
        copied_source = self.base / "backup-source"
        shutil.copytree(self.source, copied_source)
        self.assertEqual(json.loads((copied_source / "person.json").read_text())["archive_root"],
                         str(self.source))
        copied_before = self.files(copied_source)
        for index, source in enumerate((self.source, copied_source)):
            with self.subTest(source=source.name):
                destination = self.base / f"migrated-{index}"
                self.migrate(source=source, destination=destination)
                report = destination / "journal" / self.bundle["session_id"] / "knowledge/ai/report.md"
                text = report.read_text(encoding="utf-8")
                for label, relative, fragment in (("研究", "research/reference.md", "finding"),
                                                   ("索引", "INDEX.md", "")):
                    target = text.split(f"[{label}](", 1)[1].split(")", 1)[0]
                    path, _, actual_fragment = target.partition("#")
                    self.assertFalse(Path(path).is_absolute())
                    self.assertEqual(actual_fragment, fragment)
                    resolved = (report.parent / path).resolve()
                    retained = destination / ".cognirings/archive/v1" / relative
                    expected = destination / "README.md" if relative == "INDEX.md" else retained
                    self.assertEqual(resolved, expected)
                    self.assertTrue(resolved.is_file())
                    self.assertEqual(retained.read_bytes(), before[relative])
                self.assertIn(f"[本地材料]({external})", text)
                self.assertEqual(self.files(self.source), before)
                self.assertEqual(self.files(copied_source), copied_before)
        self.assertEqual(external.read_text(encoding="utf-8"), "# 档案外的私有材料\n")

    def test_historical_unique_sources_and_items_are_retained_in_registries(self):
        self.commit()
        second = copy.deepcopy(self.bundle)
        second.update(session_id="20261022-followup", created_at="2026-10-22T10:00:00+00:00")
        second["architecture"]["nodes"][1]["source_refs"] = ["source-2"]
        second["architecture"]["bridges"][0]["source_refs"] = ["source-2"]
        second["sources"] = [{"id": "source-2", "name": "新来源", "url": "https://example.org/two"}]
        second["items"] = [{"id": "item-2", "source_id": "source-2", "node_ids": ["judge"]}]
        second["topics"][0]["source_ids"] = ["source-2"]
        self.commit(second)
        self.migrate()
        sources = self.read_json(".cognirings/registry/sources.json")["sources"]
        items = self.read_json(".cognirings/research/items.json")["items"]
        self.assertEqual({s["id"] for s in sources}, {"source-1", "source-2"})
        self.assertEqual({i["id"] for i in items}, {"item-1", "item-2"})
        source_ids = {s["id"] for s in sources}
        for report in self.read_json(".cognirings/registry/artifacts.json")["artifacts"]:
            self.assertTrue(set(report["source_ids"]) <= source_ids)

    def test_empty_deliveries_and_null_feedback_are_supported_v1_input(self):
        self.bundle["session"].update(mentor_deliveries=[], user_feedback=None)
        self.commit()
        self.assertEqual(self.migrate()["reports"], 1)

    def test_null_feedback_without_items_keeps_uncalibrated_domain_report(self):
        self.bundle["session"]["user_feedback"] = None
        self.bundle["session"]["profile_changes"] = None
        self.bundle["session"].pop("mentor_deliveries")
        self.bundle["items"] = []
        self.commit()
        self.migrate()
        artifacts = self.read_json(".cognirings/registry/artifacts.json")["artifacts"]
        self.assertEqual(artifacts[0]["node_ids"], ["ai"])
        self.assertEqual(self.read_json(".cognirings/evidence/records.json")["evidence"], [])
        reflection = self.destination / "journal" / self.bundle["session_id"] / "profile/ai/reflection.md"
        self.assertIn("尚未收到", reflection.read_text(encoding="utf-8"))

    def test_interrupted_v1_staging_directory_is_not_a_historical_session(self):
        self.commit()
        staging = self.source / "domains/ai/sessions/.stage-interrupted"
        staging.mkdir()
        (staging / "bundle.json").write_text("incomplete")
        self.assertTrue(archive.validate(self.source)["valid"])
        self.assertEqual(self.migrate()["reports"], 1)

    def test_same_session_id_across_domains_requires_explicit_disambiguation(self):
        self.commit()
        second = copy.deepcopy(self.bundle)
        second["domain"] = {"id": "statistics", "title": "统计学"}
        second["architecture"] = {"nodes": [{"id": "statistics", "title": "统计学"}], "edges": []}
        second["session"]["user_feedback"] = []
        second["items"][0]["node_ids"] = ["statistics"]
        archive.commit(self.source, "statistics", second)
        with self.assertRaises(ValueError):
            self.migrate()
        self.assertFalse(self.destination.exists())

    def test_minimal_valid_v1_nodes_sources_and_empty_report_are_supported(self):
        for node in self.bundle["architecture"]["nodes"]:
            node.pop("title")
        self.bundle["sources"][0].pop("name")
        self.bundle["report_markdown"] = ""
        self.commit()
        self.assertEqual(self.migrate()["nodes"], 2)

    def test_reviewed_migration_accepts_followup_report_and_feedback_then_refreshes(self):
        self.commit()
        source_before = self.files(self.source)
        self.migrate()
        archive_v2.freeze(self.destination, self.bundle["session_id"],
                          "已审阅的历史报告", self.bundle["created_at"])
        archive_v2.refresh(self.destination)
        first_journal = self.destination / "journal" / self.bundle["session_id"]
        old_history = self.files(first_journal)
        old_profile = (self.destination / "profile/ai/judge/README.md").read_bytes()
        sid = "20261022-new-report"
        report_path = f"journal/{sid}/knowledge/ai/judge/report.md"
        reflection_path = f"journal/{sid}/profile/ai/judge/reflection.md"
        next_bundle = {
            "schema_version": 2, "session_id": sid,
            "title": "新一轮评判可靠性研究", "created_at": "2026-10-22T15:00:00+00:00",
            "files": {
                "README.md": "# 新一轮评判可靠性研究\n\n[报告](knowledge/ai/judge/report.md)\n",
                "knowledge/ai/judge/report.md": "# 新的评判可靠性报告\n\n本轮比较人工核验与自动评判。\n",
                "profile/ai/judge/reflection.md": "# 本轮反馈\n\n用户希望下一轮分析一个真实失败案例。\n",
            },
            "artifacts": [{
                "id": "followup-report", "title": "新的评判可靠性报告", "path": report_path,
                "type": "report", "status": "published", "created_at": "2026-10-22T15:00:00+00:00",
                "node_ids": ["judge"], "source_ids": ["source-1"],
                "summary": "比较两种评判方式并明确下一轮的验证问题。",
            }],
            "evidence": [{
                "id": "followup-feedback", "node_id": "judge", "path": reflection_path,
                "kind": "self_reported", "assistance": "independent",
                "observed_at": "2026-10-22T15:00:00+00:00",
                "scope": "用户提出希望分析失败案例的目标，不作为能力升级证据。",
            }],
        }
        self.assertTrue(archive_v2.record(self.destination, next_bundle)["recorded"])
        self.assertEqual(old_profile, (self.destination / "profile/ai/judge/README.md").read_bytes())
        knowledge = self.destination / "knowledge/ai/judge/README.md"
        knowledge.write_text(knowledge.read_text(encoding="utf-8") +
                             "\n## 新的综合结论\n\n比较结果需要结合人工核验检查失败条件。\n",
                             encoding="utf-8")
        nodes_path = self.destination / ".cognirings/registry/nodes.json"
        nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
        next(n for n in nodes["nodes"] if n["id"] == "judge")["knowledge_updated_at"] = next_bundle["created_at"]
        nodes_path.write_text(archive_v2.encoded(nodes), encoding="utf-8")
        archive_v2.refresh(self.destination)
        result = archive_v2.validate(self.destination)
        self.assertEqual((result["artifacts"], result["sessions"]), (2, 2))
        self.assertEqual(self.files(first_journal), old_history)
        self.assertEqual(self.files(self.source), source_before)
        self.assertIn("新的评判可靠性报告", (self.destination / "README.md").read_text(encoding="utf-8"))
        stats = self.read_json(".cognirings/state/nodes.json")["nodes"]
        self.assertEqual(stats["judge"]["counts"]["report"], 2)
        self.assertEqual(stats["ai"]["counts"]["report"], 2)
        self.assertEqual(stats["judge"]["level"], "U")
        self.assertEqual(stats["judge"]["practice"], "unverified")
        self.assertEqual(stats["judge"]["knowledge_updated_at"], next_bundle["created_at"])


if __name__ == "__main__":
    unittest.main()
