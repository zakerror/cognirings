#!/usr/bin/env python3
"""Check last30days; optionally ask the official skill-installer to install it.

This adapter does not download, update, configure, or run upstream research.
Exit codes: 0 = installation and current Python pass these limited checks;
1 = installation needs attention; 2 = current Python needs setup.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


UPSTREAM_REPO = "mvanhorn/last30days-skill"
UPSTREAM_PATH = "skills/last30days"
UPSTREAM_REF = "832d09591568e37dd990ccb9d5c51fb4a403f5ed"
MIN_PYTHON = (3, 12)
INSTALLER_RELATIVE = Path(".system/skill-installer/scripts/install-skill-from-github.py")
REQUIRED_FILES = ("SKILL.md", "scripts/last30days.py", "references/runtime.md")


def absolute_path(value):
    # Preserve symlink paths: a host may register a symlinked skill installation.
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def distinct_paths(paths):
    result = []
    seen = set()
    for value in paths:
        path = absolute_path(value)
        identity = os.path.normcase(str(path))
        if identity not in seen:
            result.append(path)
            seen.add(identity)
    return result


def inspect_installation(root):
    """Return (missing|valid|invalid, concise reason), accepting valid symlinks."""
    try:
        if not root.exists() and not root.is_symlink():
            return "missing", "No installation at this path."
        if not root.is_dir():
            return "invalid", "The installation path is not a readable directory."
        for relative in REQUIRED_FILES:
            if not (root / relative).is_file():
                return "invalid", "Required file is missing: " + relative
        # The upstream name is a plain YAML scalar. Parse only this identity field,
        # without importing YAML or interpreting any other instructions or values.
        content = (root / "SKILL.md").read_text(encoding="utf-8-sig")
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            return "invalid", "SKILL.md is missing YAML frontmatter."
        end = next((i for i, line in enumerate(lines[1:], 1)
                    if line.strip() == "---"), None)
        if end is None:
            return "invalid", "SKILL.md has unterminated YAML frontmatter."
        names = [line for line in lines[1:end] if re.match(r"^name\s*:", line)]
        if len(names) != 1 or not re.fullmatch(
                r"name\s*:\s*(?:last30days|'last30days'|\"last30days\")\s*(?:#.*)?",
                names[0]):
            return "invalid", "SKILL.md must declare name: last30days exactly once."
        return "valid", "Required files and skill identity are present."
    except (OSError, UnicodeError):
        return "invalid", "The installation could not be read."


def candidate_paths(args, environ, home, script_path):
    own_skill = absolute_path(script_path).parent.parent
    siblings = own_skill.parent if own_skill.parent.name == "skills" else None
    codex_home = environ.get("CODEX_HOME")
    codex_skills = absolute_path(codex_home) / "skills" if codex_home else None
    roots = []
    if args.skills_dir:
        roots.append(absolute_path(args.skills_dir) / "last30days")
    if siblings:
        roots.append(siblings / "last30days")
    if codex_skills:
        roots.append(codex_skills / "last30days")
    roots.extend((home / ".agents/skills/last30days", home / ".codex/skills/last30days"))
    if args.skills_dir:
        destination = absolute_path(args.skills_dir)
    elif siblings:
        destination = siblings
    else:
        destination = codex_skills or home / ".codex/skills"
    helpers = []
    if codex_skills:
        helpers.append(codex_skills / INSTALLER_RELATIVE)
    if siblings:
        helpers.append(siblings / INSTALLER_RELATIVE)
    helpers.extend((destination / INSTALLER_RELATIVE,
                    home / ".codex/skills" / INSTALLER_RELATIVE,
                    home / ".agents/skills" / INSTALLER_RELATIVE))
    return distinct_paths(roots), absolute_path(destination), distinct_paths(helpers)


def ensure_dependencies(args, *, environ=None, home=None, script_path=None,
                        python_version=None, python_executable=None, runner=None):
    environ = os.environ if environ is None else environ
    home = absolute_path(Path.home() if home is None else home)
    script_path = __file__ if script_path is None else script_path
    version = tuple(sys.version_info[:3] if python_version is None else python_version)
    executable = sys.executable if python_executable is None else python_executable
    runner = subprocess.run if runner is None else runner
    roots, destination, helpers = candidate_paths(args, environ, home, script_path)
    runtime_ok = version[:2] >= MIN_PYTHON
    result = {
        "schema_version": 1,
        "dependency": {"name": "last30days", "repository": UPSTREAM_REPO,
                       "path": UPSTREAM_PATH, "new_install_ref": UPSTREAM_REF},
        "installation": {"status": "missing"},
        "runtime": {"status": "ready" if runtime_ok else "runtime_needs_setup",
                    "scope": "current_interpreter_only", "minimum_python": "3.12",
                    "python_version": ".".join(str(part) for part in version),
                    "python_executable": executable},
        "selected_root": None,
        "newly_installed": False,
        "research_readiness": "not_checked",
        "install_destination": str(destination),
        "checked_roots": [],
        "invalid_candidates": [],
    }

    def finish():
        valid = result["installation"]["status"] in ("present", "installed")
        if not runtime_ok:
            result["runtime"]["next_action"] = (
                "Follow the upstream runtime guide to select or configure Python 3.12+, "
                "then rerun this script with that interpreter."
            )
            result["runtime"]["guide"] = (
                str(Path(result["selected_root"]) / "references/runtime.md")
                if result["selected_root"] else
                "https://github.com/" + UPSTREAM_REPO + "/blob/" + UPSTREAM_REF +
                "/" + UPSTREAM_PATH + "/references/runtime.md"
            )
        return result, (2 if not runtime_ok else 0 if valid else 1)

    if args.last30days_root:
        # An explicitly loaded path is authoritative, even if another copy exists.
        roots = [absolute_path(args.last30days_root)]
    for root in roots:
        state, reason = inspect_installation(root)
        result["checked_roots"].append(str(root))
        if state == "valid":
            result["selected_root"] = str(root)
            result["installation"] = {"status": "present", "detail": reason}
            return finish()
        if state == "invalid":
            result["invalid_candidates"].append({"root": str(root), "detail": reason})
        if args.last30days_root:
            result["installation"] = {
                "status": "invalid", "detail": "The explicit --last30days-root is " + state + ". " + reason,
                "next_action": "Correct the explicit path; no other installation was selected or changed."
            }
            return finish()

    if result["invalid_candidates"]:
        result["installation"] = {
            "status": "invalid", "detail": "An incomplete or invalid installation exists.",
            "next_action": "Inspect the listed path(s); nothing was overwritten or deleted."
        }
        return finish()
    if not args.install:
        result["installation"]["next_action"] = "Rerun with --install to install the pinned upstream dependency."
        return finish()
    if not runtime_ok:
        result["installation"]["detail"] = "Installation was deferred until the current Python meets the runtime requirement."
        return finish()

    if args.installer:
        helpers = [absolute_path(args.installer)]
    helper = next((path for path in helpers if path.is_file()), None)
    if helper is None:
        result["installation"] = {
            "status": "installer_missing", "detail": "The official skill-installer helper was not found.",
            "next_action": "Pass its existing install-skill-from-github.py path with --installer.",
            "checked_helpers": [str(path) for path in helpers],
        }
        return finish()
    # The official installer creates directories and rejects occupied destinations.
    # Never relay its output: git/HTTP errors may contain credentials or remote text.
    command = [executable, str(helper), "--repo", UPSTREAM_REPO,
               "--path", UPSTREAM_PATH, "--name", "last30days", "--ref", UPSTREAM_REF,
               "--dest", str(destination)]
    result["installation"]["installer"] = str(helper)
    try:
        process = runner(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         timeout=180, check=False)
    except subprocess.TimeoutExpired:
        result["installation"].update(status="install_failed", detail="Official installer exceeded the 180-second timeout.")
        return finish()
    except OSError:
        result["installation"].update(status="install_failed", detail="Could not execute the official installer.")
        return finish()
    if process.returncode != 0:
        result["installation"].update(
            status="install_failed", detail="Official installer failed (exit code %s)." % process.returncode,
            next_action="Inspect the installation destination and official installer setup; no cleanup or retry was attempted.")
        return finish()
    installed = destination / "last30days"
    state, reason = inspect_installation(installed)
    if state != "valid":
        result["installation"].update(status="install_failed", detail="Installer returned success, but validation failed. " + reason)
        return finish()
    result["installation"].update(status="installed", detail=reason)
    result["selected_root"] = str(installed)
    result["newly_installed"] = True
    return finish()


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--install", action="store_true", help="Install only if no valid or damaged existing installation is found.")
    cli.add_argument("--skills-dir", help="Preferred search directory and destination for a new installation.")
    cli.add_argument("--last30days-root", help="Authoritative path of an existing, host-loaded last30days skill.")
    cli.add_argument("--installer", help="Path to the existing official skill-installer helper script.")
    return cli


def main():
    result, exit_code = ensure_dependencies(parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
