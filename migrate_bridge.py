"""One-time source migration for the bridge bundled with assistant v0.1.9.

Run on the BB server as its service user, with a quiet maintenance window.
Default is inspection. --apply requires an unused receipt path outside the project.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from uuid import UUID

PLUGIN = "telegram-projects"
REPO = "git:https://github.com/ainurminibaev/bb-plugin-telegram-projects.git@"
LEGACY = REPO + "82a41268c5f0ed97b8ca24bd198b8729ce6ba5e5"
TRACKING = REPO + "v0.9.18-rc.1"


def bb(*args):
    result = subprocess.run(["bb", *args, "--json"], capture_output=True, text=True, timeout=240)
    if result.returncode:
        raise RuntimeError(f"Command failed: bb {' '.join(args[:4])}. Inspect the BB server log.")
    return json.loads(result.stdout)


def rpc(name, **payload):
    if not payload:
        return bb("plugin", "rpc", "call", PLUGIN, name)
    with tempfile.NamedTemporaryFile(mode="w") as request:
        json.dump(payload, request)
        request.flush()
        return bb("plugin", "rpc", "call", PLUGIN, name, "--input-file", request.name)


def source():
    return next((p["source"] for p in bb("plugin", "list")["plugins"] if p["id"] == PLUGIN), None)


def receivers(flags):
    for key, value in flags.items():
        bb("plugin", "config", PLUGIN, "set", key, str(value).lower())
    bb("plugin", "reload", PLUGIN)


def quiet():
    state = rpc("status")
    if state["queue"] or state["chatQueue"] or state["chatError"]:
        raise ValueError("Resolve queued work or delivery errors first.")
    live = bb("telegram-projects", "chat-status")
    if live.get("started") is not False:
        if live["pending"] or live["outgoing"] or live.get("lastError"):
            raise ValueError("Wait for all messages to be delivered.")
    for binding in state["chatBindings"]:
        handoff = rpc("providerHandoffStatus", topicId=binding["topicId"])
        if handoff and handoff.get("phase") not in {"done", "cancelled"}:
            raise ValueError("Wait for the provider handoff.")
        if binding["threadId"]:
            res = bb("thread", "show", binding["threadId"])
            if res.get("thread", res)["status"] != "idle":
                raise ValueError("Wait for the bound thread to become idle.")
    return state


def verify_checkpoint(snapshot, backup):
    with os.fdopen(os.open(snapshot, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or info.st_size > 100 * 1024 * 1024):
            raise ValueError("Expected a private snapshot owned by the BB service user.")
        data = stream.read(100 * 1024 * 1024 + 1)
    if len(data) != info.st_size or hashlib.sha256(data).hexdigest() != backup["sha256"]:
        raise ValueError("Could not verify the snapshot on this host.")
    state = json.loads(data)
    if (state["pluginId"] != PLUGIN or state["version"] != 1 or state["settings"]["enabled"]
            or state["settings"]["chatEnabled"] or len(state["rows"]) != backup["rows"]):
        raise ValueError("Unexpected checkpoint format or receiver state.")
    # Recheck the durable state after polling stops. Work can arrive between
    # the initial readiness check and the receivers acknowledging shutdown.
    for row in state["rows"]:
        key, value = row["key"], row["value"]
        busy = (key.startswith("queue:")
                or (key.startswith("chat:in:") and value.get("state") != "done")
                or (key.startswith("chat:out:") and ":part:" not in key and not value.get("sentId"))
                or (key.startswith("chat:handoff:") and value.get("phase") not in {"done", "cancelled"})
                or (key.startswith("group:in:") and value.get("state") not in {"done", "revoked"}))
        if busy:
            raise ValueError("Work arrived before shutdown. Resume the receivers and retry after delivery.")


    return state


def verify_restored_state(before, after):
    # personal.28 adds these two empty settings to an otherwise unchanged v0.1.9 installation.
    settings = dict(after["settings"])
    for key in ("isolatedGroupProject", "isolatedFileHost"):
        if key not in before["settings"] and settings.get(key) == "":
            del settings[key]
    comparable = dict(after, settings=settings)
    if json.dumps(before, sort_keys=True) != json.dumps(comparable, sort_keys=True):
        raise ValueError("Re-export differs from backup. Receivers remain stopped.")


def migrate(receipt):
    current = source()
    if current == TRACKING:
        print("The bridge already tracks compatible releases.")
        return
    if current != LEGACY:
        raise ValueError("Only the v0.1.9 bridge source is supported by this migration.")
    state = quiet()
    flags = {k: state[k] for k in ("enabled", "chatEnabled")}
    if receipt is None:
        print(f"Eligible for migration. Bound chats: {len(state['chatBindings'])}.")
        return
    with receipt.open("x") as journal:
        def record(event, **data):
            journal.write(json.dumps(dict(event=event, **data)) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        record("before", source=current, target=TRACKING, flags=flags, bindings=state["chatBindings"])
        removal_started = False
        try:
            receivers(dict(enabled=False, chatEnabled=False))
            backup = rpc("exportPrivateState", confirm=True)
            snapshot = Path.home() / ".bb-plugin-backups" / PLUGIN / (str(UUID(backup["id"])) + ".json")
            before = verify_checkpoint(snapshot, backup)
            # A stopped receiver omits bindings from its status RPC.
            threads = {row["value"].get("threadId") for row in before["rows"]
                       if row["key"].startswith("chat:binding:")}
            for thread_id in sorted(filter(None, threads)):
                thread = bb("thread", "show", thread_id)
                if thread.get("thread", thread)["status"] != "idle":
                    raise ValueError("Wait for the bound thread to become idle.")
            record("backup", **backup)
            removal_started = True
            bb("plugin", "remove", PLUGIN)
            bb("plugin", "install", TRACKING, "--yes")
            restored = rpc("restorePrivateState", confirm=True, id=backup["id"], sha256=backup["sha256"])
            if any(restored[k] != backup[k] for k in ("sha256", "rows")):
                raise ValueError("Restore receipt differs from backup.")
            verified = rpc("exportPrivateState", confirm=True)
            if verified["sha256"] != backup["sha256"]:
                reexport = snapshot.parent / (str(UUID(verified["id"])) + ".json")
                verify_restored_state(before, verify_checkpoint(reexport, verified))
            record("restored", **restored)
        finally:
            if not removal_started:
                receivers(flags)
        receivers(flags)
        after = rpc("status")
        if source() != TRACKING or any(after[k] != state[k] for k in (*flags, "chatBindings")):
            raise ValueError("Final check failed. Inspect the receipt before retrying.")
        record("verified")
        print("Version tracking enabled. Private state and chat bindings verified.")


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", metavar="NEW_PRIVATE_RECEIPT", type=Path)
    try:
        migrate(parser.parse_args().apply)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"{error}\n")
