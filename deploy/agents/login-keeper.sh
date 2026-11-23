#!/bin/bash
# Выкладка общего входа в модель для агентов (под root, по таймеру и при каждом изменении файла входа).
#
# Долгий ключ входа (refresh token) одноразовый и существует в одном месте — в штатном файле входа учётки, под которой
# CLI авторизован (на основном сервере — учётка владельца или отдельная учётка BB агентов). Обновляет вход только
# штатный CLI этой учётки. Здесь своего кода обновления нет: только копия файла без долгого ключа, только на чтение
# группе агентов. Если ключ доступа скоро истечёт, запускаем штатный CLI той же учётки коротким вызовом —
# он обновит вход сам, под своей блокировкой, — и выкладываем свежую копию.
#
#   login-keeper.sh publish   — выложить копии (Codex и Claude, если исходный файл есть)
#   login-keeper.sh refresh   — освежить при необходимости, затем выложить
#   login-keeper.sh status    — сроки, без ключей
# Настройки — /etc/bb-agents/login.env:
#   LOGIN_USER=root                     учётка, под которой авторизованы штатные CLI
#   CODEX_SOURCE=/root/.codex/auth.json
#   CLAUDE_SOURCE=/root/.claude/.credentials.json
#   CODEX_MIN_HOURS=24  CLAUDE_MIN_MINUTES=60
set -euo pipefail
. /etc/bb-agents/agents.env 2>/dev/null || true
. /etc/bb-agents/login.env 2>/dev/null || true
LOGIN_USER=${LOGIN_USER:-root}
CODEX_SOURCE=${CODEX_SOURCE:-}; CLAUDE_SOURCE=${CLAUDE_SOURCE:-}
CODEX_MIN_HOURS=${CODEX_MIN_HOURS:-24}; CLAUDE_MIN_MINUTES=${CLAUDE_MIN_MINUTES:-60}
out=/var/lib/bb-agents/login
lib=$(dirname "$(readlink -f "$0")")

# Сколько минут осталось до истечения ключа доступа (по самому файлу; ключи не печатаются).
minutes_left() { python3 - "$1" "$2" <<'EOF'
import json,sys,base64,time
kind,p=sys.argv[1],sys.argv[2]; d=json.load(open(p))
if kind=="codex":
    tok=d["tokens"]["access_token"].split(".")[1]; tok+="="*(-len(tok)%4)
    exp=json.loads(base64.urlsafe_b64decode(tok))["exp"]
else:
    exp=d["claudeAiOauth"]["expiresAt"]/1000
print(int((exp-time.time())//60))
EOF
}

publish_one() {  # kind source target
  [ -n "$2" ] && [ -r "$2" ] || return 0
  install -d -o root -g bb-agents-login -m 750 "$out" "$(dirname "$3")"
  tmp=$(mktemp "$(dirname "$3")/.login-XXXXXX")
  python3 - "$1" "$2" "$tmp" <<'EOF'
import json,sys
kind,src,dst=sys.argv[1:4]; d=json.load(open(src))
if kind=="codex":
    d.get("tokens",{})["refresh_token"]=""              # долгий ключ агентам не выдаётся
else:
    o=d.get("claudeAiOauth",{}); o["refreshToken"]=""; o.pop("refreshTokenExpiresAt",None)
    d={"claudeAiOauth":o}                                 # входы владельца в подключения (mcpOAuth) не выдаются
json.dump(d,open(dst,"w"),indent=2)
EOF
  chown root:bb-agents-login "$tmp"; chmod 440 "$tmp"; mv -f "$tmp" "$3"
}

publish() {
  publish_one codex "$CODEX_SOURCE" "$out/codex/auth.json"
  publish_one claude "$CLAUDE_SOURCE" "$out/claude/.credentials.json"
}

refresh() {
  if [ -n "$CODEX_SOURCE" ] && [ -r "$CODEX_SOURCE" ] && [ "$(minutes_left codex "$CODEX_SOURCE")" -lt $((CODEX_MIN_HOURS*60)) ]; then
    # Штатное обновление Codex: app-server, запрос account/read с refreshToken=true (своего кода обновления нет).
    sudo -u "$LOGIN_USER" -H env CODEX_HOME="$(dirname "$CODEX_SOURCE")" PATH="$PATH" \
      "${NODE_BIN:-node}" "$lib/codex-refresh.mjs" "${CODEX_BIN:-codex}" || echo "Codex: вход не обновлён" >&2
  fi
  if [ -n "$CLAUDE_SOURCE" ] && [ -r "$CLAUDE_SOURCE" ] && [ "$(minutes_left claude "$CLAUDE_SOURCE")" -lt "$CLAUDE_MIN_MINUTES" ]; then
    # Штатный Claude Code той же учётки сам обновляет вход перед запросом.
    sudo -u "$LOGIN_USER" -H "${CLAUDE_BIN:-claude}" -p "ок" --max-turns 1 >/dev/null 2>&1 || echo "Claude: вход не обновлён" >&2
  fi
  publish
}

case "${1:-status}" in
  publish) publish;;
  refresh) refresh;;
  status)
    for pair in "codex:$CODEX_SOURCE:$out/codex/auth.json" "claude:$CLAUDE_SOURCE:$out/claude/.credentials.json"; do
      IFS=: read -r kind src dst <<< "$pair"
      [ -n "$src" ] && [ -r "$src" ] || continue
      echo "$kind: до истечения ключа доступа $(minutes_left "$kind" "$src") мин; копия агентам: $( [ -r "$dst" ] && echo "$(minutes_left "$kind" "$dst") мин" || echo нет)"
    done;;
  *) echo "publish | refresh | status" >&2; exit 64;;
esac
