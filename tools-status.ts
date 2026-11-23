import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * `bb personal-assistant tools` (С43): what this agent can use, from the installer's last check
 * (`local/tools-report.json` in the project folder, next to its state file). Names, states and reasons in words — no paths, addresses or keys.
 * A guest conversation sees only what it can apply there (`--guest`, enforced by the guest thread guard).
 */
type Row = { id: string; state: string; reason?: string; action?: string; level?: string };
type Access = { name: string; what?: string; level?: string; source?: string; state?: string };
type Report = { kind?: string; tools?: Row[]; accesses?: Access[] };

const STATE: Record<string, string> = {
  installed: "работает", needs_login: "нужен вход", needs_setup: "нужна настройка",
  excluded: "не для этой установки", error: "ошибка",
};
// What a guest conversation can apply: the bridge's memory and sending, speech, the agent's own helpers.
const GUEST_TOOLS = new Set(["telegram-projects", "speech", "context-mode"]);

export function clean(text: string) {
  return text
    .replace(/https?:\/\/\S+/g, "…")
    .replace(/(^|[\s(«"'`])(~|\.{0,2})?\/[^\s)»"'`]+/g, "$1…")
    .replace(/\b[A-Za-z0-9_-]{32,}\b/g, "…")
    .replace(/\s+/g, " ")
    .trim();
}

export function render(report: Report, guest: boolean, age?: string) {
  const lines: string[] = [];
  const tools = (report.tools ?? []).filter(r => !guest || (GUEST_TOOLS.has(r.id) && r.state === "installed"));
  lines.push(guest ? "Инструменты, доступные в этом разговоре:" : "Инструменты:");
  for (const r of tools) {
    const reason = !guest && r.reason ? ` — ${clean(r.reason)}` : "";
    const action = !guest && r.action && r.state !== "installed" ? `; что сделать: ${clean(r.action)}` : "";
    lines.push(`- ${r.id}: ${STATE[r.state] ?? r.state}${reason}${action}`);
  }
  if (!tools.length) lines.push("- нет");
  const accesses = report.accesses ?? [];
  if (accesses.length) {
    lines.push(guest ? "Доступы (названия и назначение, без значений):" : "Доступы (без значений):");
    for (const a of accesses) {
      const what = a.what ? ` — ${clean(a.what)}` : "";
      const more = guest ? "" : [a.level, a.state && a.state !== "present" ? a.state : ""].filter(Boolean).join(", ");
      lines.push(`- ${a.name}${what}${more ? ` (${more})` : ""}`);
    }
  }
  if (age) lines.push(`Сверка: ${age}.`);
  return lines.join("\n");
}

/** When this plugin version was loaded: a report older than that was made before the update. */
const LOADED_AT = Date.now();
/** The package root (dist/server.js → ..), where setup.py lives. */
const PACKAGE = dirname(dirname(fileURLToPath(import.meta.url)));

/** Re-run the installer's check (read-only: states only, nothing installed) and save the report. */
function refreshReport(folder: string) {
  return new Promise<void>(resolve => {
    const setup = join(PACKAGE, "setup.py");
    if (!existsSync(setup)) return resolve();
    execFile("python3", [setup, "tools", "status", "--workspace", folder, "--json"],
      { cwd: folder, timeout: 120_000, maxBuffer: 4 << 20 }, () => resolve());
  });
}

export async function toolsReport(folder: string | undefined, guest: boolean, refresh = refreshReport) {
  if (!folder) return { exitCode: 1, stderr: "No project folder for this thread." };
  const path = join(folder, "local", "tools-report.json");
  // С43: the report follows the plugin — missing, or made before this version was loaded, it is checked again now.
  const made = await stat(path).then(i => i.mtimeMs, () => 0);
  if (made < LOADED_AT) await refresh(folder);
  try {
    const [text, info] = await Promise.all([readFile(path, "utf8"), stat(path)]);
    const age = info.mtime.toISOString().slice(0, 16).replace("T", " ") + " UTC";
    return { exitCode: 0, stdout: render(JSON.parse(text) as Report, guest, age) };
  } catch {
    return { exitCode: 1, stderr: "Сверку инструментов не удалось собрать: запустите установщик (setup.py)." };
  }
}
