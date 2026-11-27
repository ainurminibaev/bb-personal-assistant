"""С114: acceptance checks without a person — each 25.11 case gives «не прошло — что сделать»; a shared model login is the
owner's decision, not a failure; the summary refuses the launch while anything failed."""
import json
import subprocess
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import acceptance as a  # noqa: E402

MEM_OK = "MemTotal: 32000000 kB\nMemAvailable: 20000000 kB\nSwapTotal: 8000000 kB\n"


class Checks(unittest.TestCase):
    def test_owner_chat(self):
        self.assertEqual(a.check_owner_chat({}, "p")["state"], a.FAIL)
        self.assertEqual(a.check_owner_chat({"owner": 1, "chatBindings": [{"topicId": 0, "projectId": "x"}]}, "p")["state"], a.FAIL)
        self.assertEqual(a.check_owner_chat({"owner": 1, "chatBindings": [{"topicId": 0, "projectId": "p"}]}, "p")["state"], a.OK)

    def test_files_empty_host_is_the_25_11_case(self):
        r = a.check_files({"telegramInboxRoot": "/in", "telegramFileHostId": ""}, "h")
        self.assertEqual(r["state"], a.FAIL)
        self.assertIn("не принимаются", r["text"])
        self.assertEqual(a.check_files({"telegramInboxRoot": "/in", "telegramFileHostId": "h"}, "h")["state"], a.OK)

    def test_access_curator_missing(self):
        org = {"people": {"Владелец": {"telegram": 1}, "Куратор": {"telegram": 2}}}
        r = a.check_access(org, 1, {3})
        self.assertEqual(r["state"], a.FAIL)
        self.assertIn("Куратор", r["text"])
        self.assertEqual(a.check_access(org, 1, {2})["state"], a.OK)
        self.assertEqual(a.check_access({}, 1, set())["state"], a.FAIL)

    def test_shared_login_is_owners_decision(self):
        self.assertEqual(a.check_login(None, {})["state"], a.FAIL)
        r = a.check_login("abcd1234", {"первый": "abcd1234", "третий": "ffff0000"})
        self.assertEqual(r["state"], a.OWNER)
        self.assertIn("первый", r["text"])
        self.assertEqual(a.check_login("abcd1234", {"третий": "ffff0000"})["state"], a.OK)
        self.assertEqual(a.check_login("abcd1234", None)["state"], a.UNCHECKED)

    def test_prints_exchange(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            self.assertEqual(a.exchange_prints("второй", "aaaa1111", folder), {})
            import pwd, os as _os
            me = pwd.getpwuid(_os.getuid()).pw_name
            (folder / me).write_text("aaaa1111\n")             # a file owned by the account of that name counts
            (folder / "чужой").write_text("bbbb2222\n")        # a file named after another account but not its own — no
            self.assertEqual(a.exchange_prints("второй", "aaaa1111", folder), {me: "aaaa1111"})
            self.assertEqual(oct((folder / "второй").stat().st_mode)[-3:], "644")
            self.assertEqual((folder / "второй").read_text(), "aaaa1111")
        self.assertIsNone(a.exchange_prints("x", "y", Path("/nonexistent/prints")))

    def test_resources_idle_configured(self):
        self.assertEqual(a.check_resources(MEM_OK)["state"], a.OK)
        self.assertEqual(a.check_resources(MEM_OK.replace("SwapTotal: 8000000", "SwapTotal: 0"))["state"], a.FAIL)
        self.assertEqual(a.check_resources(MEM_OK.replace("20000000", "1000000"))["state"], a.FAIL)
        self.assertEqual(a.check_idle({"generalSettings": {"idleProviderSessionTimeoutMinutes": 30}})["state"], a.FAIL)
        self.assertEqual(a.check_idle({"generalSettings": {"idleProviderSessionTimeoutMinutes": 10}})["state"], a.OK)
        bad = "personal-assistant@0.4.11  running  (agent configure failed: x)"
        self.assertEqual(a.check_configured(bad)["state"], a.FAIL)
        self.assertEqual(a.check_configured("personal-assistant@0.4.11  running")["state"], a.OK)

    def test_login_fingerprint_never_the_value(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".codex").mkdir()
            (Path(home) / ".codex" / "auth.json").write_text('{"tokens": {"account_id": "acct-secret-123"}}')
            fp = a.login_fingerprint(home)
            self.assertEqual(len(fp), 8)
            self.assertNotIn("acct", fp)

    def test_summary_refuses_launch_on_failure(self):
        rows = [a._row("x", a.OK, "ok"), a._row("y", a.OWNER, "общий вход"), a._row("z", a.FAIL, "плохо", "сделать")]
        self.assertIn("не прошло 1 из 3", a.summary(rows))
        self.assertIn("— сделать", a.render(rows))
        self.assertEqual(a.summary(rows[:2]), "Приёмка: всё прошло.")


    def test_group_rights_all_ok_and_empty(self):
        class R:
            def __init__(self, rc=0, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        ok_json = '{"ok": true, "bot": "b_bot", "privacyMode": false, "groups": [{"chatId": -1001, "title": "Отдел", "ok": true, "missing": [], "hint": ""}]}'
        r_ok = a.check_group_rights(lambda *args, **kw: R(0, ok_json))
        self.assertEqual(r_ok["state"], a.OK)
        self.assertIn("1", r_ok["text"])

        empty_json = '{"ok": true, "bot": "b_bot", "privacyMode": false, "groups": []}'
        r_empty = a.check_group_rights(lambda *args, **kw: R(0, empty_json))
        self.assertEqual(r_empty["state"], a.OK)

    def test_group_rights_missing_returns_c77_path(self):
        class R:
            def __init__(self, rc=0, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        hint = "В группе «Разработка» не хватает прав: Удаление сообщений. Откройте группу → название группы сверху → «Изменить» → «Администраторы» → @b_bot → включите: Удаление сообщений."
        bad_json = json.dumps({"ok": False, "bot": "b_bot", "groups": [
            {"chatId": -1001, "title": "Разработка", "ok": False, "missing": ["can_delete_messages"], "hint": hint},
            {"chatId": -1002, "title": "Общий", "ok": True, "missing": [], "hint": ""},
        ]}, ensure_ascii=False)
        r = a.check_group_rights(lambda *args, **kw: R(0, bad_json))
        self.assertEqual(r["state"], a.FAIL)
        self.assertIn("Разработка", r["text"])
        self.assertIn("Откройте группу → название группы сверху", r["action"])
        self.assertIn(hint, a.render([r]))

    def test_group_rights_errors_and_old_bridge(self):
        class R:
            def __init__(self, rc=0, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        r_old = a.check_group_rights(lambda *args, **kw: R(2, "", "Usage: bb telegram-projects ..."))
        self.assertEqual(r_old["state"], a.UNCHECKED)

        r_err = a.check_group_rights(lambda *args, **kw: R(1, "", "bot_token_missing"))
        self.assertEqual(r_err["state"], a.FAIL)
        self.assertIn("bot_token_missing", r_err["text"])

    def test_group_rights_hardening_timeout_bad_json_and_ok_false(self):
        class R:
            def __init__(self, rc=0, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        def raise_timeout(*args, **kw):
            raise subprocess.TimeoutExpired(cmd="bb", timeout=60)

        self.assertEqual(a.check_group_rights(raise_timeout)["state"], a.FAIL)
        # exitCode 1 with "not found" must be FAIL, not UNCHECKED
        self.assertEqual(a.check_group_rights(lambda *args, **kw: R(1, "", "chat not found"))["state"], a.FAIL)
        # top-level ok:false with empty groups must be FAIL
        self.assertEqual(a.check_group_rights(lambda *args, **kw: R(0, '{"ok": false, "groups": []}'))["state"], a.FAIL)
        # non-dict group entry must be FAIL
        self.assertEqual(a.check_group_rights(lambda *args, **kw: R(0, '{"ok": true, "groups": ["bad"]}'))["state"], a.FAIL)

if __name__ == "__main__":
    unittest.main()
