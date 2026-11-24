#!/usr/bin/env python3
"""A stand-in for `bb telegram-projects search-history` in tests: the same arguments and answer, messages from a file.

FAKE_HISTORY — a JSON list of messages as the bridge returns them; FAKE_HISTORY_CALLS — a file that gets one line
per call (how many times the archive was asked). Like the bridge, it returns the last N messages of the period,
oldest first, and never more than 200.

FAKE_HISTORY_CALLER — the conversation that calls, as the bridge sees it: "owner" (default; every group and the
owner's own chat), "group:<chat>" (only that chat), "person:<id>" (their own private chat and the groups they
write in). Other people's private chats are never returned. FAKE_HISTORY_OWNER — the owner's ID (default 7777).
"""
import datetime as dt
import json
import os
import sys


def second(text):
    return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=dt.timezone.utc).timestamp())


def main():
    args, opt = sys.argv[1:], {}
    while args:
        key = args.pop(0)
        if key in ("--chat", "--from", "--to", "--tz", "--last", "--author"):
            opt[key] = args.pop(0)
    if os.environ.get("FAKE_HISTORY_FAIL"):
        print(os.environ["FAKE_HISTORY_FAIL"], file=sys.stderr)
        return 1
    if ("--from" in opt or "--to" in opt) and opt.get("--tz") != "UTC":
        print("the fake archive expects --tz UTC", file=sys.stderr)
        return 2
    with open(os.environ["FAKE_HISTORY"], encoding="utf-8") as source:
        messages = json.load(source)
    if os.environ.get("FAKE_HISTORY_CALLS"):
        with open(os.environ["FAKE_HISTORY_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps(opt) + "\n")
    start = second(opt["--from"]) if "--from" in opt else 0
    end = second(opt["--to"]) if "--to" in opt else 10 ** 11
    caller, owner = os.environ.get("FAKE_HISTORY_CALLER", "owner"), int(os.environ.get("FAKE_HISTORY_OWNER", "7777"))
    kind, _, subject = caller.partition(":")
    if kind == "owner":
        may = lambda chat: chat < 0 or chat == owner
    elif kind == "group":
        may = lambda chat: chat == int(subject)
    else:
        member = {m["chat"] for m in messages if m["chat"] < 0 and m["authorId"] == int(subject)}
        may = lambda chat: chat == int(subject) or chat in member
    found = [m for m in messages if may(m["chat"]) and ("--chat" not in opt or str(m["chat"]) == opt["--chat"])
             and ("--author" not in opt or str(m["authorId"]) == opt["--author"])
             and start <= second(m["date"].replace("Z", "")) <= end]
    found.sort(key=lambda m: (m["date"], m["message"]))
    last = min(int(opt.get("--last", 30)), 200)
    print(json.dumps({"messages": found[-last:]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
