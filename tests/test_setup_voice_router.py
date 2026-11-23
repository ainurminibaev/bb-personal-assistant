import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup


class VoiceRouterTest(unittest.TestCase):
    """configure_bridge points the bridge's voice at the server's speech service when it is installed."""

    def run_with(self, current, router_exists, current_exists):
        calls = []

        def bb(*args):
            if args == ("plugin", "config", "telegram-projects"):
                return {"values": {"installKind": "assistant", "telegramInboxRoot": "/inbox", "voiceRouterPath": current}}
            calls.append(args)
            return {}

        real_exists = Path.exists

        def exists(path):
            if str(path) == "/usr/local/bin/voice-to-text":
                return router_exists
            if current and str(path) == current:
                return current_exists
            return real_exists(path)

        with patch.object(setup, "bb", bb), patch.object(setup, "VOICE_ROUTER", Path("/usr/local/bin/voice-to-text")), \
                patch.object(Path, "exists", exists), tempfile.TemporaryDirectory() as d:
            setup.configure_bridge("assistant", Path(d) / "inbox")
        return [c for c in calls if "voiceRouterPath" in c]

    def test_set_when_empty_or_gone(self):
        self.assertEqual(self.run_with("", True, False)[0][-1], "/usr/local/bin/voice-to-text")
        self.assertEqual(self.run_with("/opt/old/voice-to-text", True, False)[0][-1], "/usr/local/bin/voice-to-text")

    def test_kept_when_custom_works_or_no_service(self):
        self.assertEqual(self.run_with("/opt/custom/router", True, True), [])
        self.assertEqual(self.run_with("", False, False), [])


if __name__ == "__main__":
    unittest.main()
