"""Optional self-hosted Telegram account connector. Run on the owner's machine."""
import argparse
import asyncio
import base64
import contextlib
import fcntl
import getpass
import hmac
import json
import os
import secrets
import subprocess
from urllib.parse import urlsplit
import sys
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from telethon import TelegramClient, utils
from telethon.errors import AuthKeyUnregisteredError, SessionPasswordNeededError, SessionRevokedError
from telethon.sessions import StringSession
from telethon.tl.types import InputPeerChannel
import uvicorn

ROOT = Path(os.environ.get("PA_TELEGRAM_STATE", "~/.local/state/personal-assistant/telegram")).expanduser()
APP = ROOT / "app.json"
ACCOUNT = ROOT / "account.json"


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ROOT.stat().st_mode & 0o077:
        raise SystemExit("The state directory requires permissions 0700.")
    with tempfile.NamedTemporaryFile(mode="w", dir=ROOT, delete=False) as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, path)


def owner():
    configured = os.environ.get("PA_TELEGRAM_OWNER_ID")
    if configured is not None:
        if not configured.isascii() or not configured.isdecimal() or int(configured) <= 0:
            raise ValueError("Invalid paired Telegram owner ID.")
        return int(configured)
    raw = subprocess.check_output(["bb", "plugin", "rpc", "call", "telegram-projects", "status", "--json"])
    value = json.loads(raw).get("owner")
    if type(value) is not int or value <= 0:
        raise SystemExit("Pair the owner with the private Telegram bot in BB first.")
    return value


def client(session=""):
    app = read(APP)
    return TelegramClient(StringSession(session), app["id"], app["hash"], device_model="Personal Assistant")


class RevokedSession(ValueError):
    """Telegram confirmed that the stored key no longer authorizes a user."""


async def authorized(expected):
    saved = read(ACCOUNT)
    if saved["owner"] != expected:
        raise ValueError("The saved connection belongs to a different owner.")
    c = client(saved["session"])
    try:
        await c.connect()
        me = await c.get_me()
        if me is None:
            raise RevokedSession("Telegram revoked this session. Connect again.")
        if me.id != expected:
            raise ValueError("This session belongs to another owner.")
        return c, saved
    except (AuthKeyUnregisteredError, SessionRevokedError) as error:
        await c.disconnect()
        raise RevokedSession("Telegram revoked this session. Connect again.") from error
    except BaseException:
        await c.disconnect()
        raise


async def search(args):
    status = json.loads(subprocess.check_output(
        ["bb", "plugin", "rpc", "call", "telegram-projects", "status", "--json"]))
    thread = os.environ.get("BB_THREAD_ID")
    if not thread or not any(b.get("threadId")==thread and b.get("topicId")==0 for b in status.get("chatBindings", [])):
        raise ValueError("Search is available only from the paired private thread.")
    print(json.dumps(await read_channels(args, status["owner"]), ensure_ascii=False))


async def read_channels(args, expected_owner):
    c, saved = await authorized(expected_owner)
    try:
        if args.cmd=="search" and args.channel not in saved["channels"]:
            raise ValueError("The owner has not selected this channel.")
        async def permitted_channels():
            wanted = set(saved["channels"]) if args.cmd == "channels" else {args.channel}
            peers = saved.get("channelPeers", {})
            if all(str(channel) in peers for channel in wanted):
                for channel in sorted(wanted):
                    peer = peers[str(channel)]
                    reference = InputPeerChannel(peer["id"], peer["accessHash"])
                    if utils.get_peer_id(reference) != channel:
                        raise ValueError("The saved channel does not match the selected channel. Reconnect.")
                    entity = await c.get_entity(reference)
                    if getattr(entity, "broadcast", False):
                        yield channel, entity
                return
            # v0.1.9 stored only marked IDs. Reconnecting once caches selected peers.
            async for dialog in c.iter_dialogs():
                if dialog.id in wanted and getattr(dialog.entity, "broadcast", False):
                    yield dialog.id, dialog.entity
                    wanted.remove(dialog.id)
                    if not wanted:
                        return

        available = []
        async for channel, entity in permitted_channels():
            if args.cmd == "channels":
                available.append(dict(id=channel, title=entity.title))
                continue
            hits = []
            async for msg in c.iter_messages(entity, search=args.words, limit=args.limit):
                hits.append(dict(id=msg.id, date=msg.date.isoformat(), text=msg.raw_text[:800],
                                 url=f"https://t.me/c/{entity.id}/{msg.id}"))
            return hits
        if args.cmd == "channels":
            return available
        raise ValueError("This account no longer has access to the channel.")
    finally:
        await c.disconnect()


def wizard(expected, port):
    import shutil
    if not shutil.which("qrencode"):
        raise ValueError("Install qrencode on the host running the connection wizard.")
    token = secrets.token_urlsafe(32)
    deadline = time.monotonic() + 600
    lock = asyncio.Lock()
    c = None
    watcher = None
    qr_image = ""
    channels = {}
    channel_peers = {}
    phase = "new"
    fresh = not ACCOUNT.exists()
    committed = False

    async def list_channels():
        nonlocal phase
        me = await c.get_me()
        if not me or me.id != expected:
            phase = "wrong-owner"
            if fresh and me:
                await c.log_out()
            return
        async for d in c.iter_dialogs():
            if getattr(d.entity, "broadcast", False) and isinstance(d.entity.access_hash, int):
                channels[d.id] = d.title
                channel_peers[d.id] = {"id": d.entity.id, "accessHash": d.entity.access_hash}
        phase = "channels"

    async def login():
        nonlocal phase, qr_image
        try:
            while time.monotonic() < deadline:
                qr = await c.qr_login()
                png = subprocess.run(["qrencode", "-t", "PNG", "-o", "-"], input=qr.url.encode(),
                                     stdout=subprocess.PIPE, check=True).stdout
                qr_image = base64.b64encode(png).decode()
                phase = "qr"
                try:
                    await qr.wait()
                except asyncio.TimeoutError:
                    continue
                except SessionPasswordNeededError:
                    phase = "password"
                    return
                await list_channels()
                return
        except Exception:
            phase = "error"

    @contextlib.asynccontextmanager
    async def lifespan(app):
        nonlocal c, phase, watcher, fresh
        try:
            if not fresh:
                try:
                    c, _ = await authorized(expected)
                except RevokedSession:
                    ACCOUNT.unlink()
                    fresh = True
            if fresh:
                c = client()
                await c.connect()
                watcher = asyncio.create_task(login())
            else:
                await list_channels()
            yield
        finally:
            if watcher:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            if c and c.is_connected():
                try:
                    if fresh and not committed and await c.is_user_authorized():
                        await c.log_out()
                finally:
                    await c.disconnect()

    web = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    local_host = f"127.0.0.1:{port}"
    public = read(APP).get("origin")
    origin = public or "http://"+local_host
    allowed_hosts = {local_host, urlsplit(origin).netloc}
    @web.middleware("http")
    async def security(req, call_next):
        if req.headers.get("host") not in allowed_hosts or (req.method == "POST" and
            (req.headers.get("origin") != origin or
             not hmac.compare_digest(req.headers.get("x-owner-key",""), token))):
            return JSONResponse({}, status_code=403)
        res = await call_next(req)
        res.headers.update({"Cache-Control":"no-store", "Referrer-Policy":"no-referrer",
                            "X-Frame-Options":"DENY","X-Content-Type-Options":"nosniff",
                            "Content-Security-Policy":"default-src 'none'; connect-src 'self'; img-src data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"})
        return res

    @web.get("/")
    async def home():
        return HTMLResponse(Path(__file__).with_name("connect.html").read_text())

    @web.post("/step")
    async def step(req: Request):
        nonlocal phase, committed
        async with lock:
            if committed or time.monotonic() >= deadline:
                return JSONResponse({}, status_code=410)
            data = await req.json()
            if phase == "password" and data.get("password"):
                try:
                    await c.sign_in(password=data["password"])
                except Exception:
                    return {"phase":"password","error":"Telegram rejected the password."}
                await list_channels()
            if phase == "channels" and "channels" in data:
                ids = data["channels"]
                if not isinstance(ids,list) or not ids or any(type(n) is not int or n not in channels for n in ids):
                    return JSONResponse({}, status_code=400)
                if expected != owner():
                    return JSONResponse({}, status_code=403)
                selected = sorted(set(ids))
                write(ACCOUNT, dict(owner=expected, session=c.session.save(), channels=selected,
                                    channelPeers={str(i): channel_peers[i] for i in selected}))
                committed = True
                return {"phase":"done"}
            return {"phase":phase, "qr":qr_image if phase=="qr" else "",
                    "channels":list(channels.items()) if phase=="channels" else [],
                    "selected":read(ACCOUNT)["channels"] if phase=="channels" and not fresh else []}

    async def serve():
        server=uvicorn.Server(uvicorn.Config(web,host="127.0.0.1",port=port,access_log=False,log_level="error"))
        task=asyncio.create_task(server.serve())
        try:
            while not task.done() and not server.started:
                await asyncio.sleep(.1)
            if server.started:
                print(f"{origin}/#{token}",flush=True)
            while not task.done() and not committed and time.monotonic()<deadline:
                await asyncio.sleep(.5)
        finally:
            server.should_exit=True
            await task
    asyncio.run(serve())


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest="cmd",required=True)
    sub.add_parser("configure").add_argument("--origin",help="Dedicated HTTPS origin for the wizard on your reverse proxy")
    sub.add_parser("open")
    sub.add_parser("channels")
    sub.add_parser("disconnect")
    sub.add_parser("connect").add_argument("--port",type=int,default=18341)
    s=sub.add_parser("search")
    s.add_argument("channel",type=int)
    s.add_argument("words")
    s.add_argument("--limit",type=int,choices=range(1,51),default=10)
    a=parser.parse_args()
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ROOT.stat().st_mode & 0o077:
        raise ValueError("The state directory requires permissions 0700.")
    if a.cmd=="open":
        status = json.loads(subprocess.check_output(["bb","plugin","rpc","call","telegram-projects","status","--json"]))
        thread = os.environ.get("BB_THREAD_ID")
        if not thread or not any(b.get("topicId")==0 and b.get("threadId")==thread for b in status.get("chatBindings",[])):
            raise ValueError("Open the connection link from the paired private thread.")
        if not APP.exists() or not read(APP).get("origin"):
            raise ValueError("Configure an HTTPS origin for the wizard or run connect over SSH.")
        import select
        proc=subprocess.Popen([sys.executable,__file__,"connect"],stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True)
        ready,_,_=select.select([proc.stdout],[],[],30)
        url=proc.stdout.readline().strip() if ready else ""
        if not url.startswith(read(APP)["origin"]+"/#"):
            if proc.poll() is None:proc.terminate()
            raise ValueError("The link could not be created. Check the service and connect again.")
        print(url)
        return
    with (ROOT / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if a.cmd in ("search","channels") else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another Telegram account operation is in progress.")
        if a.cmd=="configure":
            if ACCOUNT.exists():raise ValueError("Disconnect the current session before replacing the application.")
            key=int(input("Telegram application API ID: "))
            secret=getpass.getpass("Telegram application API hash: ").strip()
            if key<=0 or len(secret)!=32 or any(c not in "0123456789abcdefABCDEF" for c in secret):
                raise ValueError("Invalid Telegram application credentials.")
            origin=a.origin
            if origin:
                parsed=urlsplit(origin)
                if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("","/"):
                    raise ValueError("Enter a dedicated HTTPS origin without a path or query.")
                origin="https://"+parsed.netloc
            write(APP,dict(id=key,hash=secret,origin=origin))
        elif a.cmd=="connect":
            if not APP.is_file():raise ValueError("Run configure first.")
            wizard(owner(),a.port)
        elif a.cmd in ("search","channels"):
            asyncio.run(search(a))
        else:
            async def revoke():
                try:
                    c, _ = await authorized(owner())
                except RevokedSession:
                    ACCOUNT.unlink()
                    return
                try:
                    if not await c.log_out():raise ValueError("Telegram did not confirm sign-out.")
                    ACCOUNT.unlink()
                finally:
                    await c.disconnect()
            asyncio.run(revoke())


if __name__=="__main__":
    try: main()
    except (ValueError,FileNotFoundError) as error:
        sys.exit(str(error))
