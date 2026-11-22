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

# bb subcommands a guest thread may still run: reading its own context and the bridge archive tool.
ALLOWED_BB = {("status",), ("voice", "transcribe"), ("telegram-projects", "search-history")}
CACHE_SECONDS = 60
BLOCK_REASON = ("This is a guest conversation. It cannot manage BB or read other conversations. "
                "Use the conversation archive tool for earlier messages; tell the person that this is not available here otherwise.")


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
    """True for a guest conversation or when it cannot be told apart (only BB control is closed then)."""
    data_dir = data_dir_setting()
    bb = open_ro(os.path.join(data_dir, "bb.db")) if data_dir else None
    key = str(os.environ.get("BB_THREAD_ID") or event.get("session_id") or "unknown")
    path = cache_path(key)
    try:
        with open(path) as f:
            cached = json.load(f)
        # Only "guest" is cached: a guest could rewrite the file, and claiming to be a guest gains nothing.
        if cached.get("guest") is True and time.time() - cached["at"] < CACHE_SECONDS:
            return True, data_dir
    except (OSError, ValueError, KeyError):
        pass
    thread = calling_thread(event, bb)
    if not thread or not bb:
        return True, data_dir
    bridge = open_ro(os.path.join(data_dir, "plugins", "telegram-projects", "data.db"))
    guest, current, seen = False, thread, set()
    while current and current not in seen and len(seen) < 8:
        seen.add(current)
        if bridge and bound_as_guest(bridge, current):
            guest = True
            break
        row = bb.execute("select parent_thread_id from threads where id=?", (current,)).fetchone()
        current = row[0] if row else None
    if guest:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"at": time.time(), "guest": True}, f)
    return guest, data_dir


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


def commands_of(tool_input):
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, list):
        return [" ".join(map(str, command))]
    return [command] if isinstance(command, str) else []


def paths_of(tool_input):
    if not isinstance(tool_input, dict):
        return []
    return [str(tool_input[k]) for k in ("file_path", "path", "notebook_path", "pattern") if k in tool_input]


def bb_calls(command):
    """Yields the argument lists of every bb invocation in a shell command line."""
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    for i, word in enumerate(words):
        name = os.path.basename(word.strip("\"'"))
        if name == "bb" or word in ("$BB_CLI", "${BB_CLI}") or word == os.environ.get("BB_CLI"):
            rest = []
            for w in words[i + 1:]:
                if w in (";", "&&", "||", "|") or w.startswith("-"):
                    break
                rest.append(w)
            yield rest


def bb_allowed(args):
    return any(tuple(args[:len(prefix)]) == prefix for prefix in ALLOWED_BB)


def overrides_identity(command):
    """BB tools learn the calling conversation from BB_THREAD_ID; a guest may not pose as another thread."""
    return bool(re.search(r"\bBB_(THREAD|PROJECT|ENVIRONMENT)_ID\b\s*=|\benv\b[^|;&]*\s-u\s*BB_|\bunset\s+BB_", command))


def touches_data_dir(text, data_dir):
    if data_dir and data_dir.rstrip("/") in text:
        return True
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
        guest, data_dir = is_guest(event)
    except Exception:
        guest, data_dir = True, data_dir_setting()
    if not guest:
        return 0
    for command in commands:
        if (any(not bb_allowed(args) for args in bb_calls(command)) or touches_data_dir(command, data_dir)
                or overrides_identity(command)):
            print(BLOCK_REASON, file=sys.stderr)
            return 2
    for path in paths:
        if touches_data_dir(path, data_dir):
            print(BLOCK_REASON, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
