"""Guest thread guard: a group thread loses BB control, the owner's thread keeps it."""
import json, os, sqlite3, subprocess, sys, tempfile, unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "guest-thread-guard.py")
FAKE_BB = """#!/bin/sh
case "$1" in
  status) echo '{"dataDir":"%s"}';;
  thread) case "$3" in thr_child) echo '{"thread":{"parentThreadId":"thr_group"}}';; *) echo '{"thread":{"parentThreadId":null}}';; esac;;
esac
"""

class GuestGuard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        data = os.path.join(self.dir, ".bb-data")
        os.makedirs(os.path.join(data, "plugins", "telegram-projects"))
        con = sqlite3.connect(os.path.join(data, "plugins", "telegram-projects", "data.db"))
        con.execute("create table state(key text primary key, value text)")
        rows = {"group:thread:thr_group": "group:policy:-100:3",
                "group:policy:-100:3": {"chatId": -100, "topicId": 3},
                "group:thread:thr_ownertopic": "group:policy:-200:4",
                "group:policy:-200:4": {"chatId": -200, "topicId": 4, "ownerTopic": True}}
        for k, v in rows.items():
            con.execute("insert into state values(?,?)", (k, json.dumps(v)))
        con.commit(); con.close()
        bb = os.path.join(self.dir, "bb")
        with open(bb, "w") as f:
            f.write(FAKE_BB % data)
        os.chmod(bb, 0o755)
        self.data = data

    def run_hook(self, thread, tool_input):
        env = dict(os.environ, BB_CLI=os.path.join(self.dir, "bb"), BB_THREAD_ID=thread, XDG_RUNTIME_DIR=self.dir)
        return subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": "Bash", "tool_input": tool_input}),
                              capture_output=True, text=True, env=env).returncode

    def test_group_thread_cannot_read_other_threads(self):
        self.assertEqual(self.run_hook("thr_group", {"command": "bb thread list"}), 2)
        self.assertEqual(self.run_hook("thr_group", {"command": "$BB_CLI thread log thr_owner --json"}), 2)
        self.assertEqual(self.run_hook("thr_group", {"command": f"sqlite3 {self.data}/bb.db .dump"}), 2)
        self.assertEqual(self.run_hook("thr_group", {"command": ["cat", "/var/lib/x/.bb-mr/thread-storage/a"]}), 2)

    def test_group_thread_keeps_ordinary_tools(self):
        self.assertEqual(self.run_hook("thr_group", {"command": "git status && python3 tools/report.py"}), 0)
        self.assertEqual(self.run_hook("thr_group", {"command": "bb telegram-projects search-history срок"}), 0)
        self.assertEqual(self.run_hook("thr_group", {"file_path": "docs/plan.md"}), 0)

    def test_a_guest_cannot_pose_as_another_thread(self):
        self.assertEqual(self.run_hook("thr_group", {"command": "BB_THREAD_ID=thr_owner bb telegram-projects search-history x"}), 2)
        self.assertEqual(self.run_hook("thr_group", {"command": "export BB_THREAD_ID=thr_owner; bb status"}), 2)

    def test_children_of_a_guest_are_guests(self):
        self.assertEqual(self.run_hook("thr_child", {"command": "bb thread list"}), 2)

    def test_a_forged_cache_does_not_lift_the_guard(self):
        with open(os.path.join(self.dir, f"bb-guest-guard-{os.getuid()}-thr_group.json"), "w") as f:
            json.dump({"at": 9e12, "guest": False, "dataDir": ""}, f)
        self.assertEqual(self.run_hook("thr_group", {"command": "bb thread list"}), 2)

    def test_owner_threads_keep_bb(self):
        self.assertEqual(self.run_hook("thr_owner", {"command": "bb thread list"}), 0)
        self.assertEqual(self.run_hook("thr_ownertopic", {"command": "bb thread list"}), 0)

if __name__ == "__main__":
    unittest.main()
