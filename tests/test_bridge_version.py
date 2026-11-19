import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup import supports_coordinator_handoff


class InstalledBridgeTest(unittest.TestCase):
    def test_release_boundary(self):
        for version in ("0.9.0", "0.9.1-personal.9", "0.9.1-personal.28", "0.9.1", "0.9.2", "0.9.17", "0.9.19", "0.9.20", "0.9.21-rc.1", "unknown"):
            with self.subTest(version=version):
                self.assertFalse(supports_coordinator_handoff(version))
        for version in ("0.9.21", "0.9.22", "0.10.0"):
            with self.subTest(version=version):
                self.assertTrue(supports_coordinator_handoff(version))
