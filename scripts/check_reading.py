#!/usr/bin/env python3
"""Check local Markdown destinations without fetching external pages or changing files."""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def check(root, portable=False):
    root = Path(root).resolve()
    errors, links = [], 0
    ignored = {".git", "node_modules", "__pycache__", ".venv"}
    for path in sorted(root.rglob("*")):
        if any(part in ignored for part in path.relative_to(root).parts) or not path.is_file():
            continue
        if path.suffix not in (".md", ".json", ".yaml", ".yml"):
            continue
        text = path.read_text(encoding="utf-8")
        if portable and re.search(r"(?:/Users/|file://|https?://[^\s\"<>]*\.alibaba-inc\.com)", text):
            errors.append(f"{path.relative_to(root)}: private/local address in portable archive")
        if path.suffix != ".md":
            continue
        prose = re.sub(r"^```[^\n]*\n.*?^```\s*$", "", text, flags=re.M | re.S)
        for match in re.finditer(r"\]\((<[^>]*>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", prose):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            links += 1
            name = unquote(parsed.path)
            destination = (path.parent / name).resolve()
            if name.startswith("/") or not destination.is_relative_to(root):
                errors.append(f"{path.relative_to(root)}: nonportable link {target}")
            elif not destination.exists():
                errors.append(f"{path.relative_to(root)}: missing {target}")
    return {"valid": not errors, "local_links": links, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--portable", action="store_true")
    args = parser.parse_args()
    result = check(args.root, args.portable)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
