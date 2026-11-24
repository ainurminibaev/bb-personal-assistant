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
  const owner = { thread: "owner-dm" };
  await plugin({
    settings: { define: () => ({ get: async () => ({}), onChange() {} }) },
    agents: { configure() {}, registerTool() {}, contributeInstructions() {} },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [], get() {}, run() {} }) }),
               kv: { get: async k => kv.get(k), set: async (k, v) => { kv.set(k, v); } } },
    cli: { register: c => { command = c; } },
    rpc: { register() {} },
    background: { schedule() {}, service() {} },
    experimental_hooks: { on: (name, fn) => { if (name === "message.dispatch") hook = fn; } },
    experimental_aiServices: { register: () => ({ dispose() {} }) },
    sdk: {
      projects: { list: async () => [{ id: "proj", sources: [{ path: join(clone, "employees", "dev"), isDefault: true }] }],
                  get: async () => ({ sources: [{ path: join(clone, "employees", "dev") }] }) },
      threads: { get: async ({ threadId }) => ({ id: threadId }), list: async () => [] },
      plugins: {
        getSettings: async () => ({ values: { installKind: "employee" } }),
        callRpc: async ({ pluginId, method, outputSchema }) => {
          if (pluginId === "telegram-projects" && method === "status")
            return outputSchema.parse({ chatBindings: [{ topicId: 0, threadId: owner.thread }, { topicId: 7, threadId: "group" }] });
          throw Error("plugin_not_found");
        },
      },
    },
  });
  await new Promise(r => setTimeout(r, 20));
  const run = (threadId, ...argv) => command.run(argv, { threadId, projectId: "proj" });
  const dispatch = threadId => hook({ thread: { id: threadId }, parentThreadId: null });
  return { run, dispatch, kv };
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
