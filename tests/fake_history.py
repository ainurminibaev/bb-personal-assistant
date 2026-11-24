#!/usr/bin/env python3
"""A stand-in for `bb telegram-projects search-history` in tests: the same arguments and answer, messages from a file.

FAKE_HISTORY — a JSON list of messages as the bridge returns them; FAKE_HISTORY_CALLS — a file that gets one line
per call (how many times the archive was asked). Like the bridge, it returns the last N messages of the period,
oldest first, and never more than 200.
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
        if key in ("--chat", "--from", "--to", "--tz", "--last"):
            opt[key] = args.pop(0)
    if os.environ.get("FAKE_HISTORY_FAIL"):
        print(os.environ["FAKE_HISTORY_FAIL"], file=sys.stderr)
        return 1
    if opt.get("--tz") != "UTC":
        print("the fake archive expects --tz UTC", file=sys.stderr)
        return 2
    with open(os.environ["FAKE_HISTORY"], encoding="utf-8") as source:
        messages = json.load(source)
    if os.environ.get("FAKE_HISTORY_CALLS"):
        with open(os.environ["FAKE_HISTORY_CALLS"], "a", encoding="utf-8") as calls:
            calls.write(json.dumps(opt) + "\n")
    start, end = second(opt["--from"]), second(opt["--to"])
    found = [m for m in messages if str(m["chat"]) == opt["--chat"]
             and start <= second(m["date"].replace("Z", "")) <= end]
    found.sort(key=lambda m: (m["date"], m["message"]))
    last = min(int(opt.get("--last", 30)), 200)
    print(json.dumps({"messages": found[-last:]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
