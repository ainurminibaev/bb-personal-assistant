import importlib.machinery, importlib.util, sys, unittest
from datetime import datetime, timezone
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "deploy" / "google" / "google-connect"
loader = importlib.machinery.SourceFileLoader("google_connect", str(PATH))
spec = importlib.util.spec_from_loader("google_connect", loader)
gc = importlib.util.module_from_spec(spec); loader.exec_module(gc)


class GoogleConnect(unittest.TestCase):
    def test_login_in_both_forms(self):
        token = {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": " ".join(gc.SCOPES)}
        ws, sheets = gc.token_files(token, "cid", "csec", now=datetime(2026, 11, 24, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(sorted(ws), ["client_id", "client_secret", "expiry", "refresh_token", "scopes", "token", "token_uri"])
        self.assertEqual(ws["expiry"], "2026-11-24T14:00:00")
        self.assertEqual(sheets["expiry"], "2026-11-24T14:00:00Z")
        self.assertEqual(sheets["universe_domain"], "googleapis.com")
        self.assertEqual(len(ws["scopes"]), 10)

    def test_codex_block_takes_keys_from_the_store(self):
        block = gc.codex_block("/var/lib/agent", "/opt/bin/uvx", "/opt/bin/vallis-secret")
        self.assertIn('[mcp_servers.google-workspace]', block)
        self.assertIn('"run", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "--"', block)
        self.assertIn("workspace-mcp==2.5.0", block)
        self.assertIn("mcp-google-sheets==0.6.3", block)
        self.assertNotIn("GOOGLE_OAUTH_CLIENT_SECRET =", block)   # never a value in the settings file


if __name__ == "__main__":
    unittest.main()
