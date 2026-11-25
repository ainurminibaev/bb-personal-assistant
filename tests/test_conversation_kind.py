"""С116: the guard's own classification decides the conversation kind (owner DM of an employee — owner; a group — guest)."""
import json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path
GUARD = Path(__file__).resolve().parent.parent / "hooks" / "guest-thread-guard.py"

class Kind(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        bb = sqlite3.connect(os.path.join(self.d, "bb.db")); bb.execute("create table threads(id text, parent_thread_id text, project_id text)")
        bb.executemany("insert into threads values(?,?,?)", [("dm", None, "p"), ("grp", None, "p")]); bb.commit()
        os.makedirs(os.path.join(self.d, "plugins", "telegram-projects"))
        br = sqlite3.connect(os.path.join(self.d, "plugins", "telegram-projects", "data.db")); br.execute("create table state(key text, value text)")
        br.executemany("insert into state values(?,?)", [
            ("chat:binding:0", json.dumps({"topicId": 0, "threadId": "dm", "owner": 1})),
            ("group:thread:grp", json.dumps("group:policy:g")), ("group:policy:g", json.dumps({"chatId": -100})),
            ("rights:install-kind", json.dumps("employee"))]); br.commit()

    def kind(self, t):
        return subprocess.run([sys.executable, str(GUARD), "--thread", t, self.d], capture_output=True, text=True).stdout.strip()

    def test_owner_dm_of_an_employee_is_owner_and_a_group_is_guest(self):
        self.assertEqual(self.kind("dm"), "owner")
        self.assertEqual(self.kind("grp"), "guest")

if __name__ == "__main__":
    unittest.main()
