"""Guest thread guard: a group thread loses BB control, the owner's thread keeps it.
The calling thread comes from the agent session ID, as Codex and Claude Code start hooks without BB variables."""
import json, os, shutil, sqlite3, subprocess, sys, tempfile, unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "guest-thread-guard.py")


class GuestGuard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        data = os.path.join(self.dir, ".bb-data")
        os.makedirs(os.path.join(data, "plugins", "telegram-projects"))
        con = sqlite3.connect(os.path.join(data, "plugins", "telegram-projects", "data.db"))
        con.execute("create table state(key text primary key, value text)")
        rows = {"group:thread:thr_group": "group:policy:-100:3", "group:policy:-100:3": {"chatId": -100, "topicId": 3},
                "group:thread:thr_ownertopic": "group:policy:-200:4", "group:policy:-200:4": {"chatId": -200, "topicId": 4, "ownerTopic": True},
                "group:thread:thr_ownergroup": "group:policy:-300:0", "group:policy:-300:0": {"chatId": -300, "topicId": 0},
                "rights:5:group:-300": {"memory": "shared"}, "rights:5:group:-100": {"memory": "separate"},
                "rights:install-kind": "employee"}
        for k, v in rows.items():
            con.execute("insert into state values(?,?)", (k, json.dumps(v)))
        con.commit(); con.close()
        bb = sqlite3.connect(os.path.join(data, "bb.db"))
        bb.execute("create table events(thread_id text, provider_thread_id text)")
        bb.execute("create table threads(id text primary key, parent_thread_id text)")
        for thread, session, parent in [("thr_group", "s-group", None), ("thr_child", "s-child", "thr_group"),
                                        ("thr_owner", "s-owner", None), ("thr_ownertopic", "s-ownertopic", None), ("thr_ownergroup", "s-ownergroup", None)]:
            bb.execute("insert into events values(?,?)", (thread, session))
            bb.execute("insert into threads values(?,?)", (thread, parent))
        bb.commit(); bb.close()
        self.data = data
        hooks = os.path.join(self.dir, "hooks"); os.makedirs(hooks)
        self.hook = os.path.join(hooks, "guest-thread-guard.py")
        shutil.copy(HOOK, self.hook)
        with open(os.path.join(hooks, "guest-thread-guard.json"), "w") as f:
            json.dump({"dataDir": data}, f)

    def run_hook(self, session, tool_input, thread=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BB_")}
        env["XDG_RUNTIME_DIR"] = self.dir
        if thread: env["BB_THREAD_ID"] = thread
        return subprocess.run([sys.executable, self.hook], input=json.dumps({"session_id": session, "tool_name": "Bash", "tool_input": tool_input}),
                              capture_output=True, text=True, env=env).returncode

    def test_group_thread_cannot_read_other_threads(self):
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread list"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "$BB_CLI thread log thr_owner --json"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": f"sqlite3 {self.data}/bb.db .dump"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": ["cat", "/var/lib/x/.bb-mr/thread-storage/a"]}), 2)

    def test_group_thread_keeps_ordinary_tools(self):
        self.assertEqual(self.run_hook("s-group", {"command": "git status && python3 tools/report.py"}), 0)
        self.assertEqual(self.run_hook("s-group", {"file_path": "docs/plan.md"}), 0)

    def test_children_of_a_guest_are_guests(self):
        self.assertEqual(self.run_hook("s-child", {"command": "bb thread list"}), 2)

    def test_a_guest_cannot_pose_as_another_thread(self):
        self.assertEqual(self.run_hook("s-group", {"command": "BB_THREAD_ID=thr_owner bb status"}), 2)

    def test_a_forged_cache_does_not_lift_the_guard(self):
        with open(os.path.join(self.dir, f"bb-guest-guard-{os.getuid()}-s-group.json"), "w") as f:
            json.dump({"at": 9e12, "guest": False}, f)
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread list"}), 2)

    def test_a_group_with_only_the_owner_is_the_owners_conversation(self):
        self.assertEqual(self.run_hook("s-ownergroup", {"command": "bb thread list"}), 0)

    def test_owner_threads_keep_bb(self):
        self.assertEqual(self.run_hook("s-owner", {"command": "bb thread list"}), 0)
        self.assertEqual(self.run_hook("s-ownertopic", {"command": "bb thread list"}), 0)
        self.assertEqual(self.run_hook(None, {"command": "bb thread list"}, thread="thr_owner"), 0)

    def test_thread_check_mode_for_other_platform_code(self):
        run = lambda t: subprocess.run([sys.executable, self.hook, "--thread", t, self.data], capture_output=True, text=True).stdout.strip()
        self.assertEqual([run("thr_owner"), run("thr_group"), run("thr_child")], ["owner", "guest", "guest"])

    def test_an_unknown_session_does_not_manage_bb(self):
        self.assertEqual(self.run_hook("s-unknown", {"command": "bb thread list"}), 2)
        self.assertEqual(self.run_hook("s-unknown", {"command": "ls"}), 0)


if __name__ == "__main__":
    unittest.main()
