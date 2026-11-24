#!/usr/bin/env python3
"""Guest thread guard: a PreToolUse hook for Codex and Claude Code.

A thread that talks to a group or to a person other than the owner is a guest thread. It keeps
every skill, MCP server, project file and command of the assistant or employee, but it does not
manage BB itself: it cannot list, read or steer other threads and cannot open the BB data
directory. The conversation archive is read through the bridge's own reading tool.

A thread is a guest when it, or one of its ancestors, is bound by the Telegram bridge to a group
topic or a person's private chat. Topics the owner uses alone stay with the owner.

The hook checks commands, not intentions. It is paired with a rule in the agent instructions
and with scenario checks; it is a boundary of ordinary use, not a wall.

Exit 0 allows the call. Exit 2 blocks it; the reason on stderr goes back to the agent.
"""
import json
import os
import re
import shlex
import sqlite3
import sys
import time

CACHE_SECONDS = 60
ASSISTANT_REASON = ("This is a guest conversation of a personal assistant: no command line and no files outside "
                    "this conversation's folder. The owner can allow it; tell the person it is not available here.")
SUBTHREADS_REASON = ("This conversation already has {n} subthreads working at once — the limit that keeps the server "
                     "responsive. Wait until one finishes (`bb thread wait <id>`), then start the next; tell the person it is queued.")
BLOCK_REASON = ("This is a guest conversation: BB only within its own thread, subthreads and schedules; no other conversations. "
                "Start a subthread with `bb thread spawn --parent-self --prompt …` (its result comes back to this thread). "
                "Earlier messages: `bb telegram-projects search-history`. Tell the person what is not available here.")
SECRETS_REASON = ("Credentials are used only through vallis-secret (run, file, list); the store folder and the age key "
                  "are not read directly, and values are never printed. See the secrets-store skill.")
GUEST_SECRETS_REASON = ("This is a guest conversation: credentials are not used or shown here, only `vallis-secret list`. "
                        "Tell the person this is not available here.")
SECRET_PATHS = re.compile(r"(vallis/age\.key|vallis/secrets(?:[/\s'\"]|$)|vallis/admin\.key|\bSOPS_AGE_KEY(?:_FILE)?\b)")
SECRET_TOOL = re.compile(r"(?:^|[\s;&|(/`'\"])vallis-secret\s+([a-z-]+)")


def secrets_problem(commands, paths, guest):
    """Everyone: no direct reading of the store or the age key. A guest turn: vallis-secret list only."""
    for command in commands:
        uses = SECRET_TOOL.findall(command)
        if guest and any(u != "list" for u in uses):
            return GUEST_SECRETS_REASON
        bare = SECRET_TOOL.sub(" ", command) if uses else command
        if SECRET_PATHS.search(bare):
            return SECRETS_REASON
    if any(SECRET_PATHS.search(p) for p in paths):
        return SECRETS_REASON
    return None


COMMITMENTS_REASON = ("This is a guest conversation: the list of commitments covers every chat of the organization "
                      "and is read only in the owner's conversation. Tell the person it is not available here.")
COMMITMENTS = re.compile(r"(\bvallis-commitments\b|vallis/commitments(?:[/\s'\"]|$)|\bVALLIS_COMMITMENTS_)")


def commitments_problem(commands, paths, guest):
    """A guest turn does not read the commitments of other chats: neither by the command nor from its folder."""
    if guest and any(COMMITMENTS.search(text) for text in (*commands, *paths)):
        return COMMITMENTS_REASON
    return None


def data_dir_setting():
    """The BB data directory, written next to the hook by setup.py. Agents start hooks without BB variables."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guest-thread-guard.json")
    try:
        with open(path) as f:
            return json.load(f).get("dataDir") or ""
    except (OSError, ValueError):
        return ""


def open_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) if os.path.exists(path) else None


def calling_thread(event, bb):
    """The BB thread of this tool call: BB_THREAD_ID when the agent has it, else the agent session ID
    (Codex and Claude Code pass it to hooks) looked up in BB's own record of provider sessions."""
    thread = os.environ.get("BB_THREAD_ID")
    if thread:
        return thread
    session = event.get("session_id") or event.get("thread_id") or event.get("conversation_id")
    if not session or not bb:
        return None
    row = bb.execute("select thread_id from events where provider_thread_id=? limit 1", (str(session),)).fetchone()
    return row[0] if row else None


def cache_path(key):
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, f"bb-guest-guard-{os.getuid()}-{re.sub(r'[^A-Za-z0-9_.-]', '_', key)[:80]}.json")


def is_guest(event):
    """(guest?, data dir, calling thread, BB db). Unknown threads count as guests (only BB control closes then)."""
    data_dir = data_dir_setting()
    bb = open_ro(os.path.join(data_dir, "bb.db")) if data_dir else None
    # The thread always comes from BB's own record, never from the cache a guest could rewrite.
    thread = calling_thread(event, bb)
    if not thread or not bb:
        return True, data_dir, thread, bb
    path = cache_path(thread)
    try:
        with open(path) as f:
            cached = json.load(f)
        # Only "guest" is cached: claiming to be a guest gains nothing.
        if cached.get("guest") is True and time.time() - cached["at"] < CACHE_SECONDS:
            return True, data_dir, thread, bb
    except (OSError, ValueError, KeyError):
        pass
    guest = guest_thread(thread, data_dir)
    if guest:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"at": time.time(), "guest": True}, f)
    return guest, data_dir, thread, bb


def assistant_guest_closed(data_dir):
    """A personal assistant runs with the owner's own files: by default its guest conversations get no command
    line and no files outside the conversation's folder. The owner may open it ("assistantGuestShell": true)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guest-thread-guard.json")
    try:
        with open(path) as f:
            if json.load(f).get("assistantGuestShell") is True:
                return False
    except (OSError, ValueError):
        pass
    bridge = open_ro(os.path.join(data_dir, "plugins", "telegram-projects", "data.db")) if data_dir else None
    row = bridge.execute("select value from state where key='rights:install-kind'").fetchone() if bridge else None
    return not row or json.loads(row[0]) != "employee"


def bound_as_guest(con, thread):
    row = con.execute("select value from state where key=?", ("group:thread:" + thread,)).fetchone()
    if not row:
        return False
    scope = json.loads(row[0])
    policy = con.execute("select value from state where key=?", (scope,)).fetchone()
    if not policy:
        return False
    value = json.loads(policy[0])
    if value.get("ownerTopic") or value.get("ownerSharedProject"):
        return False
    if value.get("privateChat"):
        return True
    # One rule with the bridge: a group with shared memory has only the owner in it. The bridge switches
    # the memory to "separate" as soon as anyone else appears there.
    return group_memory(con, value.get("chatId")) != "shared"


def group_memory(con, chat):
    for (raw,) in con.execute("select value from state where key like ?", (f"rights:%:group:{chat}",)):
        memory = json.loads(raw).get("memory")
        if memory:
            return memory
    kind = con.execute("select value from state where key='rights:install-kind'").fetchone()
    return "separate" if kind and json.loads(kind[0]) == "employee" else "shared"


def unwrap_shell(words):
    """Codex runs commands as [bash, -lc, SCRIPT]: the guard checks SCRIPT itself."""
    if len(words) == 3 and os.path.basename(str(words[0])) in ("bash", "sh", "zsh") and words[1] in ("-c", "-lc"):
        return str(words[2])
    return None


def commands_of(tool_input):
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, list):
        return [unwrap_shell(command) or shlex.join(map(str, command))]
    if isinstance(command, str):
        try:
            inner = unwrap_shell(shlex.split(command, posix=True))
        except ValueError:
            inner = None
        return [inner or command]
    return []


def paths_of(tool_input):
    if not isinstance(tool_input, dict):
        return []
    return [str(tool_input[k]) for k in ("file_path", "path", "notebook_path", "pattern") if k in tool_input]


def bb_calls(command):
    """Yields the full argument lists of every bb invocation in a shell command line."""
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    for i, word in enumerate(words):
        name = os.path.basename(word.strip("\"'"))
        if name == "bb" or word in ("$BB_CLI", "${BB_CLI}") or word == os.environ.get("BB_CLI"):
            rest = []
            for w in words[i + 1:]:
                if w in (";", "&&", "||", "|"):
                    break
                rest.append(w)
            yield rest


# bb called from code (python subprocess, node child_process, os.system): the shell parser does not see it.
EMBEDDED_BB = re.compile(r"""(?<![\w/.-])bb["']?\s*[,\s]\s*["']?([a-z][\w-]*)["']?(?:\s*[,\s]\s*["']?([a-z][\w-]*)["']?)?""")


SHELL_BB = re.compile(r"(?m)(^|[;&|(]\s*)(?:\S*/)?bb(?:\s[^;&|\n]*)?")


def embedded_bb_calls(command):
    """Yields [subcommand, action] for bb named inside code; shell-level calls are checked in full elsewhere.
    An over-wide match only refuses."""
    for m in EMBEDDED_BB.finditer(SHELL_BB.sub(r"\1", command)):
        yield [w for w in m.groups() if w]


def flag(args, name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


BARE_FLAGS = ("--json", "--parent-self", "--disabled", "--yes", "--clear-service-tier")


def positionals(args):
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith("--"):
            skip = "=" not in a and a not in BARE_FLAGS
            continue
        out.append(a)
    return out


class Scope:
    """The guest conversation's own reach: itself and its descendants, its project."""
    def __init__(self, thread, bb, data_dir):
        self.thread, self.bb, self.data_dir = thread, bb, data_dir
        row = bb.execute("select project_id from threads where id=?", (thread,)).fetchone() if bb and thread else None
        self.project = row[0] if row else None

    def owns(self, other):
        current, seen = other, set()
        while self.bb and current and current not in seen and len(seen) < 16:
            if current == self.thread:
                return True
            seen.add(current)
            row = self.bb.execute("select parent_thread_id from threads where id=?", (current,)).fetchone()
            current = row[0] if row else None
        return False

    def schedules(self):
        """Pending schedules of this conversation: on and with a next run; a one-off that already ran is not one."""
        con = open_ro(os.path.join(self.data_dir, "plugins", "automations", "data.db")) if self.data_dir else None
        rows = con.execute("select target_thread_id from automations where enabled=1 and next_run_at is not null"
                           ).fetchall() if con else []
        return sum(1 for (t,) in rows if t and self.owns(t))

    def recent_schedules(self, seconds=60):
        """Schedules of this conversation created within the last minute."""
        con = open_ro(os.path.join(self.data_dir, "plugins", "automations", "data.db")) if self.data_dir else None
        since = int((time.time() - seconds) * 1000)
        rows = con.execute("select target_thread_id from automations where created_at >= ?", (since,)).fetchall() if con else []
        return sum(1 for (t,) in rows if t and self.owns(t))

    def running_children(self):
        rows = self.bb.execute("select id from threads where parent_thread_id=? and status='active'", (self.thread,)).fetchall() if self.bb else []
        return len(rows)

    def owns_automation(self, automation):
        con = open_ro(os.path.join(self.data_dir, "plugins", "automations", "data.db")) if self.data_dir else None
        row = con.execute("select target_thread_id from automations where id=?", (automation,)).fetchone() if con else None
        return bool(row and row[0] and self.owns(row[0]))


# A guest uses BB within its own reach: its thread and subthreads, schedules that run in them, its project.
OWN_THREAD = {"wait", "output", "show", "get", "log", "messages", "stop", "tell", "read", "unread", "context", "history", "queue", "update"}
ELSEWHERE = ("--environment", "--new-environment", "--new-machine", "--machine", "--host", "--environment-provider")
RUN_ELSEWHERE = ("--script", "--script-file", "--environment", "--new-environment", "--host", "--working-directory",
                 "--interpreter", "--env-json", "--base-branch")


def limit(name, default):
    """Per-conversation limits, set next to the hook in guest-thread-guard.json."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guest-thread-guard.json")
    try:
        with open(path) as f:
            value = json.load(f).get(name)
        if isinstance(value, bool):
            return int(value)
        return value if isinstance(value, int) and value >= 0 else default
    except (OSError, ValueError):
        return default


def guest_bb_allowed(args, scope):
    head = args[:2]
    # Help only describes commands; it changes nothing.
    if "--help" in args or "-h" in args or args[:1] == ["help"]:
        return True
    # The bridge itself checks where a guest conversation may send.
    if args[:1] == ["status"] or head in (["voice", "transcribe"], ["telegram-projects", "search-history"], ["telegram-projects", "send"], ["telegram-projects", "topic"],
                                          ["telegram-projects", "delete"], ["telegram-projects", "edit"],   # С80, С82: the bridge checks whose message it is
                                          ["skill", "list"], ["skill", "show"]):
        return True
    if len(head) == 2 and head[0] == "thread":
        if head[1] in ("spawn", "create"):
            if scope.running_children() >= limit("maxRunningSubthreads", 3):
                scope.reason = SUBTHREADS_REASON.format(n=limit("maxRunningSubthreads", 3))
                return False
            parent = "--parent-self" in args or scope.owns(flag(args, "--parent-thread") or "")
            project = flag(args, "--project")
            elsewhere = any(f in args or flag(args, f) for f in ELSEWHERE)
            return parent and not elsewhere and (project is None or project == scope.project)
        if head[1] == "update" and (flag(args, "--parent-thread") or "--clear-parent-thread" in args):
            # A guest may rename its own threads, never move them out of its conversation.
            return scope.owns(flag(args, "--parent-thread") or "") and "--clear-parent-thread" not in args and \
                all(scope.owns(t) for t in positionals(args[2:]) if t.startswith("thr_"))
        if head[1] in OWN_THREAD:
            targets = [a for a in positionals(args[2:]) if a.startswith("thr_")]
            named = flag(args, "--thread")
            if named:
                targets.append(named)
            # Without a thread ID the command acts on the calling thread itself.
            if not targets and not positionals(args[2:]):
                return True
            return bool(targets) and all(scope.owns(t) for t in targets)
        return False
    if head == ["personal-assistant", "tools"]:
        # С43: a guest sees what it can apply here — only the guest view, no installation details.
        return args[2:] == ["--guest"]
    if head == ["personal-assistant", "case"]:
        # С65: a guest keeps the cases of its own chat; the command itself refuses another chat's case, and --all.
        return "--all" not in args
    if head == ["thread-cleanup", "task"]:
        # A guest marks only its own task sub-threads open or closed.
        targets = [a for a in positionals(args[2:]) if a.startswith("thr_")]
        return "--self" in args or (bool(targets) and all(scope.owns(t) for t in targets))
    if head[:1] == ["thread-cleanup"]:
        return False
    if len(head) == 2 and head[0] == "automation":
        # A schedule that spawns a new thread on each run would start outside the guest conversation.
        if any(f in args or flag(args, f) for f in RUN_ELSEWHERE):
            return False
        project = flag(args, "--project")
        if project is not None and project != scope.project:
            return False
        target = flag(args, "--target-thread")
        # The bridge delivers a schedule's result back to the topic or chat where it was set; "guestSchedules": false turns them off.
        if head[1] in ("create", "resume", "run") and limit("guestSchedules", 1) != 1:
            return False
        # No cap on schedules (owner, 23.11: "why limit?"); a burst only tells the owner once, see schedule_alert.
        if head[1] in ("create", "list"):
            return bool(target) and scope.owns(target)
        ids = positionals(args[2:])
        return bool(ids) and all(scope.owns_automation(a) for a in ids) and (target is None or scope.owns(target))
    return False


ALERTS = "guest-thread-guard-alerts.jsonl"


def schedule_alert(scope):
    """A guest sets a schedule: it always goes through. Past the thresholds (pending on one conversation, or new
    within a minute; settings warnSchedules and warnSchedulesPerMinute) one line is added next to the hook; the
    platform plugin tells the owner once. The next line for this conversation comes only after it fell below."""
    try:
        pending, recent = scope.schedules() + 1, scope.recent_schedules() + 1
        over = pending > limit("warnSchedules", 50) or recent > limit("warnSchedulesPerMinute", 10)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ALERTS)
        last = None
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if entry.get("conversation") == scope.thread:
                        last = entry.get("state")
        state = "over" if over else "below"
        if (state == "over" and last != "over") or (state == "below" and last == "over"):
            with open(path, "a") as f:
                f.write(json.dumps({"at": int(time.time() * 1000), "conversation": scope.thread, "state": state,
                                    "pending": pending, "perMinute": recent}) + "\n")
    except Exception:
        pass  # the warning never stands in the way of the schedule


def overrides_identity(command):
    """BB tools learn the calling conversation from BB_THREAD_ID; a guest may not pose as another thread."""
    return bool(re.search(r"\bBB_(THREAD|PROJECT|ENVIRONMENT)_ID\b\s*=|\benv\b[^|;&]*\s-u\s*BB_|\bunset\s+BB_", command))


READERS = {"cat", "head", "tail", "sed", "rg", "grep", "ls", "wc"}


def reads_skills_only(text, data_dir):
    """BB skills are instructions for every agent: a guest may read them, nothing else in the data folder."""
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        return text, False
    if not words or os.path.basename(words[0]) not in READERS or ".." in text or any(
            w.startswith(("-i", "--in-place")) or ">" in w or w in ("|", ";", "&&", "||", "&") or "$(" in w or "`" in w
            for w in words[1:]):
        return text, False
    skills = re.compile(re.escape(data_dir.rstrip("/")) + r"/runtime/global-skills/[0-9a-f]{16,}/skills/[\w.-]+(?:/[\w.*-]+)*/?")
    return skills.sub("", text), True


def touches_data_dir(text, data_dir):
    if data_dir and data_dir.rstrip("/") in text:
        rest, reader = reads_skills_only(text, data_dir)
        if not reader or data_dir.rstrip("/") in rest:
            return True
        text = rest
    return bool(re.search(r"(^|[/\s'\"])(\.bb-[\w.-]+|bb\.db|thread-storage)([/\s'\"]|$)", text))


def guest_thread(thread, data_dir):
    """The same definition for other platform code: is this BB thread a guest conversation?"""
    bb = open_ro(os.path.join(data_dir, "bb.db"))
    bridge = open_ro(os.path.join(data_dir, "plugins", "telegram-projects", "data.db"))
    current, seen = thread, set()
    while bb and current and current not in seen and len(seen) < 8:
        seen.add(current)
        if bridge and bound_as_guest(bridge, current):
            return True
        row = bb.execute("select parent_thread_id from threads where id=?", (current,)).fetchone()
        current = row[0] if row else None
    return not bb


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--thread":
        print("guest" if guest_thread(sys.argv[2], sys.argv[3]) else "owner")
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = event.get("tool_input") or {}
    commands, paths = commands_of(tool_input), paths_of(tool_input)
    if not commands and not paths:
        return 0
    try:
        guest, data_dir, thread, bb = is_guest(event)
    except Exception:
        guest, data_dir, thread, bb = True, data_dir_setting(), None, None
    problem = secrets_problem(commands, paths, guest) or commitments_problem(commands, paths, guest)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if not guest:
        return 0
    if assistant_guest_closed(data_dir):
        cwd = os.path.realpath(event.get("cwd") or os.getcwd())
        outside = [p for p in paths if os.path.isabs(p) and not os.path.realpath(p).startswith(cwd + os.sep)]
        if commands or outside:
            print(ASSISTANT_REASON, file=sys.stderr)
            return 2
    try:
        scope = Scope(thread, bb, data_dir)
    except Exception:
        scope = Scope(None, None, "")
    scope.reason = None
    for command in commands:
        if (any(not guest_bb_allowed(args, scope) for args in bb_calls(command))
                or any(not guest_bb_allowed(args, scope) for args in embedded_bb_calls(command))
                or touches_data_dir(command, data_dir)
                or overrides_identity(command)):
            print(scope.reason or BLOCK_REASON, file=sys.stderr)
            return 2
    for command in commands:
        for args in [*bb_calls(command), *embedded_bb_calls(command)]:
            if args[:2] == ["automation", "create"]:
                schedule_alert(scope)
    for path in paths:
        if touches_data_dir(path, data_dir):
            print(BLOCK_REASON, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
