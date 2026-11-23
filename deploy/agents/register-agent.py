#!/usr/bin/env python3
"""Добавить или обновить агента в настройке плагина project-command (JSON-список агентов).
register-agent.py <текущий JSON> <учётка> <дом> <владелец TG|""> "<проекты через пробел>" "<чаты через пробел>"
Печатает новый JSON. Проекты и чаты добавляются к уже записанным, ничего не удаляется."""
import json, sys

cur, agent, home, owner, projects, chats = sys.argv[1:7]
try:
    agents = json.loads(cur) if cur.strip() else []
except ValueError:
    agents = []
prev = next((a for a in agents if a.get("user") == agent), {})
agents = [a for a in agents if a.get("user") != agent]
entry = {"user": agent, "home": home,
         "projects": sorted(set(prev.get("projects", []) + projects.split()))}
tg = dict(prev.get("telegram") or {})
if owner:
    tg["owner"] = int(owner)
if chats.split():
    tg["chats"] = sorted(set((tg.get("chats") or []) + [int(c) for c in chats.split()]))
if tg:
    entry["telegram"] = tg
agents.append(entry)
print(json.dumps(agents, ensure_ascii=False))
