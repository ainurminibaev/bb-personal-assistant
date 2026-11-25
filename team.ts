import { existsSync, readFileSync } from "node:fs";
import { basename, dirname, join } from "node:path";

/**
 * Passport and registry of employees (С94): who does what, who gives tasks, who curates, who owns, the state. Read from
 * the organization's copy: employees/<name>/employee.yml (passport fields there) and employees/<name>/passport.yml (wins).
 * Used by people and agents to know whom to call (ai_agents docs/tasks/agents-in-chats.md, «кого звать»).
 */
export const PASSPORT_FIELDS = ["leads_chats", "name", "position", "state", "owner", "curator", "tasks_from", "tasks_to", "duties",
  "not_allowed", "grants_within", "chats", "accounts", "connections"] as const;
export const STATES = ["active", "paused", "retired"] as const;
/** Fields a guest conversation does not get: where the employee has accounts and connections. */
const PRIVATE = new Set(["accounts", "connections", "grants_within"]);

export type Passport = Record<string, string | string[]> & { id: string };

/** The passport subset of YAML: `key: value`, `key:` followed by `  - item` lines, `# comments`. Nothing more is needed. */
export function parseSimpleYaml(text: string): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  let list: string | null = null;
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\s+#.*$/, "").replace(/^#.*$/, "");
    if (!line.trim()) continue;
    const item = /^\s+-\s+(.*)$/.exec(line);
    if (item && list) { (out[list] as string[]).push(unquote(item[1])); continue; }
    const kv = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(line);
    if (!kv) { list = null; continue; }
    const [, key, value] = kv;
    if (value === "" ) { out[key] = []; list = key; }
    else if (value === "[]") { out[key] = []; list = null; }
    else if (/^\[.*\]$/.test(value)) { out[key] = value.slice(1, -1).split(",").map(s => unquote(s.trim())).filter(Boolean); list = null; }
    else { out[key] = unquote(value); list = null; }
  }
  return out;
}

const unquote = (s: string) => s.replace(/^["'](.*)["']$/, "$1");

/** The organization copy this installation works from: its project folder is employees/<name> inside it. */
export function organizationOf(folder: string | undefined): { clone: string; me: string } | null {
  if (!folder) return null;
  const parent = dirname(folder);
  if (basename(parent) !== "employees" || !existsSync(join(dirname(parent), "organization.yml"))) return null;
  return { clone: dirname(parent), me: basename(folder) };
}

export function readPassport(clone: string, id: string): Passport {
  const read = (f: string) => existsSync(f) ? parseSimpleYaml(readFileSync(f, "utf8")) : {};
  const description = read(join(clone, "employees", id, "employee.yml"));
  const passport = read(join(clone, "employees", id, "passport.yml"));
  const out: Passport = { id };
  for (const k of PASSPORT_FIELDS) {
    const v = passport[k] ?? description[k] ?? (k === "position" ? description.description : undefined);
    if (v !== undefined) out[k] = v;
  }
  if (!out.state) out.state = "active";
  return out;
}

export function registry(clone: string): Passport[] {
  const org = parseSimpleYaml(readFileSync(join(clone, "organization.yml"), "utf8"));
  const ids = Array.isArray(org.employees) ? org.employees : [];
  return ids.map(id => readPassport(clone, id));
}

const STATE_WORDS: Record<string, string> = { active: "работает", paused: "на паузе", retired: "выведен" };
const list = (v: string | string[] | undefined) => (Array.isArray(v) ? v : v ? [v] : []).join("; ");

/** Plain words for a person or an agent; a guest conversation gets no accounts and connections. */
export function formatRegistry(rows: Passport[], guest: boolean): string {
  return rows.map(p => {
    const lines = [`${p.name ?? p.id} — ${p.position ?? "должность не указана"} (${STATE_WORDS[String(p.state)] ?? p.state})`];
    if (p.duties) lines.push(`  отвечает: ${list(p.duties)}`);
    if (p.tasks_from) lines.push(`  задачи ставят: ${list(p.tasks_from)}`);
    if (p.curator) lines.push(`  куратор (как делать и хорошо ли сделано): ${list(p.curator)}`);
    if (p.owner) lines.push(`  владелец (права, доступы, люди, деньги, споры): ${list(p.owner)}`);
    if (p.not_allowed) lines.push(`  не делает: ${list(p.not_allowed)}`);
    if (p.chats) lines.push(`  где на связи: ${list(p.chats)}`);
    if (p.leads_chats !== undefined) lines.push(`  ведёт общие чаты без отметки: ${String(p.leads_chats) === "true" ? "да" : "нет"}`);
    if (!guest) for (const k of PRIVATE) if (p[k]) lines.push(`  ${k}: ${list(p[k])}`);
    return lines.join("\n");
  }).join("\n\n");
}

export type OrgPerson = { id: number; name: string; roles: string[]; of?: string[] };

/** organization.yml `people:` — `  <name>:` then `    telegram: <id>` / `    approved: false`; comments ignored. */
export function parsePeople(text: string): { name: string; telegram?: number; approved: boolean }[] {
  const out: { name: string; telegram?: number; approved: boolean }[] = [];
  let inside = false;
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\s+#.*$/, "").replace(/^#.*$/, "");
    if (!line.trim()) continue;
    if (/^\S/.test(line)) { inside = /^people:\s*$/.test(line); continue; }
    if (!inside) continue;
    const person = /^ {2}(\S[^:]*):\s*$/.exec(line);
    if (person) { out.push({ name: unquote(person[1].trim()), approved: true }); continue; }
    const field = /^ {4}([a-z_]+):\s*(.+)$/.exec(line);
    if (field && out.length) {
      const cur = out[out.length - 1];
      if (field[1] === "telegram" && /^\d+$/.test(field[2].trim())) cur.telegram = Number(field[2].trim());
      if (field[1] === "approved") cur.approved = field[2].trim() !== "false";
    }
  }
  return out;
}

/** С111: the organization's people for the bridge — the same list for every employee of the organization. Owner and
 * curator come from the passports (names as in `people`); everyone listed with a Telegram ID is approved unless
 * `approved: false`. null when the installation does not work from an organization copy. */
export function organizationPeople(folder: string | undefined, hash: (s: string) => string):
    { version: string; organization: string; me: string; people: OrgPerson[] } | null {
  const org = organizationOf(folder);
  if (!org) return null;
  const text = readFileSync(join(org.clone, "organization.yml"), "utf8");
  const passports = registry(org.clone);
  const names = (v: string | string[] | undefined) => (Array.isArray(v) ? v : v ? [v] : []).map(String);
  const people: OrgPerson[] = [];
  for (const p of parsePeople(text)) {
    if (!p.telegram) continue;
    const roles: string[] = [];
    if (passports.some(x => names(x.owner).includes(p.name))) roles.push("owner");
    const of = passports.filter(x => names(x.curator).includes(p.name)).map(x => x.id).sort();
    if (of.length) roles.push("curator");
    if (p.approved) roles.push("approved");
    people.push({ id: p.telegram, name: p.name, roles, ...(of.length ? { of } : {}) });
  }
  people.sort((a, b) => a.id - b.id);
  return { version: "op-" + hash(JSON.stringify(people)).slice(0, 8), organization: String(parseSimpleYaml(text).name ?? ""),
    me: org.me, people };
}
