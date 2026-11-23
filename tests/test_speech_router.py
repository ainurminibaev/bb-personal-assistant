"""Router of the speech service (deploy/speech/voice-to-text): order, profiles, language, Codex login.

Fake Codex and local models run on loopback; the router is run as a program, like the bridge does.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROUTER = Path(__file__).resolve().parents[1] / "deploy" / "speech" / "voice-to-text"


class Fake:
    """One loopback HTTP server: path -> (status, body); records the paths it was asked for."""

    def __init__(self, routes):
        self.routes, self.calls, self.names = routes, [], []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                path = self.path.split("?")[0]
                fake.calls.append(path)
                m = __import__("re").search(rb'filename="([^"]+)"', raw)
                fake.names.append(m.group(1).decode() if m else None)
                route = fake.routes.get(path, (404, "no"))
                status, body = route.pop(0) if isinstance(route, list) else route
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.audio = d / "voice.ogg"
        self.audio.write_bytes(b"OggS fake")
        self.codex_home = d / "codex"
        self.codex_home.mkdir()
        (self.codex_home / "auth.json").write_text(json.dumps({"tokens": {"access_token": "t"}}))
        self.fakes = []

    def tearDown(self):
        for f in self.fakes:
            f.close()
        self.tmp.cleanup()

    def fake(self, routes):
        f = Fake(routes)
        self.fakes.append(f)
        return f

    def run_router(self, codex=None, ru=None, multi=None, order="codex-first", profiles="ru,multi", lang=None,
                   login=True):
        env = {**os.environ, "SPEECH_CONFIG": "/nonexistent", "SPEECH_CODEX_PAUSE_SEC": "0.1", "SPEECH_ORDER": order, "SPEECH_PROFILES": profiles,
               "SPEECH_LANG": "ru", "CODEX_HOME": str(self.codex_home if login else Path(self.tmp.name) / "none"),
               "CODEX_TRANSCRIBE_URL": (codex.url + "/transcribe") if codex else "http://127.0.0.1:9/transcribe",
               "SPEECH_RU_URL": ru.url if ru else "http://127.0.0.1:9", "SPEECH_MULTI_URL": multi.url if multi else "http://127.0.0.1:9"}
        env.pop("CODEX_AUTH_FILE", None)
        args = [sys.executable, str(ROUTER), str(self.audio)] + (["--lang", lang] if lang else [])
        p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout.strip(), p.stderr

    def models(self, lid_ru=0.9):
        ru = self.fake({"/asr": (200, "привет"), "/detect-language": (200, json.dumps({"language_code": "ru", "ru": lid_ru}))})
        multi = self.fake({"/asr": (200, "сәлам")})
        return ru, multi

    def test_codex_first(self):
        codex = self.fake({"/transcribe": (200, json.dumps({"text": "из облака"}))})
        ru, multi = self.models()
        rc, out, err = self.run_router(codex, ru, multi)
        self.assertEqual((rc, out), (0, "из облака"))
        self.assertIn("route=codex", err)
        self.assertEqual(ru.calls + multi.calls, [])

    def test_codex_server_error_is_retried_once(self):
        codex = self.fake({"/transcribe": [(500, "down"), (200, json.dumps({"text": "со второго раза"}))]})
        ru, multi = self.models()
        rc, out, err = self.run_router(codex, ru, multi)
        self.assertEqual((rc, out), (0, "со второго раза"))
        self.assertEqual(codex.calls, ["/transcribe", "/transcribe"])
        self.assertIn("attempts=2", err)

    def test_codex_limit_goes_local_at_once(self):
        codex = self.fake({"/transcribe": [(429, "limit"), (200, json.dumps({"text": "не должно"}))]})
        ru, multi = self.models(lid_ru=0.95)
        rc, out, err = self.run_router(codex, ru, multi)
        self.assertEqual((rc, out), (0, "привет"))
        self.assertEqual(codex.calls, ["/transcribe"])           # no retry on a limit
        self.assertIn("codex failed: http 429", err)

    def test_telegram_oga_is_sent_as_ogg(self):
        self.audio = self.audio.with_name("file_14.oga")
        self.audio.write_bytes(b"OggS fake")
        codex = self.fake({"/transcribe": (200, json.dumps({"text": "ок"}))})
        rc, out, _ = self.run_router(codex, None, None)
        self.assertEqual((rc, out, codex.names), (0, "ок", ["file_14.ogg"]))

    def test_codex_fails_then_local_russian(self):
        codex = self.fake({"/transcribe": (500, "down")})
        ru, multi = self.models(lid_ru=0.95)
        rc, out, err = self.run_router(codex, ru, multi)
        self.assertEqual((rc, out), (0, "привет"))
        self.assertIn("codex failed: http 500", err)
        self.assertEqual(ru.calls, ["/detect-language", "/asr"])

    def test_codex_fails_then_local_tatar(self):
        codex = self.fake({"/transcribe": (500, "down")})
        ru, multi = self.models(lid_ru=0.1)
        rc, out, err = self.run_router(codex, ru, multi)
        self.assertEqual((rc, out), (0, "сәлам"))
        self.assertIn("route=multi", err)

    def test_latin_turkic_goes_to_multilingual(self):
        codex = self.fake({"/transcribe": (200, json.dumps({"text": "Sälam, niček hälläreñ"}))})
        ru, multi = self.models()
        rc, out, err = self.run_router(codex, ru, multi)
        self.assertEqual((rc, out), (0, "сәлам"))
        self.assertIn("Latin Turkic", err)

    def test_local_only_never_calls_codex(self):
        codex = self.fake({"/transcribe": (200, json.dumps({"text": "из облака"}))})
        ru, multi = self.models()
        rc, out, _ = self.run_router(codex, ru, multi, order="local-only")
        self.assertEqual((rc, out), (0, "привет"))
        self.assertEqual(codex.calls, [])

    def test_no_codex_login_goes_local(self):
        ru, multi = self.models()
        rc, out, err = self.run_router(None, ru, multi, login=False)
        self.assertEqual((rc, out), (0, "привет"))
        self.assertIn("no codex login", err)

    def test_explicit_tatar(self):
        codex = self.fake({"/transcribe": (200, json.dumps({"text": "из облака"}))})
        ru, multi = self.models()
        rc, out, _ = self.run_router(codex, ru, multi, lang="tt")
        self.assertEqual((rc, out), (0, "сәлам"))
        self.assertEqual(codex.calls, [])

    def test_only_ru_profile_skips_language_id(self):
        ru, _ = self.models()
        rc, out, _ = self.run_router(None, ru, None, order="local-only", profiles="ru")
        self.assertEqual((rc, out), (0, "привет"))
        self.assertEqual(ru.calls, ["/asr"])

    def test_all_engines_down(self):
        rc, out, err = self.run_router(None, None, None, order="local-only")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("all engines failed", err)

    def test_no_text_or_key_in_log(self):
        codex = self.fake({"/transcribe": (200, json.dumps({"text": "секретный текст"}))})
        rc, _, err = self.run_router(codex, None, None)
        self.assertEqual(rc, 0)
        self.assertNotIn("секретный", err)
        self.assertNotIn("Bearer", err)


if __name__ == "__main__":
    unittest.main()
