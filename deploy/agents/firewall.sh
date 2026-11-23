#!/bin/bash
# Сетевой экран агентов: своя таблица nftables «inet bb_agents», чужие таблицы и правила не трогаются.
# Процессам учёток агентов на loopback открыты только разрешённые порты (служба голоса, картинок и т. п.);
# порт BB, BB владельца и случайные порты служб на этой машине им закрыты. Внешняя сеть не ограничивается.
#
#   firewall.sh add-agent <учётка> [--allow-port N]...   добавить учётку (и порты) и применить
#   firewall.sh add-untrusted <учётка>                    учётка (например, BB агентов), которой закрыт защищённый BB
#   firewall.sh protect <группа systemd>                  защищённый BB: все его сокеты, включая случайные порты
#                                                          (например system.slice/bb-host-daemon-….service)
#   firewall.sh remove-agent <учётка>                     убрать учётку и применить
#   firewall.sh remove-port <N>                           закрыть агентам разрешённый порт и применить
#   firewall.sh apply | remove | status
# Состояние: /etc/bb-agents/firewall.conf (строки «agent <учётка>», «port <N>», «untrusted <учётка>», «protect <группа>»).
# Защищённый BB: новое соединение учётки агента или BB агентов (не ответ) помечается на выходе и отклоняется на входе, если сокет
# назначения принадлежит группе systemd защищённого BB, — так закрыты и его случайные порты.
# После перезагрузки таблицу восстанавливает служба bb-agents-firewall.service.
set -euo pipefail
conf_dir=/etc/bb-agents; conf=$conf_dir/firewall.conf; rules=$conf_dir/firewall.nft
unit=/etc/systemd/system/bb-agents-firewall.service
self=$(readlink -f "$0")

need_root() { [ "$(id -u)" = 0 ] || { echo "запускать от root" >&2; exit 77; }; }

render() {
  local uids=() ports=() untrusted=() protect=()
  while read -r kind value; do
    case "$kind" in
      agent) id "$value" >/dev/null 2>&1 && uids+=("$(id -u "$value")");;
      port) ports+=("$value");;
      untrusted) id "$value" >/dev/null 2>&1 && untrusted+=("$(id -u "$value")");;
      protect) protect+=("$value");;
    esac
  done < <(grep -E '^(agent|port|untrusted|protect) ' "$conf" 2>/dev/null || true)
  local marked=("${uids[@]}" "${untrusted[@]}")
  {
    echo "table inet bb_agents {"
    echo "  set agent_uids {"; echo "    type uid"
    [ ${#uids[@]} -gt 0 ] && echo "    elements = { $(IFS=,; echo "${uids[*]}") }"
    echo "  }"
    echo "  set allowed_ports {"; echo "    type inet_service"
    [ ${#ports[@]} -gt 0 ] && echo "    elements = { $(IFS=,; echo "${ports[*]}") }"
    echo "  }"
    echo "  set marked_uids {"; echo "    type uid"
    [ ${#marked[@]} -gt 0 ] && echo "    elements = { $(IFS=,; echo "${marked[*]}") }"
    echo "  }"
    echo "  chain output {"
    echo "    type filter hook output priority 0; policy accept;"
    echo "    oifname \"lo\" meta skuid @agent_uids tcp dport != @allowed_ports reject with tcp reset"
    echo "    oifname \"lo\" meta skuid @agent_uids udp dport != @allowed_ports udp dport != 53 reject"
    echo "    oifname \"lo\" meta skuid @marked_uids ct state new meta mark set 0x62626167"
    echo "  }"
    if [ ${#protect[@]} -gt 0 ]; then
      echo "  chain input {"
      echo "    type filter hook input priority 0; policy accept;"
      for g in "${protect[@]}"; do
        lvl=$(awk -F/ '{print NF}' <<< "$g")
        echo "    iifname \"lo\" meta mark 0x62626167 socket cgroupv2 level $lvl \"$g\" reject with tcp reset"
      done
      echo "  }"
    fi
    echo "}"
  } > "$rules"
}

apply() {
  need_root; mkdir -p "$conf_dir"; touch "$conf"; render
  nft -c -f "$rules"
  nft list table inet bb_agents >/dev/null 2>&1 && nft delete table inet bb_agents
  nft -f "$rules"
  # BB_AGENTS_FW_NO_UNIT=1 — применить без службы восстановления (первое включение: сначала проверка, потом служба).
  if [ "${BB_AGENTS_FW_NO_UNIT:-0}" = 1 ]; then return 0; fi
  if [ ! -f "$unit" ]; then
    cat > "$unit" <<EOF
[Unit]
Description=Сетевой экран агентов BB (своя таблица nftables)
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$self apply
ExecStop=$self remove

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
  fi
  systemctl enable bb-agents-firewall.service >/dev/null 2>&1 || true
}

case "${1:-status}" in
  add-agent)
    need_root; agent=${2:?учётка}; shift 2; mkdir -p "$conf_dir"; touch "$conf"
    grep -qx "agent $agent" "$conf" || echo "agent $agent" >> "$conf"
    while [ $# -gt 0 ]; do
      [ "$1" = --allow-port ] || { echo "неизвестный параметр $1" >&2; exit 64; }
      [[ $2 =~ ^[0-9]+$ ]] || { echo "порт — число" >&2; exit 64; }
      grep -qx "port $2" "$conf" || echo "port $2" >> "$conf"; shift 2
    done
    apply;;
  add-untrusted)
    need_root; u=${2:?учётка}; mkdir -p "$conf_dir"; touch "$conf"
    grep -qx "untrusted $u" "$conf" || echo "untrusted $u" >> "$conf"; apply;;
  protect)
    need_root; g=${2:?группа systemd}; mkdir -p "$conf_dir"; touch "$conf"
    [ -d "/sys/fs/cgroup/$g" ] || { echo "нет группы /sys/fs/cgroup/$g" >&2; exit 66; }
    grep -qx "protect $g" "$conf" || echo "protect $g" >> "$conf"; apply;;
  remove-agent)
    need_root; agent=${2:?учётка}; [ -f "$conf" ] && sed -i "/^agent $agent\$/d" "$conf"; apply;;
  remove-port)
    need_root; port=${2:?порт}; [[ $port =~ ^[0-9]+$ ]] || { echo "порт — число" >&2; exit 64; }
    [ -f "$conf" ] && sed -i "/^port $port\$/d" "$conf"; apply;;
  apply) apply;;
  remove)
    need_root; nft list table inet bb_agents >/dev/null 2>&1 && nft delete table inet bb_agents || true;;
  enable-unit) need_root; BB_AGENTS_FW_NO_UNIT=0 apply; systemctl is-enabled bb-agents-firewall.service;;
  uninstall)
    need_root; "$self" remove; systemctl disable bb-agents-firewall.service >/dev/null 2>&1 || true
    rm -f "$unit" "$rules"; systemctl daemon-reload;;
  status) nft list table inet bb_agents 2>/dev/null || echo "таблица bb_agents не загружена";;
  *) echo "add-agent | remove-agent | remove-port | apply | remove | uninstall | status" >&2; exit 64;;
esac
