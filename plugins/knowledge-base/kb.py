#!/usr/bin/env python3
"""Knowledge base tool: search, read and add items of the personal knowledge base.

Works on a checkout with layout topics/<topic>/items/NNN-slug.md. If the root
has no topics/ folder yet, an empty knowledge base (git repository with rules in
AGENTS.md) is created there on first use. Indexes are rebuilt by the base's own
scripts/kb-rebuild.py when present, otherwise by a built-in INDEX.md writer.
All output is JSON on stdout; errors exit non-zero with {"error": ...}.
The root comes from --root or KB_ROOT.

  kb.py topics
  kb.py search [--topic T] [--tag X] [--limit N] [query ...]
  kb.py read <ref>                 ref: path | slug | NNN-slug | topic/NNN
  kb.py add --topic T --title ... --slug ... --source ... --type ... --tags a,b
            [--status pointer] [--body-file F | body on stdin] [--force]
            [--create-topic] [--no-commit] [--dry-run]
  kb.py remove <ref> [--no-commit]  (for cleanup/tests)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

ITEM_RE = re.compile(r"^(\d{3})-(.+)\.md$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
DERIVED = ["INDEX.md", "tags-index.md", "backlinks.md", ".kb-rebuild.log"]


class KbError(Exception):
    pass


def out(data) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta: dict = {}
    for line in text[3:end].strip("\n").splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key] = value.strip("'\"")
    body = text[end + 4:].lstrip("\n")
    return meta, body


def title_of(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def items(root: Path, topic: str | None = None) -> list[dict]:
    result = []
    topics_dir = root / "topics"
    for topic_dir in sorted(p for p in topics_dir.iterdir() if p.is_dir()):
        if topic and topic_dir.name != topic:
            continue
        items_dir = topic_dir / "items"
        if not items_dir.is_dir():
            continue
        for path in sorted(items_dir.glob("*.md")):
            m = ITEM_RE.match(path.name)
            if not m:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            meta, body = parse_frontmatter(text)
            tags = meta.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            result.append({
                "path": path.relative_to(root).as_posix(),
                "topic": topic_dir.name,
                "id": m.group(1),
                "slug": str(meta.get("slug") or m.group(2)),
                "title": title_of(body, m.group(2)),
                "tags": tags,
                "source": str(meta.get("source", "")),
                "type": str(meta.get("type", "")),
                "status": str(meta.get("status", "")),
                "date_added": str(meta.get("date_added", "")),
                "_body": body,
            })
    return result


def public(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def cmd_topics(root: Path, _args) -> None:
    rows = []
    for topic_dir in sorted(p for p in (root / "topics").iterdir() if p.is_dir()):
        count = len([p for p in (topic_dir / "items").glob("*.md") if ITEM_RE.match(p.name)]) if (topic_dir / "items").is_dir() else 0
        rows.append({"topic": topic_dir.name, "items": count})
    rules = {}
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = root / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            rules = {"file": name, "text": text[:12000]}
            break
    out({"root": str(root), "topics": rows, "rules": rules})


def snippet(body: str, terms: list[str], width: int = 220) -> str:
    flat = re.sub(r"\s+", " ", body)
    low = flat.lower()
    pos = min([low.find(t) for t in terms if low.find(t) >= 0] or [0])
    start = max(0, pos - 60)
    return ("…" if start else "") + flat[start:start + width].strip() + ("…" if start + width < len(flat) else "")


def cmd_search(root: Path, args) -> None:
    query = " ".join(args.query).strip().lower()
    terms = [t for t in re.split(r"\s+", query) if t]
    tag = args.tag.lower() if args.tag else None
    pool = items(root, args.topic)
    if tag:
        pool = [i for i in pool if any(tag == t.lower() or tag in t.lower() for t in i["tags"])]

    def score(item: dict, require_all: bool) -> int:
        head = f'{item["title"]} {item["slug"]} {item["source"]}'.lower()
        tags = " ".join(item["tags"]).lower()
        body = item["_body"].lower()
        total, hits = 0, 0
        for t in terms:
            s = head.count(t) * 5 + tags.count(t) * 4 + min(body.count(t), 10)
            hits += 1 if s else 0
            total += s
        if require_all and hits < len(terms):
            return 0
        return total

    if terms:
        mode = "all-terms"
        ranked = [(score(i, True), i) for i in pool]
        ranked = [r for r in ranked if r[0] > 0]
        if not ranked:
            mode = "any-term"
            ranked = [(score(i, False), i) for i in pool]
            ranked = [r for r in ranked if r[0] > 0]
        ranked.sort(key=lambda r: (-r[0], r[1]["path"]))
    else:
        mode = "list"
        ranked = [(0, i) for i in sorted(pool, key=lambda i: (i["date_added"], i["path"]), reverse=True)]
    results = []
    for s, item in ranked[: args.limit]:
        row = public(item)
        row["score"] = s
        row["snippet"] = snippet(item["_body"], terms)
        results.append(row)
    out({"query": query, "topic": args.topic, "tag": args.tag, "mode": mode,
         "total": len(ranked), "results": results})


def resolve(root: Path, ref: str) -> dict:
    ref = ref.strip().removeprefix("[[").removesuffix("]]").split("|")[0]
    pool = items(root)
    candidate = (root / ref).resolve() if not ref.startswith("/") else Path(ref).resolve()
    for item in pool:
        if (root / item["path"]).resolve() == candidate:
            return item
    name = ref.removesuffix(".md")
    m = re.match(r"^([a-z0-9-]+)/(\d{1,3})$", name)
    matches = []
    if m:
        matches = [i for i in pool if i["topic"] == m.group(1) and int(i["id"]) == int(m.group(2))]
    else:
        base = name.rsplit("/", 1)[-1]
        matches = [i for i in pool if base in (i["slug"], f'{i["id"]}-{i["slug"]}', Path(i["path"]).stem)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise KbError(f"Ambiguous ref {ref!r}: " + ", ".join(i["path"] for i in matches))
    raise KbError(f"Item not found: {ref!r}. Use search first.")


def cmd_read(root: Path, args) -> None:
    item = resolve(root, args.ref)
    text = (root / item["path"]).read_text(encoding="utf-8", errors="replace")
    row = public(item)
    row["content"] = text
    out(row)


def normalize_source(source: str) -> str:
    s = source.strip()
    if not re.match(r"^https?://", s):
        return s.lower()
    parts = urlsplit(s)
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = parts.path.rstrip("/")
    m = re.match(r"^/(?:[^/]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", path)
    if host.endswith("instagram.com") and m:
        return f"instagram:{m.group(1)}"
    if host in ("youtu.be",):
        return f"youtube:{path.strip('/')}"
    if host.endswith("youtube.com"):
        v = re.search(r"(?:^|&)v=([^&]+)", parts.query)
        if v:
            return f"youtube:{v.group(1)}"
    if host == "github.com":
        return "github:" + "/".join(path.strip("/").split("/")[:2]).lower()
    return f"{host}{path}".lower()


@contextmanager
def lock(root: Path):
    git_dir = root / ".git"
    lock_path = git_dir / "kb-add.lock" if git_dir.is_dir() else Path("/tmp") / f"kb-add-{abs(hash(str(root)))}.lock"
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def git(root: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True, check=check)


def derived_paths(root: Path) -> list[str]:
    paths = [p for p in DERIVED if (root / p).exists()]
    paths += [p.relative_to(root).as_posix() for p in sorted((root / "topics").glob("*/INDEX.md"))]
    return paths


def clean_derived(root: Path) -> set[str]:
    """Derived files with no uncommitted changes (safe to include in our commit)."""
    tracked = set(git(root, "ls-files", "--", *derived_paths(root)).stdout.split())
    dirty = set()
    for line in git(root, "status", "--porcelain", "--", *derived_paths(root)).stdout.splitlines():
        dirty.add(line[3:].strip())
    return tracked - dirty


DEFAULT_RULES = """# Knowledge base

One item is one Markdown file in topics/<topic>/items/NNN-slug.md with frontmatter
(id, slug, source, type, date_added, status, topic, tags) and a # title.
Add items with kb_add: it checks duplicates, assigns the id, rebuilds INDEX.md
and commits. Do not edit indexes by hand.
"""


def ensure_base(root: Path) -> bool:
    """Create an empty knowledge base at root on first use. Returns True if created."""
    if (root / "topics").is_dir():
        return False
    if root.exists() and (not root.is_dir() or any(p.name != ".git" for p in root.iterdir())):
        raise KbError(f"{root} exists and is not a knowledge base (no topics/ folder); choose an empty or new folder")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / "topics").mkdir()
    (root / "topics" / ".gitkeep").write_text("", encoding="utf-8")
    (root / "AGENTS.md").write_text(DEFAULT_RULES, encoding="utf-8")
    builtin_rebuild(root)
    if git(root, "rev-parse", "--git-dir", check=False).returncode != 0:
        git(root, "init", "-q")
    git(root, "add", "--", "AGENTS.md", "INDEX.md", "topics/.gitkeep")
    identity = [] if git(root, "config", "user.email", check=False).stdout.strip() else \
        ["-c", "user.name=Knowledge Base", "-c", "user.email=kb@localhost"]
    git(root, *identity, "commit", "-q", "-m", "KB: empty knowledge base", check=False)
    return True


def builtin_rebuild(root: Path) -> None:
    lines = ["# Index", ""]
    pool = items(root)
    for topic in sorted({i["topic"] for i in pool}):
        lines += [f"## {topic}", ""]
        lines += [f"- [{i['id']} {i['title']}]({i['path']})" + (f" — {', '.join(i['tags'])}" if i["tags"] else "")
                  for i in pool if i["topic"] == topic]
        lines.append("")
    (root / "INDEX.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def rebuild_and_commit(root: Path, paths: list[str], message: str, clean_before: set[str], commit: bool) -> dict:
    script = root / "scripts" / "kb-rebuild.py"
    if script.is_file():
        proc = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True)
        result = {"rebuild": "ok" if proc.returncode == 0 else "failed",
                  "rebuild_output": (proc.stdout + proc.stderr).strip()[-800:]}
    else:
        builtin_rebuild(root)
        proc = subprocess.CompletedProcess([], 0)
        result = {"rebuild": "ok", "rebuild_output": "built-in INDEX.md"}
    if proc.returncode != 0 or not commit:
        result["commit"] = None
        return result
    if git(root, "rev-parse", "--git-dir", check=False).returncode != 0:
        result["commit"] = None
        result["commit_note"] = "not a git repository"
        return result
    changed_derived = {line[3:].strip() for line in git(root, "status", "--porcelain", "--", *derived_paths(root)).stdout.splitlines()}
    included = sorted(clean_before & changed_derived)
    skipped = sorted(changed_derived - clean_before)
    files = paths + included
    git(root, "add", "--", *[p for p in files if (root / p).exists()])
    identity = [] if git(root, "config", "user.email", check=False).stdout.strip() else \
        ["-c", "user.name=Knowledge Base", "-c", "user.email=kb@localhost"]
    done = git(root, *identity, "commit", "--only", "-m", message, "--", *files, check=False)
    if done.returncode != 0:
        result["commit"] = None
        result["commit_error"] = (done.stdout + done.stderr).strip()[-800:]
        return result
    result["commit"] = git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    result["committed_files"] = files
    if skipped:
        result["not_committed_indexes"] = skipped
        result["commit_note"] = ("Regenerated indexes already had someone else's uncommitted changes, "
                                 "so only the item was committed; indexes stay regenerated in the working tree.")
    return result


def cmd_add(root: Path, args) -> None:
    topic = args.topic.strip()
    slug = args.slug.strip().lower()
    if not TOPIC_RE.match(topic):
        raise KbError("topic must be kebab-case")
    if not SLUG_RE.match(slug):
        raise KbError("slug must be kebab-case latin, e.g. video-editing-workflows")
    topic_dir = root / "topics" / topic
    if not topic_dir.is_dir() and not args.create_topic:
        existing = sorted(p.name for p in (root / "topics").iterdir() if p.is_dir())
        raise KbError(f"Unknown topic {topic!r}. Existing: {', '.join(existing)}. Pass create_topic only if none fits.")
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else (sys.stdin.read() if not sys.stdin.isatty() else "")
    body = body.strip()
    if body.startswith("---"):
        raise KbError("Pass only the markdown body; frontmatter is generated")
    if body.startswith("# "):
        body = body.split("\n", 1)[1].strip() if "\n" in body else ""
    tags = [t.strip().lower().replace(" ", "-") for t in args.tags.split(",") if t.strip()]
    if not tags:
        raise KbError("At least one tag is required")
    with lock(root):
        pool = items(root)
        norm = normalize_source(args.source)
        is_url = bool(re.match(r"^https?://", args.source.strip()))
        dups = [i for i in pool if i["slug"] == slug or (is_url and normalize_source(i["source"]) == norm)]
        if dups and not args.force:
            out({"status": "duplicate", "message": "Similar item already exists; update it instead or pass force with a distinct slug.",
                 "duplicates": [public(i) for i in dups]})
            sys.exit(3)
        if any(i["slug"] == slug for i in pool):
            raise KbError(f"Slug {slug!r} already used; choose another")
        ids = [int(i["id"]) for i in pool if i["topic"] == topic]
        new_id = f"{(max(ids) + 1) if ids else 1:03d}"
        rel = f"topics/{topic}/items/{new_id}-{slug}.md"
        status = args.status or "pointer"
        title = args.title.strip()
        frontmatter = "\n".join([
            "---", f"id: {new_id}", f"slug: {slug}", f"source: {args.source.strip()}",
            f"type: {args.type.strip()}", f"date_added: {args.date or date.today().isoformat()}",
            f"status: {status}", f"topic: {topic}", f"tags: [{', '.join(tags)}]", "---", "", "",
        ])
        text = f"{frontmatter}# {title}\n\n{body}\n" if body else f"{frontmatter}# {title}\n"
        if args.dry_run:
            out({"status": "dry-run", "path": rel, "content": text})
            return
        clean_before = clean_derived(root) if (root / ".git").exists() else set()
        (root / "topics" / topic / "items").mkdir(parents=True, exist_ok=True)
        path = root / rel
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(text)
        result = rebuild_and_commit(root, [rel], f"KB: {new_id} {slug} — {title}", clean_before, not args.no_commit)
        out({"status": "added", "path": rel, "abs_path": str(path), "id": new_id, "topic": topic, "slug": slug, **result})


def cmd_remove(root: Path, args) -> None:
    with lock(root):
        item = resolve(root, args.ref)
        clean_before = clean_derived(root) if (root / ".git").exists() else set()
        tracked = git(root, "ls-files", "--error-unmatch", item["path"], check=False).returncode == 0
        (root / item["path"]).unlink()
        paths = [item["path"]] if tracked else []
        result = rebuild_and_commit(root, paths, f"KB: remove {item['id']} {item['slug']}", clean_before,
                                    not args.no_commit and tracked)
        out({"status": "removed", "path": item["path"], **result})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=os.environ.get("KB_ROOT"), required=not os.environ.get("KB_ROOT"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("topics")
    s = sub.add_parser("search")
    s.add_argument("query", nargs="*")
    s.add_argument("--topic")
    s.add_argument("--tag")
    s.add_argument("--limit", type=int, default=10)
    r = sub.add_parser("read")
    r.add_argument("ref")
    a = sub.add_parser("add")
    for name in ("topic", "title", "slug", "source", "type", "tags"):
        a.add_argument(f"--{name}", required=True)
    a.add_argument("--status")
    a.add_argument("--date")
    a.add_argument("--body-file")
    a.add_argument("--force", action="store_true")
    a.add_argument("--create-topic", action="store_true")
    a.add_argument("--no-commit", action="store_true")
    a.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("remove")
    d.add_argument("ref")
    d.add_argument("--no-commit", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    try:
        ensure_base(root)
        {"topics": cmd_topics, "search": cmd_search, "read": cmd_read, "add": cmd_add, "remove": cmd_remove}[args.cmd](root, args)
    except KbError as error:
        out({"error": str(error)})
        sys.exit(2)


if __name__ == "__main__":
    main()
