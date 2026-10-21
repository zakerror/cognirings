#!/usr/bin/env python3
"""Dependency adapter tests: no network access and no real user configuration."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock


SPEC = importlib.util.spec_from_file_location("ensure_dependencies", Path(__file__).with_name("ensure_dependencies.py"))
dependency = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dependency)


def installation(root, name="last30days"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "references").mkdir(exist_ok=True)
    (root / "SKILL.md").write_text("---\nname: " + name + "\n---\nFixture\n", encoding="utf-8")
    (root / "scripts/last30days.py").write_text("# fixture\n", encoding="utf-8")
    (root / "references/runtime.md").write_text("# fixture\n", encoding="utf-8")


class DependenciesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cognirings dependency tests ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "fake home"
        self.script = self.base / "development/cognirings/scripts/ensure_dependencies.py"
        self.runner = Mock(side_effect=AssertionError("Unexpected installer invocation"))

    def check(self, *cli, **overrides):
        options = {"environ": {}, "home": self.home, "script_path": self.script,
                   "python_version": (3, 12, 0), "runner": self.runner}
        options.update(overrides)
        return dependency.ensure_dependencies(dependency.parser().parse_args(cli), **options)

    def fake_helper(self, body=None):
        helper = self.base / "official helper folder/install-skill-from-github.py"
        helper.parent.mkdir(parents=True, exist_ok=True)
        if body is None:
            body = '''import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
for key in ("repo", "path", "name", "ref", "dest"):
    p.add_argument("--" + key, required=True)
a = p.parse_args()
root = Path(a.dest) / a.name
root.mkdir(parents=True)
(root / "scripts").mkdir()
(root / "references").mkdir()
(root / "SKILL.md").write_text("---\\nname: last30days\\n---\\n")
(root / "scripts/last30days.py").write_text("# fixture\\n")
(root / "references/runtime.md").write_text("# fixture\\n")
(root / "invocation.json").write_text(json.dumps(vars(a)))
print("PRIVATE_OUTPUT_SHOULD_NOT_BE_RELAYED")
'''
        helper.write_text(body, encoding="utf-8")
        return helper

    def test_default_is_read_only_without_installation(self):
        result, code = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(result["installation"]["status"], "missing")
        self.assertFalse(self.home.exists())
        self.runner.assert_not_called()
        self.assertEqual(result["research_readiness"], "not_checked")

    def test_first_install_and_second_reuse_with_spaces(self):
        helper = self.fake_helper()
        target = self.base / "new skills folder"
        result, code = self.check("--install", "--skills-dir", str(target),
                                  "--installer", str(helper), runner=subprocess.run)
        self.assertEqual(code, 0)
        self.assertTrue(result["newly_installed"])
        self.assertEqual(result["installation"]["status"], "installed")
        self.assertEqual(result["selected_root"], str(target / "last30days"))
        invocation = json.loads((target / "last30days/invocation.json").read_text())
        self.assertEqual(invocation, {"repo": dependency.UPSTREAM_REPO,
                                     "path": dependency.UPSTREAM_PATH, "name": "last30days",
                                     "ref": dependency.UPSTREAM_REF, "dest": str(target)})
        result, code = self.check("--install", "--skills-dir", str(target), "--installer", "/missing/helper")
        self.assertEqual(code, 0)
        self.assertEqual(result["installation"]["status"], "present")
        self.assertFalse(result["newly_installed"])
        self.runner.assert_not_called()

    def test_explicit_loaded_root_and_valid_symlink(self):
        loaded = self.base / "custom host/loaded skill"
        installation(loaded)
        alias = self.base / "linked skill"
        alias.symlink_to(loaded, target_is_directory=True)
        result, code = self.check("--install", "--last30days-root", str(alias))
        self.assertEqual(code, 0)
        self.assertEqual(result["selected_root"], str(alias))
        self.runner.assert_not_called()

    def test_explicit_invalid_root_never_falls_back(self):
        installation(self.home / ".codex/skills/last30days")
        result, code = self.check("--install", "--last30days-root", str(self.base / "not here"))
        self.assertEqual(code, 1)
        self.assertEqual(result["installation"]["status"], "invalid")
        self.assertIsNone(result["selected_root"])
        self.assertEqual(len(result["checked_roots"]), 1)
        self.runner.assert_not_called()

    def test_directory_precedence_and_standard_locations(self):
        preferred = self.base / "preferred"
        sibling = self.base / "other/skills"
        codex = self.base / "custom codex home"
        paths = [preferred / "last30days", sibling / "last30days", codex / "skills/last30days",
                 self.home / ".agents/skills/last30days", self.home / ".codex/skills/last30days"]
        for root in paths:
            installation(root)
        options = {"script_path": sibling / "cognirings/scripts/ensure_dependencies.py",
                   "environ": {"CODEX_HOME": str(codex)}}
        result, code = self.check("--skills-dir", str(preferred), **options)
        self.assertEqual(result["selected_root"], str(paths[0]))
        result, code = self.check(**options)
        self.assertEqual(result["selected_root"], str(paths[1]))
        result, code = self.check(environ=options["environ"])
        self.assertEqual(result["selected_root"], str(paths[2]))
        result, code = self.check()
        self.assertEqual(result["selected_root"], str(paths[3]))
        # A directory merely called "skills_dev" must not become an install root.
        result, code = self.check(script_path=self.base / "skills_dev/cognirings/scripts/check.py")
        self.assertNotIn(str(self.base / "skills_dev/last30days"), result["checked_roots"])

    def test_damaged_installation_is_preserved(self):
        target = self.base / "skills"
        damaged = target / "last30days"
        damaged.mkdir(parents=True)
        marker = damaged / "keep.txt"
        marker.write_text("user data")
        result, code = self.check("--install", "--skills-dir", str(target))
        self.assertEqual(code, 1)
        self.assertEqual(result["installation"]["status"], "invalid")
        self.assertEqual(marker.read_text(), "user data")
        self.assertEqual(list(damaged.iterdir()), [marker])
        self.runner.assert_not_called()

    def test_wrong_identity_and_broken_symlink_are_invalid(self):
        root = self.base / "wrong"
        installation(root, "not-last30days")
        self.assertEqual(dependency.inspect_installation(root)[0], "invalid")
        broken = self.base / "broken"
        broken.symlink_to(self.base / "nonexistent", target_is_directory=True)
        self.assertEqual(dependency.inspect_installation(broken)[0], "invalid")

    def test_valid_lower_priority_installation_is_reused(self):
        damaged = self.base / "preferred/last30days"
        damaged.mkdir(parents=True)
        good = self.home / ".codex/skills/last30days"
        installation(good)
        result, code = self.check("--install", "--skills-dir", str(damaged.parent))
        self.assertEqual(code, 0)
        self.assertEqual(result["selected_root"], str(good))
        self.assertEqual(len(result["invalid_candidates"]), 1)
        self.assertTrue(damaged.is_dir())

    def test_missing_helper_does_not_create_destination(self):
        result, code = self.check("--install")
        self.assertEqual(code, 1)
        self.assertEqual(result["installation"]["status"], "installer_missing")
        self.assertFalse(self.home.exists())

    def test_helper_discovery_from_codex_home(self):
        codex = self.base / "custom codex"
        helper = codex / "skills" / dependency.INSTALLER_RELATIVE
        helper.parent.mkdir(parents=True)
        helper.write_text("# fixture")
        def run(command, **kwargs):
            self.assertEqual(command[1], str(helper))
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"], 180)
            installation(codex / "skills/last30days")
            return subprocess.CompletedProcess(command, 0)
        result, code = self.check("--install", environ={"CODEX_HOME": str(codex)}, runner=run)
        self.assertEqual(code, 0)
        self.assertTrue(result["newly_installed"])

    def test_helper_failure_and_timeout_have_no_remote_output(self):
        helper = self.fake_helper("import sys\nprint('credential=secret')\nprint('token=secret', file=sys.stderr)\nsys.exit(7)\n")
        result, code = self.check("--install", "--installer", str(helper), runner=subprocess.run)
        self.assertEqual(code, 1)
        self.assertEqual(result["installation"]["status"], "install_failed")
        self.assertIn("7", result["installation"]["detail"])
        self.assertNotIn("secret", json.dumps(result))
        runner = Mock(side_effect=subprocess.TimeoutExpired("hidden command", 180, output="token=secret"))
        result, code = self.check("--install", "--installer", str(helper), runner=runner)
        self.assertEqual(code, 1)
        self.assertIn("timeout", result["installation"]["detail"])
        self.assertNotIn("secret", json.dumps(result))

    def test_installer_success_is_verified(self):
        helper = self.fake_helper("pass\n")
        result, code = self.check("--install", "--installer", str(helper), runner=subprocess.run)
        self.assertEqual(code, 1)
        self.assertEqual(result["installation"]["status"], "install_failed")
        self.assertFalse(result["newly_installed"])

    def test_old_python_reports_setup_and_does_not_install(self):
        result, code = self.check("--install", python_version=(3, 11, 9))
        self.assertEqual(code, 2)
        self.assertEqual(result["runtime"]["status"], "runtime_needs_setup")
        self.assertEqual(result["installation"]["status"], "missing")
        self.assertFalse(self.home.exists())
        root = self.home / ".agents/skills/last30days"
        installation(root)
        result, code = self.check("--install", python_version=(3, 10, 8))
        self.assertEqual(code, 2)
        self.assertEqual(result["installation"]["status"], "present")
        self.assertEqual(result["runtime"]["guide"], str(root / "references/runtime.md"))
        self.runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
