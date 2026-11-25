"""С102: the installer binds the owner's private chat to the employee's project; the owner never sends «/project»."""
import subprocess, sys, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup  # noqa: E402


class OwnerChat(unittest.TestCase):
    def run_with(self, status, values=None, rc=0):
        calls, runs = [], []
        def bb(*args):
            calls.append(args)
            return {"values": dict(values if values is not None else {"employeeProject": ""})} if args == ("plugin", "config", "telegram-projects") else {}
        def run(cmd, **kw):
            runs.append(cmd)
            return subprocess.CompletedProcess(cmd, rc, '{"changed":true}', "owner_chat_bound_to_another_project" if rc else "")
        with patch.object(setup, "bb", bb):
            line = setup.configure_owner_chat("proj_x", status, run=run)
        sets = [c[4:] for c in calls if c[:4] == ("plugin", "config", "telegram-projects", "set")]
        return line, sets, runs

    def test_before_pairing_the_bridge_is_told_the_project(self):
        line, sets, runs = self.run_with({"owner": None, "chatBindings": []})
        self.assertEqual(sets, [("employeeProject", "proj_x")])
        self.assertEqual(runs, [])
        self.assertIn("binds itself", line)

    def test_a_paired_bot_without_a_private_chat_binding_is_bound_now(self):
        line, _, runs = self.run_with({"owner": 1, "chatBindings": []})
        self.assertEqual(runs, [["bb", "telegram-projects", "bind-owner-chat", "--project", "proj_x"]])
        self.assertIn("bound to project proj_x", line)

    def test_already_bound_or_refused(self):
        line, _, runs = self.run_with({"owner": 1, "chatBindings": [{"topicId": 0, "projectId": "proj_x", "threadId": "thr_a"}]})
        self.assertEqual(runs, []); self.assertIn("agent answers", line)
        line, _, _ = self.run_with({"owner": 1, "chatBindings": []}, rc=1)
        self.assertIn("not bound", line)
        line, sets, _ = self.run_with({"owner": None, "chatBindings": []}, values={})   # an older bridge: nothing set
        self.assertEqual(sets, [])
