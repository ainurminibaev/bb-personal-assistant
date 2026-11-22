"""Register this assistant workspace and install the release-tracking BB plugins."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys

from migrate_bridge import LEGACY, TRACKING

ASSISTANT = "git:https://github.com/ainurminibaev/bb-personal-assistant.git@semver:^0.2.0-rc.1"
SOURCES = {"telegram-projects": TRACKING, "personal-assistant": ASSISTANT}
# The knowledge base ships in this repository and release, as a separate plugin folder.
KNOWLEDGE_BASE = ("knowledge-base", ASSISTANT, "plugins/knowledge-base")


def bb(*args):
    result = subprocess.run(["bb", *args, "--json"], check=True, stdout=subprocess.PIPE)
    return json.loads(result.stdout)


def stable_version(version):
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    return tuple(map(int, match.groups())) if match else ()


def supports_coordinator_handoff(version):
    return stable_version(version) >= (0, 9, 21)


GUARD = Path("hooks") / "guest-thread-guard.py"


def clone_workspace(url, root):
    """An employee's workspace is a git copy of its repository, so the changes it makes to itself are commits."""
    if (root / ".git").exists():
        return
    if root.exists() and any(root.iterdir()):
        raise SystemExit("--workspace-repo needs an empty or missing workspace folder.")
    root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", url, str(root)], check=True)
    root.chmod(0o700)


def configure_bridge(kind, inbox):
    """The kind of installation and the folder for received files; existing settings stay."""
    config = bb("plugin", "config", "telegram-projects")
    values = config.get("values", config)
    if values.get("installKind") != kind:
        bb("plugin", "config", "telegram-projects", "set", "installKind", kind)
    if not values.get("telegramInboxRoot"):
        inbox.mkdir(mode=0o700, parents=True, exist_ok=True)
        bb("plugin", "config", "telegram-projects", "set", "telegramInboxRoot", str(inbox))


def remember_agent(project_id, provider, model):
    """BB remembers the agent and model of the last start in a project; a short check sets both."""
    args = ["thread", "spawn", "--project", project_id, "--title", "Setup: agent check",
            "--prompt", "Setup check. Reply with one line: the agent and model you run on. Do not use tools."]
    if provider:
        args += ["--provider", provider]
    if model:
        args += ["--model", model]
    thread = bb(*args)
    print(f"Agent check started in thread {thread.get('id') or thread.get('thread', {}).get('id')}.")


def install_guest_guard(package, root, data_dir):
    """Guest thread guard for Codex and Claude Code: threads with groups and other people keep
    every tool but do not manage BB. Codex runs a new or changed hook only after the owner allows it."""
    target = root / ".bb" / "hooks" / GUARD.name
    target.parent.mkdir(parents=True, exist_ok=True)
    source = (package / GUARD).read_bytes()
    if not target.exists() or target.read_bytes() != source:
        target.write_bytes(source)
        target.chmod(0o755)
    # Agents start hooks without BB variables: the hook finds BB's data here.
    settings = target.with_name("guest-thread-guard.json")
    wanted = json.dumps({"dataDir": str(data_dir)}) + "\n"
    if not settings.exists() or settings.read_text(encoding="utf-8") != wanted:
        settings.write_text(wanted, encoding="utf-8")
    command = f"python3 {target}"
    entry = {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 30}]}
    for config in (root / ".codex" / "hooks.json", root / ".claude" / "settings.json"):
        if config.is_symlink():
            raise SystemExit(f"Review the linked {config} before setup.")
        config.parent.mkdir(exist_ok=True)
        data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
        hooks = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
        if not any(h.get("command") == command for group in hooks for h in group.get("hooks", [])):
            hooks.append(entry)
            config.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True, help="BB host ID of this folder")
    parser.add_argument("--name", default="Personal Assistant", help="Display name for a newly created BB project")
    parser.add_argument("--language", choices=("en", "ru"), default="en",
                        help="Telegram menu language for a newly installed bridge (default: en)")
    parser.add_argument("--yes", action="store_true", help="Approve installation of the displayed release sources")
    parser.add_argument("--allow-guest-guard", action="store_true",
                        help="Allow the guest thread guard in Codex now, without the BB interface (only this hook)")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Existing assistant project or a new private working directory")
    parser.add_argument("--kind", choices=("assistant", "employee"), default="assistant",
                        help="assistant: a personal assistant with the owner's files; employee: a digital employee of a team")
    parser.add_argument("--workspace-repo",
                        help="Git URL of the employee's repository: the workspace becomes its git copy (role, team rules, connections)")
    parser.add_argument("--provider", help="Agent for the project, for example codex or claude-code")
    parser.add_argument("--model", help="Model for the project, for example the provider's current model ID")
    parser.add_argument("--inbox", type=Path,
                        help="Folder for received Telegram files (default: telegram-inbox next to the workspace)")
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    root = args.workspace.expanduser().resolve()
    if root == package or package in root.parents:
        raise SystemExit("Choose a workspace outside the extracted release directory.")
    if args.workspace_repo:
        clone_workspace(args.workspace_repo, root)
    cloned = bool(args.workspace_repo) and (root / ".git").exists()
    if root.exists() and (not root.is_dir() or (any(root.iterdir()) and not (root / "projects.md").is_file() and not cloned)):
        raise SystemExit("Choose an empty directory or an existing assistant workspace containing projects.md.")
    machines = bb("machine", "list")
    if not any(m["id"] == args.machine and m["status"] == "connected" for m in machines):
        raise SystemExit("The selected BB host must be connected. Check bb machine list.")
    plugins = bb("plugin", "list")["plugins"]
    installed = {p["id"]: p for p in plugins}
    for plugin_id, expected in SOURCES.items():
        plugin = installed.get(plugin_id)
        if (plugin and plugin_id == "telegram-projects"
                and plugin["source"].lower().split("@", 1)[0].removesuffix(".git")
                == "git:https://github.com/vkirill/bb-plugin-telegram-projects"):
            raise SystemExit(
                "The original Telegram Projects plugin is installed. Its v0.9.0 has no "
                "private-state export for this migration. Keep this installation for its existing "
                "chats. To try the assistant separately, use a separate BB instance and bot token. "
                "See BRIDGE-MIGRATION.md."
            )
        if plugin and plugin["source"] == LEGACY and plugin_id == "telegram-projects":
            raise SystemExit("Migrate the existing chat binding first. Follow BRIDGE-MIGRATION.md.")
        if plugin and (plugin["source"] != expected or not plugin["enabled"]):
            raise SystemExit(f"Check the installed source and enabled state of {plugin_id}.")
    projects = [p for p in bb("project", "list") if any(
        s.get("hostId") == args.machine and s.get("path") == str(root)
        for s in p.get("sources", [])
    )]
    if len(projects) > 1:
        raise SystemExit("Multiple BB projects use this folder. Check their workspace bindings.")
    missing = [src for pid, src in SOURCES.items() if pid not in installed]
    kb_id, kb_source, kb_folder = KNOWLEDGE_BASE
    kb_missing = kb_id not in installed
    if missing or kb_missing:
        print("BB release sources:", *missing, *([f"{kb_source} (folder {kb_folder})"] if kb_missing else []),
              sep="\n", flush=True)
        if not args.yes and (not sys.stdin.isatty() or input("Install [y/N]? ").lower() != "y"):
            raise SystemExit("Installation requires confirmation. For unattended installation use --yes.")
        for src in missing:
            bb("plugin", "install", src, "--yes")
            if src == SOURCES["telegram-projects"]:
                bb("plugin", "config", "telegram-projects", "set", "language", args.language)
        if kb_missing:
            bb("plugin", "install", kb_source, "--subdirectory", kb_folder, "--yes")
    # Existing release-tracking installations can lag behind the newest tag.
    releases = {p["id"]: p["version"] for p in bb("plugin", "list")["plugins"]}
    if stable_version(releases.get("personal-assistant", "")) < (0, 2, 3):
        raise SystemExit("Update Personal Agent through BB Settings → Updates, then rerun setup. "
                         "Live coordinator settings require stable Personal Agent 0.2.3 or later.")
    if not supports_coordinator_handoff(releases.get("telegram-projects", "")):
        raise SystemExit("Update Telegram Bridge through BB Settings → Updates, then rerun setup. "
                         "Transferring coordinator instructions requires stable Telegram Bridge 0.9.21 or later.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    configure_bridge(args.kind, (args.inbox or root.parent / "telegram-inbox").expanduser().resolve())
    try:
        with (root / "projects.md").open("x", encoding="utf-8") as registry:
            registry.write((package / "projects.md").read_text(encoding="utf-8"))
    except FileExistsError:
        pass
    local = root / "local"
    if local.is_symlink():
        raise SystemExit("Review the linked local instructions directory before setup.")
    local.mkdir(mode=0o700, exist_ok=True)
    custom = local / "agents-custom.md"
    if custom.is_symlink() or (custom.exists() and not custom.is_file()):
        raise SystemExit("Review the local instructions path before setup.")
    try:
        with custom.open("x", encoding="utf-8"):
            pass
    except FileExistsError:
        pass
    install_guest_guard(package, root, bb("status")["dataDir"])
    if args.allow_guest_guard:
        subprocess.run([sys.executable, str(package / "hooks" / "approve-guest-guard.py"), str(root)], check=True)
        print("Guest thread guard installed and allowed in Codex.")
    else:
        print("Guest thread guard installed. Allow it once: the \"Codex hooks\" card in BB, or run setup.py again with --allow-guest-guard.")
    project = projects[0] if projects else bb(
        "project", "create", "--name", args.name, "--root", str(root),
        "--machine", args.machine
    )
    if args.provider or args.model:
        remember_agent(project["id"], args.provider, args.model)
    status = bb("plugin", "rpc", "call", "telegram-projects", "status")
    preferences = bb("plugin", "rpc", "call", "telegram-projects", "preferences")
    print(f"Workspace registered as BB project {project['id']}.")
    personal = [b for b in status["chatBindings"] if b.get("topicId") == 0
                and b.get("threadId") and b.get("projectId") == project["id"]]
    if personal:
        if len(personal) != 1:
            raise SystemExit("Select a single coordinator for the private chat.")
        expected = {"projectId": project["id"], "threadId": personal[0]["threadId"]}
        configured = bb("plugin", "config", "personal-assistant")["values"]
        if any(configured.get(k) and configured[k] != v for k, v in expected.items()):
            raise SystemExit("A different coordinator is already configured. Check the chat binding before replacing it.")
        if any(configured.get(k) != v for k, v in expected.items()):
            for key, value in expected.items():
                bb("plugin", "config", "personal-assistant", "set", key, value)
            bb("plugin", "reload", "personal-assistant")
        print("The private chat and assistant instructions are bound to the coordinator.")
    elif preferences["tokenPresent"] or status.get("owner") or status["chatBindings"]:
        print("Telegram is already configured in this BB instance. To run an independent second bot, "
              "use a separate BB instance. The existing chat binding was preserved.")
    else:
        print("Follow the owner pairing steps in README.md to connect your first bot.")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError:
        sys.exit("The bb command is unavailable. Start BB and use a terminal with the BB CLI on PATH.")
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
