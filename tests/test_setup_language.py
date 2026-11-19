import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup


class SetupLanguageTest(unittest.TestCase):
    def test_first_install_and_rerun(self):
        for requested, expected in (([], "en"), (["--language", "ru"], "ru")):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as work:
                plugins = {}
                settings = {}

                def cli(*args):
                    if args == ("machine", "list"):
                        return [{"id": "host", "status": "connected"}]
                    if args == ("plugin", "list"):
                        return {"plugins": list(plugins.values())}
                    if args[:2] == ("plugin", "install"):
                        key = next(k for k, src in setup.SOURCES.items() if src == args[2])
                        plugins[key] = dict(id=key, source=args[2], enabled=True,
                                            version="0.9.22" if key == "telegram-projects" else "0.2.5")
                    elif args[:5] == ("plugin", "config", "telegram-projects", "set", "language"):
                        settings["language"] = args[5]
                    elif args == ("project", "list"):
                        return [{"id": "project", "sources": [{"hostId": "host", "path": str(Path(work).resolve())}]}]
                    elif args[-1] == "status":
                        return {"chatBindings": []}
                    elif args[-1] == "preferences":
                        return {"tokenPresent": False}
                    else:
                        self.fail(args)

                argv = ["setup.py", "--workspace", work, "--machine", "host", "--yes", *requested]
                with patch.object(sys, "argv", argv), patch.object(setup, "bb", cli):
                    setup.main()
                    self.assertEqual(settings["language"], expected)
                    settings["language"] = "ru" if expected == "en" else "en"
                    setup.main()
                    self.assertNotEqual(settings["language"], expected)
