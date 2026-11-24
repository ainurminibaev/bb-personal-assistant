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
        bb.execute("create table threads(id text primary key, parent_thread_id text, project_id text, status text)")
        for thread, session, parent in [("thr_group", "s-group", None), ("thr_child", "s-child", "thr_group"),
                                        ("thr_owner", "s-owner", None), ("thr_ownertopic", "s-ownertopic", None), ("thr_ownergroup", "s-ownergroup", None)]:
            bb.execute("insert into events values(?,?)", (thread, session))
            bb.execute("insert into threads values(?,?,?,?)", (thread, parent, "proj_team", "idle"))
        bb.commit(); bb.close()
        os.makedirs(os.path.join(data, "plugins", "automations"))
        au = sqlite3.connect(os.path.join(data, "plugins", "automations", "data.db"))
        au.execute("create table automations(id text primary key, target_thread_id text, enabled integer, next_run_at integer, created_at integer)")
        au.executemany("insert into automations values(?,?,1,1,0)", [("auto_mine", "thr_group"), ("auto_owner", "thr_owner")])
        au.commit(); au.close()
        self.data = data
        hooks = os.path.join(self.dir, "hooks"); os.makedirs(hooks)
        self.hook = os.path.join(hooks, "guest-thread-guard.py")
        shutil.copy(HOOK, self.hook)
        with open(os.path.join(hooks, "guest-thread-guard.json"), "w") as f:
            json.dump({"dataDir": data, "guestSchedules": True}, f)

    def run_hook(self, session, tool_input, thread=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("BB_")}
        env["XDG_RUNTIME_DIR"] = self.dir
        if thread: env["BB_THREAD_ID"] = thread
        return subprocess.run([sys.executable, self.hook], input=json.dumps({"session_id": session, "tool_name": "Bash", "tool_input": tool_input}),
                              capture_output=True, text=True, env=env).returncode

    def test_credentials_store(self):
        # everyone: the store and the age key are not read directly
        for who in ("s-owner", "s-group"):
            self.assertEqual(self.run_hook(who, {"command": "cat ~/.config/vallis/age.key"}), 2)
            self.assertEqual(self.run_hook(who, {"command": "ls ~/.local/share/vallis/secrets/own"}), 2)
            self.assertEqual(self.run_hook(who, {"command": "vallis-secret list && cat ~/.local/share/vallis/secrets/own/A.json"}), 2)
            self.assertEqual(self.run_hook(who, {"command": "SOPS_AGE_KEY_FILE=x sops decrypt f"}), 2)
            self.assertEqual(self.run_hook(who, {"file_path": "/var/lib/e/.config/vallis/age.key"}), 2)
            self.assertEqual(self.run_hook(who, {"command": "vallis-secret list --json"}), 0)
        # the owner's conversation uses keys, a guest's does not
        self.assertEqual(self.run_hook("s-owner", {"command": "vallis-secret run KEY -- ./deploy.sh"}), 0)
        self.assertEqual(self.run_hook("s-owner", {"command": ["bash", "-lc", "vallis-secret put A --generate --what x"]}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": "vallis-secret run KEY -- env"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "x; /usr/local/bin/vallis-secret file KEY /tmp/k"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "vallis-secret header KEY"}), 2)
        self.assertEqual(self.run_hook("s-child", {"command": "vallis-secret run KEY -- true"}), 2)

    def test_group_thread_cannot_read_other_threads(self):
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread list"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "$BB_CLI thread log thr_owner --json"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": f"sqlite3 {self.data}/bb.db .dump"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": ["cat", "/var/lib/x/.bb-mr/thread-storage/a"]}), 2)

    def test_guest_reads_bb_skills_and_nothing_else_in_the_data_folder(self):
        skill = f"{self.data}/runtime/global-skills/{'a1'*32}/skills/telegram-projects/SKILL.md"
        self.assertEqual(self.run_hook("s-group", {"command": f"cat {skill}"}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": ["/usr/bin/bash", "-lc", f"sed -n '1,80p' {skill}"]}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": f"rg -n send {skill}"}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": f"cat {skill} {self.data}/bb.db"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": f"cat {skill}/../../../../../bb.db"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": f"sed -i s/a/b/ {skill}"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": f"cat {skill} > {skill}"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": f"cat {skill}; cat {self.data}/plugins/x"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": ["/usr/bin/bash", "-lc", "bb thread list"]}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "/usr/bin/bash -lc 'bb thread list'"}), 2)

    def test_bb_called_from_code_is_checked_too(self):
        py = "python3 - <<'PY'\nimport subprocess\nsubprocess.run([\"bb\", \"thread\", \"list\"])\nPY"
        self.assertEqual(self.run_hook("s-group", {"command": py}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "python3 -c 'import os; os.system(\"bb thread list\")'"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "node -e 'require(\"child_process\").execSync(\"bb telegram-projects search-history x\")'"}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": "python3 -c 'print(\"bb telegram-projects send\")'"}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": "grep -rn 'bb thread' README.md"}), 2, "an over-wide match only refuses")

    def test_guest_renames_its_subthread_but_cannot_move_it_out(self):
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread update thr_child --title 'готово'"}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread update thr_owner --title x"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread update thr_child --clear-parent-thread"}), 2)

    def test_guest_keeps_cases_of_its_own_chat(self):
        ok = lambda c: self.assertEqual(self.run_hook("s-group", {"command": c}), 0, c)
        no = lambda c: self.assertEqual(self.run_hook("s-group", {"command": c}), 2, c)
        ok("bb personal-assistant case open --title 'Отчёт' --by Анна")
        ok("bb personal-assistant case list --here")
        ok("bb personal-assistant case close c-20261125-otchet --result готово")
        no("bb personal-assistant case list --all")

    def test_guest_marks_only_its_own_task_subthreads(self):
        ok = lambda c: self.assertEqual(self.run_hook("s-group", {"command": c}), 0, c)
        no = lambda c: self.assertEqual(self.run_hook("s-group", {"command": c}), 2, c)
        ok("bb thread-cleanup task thr_child open")
        ok("bb thread-cleanup task thr_child closed")
        ok("bb thread-cleanup task --self closed")
        no("bb thread-cleanup task thr_owner closed")
        no("bb thread-cleanup run")
        no("bb thread-cleanup restore --last-run")

    def test_guest_sees_only_its_own_view_of_tools(self):
        ok = lambda c: self.assertEqual(self.run_hook("s-group", {"command": c}), 0, c)
        no = lambda c: self.assertEqual(self.run_hook("s-group", {"command": c}), 2, c)
        ok("bb personal-assistant tools --guest")
        no("bb personal-assistant tools")
        no("bb personal-assistant tools --guest --all")
        no("bb personal-assistant alerts")

    def test_guest_may_read_help_and_its_own_thread(self):
        for cmd in ("bb thread spawn --help", "bb thread show --json", "bb thread context --self --json",
                    "bb skill list", "bb help"):
            self.assertEqual(self.run_hook("s-group", {"command": cmd}), 0, cmd)
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread show --thread thr_owner"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread list"}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread spawn --prompt x"}), 2)

    def test_guest_uses_bb_within_its_own_reach(self):
        ok = lambda c: self.assertEqual(self.run_hook("s-group", {"command": c}), 0, c)
        no = lambda c: self.assertEqual(self.run_hook("s-group", {"command": c}), 2, c)
        ok("bb thread spawn --parent-self --prompt 'собери отчёт'")
        ok("bb thread wait thr_child --timeout 300")
        ok("bb thread output thr_child")
        ok("bb automation create --project proj_team --name x --cron '0 9 * * 1' --timezone Europe/Moscow --target-thread thr_group --prompt p")
        ok("bb automation list --target-thread thr_group")
        ok("bb automation pause auto_mine")
        no("bb thread spawn --prompt x")
        no("bb thread spawn --parent-self --project proj_other --prompt x")
        no("bb thread spawn --parent-self --new-machine m --prompt x")
        no("bb thread log thr_owner")
        no("bb thread tell thr_owner --message-file x")
        no("bb automation create --project proj_team --name x --in 5m --prompt p")
        no("bb automation create --project proj_team --name x --in 5m --target-thread thr_group --script 'rm -rf /'")
        no("bb automation delete auto_owner")
        no("bb plugin list")

    def test_guest_schedules_are_on_by_default_and_can_be_turned_off(self):
        cfg = os.path.join(os.path.dirname(self.hook), "guest-thread-guard.json")
        create = "bb automation create --project proj_team --name x --in 5m --target-thread thr_group --prompt p"
        with open(cfg, "w") as f:
            json.dump({"dataDir": self.data}, f)
        self.assertEqual(self.run_hook("s-group", {"command": create}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": "bb telegram-projects topic rename --topic x --name y"}), 0)
        # С80: a group conversation deletes a message; the bridge decides whose (its own chat only, others on the owner's word).
        self.assertEqual(self.run_hook("s-group", {"command": "bb telegram-projects delete --message 42"}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": "bb telegram-projects edit --message 42 --text x"}), 0)   # С82
        self.assertNotEqual(self.run_hook("s-group", {"command": "bb telegram-projects rights set --person 1"}), 0)
        # С85: a confirmation code never leaves through a guest conversation.
        self.assertNotEqual(self.run_hook("s-group", {"command": "bb personal-assistant code-to-owner"}), 0)
        with open(cfg, "w") as f:
            json.dump({"dataDir": self.data, "guestSchedules": False}, f)
        self.assertEqual(self.run_hook("s-group", {"command": create}), 2)
        self.assertEqual(self.run_hook("s-group", {"command": "bb automation list --target-thread thr_group"}), 0)
        self.assertEqual(self.run_hook("s-group", {"command": "bb thread spawn --parent-self --prompt x"}), 0)

    def alerts(self):
        path = os.path.join(os.path.dirname(self.hook), "guest-thread-guard-alerts.jsonl")
        return [json.loads(l) for l in open(path)] if os.path.exists(path) else []

    def test_no_cap_on_schedules_one_warning_past_the_threshold(self):
        cfg = os.path.join(os.path.dirname(self.hook), "guest-thread-guard.json")
        with open(cfg, "w") as f:  # an old installed limit must not refuse again after the update
            json.dump({"dataDir": self.data, "maxSchedules": 1}, f)
        create = "bb automation create --project proj_team --name x --in 5m --target-thread thr_group --prompt p"
        au = sqlite3.connect(os.path.join(self.data, "plugins", "automations", "data.db"))
        au.executemany("insert into automations values(?,?,1,1,0)", [(f"auto_p{i}", "thr_group") for i in range(48)])
        au.executemany("insert into automations values(?,?,0,null,0)", [(f"auto_done{i}", "thr_group") for i in range(30)])
        au.commit()
        self.assertEqual(self.run_hook("s-group", {"command": create}), 0)  # 49 pending + this one = 50: no warning yet
        self.assertEqual(self.alerts(), [])
        au.execute("insert into automations values('auto_p50','thr_group',1,1,0)"); au.commit()
        for _ in range(3):  # past 50: it still goes through, and the owner hears once
            self.assertEqual(self.run_hook("s-group", {"command": create}), 0)
        self.assertEqual([(a["conversation"], a["state"], a["pending"]) for a in self.alerts()], [("thr_group", "over", 51)])
        au.execute("delete from automations where id like 'auto_p%'"); au.commit()
        self.assertEqual(self.run_hook("s-group", {"command": create}), 0)
        self.assertEqual([a["state"] for a in self.alerts()], ["over", "below"])  # fell below: the next crossing warns again

    def test_many_new_schedules_within_a_minute_warn_once(self):
        with open(os.path.join(os.path.dirname(self.hook), "guest-thread-guard.json"), "w") as f:
            json.dump({"dataDir": self.data, "warnSchedulesPerMinute": 3}, f)
        au = sqlite3.connect(os.path.join(self.data, "plugins", "automations", "data.db"))
        now = int(__import__("time").time() * 1000)
        au.executemany("insert into automations values(?,?,1,1,?)", [(f"auto_new{i}", "thr_group", now) for i in range(3)])
        au.commit()
        create = "bb automation create --project proj_team --name x --in 5m --target-thread thr_group --prompt p"
        self.assertEqual(self.run_hook("s-group", {"command": create}), 0)
        self.assertEqual([(a["state"], a["perMinute"]) for a in self.alerts()], [("over", 4)])

    def test_limits_per_conversation(self):
        with open(os.path.join(os.path.dirname(self.hook), "guest-thread-guard.json"), "w") as f:
            json.dump({"dataDir": self.data, "guestSchedules": True, "maxSchedules": 1, "maxRunningSubthreads": 0}, f)
        self.assertEqual(self.run_hook("s-group", {"command": "bb automation create --project proj_team --name x --in 5m --target-thread thr_group --prompt p"}), 0)
        env = {k: v for k, v in os.environ.items() if not k.startswith("BB_")}
        env["XDG_RUNTIME_DIR"] = self.dir
        done = subprocess.run([sys.executable, self.hook], capture_output=True, text=True, env=env, input=json.dumps(
            {"session_id": "s-group", "tool_name": "Bash", "tool_input": {"command": "bb thread spawn --parent-self --prompt x"}}))
        self.assertEqual(done.returncode, 2)
        self.assertIn("subthreads working at once", done.stderr)  # the refusal names its reason

    def test_personal_assistant_guest_has_no_command_line_by_default(self):
        con = sqlite3.connect(os.path.join(self.data, "plugins", "telegram-projects", "data.db"))
        con.execute("update state set value=? where key='rights:install-kind'", (json.dumps("assistant"),)); con.commit(); con.close()
        self.assertEqual(self.run_hook("s-group", {"command": "ls"}), 2)
        self.assertEqual(self.run_hook("s-group", {"file_path": "/home/owner/private.txt"}), 2)
        self.assertEqual(self.run_hook("s-group", {"file_path": "notes.md"}), 0)
        self.assertEqual(self.run_hook("s-owner", {"command": "ls"}), 0, "the owner's own conversation is not affected")

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
