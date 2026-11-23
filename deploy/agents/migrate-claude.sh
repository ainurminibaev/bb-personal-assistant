#!/bin/bash
# Перевод агента из формата Claude Code в общий для Claude Code и Codex (запускается под учёткой агента):
#   migrate-claude.sh <папка правил агента>
# - правила: CLAUDE.md → AGENTS.md, CLAUDE.md остаётся одной строкой «@AGENTS.md»;
# - личные навыки: ~/.claude/skills/<имя> → ~/.agents/skills/<имя>, ссылки из ~/.claude/skills и ~/.codex/skills;
# - память Claude (~/.claude/projects/*/memory) — раздел «Память» в AGENTS.md: где она и правило её читать и вести;
# - хук автоответа в Telegram (stop-autoreply) снимается: под BB ответ доставляет мост, хук дублировал бы его.
set -euo pipefail
ws=${1:?папка правил}; cd "$ws"
if [ -s CLAUDE.md ] && [ "$(tr -d '[:space:]' < CLAUDE.md)" != "@AGENTS.md" ]; then
  if [ -e AGENTS.md ]; then { echo; cat CLAUDE.md; } >> AGENTS.md; else mv CLAUDE.md AGENTS.md; fi
fi
[ -e AGENTS.md ] && echo "@AGENTS.md" > CLAUDE.md

mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills" "$HOME/.codex/skills"
for d in "$HOME"/.claude/skills/*/; do
  [ -d "$d" ] || continue; n=$(basename "$d")
  [ -L "$HOME/.claude/skills/$n" ] && continue
  mv "$HOME/.claude/skills/$n" "$HOME/.agents/skills/$n"
  ln -sfn "../../.agents/skills/$n" "$HOME/.claude/skills/$n"
done
for d in "$HOME"/.agents/skills/*/; do
  [ -d "$d" ] && ln -sfn "../../.agents/skills/$(basename "$d")" "$HOME/.codex/skills/$(basename "$d")"
done

mem=$(find "$HOME/.claude/projects" -maxdepth 2 -type d -name memory 2>/dev/null | head -1 || true)
if [ -n "$mem" ] && ! grep -q '^## Память' AGENTS.md 2>/dev/null; then
  cat >> AGENTS.md <<EOF

## Память
Твоя память — папка $mem. В начале работы прочитай $mem/MEMORY.md (оглавление) и нужные файлы из неё.
Новое устойчивое знание записывай туда же: один факт — один файл, строка со ссылкой — в MEMORY.md.
EOF
fi

for f in "$HOME/.claude/settings.json" "$ws/.claude/settings.json"; do
  [ -f "$f" ] && grep -q stop-autoreply "$f" || continue
  cp "$f" "$f.before-bb"
  python3 - "$f" <<'EOF'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); h=d.get("hooks",{})
h["Stop"]=[e for e in h.get("Stop",[]) if "stop-autoreply" not in json.dumps(e)]
if not h["Stop"]: h.pop("Stop")
json.dump(d,open(p,"w"),ensure_ascii=False,indent=2)
EOF
  echo "снят хук автоответа в $f (копия — $f.before-bb)"
done
echo "перевод готов: $ws"
