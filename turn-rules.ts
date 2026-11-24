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

/**
 * Where a conversation's rules come from (one rule, with the bridge's С76 folders):
 *  - a folder of its own made by the bridge carries `.vallis-conversation.json` {"installation": <folder>} — the
 *    organization's rules and the role from there, the standing notes the conversation's own (local/notes.md);
 *  - a folder inside the organization's copy — the folder itself, with the employee's notes (local/agents-custom.md);
 *  - any other folder of a bound conversation (an older project of a group outside the copy) — the organization's
 *    rules and the role from the installation's folder, the notes again only its own (local/notes.md).
 * The employee's own notes from its private chat never reach a group or another person's chat.
 */
export const CONVERSATION_MARKER = ".vallis-conversation.json";
export function rulesSource(folder: string, installation?: string): { installation: string; notes: string; own: boolean } {
  try {
    const mark = JSON.parse(read(join(folder, CONVERSATION_MARKER)) || "{}") as { installation?: unknown };
    if (typeof mark.installation === "string" && mark.installation && existsSync(mark.installation))
      return { installation: mark.installation, notes: join(folder, "local", "notes.md"), own: true };
  } catch { /* not a conversation folder */ }
  if (organizationRoot(folder) || !installation) return { installation: folder, notes: join(folder, "local", "agents-custom.md"), own: false };
  return { installation, notes: join(folder, "local", "notes.md"), own: true };
}

export function turnRules(folder: string | undefined, platformRules: string, installation?: string): TurnRules | null {
  // Outside an organization's copy (the personal assistant, a conversation in a folder of its own) the platform's
  // common rules still go: a conversation never misses them; the organization's parts are added where they exist.
  const source = folder ? rulesSource(folder, installation) : undefined;
  const root = source ? organizationRoot(source.installation) : undefined;
  const shared = root ? read(join(root, "shared", "AGENTS.md")).trim() : "";
  const role = root && source ? roleEssence(read(join(source.installation, "AGENTS.md"))) : "";
  const notes = root && source ? read(source.notes).trim() : "";
  const parts = [
    platformRules.trim() && `### Общие правила платформы\n${platformRules.trim()}`,
    shared && `### Общие правила организации (shared/AGENTS.md)\n${shared}`,
    role && `### Роль — суть (AGENTS.md проекта; карта, учётки и инструменты — в самом файле)\n${role}`,
    notes && `### Твои постоянные заметки (${source?.own ? "local/notes.md этого разговора" : "local/agents-custom.md"})\n${notes}`,
  ].filter(Boolean) as string[];
  if (!parts.length) return null;
  const text = parts.join("\n\n");
  return { version: createHash("sha256").update(text).digest("hex").slice(0, 12), text };
}
