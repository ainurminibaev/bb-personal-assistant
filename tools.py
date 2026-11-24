"""Platform tools: the list in tools.yml, what one installation gets, installation and its state.

    python3 tools.py status [--json] [--workspace <BB project folder>]       an assistant or an employee
    python3 tools.py status [--json] --kind agents --account <agent> ...     an agent in the agents' BB (root)
    python3 tools.py install --kind agents --account <agent> ...             general tools for an agent (root)

setup.py installs the tools of an assistant or an employee itself; `setup.py tools status` runs the status above.
Every line of tools.yml and every MCP connection of the description gets one state: installed, excluded,
needs_login, needs_setup or error, with the reason and what to do. Format: docs/tools.md.
"""
import argparse
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REGISTRY = Path(__file__).resolve().with_name("tools.yml")
KINDS = ("assistant", "employee", "agents")
STATES = ("installed", "excluded", "needs_login", "needs_setup", "error")
FORMAT = 1
TOOL_KINDS = ("plugin", "npm", "skill", "hook", "service", "cli", "mcp")
SKILL_DIRS = (Path(".agents") / "skills", Path(".claude") / "skills")
SOURCE_MARK = ".vallis-source"
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
MCP_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
MCP_FIELDS = {"url", "token_env", "employees", "what"}
SECRET_FIELDS = {"what", "to"}
SPACE_KINDS = ("tracker", "knowledge", "drive", "chats", "repositories", "mail", "other")
SPACE_FIELDS = {"what", "kind", "via", "to", "access", "map"}
SPACE_VIA = re.compile(r"(mcp|tool|connections):[a-z0-9][a-z0-9_-]*|folder:[\w./-]+|groups")
SPACE_STATES = {"installed": ("ready", ""), "needs_login": ("needs_login", "нужен вход"),
                "needs_setup": ("needs_setup", "нужна настройка"), "error": ("error", "ошибка подключения"),
                "excluded": ("needs_setup", "инструмент выключен")}
ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
PACKAGE = Path(__file__).resolve().parent
TOML_BEGIN = "# vallis: MCP from the organization description (managed by setup.py)"
TOML_END = "# vallis: end"
STATE_FILE = Path("local") / "tools-state.json"
RERUN = "run setup.py"  # what sets the tools up again; agents: install-agent.sh


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        raise SystemExit("The tools list needs PyYAML: install the python3-yaml package.")
    with open(path, encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def load(path=REGISTRY):
    tools = load_yaml(path).get("tools") or {}
    for name, tool in tools.items():
        if tool.get("kind") not in TOOL_KINDS:
            raise SystemExit(f"tools.yml {name}: unknown kind {tool.get('kind')}")
        if not set(tool.get("installs") or ()) <= set(KINDS):
            raise SystemExit(f"tools.yml {name}: unknown installation kind in installs")
    return tools


def _names(section, key):
    value = (section or {}).get(key) or []
    return [str(v) for v in (value if isinstance(value, list) else [value])]


def select(registry, kind, organization=None, employee=None):
    """Decide every line of the list for this installation.

    Returns (decisions, problems): decisions[name] = (selected, level, reason)."""
    organization, employee = organization or {}, employee or {}
    problems = []
    org, own = organization.get("tools") or {}, employee.get("tools") or {}
    for label, section in (("organization.yml", org), ("employee.yml", own)):
        if not isinstance(section, dict):
            problems.append(f"{label} tools: expected exclude and add lists")
            continue
        for key in sorted(set(section) - {"exclude", "add"}):
            problems.append(f"{label} tools.{key}: unknown field, not used")
        for key in ("exclude", "add"):
            for name in _names(section, key):
                if name not in registry:
                    problems.append(f"{label} tools.{key}: {name} is not in the platform list (tools.yml)")
    org = org if isinstance(org, dict) else {}
    own = own if isinstance(own, dict) else {}
    decisions = {}
    for name, tool in registry.items():
        installs = tool.get("installs") or []
        if kind not in installs:
            decisions[name] = (False, "platform", f"not for this kind of installation ({kind})")
            continue
        default = tool.get("default", True)
        by_default = kind in default if isinstance(default, list) else bool(default)
        if tool.get("core"):
            for label, section in (("organization.yml", org), ("employee.yml", own)):
                if name in _names(section, "exclude"):
                    problems.append(f"{label} tools.exclude: {name} is the base of the installation and stays")
            decisions[name] = (True, "platform", "")
        elif name in _names(own, "exclude"):
            decisions[name] = (False, "employee", "excluded in employee.yml")
        elif name in _names(own, "add"):
            decisions[name] = (True, "employee", "")
        elif name in _names(org, "exclude"):
            decisions[name] = (False, "organization", "excluded in organization.yml")
        elif name in _names(org, "add"):
            decisions[name] = (True, "organization", "")
        elif by_default:
            decisions[name] = (True, "platform", "")
        else:
            decisions[name] = (False, "platform", "not included by default; add it in tools.add")
    return decisions, problems


def mcp_servers(organization, employee, name):
    """MCP connections described for this employee: {server: (level, url, token_env)}, problems."""
    servers, problems = {}, []
    sections = (("organization.yml", "organization", (organization or {}).get("mcp")),
                ("employee.yml", "employee", (employee or {}).get("mcp")))
    for label, level, section in sections:
        if section is None:
            continue
        if not isinstance(section, dict):
            problems.append(f"{label} mcp: expected one entry per server")
            continue
        for server, entry in section.items():
            server = str(server)
            if not MCP_NAME.fullmatch(server) or not isinstance(entry, dict):
                problems.append(f"{label} mcp.{server}: a lowercase name with url and token_env")
                continue
            unknown = sorted(set(entry) - MCP_FIELDS)
            if unknown:
                problems.append(f"{label} mcp.{server}: {', '.join(unknown)} not used; values and keys do not "
                                "belong in the repository, name the environment variable in token_env")
            url, token = str(entry.get("url") or ""), entry.get("token_env")
            if not re.match(r"https?://", url):
                problems.append(f"{label} mcp.{server}: url must start with https://")
                continue
            if token is not None and not ENV_NAME.fullmatch(str(token)):
                problems.append(f"{label} mcp.{server}: token_env must be a variable name such as TASKS_TOKEN")
                continue
            if level == "organization":
                who = entry.get("employees")
                if who is None:
                    problems.append(f"organization.yml mcp.{server}: employees is missing (a list of names or all)")
                    continue
                if who != "all" and name not in [str(w) for w in (who if isinstance(who, list) else [who])]:
                    continue
            servers[server] = (level, url, str(token) if token else None)
    return servers, problems


def described_secrets(organization, employee, name):
    """Keys the description names for this installation, without values: ({NAME: (level, what)}, admins, problems).

    organization.yml: secrets: {admin: [age public keys], keys: {NAME: {what, to: all | [employees]}}}
    employee.yml (and an assistant's description): secrets: {keys: {NAME: {what}}}"""
    keys, admins, problems = {}, [], []
    for label, level, section in (("organization.yml", "organization", (organization or {}).get("secrets")),
                                  ("employee.yml", "employee", (employee or {}).get("secrets"))):
        if section is None:
            continue
        if not isinstance(section, dict):
            problems.append(f"{label} secrets: expected admin and keys")
            continue
        for field in sorted(set(section) - {"admin", "keys"}):
            problems.append(f"{label} secrets.{field}: unknown field, not used")
        for key in _names(section, "admin"):
            if level != "organization" or not key.startswith(("age1", "ssh-")):
                problems.append(f"{label} secrets.admin: {key[:12]}… — age public keys, in organization.yml only")
            elif key not in admins:
                admins.append(key)
        entries = section.get("keys") or {}
        if not isinstance(entries, dict):
            problems.append(f"{label} secrets.keys: one entry per key name")
            continue
        for key, entry in entries.items():
            key = str(key)
            if not ENV_NAME.fullmatch(key) or not isinstance(entry, dict) or not str(entry.get("what") or "").strip():
                problems.append(f"{label} secrets.keys.{key}: a CAPITAL_NAME with what (and to in organization.yml)")
                continue
            unknown = sorted(set(entry) - SECRET_FIELDS)
            if unknown:
                problems.append(f"{label} secrets.keys.{key}: {', '.join(unknown)} not used; values never go into "
                                "the repository, the store holds them")
            if level == "organization":
                who = entry.get("to")
                if who is None:
                    problems.append(f"organization.yml secrets.keys.{key}: to is missing (a list of names or all)")
                    continue
                if who != "all" and name not in [str(w) for w in (who if isinstance(who, list) else [who])]:
                    continue
            elif "to" in entry:
                problems.append(f"employee.yml secrets.keys.{key}: to belongs to organization.yml")
            keys[key] = (level, str(entry["what"]).strip())
    return keys, admins, problems


def described_spaces(organization, employee, name):
    """Where the agent works (С53): ({id: entry}, problems). An entry only refers to what is described elsewhere.

    organization.yml: spaces: {ID: {what, kind, via, to: all | [employees], access: read | write, map}}
    employee.yml: spaces: {exclude: [ID], ID: {what, kind, via, access, map}}"""
    spaces, problems = {}, []
    excluded = []
    for label, level, section in (("organization.yml", "organization", (organization or {}).get("spaces")),
                                  ("employee.yml", "employee", (employee or {}).get("spaces"))):
        if section is None:
            continue
        if not isinstance(section, dict):
            problems.append(f"{label} spaces: one entry per space")
            continue
        for key, entry in section.items():
            key = str(key)
            if key == "exclude" and level == "employee":
                excluded += _names(section, "exclude")
                continue
            if not MCP_NAME.fullmatch(key) or not isinstance(entry, dict):
                problems.append(f"{label} spaces.{key}: a lowercase name with what, kind and via")
                continue
            unknown = sorted(set(entry) - SPACE_FIELDS)
            if unknown:
                problems.append(f"{label} spaces.{key}: {', '.join(unknown)} not used")
            what, kind, via = str(entry.get("what") or "").strip(), entry.get("kind"), str(entry.get("via") or "")
            access = entry.get("access", "read")
            if not what or kind not in SPACE_KINDS or not SPACE_VIA.fullmatch(via) or access not in ("read", "write"):
                problems.append(f"{label} spaces.{key}: what, kind ({'|'.join(SPACE_KINDS)}), via "
                                "(mcp:, tool:, connections:, folder: or groups) and access (read | write)")
                continue
            if ".." in via or ".." in str(entry.get("map") or ""):
                problems.append(f"{label} spaces.{key}: paths stay inside the organization repository")
                continue
            if level == "organization":
                who = entry.get("to")
                if who is None:
                    problems.append(f"organization.yml spaces.{key}: to is missing (a list of names or all)")
                    continue
                if who != "all" and name not in [str(w) for w in (who if isinstance(who, list) else [who])]:
                    continue
            elif "to" in entry:
                problems.append(f"employee.yml spaces.{key}: to belongs to organization.yml")
            spaces[key] = {"level": level, "what": what, "kind": kind, "via": via, "access": access,
                           "map": str(entry["map"]) if entry.get("map") else ""}
    for key in excluded:
        if key not in spaces:
            problems.append(f"employee.yml spaces.exclude: {key} is not a space of this employee")
        spaces.pop(key, None)
    return spaces, problems


def space_rows(spaces, tool_rows, clone=None, organization=None, employee=None):
    """One row per space: its state comes from what via refers to, and from the map file being there.

    what and reason are words without paths or addresses: a guest's view shows them."""
    tools_by_id = {r["id"]: r for r in tool_rows}
    rows = []
    for key, space in spaces.items():
        via, state, reason, action = space["via"], "ready", "", ""
        kind, _, target = via.partition(":")
        if kind in ("mcp", "tool"):
            row = tools_by_id.get(f"mcp:{target}" if kind == "mcp" else target)
            if row is None:
                state, reason = "needs_setup", "подключение не описано"
                action = f"describe {via} in organization.yml or employee.yml"
            else:
                state, reason = SPACE_STATES.get(row["state"], ("error", "ошибка подключения"))
                action = row.get("action", "")
        elif kind == "connections":
            if not ((organization or {}).get("connections") or {}).get(target):
                state, reason, action = "needs_setup", "подключение не описано", f"add connections.{target}"
        elif kind == "folder":
            if clone is None or not (clone / target).is_dir():
                state, reason, action = "needs_setup", "папки нет", f"create {target} in the organization repository"
        else:  # groups: the employee's groups and the bridge's settings for each
            groups = (employee or {}).get("groups") or []
            if groups:
                reason = f"групп: {len(groups)}"
            else:
                state, reason, action = "needs_setup", "групп нет", "list the groups in employee.yml groups"
        if state == "ready" and space["map"]:
            path = space["map"].split("#", 1)[0]
            if clone is None or not (clone / path).is_file():
                state, reason, action = "needs_setup", "нет описания, что где лежит", f"write {path}"
        rows.append({"id": key, "what": space["what"], "kind": space["kind"], "level": space["level"],
                     "via": via, "access": space["access"], "map": space["map"],
                     "state": state, "reason": reason, "action": action})
    return rows


# --- running commands as the agent's account -------------------------------------------------------------

class Account:
    """The account that runs the agent: the current user, or another one through sudo (agents' BB)."""

    def __init__(self, name=None):
        entry = pwd.getpwnam(name) if name else pwd.getpwuid(os.getuid())
        self.name, self.home, self.uid, self.gid = entry.pw_name, Path(entry.pw_dir), entry.pw_uid, entry.pw_gid
        self.other = entry.pw_uid != os.getuid()

    def run(self, *args, check=False, timeout=900):
        prefix = ["sudo", "-u", self.name, "-H", "--"] if self.other else []
        return subprocess.run([*prefix, *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=check, timeout=timeout, cwd="/" if self.other else None)

    def which(self, program):
        if not self.other:
            return shutil.which(program)
        found = self.run("sh", "-c", f'command -v "{program}"')
        return found.stdout.strip() or None

    def give(self, path):
        if self.other:
            for p in [path, *path.rglob("*")]:
                os.chown(p, self.uid, self.gid, follow_symlinks=False)


def _fetch(repo, ref, path, cache):
    """A file or folder of a git tag, fetched once per installation run."""
    key = (repo, ref)
    if key not in cache:
        folder = Path(tempfile.mkdtemp(prefix="vallis-tools-"))
        folder.chmod(0o755)
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch", ref, "--filter=blob:none",
                        "--no-checkout", repo, str(folder)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cache[key] = folder
    folder = cache[key]
    subprocess.run(["git", "-C", str(folder), "sparse-checkout", "set", "--no-cone", f"/{path}"], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", str(folder), "checkout", "--quiet", ref], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    target = folder / path
    if not target.exists():
        raise FileNotFoundError(f"{path} is not in {repo} {ref}")
    for p in [target, *(target.rglob("*") if target.is_dir() else [])]:
        p.chmod(0o755 if p.is_dir() else 0o644)
    return target


def _err(error):
    if isinstance(error, subprocess.CalledProcessError):
        text = (error.stderr or error.stdout or "") if isinstance(error.stderr or error.stdout, str) else ""
        return (text.strip().splitlines() or [f"exit code {error.returncode}"])[-1][:300]
    return str(error)[:300]


# --- one line of the list: state and installation --------------------------------------------------------

def _row(name, tool, level, state, reason="", action=""):
    return {"id": name, "what": tool.get("what", ""), "kind": tool.get("kind", "mcp"), "level": level,
            "state": state, "reason": reason, "action": action}


def plugin_id(name, tool):
    """The BB plugin id of a plugin line: the line's name unless the plugin is called otherwise."""
    return tool.get("plugin_id", name)


def wait_for_plugins(bb, names, timeout=300, step=5):
    """bb plugin install builds in the background: wait until the plugins are listed, so the state is not stale."""
    import time
    deadline = time.monotonic() + timeout
    while True:
        installed = plugin_list(bb)
        missing = [n for n in names if n not in installed]
        if not missing or time.monotonic() > deadline:
            return installed
        time.sleep(step)


def _plugin_state(name, tool, level, plugins, bb_home, errors):
    plugin = plugins.get(plugin_id(name, tool))
    if not plugin:
        if name in errors:
            return _row(name, tool, level, "error", errors[name],
                        "give this server read access to the source, or pass --tool-source "
                        f"{name}=<path or git source> to setup.py")
        return _row(name, tool, level, "needs_setup", "the plugin is not installed", RERUN)
    if not plugin.get("enabled", True):
        return _row(name, tool, level, "needs_setup", "the plugin is installed but disabled",
                    f"bb plugin enable {plugin_id(name, tool)}")
    check = tool.get("login_check")
    if check and bb_home and not (bb_home / check.removeprefix("~/")).exists():
        return _row(name, tool, level, "needs_login", f"no login: {tool.get('login', check)}",
                    "log in the account that runs BB (for Codex: codex login)")
    note = f"needs a login: {tool['login']}" if tool.get("login") and not check else ""
    return _row(name, tool, level, "installed", note)


def _service_state(name, tool, level, account):
    """A service of the server (one per server, outside any BB): installed by the services installer, not here.

    The service may write its own state (state_file: {"state", "reason", "profiles"}); units are checked for the
    profiles it lists, or all of them when there is no such file."""
    found = [c for c in tool.get("check_commands", []) if account.which(c) or Path(c).exists()]
    try:
        own = json.loads(Path(tool["state_file"]).read_text(encoding="utf-8")) if tool.get("state_file") else {}
    except (OSError, ValueError):
        own = {}
    if own.get("state") in STATES and own["state"] != "installed":
        return _row(name, tool, level, own["state"], str(own.get("reason") or ""),
                    tool.get("install", "install the server services"))
    if not found and own.get("state") == "installed":
        # The service's own word: its command may be outside the PATH this status runs with
        # (a BB service often has no /usr/local/bin in PATH).
        found = [tool["state_file"]]
    if not found:
        return _row(name, tool, level, "needs_setup", "the service of the server is not installed",
                    tool.get("install", "install the server services"))
    units = tool.get("check_units") or {}
    if isinstance(units, dict):
        profiles = own.get("profiles") if isinstance(own.get("profiles"), list) else list(units)
        units = [units[p] for p in profiles if p in units]
    idle = [u for u in units if subprocess.run(
        ["systemctl", "is-active", "--quiet", u], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode]
    if idle:
        return _row(name, tool, level, "error", f"installed, but not running: {', '.join(idle)}",
                    f"systemctl start {' '.join(idle)}")
    # A service that says itself which of its parts really start (pdf: `vallis-pdf --check` → {"PDF": true, ...}).
    if tool.get("check_run"):
        command = list(tool["check_run"])
        if "/" not in command[0] and found:          # the command found above (the account's PATH or a server path)
            command[0] = found[0]
        try:
            said = account.run(*command, timeout=300)
            parts = json.loads(said.stdout)
            if not isinstance(parts, dict) or not parts:
                raise ValueError
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return _row(name, tool, level, "error", f"{' '.join(tool['check_run'])}: no answer",
                        tool.get("install", "install the server services"))
        said_parts = "; ".join(f"{part}: {'стоит' if ok else 'не стоит'}" for part, ok in parts.items())
        required = tool.get("required_parts") or list(parts)
        if not all(parts.get(part) for part in required):
            return _row(name, tool, level, "needs_setup", said_parts, tool.get("install", "install the server services"))
        return _row(name, tool, level, "installed", said_parts)
    return _row(name, tool, level, "installed", f"service of the server: {found[0]}")


def _npm_state(name, tool, level, account):
    binary = account.which(name if tool.get("package") is None else tool["package"])
    if not binary:
        return _row(name, tool, level, "needs_setup", f"{tool['package']} is not installed", RERUN)
    agents = [a for a in tool.get("agents", []) if account.which("claude" if a == "claude-code" else a)]
    if not agents:
        return _row(name, tool, level, "needs_setup", "no agent CLI (codex, claude) for this account",
                    f"install the agent CLI, then {RERUN}")
    dry = account.run(binary, "setup", "--dry-run", "--no-input", "--agents", ",".join(agents), "--json")
    try:
        report = json.loads(dry.stdout)
        todo = [a["id"] for a in report.get("agents", []) if a.get("changes") or a.get("status") != "PASS"]
    except (ValueError, AttributeError):
        return _row(name, tool, level, "error", f"{name} setup --dry-run: {(dry.stderr or dry.stdout).strip()[-200:]}",
                    f"{RERUN}; if it repeats, run context-mode doctor")
    if todo:
        return _row(name, tool, level, "needs_setup", f"not set up for {', '.join(todo)}", RERUN)
    return _row(name, tool, level, "installed", f"for {', '.join(agents)}")


def _skill_dirs(account):
    seen, dirs = set(), []
    for d in SKILL_DIRS:
        path = account.home / d
        real = path.resolve() if path.exists() else path
        if real not in seen:
            seen.add(real)
            dirs.append(path)
    return dirs


def _skill_state(name, tool, level, account, plugins):
    if tool.get("plugin"):
        if tool["plugin"] in plugins:
            return _row(name, tool, level, "installed", f"comes with the {tool['plugin']} plugin")
        return _row(name, tool, level, "needs_setup", f"the {tool['plugin']} plugin is not installed", RERUN)
    if tool.get("present_with_plugin") in plugins:
        return _row(name, tool, level, "installed", f"through the {tool['present_with_plugin']} plugin")
    missing = [str(d) for d in _skill_dirs(account) if not (d / name / "SKILL.md").is_file()]
    if missing:
        return _row(name, tool, level, "needs_setup", "not in " + ", ".join(missing), RERUN)
    return _row(name, tool, level, "installed")


def _cli_name(tool):
    return Path(tool["path"]).name


def _cli_dir(account):
    """Where the account's agents find programs: /usr/local/bin for an agent set up by root (agents' BB),
    otherwise ~/.local/bin when it is on PATH, else the folder of node (the BB service's PATH has it)."""
    if account.other or os.getuid() == 0:
        return Path("/usr/local/bin")
    local = account.home / ".local" / "bin"
    if str(local) in os.environ.get("PATH", "").split(os.pathsep):
        return local
    node = shutil.which("node")
    if node and account.home in Path(node).resolve().parents and os.access(os.path.dirname(node), os.W_OK):
        return Path(os.path.dirname(node))
    return local


def _cli_state(name, tool, level, account):
    program = _cli_name(tool)
    if not account.which(program):
        return _row(name, tool, level, "needs_setup", f"{program} is not on the PATH of {account.name}", RERUN)
    key = account.home / ".config" / "vallis" / "age.key"
    missing = [p for p in tool.get("requires", []) if not account.which(p)]
    if missing and not key.is_file():  # age-keygen makes the account's key; afterwards sops alone does the work
        return _row(name, tool, level, "needs_setup", f"not installed: {', '.join(missing)}",
                    "apt-get install age (as root), then " + RERUN)
    missing = [p for p in tool.get("download", {}) if not account.which(p)]
    if missing:
        return _row(name, tool, level, "needs_setup", f"not installed: {', '.join(missing)}", RERUN)
    listed = account.run(program, "list", "--json")
    try:
        count = len(json.loads(listed.stdout)["secrets"])
    except (ValueError, KeyError, TypeError):
        return _row(name, tool, level, "needs_setup", f"{program} list: {(listed.stderr or listed.stdout).strip()[-200:]}",
                    f"{program} init, or {RERUN}")
    if not key.is_file() and not os.environ.get("VALLIS_AGE_KEY"):
        return _row(name, tool, level, "needs_setup", "no age key of the account", f"{program} init")
    return _row(name, tool, level, "installed", f"keys in the store: {count}")


def mcp_processes(registry, decisions, account):
    """MCP processes of the selected tools.yml lines: {name: {"level", "command", "env"}}.

    Keys of the line come from the credentials store at start (vallis-secret run), never from the settings files."""
    out = {}
    for name, tool in registry.items():
        if tool["kind"] != "mcp" or not decisions.get(name, (False,))[0]:
            continue
        command = [str(c) for c in tool["command"]]
        if tool.get("secrets"):
            command = ["vallis-secret", "run", *tool["secrets"], "--", *command]
        env = {k: str(account.home / v[2:]) if str(v).startswith("~/") else str(v)
               for k, v in (tool.get("env") or {}).items()}
        out[name] = {"level": decisions[name][1], "command": command, "env": env}
    return out


def _login_dir(tool, account):
    folder = (tool.get("login") or {}).get("dir")
    return account.home / folder[2:] if folder and folder.startswith("~/") else (Path(folder) if folder else None)


def prepare_mcp(tool, account):
    """The closed folder for the line's login (the MCP writes and refreshes the login there itself)."""
    folder = _login_dir(tool, account)
    if folder:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        account.give(folder)
    missing = [p for p in tool.get("requires", []) if not account.which(p)]
    return f"not installed: {', '.join(missing)}" if missing else None


def _mcp_tool_state(name, tool, level, account):
    missing = [p for p in tool.get("requires", []) if not account.which(p)]
    if missing:
        return _row(name, tool, level, "needs_setup", f"not installed: {', '.join(missing)}", RERUN)
    stored = {}
    if tool.get("secrets") or (tool.get("login") or {}).get("backup"):
        listed = account.run("vallis-secret", "list", "--json") if account.which("vallis-secret") else None
        try:
            stored = {r["name"]: r for r in json.loads(listed.stdout)["secrets"]} if listed else {}
        except (ValueError, KeyError, TypeError):
            stored = {}
    lacking = [k for k in tool.get("secrets", []) if k not in stored]
    if lacking:
        return _row(name, tool, level, "needs_setup", "no key of the application in the credentials store",
                    "the administrator grants " + ", ".join(lacking) + " (vallis-secret grant / receive)")
    login = tool.get("login") or {}
    what = login.get("what", "a login")
    folder = _login_dir(tool, account)
    if folder and not (folder.is_dir() and any(p.is_file() for p in folder.iterdir())):
        return _row(name, tool, level, "needs_login", f"no login: {what}",
                    "log in with the owner's yes (google-workspace skill)")
    days = login.get("max_age_days")
    if days:
        import datetime
        copy = stored.get(login.get("backup") or "")
        try:
            since = datetime.date.fromisoformat(str((copy or {}).get("updated") or "")[:10])
        except ValueError:
            since = None
        if since is None:
            return _row(name, tool, level, "installed",
                        f"the login lasts {days} days and its date is unknown: keep its copy in the store")
        age = (datetime.date.today() - since).days
        if age >= days:
            return _row(name, tool, level, "needs_login",
                        f"the login has expired: the application is in testing mode and a login lasts {days} days",
                        "log in again with the owner's yes (google-workspace skill)")
        if age >= days - 1:
            return _row(name, tool, level, "installed", "the login expires tomorrow: ask the owner to log in again")
        return _row(name, tool, level, "installed", f"the login lasts {days - age} more day(s)")
    return _row(name, tool, level, "installed")


def _hook_state(name, tool, level, account):
    script = Path(tool["path"]).name
    settings = account.home / ".claude" / "settings.json"
    try:
        registered = script in settings.read_text(encoding="utf-8")
    except OSError:
        registered = False
    if registered and (account.home / ".claude" / "hooks" / script).is_file():
        return _row(name, tool, level, "installed", "Claude Code")
    return _row(name, tool, level, "needs_setup", "the hook is not registered in ~/.claude/settings.json",
                RERUN)


# --- browser runtime of the browser-automation plugin -----------------------------------------------------
# The plugin looks for <BB data dir>/plugins/browser-automation/host-data/runtime/chrome first (an executable;
# a link or a wrapper script will do), then google-chrome, google-chrome-stable, chromium, chromium-browser on
# PATH. Installed means: that executable starts. A file that cannot load its libraries is not a browser.

BROWSER_HOST_DATA = Path("plugins") / "browser-automation" / "host-data"
BROWSER_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def bb_data_dir(bb):
    """Data folder of the BB the plugins belong to (for the agents' BB: of its own account)."""
    data = (bb("status") or {}).get("dataDir") if bb else None
    return Path(data) if data else None


def find_browser(data_dir):
    """The executable the plugin would launch, or None."""
    if data_dir:
        link = data_dir / BROWSER_HOST_DATA / "runtime" / "chrome"
        if os.access(link, os.X_OK) and link.resolve().is_file():
            return link
    for name in BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def browser_starts(path, timeout=60):
    """(True, version) when the browser runs headless, else (False, the loader's or browser's own words)."""
    try:
        done = subprocess.run([str(path), "--headless=new", "--no-sandbox", "--version"], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, _err(error)
    if done.returncode == 0 and done.stdout.strip():
        return True, done.stdout.strip()
    missing = sorted(set(re.findall(r"(lib[\w.+-]+\.so[\w.]*)", done.stderr)))
    return False, ("missing libraries: " + ", ".join(missing)) if missing else (done.stderr.strip()[:200] or f"exit {done.returncode}")


def _browser_state(name, tool, level, data_dir):
    path = find_browser(data_dir)
    if not path:
        return _row(name, tool, level, "needs_setup", "no browser on this server", RERUN)
    ok, said = browser_starts(path)
    if not ok:
        return _row(name, tool, level, "needs_setup", f"the browser does not start: {said}", RERUN)
    return None


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _apt_packages(wanted):
    """Packages to bring next to the browser: the wanted ones (first known alternative) and what they need,
    except what the system already has. Nothing is installed into the system."""
    def known(name):
        return subprocess.run(["apt-cache", "show", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    roots = []
    for spec in wanted:
        choice = next((n for n in spec.split("|") if known(n)), None)
        if choice:
            roots.append(choice)
    if not roots:
        return []
    deps = subprocess.run(["apt-cache", "depends", "--recurse", "--no-recommends", "--no-suggests", "--no-conflicts",
                           "--no-breaks", "--no-replaces", "--no-enhances", *roots],
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    names = {l.strip() for l in deps.splitlines() if l and not l.startswith(" ") and not l.startswith("<")}
    def installed(n):
        q = subprocess.run(["dpkg-query", "-W", "-f=${Status}", n], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return "install ok installed" in q.stdout
    # Only libraries: the dependency tree also offers fonts and tools the headless browser does not load.
    return sorted(n for n in names | set(roots) if n.startswith("lib") and not installed(n))


def install_browser(tool, data_dir, fetch=None):
    """Put the pinned Chrome for Testing and the libraries it lacks into the plugin's data folder, behind
    runtime/chrome. A browser that already starts there is kept. Returns an error text or None."""
    import platform
    runtime_spec = tool.get("runtime") or {}
    cft = runtime_spec.get("chrome_for_testing")
    if not cft or not data_dir:
        return None
    current = find_browser(data_dir)
    if current and browser_starts(current)[0]:
        return None
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        return "the pinned browser build is for Linux x86-64 only; install Chrome on this host"
    runtime = data_dir / BROWSER_HOST_DATA / "runtime"
    home = runtime / "chrome-for-testing" / cft["version"]
    runtime.mkdir(parents=True, exist_ok=True)
    owner = runtime.parent.stat()
    with tempfile.TemporaryDirectory(dir=runtime) as tmp:
        tmp = Path(tmp)
        archive = tmp / "chrome-linux64.zip"
        url = cft["url"].format(version=cft["version"])
        try:
            (fetch or _fetch_url)(url, archive)
        except Exception as error:
            return f"download of {url} failed: {_err(error)}"
        if _sha256(archive) != cft["sha256"]:
            return f"checksum of {url} does not match the pinned one; nothing installed"
        _unzip(archive, tmp / "unpacked")
        libs = tmp / "unpacked" / "libs"
        libs.mkdir()
        packages = _apt_packages(runtime_spec.get("libraries", []))
        if packages:
            debs = tmp / "debs"
            debs.mkdir()
            got = subprocess.run(["apt-get", "download", *packages], cwd=debs, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if got.returncode != 0:
                return f"apt-get download failed: {got.stderr.strip()[:200]}"
            for deb in debs.glob("*.deb"):
                subprocess.run(["dpkg-deb", "-x", str(deb), str(tmp / "unpacked" / "root")], check=True)
            for lib in (tmp / "unpacked" / "root").glob("**/*.so*"):
                if lib.is_file() or lib.is_symlink():
                    target = libs / lib.name
                    if not target.exists():
                        shutil.copy2(lib, target, follow_symlinks=True)
            shutil.rmtree(tmp / "unpacked" / "root", ignore_errors=True)
        if home.exists():
            shutil.rmtree(home)
        home.parent.mkdir(parents=True, exist_ok=True)
        (tmp / "unpacked").rename(home)
    binary = home / "chrome-linux64" / "chrome"
    wrapper = home / "chrome"
    wrapper.write_text("#!/bin/sh\n# Chrome for Testing with its own libraries; put by the platform installer.\n"
                       f'export LD_LIBRARY_PATH="{home / "libs"}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"\n'
                       f'exec "{binary}" "$@"\n')
    wrapper.chmod(0o755)
    ok, said = browser_starts(wrapper)
    if not ok:
        return f"the browser was put in place but does not start: {said}"
    link = runtime / "chrome"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(wrapper)
    for path in [runtime, *runtime.rglob("*")]:
        try:
            os.lchown(path, owner.st_uid, owner.st_gid)
        except PermissionError:
            pass
    return None


def _unzip(archive, target):
    """Unpack keeping the executable bits (zipfile alone drops them)."""
    import zipfile
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            path = Path(z.extract(info, target))
            mode = info.external_attr >> 16
            if mode and not info.is_dir():
                path.chmod(mode & 0o777)


def _fetch_url(url, path):
    """A plain download (the browser checks its own sha256 after it)."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=600) as answer, open(path, "wb") as out:
        shutil.copyfileobj(answer, out)


def tool_state(name, tool, decision, account, plugins, bb_home, errors, data_dir=None):
    selected, level, reason = decision
    if not selected:
        return _row(name, tool, level, "excluded", reason)
    try:
        if tool["kind"] == "plugin":
            row = _plugin_state(name, tool, level, plugins, bb_home, errors)
            if row["state"] == "installed" and tool.get("runtime", {}).get("chrome_for_testing"):
                row = _browser_state(name, tool, level, data_dir) or row
        elif tool["kind"] == "service":
            row = _service_state(name, tool, level, account)
        elif tool["kind"] == "npm":
            row = _npm_state(name, tool, level, account)
        elif tool["kind"] == "skill":
            row = _skill_state(name, tool, level, account, plugins)
        elif tool["kind"] == "cli":
            row = _cli_state(name, tool, level, account)
        elif tool["kind"] == "mcp":
            row = _mcp_tool_state(name, tool, level, account)
        else:
            row = _hook_state(name, tool, level, account)
    except Exception as error:  # a broken check is a state, not a crash of the whole list
        return _row(name, tool, level, "error", _err(error), RERUN)
    setting = tool.get("setting")
    if row["state"] == "installed" and setting:
        env, file = setting.get("env"), setting.get("file")
        stored = False
        if env and account.which("vallis-secret"):
            listed = account.run("vallis-secret", "list", "--json")
            try:
                stored = any(r["name"] == env for r in json.loads(listed.stdout)["secrets"])
            except (ValueError, KeyError, TypeError):
                stored = False
        if not stored and not (env and os.environ.get(env)) and not (file and (account.home / file.removeprefix("~/")).is_file()):
            where = " or ".join(x for x in (env and f"the store ({env})", env and f"the {env} variable", file) if x)
            row.update(state="needs_setup", reason=f"no {setting.get('what', 'setting')}: {where}",
                       action=f"put it into {where} (not into a repository)")
    if row["state"] != "installed" and name in errors and row["state"] != "error":
        row.update(state="error", reason=errors[name], action=f"fix the reason, then {RERUN}")
    return row


def install_tool(name, tool, account, plugins, bb, cache, source=None, admins=()):
    """Install one selected line; returns an error text or None. Plugins go through bb, the rest to the account."""
    kind = tool["kind"]
    if kind == "service":
        return None  # one per server, the services installer puts it; the state says what is missing
    if kind == "plugin":
        if plugin_id(name, tool) in plugins:
            return install_browser(tool, bb_data_dir(bb)) if tool.get("runtime") else None
        args = ["plugin", "install", source or tool["source"]]
        if tool.get("subdirectory") and not source:
            args += ["--subdirectory", tool["subdirectory"]]
        if tool.get("tag_prefix") and not source:
            args += ["--tag-prefix", tool["tag_prefix"]]
        try:
            bb(*args, "--yes")
        except subprocess.CalledProcessError as error:
            return f"bb plugin install failed: {_err(error)} (is the source readable from this server?)"
        return install_browser(tool, bb_data_dir(bb)) if tool.get("runtime") else None
    if kind == "npm":
        package = f"{tool['package']}@{tool['version']}"
        binary = account.which(tool["package"])
        current = account.run(binary, "--version").stdout.strip() if binary else ""
        if current != str(tool["version"]):
            npm = shutil.which("npm")
            if not npm:
                return "npm is not found next to node"
            done = subprocess.run([npm, "install", "-g", package, "--no-fund", "--no-audit"], text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if done.returncode:
                return f"npm install -g {package}: {(done.stderr or done.stdout).strip()[-200:]}"
            binary = account.which(tool["package"])
            if not binary:
                return f"{tool['package']} is installed but not on the PATH of {account.name}"
        agents = [a for a in tool.get("agents", []) if account.which("claude" if a == "claude-code" else a)]
        if not agents:
            return None  # the state says what is missing
        done = account.run(binary, "setup", "--yes", "--no-input", "--agents", ",".join(agents), "--json")
        if done.returncode:
            return f"{tool['package']} setup: {(done.stderr or done.stdout).strip()[-200:]}"
        return None
    if kind == "cli":
        return install_cli(tool, account, admins)
    if kind == "mcp":
        return prepare_mcp(tool, account)  # the settings files are written with the described MCP (write_mcp)
    if kind == "skill":
        if tool.get("plugin") or tool.get("present_with_plugin") in plugins:
            return None
        try:
            origin = _fetch(tool["repo"], tool["ref"], tool["path"], cache)
        except (subprocess.CalledProcessError, FileNotFoundError) as error:
            return f"skill source {tool['repo']} {tool['ref']}: {_err(error)}"
        for skills in _skill_dirs(account):
            target = skills / name
            mark = target / SOURCE_MARK
            if target.exists() and not mark.is_file():
                continue  # the account's own skill with this name wins
            if target.exists():
                shutil.rmtree(target)
            skills.mkdir(parents=True, exist_ok=True)
            shutil.copytree(origin, target)
            mark.write_text(f"{tool['repo']} {tool['ref']} {tool['path']}\n", encoding="utf-8")
            account.give(skills.parent)
        return None
    try:
        script = _fetch(tool["repo"], tool["ref"], tool["path"], cache)
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        return f"hook source {tool['repo']} {tool['ref']}: {_err(error)}"
    if account.run(sys.executable, str(script), "--check").returncode == 0:
        return None
    done = account.run(sys.executable, str(script), "--apply")
    return None if done.returncode == 0 else f"{script.name} --apply: {(done.stderr or done.stdout).strip()[-200:]}"


def _download(url, sha256, target):
    import hashlib
    import urllib.request
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".download-")
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=120) as response:
            for chunk in iter(lambda: response.read(1 << 16), b""):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha256:
            raise ValueError(f"sha256 of {url} does not match tools.yml")
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def install_cli(tool, account, admins=()):
    """The command into the account's program folder, its pinned downloads next to it, the account's key."""
    folder = _cli_dir(account)
    arch = ARCH.get(os.uname().machine)
    for program, spec in (tool.get("download") or {}).items():
        version_args = spec.get("version_args", ["--version", "--disable-version-check"])
        if account.which(program) and spec["version"].lstrip("v") in account.run(program, *version_args).stdout:
            continue
        if not arch or arch not in spec.get("sha256", {}):
            return f"{program}: no pinned build for {os.uname().machine}"
        target = folder / program
        try:
            if spec.get("files"):
                # A pinned archive (tar.gz): its named programs go next to the command, nothing else is kept.
                import tarfile
                archive = folder / f".{program}-{spec['version']}.tar.gz"
                _download(spec["url"].format(arch=arch), spec["sha256"][arch], archive)
                try:
                    with tarfile.open(archive) as tar:
                        for member in spec["files"]:
                            data = tar.extractfile(member.format(arch=arch))
                            out = folder / Path(member).name
                            out.write_bytes(data.read())
                            out.chmod(0o755)
                            if account.home in out.parents:
                                account.give(out)
                finally:
                    archive.unlink(missing_ok=True)
            else:
                _download(spec["url"].format(arch=arch), spec["sha256"][arch], target)
        except (OSError, ValueError, KeyError, AttributeError) as error:
            return f"{program} {spec['version']}: {error}"
        if account.home in target.parents:
            account.give(target)
    source = PACKAGE / tool["path"]
    target = folder / _cli_name(tool)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
        target.chmod(0o755)
    except OSError as error:
        return f"{target}: {error}"
    missing = [p for p in tool.get("requires", []) if not account.which(p)]
    if missing:
        return f"not installed: {', '.join(missing)}"
    done = account.run(str(target), "init")
    if done.returncode:
        return f"{target.name} init: {(done.stderr or done.stdout).strip()[-200:]}"
    if admins:
        config = account.home / ".config" / "vallis"
        recipients = config / "admin.recipients"
        text = "# administrators of the organization (organization.yml secrets.admin)\n" + "\n".join(admins) + "\n"
        if not recipients.exists() or recipients.read_text(encoding="utf-8") != text:
            recipients.write_text(text, encoding="utf-8")
            recipients.chmod(0o644)
            account.give(recipients)
    return None


LEAK_CHECK = """#!/bin/sh
# vallis: values of the credentials store never go into a commit (managed by setup.py)
for program in "$(command -v vallis-secret 2>/dev/null)" {installed} "$HOME/.local/bin/vallis-secret" /usr/local/bin/vallis-secret; do
  [ -n "$program" ] && [ -x "$program" ] && exec "$program" leaks --git-staged
done
echo "vallis: WARNING — vallis-secret is not found, this commit is NOT checked for values of the credentials store" >&2
exit 0
"""


def install_leak_check(repo, program=None):
    """A pre-commit hook of the workspace's git copy; someone's own pre-commit hook is left alone (returns a note)."""
    git = repo / ".git"
    if not git.is_dir():
        return None
    hook = git / "hooks" / "pre-commit"
    if hook.exists() and "vallis: values of the credentials store" not in hook.read_text(encoding="utf-8", errors="replace"):
        return f"{hook} is someone's own: add `vallis-secret leaks --git-staged` to it"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(LEAK_CHECK.replace("{installed}", shlex_quote(str(program or ""))), encoding="utf-8")
    hook.chmod(0o755)
    return None


def shlex_quote(text):
    import shlex
    return shlex.quote(text) if text else "''"


def access_rows(account, program="vallis-secret"):
    """The agent's own accesses for its self-awareness (С43): names and purpose, never values."""
    if not account.which(program):
        return []
    listed = account.run(program, "list", "--json")
    try:
        rows = json.loads(listed.stdout)["secrets"]
    except (ValueError, KeyError, TypeError):
        return []
    return [{"name": r.get("name"), "what": r.get("what") or "", "level": r.get("level") or "",
             "source": r.get("source") or "", "state": r.get("state") or ""} for r in rows]


def secret_rows(account, keys, program="vallis-secret"):
    """One row per described key: in the store and decryptable, or what to do."""
    rows = []
    if not keys:
        return rows
    listed = account.run(program, "list", "--json", "--verify") if account.which(program) else None
    try:
        present = {r["name"]: r for r in json.loads(listed.stdout)["secrets"]} if listed else {}
    except (ValueError, KeyError, TypeError):
        present = {}
    for key, (level, what) in sorted(keys.items()):
        tool = {"what": what, "kind": "secret"}
        row = present.get(key)
        if row and row.get("state") == "present":
            rows.append(_row(f"secret:{key}", tool, level, "installed", f"{row.get('source', '')} {row.get('created', '')}".strip()))
        elif row:
            rows.append(_row(f"secret:{key}", tool, level, "error", row.get("state", ""),
                             "the administrator grants it again to this account's key"))
        elif level == "organization":
            rows.append(_row(f"secret:{key}", tool, level, "needs_setup", "not in the store",
                             f"the administrator: vallis-secret grant {key} …; here: vallis-secret receive <file>"))
        else:
            rows.append(_row(f"secret:{key}", tool, level, "needs_setup", "not in the store",
                             f"vallis-secret ask {key} --what \"{what}\""))
    return rows


# --- MCP connections of the description ------------------------------------------------------------------

def _tracked(root, path):
    return subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=root, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def _toml_block(servers):
    lines = [TOML_BEGIN]
    for server, spec in sorted(servers.items()):
        lines.append(f"[mcp_servers.{json.dumps(server)}]")
        if isinstance(spec, dict):  # an MCP process of a tools.yml line
            lines += [f"command = {json.dumps(spec['command'][0])}", f"args = {json.dumps(spec['command'][1:])}",
                      "startup_timeout_sec = 120", "tool_timeout_sec = 180"]
            if spec.get("env"):
                lines.append(f"[mcp_servers.{json.dumps(server)}.env]")
                lines += [f"{json.dumps(k)} = {json.dumps(v)}" for k, v in sorted(spec["env"].items())]
            continue
        _, url, token = spec
        lines.append(f"url = {json.dumps(url)}")
        if token:
            lines.append(f"bearer_token_env_var = {json.dumps(token)}")
    return "\n".join(lines + [TOML_END]) + "\n"


def _without_block(text):
    return re.sub(rf"\n?{re.escape(TOML_BEGIN)}.*?{re.escape(TOML_END)}\n?", "\n", text, flags=re.S).strip("\n")


def write_mcp(root, servers, stored=()):
    """Described MCP connections into .mcp.json (Claude Code) and .codex/config.toml (Codex) of the folder.

    A file kept in git is not changed: the connection then waits for the file to leave git. Returns errors by server."""
    errors = {}
    managed_file = root / "local" / "mcp-managed.json"
    try:
        managed = set(json.loads(managed_file.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        managed = set()
    claude = root / ".mcp.json"
    if servers or managed:
        if _tracked(root, ".mcp.json"):
            for server in servers:
                errors[server] = ".mcp.json is kept in git: move its servers into the description and remove the file from git"
        else:
            data = json.loads(claude.read_text(encoding="utf-8")) if claude.exists() else {}
            entries = data.setdefault("mcpServers", {})
            for server in managed - set(servers):
                entries.pop(server, None)
            for server, spec in servers.items():
                if server in entries and server not in managed:
                    errors[server] = f".mcp.json already has its own {server}: remove it or rename the description"
                    continue
                if isinstance(spec, dict):
                    entries[server] = {"type": "stdio", "command": spec["command"][0], "args": spec["command"][1:],
                                       "env": dict(spec.get("env") or {})}
                    continue
                _, url, token = spec
                entry = {"type": "http", "url": url}
                if token and token in stored:
                    entry["headersHelper"] = f"vallis-secret header {token}"  # read by Claude Code, not the agent
                elif token:
                    entry["headers"] = {"Authorization": f"Bearer ${{{token}}}"}
                entries[server] = entry
            claude.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        codex = root / ".codex" / "config.toml"
        if _tracked(root, ".codex/config.toml"):
            for server in servers:
                errors.setdefault(server, ".codex/config.toml is kept in git: move its servers into the description "
                                          "and remove the file from git")
        else:
            import tomllib
            text = codex.read_text(encoding="utf-8") if codex.exists() else ""
            rest = _without_block(text)
            own = tomllib.loads(rest).get("mcp_servers", {}) if rest else {}
            ours = {s: v for s, v in servers.items() if s not in own}
            for server in set(servers) - set(ours):
                errors.setdefault(server, f".codex/config.toml already has its own {server}")
            codex.parent.mkdir(exist_ok=True)
            codex.write_text((rest + "\n\n" if rest else "") + (_toml_block(ours) if ours else ""), encoding="utf-8")
    managed_file.parent.mkdir(mode=0o700, exist_ok=True)
    managed_file.write_text(json.dumps(sorted(servers)) + "\n", encoding="utf-8")
    return errors


def mcp_state(root, servers, errors, stored=()):
    rows = []
    try:
        claude = json.loads((root / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers", {})
    except (OSError, ValueError):
        claude = {}
    try:
        import tomllib
        codex = tomllib.loads((root / ".codex" / "config.toml").read_text(encoding="utf-8")).get("mcp_servers", {})
    except (OSError, ValueError):
        codex = {}
    for server, spec in sorted(servers.items()):
        if isinstance(spec, dict):
            continue  # an MCP process of a tools.yml line has the line's own row
        level, url, token = spec
        tool = {"what": url, "kind": "mcp"}
        name = f"mcp:{server}"
        if server in errors:
            rows.append(_row(name, tool, level, "error", errors[server], f"fix the reason, then {RERUN}"))
        elif server not in claude or server not in codex:
            missing = [f for f, have in ((".mcp.json", claude), (".codex/config.toml", codex)) if server not in have]
            rows.append(_row(name, tool, level, "needs_setup", "not in " + ", ".join(missing), RERUN))
        elif token and token in stored and os.environ.get(token):
            rows.append(_row(name, tool, level, "installed", f"Claude Code: from the store; Codex: {token}"))
        elif token and token in stored:
            rows.append(_row(name, tool, level, "needs_login",
                             f"Claude Code reads {token} from the store; Codex reads the {token} variable",
                             f"for Codex start the BB service through: vallis-secret run {token} -- <its command>"))
        elif token:
            rows.append(_row(name, tool, level, "needs_login", f"the key is read from {token}",
                             f"put it into the store (vallis-secret ask {token} or grant/receive) and describe it in "
                             f"secrets.keys, or set {token} in the environment of the BB service (not in the repository)"))
        else:
            rows.append(_row(name, tool, level, "installed"))
    return rows


# --- state of the whole installation ---------------------------------------------------------------------

def read_errors(path):
    """Errors of the last installation run: {"tools": {id: reason}, "mcp": {server: reason}}."""
    try:
        errors = json.loads(path.read_text(encoding="utf-8")).get("errors", {})
        return {"tools": dict(errors.get("tools", {})), "mcp": dict(errors.get("mcp", {}))}
    except (OSError, ValueError, AttributeError, TypeError):
        return {"tools": {}, "mcp": {}}


def save_errors(path, tools_errors, mcp_errors=None):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"errors": {"tools": tools_errors, "mcp": mcp_errors or {}}}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def report(kind, rows, problems, who=None, spaces=None, accesses=None):
    return {"format": FORMAT, "kind": kind, "for": who, "tools": rows, "spaces": spaces or [],
            "accesses": accesses or [], "problems": problems}


REPORT_FILE = STATE_FILE.parent / "tools-report.json"


def save_report(root, data):
    """The last state for the agent's own command `bb personal-assistant tools` (С43): no values, no new secrets."""
    try:
        path = Path(root) / REPORT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o640)
    except OSError:
        pass  # an unwritable folder keeps the previous report; the command says how old it is


def print_report(data, stream=sys.stdout):
    width = max((len(r["id"]) for r in data["tools"]), default=0)
    print("Tools:", file=stream)
    for row in data["tools"]:
        text = f"  {row['id']:<{width}}  {row['state']:<11}  {row['level']}"
        details = "; ".join(x for x in (row["reason"], f"to do: {row['action']}" if row["action"] else "") if x)
        print(text + (f"  — {details}" if details else ""), file=stream)
    if data.get("spaces"):
        width = max(len(r["id"]) for r in data["spaces"])
        print("Spaces:", file=stream)
        for row in data["spaces"]:
            text = f"  {row['id']:<{width}}  {row['state']:<11}  {row['access']:<5}  {row['what']}"
            details = "; ".join(x for x in (row["reason"], f"to do: {row['action']}" if row["action"] else "") if x)
            print(text + (f"  — {details}" if details else ""), file=stream)
    if data.get("accesses"):
        print("Accesses (names and purpose, no values):", file=stream)
        for row in data["accesses"]:
            print(f"  {row['name']}  {row['level']}  {row['what']}", file=stream)
    for line in data["problems"]:
        print(f"  ! {line}", file=stream)


def states(registry, decisions, account, plugins, bb_home, errors, root=None, servers=None, mcp_errors=None,
           data_dir=None, keys=None):
    rows = [tool_state(n, t, decisions[n], account, plugins, bb_home, errors, data_dir) for n, t in registry.items()]
    if root is not None and servers:
        rows += mcp_state(root, servers, mcp_errors or {}, set(keys or ()))
    rows += secret_rows(account, keys or {})
    return rows


def plugin_list(bb):
    return {p["id"]: p for p in bb("plugin", "list")["plugins"]}


def bb_command(prefix):
    def bb(*args):
        done = subprocess.run([*prefix, *args, "--json"], check=True, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd="/tmp")
        return json.loads(done.stdout) if done.stdout.strip() else {}
    return bb


# --- employee or assistant folder: what the installation is ----------------------------------------------

def describe_folder(root):
    """Kind, organization and employee descriptions of a BB project folder (employees/<name> of an organization copy)."""
    import organization as org
    if root.parent.name == "employees" and (root.parent.parent / "organization.yml").is_file():
        clone, name = root.parent.parent, root.name
        description, employee, problems = org.read_organization(clone, name)
        return "employee", name, description, employee, problems
    return None, None, {}, {}, []


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tools.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("status", "install"))
    parser.add_argument("--json", action="store_true", help="machine-readable state (format: docs/tools.md)")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="BB project folder of the installation")
    parser.add_argument("--kind", choices=KINDS, help="installation kind (default: from the folder and the bridge)")
    parser.add_argument("--account", help="agents: the agent's Linux account")
    parser.add_argument("--bb-user", help="agents: the account of the agents' BB")
    parser.add_argument("--bb-cli", help="agents: bb command of the agents' BB")
    parser.add_argument("--bb-url", help="agents: server URL of the agents' BB")
    parser.add_argument("--bb-env", action="append", default=[], help="agents: VAR=value for the bb command")
    parser.add_argument("--tool-source", action="append", default=[], metavar="ID=SOURCE",
                        help="install a plugin from another source, for example a copy brought by the administrator")
    args = parser.parse_args(argv)
    registry = load()
    sources = dict(s.split("=", 1) for s in args.tool_source if "=" in s)
    global RERUN
    if args.kind == "agents" or args.account:
        RERUN = f"run install-agent.sh install {args.account}"
        if not args.account:
            raise SystemExit("--account names the agent's Linux account.")
        account = Account(args.account)
        if args.bb_user and args.bb_cli and args.bb_url:
            bb = bb_command(["sudo", "-u", args.bb_user, "-H", "env", f"BB_SERVER_URL={args.bb_url}",
                             *args.bb_env, args.bb_cli])
            bb_home = Path(pwd.getpwnam(args.bb_user).pw_dir)
        else:
            bb, bb_home = None, None
        decisions, problems = select(registry, "agents")
        state_file = account.home / ".config" / "vallis" / "tools-state.json"
        plugins = plugin_list(bb) if bb else {}
        if args.action == "install":
            errors, cache = {}, {}
            for name, tool in registry.items():
                if not decisions[name][0] or (tool["kind"] == "plugin" and not bb):
                    continue
                error = install_tool(name, tool, account, plugins, bb, cache, sources.get(name))
                if error:
                    errors[name] = error
            for folder in cache.values():
                shutil.rmtree(folder, ignore_errors=True)
            save_errors(state_file, errors)
            account.give(state_file.parent)
            wanted = [plugin_id(n, t) for n, t in registry.items()
                      if t["kind"] == "plugin" and decisions[n][0] and n not in errors]
            plugins = wait_for_plugins(bb, wanted) if bb else {}
        if not bb:
            problems.append("BB plugins are not checked: pass --bb-user, --bb-cli and --bb-url of the agents' BB")
        errors = read_errors(state_file)["tools"]
        rows = states(registry, decisions, account, plugins, bb_home, errors, data_dir=bb_data_dir(bb) if bb else None)
        if not bb:
            rows = [r for r in rows if r["kind"] != "plugin" or r["state"] == "excluded"] + [
                _row(n, t, decisions[n][1], "error", "the agents' BB was not given", "pass --bb-user, --bb-cli, --bb-url")
                for n, t in registry.items() if t["kind"] == "plugin" and decisions[n][0]]
        data = report("agents", rows, problems, account.name, accesses=access_rows(account))
    else:
        if args.action == "install":
            raise SystemExit("An assistant or an employee is installed by setup.py.")
        root = args.workspace.expanduser().resolve()
        bb = bb_command(["bb"])
        kind, name, description, employee, problems = describe_folder(root)
        if kind is None:
            config = bb("plugin", "config", "telegram-projects")
            kind = args.kind or (config.get("values", config).get("installKind") or "assistant")
        decisions, more = select(registry, kind, description, employee)
        servers, mcp_problems = mcp_servers(description, employee, name) if name else ({}, [])
        keys, _, secret_problems = described_secrets(description, employee, name) if name else ({}, [], [])
        account = Account()
        errors = read_errors(root / STATE_FILE)
        rows = states(registry, decisions, account, plugin_list(bb), account.home, errors["tools"],
                      root, servers, errors["mcp"], bb_data_dir(bb), keys=keys)
        spaces, space_problems = described_spaces(description, employee, name) if name else ({}, [])
        space_list = space_rows(spaces, rows, root.parent.parent if name else None, description, employee)
        data = report(kind, rows, problems + more + mcp_problems + secret_problems + space_problems, name,
                      spaces=space_list, accesses=access_rows(account))
        save_report(root, data)
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
