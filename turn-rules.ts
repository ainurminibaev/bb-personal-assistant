import { createHash } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";

/**
 * The rules in force for a conversation, read from the files when asked (0.4.4).
 * A Codex session keeps the instructions it started with; a long conversation never sees later edits.
 * The bridge asks for these rules on every Telegram turn: the fingerprint tells whether this conversation already has
 * the current text, so the full text goes once per edit and an unchanged version costs one line.
 *
 * Parts: the platform's common rules, the organization's shared rules, the essence of the role
 * (its sections on how it works, how it writes and what it does not do) and the employee's own standing notes.
 */
export type TurnRules = { version: string; text: string };

/** Role sections that carry behaviour; the rest of the role (maps, accounts, tools) stays a file to open when needed. */
const ROLE_SECTIONS = /^##\s+(Как работаешь|Как пишешь|Не делаешь|Зона ответственности)/i;

const cache = new Map<string, { mtime: number; text: string }>();
function read(path: string) {
  try {
    const mtime = statSync(path).mtimeMs;
    const hit = cache.get(path);
    if (hit && hit.mtime === mtime) return hit.text;
    const text = readFileSync(path, "utf8");
    cache.set(path, { mtime, text });
    return text;
  } catch { return ""; }
}

/** The organization repository holds organization.yml; a project folder is usually employees/<name> inside it. */
export function organizationRoot(folder: string) {
  let dir = folder;
  for (let i = 0; i < 5; i++) {
    if (existsSync(join(dir, "organization.yml"))) return dir;
    const up = dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return undefined;
}

export function roleEssence(role: string) {
  const out: string[] = [];
  let keep = false;
  for (const line of role.split("\n")) {
    if (/^##\s/.test(line)) keep = ROLE_SECTIONS.test(line);
    if (keep) out.push(line);
  }
  return out.join("\n").trim();
}

export function turnRules(folder: string | undefined, platformRules: string): TurnRules | null {
  if (!folder) return null;
  const root = organizationRoot(folder);
  if (!root) return null; // not an organization's employee (the personal assistant keeps its own policy)
  const shared = read(join(root, "shared", "AGENTS.md")).trim();
  const role = roleEssence(read(join(folder, "AGENTS.md")));
  const notes = read(join(folder, "local", "agents-custom.md")).trim();
  const parts = [
    platformRules.trim() && `### Общие правила платформы\n${platformRules.trim()}`,
    shared && `### Общие правила организации (shared/AGENTS.md)\n${shared}`,
    role && `### Роль — суть (AGENTS.md проекта; карта, учётки и инструменты — в самом файле)\n${role}`,
    notes && `### Твои постоянные заметки (local/agents-custom.md)\n${notes}`,
  ].filter(Boolean) as string[];
  if (!parts.length) return null;
  const text = parts.join("\n\n");
  return { version: createHash("sha256").update(text).digest("hex").slice(0, 12), text };
}
