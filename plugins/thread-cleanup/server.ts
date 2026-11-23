// Thread cleanup — archives finished sub-threads once a day, without a model turn.
//
// The rule is in policy.ts. Here: collect facts through the plugin API, archive
// up to a limit, keep a journal, and tell the owner one line when something
// needs a look. Archive is not delete: `bb thread-cleanup restore` or
// `bb thread unarchive <id>` brings a thread back.
import type { BbPluginApi } from "@get-bb/plugin-sdk";
import {
  decide, inScope, kindOf, parseHours, parseList,
  type CleanupConfig, type ThreadFacts, type ThreadRow, type WorkspaceState,
} from "./policy.ts";

const PAGE = 100;
const JOURNAL_MAX = 1000;

interface RunOptions { dryRun: boolean; maxArchives: number; signal?: AbortSignal; now?: number }
interface Decision { id: string; projectId: string; title: string; kind: string; archive: boolean; keep: string[]; attention: string | null }
export interface RunResult {
  at: number; dryRun: boolean; scanned: number; checked: number;
  archived: string[]; wouldArchive: Decision[]; errors: { id: string; error: string }[];
  attention: Decision[]; kept: Record<string, number>;
}
interface JournalEntry { at: number; id: string; title: string; reason: string; restoredAt?: number }

type Sdk = BbPluginApi["sdk"];

async function listAll(sdk: Sdk, signal?: AbortSignal): Promise<ThreadRow[]> {
  const out: ThreadRow[] = [];
  for (let offset = 0; !signal?.aborted; offset += PAGE) {
    const page = (await sdk.threads.list({ archived: false, includeHidden: true, limit: PAGE, offset, signal })) as unknown as ThreadRow[];
    out.push(...page);
    if (page.length < PAGE) break;
  }
  return out;
}

/** Thread ids an enabled automation targets or that created one. */
async function scheduledThreads(sdk: Sdk, projectIds: Iterable<string>): Promise<Set<string>> {
  const out = new Set<string>();
  for (const projectId of new Set(projectIds)) {
    const list = (await sdk.plugins.callRpc({ pluginId: "automations", method: "automations_list", input: { projectId } } as never)) as unknown;
    if (!Array.isArray(list)) throw new Error("automations_unavailable");
    for (const a of list as { enabled?: boolean; createdByThreadId?: string | null; execution?: { targetThreadId?: string | null } }[]) {
      if (!a.enabled) continue;
      if (a.execution?.targetThreadId) out.add(a.execution.targetThreadId);
      if (a.createdByThreadId) out.add(a.createdByThreadId);
    }
  }
  return out;
}

/** Thread ids other plugins ask to keep (chat bindings), via an optional RPC each. */
async function protectedByPlugins(sdk: Sdk, specs: string[]): Promise<Set<string>> {
  const out = new Set<string>();
  for (const spec of specs) {
    const dot = spec.lastIndexOf(".");
    if (dot <= 0) continue;
    const ids = (await sdk.plugins.callRpc({ pluginId: spec.slice(0, dot), method: spec.slice(dot + 1), input: {} } as never)) as unknown;
    if (!Array.isArray(ids)) throw new Error(`protection_unavailable:${spec}`);
    for (const id of ids) if (typeof id === "string") out.add(id);
  }
  return out;
}

async function lastTurn(sdk: Sdk, threadId: string, signal?: AbortSignal) {
  const ev = await sdk.threads.events.list({ threadId, types: ["turn/completed"], order: "desc", limit: "1", signal } as never) as unknown as { createdAt: number; data?: { status?: string } }[];
  return ev[0] ? { at: ev[0].createdAt, status: ev[0].data?.status ?? "unknown" } : null;
}

async function lastActivity(sdk: Sdk, threadId: string, signal?: AbortSignal) {
  const ev = await sdk.threads.events.list({ threadId, order: "desc", limit: "1", signal } as never) as unknown as { createdAt: number }[];
  return ev[0]?.createdAt ?? 0;
}

async function workspaceOf(sdk: Sdk, t: ThreadRow): Promise<WorkspaceState> {
  if (!t.environmentIsWorktree || !t.environmentId) return "none";
  try {
    const res = (await sdk.plugins.callRpc({ pluginId: "bb--git", method: "status", input: { keys: [t.environmentId] } } as never)) as Record<string, {
      outcome?: string; workspace?: { workingTree?: { fileCount?: number }; mergeBase?: { hasCommittedUnmergedChanges?: boolean } };
    }>;
    const s = res?.[t.environmentId];
    if (s?.outcome !== "available" || !s.workspace) return "unknown";
    const dirty = (s.workspace.workingTree?.fileCount ?? 0) > 0 || s.workspace.mergeBase?.hasCommittedUnmergedChanges !== false;
    return dirty ? "dirty" : "clean";
  } catch {
    return "unknown";
  }
}

export async function runCleanup(bb: BbPluginApi, cfg: CleanupConfig, opts: RunOptions & { protectionRpcs: string[] }): Promise<RunResult> {
  const now = opts.now ?? Date.now();
  const sdk = bb.sdk;
  const threads = await listAll(sdk, opts.signal);
  const byId = new Map(threads.map(t => [t.id, t]));
  const live = new Map<string, number>();
  for (const t of threads) if (t.parentThreadId) live.set(t.parentThreadId, (live.get(t.parentThreadId) ?? 0) + 1);

  const scope = threads.filter(t => inScope(t, cfg));
  // Fail closed: without schedules or bindings nothing is archived.
  const scheduled = await scheduledThreads(sdk, scope.map(t => t.projectId));
  const bound = await protectedByPlugins(sdk, opts.protectionRpcs);
  const config: CleanupConfig = { ...cfg, protectedThreadIds: new Set([...cfg.protectedThreadIds, ...bound]) };

  const res: RunResult = { at: now, dryRun: opts.dryRun, scanned: threads.length, checked: 0, archived: [], wouldArchive: [], errors: [], attention: [], kept: {} };
  const parentTurn = new Map<string, number | null>();
  for (const t of scope) {
    if (opts.signal?.aborted) break;
    res.checked++;
    const [turn, activity, queue, asks] = await Promise.all([
      lastTurn(sdk, t.id, opts.signal),
      lastActivity(sdk, t.id, opts.signal),
      sdk.threads.queuedMessages.list({ threadId: t.id } as never) as unknown as Promise<unknown[]>,
      (sdk.threads.interactions.list({ threadId: t.id, signal: opts.signal } as never) as unknown as Promise<{ status?: string }[]>).catch(() => [{ status: "pending" }]),
    ]);
    let parentLastTurnAt: number | null = null;
    let parentArchived = false;
    if (t.parentThreadId) {
      const p = byId.get(t.parentThreadId);
      parentArchived = !p;
      if (p) {
        if (!parentTurn.has(p.id)) parentTurn.set(p.id, (await lastTurn(sdk, p.id, opts.signal))?.at ?? null);
        parentLastTurnAt = parentTurn.get(p.id) ?? null;
      }
    }
    const facts: ThreadFacts = {
      lastTurn: turn, lastActivityAt: activity, parentLastTurnAt, parentArchived,
      pendingQuestion: asks.some(a => a.status === "pending"),
      queuedMessages: queue.length, scheduled: scheduled.has(t.id), liveChildren: live.get(t.id) ?? 0,
      workspace: "none",
    };
    let v = decide(t, facts, config, now);
    // The workspace check is slow (remote hosts); only for threads that otherwise go.
    if (v.archive && t.environmentIsWorktree) v = decide(t, { ...facts, workspace: await workspaceOf(sdk, t) }, config, now);
    const d: Decision = { id: t.id, projectId: t.projectId, title: t.title ?? t.titleFallback ?? "", kind: kindOf(t), ...v };
    for (const k of v.keep) res.kept[k] = (res.kept[k] ?? 0) + 1;
    if (v.attention) res.attention.push(d);
    if (v.archive) res.wouldArchive.push(d);
  }

  if (!opts.dryRun) {
    const journal = (await bb.storage.kv.get<JournalEntry[]>("journal")) ?? [];
    for (const d of res.wouldArchive.slice(0, opts.maxArchives)) {
      if (opts.signal?.aborted) break;
      try {
        await sdk.threads.archive({ threadId: d.id });
        res.archived.push(d.id);
        journal.push({ at: now, id: d.id, title: d.title, reason: `${d.kind}: finished, result picked up, quiet` });
        bb.log.info(`archived ${d.id} — ${d.title}`);
      } catch (e) {
        res.errors.push({ id: d.id, error: e instanceof Error ? e.message : String(e) });
      }
    }
    await bb.storage.kv.set("journal", journal.slice(-JOURNAL_MAX));
  }
  await bb.storage.kv.set("last-run", res);
  return res;
}

/** One line for the owner, only when something needs a look; each thread once. */
async function report(bb: BbPluginApi, res: RunResult): Promise<string | null> {
  const told = new Set((await bb.storage.kv.get<string[]>("told")) ?? []);
  const fresh = res.attention.filter(d => !told.has(d.id));
  if (!res.errors.length && !fresh.length) return null;
  const parts: string[] = [];
  if (res.errors.length) parts.push(`не удалось убрать ${res.errors.length}`);
  if (fresh.length) parts.push(`нужен взгляд ${fresh.length}: ` + fresh.slice(0, 5).map(d => `«${d.title.slice(0, 40)}» — ${d.attention}`).join("; "));
  const line = `Уборка тредов: ${parts.join(", ")}. Подробно: bb thread-cleanup status`;
  for (const d of fresh) told.add(d.id);
  await bb.storage.kv.set("told", [...told].slice(-JOURNAL_MAX));
  await bb.sdk.plugins.callRpc({ pluginId: "push-notifications", method: "notifications.send", input: { title: "Уборка тредов", body: line } } as never)
    .catch(e => bb.log.warn(`report not sent: ${e instanceof Error ? e.message : e}`));
  return line;
}

function formatRun(r: RunResult, verbose: boolean): string {
  const head = `${new Date(r.at).toISOString()}${r.dryRun ? " (сухой прогон)" : ""}: тредов ${r.scanned}, проверено ${r.checked}, ` +
    `${r.dryRun ? "ушло бы" : "в архив"} ${r.dryRun ? r.wouldArchive.length : r.archived.length}, ошибок ${r.errors.length}, нужен взгляд ${r.attention.length}`;
  if (!verbose) return head;
  const kept = Object.entries(r.kept).sort((a, b) => b[1] - a[1]).map(([k, n]) => `  остаются — ${k}: ${n}`);
  const list = r.wouldArchive.map(d => `  ${d.id} [${d.kind}] ${d.title.slice(0, 70)}`);
  const att = r.attention.map(d => `  ! ${d.id} ${d.title.slice(0, 60)} — ${d.attention}`);
  return [head, ...kept, ...(list.length ? ["кандидаты:", ...list] : []), ...(att.length ? ["нужен взгляд:", ...att] : [])].join("\n");
}

export default async function plugin(bb: BbPluginApi) {
  const settings = bb.settings.define({
    subthreadHours: { type: "string", label: "Срок для подтредов, часов", description: "Подтред и скрытая ветка без движения дольше срока могут уйти в архив, если работа закончена и итог забран.", default: "24" },
    rootHours: { type: "string", label: "Срок для верхних тредов, часов", description: "0 — верхние треды не трогать (по умолчанию). В личном BB владельца — 72.", default: "0" },
    exemptProjects: { type: "string", label: "Проекты-исключения", description: "Id проектов через запятую.", default: "" },
    exemptTitleMarkers: { type: "string", label: "Метки открытого поручения", description: "Фрагменты названия через запятую; тред с такой меткой не трогается.", default: "⏳,❓" },
    protectedThreads: { type: "string", label: "Треды-исключения", description: "Id тредов через запятую.", default: "" },
    protectionRpcs: { type: "string", label: "Привязки из плагинов", description: "pluginId.method через запятую; метод возвращает id тредов, привязанных к чатам. Недоступен — уборка не идёт.", default: "" },
    maxArchives: { type: "string", label: "Предел архиваций за запуск", default: "100" },
    dryRun: { type: "boolean", label: "Только сухой прогон", description: "Показывать, что ушло бы в архив, ничего не трогая.", default: false },
  });

  async function resolve() {
    const v = await settings.get();
    const cfg: CleanupConfig = {
      subthreadHours: parseHours(v.subthreadHours, 24) || 24,
      rootHours: parseHours(v.rootHours, 0),
      exemptProjects: parseList(v.exemptProjects),
      exemptTitleMarkers: parseList(v.exemptTitleMarkers),
      protectedThreadIds: new Set(parseList(v.protectedThreads)),
    };
    return { cfg, dryRun: Boolean(v.dryRun), maxArchives: parseHours(v.maxArchives, 100), protectionRpcs: parseList(v.protectionRpcs) };
  }

  // Daily, server-local time. The first run after install is always a dry run.
  bb.background.schedule("daily-cleanup", "17 4 * * *", async () => {
    const r = await resolve();
    const first = !(await bb.storage.kv.get<boolean>("first-run-done"));
    const res = await runCleanup(bb, r.cfg, { ...r, dryRun: r.dryRun || first });
    await bb.storage.kv.set("first-run-done", true);
    bb.log.info(formatRun(res, false));
    await report(bb, res);
  });

  bb.cli.register({
    name: "thread-cleanup",
    summary: "Уборка законченных подтредов в архив",
    commands: [
      { name: "run", summary: "Запустить уборку сейчас", usage: "bb thread-cleanup run [--dry-run]" },
      { name: "status", summary: "Итог последнего запуска и настройки", usage: "bb thread-cleanup status" },
      { name: "log", summary: "Журнал убранного", usage: "bb thread-cleanup log [--limit N]" },
      { name: "restore", summary: "Вернуть тред или весь последний заход", usage: "bb thread-cleanup restore <thread-id>|--last-run" },
    ],
    async run(argv, ctx) {
      const [sub, ...rest] = argv;
      if (sub === "run") {
        const r = await resolve();
        const res = await runCleanup(bb, r.cfg, { ...r, dryRun: r.dryRun || rest.includes("--dry-run"), signal: ctx.signal });
        return { exitCode: 0, stdout: formatRun(res, true) };
      }
      if (sub === "status") {
        const r = await resolve();
        const last = await bb.storage.kv.get<RunResult>("last-run");
        const conf = `срок подтредов ${r.cfg.subthreadHours} ч, верхних ${r.cfg.rootHours || "выключено"}, предел ${r.maxArchives}, сухой прогон ${r.dryRun ? "да" : "нет"}`;
        return { exitCode: 0, stdout: [conf, last ? formatRun(last, true) : "запусков ещё не было"].join("\n") };
      }
      if (sub === "log") {
        const i = rest.indexOf("--limit");
        const n = i >= 0 ? Number(rest[i + 1]) || 50 : 50;
        const j = ((await bb.storage.kv.get<JournalEntry[]>("journal")) ?? []).slice(-n);
        return { exitCode: 0, stdout: j.map(e => `${new Date(e.at).toISOString()} ${e.id}${e.restoredAt ? " (возвращён)" : ""} ${e.title.slice(0, 70)}`).join("\n") || "пусто" };
      }
      if (sub === "restore") {
        const journal = (await bb.storage.kv.get<JournalEntry[]>("journal")) ?? [];
        const lastAt = Math.max(0, ...journal.map(e => e.at));
        const ids = rest[0] === "--last-run" ? journal.filter(e => e.at === lastAt && !e.restoredAt).map(e => e.id) : rest.filter(a => a.startsWith("thr_"));
        if (!ids.length) return { exitCode: 2, stderr: "usage: bb thread-cleanup restore <thread-id>|--last-run" };
        const done: string[] = [], failed: string[] = [];
        for (const id of ids) {
          try { await bb.sdk.threads.unarchive({ threadId: id }); done.push(id); for (const e of journal) if (e.id === id) e.restoredAt = Date.now(); }
          catch (e) { failed.push(`${id}: ${e instanceof Error ? e.message : e}`); }
        }
        await bb.storage.kv.set("journal", journal);
        return { exitCode: failed.length ? 1 : 0, stdout: `возвращено ${done.length}`, stderr: failed.join("\n") || undefined };
      }
      return { exitCode: 2, stderr: "usage: bb thread-cleanup <run|status|log|restore>" };
    },
  });
}
