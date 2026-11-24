"""Register this assistant workspace and install the release-tracking BB plugins."""
import argparse
import os
import json
import re
import shutil
from pathlib import Path
import subprocess
import sys

from migrate_bridge import LEGACY, TRACKING
import organization
import tools

# The platform list (tools.yml): core plugins are the base of every installation, the rest is general by default.
REGISTRY = tools.load()
SOURCES = {name: tool["source"] for name, tool in REGISTRY.items() if tool.get("core")}


def bb(*args):
    result = subprocess.run(["bb", *args, "--json"], check=True, stdout=subprocess.PIPE)
    return json.loads(result.stdout)


def stable_version(version):
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    return tuple(map(int, match.groups())) if match else ()


def supports_coordinator_handoff(version):
    return stable_version(version) >= (0, 9, 21)


GUARD = Path("hooks") / "guest-thread-guard.py"
VOICE_ROUTER = Path("/usr/local/bin/voice-to-text")   # the server's speech service router


def clone_workspace(url, root):
    """An employee's workspace is a git copy of its repository, so the changes it makes to itself are commits."""
    if (root / ".git").exists():
        return
    if root.exists() and any(root.iterdir()):
        raise SystemExit("--workspace-repo needs an empty or missing workspace folder.")
    root.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", url, str(root)], check=True)
    root.chmod(0o700)


def same_folder(path, root):
    """A project registered with a link path is the same folder as its real path (a stand under a link, 25.11)."""
    if not path:
        return False
    try:
        return Path(path).resolve() == Path(root).resolve()
    except OSError:
        return str(path) == str(root)


def conversation_folders(kind, root, clone, home=None):
    """С76: where the own folders of groups and people's chats live, and the role rules they read.

    An employee: ~/conversations of its account, apart from the organization copy. An assistant: next to its
    workspace, where the owner keeps projects. Rules: the employee's role (AGENTS.md of its folder)."""
    home = Path(home or Path.home())
    folders = home / "conversations" if kind == "employee" else root.parent / (root.name + "-conversations")
    rules = root / "AGENTS.md" if kind == "employee" and (root / "AGENTS.md").is_file() else None
    # What a conversation folder reaches by a link (the bridge links these from the owner's folder): rules, skills, MCP
    # settings without values — never local/ or outputs/.
    allow = [root / "AGENTS.md", root / "CLAUDE.md", root / "skills", root / ".mcp.json", root / ".codex" / "config.toml",
             root / ".claude" / "skills", root / ".agents" / "skills"] + ([clone / "shared"] if clone else [])
    return folders, rules, allow


def configure_conversation_folders(folders, rules):
    """The bridge puts new groups and people's chats into their own folders; settings set by hand stay."""
    config = bb("plugin", "config", "telegram-projects")
    values = config.get("values", config)
    if "conversationFoldersRoot" not in values:
        print("This bridge has no own folders for groups yet (С76): update the bridge, then run setup again.")
        return False
    if not values.get("conversationFoldersRoot"):
        folders.mkdir(mode=0o700, parents=True, exist_ok=True)
        bb("plugin", "config", "telegram-projects", "set", "conversationFoldersRoot", str(folders))
    if rules and not values.get("conversationRulesFile"):
        bb("plugin", "config", "telegram-projects", "set", "conversationRulesFile", str(rules))
    return True


def configure_bridge(kind, inbox):
    """The kind of installation and the folder for received files; existing settings stay."""
    config = bb("plugin", "config", "telegram-projects")
    values = config.get("values", config)
    if values.get("installKind") != kind:
        bb("plugin", "config", "telegram-projects", "set", "installKind", kind)
    if not values.get("telegramInboxRoot"):
        inbox.mkdir(mode=0o700, parents=True, exist_ok=True)
        bb("plugin", "config", "telegram-projects", "set", "telegramInboxRoot", str(inbox))
    # Voice of the bridge goes through the server's speech service when it is installed (a path that no longer
    # exists, e.g. a retired local recognizer, is replaced; a working custom router stays).
    router = VOICE_ROUTER
    current = values.get("voiceRouterPath") or ""
    if router.exists() and current != str(router) and (not current or not Path(current).exists()):
        bb("plugin", "config", "telegram-projects", "set", "voiceRouterPath", str(router))


def configure_curators(description, employee):
    """С94: the employee's curators (passport) lead its topics with the owner — the bridge's `curators`."""
    ids, missing = organization.curator_ids(description, employee)
    for name in missing:
        print(f"Curator {name}: no Telegram ID in organization.yml people — add it so the bridge knows them.", flush=True)
    values = bb("plugin", "config", "telegram-projects")
    values = values.get("values", values)
    if "curators" in values and ids and values.get("curators") != ",".join(ids):
        bb("plugin", "config", "telegram-projects", "set", "curators", ",".join(ids))
    # Leads shared chats by default (passport leads_chats): only when the passport says it; otherwise the bridge's auto.
    lead = employee.get("leads_chats")
    if isinstance(lead, bool) and "leadsChatsByDefault" in values and values.get("leadsChatsByDefault") != ("yes" if lead else "no"):
        bb("plugin", "config", "telegram-projects", "set", "leadsChatsByDefault", "yes" if lead else "no")


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


def install_guest_guard(package, root, data_dir, private=None, user_level=False, home=None):
    """Guest thread guard for Codex and Claude Code: threads with groups and other people keep
    every tool but do not manage BB. Codex runs a new or changed hook only after the owner allows it."""
    target = root / ".bb" / "hooks" / GUARD.name
    target.parent.mkdir(parents=True, exist_ok=True)
    source = (package / GUARD).read_bytes()
    if not target.exists() or target.read_bytes() != source:
        target.write_bytes(source)
        target.chmod(0o755)
    # Agents start hooks without BB variables: the hook finds BB's data here.
    # Only dataDir is the installer's; limits set next to it by hand (maxSchedules, guestSchedules...) stay.
    settings = target.with_name("guest-thread-guard.json")
    try:
        current = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
    except ValueError:
        raise SystemExit(f"{settings} is not valid JSON; fix or remove it, then run setup again.")
    if not isinstance(current, dict):
        raise SystemExit(f"{settings} must hold a JSON object; fix or remove it, then run setup again.")
    wanted = {**current, "dataDir": str(data_dir)}
    if private:  # С76: the owner's folder, closed to conversations that live in their own folders
        folder, allow = private
        wanted.update(privateRoot=str(folder), privateAllow=[str(a) for a in allow])
    if wanted != current or not settings.exists():
        settings.write_text(json.dumps(wanted, ensure_ascii=False) + "\n", encoding="utf-8")
    command = f"python3 {target}"
    entry = {"matcher": "*", "hooks": [{"type": "command", "command": command, "timeout": 30}]}
    configs = [root / ".codex" / "hooks.json", root / ".claude" / "settings.json"]
    if user_level:  # conversations in their own folders are outside the workspace: the account's settings reach them
        home = Path(home or Path.home())
        codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex")
        configs += [codex_home / "hooks.json", home / ".claude" / "settings.json"]
    for config in configs:
        if config.is_symlink():
            raise SystemExit(f"Review the linked {config} before setup.")
        config.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
        hooks = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
        if not any(h.get("command") == command for group in hooks for h in group.get("hooks", [])):
            hooks.append(entry)
            config.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def main():
    if sys.argv[1:2] == ["tools"]:
        # setup.py tools status [--json] [--workspace <folder>]: the state of every tool of this installation.
        sys.exit(tools.main(sys.argv[2:]))
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
    parser.add_argument("--organization",
                        help="Git URL of the organization repository: the workspace becomes its copy with shared/ "
                             "and employees/<name> only; the BB project is employees/<name>")
    parser.add_argument("--employee", help="Employee folder name in the organization repository (with --organization)")
    parser.add_argument("--keep-installed-sources", action="store_true",
                        help="Keep plugin sources that are already installed (a release candidate or a local mirror)")
    parser.add_argument("--provider", help="Agent for the project, for example codex or claude-code")
    parser.add_argument("--model", help="Model for the project, for example the provider's current model ID")
    parser.add_argument("--tool-source", action="append", default=[], metavar="ID=SOURCE",
                        help="Install a tool plugin from another source, for example a copy brought by the administrator")
    parser.add_argument("--own-conversation-folders", action="store_true",
                        help="Put new groups and people's chats into folders of their own (С76); off by default until the owner's word")
    parser.add_argument("--inbox", type=Path,
                        help="Folder for received Telegram files (default: telegram-inbox next to the workspace)")
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    root = args.workspace.expanduser().resolve()
    if root == package or package in root.parents:
        raise SystemExit("Choose a workspace outside the extracted release directory.")
    if bool(args.organization) != bool(args.employee):
        raise SystemExit("--organization and --employee go together.")
    if args.organization and args.workspace_repo:
        raise SystemExit("Choose --organization or --workspace-repo.")
    clone = None
    description, employee = {}, {}
    if args.organization:
        args.kind = "employee"
        clone = root
        print(f"Organization copy {organization.checkout(args.organization, clone, args.employee)}: {clone}.")
        root = clone / "employees" / args.employee
        description, employee, layout = organization.read_organization(clone, args.employee)
    if args.workspace_repo:
        clone_workspace(args.workspace_repo, root)
    cloned = (bool(args.workspace_repo) and (root / ".git").exists()) or clone is not None
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
        if plugin and args.keep_installed_sources and plugin["enabled"]:
            continue
        if plugin and (plugin["source"] != expected or not plugin["enabled"]):
            raise SystemExit(f"Check the installed source and enabled state of {plugin_id}.")
    projects = [p for p in bb("project", "list") if any(
        s.get("hostId") == args.machine and same_folder(s.get("path"), root)
        for s in p.get("sources", [])
    )]
    if len(projects) > 1:
        raise SystemExit("Multiple BB projects use this folder. Check their workspace bindings.")
    decisions, tool_problems = tools.select(REGISTRY, args.kind, description, employee)
    sources = dict(s.split("=", 1) for s in args.tool_source if "=" in s)
    missing = [src for pid, src in SOURCES.items() if pid not in installed]
    # General plugins of the list (knowledge base, pictures...): what this installation gets and does not have yet.
    extra = [name for name, tool in REGISTRY.items() if tool["kind"] == "plugin" and not tool.get("core")
             and decisions[name][0] and tools.plugin_id(name, tool) not in installed]
    if missing or extra:
        print("BB release sources:", *missing, *(
            sources.get(n) or REGISTRY[n]["source"] + (f" (folder {REGISTRY[n]['subdirectory']})"
                                                     if REGISTRY[n].get("subdirectory") else "") for n in extra),
              sep="\n", flush=True)
        if not args.yes and (not sys.stdin.isatty() or input("Install [y/N]? ").lower() != "y"):
            raise SystemExit("Installation requires confirmation. For unattended installation use --yes.")
        for src in missing:
            bb("plugin", "install", src, "--yes")
            if src == SOURCES["telegram-projects"]:
                bb("plugin", "config", "telegram-projects", "set", "language", args.language)
    tool_errors = {}
    for name in extra:
        error = tools.install_tool(name, REGISTRY[name], None, {}, bb, {}, sources.get(name))
        if error:
            print(f"{name}: {error}. Setup goes on; the tools list at the end says what to do.", flush=True)
            tool_errors[name] = error
    if extra:
        tools.wait_for_plugins(bb, [tools.plugin_id(n, REGISTRY[n]) for n in extra if n not in tool_errors])
    # A release with a new skill: BB may not know it until the plugin reloads (С: update rule, 25.11).
    configure_error = tools.ensure_platform_configured(bb)
    if configure_error:
        print(f"personal-assistant: {configure_error} — conversations get no platform skills until this is fixed.",
              flush=True)
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
    if clone is not None:
        configure_curators(description, employee)
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
    if clone is not None:
        links, notes = organization.link_skills(clone, args.employee)
        organization.exclude_installation_files(clone, args.employee, links)
        layout += notes
    folders, rules, allow = conversation_folders(args.kind, root, clone)
    own_folders = args.own_conversation_folders and configure_conversation_folders(folders, rules)
    install_guest_guard(package, root, bb("status")["dataDir"], (root, allow) if own_folders else None,
                        user_level=own_folders)
    if args.allow_guest_guard:
        subprocess.run([sys.executable, str(package / "hooks" / "approve-guest-guard.py"), str(root),
                        *([str(folders)] if own_folders else [])], check=True)
        print("Guest thread guard installed and allowed in Codex.")
    else:
        print("Guest thread guard installed. Allow it once: the \"Codex hooks\" card in BB, or run setup.py again with --allow-guest-guard.")
    project = projects[0] if projects else bb(
        "project", "create", "--name", args.employee if clone is not None and args.name == "Personal Assistant" else args.name, "--root", str(root),
        "--machine", args.machine
    )
    if args.provider or args.model:
        remember_agent(project["id"], args.provider, args.model)
    # Voice transcripts: through the server's speech service when it is installed (Codex first, local models as the
    # fallback); otherwise Codex first (С23) — BB's "automatic" order would try the BB cloud first once signed in.
    if Path("/usr/local/bin/voice-to-text").exists():
        subprocess.run(["bb", "settings", "ai-services", "set", "voice", "server-speech"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif args.provider == "codex":
        subprocess.run(["bb", "settings", "ai-services", "set", "voice", "codex"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Tools of the agent's account (context-mode, skills, hooks, the credentials store) and the described MCP connections.
    keys, admins, secret_problems = (tools.described_secrets(description, employee, args.employee)
                                     if clone is not None else ({}, [], []))
    account, cache = tools.Account(), {}
    for name, tool in REGISTRY.items():
        if tool["kind"] not in ("plugin", "service") and decisions[name][0]:
            error = tools.install_tool(name, tool, account, {p["id"]: p for p in bb("plugin", "list")["plugins"]},
                                       bb, cache, admins=admins)
            if error:
                print(f"{name}: {error}. Setup goes on; the tools list at the end says what to do.", flush=True)
                tool_errors[name] = error
    for folder in cache.values():
        shutil.rmtree(folder, ignore_errors=True)
    # A plugin that needs a program on the host (the browser): put it next to the plugin, also when the
    # plugin itself was already there (browser-automation comes with BB).
    data_dir = tools.bb_data_dir(bb)
    for name, tool in REGISTRY.items():
        if tool.get("runtime") and decisions[name][0] and name not in tool_errors:
            print(f"{name}: checking the browser on this server (first time downloads it, a few minutes)", flush=True)
            error = tools.install_browser(tool, data_dir)
            if error:
                print(f"{name}: {error}. Setup goes on; the tools list at the end says what to do.", flush=True)
                tool_errors[name] = error
    note = tools.install_leak_check(clone if clone is not None else root, tools._cli_dir(account) / "vallis-secret")
    if note:
        secret_problems.append(note)
    servers, mcp_problems = tools.mcp_servers(description, employee, args.employee) if clone is not None else ({}, [])
    processes = tools.mcp_processes(REGISTRY, decisions, account)
    # С87: with the tool catalog on, the lines' MCP processes go to the catalog, not into every conversation.
    to_catalog = processes if tools.catalog_on({p["id"]: p for p in bb("plugin", "list")["plugins"]}) else {}
    engines = {**servers, **{k: v for k, v in processes.items() if k not in to_catalog}}
    mcp_errors = (tools.write_mcp(root, engines, set(keys)) if clone is not None or processes else {})
    mcp_errors.update(tools.write_catalog(root, to_catalog))
    for name in processes:
        if name in mcp_errors:
            tool_errors[name] = mcp_errors.pop(name)
    tools.save_errors(root / tools.STATE_FILE, tool_errors, mcp_errors)
    if clone is not None:
        facts = organization.collect_facts(bb, project["id"], args.machine)
        differences = layout + organization.compare(description, employee, facts)
        print(f"Employee {args.employee}: " + ("description matches the installation." if not differences
                                               else f"{len(differences)} difference(s):"))
        for line in differences:
            print(f"  - {line}")
        if organization.not_compared(employee):
            print("  Not compared by this version: " + ", ".join(organization.not_compared(employee)) + ".")
    rows = tools.states(REGISTRY, decisions, account, {p["id"]: p for p in bb("plugin", "list")["plugins"]},
                        account.home, tool_errors, root, servers, mcp_errors, data_dir, keys=keys)
    spaces, space_problems = (tools.described_spaces(description, employee, args.employee)
                              if clone is not None else ({}, []))
    space_list = tools.space_rows(spaces, rows, clone, description, employee)
    report = tools.report(args.kind, rows, tool_problems + mcp_problems + secret_problems + space_problems,
                          args.employee, spaces=space_list, accesses=tools.access_rows(account))
    tools.save_report(root, report)
    tools.print_report(report)
    print("The same list later: python3 setup.py tools status [--json] --workspace " + str(root))
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
