import type { BbPluginApi } from "@get-bb/plugin-sdk";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
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

/** Tokens and the latest window from the agents' logs of this account since a moment. */
export function readAgentLogs(home: string, since: number): Omit<Usage, "turns" | "days"> {
  const tokens = { codex: 0, claude: 0, cached: 0 };
  let latest: { at: number; limits: Record<string, { used_percent?: number; window_minutes?: number; resets_at?: number } | null> } | null = null;
  for (const f of codexFiles(home, since)) {
    for (const line of readFileSync(f, "utf8").split("\n")) {
      if (!line.includes('"token_count"')) continue;
      try {
        const e = JSON.parse(line);
        const at = Date.parse(e.timestamp);
        if (!(at >= since)) continue;
        const u = e.payload?.info?.last_token_usage;
        if (u) { tokens.codex += u.total_tokens ?? 0; tokens.cached += u.cached_input_tokens ?? 0; }
        const rl = e.payload?.rate_limits;
        if (rl && (!latest || at >= latest.at)) latest = { at, limits: { primary: rl.primary, secondary: rl.secondary } };
      } catch { /* a torn line */ }
    }
  }
  for (const f of claudeFiles(home, since)) {
    for (const line of readFileSync(f, "utf8").split("\n")) {
      if (!line.includes('"usage"')) continue;
      try {
        const e = JSON.parse(line);
        if (!(Date.parse(e.timestamp) >= since)) continue;
        const u = e.message?.usage;
        if (u) {
          tokens.claude += (u.input_tokens ?? 0) + (u.output_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0);
          tokens.cached += u.cache_read_input_tokens ?? 0;
        }
      } catch { /* a torn line */ }
    }
  }
  const window = Object.values(latest?.limits ?? {}).filter(Boolean).map(w => ({
    name: w!.window_minutes === 300 ? "5 часов" : w!.window_minutes === 10080 ? "неделя" : `${w!.window_minutes} мин`,
    usedPercent: w!.used_percent ?? 0, resetsAt: w!.resets_at ? w!.resets_at * 1000 : null }));
  return { tokens, window };
}

/** Turns of this BB since a moment: BB's own events, only threads that moved in the period. */
async function countTurns(sdk: BbPluginApi["sdk"], since: number): Promise<number> {
  let turns = 0;
  for (let offset = 0; ; offset += 100) {
    const page = await sdk.threads.list({ includeHidden: true, limit: 100, offset }) as unknown as { id: string; updatedAt: number }[];
    for (const t of page.filter(t => t.updatedAt >= since)) {
      const ev = await sdk.threads.events.list({ threadId: t.id, types: ["turn/completed"], order: "desc", limit: "500" }) as unknown as { createdAt: number }[];
      turns += ev.filter(e => e.createdAt >= since).length;
    }
    if (page.length < 100) break;
  }
  return turns;
}

export async function usage(bb: BbPluginApi, days: number, home = homedir()): Promise<Usage> {
  const since = Date.now() - days * DAY;
  return { days, turns: await countTurns(bb.sdk, since), ...readAgentLogs(home, since) };
}

const m = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)} млн` : n >= 1e3 ? `${Math.round(n / 1e3)} тыс.` : String(n);

export function formatUsage(day: Usage, week: Usage): string {
  const line = (u: Usage, label: string) => `${label}: ${u.turns} ходов, ${m(u.tokens.codex + u.tokens.claude)} токенов`
    + (u.tokens.codex && u.tokens.claude ? ` (Codex ${m(u.tokens.codex)}, Claude ${m(u.tokens.claude)})` : "")
    + (u.tokens.cached ? `, из них из кэша ${m(u.tokens.cached)}` : "");
  const win = week.window.length
    ? "Окно подписки модели (на весь аккаунт, если он общий): " + week.window.map(w => `${w.name} — ${Math.round(w.usedPercent)} %`
      + (w.resetsAt ? `, сброс ${new Date(w.resetsAt).toISOString().slice(0, 16).replace("T", " ")} UTC` : "")).join("; ")
    : "Окно подписки: данных нет (Codex ещё не писал отметку в этой учётке).";
  return [line(day, "За сутки"), line(week, "За неделю"), win].join("\n");
}
