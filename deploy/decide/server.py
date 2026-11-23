#!/usr/bin/env python3
"""bb-decide: quick decisions for agents, the bridge and plugins of this server (one service per server).

POST /decide   {"request": <Decisions-shaped request>, "timeout": seconds} -> {"answers", "provider", "ms"}
               request = {"input": str, "questions": [{"type": "choice"|"predicate"|"score", "name", "instructions",
                          "choices": [{"value", "description"}] | "levels": [{"label", "description"}]}]}
GET  /health   -> {"state", "reason", "providers": {name: ready}} and the same into the state file

Providers, in the order of DECIDE_PROVIDERS; the first ready one answers, an error or a timeout moves to the next:
  openai-decisions  POST /v1/decisions (gpt-6-luna) with the service's own API key
  claude            claude -p (fast model) under the service's own account login
  codex             codex exec (fast model) under the service's own account login
The request text is never written anywhere: the log keeps the provider, the duration and the outcome only.
systemd socket activation; exits after IDLE_TIMEOUT_SEC without requests.
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.environ.get("HOME", "/var/lib/bb-decide")
PROVIDERS = [p.strip() for p in os.environ.get("DECIDE_PROVIDERS", "openai-decisions").split(",") if p.strip()]
STATE_FILE = os.environ.get("DECIDE_STATE_FILE", os.path.join(HOME, "state.json"))
OPENAI_KEY_FILE = os.environ.get("DECIDE_OPENAI_KEY_FILE", os.path.join(HOME, "openai.key"))
OPENAI_URL = os.environ.get("DECIDE_OPENAI_URL", "https://api.openai.com/v1/decisions")
OPENAI_MODEL = os.environ.get("DECIDE_OPENAI_MODEL", "gpt-6-luna")
CLAUDE_BIN = os.environ.get("DECIDE_CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("DECIDE_CLAUDE_MODEL", "claude-haiku-5-5")
CODEX_BIN = os.environ.get("DECIDE_CODEX_BIN", "codex")
CODEX_MODEL = os.environ.get("DECIDE_CODEX_MODEL", "gpt-6-luna")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "9014"))
IDLE_TIMEOUT_SEC = float(os.environ.get("IDLE_TIMEOUT_SEC", "300"))
MAX_BODY = 256 * 1024


class Fail(Exception):
    pass


def log(*a):
    print("[bb-decide]", *a, file=sys.stderr, flush=True)


def ready():
    """Which providers can answer: each needs its own credential of this service, never anyone else's."""
    def exists(path):
        try:
            return os.path.getsize(path) > 0
        except OSError:
            return False
    return {
        "openai-decisions": exists(OPENAI_KEY_FILE),
        "claude": exists(os.path.join(HOME, ".claude", ".credentials.json")),
        "codex": exists(os.path.join(os.environ.get("CODEX_HOME", os.path.join(HOME, ".codex")), "auth.json")),
    }


def write_state():
    r = ready()
    active = [p for p in PROVIDERS if r.get(p)]
    state = {"state": "installed" if active else "needs_setup",
             "reason": f"provider: {active[0]}" if active else
             "no provider: an OpenAI API key or the service's own Claude/Codex login is needed",
             "providers": {p: bool(r.get(p)) for p in PROVIDERS}}
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, ensure_ascii=False)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass
    return state


def validate(req):
    if not isinstance(req, dict) or not isinstance(req.get("questions"), list) or not req["questions"]:
        raise ValueError("questions required")
    if not isinstance(req.get("input"), (str, list)):
        raise ValueError("input required")
    for q in req["questions"]:
        if q.get("type") not in ("choice", "predicate", "score") or not isinstance(q.get("instructions"), str):
            raise ValueError("bad question")
        if q["type"] == "choice" and not (2 <= len(q.get("choices") or []) <= 255):
            raise ValueError("choice needs 2-255 choices")
        if q["type"] == "score" and not (2 <= len(q.get("levels") or []) <= 10):
            raise ValueError("score needs 2-10 levels")
    return req


# ---------- providers ----------

def openai_decisions(req, deadline):
    with open(OPENAI_KEY_FILE) as f:
        key = f.read().strip()
    body = json.dumps({"model": OPENAI_MODEL, **{k: req[k] for k in ("input", "questions")}}).encode()
    request = urllib.request.Request(OPENAI_URL, data=body, method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=max(1, deadline - time.monotonic())) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise Fail(f"http {e.code}")
    except Exception as e:
        raise Fail(type(e).__name__)
    return data.get("answers") or []


def question_prompt(req):
    """A Decisions request as one plain prompt for a chat model: answer JSON {name: value} only."""
    text = req["input"] if isinstance(req["input"], str) else json.dumps(req["input"], ensure_ascii=False)
    lines = ["Answer the questions about the text. Reply with one JSON object only: {\"<name>\": <answer>}, no other words.",
             "Text:", text, "Questions:"]
    for i, q in enumerate(req["questions"]):
        name = q.get("name") or f"q{i}"
        if q["type"] == "choice":
            opts = "; ".join(f"{c['value']}" + (f" — {c['description']}" if c.get("description") else "") for c in q["choices"])
            lines.append(f"- {name}: {q['instructions']} Answer exactly one value from: {opts}")
        elif q["type"] == "predicate":
            lines.append(f"- {name}: {q['instructions']} Answer true or false.")
        else:
            opts = "; ".join(f"{l['label']}" + (f" — {l['description']}" if l.get("description") else "") for l in q["levels"])
            lines.append(f"- {name}: {q['instructions']} Answer exactly one label from: {opts}")
    return "\n".join(lines)


def answers_from_text(req, reply):
    """Answers in the Decisions shape from a chat model's JSON reply; anything off the list fails."""
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        raise Fail("no json")
    try:
        got = json.loads(m.group(0))
    except ValueError:
        raise Fail("bad json")
    out = []
    for i, q in enumerate(req["questions"]):
        name = q.get("name") or f"q{i}"
        v = got.get(name)
        if q["type"] == "choice":
            values = [c["value"] for c in q["choices"]]
            if v not in values:
                raise Fail("choice off the list")
            out.append({"name": name, "type": "choice", "choice": v})
        elif q["type"] == "predicate":
            if not isinstance(v, bool):
                v = str(v).strip().lower() in ("true", "yes", "да")
            out.append({"name": name, "type": "predicate", "probability": 1.0 if v else 0.0})
        else:
            labels = [l["label"] for l in q["levels"]]
            if v not in labels:
                raise Fail("level off the list")
            out.append({"name": name, "type": "score", "score": labels.index(v), "label": v})
    return out


def run_cli(args, deadline):
    try:
        p = subprocess.run(args, capture_output=True, text=True, cwd=tempfile.gettempdir(),
                           timeout=max(1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        raise Fail("timeout")
    except OSError as e:
        raise Fail(type(e).__name__)
    if p.returncode != 0:
        raise Fail(f"exit {p.returncode}")
    return p.stdout


def claude(req, deadline):
    out = run_cli([CLAUDE_BIN, "-p", "--model", CLAUDE_MODEL, "--output-format", "json", "--max-turns", "1",
                   "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--tools", "", "--",
                   question_prompt(req)], deadline)
    try:
        reply = str(json.loads(out).get("result") or "")
    except ValueError:
        raise Fail("bad output")
    return answers_from_text(req, reply)


def codex(req, deadline):
    out = run_cli([CODEX_BIN, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-m", CODEX_MODEL,
                   "-c", "model_reasoning_effort=low", "--json", "--", question_prompt(req)], deadline)
    reply = ""
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        item = e.get("item") or {}
        if item.get("type") in ("agent_message", "agentMessage") and isinstance(item.get("text"), str):
            reply = item["text"]
    return answers_from_text(req, reply)


ENGINES = {"openai-decisions": openai_decisions, "claude": claude, "codex": codex}


def decide(req, timeout):
    deadline = time.monotonic() + timeout
    r = ready()
    last = "no provider ready"
    for name in PROVIDERS:
        if name not in ENGINES or not r.get(name):
            continue
        if time.monotonic() >= deadline:
            last = "timeout"
            break
        t = time.monotonic()
        try:
            answers = ENGINES[name](req, deadline)
            ms = int((time.monotonic() - t) * 1000)
            log(f"provider={name} ms={ms} outcome=ok")
            return {"answers": answers, "provider": name, "ms": ms}
        except Fail as e:
            last = f"{name}: {e}"
            log(f"provider={name} ms={int((time.monotonic() - t) * 1000)} outcome=failed ({e})")
    raise Fail(last)


class State:
    active = 0
    last = time.monotonic()
    lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = "bb-decide/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _busy(self, delta):
        with State.lock:
            State.active += delta
            State.last = time.monotonic()

    def do_GET(self):
        if self.path != "/health":
            return self._send(404, {"error": "not found"})
        self._send(200, write_state())

    def do_POST(self):
        if self.path != "/decide":
            return self._send(404, {"error": "not found"})
        self._busy(1)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                return self._send(413, {"error": "bad length"})
            body = json.loads(self.rfile.read(length))
            req = validate(body.get("request"))
            timeout = min(60.0, max(1.0, float(body.get("timeout", 8))))
            self._send(200, decide(req, timeout))
        except (ValueError, TypeError) as e:
            self._send(400, {"error": str(e)})
        except Fail as e:
            self._send(503, {"error": str(e)})
        finally:
            self._busy(-1)


def idle_watch(server):
    while True:
        time.sleep(5)
        with State.lock:
            idle = State.active == 0 and time.monotonic() - State.last >= IDLE_TIMEOUT_SEC
        if idle and IDLE_TIMEOUT_SEC > 0:
            log(f"idle {IDLE_TIMEOUT_SEC:.0f}s -> exiting")
            server.shutdown()
            return


def main():
    if os.environ.get("LISTEN_FDS") and int(os.environ.get("LISTEN_PID", os.getpid())) == os.getpid():
        sock = socket.socket(fileno=3)
        server = ThreadingHTTPServer(sock.getsockname()[:2], Handler, bind_and_activate=False)
        server.socket.close()
        server.socket = sock
    else:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    write_state()
    threading.Thread(target=idle_watch, args=(server,), daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
