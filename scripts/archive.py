#!/usr/bin/env python3
"""CogniRings archives: v2 knowledge/profile/journal, with v1 compatibility."""
import argparse
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


class ArchiveError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ArchiveError(message)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value),
            f"Unsafe identifier: {value!r}")
    return value


def child(parent, name):
    path = parent / name
    require(not path.is_symlink(), f"Symlink is not allowed: {path}")
    return path


def read_json(path):
    require(not path.is_symlink(), f"Symlink is not allowed: {path}")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, parse_constant=reject_constant, object_pairs_hook=unique_object)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_person(root):
    person = read_json(child(root, "person.json"))
    require(isinstance(person, dict) and isinstance(person.get("name"), str)
            and person["name"].strip(), "Invalid person.json: expected an object with a name")
    return person


def reject_constant(value):
    raise ArchiveError(f"Non-finite JSON value: {value}")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path, text):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_write(path, text):
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # The replacement is already committed; do not report a failed transaction.
        try:
            sync_directory(path.parent)
        except OSError as exc:
            return f"Committed, but directory durability could not be confirmed: {exc}"
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return None


@contextmanager
def lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def check_sources(bundle):
    if not isinstance(bundle.get("sources"), list):
        return
    registry = set()
    for source in bundle["sources"]:
        require(isinstance(source, dict), "Each source must be an object")
        source_id = identifier(source.get("id"))
        require(source_id not in registry, f"Duplicate source: {source_id}")
        registry.add(source_id)
    graph = bundle["architecture"]
    groups = ((bundle.get("items", []), "source_id"), (bundle.get("topics", []), "source_ids"),
              (graph["nodes"], "source_refs"), (graph.get("bridges", []), "source_refs"))
    for records, field in groups:
        require(isinstance(records, list), f"Collection containing {field} must be an array")
        for record in records:
            require(isinstance(record, dict), f"Record containing {field} must be an object")
            if field not in record:
                continue
            refs = [record[field]] if field == "source_id" else record[field]
            require(isinstance(refs, list), f"{field} must be an array")
            for ref in refs:
                require(identifier(ref) in registry, f"Missing source registry entry: {ref} ({field})")


def check_bundle(bundle, domain):
    require(isinstance(bundle, dict), "Bundle must be an object")
    identifier(bundle.get("session_id"))
    created = bundle.get("created_at")
    require(isinstance(created, str) and bool(created), "created_at must be ISO 8601 text")
    try:
        datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchiveError("created_at must be ISO 8601 text") from exc
    info = bundle.get("domain")
    require(isinstance(info, dict) and info.get("id") == domain, "domain.id must match --domain")
    identifier(info["id"])
    require(isinstance(info.get("title"), str) and info["title"].strip(), "domain.title is required")
    for key in ("profile", "architecture", "session"):
        require(isinstance(bundle.get(key), dict), f"{key} must be an object")
    for key in ("user_feedback", "questions", "profile_changes"):
        require(key in bundle["session"], f"session.{key} is required (null/empty is allowed)")
    require(isinstance(bundle.get("report_markdown"), str), "report_markdown must be text")
    for key in ("profile_markdown", "architecture_markdown", "storybook_markdown"):
        if key in bundle:
            require(isinstance(bundle[key], str) and bundle[key].strip(), f"{key} must be nonempty text")
    graph = bundle["architecture"]
    require(isinstance(graph.get("nodes"), list) and isinstance(graph.get("edges"), list),
            "architecture.nodes and architecture.edges must be arrays")
    require(isinstance(graph.get("bridges", []), list), "architecture.bridges must be an array")
    nodes = set()
    for node in graph["nodes"]:
        require(isinstance(node, dict), "Each node must be an object")
        node_id = identifier(node.get("id"))
        require(node_id not in nodes, f"Duplicate node: {node_id}")
        nodes.add(node_id)
    outgoing, indegree = {n: [] for n in nodes}, dict.fromkeys(nodes, 0)
    for edge in graph["edges"]:
        require(isinstance(edge, dict), "Each edge must be an object")
        source, target = identifier(edge.get("from")), identifier(edge.get("to"))
        require(source in nodes and target in nodes, f"Missing local endpoint: {source} -> {target}")
        require(isinstance(edge.get("type"), str) and edge["type"].strip(), "Edge type is required")
        if edge["type"] == "prerequisite":
            outgoing[source].append(target)
            indegree[target] += 1
    queue, visited = deque(n for n in nodes if indegree[n] == 0), 0
    while queue:
        visited += 1
        for target in outgoing[queue.popleft()]:
            indegree[target] -= 1
            if not indegree[target]:
                queue.append(target)
    require(visited == len(nodes), "prerequisite edges must form a DAG")
    check_sources(bundle)
    encoded(bundle)  # Reject values that cannot be preserved as strict JSON.


def snapshot_files(bundle):
    files = {"bundle.json": encoded(bundle), "report.md": bundle["report_markdown"]}
    files.update({f"{key}.json": encoded(bundle[key]) for key in ("profile", "architecture", "session")})
    for key in ("profile", "architecture", "storybook"):
        if f"{key}_markdown" in bundle:
            files[f"{key}.md"] = bundle[f"{key}_markdown"]
    return files


def check_snapshot(folder, domain):
    bundle = read_json(child(folder, "bundle.json"))
    check_bundle(bundle, domain)
    require(bundle["session_id"] == folder.name, f"Session directory mismatch: {folder}")
    for name, expected in snapshot_files(bundle).items():
        require(child(folder, name).read_text(encoding="utf-8") == expected,
                f"Snapshot projection differs from bundle: {folder / name}")
    return bundle


def domains(root):
    base = child(root, "domains")
    require(base.is_dir(), "Archive is not initialized: missing domains directory")
    result = []
    for path in sorted(base.iterdir()):
        identifier(path.name)
        require(not path.is_symlink() and path.is_dir(), f"Invalid domain directory: {path}")
        result.append(path)
    return result


def current(domain):
    path = child(domain, "current.json")
    if not path.exists():
        return None
    pointer = read_json(path)
    require(isinstance(pointer, dict), f"Invalid current pointer: {path}")
    session_id = identifier(pointer.get("session_id"))
    require(pointer.get("schema_version") == 1 and pointer.get("domain") == domain.name
            and pointer.get("snapshot") == f"sessions/{session_id}",
            f"Invalid current pointer: {path}")
    bundle = check_snapshot(child(child(domain, "sessions"), session_id), domain.name)
    require(pointer.get("created_at") == bundle["created_at"], f"Current timestamp mismatch: {path}")
    return {"domain": domain.name, "title": bundle["domain"]["title"], **pointer}


def status(root):
    person = read_person(root)
    return {"root": str(root), "person": person, "domains": [p for d in domains(root) if (p := current(d))]}


def rebuild_index(root):
    with lock(root / ".index.lock"):
        lines = ["# 知年轮 · 我的成长档案", "", "按领域回看知识地图、能力证据与每一次咨询。当前切片持续更新，历史年轮保留当时的理解和反馈。", ""]
        for domain in domains(root):
            pointer = current(domain)
            if pointer is None:
                continue
            lines += [f"## {pointer['title']}", ""]
            sessions = child(domain, "sessions")
            for folder in sorted(sessions.iterdir(), key=lambda p: p.name, reverse=True):
                if folder.name.startswith(".stage-"):
                    continue
                identifier(folder.name)
                require(not folder.is_symlink() and folder.is_dir(), f"Invalid session directory: {folder}")
                bundle = check_snapshot(folder, domain.name)
                prefix = folder.absolute().as_posix()
                links = [f"[知识报告](<{prefix}/report.md>)"]
                for key, label in (("profile", "画像"), ("architecture", "知识架构"), ("storybook", "故事书")):
                    suffix = "md" if f"{key}_markdown" in bundle else "json"
                    if key != "storybook" or suffix == "md":
                        links.append(f"[{label}](<{prefix}/{key}.{suffix}>)")
                marker = "当前" if folder.name == pointer["session_id"] else "历史"
                lines.append(f"- {marker} · {folder.name} · " + " · ".join(links))
            lines.append("")
        return atomic_write(root / "INDEX.md", "\n".join(lines))


def index_warnings(root):
    try:
        warning = rebuild_index(root)
        return [warning] if warning else []
    except (OSError, ValueError) as exc:
        return [f"Snapshot committed; INDEX.md rebuild failed: {exc}"]


def initialize(root, person_name):
    require(person_name.strip(), "person-name must not be empty")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(root / ".init.lock"):
        person_path = child(root, "person.json")
        if person_path.exists():
            person = read_person(root)
            require(person.get("name") == person_name, "Archive belongs to a different person-name")
            warning = None
        else:
            person = {"schema_version": 1, "name": person_name, "archive_root": str(root),
                      "created_at": datetime.now(timezone.utc).isoformat()}
            warning = atomic_write(person_path, encoded(person))
        child(root, "domains").mkdir(exist_ok=True, mode=0o700)
    return {"person": person, "warnings": ([warning] if warning else []) + index_warnings(root)}


def commit(root, domain_id, bundle):
    identifier(domain_id)
    check_bundle(bundle, domain_id)
    read_person(root)
    domain = child(child(root, "domains"), domain_id)
    domain.mkdir(exist_ok=True, mode=0o700)
    with lock(domain / ".lock"):
        sessions = child(domain, "sessions")
        sessions.mkdir(exist_ok=True, mode=0o700)
        target = child(sessions, bundle["session_id"])
        require(not target.exists(), f"Session already exists: {target.name}")
        staging = Path(tempfile.mkdtemp(prefix=".stage-", dir=sessions))
        try:
            for name, contents in snapshot_files(bundle).items():
                write_file(staging / name, contents)
            sync_directory(staging)
            os.rename(staging, target)
            sync_directory(sessions)
            pointer = {"schema_version": 1, "domain": domain_id, "session_id": target.name,
                       "created_at": bundle["created_at"], "snapshot": f"sessions/{target.name}"}
            warning = atomic_write(domain / "current.json", encoded(pointer))
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {**pointer, "warnings": ([warning] if warning else []) + index_warnings(root)}


def validate(root):
    status(root)
    count = 0
    for domain in domains(root):
        sessions = child(domain, "sessions")
        if not sessions.exists():
            continue
        for folder in sessions.iterdir():
            if folder.name.startswith(".stage-"):
                continue  # Interrupted staging is never authoritative.
            identifier(folder.name)
            require(not folder.is_symlink() and folder.is_dir(), f"Invalid session directory: {folder}")
            check_snapshot(folder, domain.name)
            count += 1
    return {"valid": True, "snapshots": count, "warnings": []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "commit", "record", "freeze", "refresh", "validate", "status"):
        command = commands.add_parser(name)
        command.add_argument("--root", required=True, type=Path)
        if name == "init":
            command.add_argument("--person-name", required=True)
            command.add_argument("--schema-version", type=int, choices=(1, 2), default=2)
        if name == "commit":
            command.add_argument("--domain", required=True)
        if name in ("commit", "record"):
            command.add_argument("--bundle", required=True, type=Path)
        if name == "freeze":
            command.add_argument("--session-id", required=True)
            command.add_argument("--title")
            command.add_argument("--created-at")
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    try:
        import archive_v2
        is_v2 = (root / ".cognirings/archive.json").exists()
        if args.command == "init":
            require(not (is_v2 and args.schema_version == 1), "A v2 archive cannot be reinitialized as v1")
            initializer = archive_v2.initialize if args.schema_version == 2 else initialize
            result = initializer(root, args.person_name)
        elif args.command in ("record", "freeze"):
            require(is_v2, f"{args.command} requires a v2 archive; migrate explicitly first")
            if args.command == "record":
                result = archive_v2.record(root, read_json(args.bundle.expanduser()))
            else:
                result = archive_v2.freeze(root, args.session_id, args.title, args.created_at)
        elif args.command == "commit":
            require(not is_v2, "Use record --bundle for v2 journals")
            result = commit(root, args.domain, read_json(args.bundle.expanduser()))
        elif is_v2:
            result = {"status": archive_v2.status, "validate": archive_v2.validate,
                      "refresh": archive_v2.refresh}[args.command](root)
        elif args.command == "refresh":
            validate(root)
            result = {"refreshed": True, "schema_version": 1, "warnings": index_warnings(root)}
        else:
            result = {"status": status, "validate": validate}[args.command](root)
        print(encoded(result), end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(encoded({"error": str(exc)}), file=sys.stderr, end="")
        return 1


if __name__ == "__main__":
    sys.exit(main())
