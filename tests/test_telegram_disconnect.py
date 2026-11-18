"""Run with the optional telegram-account requirements installed."""
import contextlib
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch

module_path = Path(__file__).resolve().parents[1] / "telegram-account/account.py"
spec = importlib.util.spec_from_file_location("assistant_account", module_path)
account = importlib.util.module_from_spec(spec)
spec.loader.exec_module(account)


class DisconnectTest(unittest.TestCase):
    def setUp(self):
        self.resources = contextlib.ExitStack()
        self.addCleanup(self.resources.close)
        root = Path(self.resources.enter_context(TemporaryDirectory()))
        self.state = root / "account.json"
        self.previous = json.dumps({"owner": 42, "session": "fixture"})
        self.state.write_text(self.previous)
        self.client = AsyncMock()
        self.client.get_me.return_value = Mock(id=42)
        self.client.log_out.return_value = True
        self.resources.enter_context(patch.multiple(account, ROOT=root, ACCOUNT=self.state,
            owner=Mock(return_value=42), client=Mock(return_value=self.client)))
        self.resources.enter_context(patch.object(sys, "argv", ["account.py", "disconnect"]))

    def test_valid_session(self):
        account.main()
        self.client.log_out.assert_awaited_once()
        self.assertFalse(self.state.exists())

    def test_revoked_session(self):
        self.client.get_me.return_value = None
        account.main()
        self.client.log_out.assert_not_awaited()
        self.assertFalse(self.state.exists())

    def test_revoked_at_connect(self):
        self.client.connect.side_effect = account.SessionRevokedError(None)
        account.main()
        self.assertFalse(self.state.exists())

    def test_network_error_preserves_state(self):
        self.client.get_me.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            account.main()
        self.assertEqual(self.state.read_text(), self.previous)

    def test_wrong_identity_preserves_state(self):
        self.client.get_me.return_value = Mock(id=43)
        with self.assertRaises(ValueError):
            account.main()
        self.client.log_out.assert_not_awaited()
        self.assertEqual(self.state.read_text(), self.previous)

    def test_failed_logout_preserves_state(self):
        self.client.log_out.return_value = False
        with self.assertRaises(ValueError):
            account.main()
        self.assertEqual(self.state.read_text(), self.previous)

    def test_connect_starts_fresh_after_revocation(self):
        fresh_client = AsyncMock()
        fresh_client.is_connected = Mock(return_value=True)
        fresh_client.is_user_authorized.return_value = False
        self.client.get_me.return_value = None
        account.client.side_effect = [self.client, fresh_client]
        app_file = self.state.parent / "app.json"
        app_file.write_text("{}")

        class WizardServer:
            started = False

            def __init__(self, config):
                self.app = config.app

            async def serve(self):
                async with self.app.router.lifespan_context(self.app):
                    fresh_client.connect.assert_awaited_once()

        with patch.object(account, "APP", app_file), patch("shutil.which", return_value="qrencode"), \
             patch.object(account.uvicorn, "Server", WizardServer), \
             patch.object(sys, "argv", ["account.py", "connect"]):
            account.main()
        self.assertFalse(self.state.exists())
        fresh_client.disconnect.assert_awaited_once()


class ChannelSearchTest(unittest.IsolatedAsyncioTestCase):
    async def run_search(self, allowed_channel, requested_channel, peer_id):
        from argparse import Namespace
        from contextlib import redirect_stdout
        from io import StringIO
        c = AsyncMock()
        c.iter_dialogs = Mock(side_effect=AssertionError("unrelated dialogs enumerated"))
        c.get_entity.return_value = Mock(broadcast=True, title="fixture")
        async def messages(*args, **kwargs):
            for item in []:
                yield item
        c.iter_messages = Mock(side_effect=messages)
        saved = {"channels": [allowed_channel], "channelPeers": {
            str(allowed_channel): {"id": peer_id, "accessHash": 42}
        }}
        status = {"owner": 42, "chatBindings": [{"threadId": "owner", "topicId": 0}]}
        with patch.dict(account.os.environ, BB_THREAD_ID="owner"), \
             patch.object(account.subprocess, "check_output", return_value=json.dumps(status).encode()), \
             patch.object(account, "authorized", AsyncMock(return_value=(c, saved))), \
             redirect_stdout(StringIO()):
            await account.search(Namespace(cmd="search", channel=requested_channel, words="fixture", limit=1))
        return c

    async def test_direct_read_uses_selected_peer_only(self):
        channel = -1000000000123
        c = await self.run_search(channel, channel, 123)
        c.get_entity.assert_awaited_once()
        c.iter_messages.assert_called_once()

    async def test_unselected_channel_rejected(self):
        with self.assertRaisesRegex(ValueError, "не выбран"):
            await self.run_search(-1000000000123, -1000000000124, 123)

    async def test_inconsistent_cached_peer_rejected(self):
        with self.assertRaisesRegex(ValueError, "не совпадает"):
            await self.run_search(-1000000000123, -1000000000123, 124)
