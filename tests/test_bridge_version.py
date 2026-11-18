import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup import supports_isolated_groups


class InstalledBridgeTest(unittest.TestCase):
    def test_release_boundary(self):
        for version in ("0.9.0", "0.9.1-personal.9", "0.9.1-personal.27", "unknown"):
            with self.subTest(version=version):
                self.assertFalse(supports_isolated_groups(version))
        for version in ("0.9.1-personal.28", "0.9.1-personal.100", "0.9.1", "0.9.2"):
            with self.subTest(version=version):
                self.assertTrue(supports_isolated_groups(version))
