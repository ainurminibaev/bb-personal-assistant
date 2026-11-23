import { test } from "node:test";
import assert from "node:assert/strict";
import { decide, HOUR_MS, inScope, parseHours, parseList, resultArrived, type CleanupConfig, type ThreadFacts, type ThreadRow } from "../policy.ts";

const NOW = 1_800_000_000_000;
const cfg: CleanupConfig = { subthreadHours: 24, rootHours: 0, exemptProjects: [], exemptTitleMarkers: ["⏳"], protectedThreadIds: new Set() };

const row = (o: Partial<ThreadRow> = {}): ThreadRow => ({
  id: "thr_child", projectId: "proj_a", title: "Сделать отчёт", status: "idle", parentThreadId: "thr_parent",
  visibility: "visible", pinnedAt: null, archivedAt: null, deletedAt: null, lastReadAt: null,
  hasPendingInteraction: false, queuedWork: "none", environmentIsWorktree: false, activity: {}, ...o,
});
const done = NOW - 48 * HOUR_MS;
const facts = (o: Partial<ThreadFacts> = {}): ThreadFacts => ({
  lastTurn: { at: done, status: "completed" }, lastActivityAt: done, parentLastTurnAt: done + HOUR_MS, parentArchived: false,
  pendingQuestion: false, queuedMessages: 0, scheduled: false, liveChildren: 0, workspace: "none", ...o,
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
  assert.equal(v.attention, "last turn failed");
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
