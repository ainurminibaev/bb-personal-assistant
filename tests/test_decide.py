"""Quick decision service (deploy/decide): cascade of providers, answers in the Decisions shape, no text in the log.

The service runs as a program on a free loopback port; OpenAI is a fake HTTP server, Claude and Codex are fake CLIs.
"""
import getpass
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "deploy" / "decide"
REQUEST = {"input": "Боцман, напомни про созвон и пиццу",
           "questions": [{"type": "choice", "name": "reaction", "instructions": "Реакция на сообщение.",
                          "choices": [{"value": "👍", "description": "принято"}, {"value": "😁", "description": "смешно"}]}]}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeOpenAI:
    def __init__(self, status, answer=None):
        self.calls = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.calls.append((self.headers.get("Authorization"), body))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps({"answers": [answer]} if answer else {"error": "x"}).encode())

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/decisions"


class DecideTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.procs, self.fakes = [], []

    def tearDown(self):
        for p in self.procs:
            p.terminate()
            p.wait(5)
        for f in self.fakes:
            f.server.shutdown()
        if getattr(self, "log", None):
            self.log.close()
        self.tmp.cleanup()

    def cli(self, name, reply_json):
        """A fake CLI printing what the real one prints for a reply."""
        path = self.home / name
        if name == "claude":
            out = json.dumps({"result": reply_json})
        else:
            out = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": reply_json}})
        path.write_text(f"#!/bin/sh\ncat <<'EOF'\n{out}\nEOF\n")
        path.chmod(0o755)
        return str(path)

    def start(self, providers, openai=None, claude=None, codex=None, key=False, claude_login=False, codex_login=False,
              consumers=None):
        if key:
            (self.home / "openai.key").write_text("sk-test")
        if claude_login:
            (self.home / ".claude").mkdir(exist_ok=True)
            (self.home / ".claude" / ".credentials.json").write_text("{}")
        if codex_login:
            (self.home / ".codex").mkdir(exist_ok=True)
            (self.home / ".codex" / "auth.json").write_text("{}")
        sock = str(self.home / "decide.sock")
        env = {**os.environ, "HOME": str(self.home), "DECIDE_SOCKET": sock, "DECIDE_PROVIDERS": providers,
               "DECIDE_OPENAI_CONSUMERS": getpass.getuser() if consumers is None else consumers,
               "DECIDE_OPENAI_URL": openai.url if openai else "http://127.0.0.1:9/v1/decisions",
               "DECIDE_CLAUDE_BIN": claude or "/nonexistent", "DECIDE_CODEX_BIN": codex or "/nonexistent"}
        env.pop("CODEX_HOME", None)
        self.log = open(self.home / "log.txt", "w")
        p = subprocess.Popen([sys.executable, str(D / "server.py")], env=env, stderr=self.log)
        self.procs.append(p)
        self.env = {**os.environ, "DECIDE_SOCKET": sock, "DECIDE_CONFIG": "/nonexistent"}
        for _ in range(50):
            if os.path.exists(sock):
                return
            time.sleep(0.1)

    def ask(self, request=REQUEST, *args):
        p = subprocess.run([sys.executable, str(D / "vallis-decide"), *args], input=json.dumps(request),
                           capture_output=True, text=True, env=self.env, timeout=30)
        return p.returncode, (json.loads(p.stdout) if p.stdout.strip() else None), p.stderr

    def test_openai_decisions_is_a_thin_proxy(self):
        fake = FakeOpenAI(200, {"name": "reaction", "type": "choice", "choice": "😁"})
        self.fakes.append(fake)
        self.start("openai-decisions,claude", openai=fake, key=True)
        rc, out, _ = self.ask()
        self.assertEqual((rc, out["provider"], out["answers"][0]["choice"]), (0, "openai-decisions", "😁"))
        auth, body = fake.calls[0]
        self.assertEqual((auth, body["model"]), ("Bearer sk-test", "gpt-6-luna"))
        self.assertEqual(body["questions"], REQUEST["questions"])

    def test_openai_key_only_for_allowed_accounts(self):
        fake = FakeOpenAI(200, {"name": "reaction", "type": "choice", "choice": "😁"})
        self.fakes.append(fake)
        self.start("openai-decisions,claude", openai=fake, key=True, consumers="someone-else",
                   claude=self.cli("claude", '{"reaction": "👍"}'), claude_login=True)
        rc, out, _ = self.ask()
        self.assertEqual((rc, out["provider"]), (0, "claude"))     # the key was not used for this account
        self.assertEqual(fake.calls, [])

    def test_openai_goes_to_one_address_only(self):
        env = {**os.environ, "DECIDE_OPENAI_URL": "https://example.com/v1/other"}
        out = subprocess.run([sys.executable, "-c", "import runpy,sys;sys.argv=['x'];"
                              "m=runpy.run_path(sys.argv[0] if False else %r, run_name='m');print(m['OPENAI_URL'])"
                              % str(D / "server.py")], env=env, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "https://api.openai.com/v1/decisions")

    def test_cascade_openai_fails_then_claude(self):
        fake = FakeOpenAI(500)
        self.fakes.append(fake)
        self.start("openai-decisions,claude,codex", openai=fake, key=True,
                   claude=self.cli("claude", '{"reaction": "👍"}'), claude_login=True)
        rc, out, _ = self.ask()
        self.assertEqual((rc, out["provider"], out["answers"][0]["choice"]), (0, "claude", "👍"))

    def test_off_the_list_moves_to_next_provider(self):
        self.start("claude,codex", claude=self.cli("claude", '{"reaction": "🦖"}'), claude_login=True,
                   codex=self.cli("codex", '{"reaction": "😁"}'), codex_login=True)
        rc, out, _ = self.ask()
        self.assertEqual((rc, out["provider"], out["answers"][0]["choice"]), (0, "codex", "😁"))

    def test_provider_without_own_login_is_skipped(self):
        self.start("claude,codex", claude=self.cli("claude", '{"reaction": "👍"}'),
                   codex=self.cli("codex", '{"reaction": "😁"}'), codex_login=True)
        rc, out, _ = self.ask()
        self.assertEqual(out["provider"], "codex")

    def test_nothing_ready_is_needs_setup_and_no_answer(self):
        self.start("openai-decisions,claude")
        rc, out, err = self.ask()
        self.assertEqual((rc, out), (1, None))
        self.assertIn("no provider", err)
        rc, status = subprocess.run([sys.executable, str(D / "vallis-decide"), "--status"], capture_output=True,
                                    text=True, env=self.env).returncode, json.loads((self.home / "state.json").read_text())
        self.assertEqual(status["state"], "needs_setup")

    def test_state_names_the_active_provider(self):
        self.start("openai-decisions,codex", codex=self.cli("codex", "{}"), codex_login=True)
        subprocess.run([sys.executable, str(D / "vallis-decide"), "--status"], capture_output=True, env=self.env)
        state = json.loads((self.home / "state.json").read_text())
        self.assertEqual((state["state"], state["reason"]), ("installed", "provider: codex"))

    def test_predicate_and_score(self):
        req = {"input": "text", "questions": [
            {"type": "predicate", "name": "urgent", "instructions": "Срочно?"},
            {"type": "score", "name": "tone", "instructions": "Тон.", "levels": [{"label": "low"}, {"label": "high"}]}]}
        self.start("claude", claude=self.cli("claude", '{"urgent": true, "tone": "high"}'), claude_login=True)
        rc, out, _ = self.ask(req)
        self.assertEqual(out["answers"], [{"name": "urgent", "type": "predicate", "probability": 1.0},
                                          {"name": "tone", "type": "score", "score": 1, "label": "high"}])

    def test_no_request_text_in_the_log(self):
        self.start("claude", claude=self.cli("claude", '{"reaction": "👍"}'), claude_login=True)
        self.ask()
        self.log.flush()
        log = (self.home / "log.txt").read_text()
        self.assertIn("provider=claude", log)
        self.assertNotIn("пицц", log)
        self.assertNotIn("👍", log)

    def test_bad_request_is_refused(self):
        self.start("claude", claude=self.cli("claude", "{}"), claude_login=True)
        rc, out, err = self.ask({"input": "x", "questions": []})
        self.assertEqual(rc, 1)
        self.assertIn("questions required", err)


if __name__ == "__main__":
    unittest.main()
