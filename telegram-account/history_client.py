"""Search approved Telegram channels through the coordinator's Unix socket."""
import argparse
import json
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", default="/run/personal-agent-history.sock")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("channels")
    search = sub.add_parser("search")
    search.add_argument("channel", type=int)
    search.add_argument("words")
    search.add_argument("--limit", type=int, default=10, choices=range(1, 51))
    args = vars(parser.parse_args())
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(60)
        client.connect(args.pop("socket"))
        client.sendall(json.dumps(args).encode() + b"\n")
        with client.makefile("rb") as stream:
            data = stream.readline(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024 or not data.endswith(b"\n"):
            raise ValueError("Incomplete service response.")
        response = json.loads(data)
        if "error" in response:
            raise ValueError(response["error"])
        print(json.dumps(response["result"], ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(str(error))
