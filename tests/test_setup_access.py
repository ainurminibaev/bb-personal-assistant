"""setup.py approves the organization's people (curators among them) in a new employee's bridge: added to the list,
nothing removed, the owner not listed; with no people — a line saying the curator cannot give tasks yet."""
import json, sys, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup  # noqa: E402

ORG = {"people": {"Айнур": {"telegram": 1001}, "Евгений Малков": {"telegram": 2002}, "Без ID": {"note": "x"}}}


class Access(unittest.TestCase):
    def run_with(self, current, description=ORG, owner=1001):
        sent = []
        def bb(*args):
            if args[-1] == "rightsAccess":
                return list(current)
            if "rightsAccessSet" in args:
                sent.append(json.loads(Path(args[-1]).read_text())["people"])
                return sent[-1]
            return {}
        with patch.object(setup, "bb", bb):
            line = setup.configure_access(description, owner)
        return line, sent

    def test_curator_added_existing_kept_owner_skipped(self):
        line, sent = self.run_with([{"id": 3003, "name": "Тестер"}])
        self.assertEqual(sent, [[{"id": 3003, "name": "Тестер"}, {"id": 2002, "name": "Евгений Малков"}]])
        self.assertIn("Евгений Малков", line)
        self.assertNotIn("Айнур", line)

    def test_nothing_to_add(self):
        line, sent = self.run_with([{"id": 2002, "name": "Евгений Малков"}])
        self.assertEqual(sent, [])
        self.assertIn("approved", line)

    def test_no_people(self):
        line, sent = self.run_with([], {"people": {}})
        self.assertEqual(sent, [])
        self.assertIn("cannot give tasks", line)


if __name__ == "__main__":
    unittest.main()
