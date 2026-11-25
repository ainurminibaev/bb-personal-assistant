"""setup.py sets the bot's profile photo from the employee's passport picture: once per picture (a marker keeps its
SHA-256), again when the picture changes or the bot has no photo; it never calls removeMyProfilePhoto and never prints
the token."""
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import setup  # noqa: E402


class FakeBot:
    def __init__(self, photos=0, set_ok=True):
        self.photos, self.set_ok, self.calls = photos, set_ok, []

    def __call__(self, api, token, method, fields=None, files=None):
        self.calls.append((api, method))
        if method == "getMe":
            return {"ok": True, "result": {"id": 42}}
        if method == "getUserProfilePhotos":
            return {"ok": True, "result": {"total_count": self.photos}}
        if method == "setMyProfilePhoto":
            if self.set_ok:
                self.photos += 1
                return {"ok": True, "result": True}
            return {"ok": False, "description": "Not Found: method not found"}
        raise AssertionError(f"unexpected method {method}")


class Avatar(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.folder = self.tmp / "employee"
        self.folder.mkdir()
        self.data = self.tmp / "bb"
        (self.data / "plugins" / "telegram-projects" / "secrets").mkdir(parents=True)
        (self.data / "plugins" / "telegram-projects" / "secrets" / "botToken").write_text("123:SECRET\n")
        (self.folder / "avatar.jpg").write_bytes(b"\xff\xd8jpeg")

    def run_avatar(self, bot, employee=None, values=None):
        return setup.configure_avatar(self.folder, employee or {}, self.data, values or {}, call=bot)

    def methods(self, bot):
        return [m for _, m in bot.calls]

    def test_sets_once_then_keeps(self):
        bot = FakeBot()
        self.assertEqual(self.run_avatar(bot), "Avatar: set from avatar.jpg.")
        self.assertIn("setMyProfilePhoto", self.methods(bot))
        again = FakeBot(photos=1)
        self.assertEqual(self.run_avatar(again), "Avatar: set from avatar.jpg.")
        self.assertNotIn("setMyProfilePhoto", self.methods(again))

    def test_new_picture_or_no_photo_sets_again(self):
        self.run_avatar(FakeBot())
        (self.folder / "avatar.jpg").write_bytes(b"\xff\xd8other")
        bot = FakeBot(photos=1)
        self.run_avatar(bot)
        self.assertIn("setMyProfilePhoto", self.methods(bot))
        (self.folder / "avatar.jpg").write_bytes(b"\xff\xd8jpeg")
        self.run_avatar(FakeBot())
        empty = FakeBot(photos=0)
        self.run_avatar(empty)
        self.assertIn("setMyProfilePhoto", self.methods(empty))

    def test_passport_name_and_local_server(self):
        (self.folder / "green.jpg").write_bytes(b"\xff\xd8green")
        bot = FakeBot()
        line = self.run_avatar(bot, {"avatar": "green.jpg"}, {"telegramApiRoot": "http://127.0.0.1:8083/"})
        self.assertEqual(line, "Avatar: set from green.jpg.")
        self.assertEqual({api for api, _ in bot.calls}, {"http://127.0.0.1:8083"})

    def test_refusal_is_a_line_and_no_marker(self):
        line = self.run_avatar(FakeBot(set_ok=False))
        self.assertIn("not set", line)
        self.assertNotIn("SECRET", line)
        self.assertFalse((self.data / "personal-assistant-avatar.sha256").exists())

    def test_nothing_without_picture_or_bot(self):
        (self.folder / "avatar.jpg").unlink()
        self.assertIsNone(self.run_avatar(FakeBot()))
        (self.folder / "avatar.jpg").write_bytes(b"\xff\xd8jpeg")
        (self.data / "plugins" / "telegram-projects" / "secrets" / "botToken").unlink()
        self.assertIn("waits for the bot", self.run_avatar(FakeBot()))

    def test_never_removes(self):
        bot = FakeBot()
        self.run_avatar(bot)
        self.assertNotIn("removeMyProfilePhoto", self.methods(bot))


if __name__ == "__main__":
    unittest.main()
