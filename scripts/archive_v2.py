#!/usr/bin/env python3
"""Reader-first archives with immutable journals and explicitly refreshed projections.

Only Python's standard library is required. Current knowledge/profile prose is
maintained by the mentor; generated blocks and state are disposable projections.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile


class ArchiveError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ArchiveError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ArchiveError(f"Non-finite JSON value: {value}")


def read_json(path):
    require(not path.is_symlink(), f"Symlink is not allowed: {path}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique,
                      parse_constant=_constant)


def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", value),
            f"Unsafe identifier: {value!r}")
    return value


def relative(value):
    require(isinstance(value, str) and value and "\\" not in value, f"Unsafe path: {value!r}")
    parts = value.split("/")
    require(not value.startswith("/") and all(p not in ("", ".", "..") for p in parts),
            f"Unsafe path: {value!r}")
    return value


def safe(root, value):
    relative(value)
    require(not root.is_symlink(), f"Symlink archive root: {root}")
    path = root
    for part in value.split("/"):
        path = path / part
        require(not path.is_symlink(), f"Symlink is not allowed: {path}")
    require(path.resolve().is_relative_to(root.resolve()), f"Path escapes archive: {value}")
    return path


def no_symlinks(root):
    require(not root.is_symlink(), f"Symlink archive root: {root}")
    for folder in ("knowledge", "profile", "journal", ".cognirings"):
        base = safe(root, folder)
        if base.exists():
            for current, dirs, files in os.walk(base, followlinks=False):
                for name in dirs + files:
                    path = Path(current) / name
                    require(not path.is_symlink(), f"Symlink is not allowed: {path}")


def timestamp(value, nullable=False):
    if value is None and nullable:
        return
    require(isinstance(value, str) and value, "Expected ISO 8601 date/time")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchiveError(f"Invalid ISO 8601 date/time: {value}") from exc


def date_key(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def atomic_write(path, content):
    require(not path.is_symlink(), f"Symlink is not allowed: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def archive_lock(root):
    path = safe(root, ".cognirings/.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


REGISTRIES = {
    "nodes": ".cognirings/registry/nodes.json",
    "artifacts": ".cognirings/registry/artifacts.json",
    "sources": ".cognirings/registry/sources.json",
    "evidence": ".cognirings/evidence/records.json",
    "sessions": ".cognirings/registry/journal.json",
}
LEVELS = ("U", "L1", "L2", "L3", "L4", "L5")
PRACTICE = ("unverified", "assisted", "independent", "transfer")
ASSISTANCE = ("independent", "hint", "references", "ai_collaboration")
CONTENT_LABELS = {"foundation": "基础卡", "synthesis": "已有综述", "outline": "待扩展"}
PRACTICE_LABELS = {"unverified": "未验证", "assisted": "辅助完成", "independent": "独立完成",
                   "transfer": "跨情境迁移已验证"}


def frontmatter(text):
    """Read a deliberately small YAML subset; no anchors, nesting or coercion magic."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ArchiveError("Unterminated YAML frontmatter") from exc
    result = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*", line)
        require(match is not None, f"Unsupported frontmatter line: {line}")
        key, raw = match.groups()
        require(key not in result, f"Duplicate frontmatter key: {key}")
        if raw == "" or raw in ("null", "~"):
            value = None
        elif raw.startswith(("[", '"')) or raw in ("true", "false") or re.fullmatch(r"-?\d+", raw):
            try:
                value = json.loads(raw, parse_constant=_constant)
            except ValueError as exc:
                raise ArchiveError(f"Use JSON inline values in frontmatter: {line}") from exc
            require(not isinstance(value, dict), "Nested frontmatter objects are unsupported")
        elif raw.startswith("'") and raw.endswith("'"):
            value = raw[1:-1].replace("''", "'")
        else:
            require(not raw.startswith(("{", "&", "*", "|", ">", "!")),
                    f"Unsupported frontmatter scalar: {line}")
            value = raw
        result[key] = value
    return result


def profile_template(node):
    return (f"---\nnode_id: {node['id']}\nlevel: U\npractice: unverified\n"
            "evidence_ids: []\nconfirmed_at: null\ntarget_level: null\n"
            "checkpoints_total: 0\ncheckpoints_evidenced: 0\n---\n\n"
            f"# {node['title']} · 当前认知画像\n\n尚未校准。缺少个人证据不等于不会。\n")


def load(root):
    config = read_json(safe(root, ".cognirings/archive.json"))
    require(isinstance(config, dict) and config.get("schema_version") == 2,
            "Expected .cognirings/archive.json with schema_version 2")
    data = {"config": config}
    for key, path in REGISTRIES.items():
        collection = read_json(safe(root, path))
        require(isinstance(collection, dict) and isinstance(collection.get(key), list),
                f"Expected {key} array in {path}")
        data[key] = collection[key]
        if key == "nodes":
            data["edges"] = collection.get("edges", [])
            data["bridges"] = collection.get("bridges", [])
    return data


def index_records(records, kind):
    result = {}
    for entry in records:
        require(isinstance(entry, dict), f"Invalid {kind} record")
        key = identifier(entry.get("id"))
        require(key not in result, f"Duplicate {kind} ID: {key}")
        result[key] = entry
    return result


def string_array(record, field, default=None):
    values = record.get(field, [] if default is None else default)
    require(isinstance(values, list) and all(isinstance(x, str) for x in values),
            f"{field} must be an array of strings")
    require(len(values) == len(set(values)), f"Duplicate reference in {field}")
    return values


def hashes(folder):
    require(folder.is_dir() and not folder.is_symlink(), f"Invalid journal directory: {folder}")
    values = {}
    for current, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            require(not (Path(current) / name).is_symlink(), f"Symlink in journal: {name}")
        for name in sorted(files):
            path = Path(current) / name
            values[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(values.items()))


def validate(root):
    """Read-only: do not create locks, rewrite indexes or refresh timestamps."""
    no_symlinks(root)
    data = load(root)
    nodes = index_records(data["nodes"], "node")
    sources = index_records(data["sources"], "source")
    artifacts = index_records(data["artifacts"], "artifact")
    evidence = index_records(data["evidence"], "evidence")
    sessions = index_records(data["sessions"], "session")
    for index in ("README.md", "knowledge/README.md", "profile/README.md", "journal/README.md"):
        require(safe(root, index).is_file(), f"Missing reader entry: {index}")
    paths = set()
    profiles = {}
    for node_id, node in nodes.items():
        require(isinstance(node.get("title"), str) and node["title"].strip(), f"Node title missing: {node_id}")
        path = relative(node.get("path"))
        require(all(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", p) for p in path.split("/")),
                f"Topic paths must use lowercase slugs: {path}")
        require(path not in paths, f"Duplicate node path: {path}")
        paths.add(path)
        require(node.get("domain") == path.split("/")[0], f"Node domain/path mismatch: {node_id}")
        parent_id = node.get("parent_id")
        if parent_id is None:
            require("/" not in path, f"Nested node requires a parent: {node_id}")
        else:
            require(parent_id in nodes, f"Unknown parent of {node_id}: {parent_id}")
            require(str(PurePosixPath(path).parent) == nodes[parent_id]["path"],
                    f"Parent/path mismatch: {node_id}")
        timestamp(node.get("knowledge_updated_at"), nullable=True)
        require(isinstance(node.get("content_status", "待整理"), str)
                and node.get("content_status", "待整理").strip(), f"Invalid content_status: {node_id}")
        require("order" not in node or (type(node["order"]) in (int, float)
                and node["order"] == node["order"] and abs(node["order"]) != float("inf")),
                f"Node order must be a finite number: {node_id}")
        for ref in string_array(node, "source_ids") + string_array(node, "source_refs"):
            require(ref in sources, f"Unknown node source: {ref}")
        for ref in string_array(node, "prerequisite_ids"):
            require(ref in nodes, f"Unknown prerequisite node: {ref}")
        knowledge = safe(root, f"knowledge/{path}/README.md")
        profile = safe(root, f"profile/{path}/README.md")
        require(knowledge.is_file() and profile.is_file(), f"Missing mirrored node README: {path}")
        km = frontmatter(knowledge.read_text(encoding="utf-8"))
        require("node_id" not in km or km["node_id"] == node_id, f"Knowledge node_id mismatch: {path}")
        pm = frontmatter(profile.read_text(encoding="utf-8"))
        require(pm.get("node_id") == node_id, f"Profile node_id mismatch: {path}")
        require(pm.get("level") in LEVELS, f"Invalid understanding level: {path}")
        require(pm.get("practice") in PRACTICE, f"Invalid practice state: {path}")
        require(pm.get("target_level") is None or pm["target_level"] in LEVELS[1:], f"Invalid target level: {path}")
        timestamp(pm.get("confirmed_at"), nullable=True)
        refs = string_array(pm, "evidence_ids")
        for ref in refs:
            require(ref in evidence, f"Unknown profile evidence: {ref}")
            require(evidence[ref].get("node_id") == node_id, f"Evidence belongs to another node: {ref}")
        total, done = pm.get("checkpoints_total"), pm.get("checkpoints_evidenced")
        require(type(total) is int and type(done) is int and 0 <= done <= total,
                f"Invalid checkpoint counts: {path}")
        require(not (pm["level"] != "U" or pm["practice"] != "unverified" or done) or bool(refs),
                f"Personal progress requires evidence: {path}")
        require(not (pm["level"] != "U" or pm["practice"] != "unverified" or done)
                or any(evidence[ref].get("kind") == "observed" for ref in refs),
                f"Calibrated progress requires observed evidence, not goals or self-report alone: {path}")
        require(pm["level"] == "U" or pm.get("confirmed_at") is not None,
                f"Calibrated understanding requires confirmed_at: {path}")
        profiles[node_id] = pm
    edges = data["edges"]
    require(isinstance(edges, list), "edges must be an array")
    edges = edges + [{"from": ref, "to": key, "type": "prerequisite"}
                     for key, node in nodes.items() for ref in node.get("prerequisite_ids", [])]
    require(isinstance(data["bridges"], list), "bridges must be an array")
    bridges = index_records(data["bridges"], "bridge")
    for bridge_id, bridge in bridges.items():
        require(isinstance(bridge.get("type"), str) and bridge["type"], f"Bridge type required: {bridge_id}")
        for ref in string_array(bridge, "source_refs") + string_array(bridge, "source_ids"):
            require(ref in sources, f"Unknown bridge source: {ref}")
        for side in ("from", "to"):
            endpoint = bridge.get(side)
            require(isinstance(endpoint, dict),
                    f"Bridge {bridge_id} {side} requires a domain/node object; archive unsupported legacy bridges separately")
            domain, node_id = identifier(endpoint.get("domain")), identifier(endpoint.get("node"))
            if side == "from":
                require(node_id in nodes, f"Unknown bridge source node: {bridge_id}/{node_id}")
            if node_id in nodes:
                require(nodes[node_id]["domain"] == domain, f"Bridge endpoint domain mismatch: {bridge_id}/{side}")
            else:
                require(side == "to" and bridge.get("status") == "proposed",
                        f"Unknown bridge target requires proposed status: {bridge_id}/{node_id}")
        if bridge["to"]["node"] in nodes:
            edges.append({"from": bridge["from"]["node"], "to": bridge["to"]["node"], "type": bridge["type"]})
    outgoing = {key: [] for key in nodes}
    indegree = dict.fromkeys(nodes, 0)
    for edge in edges:
        require(isinstance(edge, dict), "Invalid graph edge")
        start, end = edge.get("from"), edge.get("to")
        require(start in nodes and end in nodes, f"Unknown edge endpoint: {start} -> {end}")
        require(isinstance(edge.get("type"), str) and edge["type"], "Edge type required")
        if edge["type"] == "prerequisite":
            outgoing[start].append(end)
            indegree[end] += 1
    queue = [key for key, count in indegree.items() if count == 0]
    seen = 0
    while queue:
        seen += 1
        for end in outgoing[queue.pop()]:
            indegree[end] -= 1
            if indegree[end] == 0:
                queue.append(end)
    require(seen == len(nodes), "Prerequisite edges must form a DAG")
    originals = set()
    for artifact_id, item in artifacts.items():
        require(isinstance(item.get("title"), str) and item["title"].strip(), f"Artifact title required: {artifact_id}")
        path = relative(item.get("path"))
        require(path.startswith("journal/"), f"Formal artifact must have one journal original: {artifact_id}")
        require(path not in originals, f"Artifact original is registered twice: {path}")
        originals.add(path)
        require(safe(root, path).is_file(), f"Missing artifact: {path}")
        require(item.get("type") in ("report", "case", "share"), f"Invalid artifact type: {artifact_id}")
        require(item.get("status") in ("published", "draft"), f"Invalid artifact status: {artifact_id}")
        timestamp(item.get("created_at"))
        refs = string_array(item, "node_ids")
        require(bool(refs), f"Artifact must reference at least one node: {artifact_id}")
        for ref in refs:
            require(ref in nodes, f"Unknown artifact node: {ref}")
        for ref in string_array(item, "source_ids"):
            require(ref in sources, f"Unknown artifact source: {ref}")
        parts = path.split("/")
        require(len(parts) >= 3 and parts[1] in sessions, f"Artifact journal is not frozen: {path}")
    for evidence_id, entry in evidence.items():
        require(entry.get("node_id") in nodes, f"Unknown evidence node: {evidence_id}")
        path = relative(entry.get("path"))
        require(path.startswith("journal/") and safe(root, path).is_file(), f"Missing journal evidence: {path}")
        require(path.split("/")[1] in sessions, f"Evidence journal is not frozen: {path}")
        require(entry.get("kind") in ("self_reported", "observed"), f"Invalid evidence kind: {evidence_id}")
        require(entry.get("assistance") in ASSISTANCE, f"Invalid assistance: {evidence_id}")
        require(isinstance(entry.get("scope"), str) and entry["scope"].strip(), f"Evidence scope required: {evidence_id}")
        timestamp(entry.get("observed_at"))
    journal = safe(root, "journal")
    actual_sessions = set()
    for folder in journal.iterdir():
        if folder.is_dir():
            actual_sessions.add(identifier(folder.name))
    require(actual_sessions == set(sessions), "Journal directories and frozen session registry differ")
    for session_id, entry in sessions.items():
        timestamp(entry.get("created_at"))
        require(isinstance(entry.get("title"), str) and entry["title"].strip(), f"Journal title required: {session_id}")
        require(safe(root, f"journal/{session_id}/README.md").is_file(), f"Missing session README: {session_id}")
        require(isinstance(entry.get("files"), dict) and entry["files"], f"Empty journal manifest: {session_id}")
        require(hashes(safe(root, f"journal/{session_id}")) == entry["files"], f"Immutable journal changed: {session_id}")
    return {"valid": True, "schema_version": 2, "nodes": len(nodes), "artifacts": len(artifacts),
            "evidence": len(evidence), "sessions": len(sessions), "sources": len(sources), "bridges": len(bridges)}


def status(root):
    result = validate(root)
    config = load(root)["config"]
    return {**result, "person": config.get("person", {}), "root": str(root)}


def replace_block(text, name, body):
    start, end = f"<!-- cognirings:{name}:start -->", f"<!-- cognirings:{name}:end -->"
    require(text.count(start) == text.count(end) and text.count(start) <= 1,
            f"Malformed generated block: {name}")
    block = start + "\n" + body.rstrip() + "\n" + end
    if start not in text:
        return text.rstrip() + "\n\n" + block + "\n"
    a, b = text.index(start), text.index(end)
    require(a < b, f"Reversed generated block: {name}")
    return text[:a] + block + text[b + len(end):]


def linked(label, target, page):
    rel = os.path.relpath(target, Path(page).parent).replace(os.sep, "/")
    label = str(label).replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
    return f"[{label}](<{rel}>)"


def metrics(data, root):
    nodes = index_records(data["nodes"], "node")
    by_node = {key: set() for key in nodes}
    published = {a["id"]: a for a in data["artifacts"] if a["status"] == "published"}
    for artifact_id, artifact in published.items():
        for key in artifact["node_ids"]:
            while key is not None:
                by_node[key].add(artifact_id)
                key = nodes[key].get("parent_id")
    result = {}
    for key, node in nodes.items():
        profile = frontmatter(safe(root, f"profile/{node['path']}/README.md").read_text(encoding="utf-8"))
        descendants = [n for n in nodes.values() if n["path"] == node["path"] or n["path"].startswith(node["path"] + "/")]
        leaves = [n for n in descendants if not any(other.get("parent_id") == n["id"] for other in nodes.values())]
        substantive_dates = [n["knowledge_updated_at"] for n in descendants if n.get("knowledge_updated_at")]
        latest_substantive = max(substantive_dates, key=date_key) if substantive_dates else None
        distribution = dict.fromkeys(LEVELS, 0)
        for leaf in leaves:
            lm = frontmatter(safe(root, f"profile/{leaf['path']}/README.md").read_text(encoding="utf-8"))
            distribution[lm["level"]] += 1
        result[key] = {"path": node["path"], "knowledge_updated_at": latest_substantive,
                       "own_knowledge_updated_at": node.get("knowledge_updated_at"),
                       "is_branch": len(descendants) > 1,
                       "content_status": node.get("content_status", "待整理"),
                       "artifact_ids": sorted(by_node[key]),
                       "counts": {kind: sum(published[a]["type"] == kind for a in by_node[key]) for kind in ("report", "case", "share")},
                       "level": profile["level"], "practice": profile["practice"],
                       "confirmed_at": profile.get("confirmed_at"), "target_level": profile.get("target_level"),
                       "checkpoints_total": profile["checkpoints_total"], "checkpoints_evidenced": profile["checkpoints_evidenced"],
                       "leaf_count": len(leaves), "calibrated_leaf_count": len(leaves) - distribution["U"],
                       "leaf_levels": distribution}
    return result


def metric_line(stat):
    counts = stat["counts"]
    total, done = stat["checkpoints_total"], stat["checkpoints_evidenced"]
    progress = (f"{total}项检查点：{done}项有证据、{total - done}项待验证" if total else "检查点待设定")
    content = CONTENT_LABELS.get(stat["content_status"], stat["content_status"])
    practice = PRACTICE_LABELS[stat["practice"]]
    understanding = (f"叶子校准：{stat['calibrated_leaf_count']}/{stat['leaf_count']}，"
                     f"{stat['leaf_count'] - stat['calibrated_leaf_count']}个待校准" if stat["is_branch"]
                     else f"理解：{stat['level']}")
    return (f"知识更新：{stat['knowledge_updated_at'] or '待登记'} · "
            f"{counts['report']}份报告 / {counts['case']}个案例 / {counts['share']}份分享 · {content} · "
            f"{understanding} · 实践：{practice} · 目标：{stat['target_level'] or '待设定'} · {progress} · "
            f"证据确认：{stat['confirmed_at'] or '待校准'}")


def topic_summary(artifact, nodes):
    refs = [nodes[key] for key in artifact["node_ids"]]
    domains = list(dict.fromkeys(node["domain"] for node in refs))
    domain_titles = {node["domain"]: node["title"] for node in nodes.values() if node.get("parent_id") is None}
    domain_text = " / ".join(domain_titles.get(domain, domain) for domain in domains[:3])
    if len(domains) > 3:
        domain_text += f"等{len(domains)}个领域"
    # Prefer specific topics over their already included parent branches.
    specific = [node for node in refs if not any(other["path"].startswith(node["path"] + "/") for other in refs)]
    preview = "、".join(node["title"] for node in specific[:3])
    if len(refs) > 3:
        preview += f"等{len(refs)}个主题"
    return domain_text + (f" / {preview}" if preview and preview != domain_text else "")


def tree_metric_line(stat):
    """Compact navigation metrics; detailed evidence/practice remain on the node."""
    updated = (stat["knowledge_updated_at"] or "待登记")[:10]
    labels = (("report", "份报告"), ("case", "个案例"), ("share", "份分享"))
    counts = "/".join(f"{stat['counts'][kind]}{label}" for kind, label in labels if stat["counts"][kind])
    counts = counts or "无正式成果"
    content = CONTENT_LABELS.get(stat["content_status"], stat["content_status"])
    understanding = (f"叶子校准{stat['calibrated_leaf_count']}/{stat['leaf_count']}" if stat["is_branch"]
                     else f"理解{stat['level']}")
    parts = [f"知识{updated}", counts, content, understanding]
    if stat["target_level"]:
        parts.append(f"目标{stat['target_level']}")
    total, done = stat["checkpoints_total"], stat["checkpoints_evidenced"]
    if total:
        parts.append(f"检查点{done}项有证据/{total - done}项待验证")
    return " · ".join(parts)


def ordered_nodes(nodes):
    """Sort siblings by optional order, then path; always visit parents first."""
    children = {}
    for node in nodes:
        children.setdefault(node.get("parent_id"), []).append(node)
    for siblings in children.values():
        siblings.sort(key=lambda node: (node.get("order", float("inf")), node["path"]))
    result = []
    def visit(parent):
        for node in children.get(parent, []):
            result.append(node)
            visit(node["id"])
    visit(None)
    return result


def tree_block(data, stats, section, page, ancestor=None):
    nodes = ordered_nodes(data["nodes"])
    base_depth = len(ancestor.split("/")) if ancestor else 0
    lines = ["完整主题树（点击主题阅读；U 表示尚未校准）：", ""]
    for node in nodes:
        if ancestor and not node["path"].startswith(ancestor + "/"):
            continue
        depth = len(node["path"].split("/")) - base_depth - 1
        link = linked(node["title"], f"{section}/{node['path']}/README.md", page)
        other = "profile" if section == "knowledge" else "knowledge"
        alternate = linked("画像" if other == "profile" else "知识", f"{other}/{node['path']}/README.md", page)
        lines.append("  " * depth + f"- {link} · {alternate} — {tree_metric_line(stats[node['id']])}")
    if len(lines) == 2:
        lines.append("此节点为当前叶子主题。")
    return "\n".join(lines)


def refresh(root):
    with archive_lock(root):
        validate(root)
        data = load(root)
        stats = metrics(data, root)
        updates = {}
        for section in ("knowledge", "profile"):
            page = f"{section}/README.md"
            updates[page] = replace_block(safe(root, page).read_text(encoding="utf-8"), "tree", tree_block(data, stats, section, page))
            for node in data["nodes"]:
                page = f"{section}/{node['path']}/README.md"
                value = safe(root, page).read_text(encoding="utf-8")
                stat = stats[node["id"]]
                body = metric_line(stat) + f"\n\n叶子主题证据覆盖：{stat['leaf_count']}个中{stat['calibrated_leaf_count']}个已校准；其余待校准。"
                other = "profile" if section == "knowledge" else "knowledge"
                body += "\n\n" + linked("对应认知画像" if other == "profile" else "对应知识综述", f"{other}/{node['path']}/README.md", page)
                value = replace_block(value, "metrics", body)
                if any(n.get("parent_id") == node["id"] for n in data["nodes"]):
                    value = replace_block(value, "tree", tree_block(data, stats, section, page, node["path"]))
                elif "<!-- cognirings:tree:start -->" in value:
                    value = replace_block(value, "tree", "此节点为当前叶子主题。")
                updates[page] = value
        recent = sorted((a for a in data["artifacts"] if a["status"] == "published" and a["type"] == "report"),
                        key=lambda a: (date_key(a["created_at"]), a["id"]), reverse=True)[:10]
        node_map = index_records(data["nodes"], "node")
        recent_lines = []
        for artifact in recent:
            topics = topic_summary(artifact, node_map)
            summary = str(artifact.get("summary", "")).replace("\n", " ")
            recent_lines.append(f"- {artifact['created_at']} · {linked(artifact['title'], artifact['path'], 'README.md')} · {topics}" + (f" — {summary}" if summary else ""))
        recent_text = "\n".join(recent_lines) or "尚无正式报告。"
        root_text = safe(root, "README.md").read_text(encoding="utf-8")
        root_text = replace_block(root_text, "tree", tree_block(data, stats, "knowledge", "README.md"))
        updates["README.md"] = replace_block(root_text, "recent", recent_text)
        page = "journal/README.md"
        entries = sorted(data["sessions"], key=lambda s: (date_key(s["created_at"]), s["id"]), reverse=True)
        lines = [f"- {s['created_at']} · {linked(s['title'], 'journal/' + s['id'] + '/README.md', page)}" for s in entries]
        updates[page] = replace_block(safe(root, page).read_text(encoding="utf-8"), "timeline", "\n".join(lines) or "尚无成长纪事。")
        updates[".cognirings/state/nodes.json"] = encoded({"schema_version": 2, "nodes": stats})
        updates[".cognirings/state/overview.json"] = encoded({"schema_version": 2, "node_count": len(stats),
            "published_artifact_ids": sorted(a["id"] for a in data["artifacts"] if a["status"] == "published"),
            "recent_report_ids": [a["id"] for a in recent]})
        changed = []
        # Prepare all block replacements before writing any; malformed blocks fail cleanly.
        for path, content in updates.items():
            target = safe(root, path)
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                atomic_write(target, content)
                changed.append(path)
        return {"schema_version": 2, "refreshed": True, "changed": changed}


def initialize(root, person_name):
    require(isinstance(person_name, str) and person_name.strip(), "person-name must not be empty")
    require(not root.is_symlink(), "Archive root must not be a symlink")
    if safe(root, ".cognirings/archive.json").exists():
        config = load(root)["config"]
        require(config.get("person", {}).get("name") == person_name, "Archive belongs to another person")
        return status(root)
    require(not (root / "person.json").exists(), "Existing v1 archive requires explicit migration; init does not convert history")
    if root.exists():
        require(not any(root.iterdir()), "Initialize an empty directory; existing files will not be overwritten")
    root.mkdir(parents=True, exist_ok=True)
    config = {"schema_version": 2, "person": {"name": person_name}, "created_at": datetime.now(timezone.utc).isoformat()}
    atomic_write(safe(root, ".cognirings/archive.json"), encoded(config))
    for key, path in REGISTRIES.items():
        atomic_write(safe(root, path), encoded({key: [], **({"edges": [], "bridges": []} if key == "nodes" else {})}))
    for path, title, desc in (("README.md", "知年轮 · 成长档案", "从知识版图定位主题，结合当前画像选择下一步。"),
                              ("knowledge/README.md", "知识体系", "收录表示已经整理，不代表已经掌握。"),
                              ("profile/README.md", "当前认知画像", "结合历史有效证据综合更新；未知不等于不会。"),
                              ("journal/README.md", "成长纪事", "保留每次活动的正式成果、实际反馈与当时判断。")):
        atomic_write(safe(root, path), f"# {title}\n\n{desc}\n")
    refresh(root)
    return status(root)


def freeze(root, session_id, title=None, created_at=None):
    identifier(session_id)
    with archive_lock(root):
        path = safe(root, REGISTRIES["sessions"])
        registry = read_json(path)
        sessions = index_records(registry["sessions"], "session")
        folder = safe(root, f"journal/{session_id}")
        manifest = hashes(folder)
        require("README.md" in manifest, "Journal requires README.md")
        if session_id in sessions:
            require(sessions[session_id]["files"] == manifest, f"Immutable journal changed: {session_id}")
            return {"frozen": True, "session_id": session_id, "already_registered": True}
        timestamp(created_at)
        require(isinstance(title, str) and title.strip(), "First freeze requires --title")
        registry["sessions"].append({"id": session_id, "title": title, "created_at": created_at, "files": manifest})
        atomic_write(path, encoded(registry))
        return {"frozen": True, "session_id": session_id, "files": len(manifest)}


def record(root, bundle):
    """Validate a complete staged archive before publishing a new immutable journal.

    Reversible installation under an exclusive lock; on ordinary exceptions all
    changed registries/files roll back. A process crash is not a multi-file atomic
    transaction: validate before continuing after an interrupted write.
    """
    require(isinstance(bundle, dict) and bundle.get("schema_version") == 2, "Expected v2 bundle")
    session_id = identifier(bundle.get("session_id"))
    timestamp(bundle.get("created_at"))
    require(isinstance(bundle.get("title"), str) and bundle["title"].strip(), "Session title required")
    files = bundle.get("files")
    require(isinstance(files, dict) and "README.md" in files, "files must include session README.md")
    for name, contents in files.items():
        relative(name)
        require(isinstance(contents, str), f"File contents must be text: {name}")
    with archive_lock(root):
        validate(root)
        require(not safe(root, f"journal/{session_id}").exists(), f"Session already exists: {session_id}")
        with tempfile.TemporaryDirectory(prefix="cognirings-stage-") as temporary:
            stage = Path(temporary) / "archive"
            stage.mkdir()
            for name in ("README.md", "knowledge", "profile", "journal", ".cognirings"):
                original = safe(root, name)
                if original.is_dir():
                    shutil.copytree(original, stage / name, ignore=shutil.ignore_patterns(".lock"))
                else:
                    shutil.copy2(original, stage / name)
            new_folder = safe(stage, f"journal/{session_id}")
            new_folder.mkdir()
            for name, contents in files.items():
                atomic_write(safe(new_folder, name), contents)
            added_nodes = bundle.get("nodes", [])
            for key in ("nodes", "artifacts", "evidence", "sources"):
                additions = bundle.get(key, [])
                require(isinstance(additions, list), f"{key} must be an array")
                path = safe(stage, REGISTRIES[key])
                registry = read_json(path)
                for entry in additions:
                    require(isinstance(entry, dict), f"Invalid {key} addition")
                    if key in ("artifacts", "evidence"):
                        require(entry.get("path", "").startswith(f"journal/{session_id}/"), f"New {key} must belong to the new journal")
                registry[key].extend(additions)
                if key == "nodes":
                    edges = bundle.get("edges", [])
                    require(isinstance(edges, list), "edges must be an array")
                    registry.setdefault("edges", []).extend(edges)
                    bridges = bundle.get("bridges", [])
                    require(isinstance(bridges, list), "bridges must be an array")
                    registry.setdefault("bridges", []).extend(bridges)
                atomic_write(path, encoded(registry))
            for node in added_nodes:
                path = relative(node.get("path"))
                for section, content in (("knowledge", f"# {node.get('title', '')}\n\n当前综述待整理。\n"),
                                         ("profile", profile_template(node))):
                    target = safe(stage, f"{section}/{path}/README.md")
                    require(not target.exists(), f"Node path already exists: {path}")
                    atomic_write(target, content)
            freeze(stage, session_id, bundle["title"], bundle["created_at"])
            validate(stage)
            updates = {path: safe(stage, path).read_text(encoding="utf-8") for path in REGISTRIES.values()}
            for node in added_nodes:
                for section in ("knowledge", "profile"):
                    path = f"{section}/{node['path']}/README.md"
                    updates[path] = safe(stage, path).read_text(encoding="utf-8")
            backups = {path: safe(root, path).read_bytes() if safe(root, path).exists() else None for path in updates}
            installed = []
            target_folder = safe(root, f"journal/{session_id}")
            try:
                shutil.copytree(new_folder, target_folder)
                for path, content in updates.items():
                    atomic_write(safe(root, path), content)
                    installed.append(path)
                validate(root)
            except BaseException:
                if target_folder.exists():
                    shutil.rmtree(target_folder)
                for path in reversed(installed):
                    target = safe(root, path)
                    if backups[path] is None:
                        target.unlink(missing_ok=True)
                        parent = target.parent
                        while parent != root and parent.exists() and not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
                    else:
                        atomic_write(target, backups[path].decode("utf-8"))
                raise
    return {"recorded": True, "session_id": session_id, "schema_version": 2,
            "next": "Synthesize current knowledge/profile prose, then run refresh and validate."}
