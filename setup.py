"""Register this assistant workspace and install both release-tracking BB plugins."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys

from migrate_bridge import LEGACY, TRACKING

ASSISTANT = "git:https://github.com/ainurminibaev/bb-personal-assistant.git@semver:^0.2.0-rc.1"
SOURCES = {"telegram-projects": TRACKING, "personal-assistant": ASSISTANT}


def bb(*args):
    result = subprocess.run(["bb", *args, "--json"], check=True, stdout=subprocess.PIPE)
    return json.loads(result.stdout)


def stable_version(version):
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    return tuple(map(int, match.groups())) if match else ()


def supports_coordinator_handoff(version):
    return stable_version(version) >= (0, 9, 20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True, help="BB host ID of this folder")
    parser.add_argument("--name", default="Personal Assistant", help="Display name for a newly created BB project")
    parser.add_argument("--yes", action="store_true", help="Approve installation of the displayed release sources")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Existing assistant project or a new private working directory")
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    root = args.workspace.expanduser().resolve()
    if root == package or package in root.parents:
        raise SystemExit("Choose a workspace outside the extracted release directory.")
    if root.exists() and (not root.is_dir() or (any(root.iterdir()) and not (root / "projects.md").is_file())):
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
    if missing:
        print("BB release sources:", *missing, sep="\n", flush=True)
        if not args.yes and (not sys.stdin.isatty() or input("Install [y/N]? ").lower() != "y"):
            raise SystemExit("Installation requires confirmation. For unattended installation use --yes.")
        for src in missing:
            bb("plugin", "install", src, "--yes")
    # Existing release-tracking installations can lag behind the newest tag.
    releases = {p["id"]: p["version"] for p in bb("plugin", "list")["plugins"]}
    if stable_version(releases.get("personal-assistant", "")) < (0, 2, 3):
        raise SystemExit("Update Personal Agent through BB Settings → Updates, then rerun setup. "
                         "Live coordinator settings require stable Personal Agent 0.2.3 or later.")
    if not supports_coordinator_handoff(releases.get("telegram-projects", "")):
        raise SystemExit("Update Telegram Bridge through BB Settings → Updates, then rerun setup. "
                         "Transferring coordinator instructions requires stable Telegram Bridge 0.9.20 or later.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with (root / "projects.md").open("x", encoding="utf-8") as registry:
            registry.write((package / "projects.md").read_text(encoding="utf-8"))
    except FileExistsError:
        pass
    project = projects[0] if projects else bb(
        "project", "create", "--name", args.name, "--root", str(root),
        "--machine", args.machine
    )
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
