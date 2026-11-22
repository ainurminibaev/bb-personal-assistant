#!/bin/bash
# Local Telegram Bot API server: one per machine, shared by every bot on it (large files, up to 2 GB).
# Run as root.
#
#   telegram-local.sh install --credentials FILE            set up the shared server, or reuse the running one
#   telegram-local.sh switch  --bb-user USER --bb-url URL --bb-data DIR [--to local|cloud] [--restart-unit UNIT] [--bb-path PATH]
#                                                           move one bot's bridge to the local server or back
#
# FILE holds TELEGRAM_API_ID and TELEGRAM_API_HASH of a Telegram application (my.telegram.org). It is copied
# to /var/lib/telegram-api/credentials.env (600, root) and never printed.
# Without this server a bridge works through api.telegram.org with the usual file size limits.
set -euo pipefail

NAME=vallis-telegram-api
IMAGE=aiogram/telegram-bot-api@sha256:f31a6bc348b3e0e94eb40fe83b3d147bcdd37897900b3896f9667bb3d026e661
ROOT=/var/lib/telegram-api
PORT=${PORT:-8083}
URL=http://127.0.0.1:$PORT
SERVICE_USER=telegram-api

cmd=${1:-}; shift || true
declare -A opt
while [ $# -gt 0 ]; do opt[${1#--}]=${2:-}; shift 2 || break; done

need() { [ -n "${opt[$1]:-}" ] || { echo "missing --$1" >&2; exit 2; }; }

running() { command -v docker >/dev/null && [ "$(docker inspect -f '{{.State.Running}}' $NAME 2>/dev/null)" = true ]; }

install_server() {
  if running; then
    echo "Local Telegram server already runs on this machine: $URL (reused)."
    return
  fi
  need credentials
  [ -f "${opt[credentials]}" ] || { echo "no credentials file" >&2; exit 2; }
  grep -q '^TELEGRAM_API_ID=' "${opt[credentials]}" && grep -q '^TELEGRAM_API_HASH=' "${opt[credentials]}" ||
    { echo "credentials file needs TELEGRAM_API_ID and TELEGRAM_API_HASH" >&2; exit 2; }
  if ! command -v docker >/dev/null; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io >/dev/null
    systemctl enable --now docker >/dev/null
  fi
  getent group $SERVICE_USER >/dev/null || groupadd --system $SERVICE_USER
  id $SERVICE_USER >/dev/null 2>&1 || useradd --system --gid $SERVICE_USER --home-dir $ROOT --shell /usr/sbin/nologin $SERVICE_USER
  install -d -m 0750 -o $SERVICE_USER -g $SERVICE_USER $ROOT $ROOT/data $ROOT/temp
  install -m 0600 -o root -g root "${opt[credentials]}" $ROOT/credentials.env
  docker rm -f $NAME >/dev/null 2>&1 || true
  # The server runs under its own account; each bot's BB reads its files through the group membership.
  docker run -d --name $NAME --restart unless-stopped \
    --user "$(id -u $SERVICE_USER):$(id -g $SERVICE_USER)" \
    --env-file $ROOT/credentials.env \
    -p 127.0.0.1:$PORT:8081 -v $ROOT/data:$ROOT/data -v $ROOT/temp:$ROOT/temp \
    --memory 1g --cpus 1 --log-opt max-size=10m --log-opt max-file=2 \
    --entrypoint telegram-bot-api $IMAGE --local --dir=$ROOT/data --temp-dir=$ROOT/temp --http-port=8081 >/dev/null
  for _ in $(seq 1 30); do curl -s -o /dev/null "$URL" && break; sleep 1; done
  running || { echo "local Telegram server did not start" >&2; exit 1; }
  echo "Local Telegram server runs: $URL, data $ROOT/data."
}

bb_as() { cd / && sudo -u "${opt[bb-user]}" env HOME="$(getent passwd "${opt[bb-user]}" | cut -d: -f6)" \
  BB_DATA_DIR="${opt[bb-data]}" BB_SERVER_URL="${opt[bb-url]}" PATH="${opt[bb-path]:-$PATH}" bb "$@"; }
setting() { bb_as plugin config telegram-projects set "$1" "$2" >/dev/null || { echo "could not set $1" >&2; exit 1; }; }
current() { bb_as plugin config telegram-projects 2>/dev/null | sed -n "s/^$1 = \"\{0,1\}\([^\" ]*\)\"\{0,1\} .*/\1/p" | head -1; }

switch_bot() {
  need bb-user; need bb-url; need bb-data
  local to=${opt[to]:-local}
  # Global, not local: the EXIT trap runs after this function has returned.
  WAS_ENABLED=$(current enabled); WAS_CHAT=$(current chatEnabled)
  # Receivers stop while the bot changes servers; they come back as they were.
  setting enabled false; setting chatEnabled false
  trap 'setting enabled "$WAS_ENABLED"; setting chatEnabled "$WAS_CHAT"' EXIT
  if [ "$to" = local ]; then
    running || { echo "run: telegram-local.sh install first" >&2; exit 1; }
    usermod -aG $SERVICE_USER "${opt[bb-user]}"
    local username; username=$(bb_as telegram-projects status --json | sed -n 's/.*"bot": *"\([^"]*\)".*/\1/p' | head -1)
    [ -n "$username" ] || { echo "bot username unknown: is the bridge configured?" >&2; exit 1; }
    local input; input=$(mktemp); chmod 644 "$input"; printf '{"expectedUsername":"%s","confirm":true}' "$username" >"$input"
    for _ in $(seq 1 30); do
      out=$(bb_as plugin rpc call telegram-projects prepareLocalApi --input-file "$input" --json 2>&1) && break
      echo "$out" | grep -q wait_for_receivers_to_stop || { rm -f "$input"; echo "$out" >&2; exit 1; }
      sleep 2
    done
    rm -f "$input"
    setting telegramApiRoot "$URL"; setting telegramLocalRoot "$ROOT/data"
    # Received files land on this machine: the bridge hands them only to threads running here.
    local host; host=$(bb_as machine list 2>/dev/null | awk '$2=="server"{for(i=1;i<=NF;i++) if($i ~ /^host_/) print $i}' | head -1)
    [ -n "$host" ] && setting telegramFileHostId "$host"
    echo "Bot @$username uses the local Telegram server."
    if [ -n "${opt[restart-unit]:-}" ]; then
      trap - EXIT; setting enabled "$WAS_ENABLED"; setting chatEnabled "$WAS_CHAT"
      systemctl restart "${opt[restart-unit]}"   # the BB process picks up the new group membership
      echo "Restarted ${opt[restart-unit]}."
    else
      echo "Restart this user's BB so it can read the received files (group $SERVICE_USER)."
    fi
  else
    # Back to api.telegram.org: log out of the local server; Telegram lets the bot in again after 10 minutes.
    local token; token=$(cat "${opt[bb-data]}/plugins/telegram-projects/secrets/botToken" 2>/dev/null || true)
    [ -n "$token" ] || { echo "bot token not found in the bridge secrets" >&2; exit 1; }
    curl -s -o /dev/null --max-time 30 "$URL/bot$token/logOut" || true
    unset token
    setting telegramApiRoot https://api.telegram.org; setting telegramLocalRoot ""
    echo "Bot moved back to api.telegram.org; Telegram accepts it there about 10 minutes after the logout."
  fi
}

case "$cmd" in
  install) install_server ;;
  switch) switch_bot ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
