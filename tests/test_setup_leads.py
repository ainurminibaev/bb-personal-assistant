"""setup.py writes the passport's leads_chats into the bridge's leadsChatsByDefault ("yes"/"no"); without the field,
or with a bridge that has no such setting, nothing is written (the bridge stays on auto)."""
import sys, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup  # noqa: E402


class LeadsChats(unittest.TestCase):
    def run_with(self, employee, values):
        calls = []
        def bb(*args):
            calls.append(args)
            return {"values": dict(values)} if args == ("plugin", "config", "telegram-projects") else {}
        with patch.object(setup, "bb", bb):
            setup.configure_curators({}, employee)
        return [c for c in calls if c[:4] == ("plugin", "config", "telegram-projects", "set")]

    def test_passport_word_goes_to_the_bridge(self):
        self.assertEqual(self.run_with({"leads_chats": False}, {"leadsChatsByDefault": "auto"}),
                         [("plugin", "config", "telegram-projects", "set", "leadsChatsByDefault", "no")])
        self.assertEqual(self.run_with({"leads_chats": True}, {"leadsChatsByDefault": "auto"}),
                         [("plugin", "config", "telegram-projects", "set", "leadsChatsByDefault", "yes")])

    def test_no_field_or_no_setting_leaves_the_bridge_alone(self):
        self.assertEqual(self.run_with({}, {"leadsChatsByDefault": "auto"}), [])
        self.assertEqual(self.run_with({"leads_chats": True}, {}), [])                       # an older bridge
        self.assertEqual(self.run_with({"leads_chats": True}, {"leadsChatsByDefault": "yes"}), [])   # already so


if __name__ == "__main__":
    unittest.main()
