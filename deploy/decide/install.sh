#!/bin/bash
# Quick decision service of the platform (one per server): `vallis-decide` for agents, the bridge and plugins.
#
#   install.sh [--organization <organization.yml>] [--providers openai-decisions,claude,codex] [--port 9014]
#   install.sh status | remove [--purge]
#
# Providers come from the organization file (section decide: {providers: [...]}), the option overrides it; default
# openai-decisions. Each provider needs a credential of this service itself, never anyone else's:
#   openai-decisions  API key:  install -m 600 -o bb-decide <key file> /var/lib/bb-decide/openai.key
#   claude            own login: sudo -u bb-decide -H claude   (log in once)
#   codex             own login: sudo -u bb-decide -H codex login --device-auth
# Without any of them the state is "needs_setup" and consumers keep their default (the bridge keeps 👀).
# State for `setup.py tools status`: /var/lib/bb-decide/state.json. The service never stores or logs request text.
set -uo pipefail
export LC_ALL=C.UTF-8
here=$(cd "$(dirname "$0")" && pwd)
opt=/opt/bb-decide; home=/var/lib/bb-decide; conf_dir=/etc/bb-decide; units=/etc/systemd/system; user=bb-decide
providers=""; port=9014; organization=""; action=install; purge=0
case "${1:-}" in status|remove) action=$1; shift;; esac
while [ $# -gt 0 ]; do
  case "$1" in
    --organization) organization=$2; shift 2;;
    --providers) providers=$2; shift 2;;
    --port) port=$2; shift 2;;
    --purge) purge=1; shift;;
    *) echo "unknown option $1" >&2; exit 64;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 77; }

if [ "$action" = status ]; then cat "$home/state.json" 2>/dev/null || echo '{"state":"needs_setup","reason":"not installed"}'; exit 0; fi
if [ "$action" = remove ]; then
  systemctl disable -q --now bb-decide.socket 2>/dev/null; systemctl stop bb-decide.service 2>/dev/null
  rm -f $units/bb-decide.{socket,service} /usr/local/bin/vallis-decide; systemctl daemon-reload
  rm -rf "$opt"; [ "$purge" = 1 ] && { rm -rf "$home" "$conf_dir"; userdel "$user" 2>/dev/null; }
  echo "decide service removed"; exit 0
fi

if [ -n "$organization" ] && [ -z "$providers" ]; then
  providers=$(python3 - "$organization" <<'EOF'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
try:
    import yaml
    d = (yaml.safe_load(text) or {}).get("decide") or {}
    print(",".join(d.get("providers") or []))
except ImportError:
    m = re.search(r"^decide:\s*\n(?:[ \t]+.*\n?)*?[ \t]+providers:\s*\[([^\]]*)\]", text, re.M)
    print(",".join(x.strip() for x in m.group(1).split(",")) if m else "")
EOF
)
fi
providers=${providers:-openai-decisions}
for p in ${providers//,/ }; do case "$p" in openai-decisions|claude|codex) ;; *) echo "unknown provider $p" >&2; exit 64;; esac; done
[[ $port =~ ^[0-9]+$ ]] || { echo "port: a number" >&2; exit 64; }

id "$user" >/dev/null 2>&1 || useradd --system --home-dir "$home" --shell /usr/sbin/nologin "$user"
install -d -m 750 -o "$user" -g "$user" "$home"
install -d -m 755 "$opt" "$conf_dir"
install -m 755 "$here/server.py" "$opt/server.py"
install -m 755 "$here/vallis-decide" "$opt/vallis-decide"; ln -sfn "$opt/vallis-decide" /usr/local/bin/vallis-decide
# CLI providers run under the service's own account: their binaries and node must be on the unit's PATH.
tools_path=/usr/local/bin:/usr/bin:/bin
for b in node codex claude; do d=$(dirname "$(command -v $b 2>/dev/null || echo /usr/bin/x)"); case ":$tools_path:" in *":$d:"*) ;; *) tools_path=$d:$tools_path;; esac; done
cat > "$conf_dir/decide.env" <<EOF
# Quick decision service settings (deploy/decide/install.sh). Providers in cascade order.
DECIDE_PROVIDERS=$providers
DECIDE_PORT=$port
EOF
chmod 644 "$conf_dir/decide.env"

cat > "$units/bb-decide.socket" <<EOF
[Unit]
Description=Quick decision service (loopback socket)

[Socket]
ListenStream=127.0.0.1:$port
Accept=no
Backlog=32

[Install]
WantedBy=sockets.target
EOF
cat > "$units/bb-decide.service" <<EOF
[Unit]
Description=Quick decision service (starts on request, exits when idle)
Requires=bb-decide.socket
After=bb-decide.socket

[Service]
Type=simple
User=$user
Group=$user
ExecStart=/usr/bin/python3 $opt/server.py
EnvironmentFile=$conf_dir/decide.env
Environment=HOME=$home
Environment=PATH=$tools_path
Environment=IDLE_TIMEOUT_SEC=300
Environment=PYTHONUNBUFFERED=1
UMask=0022
MemoryMax=1G
CPUQuota=100%
Nice=5
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=$home
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
EOF
systemctl daemon-reload
systemctl enable -q --now bb-decide.socket || echo "[decide] socket did not start" >&2
systemctl --no-block try-restart bb-decide.service 2>/dev/null
# The service writes its own state (which provider answers, or what is missing).
sleep 1; /usr/local/bin/vallis-decide --status >/dev/null 2>&1 || true
cat "$home/state.json" 2>/dev/null || echo '{"state":"error","reason":"the service did not answer"}'
echo
exit 0
