// Thread cleanup rule: which finished threads may go to the archive.
//
// Pure functions, no plugin API, so the rule is unit-testable. server.ts
// gathers the facts and acts on the verdict.
//
// Shape (background sweep, dry-run, run command) follows
// bb-plugin-auto-archive by Shane Logsdon (MIT); the rule itself is new:
// Auto Archive judges root threads by age only, while this rule targets
// sub-threads and requires that the work is finished and its result arrived.

export const HOUR_MS = 3_600_000;

export interface CleanupConfig {
  /** Hours without any activity before a sub-thread or hidden fork may go. */
  subthreadHours: number;
  /** Same for visible root threads; 0 turns root threads off. */
  rootHours: number;
  /** Project ids never touched. */
  exemptProjects: readonly string[];
  /** Title fragments that mark an open task (e.g. "⏳"). */
  exemptTitleMarkers: readonly string[];
  /** Thread ids never touched (bindings reported by other plugins). */
  protectedThreadIds: ReadonlySet<string>;
  /** Hours an open task may sit quiet before the owner hears about it. */
  taskStaleHours: number;
}

export interface ThreadRow {
  id: string;
  projectId: string;
  title: string | null;
  titleFallback?: string | null;
  status: string;
  parentThreadId: string | null;
  visibility: "visible" | "hidden";
  pinnedAt: number | null;
  archivedAt: number | null;
  deletedAt: number | null;
  lastReadAt: number | null;
  hasPendingInteraction: boolean;
  queuedWork: string;
  parentNotifications?: string | null;
  originPluginId?: string | null;
  environmentId?: string | null;
  environmentIsWorktree?: boolean | null;
  activity?: Record<string, number | undefined> | null;
}

export type WorkspaceState = "none" | "clean" | "dirty" | "unknown";

/** Facts the server collected about one thread. */
export interface ThreadFacts {
  /** Last completed turn: time and outcome ("completed", "failed", ...). */
  lastTurn: { at: number; status: string } | null;
  /** Time of the newest event of any kind. */
  lastActivityAt: number;
  /** Newest completed turn of the parent, or null. */
  parentLastTurnAt: number | null;
  parentArchived: boolean;
  pendingQuestion: boolean;
  queuedMessages: number;
  /** An enabled schedule targets this thread or was created by it. */
  scheduled: boolean;
  liveChildren: number;
  workspace: WorkspaceState;
  /** Task mark this module keeps in the thread's plugin metadata. */
  taskMark: "open" | "closed" | null;
}

export type Kind = "subthread" | "hidden" | "root";

export interface Verdict {
  archive: boolean;
  /** Why the thread stays; empty when it goes. */
  keep: string[];
  /** Kept for a reason the owner should see once (error, unreachable copy). */
  attention: string | null;
}

export function kindOf(t: ThreadRow): Kind {
  if (t.parentThreadId) return "subthread";
  return t.visibility === "hidden" ? "hidden" : "root";
}

/** Threads worth collecting facts for at all (cheap pre-filter). */
export function inScope(t: ThreadRow, cfg: CleanupConfig): boolean {
  if (t.archivedAt !== null || t.deletedAt !== null) return false;
  if (kindOf(t) === "root" && cfg.rootHours <= 0) return false;
  return true;
}

/**
 * A task sub-thread (an assignment kept until its result is checked) is told
 * apart by structure, not by words: it was marked by `bb thread-cleanup task`,
 * or it was spawned so that its turns do not wake the parent
 * (`--parent-notifications stuck|off`), which is how the task skill opens one.
 * Schedule runs are spawned the same way and are not tasks.
 */
export function isTask(t: ThreadRow, f: ThreadFacts): boolean {
  if (f.taskMark) return true;
  if (!t.parentThreadId || t.originPluginId === "automations") return false;
  return t.parentNotifications === "stuck" || t.parentNotifications === "off";
}

export function decide(t: ThreadRow, f: ThreadFacts, cfg: CleanupConfig, now: number): Verdict {
  const keep: string[] = [];
  let attention: string | null = null;
  const kind = kindOf(t);
  const title = t.title ?? t.titleFallback ?? "";

  if (kind === "root" && cfg.rootHours <= 0) keep.push("root thread");
  if (t.pinnedAt !== null) keep.push("pinned");
  if (cfg.exemptProjects.includes(t.projectId)) keep.push("project exempt");
  if (cfg.protectedThreadIds.has(t.id)) keep.push("bound to a chat");
  if (cfg.exemptTitleMarkers.some(m => m && title.includes(m))) keep.push("open task marker");
  if (!["idle", "error"].includes(t.status)) keep.push("running");
  if (Object.values(t.activity ?? {}).some(n => (n ?? 0) > 0)) keep.push("background work");
  if (t.hasPendingInteraction || f.pendingQuestion) keep.push("waiting for an answer");
  if (t.queuedWork !== "none" || f.queuedMessages > 0) keep.push("queued messages");
  if (f.scheduled) keep.push("schedule");
  if (f.liveChildren > 0) keep.push("live sub-threads");

  if (t.status === "error" || (f.lastTurn && f.lastTurn.status !== "completed")) {
    keep.push(`last turn ${f.lastTurn?.status ?? "error"}`);
    attention = `последний ход: ${f.lastTurn?.status ?? "error"}`;
  } else if (!f.lastTurn) {
    keep.push("no finished turn");
  }

  const hours = kind === "root" ? cfg.rootHours : cfg.subthreadHours;
  const lastAt = Math.max(f.lastActivityAt, f.lastTurn?.at ?? 0);
  if (now - lastAt < hours * HOUR_MS) keep.push(`active within ${hours} h`);

  const task = isTask(t, f);
  // Reversed rule for tasks: only an explicit close lets one go.
  if (task && f.taskMark !== "closed") keep.push("open task");
  if (f.lastTurn && f.lastTurn.status === "completed" && !(task && f.taskMark === "closed") && !resultArrived(t, f)) keep.push("result not picked up");

  if (f.workspace === "dirty") {
    keep.push("uncommitted or unmerged work");
    attention ??= "в рабочей копии несохранённая или невлитая работа";
  } else if (f.workspace === "unknown") {
    keep.push("workspace not checked");
    attention ??= "рабочую копию не удалось проверить";
  }

  // Attention only matters for threads that are otherwise done and old.
  const flags = new Set(["open task", "result not picked up", "uncommitted or unmerged work", "workspace not checked"]);
  const onlyAttention = keep.every(k => flags.has(k) || k.startsWith("last turn"));
  if (!(keep.length && onlyAttention)) attention = null;
  // An open task quiet for long is the owner's question, whatever else holds it.
  const quietH = (now - lastAt) / HOUR_MS;
  if (task && f.taskMark !== "closed" && !keep.includes("running") && !f.scheduled && quietH >= cfg.taskStaleHours)
    attention = `поручение без движения ${quietH < 48 ? `${Math.floor(quietH)} ч` : `${Math.floor(quietH / 24)} сут`}`;
  return { archive: keep.length === 0, keep, attention };
}

/**
 * The result reached someone: the parent finished a turn after it, the parent
 * is gone, or a person opened the thread after it.
 */
export function resultArrived(t: ThreadRow, f: ThreadFacts): boolean {
  const at = f.lastTurn?.at ?? 0;
  if (t.parentThreadId && (f.parentArchived || (f.parentLastTurnAt ?? 0) > at)) return true;
  return (t.lastReadAt ?? 0) >= at;
}

export function parseList(value: unknown): string[] {
  return String(value ?? "").split(",").map(s => s.trim()).filter(Boolean);
}

export function parseHours(value: unknown, fallback: number): number {
  const n = Number(String(value ?? "").trim());
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

/**
 * Mode of a scheduled run. The first scheduled run after every new install,
 * reinstall or version change is a dry run that records what would go; only
 * after that does the same installation archive. An unknown installation
 * (source not readable) is always a dry run.
 */
export function scheduledMode(settingDry: boolean, doneFor: string | null, installation: string | null):
  { dry: boolean; reason: "setting" | "new installation" | "unknown installation" | null; remember: string | null } {
  if (settingDry) return { dry: true, reason: "setting", remember: null };
  if (!installation) return { dry: true, reason: "unknown installation", remember: null };
  if (doneFor !== installation) return { dry: true, reason: "new installation", remember: installation };
  return { dry: false, reason: null, remember: null };
}

/** Identity of the installed copy: when and from what it was installed, and the active version. */
export function installationKey(source: { installedAt?: number | null; resolved?: string | null; history?: { version?: string; activatedAt?: number }[] } | null, code = ""): string | null {
  if (!source || !source.installedAt) return null;
  const active = [...(source.history ?? [])].sort((a, b) => (b.activatedAt ?? 0) - (a.activatedAt ?? 0))[0];
  // The digest of the module's own code catches a change that a folder source does not report as a new version.
  return JSON.stringify([source.installedAt, source.resolved ?? "", active?.version ?? "", code]);
}
