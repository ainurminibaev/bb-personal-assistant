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
    attention = `last turn ${f.lastTurn?.status ?? "error"}`;
  } else if (!f.lastTurn) {
    keep.push("no finished turn");
  }

  const hours = kind === "root" ? cfg.rootHours : cfg.subthreadHours;
  const lastAt = Math.max(f.lastActivityAt, f.lastTurn?.at ?? 0);
  if (now - lastAt < hours * HOUR_MS) keep.push(`active within ${hours} h`);

  if (f.lastTurn && f.lastTurn.status === "completed" && !resultArrived(t, f)) keep.push("result not picked up");

  if (f.workspace === "dirty") {
    keep.push("uncommitted or unmerged work");
    attention ??= "uncommitted or unmerged work";
  } else if (f.workspace === "unknown") {
    keep.push("workspace not checked");
    attention ??= "workspace not checked";
  }

  // Attention only matters for threads that are otherwise done and old.
  const onlyAttention = keep.every(k => k === attention || k.startsWith("last turn") || k === "uncommitted or unmerged work" || k === "workspace not checked");
  return { archive: keep.length === 0, keep, attention: keep.length && onlyAttention ? attention : null };
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
