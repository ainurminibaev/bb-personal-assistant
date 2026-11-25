"""Acceptance by deed (С114): the checks of an employee's installation that need no person and no model.

    setup.py acceptance [--workspace FOLDER] [--json]

One line per check: «прошло», or «не прошло — что сделать», or «решение владельца» (a known state the owner chose).
The result is saved to local/acceptance.json next to the tools report; the short daily check and the readiness check
after an update (С107) read the same list. Checks with a person (a picture in the private chat, «send the code»,
a mention in a test group) come in the next version.
"""
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import organization

OK, FAIL, OWNER, UNCHECKED = "прошло", "не прошло", "решение владельца", "не проверено"
# Fingerprints of the model logins of the server's employees (only 8 hex of a hash, never a value): a folder the
# administrator makes once (root, mode 1777); each acceptance writes its own print and reads the others.
PRINTS = Path("/var/lib/vallis-agents/login-prints")


def _row(check, state, text, action=""):
    return {"id": check, "state": state, "text": text, "action": action}


def bridge_values(bb):
    config = bb("plugin", "config", "telegram-projects")
    return config.get("values", config)


def check_owner_chat(status, project_id):
    own = [b for b in status.get("chatBindings", []) if b.get("topicId") == 0]
    if not status.get("owner"):
        return _row("owner_chat", FAIL, "личка владельца не подключена: бот не привязан к владельцу",
                    "владелец открывает ссылку привязки бота (README)")
    if not own or own[0].get("projectId") != project_id:
        return _row("owner_chat", FAIL, "личка владельца не привязана к проекту сотрудника",
                    "повторить установщик: он привязывает личку сам")
    return _row("owner_chat", OK, "личка владельца привязана к проекту сотрудника")


def check_files(values, machine):
    if not values.get("telegramInboxRoot"):
        return _row("files", FAIL, "у моста нет папки входящих", "повторить установщик")
    host = values.get("telegramFileHostId")
    if "telegramFileHostId" in values and not host:
        return _row("files", FAIL, "у моста не задана машина для файлов — картинки и файлы не принимаются",
                    "повторить установщик: он задаёт telegramFileHostId")
    if host and machine and host != machine:
        return _row("files", FAIL, f"файлы уходят на другую машину ({host})", "исправить telegramFileHostId")
    return _row("files", OK, "файлы в чатах принимаются")


def check_access(description, owner, approved_ids):
    people = (description or {}).get("people") or {}
    wanted = {name: v.get("telegram") for name, v in people.items()
              if isinstance(v, dict) and isinstance(v.get("telegram"), int) and v.get("telegram") != owner}
    missing = [name for name, tid in wanted.items() if tid not in approved_ids]
    if not wanted:
        return _row("access", FAIL, "в описании организации нет людей с Telegram ID — куратор не сможет поручать",
                    "добавить people в organization.yml")
    if missing:
        return _row("access", FAIL, "не одобрены у сотрудника: " + ", ".join(missing), "повторить установщик")
    return _row("access", OK, "люди организации и кураторы одобрены")


def check_org_copy(clone, run=subprocess.run):
    if not clone:
        return None
    fetch = run(["git", "-C", str(clone), "fetch", "-q", "origin"], capture_output=True, text=True)
    head = run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    remote = run(["git", "-C", str(clone), "rev-parse", "@{u}"], capture_output=True, text=True).stdout.strip()
    if fetch.returncode or not head or not remote:
        return _row("org_copy", FAIL, "копию описания организации не удалось сверить", "проверить зеркало описания")
    if head != remote:
        counts = run(["git", "-C", str(clone), "rev-list", "--left-right", "--count", "HEAD...@{u}"],
                     capture_output=True, text=True).stdout.split()
        dirty = run(["git", "-C", str(clone), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        behind = counts[1] if len(counts) == 2 else "?"
        more = "; в копии есть свои незакоммиченные правки — сначала их сохранить" if dirty else ""
        return _row("org_copy", FAIL, f"копия описания организации отстала на {behind} коммит(ов){more}",
                    "подтянуть копию (git pull --ff-only в папке организации)")
    return _row("org_copy", OK, "копия описания организации совпадает с зеркалом")


def login_fingerprint(home):
    """A short fingerprint of the model login's account (never the value): the same account → the same print."""
    auth = Path(home) / ".codex" / "auth.json"
    try:
        data = json.loads(auth.read_text())
    except (OSError, ValueError):
        return None
    account = (data.get("tokens") or {}).get("account_id") or data.get("account_id")
    return hashlib.sha256(str(account).encode()).hexdigest()[:8] if account else None


def check_login(own, others):
    """others: {employee: fingerprint} of the other employees of this server (from the shared prints, when there)."""
    if not own:
        return _row("model_login", FAIL, "нет входа в модель", "войти в Codex под учёткой сотрудника")
    same = sorted(name for name, fp in (others or {}).items() if fp == own)
    if same:
        return _row("model_login", OWNER, "общий вход в модель с: " + ", ".join(same)
                    + " — лимит модели один на всех; решение владельца")
    if others is None:
        return _row("model_login", UNCHECKED, "вход в модель есть; сверить с другими сотрудниками нечем",
                    "администратор сервера создаёт общую папку отпечатков (1777)")
    return _row("model_login", OK, "свой вход в модель")


def exchange_prints(me, own, folder=PRINTS):
    """Write this employee's print (644, its own file), read the others'. A file counts only when its owner is the
    account of the same name — nobody can put a print in another's name. None when the shared folder is not there."""
    import pwd
    if not folder.is_dir():
        return None
    mine = folder / me
    if own:
        try:
            if not mine.exists() or mine.stat().st_uid == os.getuid():
                mine.write_text(own)
                mine.chmod(0o644)
        except OSError:
            pass
    out = {}
    for p in folder.iterdir():
        if p.name == me or not p.is_file():
            continue
        try:
            if pwd.getpwuid(p.stat().st_uid).pw_name != p.name:
                continue
        except KeyError:
            continue
        out[p.name] = p.read_text().strip()[:8]
    return out


def check_resources(meminfo_text):
    info = {}
    for line in meminfo_text.splitlines():
        key, _, rest = line.partition(":")
        if rest.strip():
            info[key] = int(rest.split()[0])
    free_gb = info.get("MemAvailable", 0) / 1024 / 1024
    if not info.get("SwapTotal"):
        return _row("resources", FAIL, "на сервере нет подкачки", "включить подкачку 4–8 ГБ (администратор сервера)")
    if free_gb < 3:
        return _row("resources", FAIL, f"свободной памяти {free_gb:.1f} ГБ — меньше 3", "посмотреть, что держит память")
    return _row("resources", OK, f"подкачка есть, свободно {free_gb:.0f} ГБ памяти")


def check_idle(settings):
    general = settings.get("generalSettings", settings)
    minutes = general.get("idleProviderSessionTimeoutMinutes")
    if isinstance(minutes, int) and minutes > 10:
        return _row("idle", FAIL, f"простаивающие сеансы держатся {minutes} мин", "повторить установщик (ставит 10)")
    return _row("idle", OK, "простаивающие сеансы закрываются за 10 мин")


def check_configured(plugin_list_text):
    for line in plugin_list_text.splitlines():
        if line.startswith("personal-assistant@") and "configure failed" in line:
            return _row("configured", FAIL, "платформа не настроилась — навыки не выдаются",
                        "bb plugin reload personal-assistant")
    return _row("configured", OK, "платформа настроена")


def check_avatar(folder, employee, data_dir):
    import setup
    picture = setup.avatar_file(folder, employee or {})
    if picture is None:
        return None
    marker = Path(data_dir) / "personal-assistant-avatar.sha256" if data_dir else None
    if not marker or not marker.is_file():
        return _row("avatar", UNCHECKED, "отметки установщика нет — аватар мог стоять вручную",
                    "повторить установщик: он сверит и запишет отметку")
    if marker.read_text().strip() != hashlib.sha256(picture.read_bytes()).hexdigest():
        return _row("avatar", FAIL, "аватар в паспорте сменился, у бота старый", "повторить установщик")
    return _row("avatar", OK, "аватар из паспорта")


def summary(rows):
    failed = [r for r in rows if r["state"] == FAIL]
    return ("Приёмка: всё прошло." if not failed
            else f"Приёмка: не прошло {len(failed)} из {len(rows)} — запуск не объявляется.")


def render(rows):
    lines = []
    for r in rows:
        tail = f" — {r['action']}" if r["state"] == FAIL and r["action"] else ""
        lines.append(f"- {r['state']}: {r['text']}{tail}")
    lines.append(summary(rows))
    return "\n".join(lines)


def collect(root, bb, home, data_dir, others=None, run=subprocess.run, meminfo=None):
    """All the checks for an installation folder (employees/<name> inside the organization's copy)."""
    clone = root.parent.parent if root.parent.name == "employees" else None
    description, employee = ({}, {})
    if clone:
        description, employee, _ = organization.read_organization(clone, root.name)
    projects = [p for p in bb("project", "list") if any(Path(s.get("path") or "") == root for s in p.get("sources", []))]
    project = projects[0] if projects else {}
    machine = next((s.get("hostId") for s in project.get("sources", []) if Path(s.get("path") or "") == root), None)
    status = bb("plugin", "rpc", "call", "telegram-projects", "status")
    values = bridge_values(bb)
    approved = {p.get("id") for p in (bb("plugin", "rpc", "call", "telegram-projects", "rightsAccess") or [])}
    listing = run(["bb", "plugin", "list"], capture_output=True, text=True).stdout
    rows = [
        check_owner_chat(status, project.get("id")),
        check_files(values, machine),
        check_access(description, status.get("owner"), approved),
        check_org_copy(clone, run),
        check_login(login_fingerprint(home), others if others is not None else exchange_prints(root.name, login_fingerprint(home))),
        check_resources(meminfo if meminfo is not None else Path("/proc/meminfo").read_text()),
        check_idle(bb("settings", "show")),
        check_configured(listing),
        check_avatar(root, employee, data_dir),
    ]
    return [r for r in rows if r]


def save(root, rows):
    path = Path(root) / "local" / "acceptance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows},
                               ensure_ascii=False, indent=2))
    return path
