import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup import supports_group_results


class InstalledBridgeTest(unittest.TestCase):
    def test_release_boundary(self):
        for version in ("0.9.0", "0.9.1-personal.9", "0.9.1-personal.28", "0.9.1", "0.9.2", "0.9.3-rc.1", "unknown"):
            with self.subTest(version=version):
                self.assertFalse(supports_group_results(version))
        for version in ("0.9.3", "0.9.10", "0.10.0"):
            with self.subTest(version=version):
                self.assertTrue(supports_group_results(version))
