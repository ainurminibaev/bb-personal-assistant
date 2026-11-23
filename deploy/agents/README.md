# Несколько агентов на одном сервере: свои учётки Linux, один BB агентов

Каждый агент работает под своей учётной записью Linux; все агенты — в одном BB агентов, отдельном от BB владельца.
Три замка: **стена** (права файлов и сетевой экран), **инструмент** `project_command` (команды BB только в своих
проектах), **обёртка запуска** (ход Codex или Claude Code под учёткой агента, в его папке и в его срезе памяти).
Проверено на Ubuntu 24.04 (systemd 255, nftables, рядом с Docker и ufw).

## Что ставится

| Что | Где |
|---|---|
| BB агентов | служба [`bb-agents.service`](bb-agents.service), учётка `bbagents`, `127.0.0.1:39300/39301`; плагин `project-command` |
| Обёртка, выбор агента по папке, хранитель входа | `/usr/local/lib/bb-agents`, настройки `/etc/bb-agents` |
| Экран | своя таблица `inet bb_agents` ([`firewall.sh`](firewall.sh)) и служба восстановления; чужие таблицы не трогает |
| Вход в модель | копия входа Codex без долгого ключа в `/var/lib/bb-agents/login` (440, группа `bb-agents-login`) |
| Агент | дом `/home/<агент>`, группа `bbws-<агент>`, срез памяти `bb-agents.service/agents/<агент>` |

## Порядок

1. Общие программы в `/opt/bb-agents` (Node, `bb-app` той же версии, что у владельца, Codex CLI), учётка `bbagents`,
   служба `bb-agents.service`, плагин: `bb plugin install <платформа>/plugins/project-command` в BB агентов.
2. Экран: `firewall.sh protect <группа systemd BB владельца>` и `firewall.sh add-untrusted bbagents`, применять с
   `BB_AGENTS_FW_NO_UNIT=1`; проверить, что BB владельца отвечает root и помощник отвечает, затем `firewall.sh enable-unit`.
3. Вход: `/etc/bb-agents/login.env` — `LOGIN_USER`, `CODEX_SOURCE` (штатный вход учётки владельца входа);
   `login-keeper.sh publish`. Служба выкладки только выкладывает копию; обновление штатным CLI — `LOGIN_REFRESH=1`.
4. `/etc/bb-agents/agents.env`: `CODEX_BIN`, `CLAUDE_BIN`, `NODE_BIN`, `TOOLS_PATH`; память агента —
   `AGENT_MEMORY_MAX` (1G), `AGENT_PIDS_MAX` (256).
5. Агент: `install-agent.sh install <агент> --bb-user bbagents --project <id> --bb-cli … --bb-url http://127.0.0.1:39300
   [--allow-port N] [--owner-telegram ID] [--chat ID]`; перевод из формата Claude — `install-agent.sh migrate-claude
   <агент> --workspace <папка>`. После нового агента перезапустить `bb-agents.service` (установщик предупредит).
6. Общие инструменты платформы ставит тот же `install-agent.sh install` (с `--bb-cli` и `--bb-url` — и плагины
   в BB агентов) и печатает состояние каждой строки `tools.yml`. Позже то же одной командой от root:
   `python3 <платформа>/tools.py status [--json] --kind agents --account <агент> --bb-user bbagents --bb-cli … --bb-url …`.
   Формат — [docs/tools.md](../../docs/tools.md).
7. Проверки: ход агента (`id -un`, `pwd`), отказы `checkCommand`, порты BB владельца и BB агентов от учётки агента,
   правила Docker и ufw до и после.

## Память

BB агентов в покое ~0,7 ГБ; Codex держит процесс на каждый открытый тред (~0,1–0,2 ГБ). Предел службы 7 ГБ, BB
гарантировано 1 ГБ, агенту — 1 ГБ: выход за него завершает процесс этого агента, остальные не затронуты.
Одновременные ходы ограничивает штатный `concurrency-limit` (по умолчанию — по числу ядер), лишние ждут в очереди.

## Удаление

`install-agent.sh remove <агент> [--purge]`; `firewall.sh uninstall`; остановить и удалить `bb-agents*` юниты.
