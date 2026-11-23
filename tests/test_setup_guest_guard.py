"""setup.py installs the guest thread guard for Codex and Claude Code once, without duplicates."""
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from setup import install_guest_guard  # noqa: E402


class SetupGuard(unittest.TestCase):
    def test_install_is_idempotent_and_keeps_other_settings(self):
        package = Path(__file__).resolve().parent.parent
        root = Path(tempfile.mkdtemp())
        (root / ".claude").mkdir()
        (root / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}))
        target = install_guest_guard(package, root, "/data/bb")
        install_guest_guard(package, root, "/data/bb")
        self.assertEqual(json.loads(target.with_name("guest-thread-guard.json").read_text())["dataDir"], "/data/bb")
        self.assertTrue(target.is_file())
        claude = json.loads((root / ".claude" / "settings.json").read_text())
        codex = json.loads((root / ".codex" / "hooks.json").read_text())
        self.assertEqual(claude["permissions"], {"allow": ["Bash(ls)"]})
        for data in (claude, codex):
            groups = data["hooks"]["PreToolUse"]
            self.assertEqual(len(groups), 1)
            self.assertIn(str(target), groups[0]["hooks"][0]["command"])

    def test_a_rerun_keeps_the_limits_set_next_to_the_guard(self):
        package = Path(__file__).resolve().parent.parent
        root = Path(tempfile.mkdtemp())
        target = install_guest_guard(package, root, "/data/bb")
        settings = target.with_name("guest-thread-guard.json")
        settings.write_text(json.dumps({"dataDir": "/data/bb", "maxSchedules": 20, "guestSchedules": False}))
        install_guest_guard(package, root, "/data/bb-moved")
        self.assertEqual(json.loads(settings.read_text()),
                         {"dataDir": "/data/bb-moved", "maxSchedules": 20, "guestSchedules": False})
        settings.write_text("{broken")
        with self.assertRaises(SystemExit):
            install_guest_guard(package, root, "/data/bb")


if __name__ == "__main__":
    unittest.main()
