import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KB = Path(__file__).with_name("kb.py")
REBUILD = "from pathlib import Path\nPath('INDEX.md').write_text('\\n'.join(sorted(str(p) for p in Path('topics').rglob('*.md'))))\n"


def run(root, *args, stdin=None):
    proc = subprocess.run([sys.executable, str(KB), "--root", str(root), *args], input=stdin,
                          capture_output=True, text=True)
    return proc.returncode, json.loads(proc.stdout)


class KbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "kb-rebuild.py").write_text(REBUILD)
        items = self.root / "topics" / "scraping" / "items"
        items.mkdir(parents=True)
        (items / "001-obscura.md").write_text(
            "---\nid: 001\nslug: obscura\nsource: https://github.com/a/obscura\ntype: github-repo\n"
            "date_added: 2026-01-01\nstatus: pointer\ntopic: scraping\ntags: [browser, stealth]\n---\n\n# Obscura\n\nHeadless browser.\n")
        (self.root / "INDEX.md").write_text("old")
        for cmd in (["init", "-q"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
            subprocess.run(["git", "-C", str(self.root), *cmd], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_search_and_read(self):
        code, data = run(self.root, "search", "headless")
        self.assertEqual(code, 0)
        self.assertEqual(data["results"][0]["slug"], "obscura")
        code, data = run(self.root, "search", "--tag", "stealth")
        self.assertEqual(len(data["results"]), 1)
        code, data = run(self.root, "read", "scraping/1")
        self.assertIn("Headless browser", data["content"])

    def test_add_duplicate_and_remove(self):
        base = ["add", "--topic", "scraping", "--title", "Test", "--slug", "kb-test", "--type", "article", "--tags", "x"]
        code, data = run(self.root, *base, "--source", "https://www.github.com/A/obscura/tree/main", stdin="## Что внутри\nx")
        self.assertEqual(code, 3)
        self.assertEqual(data["status"], "duplicate")
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "t@t"], check=True)
        code, data = run(self.root, *base, "--source", "https://www.instagram.com/reel/XYZ/", stdin="## Что внутри\nx")
        self.assertEqual(code, 0, data)
        self.assertEqual(data["id"], "002")
        self.assertTrue(data["commit"])
        self.assertIn("INDEX.md", data["committed_files"])
        text = (self.root / data["path"]).read_text()
        self.assertIn("---\n\n# Test\n\n## Что внутри", text)
        code, data = run(self.root, "remove", "kb-test")
        self.assertEqual(data["status"], "removed")
        self.assertTrue(data["commit"])


class FirstUseTest(unittest.TestCase):
    def test_empty_base_created_on_first_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kb"
            code, data = run(root, "topics")
            self.assertEqual(code, 0)
            self.assertEqual(data["topics"], [])
            self.assertTrue((root / "AGENTS.md").is_file())
            code, data = run(root, "add", "--topic", "tools", "--create-topic", "--title", "Tool", "--slug", "tool",
                             "--source", "https://example.com/tool", "--type", "article", "--tags", "x", stdin="Body")
            self.assertEqual(code, 0, data)
            self.assertTrue(data["commit"])
            self.assertIn("tools/items/001-tool.md", (root / "INDEX.md").read_text())

    def test_refuses_non_empty_foreign_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "other.txt").write_text("x")
            code, data = run(tmp, "topics")
            self.assertEqual(code, 2)
            self.assertIn("not a knowledge base", data["error"])


if __name__ == "__main__":
    unittest.main()
