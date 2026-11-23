# Хранилище доступов агента (`vallis-secret`)

Пароли, ключи и входы агента — по зашифрованному файлу на ключ (sops + age), без службы. Значения не
печатаются. Навык агента — `.bb/skills/secrets-store` (выдаётся тредам, когда `vallis-secret` есть на PATH BB).

**Ставит установщик** — строка `secrets-store` в `tools.yml`:
- `vallis-secret` из пакета платформы — в папку программ учётки на PATH её BB (`/usr/local/bin` для агентов BB агентов);
- sops — закреплённый официальный выпуск со сверкой sha256 из `tools.yml`;
- age и age-keygen — пакет `age` из архива Ubuntu (`server-setup.sh` ставит; иначе `apt-get install age` от root);
- `vallis-secret init` — ключ age учётки `~/.config/vallis/age.key` (0600), папки `~/.local/share/vallis/secrets/{own,org}` (0700);
- публичные ключи администраторов из `organization.yml` (`secrets.admin`) — в `~/.config/vallis/admin.recipients`;
- проверка перед коммитом в git-копии рабочей папки: коммит со значением из хранилища не проходит.

Состояние — `setup.py tools status [--json]`: строка `secrets-store` и по строке `secret:<ИМЯ>` на каждый описанный ключ.

**Ключ организации.** Администратор на своей машине (у него свой ключ age, `vallis-secret init`):
1. файл получателей — публичные ключи названных сотрудников (печатает `vallis-secret init` у каждого), по строке;
2. `vallis-secret grant ИМЯ --what "…" --to получатели --out ИМЯ.json --stdin` (значение — на вход) или `--generate`;
3. файл `ИМЯ.json` отнести на сервер сотрудника (он зашифрован, передавать можно любым способом);
4. у сотрудника: `vallis-secret receive ИМЯ.json` — принимает, только если сотрудник среди получателей.
Убрать сотрудника — выдать заново без него и удалить у него `org/ИМЯ.json`; если он мог видеть значение — сменить его у сервиса.

**Проверить:** `vallis-secret put CHECK --generate --what проверка`, `vallis-secret list --json` — запись есть,
значения нет; `vallis-secret run CHECK -- sh -c 'test -n "$CHECK"'` — код 0; `vallis-secret remove CHECK`.

Устройство и угрозы — проект С37 в мастерской платформы.
