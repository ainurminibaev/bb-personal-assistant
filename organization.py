"""Organization repository: one private repository describes an organization and each of its employees.

Layout (see docs/organization.md):
    organization.yml          the organization: name, time zone, platform version, employees
    AGENTS.md -> shared/AGENTS.md, CLAUDE.md   organization rules, read by Claude Code and Codex from the parent folder
    shared/AGENTS.md, shared/skills/<skill>/   rules and skills common to all employees
    employees/<name>/employee.yml, AGENTS.md, skills/<skill>/, ...   one employee
    RUN.md                    how it is started, updated and rolled back

An employee's workspace is a sparse git copy with the root files, shared/ and employees/<name>/ only:
other employees' folders are not on the server. The BB project folder is employees/<name>.
"""
import os
import subprocess
from pathlib import Path

EMPLOYEE_FIELDS = {"kind", "machine", "bot", "provider", "model", "timezone", "groups",
                   "quiet_hours", "reminders", "description", "tools", "mcp", "secrets", "spaces"}
# С94: the passport — who does what, who gives tasks, who curates, who owns, the state. In employee.yml or in
# employees/<name>/passport.yml (the passport file wins); `bb personal-assistant team` prints the registry from them.
PASSPORT_FIELDS = {"leads_chats", "name", "position", "state", "owner", "curator", "tasks_from", "tasks_to", "duties",
                   "not_allowed", "grants_within", "chats", "accounts", "connections"}
PASSPORT_STATES = ("active", "paused", "retired")
EMPLOYEE_FIELDS = EMPLOYEE_FIELDS | PASSPORT_FIELDS
NOT_COMPARED = ("quiet_hours", "reminders")
SKILL_DIRS = (Path(".claude") / "skills", Path(".agents") / "skills")
EXCLUDE_BEGIN = "# vallis-organization: installation files (managed by setup.py)"
EXCLUDE_END = "# vallis-organization: end"
INSTALLATION_FILES = ("local/", "projects.md", ".bb/hooks/", ".codex/hooks.json", ".claude/settings.json",
                      ".mcp.json", ".codex/config.toml")


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        raise SystemExit("Organization files need PyYAML: install the python3-yaml package.")
    with open(path, encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must describe a mapping of fields.")
    return data


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE).stdout.strip()


def valid_name(name):
    return bool(name) and name not in (".", "..") and "/" not in name and not name.startswith(".")


def checkout(url, clone, employee):
    """Clone the organization repository with only this employee's folder, or update an existing copy."""
    if not valid_name(employee):
        raise SystemExit("--employee must be a folder name inside employees/.")
    cone = ["shared", f"employees/{employee}"]
    if (clone / ".git").exists():
        origin = git("remote", "get-url", "origin", cwd=clone)
        if origin != url:
            raise SystemExit(f"{clone} is a copy of {origin}, not of {url}.")
        git("sparse-checkout", "set", "--cone", *cone, cwd=clone)
        try:
            git("pull", "--ff-only", "--quiet", cwd=clone)
        except subprocess.CalledProcessError:
            raise SystemExit(f"Updating {clone} needs a fast-forward. Commit or push the employee's changes first.")
        return "updated"
    if clone.exists() and any(clone.iterdir()):
        raise SystemExit("--organization needs an empty or missing workspace folder for a new copy.")
    clone.parent.mkdir(parents=True, exist_ok=True)
    # blob:none keeps other employees' documents off the server; a file-path URL ignores the filter.
    git("clone", "--quiet", "--filter=blob:none", "--no-checkout", url, str(clone))
    clone.chmod(0o700)
    git("sparse-checkout", "set", "--cone", *cone, cwd=clone)
    git("checkout", "--quiet", cwd=clone)
    return "cloned"


def _skill_sources(clone, employee):
    """Employee skills first: an employee's own skill wins over a shared one with the same name."""
    sources, conflicts = {}, []
    for folder in (clone / "employees" / employee / "skills", clone / "shared" / "skills"):
        if not folder.is_dir():
            continue
        for skill in sorted(folder.iterdir()):
            if not (skill / "SKILL.md").is_file():
                continue
            if skill.name in sources:
                conflicts.append(f"skill {skill.name}: the employee's own skill is used instead of the shared one")
                continue
            sources[skill.name] = skill
    return sources, conflicts


def link_skills(clone, employee):
    """Shared and own skills appear in the folders Claude Code and Codex read; links follow updates."""
    root = clone / "employees" / employee
    sources, notes = _skill_sources(clone, employee)
    managed = []
    for skills_dir in SKILL_DIRS:
        target_dir = root / skills_dir
        if target_dir.is_symlink():
            raise SystemExit(f"Review the linked {target_dir} before setup.")
        target_dir.mkdir(parents=True, exist_ok=True)
        ours = (os.path.relpath(clone / "shared" / "skills", target_dir),
                os.path.relpath(root / "skills", target_dir))
        for entry in target_dir.iterdir():
            # Only links made by setup are removed: they point to shared/skills or the employee's skills/.
            if (entry.is_symlink() and entry.name not in sources
                    and os.path.dirname(os.readlink(entry)) in ours):
                entry.unlink()
        for name, source in sources.items():
            link = target_dir / name
            wanted = os.path.relpath(source, target_dir)
            if link.is_symlink() and os.readlink(link) == wanted:
                pass
            elif link.is_symlink():
                link.unlink()
                link.symlink_to(wanted)
            elif link.exists():
                notes.append(f"skill {name}: {link.relative_to(root)} is a real folder, left as it is")
                continue
            else:
                link.symlink_to(wanted)
            managed.append(f"/employees/{employee}/{skills_dir.as_posix()}/{name}")
    return managed, notes


def exclude_installation_files(clone, employee, links):
    """Installation data and setup's links stay out of git status of the organization copy."""
    exclude = clone / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if EXCLUDE_BEGIN in text:
        head, _, rest = text.partition(EXCLUDE_BEGIN)
        text = head + rest.partition(EXCLUDE_END)[2].lstrip("\n")
    lines = [f"/employees/{employee}/{p}" for p in INSTALLATION_FILES] + links
    block = "\n".join([EXCLUDE_BEGIN, *lines, EXCLUDE_END]) + "\n"
    exclude.write_text(text.rstrip("\n") + ("\n" if text.strip() else "") + block, encoding="utf-8")


def read_organization(clone, employee):
    """organization.yml, employee.yml and the layout problems found in them."""
    problems = []
    org_file = clone / "organization.yml"
    organization = load_yaml(org_file) if org_file.is_file() else {}
    if not org_file.is_file():
        problems.append("organization.yml is missing in the organization repository")
    root = clone / "employees" / employee
    if not root.is_dir():
        raise SystemExit(f"employees/{employee} is not in the organization repository.")
    config_file = root / "employee.yml"
    config = load_yaml(config_file) if config_file.is_file() else {}
    if not config_file.is_file():
        problems.append(f"employees/{employee}/employee.yml is missing")
    if employee not in [str(e) for e in organization.get("employees") or []]:
        problems.append(f"organization.yml employees: {employee} is not listed")
    if not (root / "AGENTS.md").is_file():
        problems.append(f"employees/{employee}/AGENTS.md is missing: the employee has no role")
    shared = clone / "shared" / "AGENTS.md"
    if shared.is_file():
        for name in ("AGENTS.md", "CLAUDE.md"):
            top = clone / name
            if not top.exists():
                problems.append(f"{name} at the repository root is missing: shared/AGENTS.md does not reach "
                                f"{'Codex' if name == 'AGENTS.md' else 'Claude Code'}")
    for key in sorted(set(config) - EMPLOYEE_FIELDS):
        problems.append(f"employee.yml {key}: unknown field, not used")
    passport_file = root / "passport.yml"
    passport = load_yaml(passport_file) if passport_file.is_file() else {}
    for key in sorted(set(passport) - PASSPORT_FIELDS):
        problems.append(f"passport.yml {key}: unknown field, not used")
    config = {**config, **{k: v for k, v in passport.items() if k in PASSPORT_FIELDS}}
    if config.get("state", "active") not in PASSPORT_STATES:
        problems.append(f"passport state {config.get('state')}: one of {', '.join(PASSPORT_STATES)}")
    if "leads_chats" in config and not isinstance(config["leads_chats"], bool):
        problems.append(f"passport leads_chats {config['leads_chats']}: true or false")
    problems += chat_leaders_problems(clone, organization)
    return organization, config, problems


def chat_leaders_problems(clone, organization):
    """Shared chats need exactly one default leader (leads_chats: true) among the organization's active employees:
    two answer the owner's message without a mention twice, none leave it unanswered."""
    leaders, active, said = [], [], False
    for name in [str(e) for e in organization.get("employees") or []]:
        data = {}
        for f in ("employee.yml", "passport.yml"):
            path = clone / "employees" / name / f
            if path.is_file():
                data.update(load_yaml(path) or {})
        if data.get("state", "active") != "active":
            continue
        active.append(name)
        said = said or "leads_chats" in data
        if data.get("leads_chats") is True:
            leaders.append(name)
    if len(active) < 2:
        return []
    if len(leaders) > 1:
        return [f"passport leads_chats: true at {', '.join(leaders)} — both answer the owner's message without a mention in a shared chat; keep it at one"]
    if not leaders and said:   # nobody uses the field yet: the bridge's auto decides, no warning
        return [f"passport leads_chats: nobody of {', '.join(active)} leads shared chats — the owner's message without a mention stays unanswered; set it at one"]
    return []


def curator_ids(organization, config):
    """Telegram IDs of the employee's curators for the bridge (`curators`): passport names looked up in
    organization.yml `people: {<name>: {telegram: <id>}}`. Returns the IDs and the names that have none."""
    names = config.get("curator") or []
    names = [names] if isinstance(names, str) else list(names)
    people = organization.get("people") or {}
    ids, missing = [], []
    for name in names:
        tid = (people.get(name) or {}).get("telegram") if isinstance(people.get(name), dict) else None
        if tid:
            ids.append(str(tid))
        else:
            missing.append(name)
    return ids, missing


def _same(a, b):
    return str(a).strip().lstrip("@").lower() == str(b).strip().lstrip("@").lower()


def compare(organization, config, facts):
    """One line per difference between the description and the installation.

    facts: kind, machine_id, machine_name, bot, provider, model, timezone, platform, groups (None = unknown)."""
    lines = []

    def differs(field, wanted, actual):
        if wanted is None or actual is None:
            return
        if isinstance(wanted, (list, tuple)):
            return
        if not _same(wanted, actual):
            lines.append(f"employee.yml {field}: {wanted}; installation: {actual}")

    differs("kind", config.get("kind"), facts.get("kind"))
    machine = config.get("machine")
    if machine is not None and facts.get("machine_id") is not None and not (
            _same(machine, facts["machine_id"]) or _same(machine, facts.get("machine_name") or "")):
        lines.append(f"employee.yml machine: {machine}; installation: {facts.get('machine_name') or facts['machine_id']}")
    differs("bot", config.get("bot"), facts.get("bot"))
    differs("provider", config.get("provider"), facts.get("provider"))
    differs("model", config.get("model"), facts.get("model"))
    timezone = config.get("timezone") or organization.get("timezone")
    if timezone and facts.get("timezone") and not _same(timezone, facts["timezone"]):
        lines.append(f"time zone {timezone} in the description; installation: {facts['timezone']}")
    platform = organization.get("platform")
    installed = facts.get("platform")
    if platform and installed and not (str(installed) == str(platform)
                                       or str(installed).startswith(f"{platform}.")):
        lines.append(f"organization.yml platform: {platform}; installation: {installed}")
    groups = config.get("groups")
    if groups and facts.get("groups") is not None:
        wanted = {str(g) for g in groups}
        actual = {str(g) for g in facts["groups"]}
        for group in sorted(wanted - actual):
            lines.append(f"employee.yml groups: {group} is not connected to this employee")
        for group in sorted(actual - wanted):
            lines.append(f"installation group {group} is not listed in employee.yml")
    return lines


def collect_facts(bb, project_id, machine_id):
    """What the installation actually has, read through the BB CLI; unknown values stay None."""
    facts = {"machine_id": machine_id}
    machines = {m["id"]: m for m in bb("machine", "list")}
    facts["machine_name"] = machines.get(machine_id, {}).get("name")
    config = bb("plugin", "config", "telegram-projects")
    values = config.get("values", config)
    facts["kind"] = values.get("installKind")
    facts["timezone"] = values.get("timeZone") or None
    status = bb("plugin", "rpc", "call", "telegram-projects", "status")
    bot = status.get("bot")
    facts["bot"] = (bot.get("username") if isinstance(bot, dict) else bot) or None
    bindings = [b for b in status.get("chatBindings", []) if b.get("projectId") == project_id]
    if bindings and all("chatId" in b for b in bindings):
        facts["groups"] = sorted({b["chatId"] for b in bindings if b.get("topicId") != 0})
    plugins = {p["id"]: p for p in bb("plugin", "list")["plugins"]}
    facts["platform"] = plugins.get("personal-assistant", {}).get("version")
    threads = bb("thread", "list", "--project", project_id)
    threads = threads.get("threads", threads) if isinstance(threads, dict) else threads
    if threads:
        latest = max(threads, key=lambda t: t.get("createdAt") or 0)
        facts["provider"] = latest.get("provider") or latest.get("providerId")
        facts["model"] = latest.get("model")
    return facts


def not_compared(config):
    return [key for key in NOT_COMPARED if key in config]
