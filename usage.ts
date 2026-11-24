import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { closeSync, existsSync, openSync, readdirSync, readSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

/**
 * What one employee spends (С95). An employee has its own BB and its own Linux account, so the spend of this BB and of
 * this account's agent CLIs is the spend of the employee. Under a subscription there is no money, only turns, tokens
 * and the share of the subscription window:
 *  - turns — BB's own events (`turn/completed`) of this BB's threads;
 *  - tokens — the agents' own logs in the account's home: Codex `~/.codex/sessions/YYYY/MM/DD/*.jsonl` (`token_count`,
 *    `last_token_usage`), Claude Code `~/.claude/projects/<project>/*.jsonl` (`message.usage`);
 *  - the window — Codex writes the account's `rate_limits` (used_percent of the 5-hour and weekly windows) into the same log.
 * Two employees on one model account: the window is the account's; each employee's share = its tokens / the sum of both
 * (each counts its own; the owner's daily summary divides).
 */
export interface Usage {
  days: number;
  turns: number;
  tokens: { codex: number; claude: number; cached: number };
  window: { name: string; usedPercent: number; resetsAt: number | null }[];
}

const DAY = 86_400_000;

function codexFiles(home: string, since: number): string[] {
  const root = join(home, ".codex", "sessions");
  const out: string[] = [];
  for (let t = since - DAY; t <= Date.now() + DAY; t += DAY) {
    const d = new Date(t);
    const dir = join(root, String(d.getUTCFullYear()), String(d.getUTCMonth() + 1).padStart(2, "0"), String(d.getUTCDate()).padStart(2, "0"));
    if (existsSync(dir)) for (const f of readdirSync(dir)) if (f.endsWith(".jsonl")) out.push(join(dir, f));
  }
  return [...new Set(out)];
}

function claudeFiles(home: string, since: number): string[] {
  const root = join(home, ".claude", "projects");
  if (!existsSync(root)) return [];
  const out: string[] = [];
  for (const p of readdirSync(root)) {
    const dir = join(root, p);
    try {
      for (const f of readdirSync(dir)) if (f.endsWith(".jsonl") && statSync(join(dir, f)).mtimeMs >= since) out.push(join(dir, f));
    } catch { /* not a folder */ }
  }
  return out;
}

/**
 * Incremental reading (coordinator 25.11: on an installation with big logs the daily check must not read them whole):
 * for every log file the cache keeps how many bytes are counted and the totals per Moscow day; a later run reads only
 * what was appended. A file that shrank or was replaced is counted again. The cache lives in the plugin's storage.
 */
export interface FileCache { size: number; offset: number; days: Record<string, Totals>; window?: { at: number; limits: unknown } }
export type Totals = { codex: number; claude: number; cached: number };
export type LogCache = Record<string, FileCache>;

export const mskDay = (t: number) => new Date(t + 3 * 3_600_000).toISOString().slice(0, 10);

function readTail(path: string, from: number, to: number): string {
  const fd = openSync(path, "r");
  try {
    const buf = Buffer.alloc(to - from);
    readSync(fd, buf, 0, buf.length, from);
    return buf.toString("utf8");
  } finally { closeSync(fd); }
}

function countFile(path: string, kind: "codex" | "claude", entry: FileCache | undefined): FileCache {
  const size = statSync(path).size;
  const e: FileCache = entry && entry.size <= size && entry.offset <= size ? entry : { size: 0, offset: 0, days: {} };
  if (size === e.size && entry) return e;
  const text = readTail(path, e.offset, size);
  const end = text.lastIndexOf("\n") + 1;   // only complete lines; the rest is read next time
  for (const line of text.slice(0, end).split("\n")) {
    if (kind === "codex" ? !line.includes('"token_count"') : !line.includes('"usage"')) continue;
    try {
      const ev = JSON.parse(line);
      const at = Date.parse(ev.timestamp);
      if (!Number.isFinite(at)) continue;
      const d = (e.days[mskDay(at)] ??= { codex: 0, claude: 0, cached: 0 });
      if (kind === "codex") {
        const u = ev.payload?.info?.last_token_usage;
        if (u) { d.codex += u.total_tokens ?? 0; d.cached += u.cached_input_tokens ?? 0; }
        const rl = ev.payload?.rate_limits;
        if (rl && (!e.window || at >= e.window.at)) e.window = { at, limits: { primary: rl.primary, secondary: rl.secondary } };
      } else {
        const u = ev.message?.usage;
        if (u) {
          d.claude += (u.input_tokens ?? 0) + (u.output_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0);
          d.cached += u.cache_read_input_tokens ?? 0;
        }
      }
    } catch { /* a torn line */ }
  }
  return { ...e, size, offset: e.offset + Buffer.byteLength(text.slice(0, end), "utf8") };
}

/** Refresh the cache for files touched in the last `keepDays` days; drop entries of older files. */
export function refreshLogs(home: string, cache: LogCache, keepDays = 8, now = Date.now()): LogCache {
  const since = now - keepDays * DAY;
  const next: LogCache = {};
  for (const f of codexFiles(home, since)) next[f] = countFile(f, "codex", cache[f]);
  for (const f of claudeFiles(home, since)) next[f] = countFile(f, "claude", cache[f]);
  return next;
}

/** Tokens of the days from `fromDay` (Moscow date) and the latest window mark, from the cache. */
export function sumLogs(cache: LogCache, fromDay: string): Omit<Usage, "turns" | "days"> {
  const tokens = { codex: 0, claude: 0, cached: 0 };
  let latest: { at: number; limits: any } | undefined;
  for (const e of Object.values(cache)) {
    for (const [day, t] of Object.entries(e.days)) if (day >= fromDay) { tokens.codex += t.codex; tokens.claude += t.claude; tokens.cached += t.cached; }
    if (e.window && (!latest || e.window.at >= latest.at)) latest = e.window as { at: number; limits: any };
  }
  const window = Object.values(latest?.limits ?? {}).filter(Boolean).map((w: any) => ({
    name: w.window_minutes === 300 ? "5 часов" : w.window_minutes === 10080 ? "неделя" : `${w.window_minutes} мин`,
    usedPercent: w.used_percent ?? 0, resetsAt: w.resets_at ? w.resets_at * 1000 : null }));
  return { tokens, window };
}

/** Uncached reading for one period (tests and a one-off look). */
export function readAgentLogs(home: string, since: number): Omit<Usage, "turns" | "days"> {
  return sumLogs(refreshLogs(home, {}, Math.ceil((Date.now() - since) / DAY) + 1), mskDay(since));
}

/** Turns of this BB since a moment: BB's own events, only threads that moved in the period. */
async function countTurns(sdk: BbPluginApi["sdk"], since: number): Promise<number> {
  let turns = 0;
  for (let offset = 0; ; offset += 100) {
    const page = await sdk.threads.list({ includeHidden: true, limit: 100, offset }) as unknown as { id: string; updatedAt: number }[];
    for (const t of page.filter(t => t.updatedAt >= since)) {
      // BB gives at most 100 events a page: page back until the period starts.
      for (let before: string | undefined; ;) {
        const ev = await sdk.threads.events.list({ threadId: t.id, types: ["turn/completed"], order: "desc", limit: "100",
          ...(before ? { beforeSeq: before } : {}) } as never) as unknown as { createdAt: number; seq: number }[];
        turns += ev.filter(e => e.createdAt >= since).length;
        if (ev.length < 100 || ev[ev.length - 1].createdAt < since) break;
        before = String(ev[ev.length - 1].seq);
      }
    }
    if (page.length < 100) break;
  }
  return turns;
}

/** Today (Moscow) and, unless onlyToday, the last 7 days; the logs through the cache in the plugin's storage. */
export async function usage(bb: BbPluginApi, days: number, home = homedir(), now = Date.now()): Promise<Usage> {
  const cache = refreshLogs(home, (await bb.storage.kv.get<LogCache>("usage-cache")) ?? {}, 8, now);
  await bb.storage.kv.set("usage-cache", cache);
  const fromDay = mskDay(now - (days - 1) * DAY);
  const since = Date.parse(fromDay + "T00:00:00+03:00");
  return { days, turns: await countTurns(bb.sdk, since), ...sumLogs(cache, fromDay) };
}

const m = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)} млн` : n >= 1e3 ? `${Math.round(n / 1e3)} тыс.` : String(n);

export function formatUsage(day: Usage, week: Usage | null): string {
  const line = (u: Usage, label: string) => `${label}: ${u.turns} ходов, ${m(u.tokens.codex + u.tokens.claude)} токенов`
    + (u.tokens.codex && u.tokens.claude ? ` (Codex ${m(u.tokens.codex)}, Claude ${m(u.tokens.claude)})` : "")
    + (u.tokens.cached ? `, из них из кэша ${m(u.tokens.cached)}` : "");
  const w = (week ?? day).window;
  const win = w.length
    ? "Окно подписки модели (на весь аккаунт, если он общий): " + w.map(w => `${w.name} — ${Math.round(w.usedPercent)} %`
      + (w.resetsAt ? `, сброс ${new Date(w.resetsAt).toISOString().slice(0, 16).replace("T", " ")} UTC` : "")).join("; ")
    : "Окно подписки: данных нет (Codex ещё не писал отметку в этой учётке).";
  return [line(day, "Сегодня"), ...(week ? [line(week, "За 7 дней")] : []), win].join("\n");
}
