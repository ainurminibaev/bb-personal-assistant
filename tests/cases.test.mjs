import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import plugin from "../dist/server.js";

// An installation folder and a BB with: the owner's private chat (bound to topic 0), a group topic conversation, and
// subthreads of both. Schedules: the case's own one is on until removed.
async function setup({ conversations = null } = {}) {
  const root = mkdtempSync(join(tmpdir(), "cases-"));
  const threads = {
    "owner-chat": { id: "owner-chat", title: "🟢 Действующий · Личный помощник", parentThreadId: null },
    "owner-chat-2": { id: "owner-chat-2", title: "⏸ Резерв · Личный помощник", parentThreadId: null },
    "task-1": { id: "task-1", title: "⏳ разбор оплат", parentThreadId: "owner-chat" },
    "group": { id: "group", title: "Финансы — группа «Команда»", parentThreadId: null },
    "group-new": { id: "group-new", title: "Финансы — группа «Команда»", parentThreadId: null },
    "group-task": { id: "group-task", title: "⏳ отчёт", parentThreadId: "group" },
    "plain": { id: "plain", title: "Обычный тред", parentThreadId: null },
    "plain-next": { id: "plain-next", title: "Обычный тред", parentThreadId: null, sourceThreadId: "plain" },
  };
  const state = { owner: "owner-chat", live: new Set(["auto_a"]) };
  let command, contributed;
  await plugin({
    settings: { define: () => ({ get: async () => ({ projectId: "proj", threadId: "owner-chat" }), onChange() {} }) },
    agents: { configure() {}, registerTool() {}, contributeInstructions: p => { contributed = p; } },
    storage: { database: () => ({ exec() {}, prepare: () => ({ all: () => [], get() {}, run() {} }) }), kv: { get: async () => undefined, set: async () => {} } },
    cli: { register: c => { command = c; } },
    rpc: { register() {} },
    background: { schedule() {}, service() {} },
    experimental_aiServices: { register: () => ({ dispose() {} }) },
    sdk: {
      projects: { list: async () => [{ id: "proj", sources: [{ path: root, isDefault: true }] },
                                     { id: "group-proj", sources: [{ path: mkdtempSync(join(tmpdir(), "group-")), isDefault: true }] }],
                  get: async () => ({ sources: [{ path: root }] }) },
      threads: { get: async ({ threadId }) => threads[threadId], list: async () => [] },
      plugins: {
        getSettings: async () => ({ values: { installKind: "assistant" } }),
        callRpc: async ({ pluginId, method, input, outputSchema }) => {
          if (pluginId === "telegram-projects" && method === "status")
            return outputSchema.parse({ chatBindings: [{ topicId: 0, threadId: state.owner }] });
          if (pluginId === "automations") return outputSchema.parse([...state.live].map(id => ({ id, enabled: true })));
          if (pluginId === "telegram-projects" && method === "conversationOf" && conversations)
            return outputSchema.parse(conversations[input.threadId] ?? null);
          throw Error("plugin_not_found");
        },
      },
    },
  });
  await new Promise(r => setTimeout(r, 20));
  const run = async (threadId, ...argv) => command.run(["case", ...argv], { threadId, projectId: threadId.startsWith("moved") ? "group-proj" : "proj" });
  const terminal = async (...argv) => command.run(["case", ...argv], {});
  return { root, run, terminal, state, hint: threadId => contributed({ threadId, projectId: "proj" }) };
}

test("a case opened in a task subthread lives on disk: card and index line, written together", async () => {
  const { root, run } = await setup();
  const opened = await run("task-1", "open", "--title", "Разбор оплат за ноябрь", "--by", "Анна", "--place", "личка владельца",
    "--thread", "task-1", "--next", "сверить выгрузку", "--promise", "Анне — итог до 25.11 18:00");
  assert.equal(opened.exitCode, 0, opened.stderr);
  const id = opened.stdout.split("\n")[0];
  assert.match(id, /^c-\d{8}-razbor-oplat-za-noyabr$/);
  const card = readFileSync(join(root, "local", "cases", `${id}.md`), "utf8");
  assert.match(card, /title: "Разбор оплат за ноябрь"/);
  assert.match(card, /promises: \["Анне — итог до 25.11 18:00"\]/);
  assert.match(readFileSync(join(root, "local", "cases", "index.md"), "utf8"), new RegExp(`^${id} \\| open \\| личка владельца \\| Анна`));
});

test("reminders belong to the case: check finds a case without a next step, close hands its schedules to remove", async () => {
  const { run, state } = await setup();
  const id = (await run("task-1", "open", "--title", "Отчёт", "--thread", "task-1")).stdout.split("\n")[0];
  assert.match((await run("task-1", "check", "--here")).stdout, /нет ни расписания следующего шага, ни ожидания с датой/);
  assert.equal((await run("task-1", "set", id, "--schedule", "auto_a", "--next", "проверить в 16:00")).exitCode, 0);
  assert.equal((await run("task-1", "check")).stdout, "У каждого открытого дела есть следующий шаг.");
  state.live.delete("auto_a");   // the schedule was removed or switched off behind the case's back
  assert.match((await run("task-1", "check")).stdout, /расписания нет или выключено — auto_a/);
  state.live.add("auto_a");
  const closed = await run("task-1", "close", id, "--result", "итог отправлен Анне");
  assert.match(closed.stdout, /bb automation delete auto_a/);
  assert.match(closed.stdout, /bb thread-cleanup task task-1 closed/);
  assert.equal((await run("task-1", "list")).stdout, "Открытых дел нет.");
  assert.match((await run("task-1", "list", "--closed")).stdout, /closed \| Отчёт/);
  assert.match((await run("task-1", "set", id, "--state", "closed")).stderr, /case close/);
});

test("after a restart the new conversation of the same chat gets its open cases, and only them", async () => {
  const { run, state, hint } = await setup();
  await run("task-1", "open", "--title", "Дело лички");
  await run("group-task", "open", "--title", "Дело группы");
  await run("plain", "open", "--title", "Дело обычного треда");
  // The owner's private chat moved to the other thread of the pair; a group topic got a new conversation; a plain
  // thread was handed off.
  state.owner = "owner-chat-2";
  assert.match((await run("owner-chat-2", "list", "--here")).stdout, /Дело лички/);
  assert.doesNotMatch((await run("owner-chat-2", "list", "--here")).stdout, /Дело группы/);
  const group = (await run("group-new", "list", "--here")).stdout;
  assert.match(group, /Дело группы/);
  assert.doesNotMatch(group, /Дело лички/);
  assert.match((await run("plain-next", "list")).stdout, /Дело обычного треда/);
  // The new conversation is told to pick them up, without a model turn to find out.
  assert.match(hint("group-new"), /case list --here/);
});

test("a chat cannot read or change another chat's cases; only the coordinator sees all", async () => {
  const { run } = await setup();
  const id = (await run("task-1", "open", "--title", "Личное дело владельца")).stdout.split("\n")[0];
  assert.match((await run("group", "show", id)).stderr, /another chat/);
  assert.match((await run("group", "close", id)).stderr, /another chat/);
  assert.match((await run("group", "list", "--all")).stderr, /coordinator/);
  assert.match((await run("owner-chat", "list", "--all")).stdout, /Личное дело владельца/);
  assert.match((await run("group", "find", "Личное")).stdout, /Ничего не найдено/);
});

test("closed lines older than 90 days move to the archive list; no open case — no hint", async () => {
  const { root, run, hint } = await setup();
  assert.doesNotMatch(String(hint("task-1")), /case list --here/);
  const dir = join(root, "local", "cases");
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "index.md"), "c-20250101-staroe | closed | личка | Анна | Старое | — | 2025-01-01 | tg:owner\n");
  await run("task-1", "open", "--title", "Новое");
  assert.doesNotMatch(readFileSync(join(dir, "index.md"), "utf8"), /staroe/);
  assert.match(readFileSync(join(dir, "index-archive.md"), "utf8"), /staroe/);
  assert.ok(existsSync(join(dir, "index.md")));
});

test("with the bridge's conversationOf a chat is its numbers: a renamed topic keeps its cases, two people with one name do not share", async () => {
  const conversations = {
    "group": { chatId: -1001, topicId: 7 }, "group-renamed": { chatId: -1001, topicId: 7 },
    "alex-1": { chatId: 501, topicId: 0 }, "alex-2": { chatId: 502, topicId: 0 },
    "moved-group": { chatId: -1001, topicId: 7 },
  };
  const { run } = await setup({ conversations });
  await run("group", "open", "--title", "Отчёт группы");
  await run("alex-1", "open", "--title", "Дело первого Александра");
  assert.match((await run("group-renamed", "list")).stdout, /Отчёт группы/);
  assert.doesNotMatch((await run("alex-2", "list")).stdout, /первого Александра/);
  // The group moved into a project and folder of its own (С76): cases stay in the installation's folder.
  assert.match((await run("moved-group", "list")).stdout, /Отчёт группы/);
});

test("a terminal of the server sees every chat's cases from the installation's folder; a case is opened only from its chat", async () => {
  const { run, terminal } = await setup();
  await run("task-1", "open", "--title", "Дело лички");
  await run("group-task", "open", "--title", "Дело группы");
  const list = await terminal("list");
  assert.equal(list.exitCode, 0, list.stderr);
  assert.match(list.stdout, /Дело лички/);
  assert.match(list.stdout, /Дело группы/);
  assert.match((await terminal("open", "--title", "x")).stderr, /from its conversation/);
  assert.equal((await terminal("check")).exitCode, 0);
});
