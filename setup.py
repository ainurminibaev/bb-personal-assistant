"""Register this assistant workspace and install both release-tracking BB plugins."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import sys

from migrate_bridge import LEGACY, TRACKING

ASSISTANT = "git:https://github.com/ainurminibaev/bb-personal-assistant.git@semver:^0.2.0-rc.1"
SOURCES = {"telegram-projects": TRACKING, "personal-assistant": ASSISTANT}


def bb(*args):
    result = subprocess.run(["bb", *args, "--json"], check=True, stdout=subprocess.PIPE)
    return json.loads(result.stdout)


def supports_group_results(version):
    """The kit needs the released group-result RPC and the isolated group worker."""
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    return bool(match and tuple(map(int, match.groups())) >= (0, 9, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True, help="BB host ID of this folder")
    parser.add_argument("--yes", action="store_true", help="Approve installation of the displayed release sources")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Existing assistant project or a new private working directory")
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    root = args.workspace.expanduser().resolve()
    if root == package or package in root.parents:
        raise SystemExit("Выберите отдельную рабочую папку вне каталога выпуска.")
    if root.exists() and (not root.is_dir() or (any(root.iterdir()) and not (root / "projects.md").is_file())):
        raise SystemExit("Выберите пустой каталог либо существующую папку помощника с projects.md.")
    machines = bb("machine", "list")
    if not any(m["id"] == args.machine and m["status"] == "connected" for m in machines):
        raise SystemExit("Машина BB должна быть подключена. Проверь bb machine list.")
    plugins = bb("plugin", "list")["plugins"]
    installed = {p["id"]: p for p in plugins}
    for plugin_id, expected in SOURCES.items():
        plugin = installed.get(plugin_id)
        if (plugin and plugin_id == "telegram-projects"
                and plugin["source"].lower().split("@", 1)[0].removesuffix(".git")
                == "git:https://github.com/vkirill/bb-plugin-telegram-projects"):
            raise SystemExit(
                "The original Telegram Projects plugin is installed. Its v0.9.0 has no "
                "private-state export for this migration. Keep this installation for its existing "
                "chats. To try the assistant separately, use a separate BB instance and bot token. "
                "See BRIDGE-MIGRATION.md."
            )
        if plugin and plugin["source"] == LEGACY and plugin_id == "telegram-projects":
            raise SystemExit("Сначала перенесите прежнюю привязку по BRIDGE-MIGRATION.md.")
        if plugin and (plugin["source"] != expected or not plugin["enabled"]):
            raise SystemExit(f"Проверьте существующий источник и состояние {plugin_id}.")
    projects = [p for p in bb("project", "list") if any(
        s.get("hostId") == args.machine and s.get("path") == str(root)
        for s in p.get("sources", [])
    )]
    if len(projects) > 1:
        raise SystemExit("У этой папки несколько проектов BB. Проверь их привязки.")
    missing = [src for pid, src in SOURCES.items() if pid not in installed]
    if missing:
        print("BB release sources:", *missing, sep="\n", flush=True)
        if not args.yes and (not sys.stdin.isatty() or input("Install [y/N]? ").lower() != "y"):
            raise SystemExit("Установка ожидает подтверждения. Для автоматизации используйте --yes.")
        for src in missing:
            bb("plugin", "install", src, "--yes")
    # Existing release-tracking installations can lag behind the newest tag.
    bridge = next(p for p in bb("plugin", "list")["plugins"] if p["id"] == "telegram-projects")
    if not supports_group_results(bridge["version"]):
        raise SystemExit("Update Telegram Bridge through BB Settings → Updates, then rerun setup. "
                         "Returning approved group results requires stable Telegram Bridge 0.9.2 or later.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with (root / "projects.md").open("x", encoding="utf-8") as registry:
            registry.write((package / "projects.md").read_text(encoding="utf-8"))
    except FileExistsError:
        pass
    project = projects[0] if projects else bb(
        "project", "create", "--name", "Личный помощник", "--root", str(root),
        "--machine", args.machine
    )
    status = bb("plugin", "rpc", "call", "telegram-projects", "status")
    preferences = bb("plugin", "rpc", "call", "telegram-projects", "preferences")
    print(f"Проект {project['id']} зарегистрирован.")
    personal = [b for b in status["chatBindings"] if b.get("topicId") == 0
                and b.get("threadId") and b.get("projectId") == project["id"]]
    if personal:
        if len(personal) != 1:
            raise SystemExit("Нужна одна однозначная привязка личного чата.")
        expected = {"projectId": project["id"], "threadId": personal[0]["threadId"]}
        configured = bb("plugin", "config", "personal-assistant")["values"]
        if any(configured.get(k) and configured[k] != v for k, v in expected.items()):
            raise SystemExit("В плагине уже выбран другой координатор. Сверьте привязку перед заменой.")
        if any(configured.get(k) != v for k, v in expected.items()):
            for key, value in expected.items():
                bb("plugin", "config", "personal-assistant", "set", key, value)
            bb("plugin", "reload", "personal-assistant")
        print("Личный чат и инструкции плагина привязаны к координатору.")
    elif preferences["tokenPresent"] or status.get("owner") or status["chatBindings"]:
        print("Telegram уже настроен в этой установке BB. Для второго независимого бота "
              "используй отдельную установку BB. Текущая привязка сохранена.")
    else:
        print("Для подключения первого личного бота открой README.md.")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError:
        sys.exit("Команда bb недоступна. Запусти BB и открой терминал с его CLI.")
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
