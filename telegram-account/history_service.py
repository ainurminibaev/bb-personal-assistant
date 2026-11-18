"""Serve one read-only request on a Linux systemd Accept=yes Unix socket."""
import argparse
import asyncio
import fcntl
import json
import os
import socket
import struct
import sys
from types import SimpleNamespace

import account


def parse(data):
    if len(data) > 4096 or not data.endswith(b"\n"):
        raise ValueError("Invalid request")
    value = json.loads(data)
    if value == {"cmd": "channels"}:
        return SimpleNamespace(cmd="channels")
    if not isinstance(value, dict) or set(value) != {"cmd", "channel", "words", "limit"}:
        raise ValueError("Invalid fields")
    if (value["cmd"] != "search" or type(value["channel"]) is not int or value["channel"] >= 0
            or type(value["limit"]) is not int or not 1 <= value["limit"] <= 50
            or not isinstance(value["words"], str) or not 1 <= len(value["words"].strip()) <= 512):
        raise ValueError("Invalid search")
    return SimpleNamespace(**value)


def serve(sock, coordinator_uid, owner_id):
    _, peer_uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if peer_uid != coordinator_uid:
        return {"error": "access_denied"}
    with sock.makefile("rb") as stream:
        try:
            args = parse(stream.readline(4097))
        except (ValueError, UnicodeError):
            return {"error": "invalid_request"}
    try:
        with (account.ROOT / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            result = asyncio.run(asyncio.wait_for(account.read_channels(args, owner_id), timeout=45))
        return {"result": result}
    except account.RevokedSession:
        return {"error": "session_revoked"}
    except Exception:
        return {"error": "read_unavailable"}


if __name__ == "__main__":
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--coordinator-uid", required=True, type=int)
    p.add_argument("--owner-id", required=True, type=int)
    args = p.parse_args()
    if os.geteuid() == 0 or args.coordinator_uid in (0, os.geteuid()) or args.coordinator_uid < 0 or args.owner_id <= 0:
        p.error("Use two distinct unprivileged system users and a positive paired Telegram owner ID.")
    with socket.socket(fileno=os.dup(sys.stdin.fileno())) as connection:
        connection.settimeout(10)
        reply = serve(connection, args.coordinator_uid, args.owner_id)
        connection.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")
