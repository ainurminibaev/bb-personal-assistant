"""С114: acceptance checks without a person — each 25.11 case gives «не прошло — что сделать»; a shared model login is the
owner's decision, not a failure; the summary refuses the launch while anything failed."""
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


if __name__ == "__main__":
    unittest.main()
