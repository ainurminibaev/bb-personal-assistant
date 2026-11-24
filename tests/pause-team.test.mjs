import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import plugin from "../dist/server.js";

// An organization copy with two employees; this installation works from employees/dev. Passport fields come from
// employee.yml and passport.yml (the latter wins), as in the organization's repository.
function organization() {
  const clone = mkdtempSync(join(tmpdir(), "org-"));
  const w = (p, t) => { mkdirSync(join(clone, p, ".."), { recursive: true }); writeFileSync(join(clone, p), t); };
  w("organization.yml", "name: Test org\nemployees:\n  - pm\n  - dev   # разработчик\n");
  w("employees/pm/employee.yml", "kind: employee\ndescription: ИИ-менеджер проекта\n");
  w("employees/pm/passport.yml", [
    "name: Менеджер", "position: ИИ-менеджер проекта «Тест»", "state: active   # active | paused | retired",
    "owner: Владелец", "curator: Владелец", "tasks_from:", "  - Владелец", "  - участники групп — по делу группы",
    "duties:", "  - встречи и договорённости", "  - сроки в трекере", "accounts:", "  - почта менеджера", "chats: рабочие группы",
  ].join("\n") + "\n");
  w("employees/dev/employee.yml", "kind: employee\ndescription: ИИ-разработчик\nposition: ИИ-разработчик проекта «Тест»\ncurator: Куратор\nduties: [код, тесты]\n");
  return clone;
}

async function load() {
  const clone = organization();
  const kv = new Map();
  let command, hook;
  const sent = [], automations = [], notices = [], minute = [];
  const owner = { thread: "owner-dm" };
  await plugin({
    settings: { define: () => ({ get: async () => ({}), onChange() {} }) },
    agents: { configure() {}, registerTool() {}, contributeInstructions() {} },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [], get() {}, run() {} }) }),
               kv: { get: async k => kv.get(k), set: async (k, v) => { kv.set(k, v); }, delete: async k => { kv.delete(k); } } },
    cli: { register: c => { command = c; } },
    rpc: { register() {} },
    background: { schedule: (name, cron, fn) => { if (name === "guest-schedule-alerts") minute.push(fn); }, service() {} },
    experimental_hooks: { on: (name, fn) => { if (name === "message.dispatch") hook = fn; } },
    experimental_aiServices: { register: () => ({ dispose() {} }) },
    sdk: {
      projects: { list: async () => [{ id: "proj", sources: [{ path: join(clone, "employees", "dev"), isDefault: true }] }],
                  get: async () => ({ sources: [{ path: join(clone, "employees", "dev") }] }) },
      threads: { get: async ({ threadId }) => ({ id: threadId }), list: async () => [], send: async a => { sent.push(a); return {}; } },
      plugins: {
        getSettings: async () => ({ values: { installKind: "employee" } }),
        callRpc: async ({ pluginId, method, input, outputSchema }) => {
          if (pluginId === "telegram-projects" && method === "status")
            return outputSchema.parse({ chatBindings: [{ topicId: 0, threadId: owner.thread }, { topicId: 7, threadId: "group" }] });
          if (pluginId === "automations" && method === "automations_list") return outputSchema.parse(automations);
          if (pluginId === "push-notifications") { notices.push(input); return outputSchema.parse({}); }
          throw Error("plugin_not_found");
        },
      },
    },
  });
  await new Promise(r => setTimeout(r, 20));
  const run = (threadId, ...argv) => command.run(argv, { threadId, projectId: "proj" });
  const dispatch = (threadId, parentThreadId = null, originPluginId = null) =>
    hook({ thread: { id: threadId }, parentThreadId, originPluginId, project: { id: "proj" } });
  return { run, dispatch, kv, owner, sent, automations, notices, minute };
}

test("the registry says who does what from the passports; a guest gets no accounts", async () => {
  const { run } = await load();
  const full = await run("group", "team");
  assert.equal(full.exitCode, 0, full.stderr);
  assert.match(full.stdout, /Менеджер — ИИ-менеджер проекта «Тест» \(работает\)/);
  assert.match(full.stdout, /отвечает: встречи и договорённости; сроки в трекере/);
  assert.match(full.stdout, /задачи ставят: Владелец; участники групп — по делу группы/);
  assert.match(full.stdout, /dev — ИИ-разработчик проекта «Тест» \(работает\)/);   // passport fields inside employee.yml
  assert.match(full.stdout, /отвечает: код; тесты/);
  assert.match(full.stdout, /accounts: почта менеджера/);
  const guest = await run("group", "team", "--guest");
  assert.doesNotMatch(guest.stdout, /почта менеджера/);
  assert.match(guest.stdout, /куратор \(как делать и хорошо ли сделано\): Куратор/);
});

test("the owner pauses the employee in his private chat: everything else waits, nothing is dropped; «включи» resumes", async () => {
  const { run, dispatch } = await load();
  assert.deepEqual(await dispatch("group"), { action: "proceed" });
  const refused = await run("group", "pause");
  assert.equal(refused.exitCode, 1);
  assert.match(refused.stderr, /только владелец/);
  const paused = await run("owner-dm", "pause", "--reason", "переезд");
  assert.equal(paused.exitCode, 0, paused.stderr);
  const wait = await dispatch("group");
  assert.equal(wait.action, "wait");          // a group message, a schedule run, a subthread: queued, not rejected
  assert.ok(wait.sendAt > Date.now());
  assert.equal((await dispatch("schedule-run-thread")).action, "wait");
  assert.deepEqual(await dispatch("owner-dm"), { action: "proceed" });   // «включи» gets through
  assert.match((await run("group", "team")).stdout, /dev — .*\(на паузе\)/);
  assert.match((await run(undefined, "pause-status")).stdout, /на паузе .* — переезд/);
  assert.equal((await run("group", "resume")).exitCode, 1);
  assert.equal((await run("owner-dm", "resume")).exitCode, 0);
  assert.deepEqual(await dispatch("group"), { action: "proceed" });
});

test("a terminal of the server (no thread) may pause and resume; the pause survives a reload", async () => {
  const first = await load();
  assert.equal((await first.run(undefined, "pause")).exitCode, 0);
  assert.equal(first.kv.get("pause").paused, true);
  assert.equal((await first.run(undefined, "resume")).exitCode, 0);
  assert.equal(first.kv.get("pause").paused, false);
});

test("while paused the owner's chat always passes: after his binding moved to another thread, and work he started there", async () => {
  const { run, dispatch, owner } = await load();
  assert.equal((await run("owner-dm", "pause")).exitCode, 0);
  assert.deepEqual(await dispatch("owner-sub", "owner-dm"), { action: "proceed" });   // a subthread of the owner's chat
  owner.thread = "owner-dm-2";                                                      // the assistant's pair switched
  assert.deepEqual(await dispatch("owner-dm-2"), { action: "proceed" });
  assert.equal((await dispatch("owner-dm")).action, "wait");                        // the old thread is not his chat now
  assert.equal((await run("owner-dm-2", "resume")).exitCode, 0);
});

test("after «включи»: a repeating run that waited past its period is skipped and named to the owner; a one-off runs with a note", async () => {
  const p = await load();
  const now = Date.now();
  p.automations.push(
    { id: "auto_hourly", name: "Статус группы", enabled: true, trigger: { triggerType: "schedule" }, nextRunAt: now + 3600_000,
      execution: { targetThreadId: "group" } },
    { id: "auto_once", name: "Напомнить про встречу", enabled: true, trigger: { triggerType: "once" }, nextRunAt: null,
      execution: { targetThreadId: "topic-2" } });
  assert.equal((await p.run("owner-dm", "pause")).exitCode, 0);
  assert.equal((await p.dispatch("group", null, "automations")).action, "wait");
  assert.equal((await p.dispatch("topic-2", null, "automations")).action, "wait");
  // The pause lasted two hours: the hourly run waited past its period.
  const hourly = p.kv.get("pause-run:group"); hourly.at -= 2 * 3600_000; p.kv.set("pause-run:group", hourly);
  const once = p.kv.get("pause-run:topic-2"); once.at -= 2 * 3600_000; p.kv.set("pause-run:topic-2", once);
  assert.equal((await p.run("owner-dm", "resume")).exitCode, 0);
  const skipped = await p.dispatch("group", null, "automations");
  assert.equal(skipped.action, "reject");
  assert.deepEqual(await p.dispatch("topic-2", null, "automations"), { action: "proceed" });
  await new Promise(r => setTimeout(r, 10));
  assert.match(p.sent.at(-1).input[0].text, /задержанный паузой сотрудника с/);
  assert.equal(p.sent.at(-1).threadId, "topic-2");
  for (const fn of p.minute) await fn().catch(() => {});
  assert.match(p.notices.at(-1).body, /пропущено запусков расписаний: 1 — «Статус группы»/);
  // Later runs are ordinary again.
  assert.deepEqual(await p.dispatch("group", null, "automations"), { action: "proceed" });
});
