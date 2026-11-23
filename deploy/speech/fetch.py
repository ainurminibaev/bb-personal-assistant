#!/usr/bin/env python3
"""Download model files from their original open source at a pinned revision and check them.

    fetch.py hf <repo> <revision> <out_dir> <file>=<sha256|git>:<hex> ...   Hugging Face, file at a fixed commit
    fetch.py url <url> <sha256>:<hex> <out_file>                             any URL with a checksum
"git:" is the git blob id of a small file (how the source repository itself names it); large files use sha256.

Exit codes: 0 — done; 3 — the source is unreachable (no network, HTTP error): the installer reports
"needs_setup" with the printed reason and goes on; 4 — checksum mismatch (file removed).
"""
import hashlib
import os
import sys
import urllib.error
import urllib.request

UA = "bb-speech-installer"


def download(url, dst):
    tmp = dst + ".part"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r, \
                open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    except (urllib.error.URLError, OSError) as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"source unreachable: {url.split('?')[0]} ({getattr(e, 'reason', e)})", file=sys.stderr)
        sys.exit(3)
    os.replace(tmp, dst)


def check(path, algo, want):
    h = hashlib.new("sha1" if algo == "git" else algo)
    if algo == "git":
        h.update(b"blob %d\0" % os.path.getsize(path))
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != want.lower():
        os.unlink(path)
        print(f"checksum mismatch: {os.path.basename(path)} ({algo})", file=sys.stderr)
        sys.exit(4)


def main(argv):
    if argv[:1] == ["hf"] and len(argv) >= 5:
        repo, rev, out = argv[1:4]
        os.makedirs(out, exist_ok=True)
        for spec in argv[4:]:
            name, _, digest = spec.partition("=")
            algo, _, want = digest.partition(":")
            if algo not in ("sha256", "git") or not want:
                print(f"no checksum for {name}", file=sys.stderr)
                return 64
            dst = os.path.join(out, name)
            if not os.path.exists(dst):
                download(f"https://huggingface.co/{repo}/resolve/{rev}/{name}", dst)
            check(dst, algo, want)
        return 0
    if argv[:1] == ["url"] and len(argv) == 4:
        url, digest, dst = argv[1:4]
        algo, _, want = digest.partition(":")
        if algo != "sha256" or not want:
            return 64
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        if not os.path.exists(dst):
            download(url, dst)
        check(dst, algo, want)
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
