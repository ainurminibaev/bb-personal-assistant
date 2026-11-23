#!/bin/bash
# Агент под своей учётной записью Linux на сервере с общим BB.
#
#   install-agent.sh install <учётка> --bb-user <учётка BB> [--allow-port N]...
#   install-agent.sh remove  <учётка> --bb-user <учётка BB> [--purge]
#
# Служба BB должна запускаться с /usr/local/lib/bb-agents/shim первым в PATH: тогда codex и claude
# в папках агентов идут под их учётками (dispatch), а остальные треды — как раньше.
# Что ставит (повторный запуск ничего не ломает):
#   - учётку агента с домашней папкой 750 и группу bbws-<агент> (агент + учётка BB) для общих папок:
#     ~/workspace (рабочие папки проектов), ~/.bb-runtime (навыки хода), ~/.bb-thread-storage, ~/inbox (входящие файлы);
#   - вход в модель: ~/.codex/auth.json и ~/.claude/.credentials.json — ссылки на общий вход машины
#     (/var/lib/bb-agents/login, группа bb-agents-login), вход выполняется один раз владельцем;
#   - обёртку запуска /usr/local/lib/bb-agents/<агент>/{claude,codex,bin/codex,bin/bb};
#   - правило sudoers: учётка BB запускает только agent-exec и только от имени агента;
#   - сетевой экран: агенту на loopback открыты только разрешённые порты (firewall.sh);
#   - общие инструменты платформы (tools.yml): плагины в BB агентов, context-mode, навыки и хуки учётке агента.
# Ничего чужого не меняет и не сбрасывает.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
action=${1:?install|remove}; agent=${2:?учётка агента}; shift 2
bbuser=""; purge=0; ports=(); owner_tg=""; chats=(); projects=(); bbcli=""; bburl=""; workspace=""; bbenv=()
while [ $# -gt 0 ]; do
  case "$1" in
    --bb-user) bbuser=$2; shift 2;;
    --allow-port) ports+=("$2"); shift 2;;
    --purge) purge=1; shift;;
    --owner-telegram) owner_tg=$2; shift 2;;
    --chat) chats+=("$2"); shift 2;;
    --project) projects+=("$2"); shift 2;;
    --bb-cli) bbcli=$2; shift 2;;
    --bb-url) bburl=$2; shift 2;;
    --bb-env) bbenv+=("$2"); shift 2;;   # переменные для команды bb учётки BB, например PATH=… или BB_DATA_DIR=…
    --workspace) workspace=$2; shift 2;;
    *) echo "неизвестный параметр $1" >&2; exit 64;;
  esac
done
[ -n "$bbuser" ] || [ "$action" = migrate-claude ] || { echo "нужен --bb-user" >&2; exit 64; }
[[ $agent =~ ^[a-z][a-z0-9-]{1,30}$ ]] || { echo "имя учётки: латиница, цифры, дефис" >&2; exit 64; }
[ "$(id -u)" = 0 ] || { echo "запускать от root" >&2; exit 77; }

lib=/usr/local/lib/bb-agents; login=/var/lib/bb-agents/login; conf=/etc/bb-agents
group=bbws-$agent

if [ "$action" = migrate-claude ]; then
  # Перевод агента из формата Claude Code в общий (как навык совместимости), под учёткой агента:
  # правила → AGENTS.md (CLAUDE.md одной строкой), личные навыки → ~/.agents/skills со ссылками для обоих CLI,
  # память Claude — ссылкой и правилом в AGENTS.md; хук автоответа в Telegram снимается (под BB ответ доставляет мост).
  [ -n "$workspace" ] || { echo "нужен --workspace <папка правил агента>" >&2; exit 64; }
  sudo -u "$agent" -H bash "$here/migrate-claude.sh" "$workspace"
  exit 0
fi

if [ "$action" = remove ]; then
  rm -f /etc/sudoers.d/bb-agent-$agent
  rm -rf "$lib/${agent:?}"
  "$lib/firewall.sh" remove-agent "$agent" 2>/dev/null || "$here/firewall.sh" remove-agent "$agent" || true
  if [ $purge = 1 ]; then
    pkill -u "$agent" 2>/dev/null || true; sleep 1
    userdel -r "$agent" 2>/dev/null || true
    groupdel "$group" 2>/dev/null || true
  fi
  echo "агент $agent снят${purge:+ }$( [ $purge = 1 ] && echo '(учётка и папка удалены)')"
  exit 0
fi

# Учётка, группа общих папок, вход
getent group bb-agents-login >/dev/null || groupadd --system bb-agents-login
getent group "$group" >/dev/null || groupadd "$group"
id "$agent" >/dev/null 2>&1 || useradd -m -s /bin/bash "$agent"
usermod -aG "$group",bb-agents-login "$agent"
newgrp_bb=0; id -nG "$bbuser" | tr ' ' '\n' | grep -qx "$group" || newgrp_bb=1
usermod -aG "$group" "$bbuser"
home=$(getent passwd "$agent" | cut -d: -f6)
chmod 750 "$home"; chgrp "$group" "$home"
for d in workspace .bb-runtime .bb-thread-storage inbox; do
  install -d -o "$agent" -g "$group" -m 2770 "$home/$d"
  # Наследование: всё, что агент и BB создают внутри, остаётся в группе агента (setgid на каталогах,
  # права группы — umask 007 хода); если есть пакет acl — ещё и ACL по умолчанию (переживает chmod и cp).
  chgrp -R "$group" "$home/$d"; chmod -R g+rwX "$home/$d"; find "$home/$d" -type d -exec chmod g+s {} +
  if command -v setfacl >/dev/null; then
    setfacl -R -m "g:$group:rwX" "$home/$d"; setfacl -R -d -m "g:$group:rwX" "$home/$d"
  fi
done
install -d -o root -g bb-agents-login -m 750 "$login" "$login/codex" "$login/claude"   # агентам только чтение (login-keeper.sh)
install -d -o "$agent" -g "$agent" -m 700 "$home/.codex" "$home/.claude"
ln -sfn "$login/codex/auth.json" "$home/.codex/auth.json"
ln -sfn "$login/claude/.credentials.json" "$home/.claude/.credentials.json"
chown -h "$agent:$agent" "$home/.codex/auth.json" "$home/.claude/.credentials.json"

# Обёртка запуска
install -d -m 755 "$lib" "$lib/$agent" "$lib/$agent/bin"
install -m 755 "$here/agent-exec" "$lib/agent-exec"
install -m 755 "$here/runner" "$lib/runner"
install -m 755 "$here/firewall.sh" "$lib/firewall.sh"
install -m 755 "$here/codex-proxy.mjs" "$lib/codex-proxy.mjs"
install -m 755 "$here/login-keeper.sh" "$lib/login-keeper.sh"
install -m 755 "$here/codex-refresh.mjs" "$lib/codex-refresh.mjs"
# Выкладка входа агентам (обновление штатным CLI — только при LOGIN_REFRESH=1 в login.env): при каждом изменении штатного файла входа и раз в 30 минут (login-keeper.sh).
. "$conf/login.env" 2>/dev/null || true
if [ ! -f /etc/systemd/system/bb-agents-login.service ]; then
  printf '[Unit]\nDescription=Выкладка входа в модель агентам\n[Service]\nType=oneshot\nExecStart=%s/login-keeper.sh %s\n' "$lib" "$( [ "${LOGIN_REFRESH:-0}" = 1 ] && echo refresh || echo publish )" > /etc/systemd/system/bb-agents-login.service
  printf '[Unit]\nDescription=Выкладка входа агентам раз в 30 минут\n[Timer]\nOnBootSec=1min\nOnUnitActiveSec=30min\n[Install]\nWantedBy=timers.target\n' > /etc/systemd/system/bb-agents-login.timer
  printf '[Unit]\nDescription=Выкладка входа агентам при изменении файла входа\n[Path]\n%b%b[Install]\nWantedBy=multi-user.target\n' \
    "${CODEX_SOURCE:+PathChanged=$CODEX_SOURCE\n}" "${CLAUDE_SOURCE:+PathChanged=$CLAUDE_SOURCE\n}" > /etc/systemd/system/bb-agents-login.path
  systemctl daemon-reload; systemctl enable --now bb-agents-login.timer bb-agents-login.path >/dev/null 2>&1 || true
fi
install -d -m 755 "$lib/shim"
install -m 755 "$here/dispatch" "$lib/shim/codex"; install -m 755 "$here/dispatch" "$lib/shim/claude"
# Пути к общим программам сервера (настоящие CLI) — один файл root.
install -d -m 755 "$conf"
[ -f "$conf/agents.env" ] || printf 'CLAUDE_BIN=%s\nCODEX_BIN=%s\nTOOLS_PATH=%s\n' \
  "$(command -v claude || echo /usr/local/bin/claude)" "$(command -v codex || echo /usr/local/bin/codex)" "/usr/local/bin" > "$conf/agents.env"
chmod 644 "$conf/agents.env"
for kind in claude codex; do
  printf '#!/bin/sh\nexec %s/runner %s %s "$@"\n' "$lib" "$kind" "$agent" > "$lib/$agent/$kind"
  chmod 755 "$lib/$agent/$kind"
done
rm -f "$lib/$agent/bin/codex"   # агенту — обычный codex; подмена только в PATH службы BB (shim)
printf '#!/bin/sh\necho "Команда bb здесь недоступна: используйте инструмент project_command с теми же аргументами, например [\\"thread\\",\\"spawn\\",...]." >&2\nexit 2\n' > "$lib/$agent/bin/bb"
chmod 755 "$lib/$agent/bin/bb"

# sudoers: только agent-exec, только от имени агента, с передачей переменных хода
tmp=$(mktemp)
printf '%s ALL=(%s) NOPASSWD:SETENV: %s/agent-exec\n' "$bbuser" "$agent" "$lib" > "$tmp"
visudo -cf "$tmp" >/dev/null
install -m 440 "$tmp" /etc/sudoers.d/bb-agent-$agent; rm -f "$tmp"

# Экран
args=(); for p in "${ports[@]}"; do args+=(--allow-port "$p"); done
"$lib/firewall.sh" add-agent "$agent" "${args[@]}"
# Запись агента в настройку плагина project-command: его проекты, владелец и чаты в Telegram.
if [ -n "$bbcli" ] && [ -n "$bburl" ]; then
  bbrun() { (cd /tmp && sudo -u "$bbuser" -H env BB_SERVER_URL="$bburl" "${bbenv[@]}" "$bbcli" "$@"); }
  cur=$(bbrun plugin config project-command 2>/dev/null | sed -n 's/^agents = "\(.*\)"  (string)$/\1/p' | sed 's/\\"/"/g' || true)
  new=$(python3 "$here/register-agent.py" "${cur:-[]}" "$agent" "$home" "$owner_tg" "${projects[*]:-}" "${chats[*]:-}")
  bbrun plugin config project-command set agents "$new" >/dev/null
  echo "настройка плагина: $new"
fi
# Общие инструменты платформы (tools.yml): плагины — в BB агентов, остальное — учётке агента.
# Ничего не пропускается молча: в конце — состояние каждой строки списка (то же: tools.py status).
toolargs=(--kind agents --account "$agent")
if [ -n "$bbcli" ] && [ -n "$bburl" ]; then
  toolargs+=(--bb-user "$bbuser" --bb-cli "$bbcli" --bb-url "$bburl")
  for e in "${bbenv[@]}"; do toolargs+=(--bb-env "$e"); done
fi
python3 "$here/../../tools.py" install "${toolargs[@]}" || echo "инструменты: установка с ошибками, см. список выше" >&2
echo "агент $agent готов: дом $home, группа $group, обёртка $lib/$agent"
[ "$newgrp_bb" = 1 ] && echo "ВНИМАНИЕ: $bbuser добавлен в группу $group — перезапустите службу BB агентов, иначе она не увидит папку агента"
