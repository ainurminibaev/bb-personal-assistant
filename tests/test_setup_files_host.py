"""setup.py sets the bridge's telegramFileHostId to the installation's machine when it is empty (an empty value makes
the bridge refuse every file: «Чат работает на другом сервере», 25.11) and prints whether files are accepted."""
import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup  # noqa: E402


class FilesHost(unittest.TestCase):
    def run_with(self, values):
        calls = []
        def bb(*args):
            calls.append(args)
            return {"values": dict(values)} if args == ("plugin", "config", "telegram-projects") else {}
        with tempfile.TemporaryDirectory() as d, patch.object(setup, "bb", bb), \
                patch.object(setup, "VOICE_ROUTER", Path("/nonexistent")):
            setup.configure_bridge("employee", Path(d) / "inbox", "host_a")
        return [c[4:] for c in calls if c[:4] == ("plugin", "config", "telegram-projects", "set")]

    def test_empty_host_becomes_this_machine(self):
        sets = self.run_with({"installKind": "employee", "telegramInboxRoot": "/in", "telegramFileHostId": ""})
        self.assertIn(("telegramFileHostId", "host_a"), sets)

    def test_set_host_and_old_bridge_left_alone(self):
        self.assertEqual(self.run_with({"installKind": "employee", "telegramInboxRoot": "/in",
                                        "telegramFileHostId": "host_b"}), [])
        self.assertEqual(self.run_with({"installKind": "employee", "telegramInboxRoot": "/in"}), [])

    def test_readiness_line(self):
        self.assertEqual(setup.files_readiness({"telegramInboxRoot": "/in", "telegramFileHostId": "host_a"}, "host_a"),
                         "Files in chats: accepted.")
        self.assertIn("is empty", setup.files_readiness({"telegramInboxRoot": "/in", "telegramFileHostId": ""}, "host_a"))
        self.assertIn("not to this installation", setup.files_readiness(
            {"telegramInboxRoot": "/in", "telegramFileHostId": "host_b"}, "host_a"))
        self.assertIn("no telegramInboxRoot", setup.files_readiness({}, "host_a"))


if __name__ == "__main__":
    unittest.main()
