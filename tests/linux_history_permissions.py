"""Linux integration test. Run as root in a disposable test environment."""
import json
import os
from pathlib import Path
import pwd
import socket
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "telegram-account"))
import history_service as service


def drop(uid):
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)


def child(func):
    pid = os.fork()
    if not pid:
        try:
            func()
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    return pid


def join(pid):
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0


def main():
    if sys.platform != "linux" or os.getuid() != 0:
        raise SystemExit("Run under Linux as root with disposable numeric test users.")
    unused = []
    for uid in range(63000, 64000):
        try:
            pwd.getpwuid(uid)
        except KeyError:
            unused.append(uid)
        if len(unused) == 3:
            break
    keeper, coordinator, group = unused
    with tempfile.TemporaryDirectory() as dirname:
        root = Path(dirname)
        root.chmod(0o711)
        state = root / "vault"
        state.mkdir(mode=0o700)
        secret_file = state / "account.json"
        secret_file.write_text(json.dumps({"session": "private-fixture-key", "owner": 42,
            "channels": [-1000000000123], "channelPeers": {"-1000000000123": {"id": 123, "accessHash": 7}}}))
        secret_file.chmod(0o600)
        os.chown(secret_file, keeper, keeper)
        os.chown(state, keeper, keeper)
        address = str(root / "history.sock")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(address)
            # Exercise SO_PEERCRED even when filesystem permissions permit connection.
            os.chmod(address, 0o666)
            listener.listen()
            listener.settimeout(15)

            def server():
                drop(keeper)
                service.account.ROOT = state
                service.account.ACCOUNT = secret_file

                class FixtureTelegram:
                    async def connect(self): pass
                    async def get_me(self): return SimpleNamespace(id=42)
                    async def disconnect(self): pass
                    async def get_entity(self, peer):
                        assert peer.channel_id == 123
                        return SimpleNamespace(title="allowed-fixture", broadcast=True)
                def fixture_client(session):
                    assert session == "private-fixture-key"
                    return FixtureTelegram()
                service.account.client = fixture_client
                for _ in range(4):
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(3)
                        reply = service.serve(conn, coordinator, 42)
                        conn.sendall(json.dumps(reply).encode()+b"\n")

            worker = child(server)
            listener.close()

            def request(uid, data, expected, subgroup=False):
                def run():
                    drop(uid)
                    def query():
                        try:
                            secret_file.read_bytes()
                        except PermissionError:
                            pass
                        else:
                            raise AssertionError("Caller could read session key")
                        with socket.socket(socket.AF_UNIX) as client:
                            client.settimeout(5)
                            client.connect(address)
                            client.sendall(json.dumps(data).encode()+b"\n")
                            reply = json.loads(client.makefile("rb").readline())
                            assert reply == expected, reply
                    if subgroup:
                        join(child(query))
                    else:
                        query()
                join(child(run))
            try:
                request(coordinator, {"cmd": "channels"}, {"result": [{"id": -1000000000123, "title": "allowed-fixture"}]})
                request(coordinator, {"cmd": "search", "channel": -1000000000124, "limit": 1, "words": "test"},
                        {"error": "read_unavailable"})
                for sub in (False, True):
                    request(group, {"cmd": "channels"}, {"error": "access_denied"}, sub)
            finally:
                join(worker)
    print("PASS: allowed coordinator read, channel scope, session file protection, denied group and child.")


if __name__ == "__main__":
    main()
