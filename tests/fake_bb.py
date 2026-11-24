#!/usr/bin/env python3
"""A stand-in for `bb` in tests of vallis-commitments: `telegram-projects search-history` and `telegram-projects send`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
if sys.argv[1:3] == ["telegram-projects", "search-history"]:
    import fake_history as tool
elif sys.argv[1:3] == ["telegram-projects", "send"]:
    import fake_send as tool
else:
    print("unexpected bb command " + " ".join(sys.argv[1:3]), file=sys.stderr)
    sys.exit(2)
sys.argv = [sys.argv[0], *sys.argv[3:]]
sys.exit(tool.main())
