#!/usr/bin/env python3
"""Test stand-in for sops: the same arguments vallis-secret uses, real age encryption, sops-like JSON.

Only for machines without sops; tests/test_vallis_secret.py runs against real sops when it is on PATH.
"""
import base64, datetime, json, os, re, subprocess, sys


def opt(args, name):
    i = args.index(name)
    value = args[i + 1]
    del args[i:i + 2]
    return value


def main():
    args = sys.argv[1:]
    if "--config" in args:
        opt(args, "--config")
    action = args.pop(0)
    for name in ("--input-type", "--output-type", "--filename-override"):
        if name in args:
            opt(args, name)
    if action == "encrypt":
        pattern = re.compile(opt(args, "--unencrypted-regex"))
        recipients = opt(args, "--age").split(",")
        doc = json.loads(open(args[0], "rb").read())
        out = {}
        for key, value in doc.items():
            if pattern.match(key):
                out[key] = value
                continue
            cmd = ["age", "-e"]
            for r in recipients:
                cmd += ["-r", r]
            done = subprocess.run(cmd, input=json.dumps(value).encode(), capture_output=True, check=True)
            out[key] = "ENC[AGE," + base64.b64encode(done.stdout).decode() + "]"
        out["sops"] = {"age": [{"recipient": r, "enc": "…"} for r in recipients],
                       "lastmodified": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       "unencrypted_regex": pattern.pattern, "version": "fake"}
        sys.stdout.write(json.dumps(out))
        return 0
    if action == "decrypt":
        doc = json.load(open(args[0]))
        doc.pop("sops", None)
        for key, value in list(doc.items()):
            if isinstance(value, str) and value.startswith("ENC[AGE,"):
                blob = base64.b64decode(value[len("ENC[AGE,"):-1])
                done = subprocess.run(["age", "-d", "-i", os.environ["SOPS_AGE_KEY_FILE"]], input=blob,
                                      capture_output=True)
                if done.returncode != 0:
                    sys.stderr.write("Failed to get the data key required to decrypt the SOPS file.\n")
                    return 128
                doc[key] = json.loads(done.stdout)
        sys.stdout.write(json.dumps(doc))
        return 0
    sys.stderr.write(f"fake sops: unsupported {action}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
