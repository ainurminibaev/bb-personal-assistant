#!/usr/bin/env python3
"""A stand-in for `bb telegram-projects send` in tests: writes down the address, the text and the file of each send
(FAKE_SEND_LOG) and answers as the bridge does. FAKE_SEND_FAIL — refuse with this reason."""
import json
import os
import sys


def main():
    args, to, text, file = sys.argv[1:], [], None, None
    while args:
        key = args.pop(0)
        if key in ("--chat", "--person"):
            to += [key, args.pop(0)]
        elif key == "--text-file":
            with open(args.pop(0), encoding="utf-8") as source:
                text = source.read()
        elif key == "--file":
            file = args.pop(0)
        else:
            print("unexpected argument " + key, file=sys.stderr)
            return 2
    if os.environ.get("FAKE_SEND_FAIL"):
        print(os.environ["FAKE_SEND_FAIL"], file=sys.stderr)
        return 1
    with open(os.environ["FAKE_SEND_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"to": to, "text": text, "file": file}, ensure_ascii=False) + "\n")
    print(json.dumps({"sent": True, "messageId": 1}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
