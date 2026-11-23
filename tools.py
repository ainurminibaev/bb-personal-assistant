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
TOOL_KINDS = ("plugin", "npm", "skill", "hook", "service")
SKILL_DIRS = (Path(".agents") / "skills", Path(".claude") / "skills")
SOURCE_MARK = ".vallis-source"
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
MCP_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
MCP_FIELDS = {"url", "token_env", "employees", "what"}
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


def _plugin_state(name, tool, level, plugins, bb_home, errors):
    plugin = plugins.get(name)
    if not plugin:
        if name in errors:
            return _row(name, tool, level, "error", errors[name],
                        "give this server read access to the source, or pass --tool-source "
                        f"{name}=<path or git source> to setup.py")
        return _row(name, tool, level, "needs_setup", "the plugin is not installed", RERUN)
    if not plugin.get("enabled", True):
        return _row(name, tool, level, "needs_setup", "the plugin is installed but disabled", f"bb plugin enable {name}")
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


def tool_state(name, tool, decision, account, plugins, bb_home, errors):
    selected, level, reason = decision
    if not selected:
        return _row(name, tool, level, "excluded", reason)
    try:
        if tool["kind"] == "plugin":
            row = _plugin_state(name, tool, level, plugins, bb_home, errors)
        elif tool["kind"] == "service":
            row = _service_state(name, tool, level, account)
        elif tool["kind"] == "npm":
            row = _npm_state(name, tool, level, account)
        elif tool["kind"] == "skill":
            row = _skill_state(name, tool, level, account, plugins)
        else:
            row = _hook_state(name, tool, level, account)
    except Exception as error:  # a broken check is a state, not a crash of the whole list
        return _row(name, tool, level, "error", _err(error), RERUN)
    setting = tool.get("setting")
    if row["state"] == "installed" and setting:
        env, file = setting.get("env"), setting.get("file")
        if not (env and os.environ.get(env)) and not (file and (account.home / file.removeprefix("~/")).is_file()):
            where = " or ".join(x for x in (env and f"the {env} variable", file) if x)
            row.update(state="needs_setup", reason=f"no {setting.get('what', 'setting')}: {where}",
                       action=f"put it into {where} (not into a repository)")
    if row["state"] != "installed" and name in errors and row["state"] != "error":
        row.update(state="error", reason=errors[name], action=f"fix the reason, then {RERUN}")
    return row


def install_tool(name, tool, account, plugins, bb, cache, source=None):
    """Install one selected line; returns an error text or None. Plugins go through bb, the rest to the account."""
    kind = tool["kind"]
    if kind == "service":
        return None  # one per server, the services installer puts it; the state says what is missing
    if kind == "plugin":
        if name in plugins:
            return None
        args = ["plugin", "install", source or tool["source"]]
        if tool.get("subdirectory") and not source:
            args += ["--subdirectory", tool["subdirectory"]]
        if tool.get("tag_prefix") and not source:
            args += ["--tag-prefix", tool["tag_prefix"]]
        try:
            bb(*args, "--yes")
        except subprocess.CalledProcessError as error:
            return f"bb plugin install failed: {_err(error)} (is the source readable from this server?)"
        return None
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


# --- MCP connections of the description ------------------------------------------------------------------

def _tracked(root, path):
    return subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=root, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def _toml_block(servers):
    lines = [TOML_BEGIN]
    for server, (_, url, token) in sorted(servers.items()):
        lines += [f"[mcp_servers.{json.dumps(server)}]", f"url = {json.dumps(url)}"]
        if token:
            lines.append(f"bearer_token_env_var = {json.dumps(token)}")
    return "\n".join(lines + [TOML_END]) + "\n"


def _without_block(text):
    return re.sub(rf"\n?{re.escape(TOML_BEGIN)}.*?{re.escape(TOML_END)}\n?", "\n", text, flags=re.S).strip("\n")


def write_mcp(root, servers):
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
            for server, (_, url, token) in servers.items():
                if server in entries and server not in managed:
                    errors[server] = f".mcp.json already has its own {server}: remove it or rename the description"
                    continue
                entry = {"type": "http", "url": url}
                if token:
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


def mcp_state(root, servers, errors):
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
    for server, (level, url, token) in sorted(servers.items()):
        tool = {"what": url, "kind": "mcp"}
        name = f"mcp:{server}"
        if server in errors:
            rows.append(_row(name, tool, level, "error", errors[server], f"fix the reason, then {RERUN}"))
        elif server not in claude or server not in codex:
            missing = [f for f, have in ((".mcp.json", claude), (".codex/config.toml", codex)) if server not in have]
            rows.append(_row(name, tool, level, "needs_setup", "not in " + ", ".join(missing), RERUN))
        elif token:
            rows.append(_row(name, tool, level, "needs_login", f"the key is read from {token}",
                             f"set {token} in the environment of the BB service (not in the repository)"))
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


def report(kind, rows, problems, who=None):
    return {"format": FORMAT, "kind": kind, "for": who, "tools": rows, "problems": problems}


def print_report(data, stream=sys.stdout):
    width = max((len(r["id"]) for r in data["tools"]), default=0)
    print("Tools:", file=stream)
    for row in data["tools"]:
        text = f"  {row['id']:<{width}}  {row['state']:<11}  {row['level']}"
        details = "; ".join(x for x in (row["reason"], f"to do: {row['action']}" if row["action"] else "") if x)
        print(text + (f"  — {details}" if details else ""), file=stream)
    for line in data["problems"]:
        print(f"  ! {line}", file=stream)


def states(registry, decisions, account, plugins, bb_home, errors, root=None, servers=None, mcp_errors=None):
    rows = [tool_state(n, t, decisions[n], account, plugins, bb_home, errors) for n, t in registry.items()]
    if root is not None and servers:
        rows += mcp_state(root, servers, mcp_errors or {})
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
            plugins = plugin_list(bb) if bb else {}
        if not bb:
            problems.append("BB plugins are not checked: pass --bb-user, --bb-cli and --bb-url of the agents' BB")
        errors = read_errors(state_file)["tools"]
        rows = states(registry, decisions, account, plugins, bb_home, errors)
        if not bb:
            rows = [r for r in rows if r["kind"] != "plugin" or r["state"] == "excluded"] + [
                _row(n, t, decisions[n][1], "error", "the agents' BB was not given", "pass --bb-user, --bb-cli, --bb-url")
                for n, t in registry.items() if t["kind"] == "plugin" and decisions[n][0]]
        data = report("agents", rows, problems, account.name)
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
        account = Account()
        errors = read_errors(root / STATE_FILE)
        rows = states(registry, decisions, account, plugin_list(bb), account.home, errors["tools"],
                      root, servers, errors["mcp"])
        data = report(kind, rows, problems + more + mcp_problems, name)
    if args.json:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
