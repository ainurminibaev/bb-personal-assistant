#!/bin/bash
# Quick decision service of the platform (one per server): `vallis-decide` for agents, the bridge and plugins.
#
#   install.sh [--organization <organization.yml>] [--providers openai-decisions,claude,codex]
#              [--openai-consumers <account,...>] [--openai-key-from <file|->]
#   install.sh status | remove [--purge]
#   install.sh add-consumer <account>   — one more account may use the OpenAI key (a new employee on this server);
#                                         the list keeps the others, the service restarts. Without it vallis-decide
#                                         answers «not allowed for this account» and the bridge picks no reactions.
#
# Providers come from the organization file (section decide: {providers: [...]}), the option overrides it; default
# openai-decisions. Each provider needs a credential of this service itself, never anyone else's:
#   openai-decisions  API key: --openai-key-from <file> or - for stdin (a bare key or OPENAI_API_KEY=…; never printed), used only for
#                     the accounts in --openai-consumers and only for POST https://api.openai.com/v1/decisions
#   claude            own login: sudo -u bb-decide -H claude   (log in once)
#   codex             own login: sudo -u bb-decide -H codex login --device-auth
# Without any of them the state is "needs_setup" and consumers keep their default (the bridge keeps 👀).
# State for `setup.py tools status`: /var/lib/bb-decide/state.json. The service never stores or logs request text.
set -uo pipefail
export LC_ALL=C.UTF-8
here=$(cd "$(dirname "$0")" && pwd)
opt=/opt/bb-decide; home=/var/lib/bb-decide; conf_dir=/etc/bb-decide; units=/etc/systemd/system; user=bb-decide
providers=""; organization=""; action=install; purge=0; consumers=""; key_from=""; sock=/run/bb-decide.sock
case "${1:-}" in status|remove) action=$1; shift;; add-consumer) action=add; consumers=${2:-}; shift; [ $# -gt 0 ] && shift;; esac
while [ $# -gt 0 ]; do
  case "$1" in
    --organization) organization=$2; shift 2;;
    --providers) providers=$2; shift 2;;
    --openai-consumers) consumers=$2; shift 2;;
    --openai-key-from) key_from=$2; shift 2;;
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

# The accounts already allowed stay: a new install or a new employee adds to the list, never replaces it.
old_consumers=$(sed -n 's/^DECIDE_OPENAI_CONSUMERS=//p' "$conf_dir/decide.env" 2>/dev/null)
merge() { printf '%s\n' ${1//,/ } ${2//,/ } | awk 'NF && !seen[$0]++' | paste -sd, -; }
if [ "$action" = add ]; then
  [ -n "$consumers" ] || { echo "usage: install.sh add-consumer <account>" >&2; exit 64; }
  id "$consumers" >/dev/null 2>&1 || { echo "no account $consumers" >&2; exit 64; }
  [ -f "$conf_dir/decide.env" ] || { echo "decide service is not installed: run install.sh first" >&2; exit 69; }
  all=$(merge "$old_consumers" "$consumers")
  cp -p "$conf_dir/decide.env" "$conf_dir/decide.env.bak-$(date -u +%Y%m%d%H%M%S)"
  sed -i "s/^DECIDE_OPENAI_CONSUMERS=.*/DECIDE_OPENAI_CONSUMERS=$all/" "$conf_dir/decide.env"
  systemctl restart bb-decide.service 2>/dev/null || true
  echo "[decide] OpenAI key allowed for: $all"; exit 0
fi
consumers=$(merge "$old_consumers" "$consumers")

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
for u in ${consumers//,/ }; do id "$u" >/dev/null 2>&1 || { echo "no account $u" >&2; exit 64; }; done

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
DECIDE_SOCKET=$sock
# Accounts allowed to use the OpenAI key (the caller's account comes from the kernel).
DECIDE_OPENAI_CONSUMERS=$consumers
# Our own spending limit for the key: by input tokens per month (USD at the price below) and by calls per day.
DECIDE_OPENAI_MONTH_USD=${DECIDE_OPENAI_MONTH_USD:-10}
DECIDE_OPENAI_PRICE_PER_MTOK=${DECIDE_OPENAI_PRICE_PER_MTOK:-0.10}
DECIDE_OPENAI_DAY_CALLS=${DECIDE_OPENAI_DAY_CALLS:-5000}
EOF
chmod 644 "$conf_dir/decide.env"

cat > "$units/bb-decide.socket" <<EOF
[Unit]
Description=Quick decision service (local socket)

[Socket]
ListenStream=$sock
SocketMode=0666
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
# The key: moved straight from the given file into the service, never printed or copied elsewhere.
if [ -n "$key_from" ]; then
  # `-` reads the key from stdin straight into the service's file: moving it from another server's service needs no
  # copy on disk in between (ssh <server> 'sudo cat /var/lib/bb-decide/openai.key' | sudo install.sh --openai-key-from -).
  [ "$key_from" = - ] || [ -s "$key_from" ] || { echo "no key in $key_from" >&2; exit 66; }
  src=$key_from; [ "$key_from" = - ] && src=/dev/stdin
  install -m 600 -o "$user" -g "$user" /dev/null "$home/openai.key.tmp" && cat "$src" > "$home/openai.key.tmp" \
    && [ -s "$home/openai.key.tmp" ] || { rm -f "$home/openai.key.tmp"; echo "no key given" >&2; exit 66; }
  mv "$home/openai.key.tmp" "$home/openai.key" && echo "[decide] OpenAI key installed for: ${consumers:-nobody}"
fi
systemctl daemon-reload
systemctl enable -q --now bb-decide.socket || echo "[decide] socket did not start" >&2
systemctl --no-block try-restart bb-decide.service 2>/dev/null
# The service writes its own state (which provider answers, or what is missing).
sleep 1; /usr/local/bin/vallis-decide --status >/dev/null 2>&1 || true
cat "$home/state.json" 2>/dev/null || echo '{"state":"error","reason":"the service did not answer"}'
echo
exit 0
