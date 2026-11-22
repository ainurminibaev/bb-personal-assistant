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
import subprocess
import sys
import time

# bb subcommands a guest thread may still run: reading its own context and the bridge archive tool.
ALLOWED_BB = {("status",), ("voice", "transcribe"), ("telegram-projects", "search-history")}
CACHE_SECONDS = 60
BLOCK_REASON = ("This is a guest conversation. It cannot manage BB or read other conversations. "
                "Use the conversation archive tool for earlier messages; tell the person that this is not available here otherwise.")


def bb_cli():
    return os.environ.get("BB_CLI") or "bb"


def run_json(*args):
    out = subprocess.run([bb_cli(), *args, "--json"], capture_output=True, text=True, timeout=20, check=True).stdout
    return json.loads(out)


def cache_path(thread):
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, f"bb-guest-guard-{os.getuid()}-{thread}.json")


def thread_info(thread):
    """Returns (is_guest, data_dir). Cached briefly: the hook runs before every tool call."""
    path = cache_path(thread)
    try:
        with open(path) as f:
            cached = json.load(f)
        # Only "guest" is cached: a guest could rewrite the file, and claiming to be a guest gains nothing.
        if cached.get("guest") is True and time.time() - cached["at"] < CACHE_SECONDS:
            return True, cached["dataDir"]
    except (OSError, ValueError, KeyError):
        pass
    data_dir = run_json("status").get("dataDir") or ""
    guest = False
    db = os.path.join(data_dir, "plugins", "telegram-projects", "data.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True) if os.path.exists(db) else None
    current, seen = thread, set()
    while current and current not in seen and len(seen) < 8:
        seen.add(current)
        if con and bound_as_guest(con, current):
            guest = True
            break
        shown = run_json("thread", "show", current)
        current = (shown.get("thread") or shown).get("parentThreadId")
    if guest:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"at": time.time(), "guest": True, "dataDir": data_dir}, f)
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
    return not (value.get("ownerTopic") or value.get("ownerSharedProject"))


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


def touches_data_dir(text, data_dir):
    if data_dir and data_dir.rstrip("/") in text:
        return True
    return bool(re.search(r"(^|[/\s'\"])(\.bb-[\w.-]+|bb\.db|thread-storage)([/\s'\"]|$)", text))


def main():
    thread = os.environ.get("BB_THREAD_ID")
    if not thread:
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = event.get("tool_input") or {}
    try:
        guest, data_dir = thread_info(thread)
    except Exception:
        # Without BB the hook cannot tell a guest from the owner. Fail closed only for BB itself.
        guest, data_dir = True, ""
    if not guest:
        return 0
    for command in commands_of(tool_input):
        if any(not bb_allowed(args) for args in bb_calls(command)) or touches_data_dir(command, data_dir):
            print(BLOCK_REASON, file=sys.stderr)
            return 2
    for path in paths_of(tool_input):
        if touches_data_dir(path, data_dir):
            print(BLOCK_REASON, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
