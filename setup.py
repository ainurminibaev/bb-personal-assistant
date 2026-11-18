"""Register this folder on its BB host and install the pinned Telegram plugin."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

SOURCE = "git:https://github.com/ainurminibaev/bb-plugin-telegram-projects.git@6ba3b02ee9d30a31547aa0b9590efdcbe65a4641"


def bb(*args):
    result = subprocess.run(["bb", *args, "--json"], check=True, stdout=subprocess.PIPE)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True, help="BB host ID of this folder")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    for name in (".bb/AGENTS.md", ".bb/skills/dispatch/SKILL.md", "projects.md"):
        if not (root / name).is_file():
            raise SystemExit(f"В папке шаблона отсутствует {name}")
    machines = bb("machine", "list")
    if not any(m["id"] == args.machine and m["status"] == "connected" for m in machines):
        raise SystemExit("Машина BB должна быть подключена. Проверь bb machine list.")
    plugins = bb("plugin", "list")["plugins"]
    installed = next((p for p in plugins if p["id"] == "telegram-projects"), None)
    if installed and (installed.get("source") != SOURCE or not installed.get("enabled")):
        raise SystemExit("В BB уже есть другая или выключенная установка telegram-projects. Проверь её источник и привязки.")
    projects = [p for p in bb("project", "list") if any(
        s.get("hostId") == args.machine and s.get("path") == str(root)
        for s in p.get("sources", [])
    )]
    if len(projects) > 1:
        raise SystemExit("У этой папки несколько проектов BB. Проверь их привязки.")
    if not installed:
        bb("plugin", "install", SOURCE, "--yes")
    project = projects[0] if projects else bb(
        "project", "create", "--name", "Личный помощник", "--root", str(root),
        "--machine", args.machine
    )
    status = bb("plugin", "rpc", "call", "telegram-projects", "status")
    preferences = bb("plugin", "rpc", "call", "telegram-projects", "preferences")
    print(f"Проект {project['id']} зарегистрирован.")
    if any(b.get("projectId") == project["id"] and b.get("topicId") == 0
           for b in status["chatBindings"]):
        print("Личный чат уже подключён к этому проекту.")
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
