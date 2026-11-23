import { test } from "node:test";
import assert from "node:assert/strict";
import { decide, HOUR_MS, inScope, installationKey, parseHours, parseList, resultArrived, scheduledMode, type CleanupConfig, type ThreadFacts, type ThreadRow } from "../policy.ts";

const NOW = 1_800_000_000_000;
const cfg: CleanupConfig = { subthreadHours: 24, rootHours: 0, exemptProjects: [], exemptTitleMarkers: ["⏳"], protectedThreadIds: new Set(), taskStaleHours: 168 };

const row = (o: Partial<ThreadRow> = {}): ThreadRow => ({
  id: "thr_child", projectId: "proj_a", title: "Сделать отчёт", status: "idle", parentThreadId: "thr_parent",
  visibility: "visible", pinnedAt: null, archivedAt: null, deletedAt: null, lastReadAt: null,
  hasPendingInteraction: false, queuedWork: "none", environmentIsWorktree: false, activity: {}, ...o,
});
const done = NOW - 48 * HOUR_MS;
const facts = (o: Partial<ThreadFacts> = {}): ThreadFacts => ({
  lastTurn: { at: done, status: "completed" }, lastActivityAt: done, parentLastTurnAt: done + HOUR_MS, parentArchived: false,
  pendingQuestion: false, queuedMessages: 0, scheduled: false, liveChildren: 0, workspace: "none", taskMark: null, ...o,
});

test("finished sub-thread whose result the parent picked up goes to the archive", () => {
  const v = decide(row(), facts(), cfg, NOW);
  assert.equal(v.archive, true);
  assert.deepEqual(v.keep, []);
});

test("open work stays", () => {
  const cases: [Partial<ThreadRow>, Partial<ThreadFacts>, string][] = [
    [{ pinnedAt: 1 }, {}, "pinned"],
    [{ status: "active" }, {}, "running"],
    [{ hasPendingInteraction: true }, {}, "waiting for an answer"],
    [{}, { pendingQuestion: true }, "waiting for an answer"],
    [{ queuedWork: "waiting" }, {}, "queued messages"],
    [{}, { queuedMessages: 1 }, "queued messages"],
    [{}, { scheduled: true }, "schedule"],
    [{}, { liveChildren: 1 }, "live sub-threads"],
    [{ title: "⏳ ждём ответа до 18:00" }, {}, "open task marker"],
    [{ activity: { activeBackgroundCommandCount: 1 } }, {}, "background work"],
    [{}, { lastActivityAt: NOW - 2 * HOUR_MS }, "active within 24 h"],
    [{}, { parentLastTurnAt: done - HOUR_MS }, "result not picked up"],
    [{ environmentIsWorktree: true }, { workspace: "dirty" }, "uncommitted or unmerged work"],
    [{ environmentIsWorktree: true }, { workspace: "unknown" }, "workspace not checked"],
    [{}, { lastTurn: null }, "no finished turn"],
  ];
  for (const [r, f, reason] of cases) {
    const v = decide(row(r), facts(f), cfg, NOW);
    assert.equal(v.archive, false, reason);
    assert.ok(v.keep.includes(reason), `${reason}: ${v.keep}`);
  }
});

test("protected threads and exempt projects stay", () => {
  assert.equal(decide(row(), facts(), { ...cfg, protectedThreadIds: new Set(["thr_child"]) }, NOW).archive, false);
  assert.equal(decide(row(), facts(), { ...cfg, exemptProjects: ["proj_a"] }, NOW).archive, false);
});

test("a thread that failed stays and is flagged once for the owner", () => {
  const v = decide(row({ status: "error" }), facts({ lastTurn: { at: done, status: "failed" } }), cfg, NOW);
  assert.equal(v.archive, false);
  assert.equal(v.attention, "последний ход: failed");
  const young = decide(row({ status: "error" }), facts({ lastTurn: { at: NOW - HOUR_MS, status: "failed" }, lastActivityAt: NOW - HOUR_MS }), cfg, NOW);
  assert.equal(young.attention, null, "not flagged before the term");
});

test("root threads are off by default and follow their own term when on", () => {
  const root = row({ parentThreadId: null, lastReadAt: done + 1 });
  assert.equal(inScope(root, cfg), false);
  assert.equal(decide(root, facts(), cfg, NOW).archive, false);
  const on = { ...cfg, rootHours: 72 };
  assert.equal(inScope(root, on), true);
  assert.equal(decide(root, facts(), on, NOW).archive, false, "48 h < 72 h");
  assert.equal(decide(row({ parentThreadId: null }), facts({ lastTurn: { at: NOW - 80 * HOUR_MS, status: "completed" }, lastActivityAt: NOW - 80 * HOUR_MS }), on, NOW).archive, false, "unread");
  assert.equal(decide(row({ parentThreadId: null, lastReadAt: NOW }), facts({ lastTurn: { at: NOW - 80 * HOUR_MS, status: "completed" }, lastActivityAt: NOW - 80 * HOUR_MS }), on, NOW).archive, true);
});

test("hidden forks use the sub-thread term", () => {
  const fork = row({ parentThreadId: null, visibility: "hidden", lastReadAt: NOW });
  assert.equal(inScope(fork, cfg), true);
  assert.equal(decide(fork, facts(), cfg, NOW).archive, true);
});

test("result arrives through the parent, its archive, or a person reading", () => {
  assert.equal(resultArrived(row(), facts({ parentLastTurnAt: null, parentArchived: true })), true);
  assert.equal(resultArrived(row({ lastReadAt: done }), facts({ parentLastTurnAt: null })), true);
  assert.equal(resultArrived(row(), facts({ parentLastTurnAt: null })), false);
});

test("settings parsing", () => {
  assert.deepEqual(parseList(" a, ,b "), ["a", "b"]);
  assert.equal(parseHours("72", 0), 72);
  assert.equal(parseHours("x", 24), 24);
  assert.equal(parseHours("-1", 24), 24);
});

const task = (o: Partial<ThreadRow> = {}) => row({ parentNotifications: "stuck", title: "ждём ответа Олега", ...o });

test("open task without a schedule stays, however old; the owner hears after the stale term", () => {
  const v = decide(task(), facts(), cfg, NOW);
  assert.equal(v.archive, false);
  assert.ok(v.keep.includes("open task"));
  assert.equal(v.attention, null, "2 days: no line yet");
  const old = NOW - 8 * 24 * HOUR_MS;
  const s = decide(task(), facts({ lastTurn: { at: old, status: "completed" }, lastActivityAt: old }), cfg, NOW);
  assert.equal(s.archive, false);
  assert.equal(s.attention, "поручение без движения 8 сут");
  assert.equal(decide(task(), facts({ lastTurn: { at: old, status: "completed" }, lastActivityAt: old, scheduled: true }), cfg, NOW).attention, null, "a schedule tracks it");
});

test("a task is told by structure: stuck/off spawn or the module's mark, not by its title", () => {
  assert.ok(decide(task({ parentNotifications: "off" }), facts(), cfg, NOW).keep.includes("open task"));
  assert.ok(decide(row({ title: "просто работа" }), facts({ taskMark: "open" }), cfg, NOW).keep.includes("open task"));
  assert.equal(decide(row({ title: "⏳ нет — это не метка", parentNotifications: "auto" }), facts(), { ...cfg, exemptTitleMarkers: [] }, NOW).archive, true);
  assert.equal(decide(task({ originPluginId: "automations" }), facts(), cfg, NOW).archive, true, "schedule runs are not tasks");
});

test("closed task goes, even though its parent was never woken", () => {
  const v = decide(task({ title: "готово, проверено" }), facts({ taskMark: "closed", parentLastTurnAt: null }), cfg, NOW);
  assert.equal(v.archive, true, String(v.keep));
  assert.equal(decide(task(), facts({ taskMark: "closed", lastActivityAt: NOW - HOUR_MS }), cfg, NOW).archive, false, "still the 24 h term");
});

test("first scheduled run after every install or update is dry, then the same installation archives", () => {
  const v1 = installationKey({ installedAt: 1, resolved: "repo@v0.4.3", history: [{ version: "aaa", activatedAt: 1 }] });
  const v2 = installationKey({ installedAt: 1, resolved: "repo@v0.4.4", history: [{ version: "aaa", activatedAt: 1 }, { version: "bbb", activatedAt: 5 }] });
  const again = installationKey({ installedAt: 9, resolved: "repo@v0.4.3", history: [{ version: "aaa", activatedAt: 9 }] });
  assert.deepEqual(scheduledMode(false, null, v1), { dry: true, reason: "new installation", remember: v1 });
  assert.deepEqual(scheduledMode(false, v1, v1), { dry: false, reason: null, remember: null });
  assert.equal(scheduledMode(false, v1, v2).dry, true, "update");
  assert.equal(scheduledMode(false, v1, again).dry, true, "reinstall of the same version");
  assert.deepEqual(scheduledMode(false, v1, null), { dry: true, reason: "unknown installation", remember: null });
  assert.equal(scheduledMode(true, v1, v1).dry, true, "setting wins");
  const sameSource = { installedAt: 1, resolved: "path:/x", history: [{ version: "aaa", activatedAt: 1 }] };
  assert.notEqual(installationKey(sameSource, "code1"), installationKey(sameSource, "code2"), "changed code in the same folder");
  assert.equal(installationKey(sameSource, "code1"), installationKey(sameSource, "code1"), "same code over itself: not new");
  assert.equal(installationKey(null), null);
  assert.equal(installationKey({ resolved: "x" }), null);
});
