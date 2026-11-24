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


class ConversationFolders(unittest.TestCase):
    """С76: the guard knows the owner's folder and reaches conversation folders through the account's settings."""

    def test_private_folder_and_account_level_hooks(self):
        import os
        from unittest.mock import patch
        from setup import conversation_folders
        package = Path(__file__).resolve().parent.parent
        home = Path(tempfile.mkdtemp())
        clone = Path(tempfile.mkdtemp())
        root = clone / "employees" / "alpha"
        root.mkdir(parents=True)
        (root / "AGENTS.md").write_text("# role\n")
        folders, rules, allow = conversation_folders("employee", root, clone, home)
        self.assertEqual(folders, home / "conversations")
        self.assertEqual(rules, root / "AGENTS.md")
        self.assertIn(clone / "shared", allow)
        self.assertIn(root / ".mcp.json", allow)
        self.assertNotIn(root / "local", allow)
        assistant = Path(tempfile.mkdtemp()) / "personal-assistant"
        self.assertEqual(conversation_folders("assistant", assistant, None, home)[0],
                         assistant.parent / "personal-assistant-conversations")
        self.assertIsNone(conversation_folders("assistant", assistant, None, home)[1])
        with patch.dict(os.environ, {"CODEX_HOME": ""}):
            target = install_guest_guard(package, root, "/data/bb", (root, allow), user_level=True, home=home)
            install_guest_guard(package, root, "/data/bb", (root, allow), user_level=True, home=home)
        settings = json.loads(target.with_name("guest-thread-guard.json").read_text())
        self.assertEqual(settings["privateRoot"], str(root))
        self.assertIn(str(root / "AGENTS.md"), settings["privateAllow"])
        for config in (home / ".codex" / "hooks.json", home / ".claude" / "settings.json",
                       root / ".codex" / "hooks.json", root / ".claude" / "settings.json"):
            groups = json.loads(config.read_text())["hooks"]["PreToolUse"]
            self.assertEqual(len(groups), 1, config)
            self.assertIn(str(target), groups[0]["hooks"][0]["command"])
        # without own folders nothing reaches the account's settings
        other_home = Path(tempfile.mkdtemp())
        install_guest_guard(package, Path(tempfile.mkdtemp()), "/data/bb", home=other_home)
        self.assertFalse((other_home / ".claude" / "settings.json").exists())
