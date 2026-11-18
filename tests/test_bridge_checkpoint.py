import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

path = Path(__file__).resolve().parents[1] / "migrate_bridge.py"
spec = importlib.util.spec_from_file_location("bridge_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class CheckpointTest(unittest.TestCase):
    def verify(self, rows):
        state = {"pluginId": "telegram-projects", "version": 1,
                 "settings": {"enabled": False, "chatEnabled": False}, "rows": rows}
        with TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "checkpoint"
            snapshot.touch(mode=0o600)
            data = json.dumps(state).encode()
            snapshot.write_bytes(data)
            migration.verify_checkpoint(snapshot, {"sha256": hashlib.sha256(data).hexdigest(), "rows": len(rows)})

    def test_quiet(self):
        self.verify([{"key": "chat:binding:0", "value": {"threadId": "fixture"}},
                     {"key": "group:in:42:1", "value": {"state": "done"}}])

    def test_work_arriving_during_shutdown_blocks_removal(self):
        for key, value in (("queue:42", True), ("chat:in:42", {"state": "pending"}),
                           ("chat:out:42", {}), ("group:in:42", {"state": "submitted"}),
                           ("chat:handoff:42", {"phase": "moving"})):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Work arrived"):
                self.verify([{"key": key, "value": value}])
