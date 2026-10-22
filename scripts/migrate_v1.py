#!/usr/bin/env python3
"""Copy a v1 archive into a new, reviewable v2 directory; never mutate the source.

An optional plan contains node_overrides (id -> slug/parent_id/title) and
session_types (session id -> report/maintenance). No personal level is inferred.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys

from archive import ArchiveError, check_snapshot, current, encoded, identifier, require


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else encoded(content), encoding="utf-8")


def link(origin, destination):
    return os.path.relpath(destination, Path(origin).parent).replace(os.sep, "/")


def relocate(text, original, destination, source_roots=()):
    """Preserve historical local links by resolving them into the retained v1 copy."""
    def replace(match):
        prefix, target, suffix = match.groups()
        if re.match(r"(?:[a-zA-Z][\w+.-]*:|#)", target):
            return match.group(0)
        target_path, sep, fragment = target.partition("#")
        if target_path.startswith("/"):
            resolved = None
            for source_root in source_roots:
                try:
                    resolved = Path(target_path).relative_to(source_root)
                    break
                except ValueError:
                    continue
            if resolved is None:
                return match.group(0)  # External private material remains explicitly local.
        else:
            resolved = Path(os.path.normpath(str(Path(original).parent / target_path)))
        require(".." not in resolved.parts, f"Historical link escapes archive: {target}")
        dest = Path("README.md") if resolved.as_posix() == "INDEX.md" else Path(".cognirings/archive/v1") / resolved
        return prefix + link(destination, dest) + (sep + fragment if sep else "") + suffix
    return re.sub(r"(\]\()([^\s)]+)(\))", replace, text)


def metadata(node_id):
    return ("---\nnode_id: " + json.dumps(node_id) + "\nlevel: U\npractice: unverified\n"
            "evidence_ids: []\nconfirmed_at: null\ntarget_level: null\n"
            "checkpoints_total: 0\ncheckpoints_evidenced: 0\n---\n\n")


def migrate(source, destination, plan=None):
    require(not Path(source).is_symlink(), "Source cannot be a symlink")
    require(not Path(destination).is_symlink(), "Destination cannot be a symlink")
    require(not any(p.is_symlink() for p in Path(destination).absolute().parents),
            "Destination cannot have symlink ancestors; use the resolved parent path")
    source, destination = Path(source).resolve(), Path(destination).absolute()
    plan = plan or {}
    require(source.is_dir() and (source / "domains").is_dir(), "Expected a v1 archive")
    require(not (source / ".cognirings/archive.json").exists(), "Source is already v2")
    require(not destination.exists(), "Destination must not exist; source is never modified")
    require(source not in destination.parents, "Destination cannot be inside source")
    bundles, latest = [], {}
    for domain_dir in sorted((source / "domains").iterdir()):
        if not domain_dir.is_dir():
            continue
        pointer = current(domain_dir)
        require(pointer, f"Missing current pointer: {domain_dir.name}")
        for folder in sorted((domain_dir / "sessions").iterdir()):
            if folder.name.startswith(".stage-"):
                continue
            require(not folder.is_symlink(), "Symlink in legacy archive")
            if folder.is_dir():
                bundles.append(check_snapshot(folder, domain_dir.name))
        latest[domain_dir.name] = check_snapshot(domain_dir / pointer["snapshot"], domain_dir.name)
    bundles.sort(key=lambda b: b["created_at"])
    require(len({b['session_id'] for b in bundles}) == len(bundles),
            "Colliding legacy session IDs: supply a deliberate identity migration first")
    ids = [n["id"] for b in latest.values() for n in b["architecture"]["nodes"]]
    require(len(ids) == len(set(ids)), "Colliding legacy node IDs: supply a deliberate identity migration first")
    # Refuse symlinks before creating anything. Only archive-owned paths are copied.
    retained = [p for p in source.iterdir() if p.name in ("domains", "research", "person.json", "publication.json")
                or (p.is_file() and (p.suffix == ".md" or p.name == "LICENSE"))]
    for path in retained:
        require(not path.is_symlink(), f"Symlink in legacy archive: {path}")
        if path.is_dir():
            require(not any(p.is_symlink() for p in path.rglob("*")), f"Symlink in {path}")
    destination.mkdir(parents=True)
    try:
        backup = destination / ".cognirings/archive/v1"
        backup.mkdir(parents=True)
        for path in retained:
            if path.is_dir():
                shutil.copytree(path, backup / path.name)
            else:
                shutil.copy2(path, backup / path.name)
        now = datetime.now(timezone.utc).isoformat()
        person = json.loads((source / "person.json").read_text())
        source_roots = [source]
        if isinstance(person.get("archive_root"), str) and Path(person["archive_root"]).is_absolute():
            source_roots.append(Path(person["archive_root"]))
        write(destination, ".cognirings/archive.json", {"schema_version": 2, "person": {"name": person["name"]}, "created_at": now})
        write(destination, ".cognirings/registry/person.json", {**person, "archive_root": ".", "schema_version": 2})
        nodes, edges, bridges, sources, items = [], [], [], {}, {}
        for historical in bundles:
            sources.update((s["id"], s) for s in historical.get("sources", []))
            items.update((i["id"], i) for i in historical.get("items", []))
        for domain, bundle in latest.items():
            graph = bundle["architecture"]
            parents = {e["from"]: e["to"] for e in graph["edges"] if e["type"] == "part_of"}
            originals = {n["id"]: n for n in graph["nodes"]}
            for node in graph["nodes"]:
                override = plan.get("node_overrides", {}).get(node["id"], {})
                parents[node["id"]] = override.get("parent_id", parents.get(node["id"]))
            paths = {}
            def node_path(nid, active=None):
                active = set() if active is None else active
                require(nid not in active, "Cycle in navigation tree")
                require(nid in originals, f"Missing parent {nid}")
                if nid in paths:
                    return paths[nid]
                slug = plan.get("node_overrides", {}).get(nid, {}).get("slug", nid)
                identifier(slug)
                parent = parents.get(nid)
                paths[nid] = (node_path(parent, active | {nid}) + "/" if parent else "") + slug
                return paths[nid]
            for original in graph["nodes"]:
                nid = original["id"]
                # A formatting-only snapshot must not advance knowledge time.
                previous, updated = None, None
                for historical in (b for b in bundles if b["domain"]["id"] == domain):
                    candidate = next((n for n in historical["architecture"]["nodes"] if n["id"] == nid), None)
                    if candidate is not None and candidate != previous:
                        updated, previous = historical["created_at"], candidate
                node = {**original, "title": original.get("title", nid), "path": node_path(nid), "parent_id": parents.get(nid),
                        "domain": domain, "knowledge_updated_at": updated, "content_status": "foundation"}
                if "title" in plan.get("node_overrides", {}).get(nid, {}):
                    node["title"] = plan["node_overrides"][nid]["title"]
                nodes.append(node)
            edges.extend([e for e in graph["edges"] if e["type"] != "part_of"])
            edges.extend({"from": nid, "to": parent, "type": "part_of"} for nid, parent in parents.items() if parent)
            bridges.extend(graph.get("bridges", []))
            sources.update((s["id"], s) for s in bundle.get("sources", []))
            items.update((i["id"], i) for i in bundle.get("items", []))
        require(len({n["path"] for n in nodes}) == len(nodes), "Duplicate navigation path in migration plan")
        write(destination, ".cognirings/registry/nodes.json", {"nodes": nodes, "edges": edges, "bridges": bridges})
        write(destination, ".cognirings/registry/sources.json", {"sources": list(sources.values())})
        write(destination, ".cognirings/research/items.json", {"items": list(items.values()), "note": "Legacy research dates and verification are retained; migration is not new research."})
        artifacts, evidence = [], []
        by_id = {n["id"]: n for n in nodes}
        for bundle in bundles:
            sid, domain = bundle["session_id"], bundle["domain"]["id"]
            identifier(sid)
            original_base = f"domains/{domain}/sessions/{sid}"
            lines = bundle["report_markdown"].splitlines()
            title = lines[0].lstrip("# ") if lines else sid
            if plan.get("session_types", {}).get(sid) == "maintenance":
                dst = f".cognirings/maintenance/{sid}/README.md"
                write(destination, dst, relocate(bundle["report_markdown"], original_base + "/report.md", dst, source_roots))
                continue
            dst = f"journal/{sid}/knowledge/{domain}/report.md"
            write(destination, dst, relocate(bundle["report_markdown"], original_base + "/report.md", dst, source_roots))
            # Topic assignment is based on what this report delivered, not every inherited node.
            if (bundle["session"].get("mentor_deliveries") or [{}])[0].get("type") == "knowledge-navigation":
                relevant = [n["id"] for n in bundle["architecture"]["nodes"]]
            else:
                relevant = sorted({nid for item in bundle.get("items", []) for nid in item.get("node_ids", []) if nid in by_id}
                                  | {nid for f in (bundle["session"].get("user_feedback") or []) for nid in f.get("node_ids", []) if nid in by_id})
            artifacts.append({"id": sid + "-report", "title": title, "path": dst, "type": "report", "status": "published",
                              "created_at": bundle["created_at"], "node_ids": relevant or [domain],
                              "source_ids": [s["id"] for s in bundle.get("sources", [])], "legacy_session_id": sid})
            reflection = f"journal/{sid}/profile/{domain}/reflection.md"
            feedback = bundle["session"].get("user_feedback") or []
            body = f"# 本次个人记录 · {title}\n\n记录时间：{bundle['created_at']}。\n\n"
            body += "## 实际反馈\n\n"
            for i, entry in enumerate(feedback):
                body += f"- {entry.get('text', '')}\n"
                for nid in entry.get("node_ids", []):
                    if nid in by_id:
                        evidence.append({"id": f"{sid}-feedback-{i}-{nid}", "node_id": nid, "path": reflection,
                                         "kind": "self_reported", "assistance": "independent", "observed_at": entry.get("at", bundle["created_at"]),
                                         "scope": "用户问题、目标或偏好；不作为概念掌握或实践能力证据", "text": entry.get("text", "")})
            if not feedback:
                body += "尚未收到针对本轮材料的个人反馈。\n"
            body += "\n## 当时的变化与待确认项\n\n"
            for change in bundle["session"].get("profile_changes") or []:
                body += f"- {change.get('change', '')}；依据：{change.get('basis', '')}。\n"
            body += "\n本轮没有足以建立理解等级的个人表现证据。问题和目标不等于能力证明。\n"
            old_story = f".cognirings/archive/v1/{original_base}/storybook.md"
            body += f"\n[当时的完整Storybook]({link(reflection, old_story)})\n"
            write(destination, reflection, body)
            write(destination, f"journal/{sid}/README.md", f"# {title}\n\n时间：{bundle['created_at']}。\n\n"
                  f"- [本次正式报告](knowledge/{domain}/report.md)\n- [本次个人记录](profile/{domain}/reflection.md)\n"
                  f"\n历史报告保留当时的研究窗口、结论与限制。迁移没有重新核验其中的近期事项。\n")
        write(destination, ".cognirings/registry/artifacts.json", {"artifacts": artifacts})
        write(destination, ".cognirings/evidence/records.json", {"evidence": evidence})
        write(destination, ".cognirings/registry/journal.json", {"sessions": []})
        for node in nodes:
            kp, pp = f"knowledge/{node['path']}/README.md", f"profile/{node['path']}/README.md"
            body = f"# {node['title']}\n\n{node.get('definition') or node.get('question') or '此主题的详细内容待扩展。'}\n\n"
            if node.get("question"):
                body += f"**核心问题：** {node['question']}\n\n"
            body += "## 应用与边界\n\n" + "\n".join("- " + s for s in node.get("applications", []) + node.get("boundaries", [])) + "\n\n"
            body += f"[个人理解与目标]({link(kp, pp)}) · [来源](sources.md) · [历史报告](reading.md)\n"
            write(destination, kp, body)
            entries = [a for a in artifacts if node["id"] in a["node_ids"]]
            readme = f"# {node['title']} · 历史报告\n\n"
            reading_path = "knowledge/" + node["path"] + "/reading.md"
            readme += "\n".join(f"- {a['created_at'][:10]} · [{a['title']}]({link(reading_path, a['path'])})" for a in reversed(entries)) if entries else "尚无直接关联的正式报告。\n"
            write(destination, f"knowledge/{node['path']}/reading.md", readme + "\n")
            source_text = f"# {node['title']} · 来源\n\n来源用于支撑知识解释，不代表个人已经掌握。迁移沿用原核验日期。\n\n"
            for sid in node.get("source_refs", []):
                src = sources[sid]
                label = src.get("name", sid)
                name = f"[{label}]({src['url']})" if src.get("url") else label + "（本地或受限来源）"
                source_text += f"- {name}：{src.get('platform', '')}；{src.get('role', '')}。{src.get('useful_for', '')} 局限：{src.get('limitations', '需按具体用途核验')}。\n"
            write(destination, f"knowledge/{node['path']}/sources.md", source_text)
            cap = next((c for c in latest[node["domain"]]["profile"].get("capabilities", []) if c["node_id"] == node["id"]), {})
            personal = metadata(node["id"]) + f"# {node['title']} · 我的理解\n\n当前为 **U · 未校准**。已整理的资料与项目背景不直接证明本主题的独立理解。\n\n"
            personal += f"[阅读知识综述]({link(pp, kp)})\n\n"
            personal += "## 下一次有用的证据\n\n" + cap.get("next_check", "围绕实际目标，提供一次自己的解释、方案判断或作品，再校准适用范围。") + "。\n"
            write(destination, pp, personal)
        write(destination, "README.md", "# 我的知年轮\n\n[知识体系](knowledge/README.md) · [认知画像](profile/README.md) · [成长纪事](journal/README.md)\n")
        write(destination, "knowledge/README.md", "# 知识体系\n\n树表示知识组织和积累，不表示已经掌握。\n")
        write(destination, "profile/README.md", "# 当前认知画像\n\n结合历史中有效的目标、偏好与能力证据持续综合。\n\n" +
              "\n".join("- " + c["text"] for b in latest.values() for c in b["profile"].get("context", [])) + "\n")
        write(destination, "journal/README.md", "# 成长纪事\n\n按实际活动时间保留正式报告和个人反馈。维护记录位于Agent维护区。\n")
        write(destination, ".cognirings/maintenance/v2-migration.json", {"migrated_at": now, "source_schema": 1,
              "source_preserved": True, "legacy_copy": ".cognirings/archive/v1", "plan": plan,
              "note": "Source directory was not modified. Historical research was not reverified. U means uncalibrated."})
        return {"root": str(destination), "nodes": len(nodes), "reports": len(artifacts), "evidence": len(evidence)}
    except BaseException:
        # The destination was created exclusively by this call; no source cleanup.
        shutil.rmtree(destination)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text()) if args.plan else None
        print(encoded(migrate(args.source, args.destination, plan)), end="")
    except (ArchiveError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Migration failed: {exc}\n")


if __name__ == "__main__":
    main()
