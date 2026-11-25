#!/bin/bash
# Prepare a Linux server (Ubuntu 24.04) for one digital employee: its own system user, private folders,
# Node, BB and the agent CLI in the user's runtime, and a systemd service for BB on localhost.
# Run as root. Re-running is safe: existing users, folders and runtimes are kept.
#
#   deploy/server-setup.sh <user> <port> [bb-version] [agent-package]
#   deploy/server-setup.sh helper 38886 0.50.1 @openai/codex
# A second employee on the same server: its own user and a free port pair (<port> for the server, <port>+1 for BB's
# host daemon — the default 38887 belongs to the first BB). System packages are installed only when missing.
#
# Then, as <user>: run setup.py from the assistant release with --kind employee (see README).
set -euo pipefail
USER_NAME=${1:?user}; PORT=${2:?port}; BB_VERSION=${3:-latest}; AGENT=${4:-@openai/codex}
NODE_VERSION=${NODE_VERSION:-24.11.1}
HOME_DIR=/var/lib/$USER_NAME
RUNTIME=$HOME_DIR/runtime
DATA=$HOME_DIR/.bb-$USER_NAME

id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$HOME_DIR" --create-home --shell /usr/sbin/nologin "$USER_NAME"
install -d -m 0750 -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR" "$HOME_DIR/workspace"
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$DATA" "$HOME_DIR/.secrets" "$HOME_DIR/telegram-inbox"
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" "$RUNTIME" "$RUNTIME/tools"
# С114: fingerprints of the employees' model logins (8 hex of a hash of the account ID, never a token), one file per
# employee written by its own account. Sticky folder: nobody removes or replaces another's file; a reader trusts a file
# only when its owner is the account of the same name.
install -d -m 1777 -o root -g root /var/lib/vallis-agents /var/lib/vallis-agents/login-prints
chmod 0755 /var/lib/vallis-agents
# Only what is missing; age comes with the credentials store from its pinned release (tools.yml), not from the system.
need=(); for pkg in git python3 python3-yaml bubblewrap curl xz-utils sudo ca-certificates; do
  dpkg -s "$pkg" >/dev/null 2>&1 || need+=("$pkg"); done
if [ ${#need[@]} -gt 0 ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-upgrade "${need[@]}" >/dev/null
fi

if [ ! -x "$RUNTIME/node/bin/node" ]; then
  tmp=$(mktemp -d)
  curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" -o "$tmp/node.tar.xz"
  install -d -o "$USER_NAME" -g "$USER_NAME" "$RUNTIME/node"
  tar -xJf "$tmp/node.tar.xz" -C "$RUNTIME/node" --strip-components=1
  chown -R "$USER_NAME:$USER_NAME" "$RUNTIME/node"; rm -rf "$tmp"
fi
sudo -u "$USER_NAME" env HOME="$HOME_DIR" PATH="$RUNTIME/node/bin:/usr/bin:/bin" \
  npm install --prefix "$RUNTIME/tools" --no-audit --no-fund "bb-app@$BB_VERSION" "$AGENT" >/dev/null

cat > "/etc/systemd/system/$USER_NAME-bb.service" <<UNIT
[Unit]
Description=BB of the digital employee $USER_NAME
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$HOME_DIR
Environment=HOME=$HOME_DIR
Environment=PATH=$RUNTIME/node/bin:$RUNTIME/tools/node_modules/.bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$RUNTIME/tools/node_modules/.bin/bb-app --bundled --no-in-app-updates --data-dir $DATA --server-bind-host 127.0.0.1 --server-port $PORT --host-daemon-port $((PORT + 1))
UMask=0077
NoNewPrivileges=yes
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now "$USER_NAME-bb.service"
echo "BB of $USER_NAME runs on 127.0.0.1:$PORT. Data: $DATA. Received files: $HOME_DIR/telegram-inbox."
echo "Next, as $USER_NAME: log the agent in, then run setup.py --kind employee --workspace $HOME_DIR/workspace/<name> --inbox $HOME_DIR/telegram-inbox"
